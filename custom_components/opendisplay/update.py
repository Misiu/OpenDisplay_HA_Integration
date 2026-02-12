from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging

from awesomeversion import AwesomeVersion
from homeassistant.components.labs import async_is_preview_feature_enabled, async_listen
from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble import BLEConnection, BLEDeviceMetadata, get_protocol_by_name
from .ble.esp32_ota import perform_esp32_ota
from .ble.nrf_dfu import parse_dfu_package, perform_dfu_update
from .ble.protocol_open_display import OpenDisplayProtocol
from .const import DOMAIN
from .entity import OpenDisplayBLEEntity
from .runtime_data import OpenDisplayBLERuntimeData
from .util import is_ble_entry

_LOGGER = logging.getLogger(__name__)

GITHUB_LATEST_URL = "https://api.github.com/repos/OpenDisplay-org/Firmware/releases/latest"
DEFAULT_RELEASE_URL = "https://github.com/OpenDisplay-org/Firmware/releases"
CACHE_DURATION = timedelta(hours=6)

# IC type values from OpenDisplay TLV config (system.ic_type)
IC_TYPE_NRF52840 = 1
IC_TYPE_ESP32_S3 = 2
IC_TYPE_ESP32_C3 = 3
IC_TYPE_ESP32_C6 = 4

# Mapping from IC type to firmware asset search prefix for GitHub releases.
# NRF52840 uses the DFU .zip package; ESP32 variants use application .bin.
_IC_TYPE_ASSET_PREFIXES: dict[int, str] = {
    IC_TYPE_NRF52840: "NRF52840",
    IC_TYPE_ESP32_S3: "esp32-s3-",
    IC_TYPE_ESP32_C3: "esp32-c3-",
    IC_TYPE_ESP32_C6: "esp32-c6-",
}

_IC_TYPE_NAMES: dict[int, str] = {
    IC_TYPE_NRF52840: "NRF52840",
    IC_TYPE_ESP32_S3: "ESP32-S3",
    IC_TYPE_ESP32_C3: "ESP32-C3",
    IC_TYPE_ESP32_C6: "ESP32-C6",
}


