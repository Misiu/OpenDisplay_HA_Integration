"""Tests for Nordic OTA support in the OpenDisplay update entity."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.update import (
    ATTR_INSTALLED_VERSION,
    ATTR_SUPPORTED_FEATURES,
    DOMAIN as UPDATE_DOMAIN,
    SERVICE_INSTALL,
    UpdateEntityFeature,
)
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from opendisplay.models.enums import ICType
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from . import DEVICE_CONFIG, VALID_SERVICE_INFO
from .bluetooth import inject_bluetooth_service_info

ENTITY = "update.opendisplay_1234_firmware"
LATEST = "v9.9.9"
GITHUB_LATEST = "https://api.github.com/repos/OpenDisplay/Firmware/releases/latest"


@pytest.fixture
def platforms() -> list[Platform]:
    """Only set up the update platform."""
    return [Platform.UPDATE]


@pytest.fixture
def device_config():
    """Use an EN04-class nRF52840 OpenDisplay device."""
    return replace(
        DEVICE_CONFIG,
        system=replace(DEVICE_CONFIG.system, ic_type=ICType.NRF52840),
    )


@pytest.fixture(autouse=True)
def mock_github(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Serve an nRF firmware release."""
    aioclient_mock.get(
        GITHUB_LATEST, json={"tag_name": LATEST, "body": "nRF release notes"}
    )
    return aioclient_mock


@pytest.fixture
def setup_seen(
    hass: HomeAssistant, setup_entry: Callable[[], Awaitable[None]]
) -> Callable[[], Awaitable[None]]:
    """Set up the entry and let one advertisement through."""

    async def _setup() -> None:
        await setup_entry()
        inject_bluetooth_service_info(hass, VALID_SERVICE_INFO)
        await hass.async_block_till_done()

    return _setup


async def test_install_is_offered_for_nrf52840(
    hass: HomeAssistant,
    setup_seen: Callable[[], Awaitable[None]],
) -> None:
    """EN04/nRF52840 exposes the Home Assistant install action."""
    await setup_seen()

    features = hass.states.get(ENTITY).attributes[ATTR_SUPPORTED_FEATURES]
    assert features & UpdateEntityFeature.INSTALL
    assert features & UpdateEntityFeature.PROGRESS


async def test_nrf_install_uses_legacy_dfu_and_verifies_reboot(
    hass: HomeAssistant,
    setup_seen: Callable[[], Awaitable[None]],
) -> None:
    """The nRF install path triggers DFU, flashes, then verifies app-mode reboot."""
    await setup_seen()

    dfu_device = MagicMock(address="AA:BB:CC:DD:EE:00")
    app_device = MagicMock(address="AA:BB:CC:DD:EE:FF")
    open_display = AsyncMock()
    open_display.__aenter__.return_value = open_display
    open_display.__aexit__.return_value = False

    with (
        patch(
            "custom_components.opendisplay.update.OpenDisplayFirmwareUpdateEntity._download_asset",
            AsyncMock(return_value=b"firmware"),
        ),
        patch(
            "custom_components.opendisplay.update.async_ble_device_from_address",
            return_value=app_device,
        ),
        patch(
            "custom_components.opendisplay.update.OpenDisplayDevice",
            return_value=open_display,
        ),
        patch(
            "custom_components.opendisplay.update.find_nrf_dfu_device",
            AsyncMock(return_value=dfu_device),
        ) as find_dfu,
        patch(
            "custom_components.opendisplay.update.perform_nrf_dfu",
            AsyncMock(),
        ) as perform_dfu,
        patch(
            "custom_components.opendisplay.update.OpenDisplayFirmwareUpdateEntity._verify_nrf_reboot",
            AsyncMock(),
        ) as verify_reboot,
    ):
        await hass.services.async_call(
            UPDATE_DOMAIN,
            SERVICE_INSTALL,
            {ATTR_ENTITY_ID: ENTITY},
            blocking=True,
        )

    open_display.trigger_dfu_bootloader.assert_awaited_once()
    find_dfu.assert_awaited_once_with("AA:BB:CC:DD:EE:FF")
    perform_dfu.assert_awaited_once()
    assert perform_dfu.await_args.args[:2] == (b"firmware", dfu_device)
    verify_reboot.assert_awaited_once()
    assert verify_reboot.await_args.args[0] == LATEST
    assert hass.states.get(ENTITY).attributes[ATTR_INSTALLED_VERSION] == LATEST
