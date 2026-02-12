"""Nordic BLE DFU protocol implementation for NRF52840 OTA updates."""
import asyncio
import io
import logging
import struct
import zipfile
from dataclasses import dataclass

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

_LOGGER = logging.getLogger(__name__)

# Nordic DFU Service and Characteristic UUIDs
DFU_SERVICE_UUID = "0000fe59-0000-1000-8000-00805f9b34fb"
DFU_CONTROL_POINT_UUID = "8ec90001-f315-4f60-9fb8-838830daea50"
DFU_PACKET_UUID = "8ec90002-f315-4f60-9fb8-838830daea50"


class DfuOpcode:
    """DFU opcodes for control point commands."""

    CREATE = 0x01
    SET_PRN = 0x02
    CALCULATE_CRC = 0x03
    EXECUTE = 0x04
    SELECT = 0x06
    RESPONSE = 0x60


class DfuObjectType:
    """DFU object types."""

    COMMAND = 0x01
    DATA = 0x02


class DfuResult:
    """DFU result codes."""

    SUCCESS = 0x01
    INVALID = 0x02
    NOT_SUPPORTED = 0x03
    INVALID_SIZE = 0x04
    CRC_ERROR = 0x05
    OPERATION_FAILED = 0x0A


DFU_DATA_OBJECT_MAX_SIZE = 4096


@dataclass
class DfuPackage:
    """Parsed DFU package from .zip file."""

    init_packet: bytes
    firmware: bytes


def parse_dfu_package(zip_data: bytes) -> DfuPackage:
    """Parse an Adafruit nrfutil DFU package (.zip).

    The .zip contains:
    - manifest.json
    - *.dat (init packet)
    - *.bin (firmware binary)

    Args:
        zip_data: Raw bytes of the .zip DFU package

    Returns:
        DfuPackage: Parsed package with init_packet and firmware

    Raises:
        ValueError: If package is missing required files
    """
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        dat_file = None
        bin_file = None

        for name in zf.namelist():
            if name.endswith(".dat"):
                dat_file = zf.read(name)
            elif name.endswith(".bin"):
                bin_file = zf.read(name)

        if not dat_file or not bin_file:
            raise ValueError("DFU package missing .dat or .bin file")

        return DfuPackage(init_packet=dat_file, firmware=bin_file)