async def async_setup_entry(
        hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up OpenDisplay firmware update entity for BLE entries when Labs is enabled."""
    entry_data = entry.runtime_data
    if not is_ble_entry(entry_data):
        return

    added: dict[str, OpenDisplayBleUpdateEntity] = {}

    async def _remove_entity(entity: "OpenDisplayBleUpdateEntity") -> None:
        await entity.async_remove()
        if entity.entity_id:
            from homeassistant.helpers import entity_registry as er

            er.async_get(hass).async_remove(entity.entity_id)

    @callback
    def _sync_feature_state() -> None:
        enabled = async_is_preview_feature_enabled(hass, DOMAIN, "opendisplay_ble_updates")

        if enabled and entry.entry_id not in added:
            metadata = BLEDeviceMetadata(entry_data.device_metadata or {})
            if not metadata.is_open_display:
                _LOGGER.debug(
                    "Skipping update entity for %s (not OpenDisplay)", entry_data.mac_address
                )
                return  # OpenDisplay-only
            _LOGGER.debug(
                "Enabling OpenDisplay firmware update entity for %s", entry_data.mac_address
            )
            entity = OpenDisplayBleUpdateEntity(hass, entry, entry_data)
            added[entry.entry_id] = entity
            async_add_entities([entity])
            return

        if not enabled and (entity := added.pop(entry.entry_id, None)):
            _LOGGER.debug(
                "Labs disabled; removing OpenDisplay firmware update entity for %s",
                entry_data.mac_address,
            )
            hass.async_create_task(_remove_entity(entity))

    # Listen for Labs toggle
    entry.async_on_unload(
        async_listen(hass, DOMAIN, "opendisplay_ble_updates", _sync_feature_state)
    )

    # Apply current state
    _sync_feature_state()


class OpenDisplayBleUpdateEntity(OpenDisplayBLEEntity, UpdateEntity):
    """Firmware update indicator for OpenDisplay tags."""

    _attr_has_entity_name = True
    _attr_translation_key = "opendisplay_ble_firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.RELEASE_NOTES
        | UpdateEntityFeature.PROGRESS
    )
    _attr_should_poll = True
    _attr_entity_registry_enabled_default = True

    def __init__(
            self,
            hass: HomeAssistant,
            entry,
            runtime_data: OpenDisplayBLERuntimeData,
    ) -> None:
        self.hass = hass
        self._entry_data = runtime_data
        self._entry = entry
        self._latest_version: str | None = None
        self._release_url: str | None = None
        self._release_notes: str | None = None
        self._last_checked: datetime | None = None
        self._last_fetch_error: str | None = None
        self._mac = runtime_data.mac_address
        self._name = runtime_data.name
        self._session = async_get_clientsession(hass)
        self._is_updating = False
        super().__init__(self._mac, self._name, entry)
        self._attr_unique_id = f"opendisplay_ble_{self._mac}_firmware_update"
        self._attr_installed_version = self._compute_installed_version()

    @property
    def available(self) -> bool:
        """Keep the update entity available even if the tag is offline."""
        return True

    def _compute_installed_version(self) -> str | None:
        metadata_dict = self._entry_data.device_metadata or {}
        metadata = BLEDeviceMetadata(metadata_dict)
        fw = metadata.fw_version
        if fw not in ("", 0, None):
            _LOGGER.debug("Firmware from metadata for %s: %s", self._mac, fw)
            return str(fw)

        from homeassistant.helpers import device_registry as dr

        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, f"ble_{self._mac}")},
        )
        if device and device.sw_version and device.sw_version.lower() != "unknown":
            _LOGGER.debug(
                "Firmware from device registry for %s: %s",
                self._mac,
                device.sw_version,
            )
            return device.sw_version

        _LOGGER.debug(
            "No firmware version available for %s; metadata=%s registry=%s",
            self._mac,
            metadata_dict,
            device.sw_version if device else None,
        )
        return None

    @property
    def installed_version(self) -> str | None:
        return self._attr_installed_version

    @property
    def latest_version(self) -> str | None:
        return self._latest_version

    @property
    def release_url(self) -> str | None:
        return self._release_url or DEFAULT_RELEASE_URL

    async def async_release_notes(self) -> str | None:
        return self._release_notes

    async def async_added_to_hass(self) -> None:
        # Ensure we have fresh installed_version and fetch latest once on add
        self._attr_installed_version = self._compute_installed_version()
        await self.async_update()
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Refresh installed_version (in case metadata changed) and latest version from GitHub (cached)."""
        self._attr_installed_version = self._compute_installed_version()

        now = datetime.utcnow()
        if self._last_checked and now - self._last_checked < CACHE_DURATION:
            return

        try:
            async with self._session.get(
                    GITHUB_LATEST_URL,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "HomeAssistant-OpenDisplay-Firmware-update-entity",
                    },
                    raise_for_status=True,
            ) as resp:
                data = await resp.json()

            tag = data.get("tag_name") or data.get("name")
            if not tag:
                _LOGGER.debug("No tag_name/name in GitHub response for %s", self._mac)
                return

            normalized = tag[1:] if tag.startswith("v") else tag
            self._latest_version = normalized
            self._release_url = data.get("html_url") or DEFAULT_RELEASE_URL
            self._release_notes = data.get("body")
            self._last_checked = now
            self._last_fetch_error = None
        except Exception as err:
            msg = str(err)
            if msg != self._last_fetch_error:
                _LOGGER.error("Failed to fetch OpenDisplay firmware latest version: %s", msg)
                self._last_fetch_error = msg
            else:
                _LOGGER.debug("Failed to fetch OpenDisplay firmware latest version: %s", msg)

    def _get_ic_type(self) -> int | None:
        """Return IC type from device metadata, or None if unavailable."""
        return (
            (self._entry_data.device_metadata or {})
            .get("open_display_config", {})
            .get("system", {})
            .get("ic_type")
        )

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Use AwesomeVersion for comparison."""
        try:
            return AwesomeVersion(latest_version) > AwesomeVersion(installed_version)
        except Exception:
            return latest_version != installed_version

    async def async_install(
        self, version: str | None, backup: bool, **kwargs
    ) -> None:
        """Install firmware update via BLE OTA.

        The update method depends on the device IC type:
        - NRF52840: Download .zip DFU package → enter DFU bootloader → Nordic DFU protocol
        - ESP32-S3/C3/C6: Download .bin → stream via BLE OTA commands (0x0046/47/48)
        """
        if self._is_updating:
            _LOGGER.warning("Update already in progress for %s", self._mac)
            return

        self._is_updating = True

        try:
            target_version = version or self._latest_version
            if not target_version:
                raise HomeAssistantError("No target version available")

            ic_type = self._get_ic_type()
            _LOGGER.info(
                "Starting OTA for %s (ic_type=%s, version=%s)",
                self._mac,
                ic_type,
                target_version,
            )

            self._attr_in_progress = True
            self.async_write_ha_state()

            if ic_type == IC_TYPE_NRF52840:
                await self._install_nrf52840(target_version)
            elif ic_type in (IC_TYPE_ESP32_S3, IC_TYPE_ESP32_C3, IC_TYPE_ESP32_C6):
                await self._install_esp32(target_version, ic_type)
            else:
                raise HomeAssistantError(
                    f"Unknown IC type {ic_type} — cannot determine OTA method"
                )

            # Mark update as complete
            self._attr_installed_version = target_version
            self._attr_in_progress = False
            self.async_write_ha_state()

            _LOGGER.info(
                "Firmware update complete for %s: now running %s",
                self._mac,
                target_version,
            )

        except Exception:
            self._attr_in_progress = False
            self.async_write_ha_state()
            raise
        finally:
            self._is_updating = False

    # ------------------------------------------------------------------
    # NRF52840: Nordic DFU via bootloader
    # ------------------------------------------------------------------

    async def _install_nrf52840(self, target_version: str) -> None:
        """Install firmware on NRF52840 via Nordic DFU bootloader."""
        # Download DFU package
        dfu_url = await self._get_firmware_download_url(
            target_version, IC_TYPE_NRF52840
        )
        if not dfu_url:
            raise HomeAssistantError(
                f"Could not find NRF52840.zip in release {target_version}"
            )

        _LOGGER.info("Downloading NRF52840 DFU package from %s", dfu_url)
        async with self._session.get(dfu_url) as resp:
            if resp.status != 200:
                raise HomeAssistantError(
                    f"Failed to download DFU package: HTTP {resp.status}"
                )
            dfu_data = await resp.read()

        _LOGGER.info("Downloaded DFU package: %d bytes", len(dfu_data))

        # Validate package structure
        try:
            parse_dfu_package(dfu_data)
        except ValueError as err:
            raise HomeAssistantError(f"Invalid DFU package: {err}") from err

        # Enter DFU bootloader via command 0x0044
        protocol = get_protocol_by_name("open_display")
        assert isinstance(protocol, OpenDisplayProtocol)

        _LOGGER.info("Sending DFU mode command to %s", self._mac)
        async with BLEConnection(
            self.hass, self._mac, protocol.service_uuid, protocol
        ) as conn:
            success = await protocol.enter_dfu_mode(conn)
            if not success:
                raise HomeAssistantError(
                    "Device rejected DFU mode command (may not be NRF52840)"
                )

        # Wait for device to reset into bootloader
        _LOGGER.info("Waiting for device to enter DFU bootloader...")
        await asyncio.sleep(3)

        # Perform Nordic DFU flash
        def _progress_callback(bytes_sent, total_bytes):
            progress = int((bytes_sent / total_bytes) * 100)
            self._attr_in_progress = progress
            self.async_write_ha_state()

        success = await perform_dfu_update(
            mac_address=self._mac,
            dfu_package_data=dfu_data,
            progress_callback=_progress_callback,
            scan_timeout=30.0,
        )
        if not success:
            raise HomeAssistantError("NRF52840 DFU update failed")

        _LOGGER.info("NRF52840 DFU complete, waiting for reboot...")
        await asyncio.sleep(5)

    # ------------------------------------------------------------------
    # ESP32: BLE OTA via commands 0x0046/0x0047/0x0048
    # ------------------------------------------------------------------

    async def _install_esp32(self, target_version: str, ic_type: int) -> None:
        """Install firmware on ESP32 via BLE OTA protocol."""
        fw_url = await self._get_firmware_download_url(target_version, ic_type)
        if not fw_url:
            chip_name = _IC_TYPE_NAMES.get(ic_type, f"ic_type={ic_type}")
            raise HomeAssistantError(
                f"Could not find firmware .bin for {chip_name}"
                f" in release {target_version}"
            )

        _LOGGER.info("Downloading ESP32 firmware from %s", fw_url)
        async with self._session.get(fw_url) as resp:
            if resp.status != 200:
                raise HomeAssistantError(
                    f"Failed to download firmware: HTTP {resp.status}"
                )
            fw_data = await resp.read()

        _LOGGER.info("Downloaded ESP32 firmware: %d bytes", len(fw_data))

        if len(fw_data) == 0:
            raise HomeAssistantError("Downloaded firmware file is empty")

        # Stream firmware over BLE
        protocol = get_protocol_by_name("open_display")
        assert isinstance(protocol, OpenDisplayProtocol)

        def _progress_callback(bytes_sent, total_bytes):
            progress = int((bytes_sent / total_bytes) * 100)
            self._attr_in_progress = progress
            self.async_write_ha_state()

        async with BLEConnection(
            self.hass, self._mac, protocol.service_uuid, protocol
        ) as conn:
            await perform_esp32_ota(
                connection=conn,
                firmware_data=fw_data,
                progress_callback=_progress_callback,
            )

        _LOGGER.info("ESP32 OTA complete, waiting for reboot...")
        await asyncio.sleep(5)

    # ------------------------------------------------------------------
    # Firmware asset resolution
    # ------------------------------------------------------------------

    async def _get_firmware_download_url(
        self, version: str, ic_type: int
    ) -> str | None:
        """Find the download URL for the correct firmware asset.

        For NRF52840 this looks for ``NRF52840.zip``.
        For ESP32 variants this looks for ``esp32-{variant}-*.bin``
        (excluding ``*_full.bin`` merged images).

        Args:
            version: Release version tag (e.g. "1.2" or "v1.2")
            ic_type: Device IC type constant

        Returns:
            Browser download URL for the asset, or *None* if not found.
        """
        prefix = _IC_TYPE_ASSET_PREFIXES.get(ic_type)
        if prefix is None:
            return None

        tags_to_try = (
            [version, f"v{version}"]
            if not version.startswith("v")
            else [version, version[1:]]
        )

        for tag in tags_to_try:
            url = (
                "https://api.github.com/repos/OpenDisplay-org/"
                f"Firmware/releases/tags/{tag}"
            )
            try:
                async with self._session.get(
                    url,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "HomeAssistant-OpenDisplay-OTA",
                    },
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()

                    for asset in data.get("assets", []):
                        name = asset.get("name", "")
                        if ic_type == IC_TYPE_NRF52840:
                            # Exact match for NRF52840.zip
                            if name == "NRF52840.zip":
                                return asset.get("browser_download_url")
                        else:
                            # ESP32: match prefix, must end with .bin,
                            # skip merged *_full.bin images
                            if (
                                name.startswith(prefix)
                                and name.endswith(".bin")
                                and not name.endswith("_full.bin")
                            ):
                                return asset.get("browser_download_url")
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Failed to query GitHub release for tag %s",
                    tag,
                    exc_info=True,
                )
                continue

        return None
