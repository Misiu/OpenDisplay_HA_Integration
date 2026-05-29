"""Tests for deep-sleep restart/runtime config sync behavior."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import custom_components.opendisplay as opendisplay_integration
from custom_components.opendisplay import async_setup_entry
from custom_components.opendisplay.deep_sleep import DeepSleepQueuedUpload


def _make_device_config(deep_sleep_seconds: int) -> SimpleNamespace:
    """Create a minimal device config object used by async_setup_entry."""
    return SimpleNamespace(
        power=SimpleNamespace(deep_sleep_time_seconds=deep_sleep_seconds),
        manufacturer=SimpleNamespace(
            manufacturer_name="OpenDisplay",
            board_type_name="TestBoard",
            board_type=1,
            board_revision="1",
        ),
        displays=[
            SimpleNamespace(
                color_scheme_enum=SimpleNamespace(name="BW"),
                screen_diagonal_inches=2.9,
                pixel_width=296,
                pixel_height=128,
            )
        ],
        touch_controllers=[],
    )


class _FakeCoordinator:
    """Minimal coordinator stub for setup/listener tests."""

    def __init__(self) -> None:
        self.available = False
        self.listener = None
        self.update_listeners_called = False

    def async_start(self):
        return lambda: None

    def async_add_listener(self, listener):
        self.listener = listener
        return lambda: None

    def async_update_listeners(self) -> None:
        self.update_listeners_called = True


def _make_hass() -> MagicMock:
    """Create minimal hass mock that can schedule tasks and forward platforms."""
    hass = MagicMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    hass.config_entries.async_update_entry = MagicMock()
    hass_tasks: list[asyncio.Task] = []

    def _create_task(coro, *, name=None):
        task = asyncio.create_task(coro, name=name)
        hass_tasks.append(task)
        return task

    hass.async_create_task = MagicMock(side_effect=_create_task)
    hass._test_tasks = hass_tasks
    return hass


def _make_entry() -> MagicMock:
    """Create minimal config entry mock."""
    entry = MagicMock()
    entry.unique_id = "AA:BB:CC:DD:EE:FF"
    entry.entry_id = "entry-1"
    entry.data = {}
    entry.async_on_unload = MagicMock()
    return entry


@pytest.mark.asyncio
async def test_restart_during_deep_sleep_uses_cached_runtime_and_syncs_when_available() -> None:
    """Restart while sleeping uses cache and later syncs on availability edge."""
    hass = _make_hass()
    entry = _make_entry()
    coordinator = _FakeCoordinator()

    cached_config = _make_device_config(300)
    latest_config = _make_device_config(600)
    latest_fw = {"major": 9, "minor": 9}

    class _FakeDevice:
        def __init__(self, **kwargs) -> None:
            self.is_flex = False
            self.config = latest_config

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def read_firmware_version(self):
            return latest_fw

    with (
        patch(
            "custom_components.opendisplay._cached_runtime_data",
            return_value=({"major": 1, "minor": 0}, cached_config, False),
        ),
        patch(
            "custom_components.opendisplay.OpenDisplayCoordinator",
            return_value=coordinator,
        ),
        patch("custom_components.opendisplay.dr.async_get", return_value=MagicMock()),
        patch("custom_components.opendisplay.OpenDisplayDevice", _FakeDevice),
        patch(
            "custom_components.opendisplay.async_ble_device_from_address",
            side_effect=[None, MagicMock()],
        ),
        patch("custom_components.opendisplay._cache_runtime_data"),
    ):
        assert await async_setup_entry(hass, entry) is True
        assert entry.runtime_data.device_config.power.deep_sleep_time_seconds == 300

        coordinator.available = True
        coordinator.listener()
        await asyncio.gather(*hass._test_tasks)

    assert entry.runtime_data.device_config.power.deep_sleep_time_seconds == 600
    assert coordinator.update_listeners_called is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial_sleep", "latest_sleep"),
    [
        (300, 300),  # config unchanged
        (300, 900),  # deep-sleep time changed
        (300, 0),  # deep-sleep disabled
        (0, 300),  # deep-sleep enabled
    ],
)
async def test_runtime_config_sync_updates_deep_sleep_value_without_restart(
    initial_sleep: int,
    latest_sleep: int,
) -> None:
    """Live availability transition refreshes runtime deep-sleep config."""
    hass = _make_hass()
    entry = _make_entry()
    coordinator = _FakeCoordinator()

    initial_config = _make_device_config(initial_sleep)
    latest_config = _make_device_config(latest_sleep)
    fw_values = [{"major": 1, "minor": 0}, {"major": 2, "minor": 3}]
    config_values = [initial_config, latest_config]

    class _FakeDevice:
        def __init__(self, **kwargs) -> None:
            self.is_flex = False
            self.config = config_values.pop(0)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def read_firmware_version(self):
            return fw_values.pop(0)

    with (
        patch("custom_components.opendisplay._cached_runtime_data", return_value=None),
        patch(
            "custom_components.opendisplay.OpenDisplayCoordinator",
            return_value=coordinator,
        ),
        patch("custom_components.opendisplay.dr.async_get", return_value=MagicMock()),
        patch("custom_components.opendisplay.OpenDisplayDevice", _FakeDevice),
        patch(
            "custom_components.opendisplay.async_ble_device_from_address",
            side_effect=[MagicMock(), MagicMock()],
        ),
        patch(
            "custom_components.opendisplay._cache_runtime_data"
        ) as mock_cache_runtime_data,
    ):
        assert await async_setup_entry(hass, entry) is True
        assert entry.runtime_data.device_config.power.deep_sleep_time_seconds == initial_sleep

        coordinator.available = True
        coordinator.listener()
        await asyncio.gather(*hass._test_tasks)

    assert entry.runtime_data.device_config.power.deep_sleep_time_seconds == latest_sleep
    assert mock_cache_runtime_data.call_args.args[3] == latest_config


@pytest.mark.asyncio
async def test_queued_upload_is_kept_when_wake_flush_has_connection_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Wake-up flush keeps queued image for later retry on transient BLE errors."""
    hass = _make_hass()
    entry = _make_entry()
    coordinator = _FakeCoordinator()
    initial_config = _make_device_config(300)

    class _FakeDevice:
        def __init__(self, **kwargs) -> None:
            self.is_flex = False
            self.config = initial_config

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def read_firmware_version(self):
            return {"major": 1, "minor": 0}

    queued_action = AsyncMock()
    queued_upload = DeepSleepQueuedUpload(
        action=queued_action,
        jpeg_bytes=b"",
        queued_at=datetime.now() - timedelta(seconds=20),
        expiry=timedelta(seconds=330),
    )
    expiry_handle = MagicMock()

    with (
        patch("custom_components.opendisplay._cached_runtime_data", return_value=None),
        patch(
            "custom_components.opendisplay.OpenDisplayCoordinator",
            return_value=coordinator,
        ),
        patch("custom_components.opendisplay.dr.async_get", return_value=MagicMock()),
        patch("custom_components.opendisplay.OpenDisplayDevice", _FakeDevice),
        patch(
            "custom_components.opendisplay.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
        patch("custom_components.opendisplay._cache_runtime_data"),
        patch(
            "custom_components.opendisplay.services._async_connect_and_run",
            new_callable=AsyncMock,
            side_effect=opendisplay_integration.BLEConnectionError("no free slot"),
        ),
        caplog.at_level(logging.INFO),
    ):
        assert await async_setup_entry(hass, entry) is True
        entry.runtime_data.deep_sleep_upload = queued_upload
        entry.runtime_data.deep_sleep_expiry_handle = expiry_handle

        coordinator.available = True
        coordinator.listener()
        await asyncio.gather(*hass._test_tasks)

    assert entry.runtime_data.deep_sleep_upload is queued_upload
    assert entry.runtime_data.deep_sleep_expiry_handle is expiry_handle
    assert entry.runtime_data.deep_sleep_flush_task is None
    expiry_handle.cancel.assert_not_called()
    assert "Queued image upload deferred again; keeping queue" in caplog.text


@pytest.mark.asyncio
async def test_queued_upload_is_cleared_when_wake_flush_succeeds() -> None:
    """Wake-up flush removes queued image after successful upload."""
    hass = _make_hass()
    entry = _make_entry()
    coordinator = _FakeCoordinator()
    initial_config = _make_device_config(300)

    class _FakeDevice:
        def __init__(self, **kwargs) -> None:
            self.is_flex = False
            self.config = initial_config

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def read_firmware_version(self):
            return {"major": 1, "minor": 0}

    queued_upload = DeepSleepQueuedUpload(
        action=AsyncMock(),
        jpeg_bytes=b"",
        queued_at=datetime.now() - timedelta(seconds=20),
        expiry=timedelta(seconds=330),
    )
    expiry_handle = MagicMock()

    with (
        patch("custom_components.opendisplay._cached_runtime_data", return_value=None),
        patch(
            "custom_components.opendisplay.OpenDisplayCoordinator",
            return_value=coordinator,
        ),
        patch("custom_components.opendisplay.dr.async_get", return_value=MagicMock()),
        patch("custom_components.opendisplay.OpenDisplayDevice", _FakeDevice),
        patch(
            "custom_components.opendisplay.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
        patch("custom_components.opendisplay._cache_runtime_data"),
        patch(
            "custom_components.opendisplay.services._async_connect_and_run",
            new_callable=AsyncMock,
        ) as mock_connect,
    ):
        assert await async_setup_entry(hass, entry) is True
        entry.runtime_data.deep_sleep_upload = queued_upload
        entry.runtime_data.deep_sleep_expiry_handle = expiry_handle

        coordinator.available = True
        coordinator.listener()
        await asyncio.gather(*hass._test_tasks)

    mock_connect.assert_awaited_once_with(
        hass,
        entry,
        queued_upload.action,
        wrap_connection_errors=False,
    )
    assert entry.runtime_data.deep_sleep_upload is None
    assert entry.runtime_data.deep_sleep_expiry_handle is None
    assert entry.runtime_data.deep_sleep_flush_task is None
    expiry_handle.cancel.assert_called_once()