class NordicDfuController:
    """BLE DFU controller for Nordic/Adafruit bootloader."""

    def __init__(self, client: BleakClient) -> None:
        self._client = client
        self._response_event = asyncio.Event()
        self._response_data: bytes = b""

    def _notification_handler(self, _sender: int, data: bytearray) -> None:
        """Handle DFU control point notifications."""
        self._response_data = bytes(data)
        self._response_event.set()

    async def _write_control_point(
        self, data: bytes, *, wait_response: bool = True
    ) -> bytes:
        """Write to DFU control point and optionally wait for response."""
        self._response_event.clear()
        await self._client.write_gatt_char(
            DFU_CONTROL_POINT_UUID, data, response=True
        )

        if wait_response:
            await asyncio.wait_for(self._response_event.wait(), timeout=10.0)
            return self._response_data
        return b""

    async def _write_data(self, data: bytes) -> None:
        """Write to DFU data characteristic."""
        chunk_size = min(len(data), 200)
        offset = 0
        while offset < len(data):
            chunk = data[offset : offset + chunk_size]
            await self._client.write_gatt_char(
                DFU_PACKET_UUID, chunk, response=False
            )
            offset += len(chunk)

    async def start(self) -> None:
        """Initialize DFU process."""
        await self._client.start_notify(
            DFU_CONTROL_POINT_UUID, self._notification_handler
        )

        prn_cmd = struct.pack("<BH", DfuOpcode.SET_PRN, 0)
        response = await self._write_control_point(prn_cmd)
        self._check_response(response, DfuOpcode.SET_PRN)

    async def send_init_packet(self, init_packet: bytes) -> None:
        """Send init packet (command object)."""
        select_cmd = struct.pack("<BB", DfuOpcode.SELECT, DfuObjectType.COMMAND)
        response = await self._write_control_point(select_cmd)
        self._check_response(response, DfuOpcode.SELECT)

        create_cmd = struct.pack(
            "<BBI", DfuOpcode.CREATE, DfuObjectType.COMMAND, len(init_packet)
        )
        response = await self._write_control_point(create_cmd)
        self._check_response(response, DfuOpcode.CREATE)

        await self._write_data(init_packet)

        crc_cmd = struct.pack("<B", DfuOpcode.CALCULATE_CRC)
        response = await self._write_control_point(crc_cmd)
        self._check_response(response, DfuOpcode.CALCULATE_CRC)

        exec_cmd = struct.pack("<B", DfuOpcode.EXECUTE)
        response = await self._write_control_point(exec_cmd)
        self._check_response(response, DfuOpcode.EXECUTE)

    async def send_firmware(
        self, firmware: bytes, progress_callback=None
    ) -> None:
        """Send firmware data in objects.

        Args:
            firmware: Firmware binary data
            progress_callback: Optional callback(bytes_sent, total_bytes)
        """
        select_cmd = struct.pack("<BB", DfuOpcode.SELECT, DfuObjectType.DATA)
        response = await self._write_control_point(select_cmd)
        self._check_response(response, DfuOpcode.SELECT)

        total_size = len(firmware)
        offset = 0

        while offset < total_size:
            remaining = total_size - offset
            object_size = min(DFU_DATA_OBJECT_MAX_SIZE, remaining)

            create_cmd = struct.pack(
                "<BBI", DfuOpcode.CREATE, DfuObjectType.DATA, object_size
            )
            response = await self._write_control_point(create_cmd)
            self._check_response(response, DfuOpcode.CREATE)

            object_data = firmware[offset : offset + object_size]
            await self._write_data(object_data)

            crc_cmd = struct.pack("<B", DfuOpcode.CALCULATE_CRC)
            response = await self._write_control_point(crc_cmd)
            self._check_response(response, DfuOpcode.CALCULATE_CRC)

            exec_cmd = struct.pack("<B", DfuOpcode.EXECUTE)
            response = await self._write_control_point(exec_cmd)
            self._check_response(response, DfuOpcode.EXECUTE)

            offset += object_size

            if progress_callback:
                progress_callback(offset, total_size)

            _LOGGER.debug("DFU progress: %d / %d bytes", offset, total_size)

    def _check_response(self, response: bytes, expected_opcode: int) -> None:
        """Validate DFU response."""
        if len(response) < 3:
            raise BleakError(f"DFU response too short: {response.hex()}")

        if response[0] != DfuOpcode.RESPONSE:
            raise BleakError(
                f"Expected response opcode 0x60, got 0x{response[0]:02x}"
            )

        if response[1] != expected_opcode:
            raise BleakError(
                f"Response for wrong opcode: expected 0x{expected_opcode:02x},"
                f" got 0x{response[1]:02x}"
            )

        if response[2] != DfuResult.SUCCESS:
            raise BleakError(
                f"DFU operation failed with result code: 0x{response[2]:02x}"
            )

    async def stop(self) -> None:
        """Clean up DFU controller."""
        try:
            await self._client.stop_notify(DFU_CONTROL_POINT_UUID)
        except Exception:  # noqa: BLE001
            pass


async def perform_dfu_update(
    mac_address: str,
    dfu_package_data: bytes,
    progress_callback=None,
    scan_timeout: float = 30.0,
) -> bool:
    """Perform complete DFU update on a device already in DFU bootloader mode.

    Args:
        mac_address: Original device MAC address
        dfu_package_data: Raw bytes of the .zip DFU package
        progress_callback: Optional callback(bytes_sent, total_bytes)
        scan_timeout: How long to scan for the DFU bootloader

    Returns:
        bool: True if update completed successfully
    """
    package = parse_dfu_package(dfu_package_data)
    _LOGGER.info(
        "DFU package: init=%d bytes, firmware=%d bytes",
        len(package.init_packet),
        len(package.firmware),
    )

    _LOGGER.info("Scanning for DFU bootloader (timeout=%ds)...", scan_timeout)

    def _match_dfu_device(_device, adv_data):
        """Match a device advertising the DFU service."""
        if DFU_SERVICE_UUID in (adv_data.service_uuids or []):
            return True
        if adv_data.local_name and "dfu" in adv_data.local_name.lower():
            return True
        return False

    dfu_device = await BleakScanner.find_device_by_filter(
        _match_dfu_device, timeout=scan_timeout
    )

    if not dfu_device:
        _LOGGER.error(
            "DFU bootloader not found after scanning for %ds", scan_timeout
        )
        return False

    _LOGGER.info(
        "Found DFU bootloader: %s (%s)", dfu_device.name, dfu_device.address
    )

    async with BleakClient(dfu_device, timeout=15.0) as client:
        dfu = NordicDfuController(client)

        try:
            await dfu.start()
            _LOGGER.info("Sending init packet...")
            await dfu.send_init_packet(package.init_packet)
            _LOGGER.info(
                "Sending firmware (%d bytes)...", len(package.firmware)
            )
            await dfu.send_firmware(package.firmware, progress_callback)
            _LOGGER.info("DFU update completed successfully!")
            return True

        except Exception as err:
            _LOGGER.error("DFU update failed: %s", err)
            raise
        finally:
            await dfu.stop()
