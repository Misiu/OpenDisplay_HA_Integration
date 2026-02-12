"""ESP32 BLE OTA update implementation.

Uses the OpenDisplay BLE OTA protocol (commands 0x0046/0x0047/0x0048) to flash
firmware directly over an existing BLE connection. This is used for ESP32-S3,
ESP32-C3, and ESP32-C6 devices.

The firmware binary must be the **application-only** ``.bin`` (e.g.
``esp32-s3-N16R8.bin``), **not** the merged ``_full.bin`` which includes the
bootloader and partition table and would be rejected by ``Update.write()``.

Protocol flow:
    HA → [0x00, 0x46, size₀, size₁, size₂, size₃]  → ACK {0x00, 0x46}
    HA → [0x00, 0x47, chunk...]                       → ACK {0x00, 0x47}  (repeat)
    HA → [0x00, 0x48]                                 → ACK {0x00, 0x48}  → reboot
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Callable
from typing import TYPE_CHECKING

from .protocol_open_display import (
    CMD_OTA_DATA,
    CMD_OTA_END,
    CMD_OTA_START,
    RESP_ERROR,
    RESP_SUCCESS,
)

if TYPE_CHECKING:
    from .connection import BLEConnection

_LOGGER = logging.getLogger(__name__)

# Maximum firmware data bytes per BLE write (conservative for BLE MTU).
# The full packet is CMD_OTA_DATA (2 bytes) + payload, so the overall
# BLE write is ESP32_OTA_CHUNK_SIZE + 2.
ESP32_OTA_CHUNK_SIZE = 200


def _check_ota_response(response: bytes, expected_cmd: int) -> None:
    """Validate an OTA ACK response from the device.

    Args:
        response: Raw response bytes from device
        expected_cmd: Expected command echo byte (e.g. 0x46, 0x47, 0x48)

    Raises:
        RuntimeError: If response indicates an error or is unexpected
    """
    if len(response) < 2:
        raise RuntimeError(
            f"OTA response too short ({len(response)} bytes): {response.hex()}"
        )
    status = response[0]
    cmd_echo = response[1]
    if status == RESP_ERROR:
        raise RuntimeError(
            f"Device rejected OTA command 0x{expected_cmd:02x}"
            " (not supported on this platform)"
        )
    if status != RESP_SUCCESS or cmd_echo != expected_cmd:
        raise RuntimeError(
            f"Unexpected OTA response for 0x{expected_cmd:02x}: {response.hex()}"
        )


async def perform_esp32_ota(
    connection: BLEConnection,
    firmware_data: bytes,
    progress_callback: Callable[[int, int], None] | None = None,
) -> bool:
    """Flash firmware to an ESP32 device over BLE using the OTA protocol.

    The device must be connected via BLEConnection using the OpenDisplay
    service UUID.  The firmware binary (.bin) is sent in three phases:

    1. OTA Start – sends total firmware size
    2. OTA Data  – streams firmware in chunks, ACK per chunk
    3. OTA End   – finalises and triggers reboot

    Args:
        connection: Active BLEConnection to the device
        firmware_data: Raw application firmware binary (.bin)
        progress_callback: Optional ``callback(bytes_sent, total_bytes)``

    Returns:
        True if the update completed successfully.

    Raises:
        RuntimeError: If the device rejects any OTA command.
    """
    total_size = len(firmware_data)
    _LOGGER.info(
        "Starting ESP32 BLE OTA for %s (%d bytes)",
        connection.mac_address,
        total_size,
    )

    # --- Step 1: OTA Start ---------------------------------------------------
    start_payload = CMD_OTA_START + struct.pack("<I", total_size)
    response = await connection.write_command_with_response(
        start_payload, timeout=10.0
    )
    _check_ota_response(response, 0x46)
    _LOGGER.info("ESP32 OTA started, sending firmware data...")

    # --- Step 2: OTA Data (chunked) ------------------------------------------
    offset = 0
    while offset < total_size:
        chunk = firmware_data[offset : offset + ESP32_OTA_CHUNK_SIZE]
        data_payload = CMD_OTA_DATA + chunk
        response = await connection.write_command_with_response(
            data_payload, timeout=10.0
        )
        _check_ota_response(response, 0x47)

        offset += len(chunk)
        if progress_callback:
            progress_callback(offset, total_size)

        if _LOGGER.isEnabledFor(logging.DEBUG):
            pct = int(offset * 100 / total_size)
            if pct % 10 == 0:
                _LOGGER.debug(
                    "ESP32 OTA progress: %d / %d bytes (%d%%)",
                    offset,
                    total_size,
                    pct,
                )

    # --- Step 3: OTA End ------------------------------------------------------
    response = await connection.write_command_with_response(
        CMD_OTA_END, timeout=15.0
    )
    _check_ota_response(response, 0x48)
    _LOGGER.info(
        "ESP32 OTA completed successfully for %s, device will reboot",
        connection.mac_address,
    )
    return True
