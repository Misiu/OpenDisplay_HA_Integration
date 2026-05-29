"""Integration for OpenDisplay BLE e-paper displays."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any

from opendisplay import (
    AuthenticationFailedError,
    AuthenticationRequiredError,
    BLEConnectionError,
    BLETimeoutError,
    GlobalConfig,
    OpenDisplayDevice,
    OpenDisplayError,
)

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.helpers.typing import ConfigType

if TYPE_CHECKING:
    from opendisplay.models import FirmwareVersion

from .const import (
    CONF_CACHED_DEVICE_CONFIG,
    CONF_CACHED_FIRMWARE,
    CONF_CACHED_IS_FLEX,
    CONF_ENCRYPTION_KEY,
    DOMAIN,
)
from .coordinator import OpenDisplayCoordinator
from .deep_sleep import DeepSleepQueuedUpload, deep_sleep_seconds
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
_LOGGER = logging.getLogger(__name__)

_BASE_PLATFORMS: list[Platform] = [Platform.IMAGE, Platform.SENSOR]
_FLEX_PLATFORMS = [Platform.EVENT, Platform.IMAGE, Platform.SENSOR, Platform.UPDATE]


@dataclass
class OpenDisplayRuntimeData:
    """Runtime data for an OpenDisplay config entry."""

    coordinator: OpenDisplayCoordinator
    firmware: FirmwareVersion
    device_config: GlobalConfig
    is_flex: bool
    upload_task: asyncio.Task | None = None
    config_sync_task: asyncio.Task | None = None
    deep_sleep_flush_task: asyncio.Task | None = None
    deep_sleep_upload: DeepSleepQueuedUpload | None = None
    deep_sleep_expiry_handle: asyncio.TimerHandle | None = None


type OpenDisplayConfigEntry = ConfigEntry[OpenDisplayRuntimeData]


def _serialize_device_config(device_config: GlobalConfig) -> dict[str, Any] | None:
    """Serialize GlobalConfig into plain dict for ConfigEntry storage."""
    if hasattr(device_config, "model_dump"):
        dumped = device_config.model_dump()
        if isinstance(dumped, dict):
            return dumped
    if hasattr(device_config, "dict"):
        dumped = device_config.dict()
        if isinstance(dumped, dict):
            return dumped
    if hasattr(device_config, "to_dict"):
        dumped = device_config.to_dict()
        if isinstance(dumped, dict):
            return dumped
    if is_dataclass(device_config):
        dumped = asdict(device_config)
        if isinstance(dumped, dict):
            return dumped
    return None


def _deserialize_device_config(raw: object) -> GlobalConfig | None:
    """Deserialize plain dict into GlobalConfig."""
    if not isinstance(raw, dict):
        return None
    if hasattr(GlobalConfig, "model_validate"):
        try:
            return GlobalConfig.model_validate(raw)
        except Exception:
            pass
    if hasattr(GlobalConfig, "parse_obj"):
        try:
            return GlobalConfig.parse_obj(raw)
        except Exception:
            pass
    if hasattr(GlobalConfig, "from_dict"):
        try:
            return GlobalConfig.from_dict(raw)
        except Exception:
            pass
    try:
        return GlobalConfig(**raw)
    except Exception:
        return None


def _cached_runtime_data(
    entry: OpenDisplayConfigEntry,
) -> tuple[FirmwareVersion, GlobalConfig, bool] | None:
    """Return cached runtime metadata if valid."""
    raw_firmware = entry.data.get(CONF_CACHED_FIRMWARE)
    raw_device_config = entry.data.get(CONF_CACHED_DEVICE_CONFIG)
    raw_is_flex = entry.data.get(CONF_CACHED_IS_FLEX)
    if not isinstance(raw_firmware, dict) or not isinstance(raw_is_flex, bool):
        return None
    device_config = _deserialize_device_config(raw_device_config)
    if device_config is None:
        return None
    return raw_firmware, device_config, raw_is_flex


def _deep_sleep_seconds(device_config: GlobalConfig) -> int:
    """Return deep sleep duration from device config."""
    return deep_sleep_seconds(device_config)


def _log_config_changes(
    address: str,
    previous_config: GlobalConfig,
    latest_config: GlobalConfig,
) -> None:
    """Log config changes detected between cached and live device config."""
    previous = _serialize_device_config(previous_config)
    latest = _serialize_device_config(latest_config)
    if (
        not isinstance(previous, dict)
        or not isinstance(latest, dict)
        or previous == latest
    ):
        return

    changed_keys = sorted(
        key
        for key in (set(previous.keys()) | set(latest.keys()))
        if previous.get(key) != latest.get(key)
    )
    _LOGGER.info(
        "%s: Device config changed; syncing Home Assistant cache (changed keys: %s)",
        address,
        ", ".join(changed_keys) if changed_keys else "unknown",
    )


def _cache_runtime_data(
    hass: HomeAssistant,
    entry: OpenDisplayConfigEntry,
    firmware: FirmwareVersion,
    device_config: GlobalConfig,
    is_flex: bool,
) -> None:
    """Persist runtime metadata so sleeping devices can restore quickly."""
    if not isinstance(firmware, dict):
        return
    serialized = _serialize_device_config(device_config)
    if serialized is None:
        return
    data = dict(entry.data)
    data[CONF_CACHED_FIRMWARE] = firmware
    data[CONF_CACHED_DEVICE_CONFIG] = serialized
    data[CONF_CACHED_IS_FLEX] = is_flex
    hass.config_entries.async_update_entry(entry, data=data)


def _get_encryption_key(entry: OpenDisplayConfigEntry) -> bytes | None:
    """Return the encryption key bytes from entry data, or None."""
    raw = entry.data.get(CONF_ENCRYPTION_KEY)
    if raw is None:
        return None
    if len(raw) != 32:
        raise ConfigEntryAuthFailed(
            "Stored OpenDisplay encryption key is invalid; reauthentication required"
        )
    try:
        return bytes.fromhex(raw)
    except ValueError as err:
        raise ConfigEntryAuthFailed(
            "Stored OpenDisplay encryption key is invalid; reauthentication required"
        ) from err


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the OpenDisplay integration."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: OpenDisplayConfigEntry) -> bool:
    """Set up OpenDisplay from a config entry."""
    address = entry.unique_id
    if TYPE_CHECKING:
        assert address is not None

    cached_runtime = _cached_runtime_data(entry)
    ble_device = async_ble_device_from_address(hass, address, connectable=True)
    encryption_key = _get_encryption_key(entry)
    fw: FirmwareVersion
    device_config: GlobalConfig
    is_flex: bool

    if ble_device is None:
        if cached_runtime is None or _deep_sleep_seconds(cached_runtime[1]) <= 0:
            raise ConfigEntryNotReady(
                f"Could not find OpenDisplay device with address {address}"
            )
        fw, device_config, is_flex = cached_runtime
        _LOGGER.info(
            "%s: Device not connectable at startup; using cached config "
            "(deep sleep=%ss, assumed state)",
            address,
            _deep_sleep_seconds(device_config),
        )
    else:
        try:
            async with OpenDisplayDevice(
                mac_address=address,
                ble_device=ble_device,
                encryption_key=encryption_key,
            ) as device:
                fw = await device.read_firmware_version()
                is_flex = device.is_flex
                device_config = device.config
                if TYPE_CHECKING:
                    assert device_config is not None
        except (AuthenticationFailedError, AuthenticationRequiredError) as err:
            raise ConfigEntryAuthFailed(
                f"Encryption key rejected by OpenDisplay device: {err}"
            ) from err
        except (BLEConnectionError, BLETimeoutError, OpenDisplayError) as err:
            if cached_runtime is None or _deep_sleep_seconds(cached_runtime[1]) <= 0:
                raise ConfigEntryNotReady(
                    f"Failed to connect to OpenDisplay device: {err}"
                ) from err
            fw, device_config, is_flex = cached_runtime
            _LOGGER.info(
                "%s: Startup connection failed (%s); using cached config "
                "(deep sleep=%ss, assumed state)",
                address,
                err,
                _deep_sleep_seconds(device_config),
            )
        else:
            _cache_runtime_data(hass, entry, fw, device_config, is_flex)

    coordinator = OpenDisplayCoordinator(hass, address)

    manufacturer = device_config.manufacturer
    display = device_config.displays[0]
    color_scheme_enum = display.color_scheme_enum
    color_scheme = (
        str(color_scheme_enum)
        if isinstance(color_scheme_enum, int)
        else color_scheme_enum.name
    )
    size = (
        f'{display.screen_diagonal_inches:.1f}"'
        if display.screen_diagonal_inches is not None
        else f"{display.pixel_width}x{display.pixel_height}"
    )
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(CONNECTION_BLUETOOTH, address)},
        manufacturer=manufacturer.manufacturer_name,
        model=f"{size} {color_scheme}",
        sw_version=f"{fw['major']}.{fw['minor']}",
        hw_version=(
            f"{manufacturer.board_type_name or manufacturer.board_type}"
            f" rev. {manufacturer.board_revision}"
        )
        if is_flex
        else None,
        configuration_url="https://opendisplay.org/firmware/config/"
        if is_flex
        else None,
    )

    entry.runtime_data = OpenDisplayRuntimeData(
        coordinator=coordinator,
        firmware=fw,
        device_config=device_config,
        is_flex=is_flex,
    )

    await hass.config_entries.async_forward_entry_setups(
        entry, _get_platforms(entry.runtime_data)
    )
    entry.async_on_unload(coordinator.async_start())
    was_available = coordinator.available

    async def _async_sync_runtime_config() -> None:
        """Refresh firmware/config after the device comes back online."""
        ble_online = async_ble_device_from_address(hass, address, connectable=True)
        if ble_online is None:
            return

        try:
            async with OpenDisplayDevice(
                mac_address=address,
                ble_device=ble_online,
                encryption_key=encryption_key,
            ) as device:
                latest_fw = await device.read_firmware_version()
                latest_config = device.config
                if TYPE_CHECKING:
                    assert latest_config is not None
                latest_is_flex = device.is_flex
        except (AuthenticationFailedError, AuthenticationRequiredError) as err:
            _LOGGER.debug(
                "%s: Skipping runtime config sync due to auth error: %s",
                address,
                err,
            )
            return
        except (BLEConnectionError, BLETimeoutError, OpenDisplayError) as err:
            _LOGGER.debug("%s: Runtime config sync skipped: %s", address, err)
            return

        _log_config_changes(address, entry.runtime_data.device_config, latest_config)
        entry.runtime_data.firmware = latest_fw
        entry.runtime_data.device_config = latest_config
        entry.runtime_data.is_flex = latest_is_flex
        _cache_runtime_data(hass, entry, latest_fw, latest_config, latest_is_flex)
        coordinator.async_update_listeners()

    # Register coordinator listener to refresh runtime config and flush any
    # queued deep-sleep upload when the device wakes up.
    def _on_coordinator_update() -> None:
        """Handle wake-up transitions and queued uploads on coordinator updates."""
        nonlocal was_available
        available_now = coordinator.available
        if available_now and not was_available:
            current = entry.runtime_data.config_sync_task
            if current is None or current.done():
                entry.runtime_data.config_sync_task = hass.async_create_task(
                    _async_sync_runtime_config(),
                    name=f"opendisplay_sync_config_{address}",
                )
        was_available = available_now

        flush_task = entry.runtime_data.deep_sleep_flush_task
        if flush_task is not None and flush_task.done():
            entry.runtime_data.deep_sleep_flush_task = None

        queued = entry.runtime_data.deep_sleep_upload
        if queued is None:
            return
        if queued.is_expired:
            entry.runtime_data.deep_sleep_upload = None
            if (handle := entry.runtime_data.deep_sleep_expiry_handle) is not None:
                handle.cancel()
                entry.runtime_data.deep_sleep_expiry_handle = None
            return
        if async_ble_device_from_address(hass, address, connectable=True) is None:
            _LOGGER.debug(
                "%s: Queued image still waiting; device is not connectable",
                address,
            )
            return
        if entry.runtime_data.deep_sleep_flush_task is not None:
            _LOGGER.debug("%s: Queued image flush already in progress", address)
            return

        queued_age = datetime.now() - queued.queued_at
        ttl_left = max(0, int((queued.expiry - queued_age).total_seconds()))

        _LOGGER.info(
            "%s: Device is online again; attempting queued image upload "
            "(sleep=%ss, ttl_left=%ss)",
            address,
            _deep_sleep_seconds(entry.runtime_data.device_config),
            ttl_left,
        )

        async def _flush_queued_upload() -> None:
            """Send queued upload once the device wakes up."""
            from .services import _async_connect_and_run  # noqa: PLC0415 – avoid circular import at module level

            try:
                await _async_connect_and_run(
                    hass, entry, queued.action, wrap_connection_errors=False
                )
            except (BLEConnectionError, BLETimeoutError) as err:
                current_queued = entry.runtime_data.deep_sleep_upload
                if current_queued is queued and not queued.is_expired:
                    queued_age = datetime.now() - queued.queued_at
                    ttl_left = max(0, int((queued.expiry - queued_age).total_seconds()))
                    _LOGGER.info(
                        "%s: Queued image upload deferred again; keeping queue "
                        "(ttl_left=%ss): %s",
                        address,
                        ttl_left,
                        err,
                    )
                else:
                    _LOGGER.debug(
                        "%s: Queued image upload failed after wake-up but queue is no "
                        "longer active: %s",
                        address,
                        err,
                    )
            except HomeAssistantError as err:
                if entry.runtime_data.deep_sleep_upload is queued:
                    entry.runtime_data.deep_sleep_upload = None
                    if (
                        handle := entry.runtime_data.deep_sleep_expiry_handle
                    ) is not None:
                        handle.cancel()
                        entry.runtime_data.deep_sleep_expiry_handle = None
                _LOGGER.warning(
                    "%s: Failed to send queued image after wake-up; "
                    "dropping queue: %s",
                    address,
                    err,
                )
            else:
                if entry.runtime_data.deep_sleep_upload is queued:
                    entry.runtime_data.deep_sleep_upload = None
                    if (
                        handle := entry.runtime_data.deep_sleep_expiry_handle
                    ) is not None:
                        handle.cancel()
                        entry.runtime_data.deep_sleep_expiry_handle = None
                _LOGGER.info("%s: Queued image sent to display", address)
            finally:
                if (
                    task := asyncio.current_task()
                ) is not None and entry.runtime_data.deep_sleep_flush_task is task:
                    entry.runtime_data.deep_sleep_flush_task = None

        entry.runtime_data.deep_sleep_flush_task = hass.async_create_task(
            _flush_queued_upload(),
            name=f"opendisplay_deepsleep_flush_{address}",
        )

    entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))

    return True


def _get_platforms(runtime_data: OpenDisplayRuntimeData) -> list[Platform]:
    """Return the platforms to set up for this device."""
    platforms = list(_FLEX_PLATFORMS if runtime_data.is_flex else _BASE_PLATFORMS)
    if not runtime_data.is_flex and runtime_data.device_config.touch_controllers:
        platforms.append(Platform.EVENT)
    return platforms


async def async_unload_entry(
    hass: HomeAssistant, entry: OpenDisplayConfigEntry
) -> bool:
    """Unload a config entry."""
    if (handle := entry.runtime_data.deep_sleep_expiry_handle) is not None:
        handle.cancel()
        entry.runtime_data.deep_sleep_expiry_handle = None

    if (task := entry.runtime_data.upload_task) and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    if (task := entry.runtime_data.deep_sleep_flush_task) and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    entry.runtime_data.deep_sleep_flush_task = None
    if (task := entry.runtime_data.config_sync_task) and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    return await hass.config_entries.async_unload_platforms(
        entry, _get_platforms(entry.runtime_data)
    )
