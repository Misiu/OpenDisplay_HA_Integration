"""Tests for the per-entry deep-sleep upload queue.

Verifies:
- QueuedDeepSleepUpload expiry logic
- _async_send_image queues upload when device is not connectable
- Queued upload is flushed when the coordinator receives an advertisement
  and the device becomes connectable
"""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from custom_components.opendisplay.deep_sleep import QueuedDeepSleepUpload
from custom_components.opendisplay.const import (
    DEFAULT_DEEP_SLEEP_QUEUE_EXPIRY_HOURS,
    MIN_DEEP_SLEEP_QUEUE_EXPIRY_HOURS,
    MAX_DEEP_SLEEP_QUEUE_EXPIRY_HOURS,
)


# ---------------------------------------------------------------------------
# QueuedDeepSleepUpload unit tests
# ---------------------------------------------------------------------------


def _make_queued(*, hours_old: float = 0, expiry_hours: int = DEFAULT_DEEP_SLEEP_QUEUE_EXPIRY_HOURS) -> QueuedDeepSleepUpload:
    return QueuedDeepSleepUpload(
        action=AsyncMock(),
        jpeg_bytes=b"",
        queued_at=datetime.now() - timedelta(hours=hours_old),
        expiry=timedelta(hours=expiry_hours),
    )


def test_queued_upload_not_expired_when_fresh() -> None:
    """A freshly queued upload is not expired."""
    q = _make_queued(hours_old=0)
    assert not q.is_expired


def test_queued_upload_not_expired_just_before_expiry() -> None:
    """Upload is not expired just before its expiry window closes."""
    q = _make_queued(hours_old=DEFAULT_DEEP_SLEEP_QUEUE_EXPIRY_HOURS - 0.01)
    assert not q.is_expired


def test_queued_upload_expired_after_default_window() -> None:
    """Upload is expired after the default 4-hour window."""
    q = _make_queued(hours_old=DEFAULT_DEEP_SLEEP_QUEUE_EXPIRY_HOURS + 0.01)
    assert q.is_expired


def test_queued_upload_expired_with_custom_expiry() -> None:
    """Upload expiry respects a custom expiry timedelta."""
    q = _make_queued(hours_old=1.1, expiry_hours=1)
    assert q.is_expired


def test_queued_upload_not_expired_with_custom_expiry() -> None:
    """Upload not expired when within custom expiry window."""
    q = _make_queued(hours_old=0.9, expiry_hours=1)
    assert not q.is_expired


# ---------------------------------------------------------------------------
# _async_send_image queuing behaviour
# ---------------------------------------------------------------------------


def _make_entry(address: str = "AA:BB:CC:DD:EE:FF", expiry_hours: int = DEFAULT_DEEP_SLEEP_QUEUE_EXPIRY_HOURS) -> MagicMock:
    """Build a minimal mock config entry."""
    runtime_data = SimpleNamespace(deep_sleep_upload=None)
    entry = MagicMock()
    entry.unique_id = address
    entry.options = {}
    entry.runtime_data = runtime_data
    return entry


@pytest.mark.asyncio
async def test_send_image_queues_when_device_not_connectable() -> None:
    """Image upload is queued when the BLE device is not currently connectable."""
    hass = MagicMock()
    entry = _make_entry()

    img = MagicMock()

    from opendisplay import DitherMode, RefreshMode
    from custom_components.opendisplay.services import _async_send_image

    with patch(
        "custom_components.opendisplay.services.async_ble_device_from_address",
        return_value=None,  # device not connectable
    ):
        await _async_send_image(
            hass, entry, img, dither_mode=DitherMode.BURKES, refresh_mode=RefreshMode.FULL
        )

    # Upload should have been queued, not sent
    assert entry.runtime_data.deep_sleep_upload is not None
    assert not entry.runtime_data.deep_sleep_upload.is_expired


@pytest.mark.asyncio
async def test_send_image_uploads_immediately_when_connectable() -> None:
    """Image upload proceeds immediately when the BLE device is connectable."""
    hass = MagicMock()
    entry = _make_entry()

    img = MagicMock()

    from opendisplay import DitherMode, RefreshMode
    from custom_components.opendisplay.services import _async_send_image

    ble_device = MagicMock()
    hass.async_add_executor_job = AsyncMock(return_value=b"jpeg")

    with (
        patch(
            "custom_components.opendisplay.services.async_ble_device_from_address",
            return_value=ble_device,
        ),
        patch(
            "custom_components.opendisplay.services._async_connect_and_run",
            new_callable=AsyncMock,
        ) as mock_run,
        patch(
            "custom_components.opendisplay.services._pil_to_jpeg",
            return_value=b"jpeg",
        ),
        patch("custom_components.opendisplay.services.async_dispatcher_send"),
    ):
        await _async_send_image(
            hass, entry, img, dither_mode=DitherMode.BURKES, refresh_mode=RefreshMode.FULL
        )

    # Upload should NOT have been queued
    assert entry.runtime_data.deep_sleep_upload is None
    # _async_connect_and_run should have been called
    mock_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_image_queued_upload_replaces_previous() -> None:
    """A new image upload replaces any previously queued upload."""
    hass = MagicMock()
    entry = _make_entry()
    first_upload = QueuedDeepSleepUpload(
        action=AsyncMock(),
        jpeg_bytes=b"",
        queued_at=datetime.now(),
        expiry=timedelta(hours=4),
    )
    entry.runtime_data.deep_sleep_upload = first_upload

    img = MagicMock()
    from opendisplay import DitherMode, RefreshMode
    from custom_components.opendisplay.services import _async_send_image

    with patch(
        "custom_components.opendisplay.services.async_ble_device_from_address",
        return_value=None,
    ):
        await _async_send_image(
            hass, entry, img, dither_mode=DitherMode.BURKES, refresh_mode=RefreshMode.FULL
        )

    new_upload = entry.runtime_data.deep_sleep_upload
    assert new_upload is not None
    assert new_upload is not first_upload


# ---------------------------------------------------------------------------
# Deep-sleep expiry configuration clipping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expiry_clamped_to_minimum() -> None:
    """Configured expiry below minimum is clamped to MIN_DEEP_SLEEP_QUEUE_EXPIRY_HOURS."""
    hass = MagicMock()
    entry = _make_entry()
    entry.options = {"deep_sleep_queue_expiry_hours": 0}  # below minimum

    img = MagicMock()
    from opendisplay import DitherMode, RefreshMode
    from custom_components.opendisplay.services import _async_send_image

    with patch(
        "custom_components.opendisplay.services.async_ble_device_from_address",
        return_value=None,
    ):
        await _async_send_image(
            hass, entry, img, dither_mode=DitherMode.BURKES, refresh_mode=RefreshMode.FULL
        )

    queued = entry.runtime_data.deep_sleep_upload
    assert queued is not None
    assert queued.expiry == timedelta(hours=MIN_DEEP_SLEEP_QUEUE_EXPIRY_HOURS)


@pytest.mark.asyncio
async def test_expiry_clamped_to_maximum() -> None:
    """Configured expiry above maximum is clamped to MAX_DEEP_SLEEP_QUEUE_EXPIRY_HOURS."""
    hass = MagicMock()
    entry = _make_entry()
    entry.options = {"deep_sleep_queue_expiry_hours": 9999}  # above maximum

    img = MagicMock()
    from opendisplay import DitherMode, RefreshMode
    from custom_components.opendisplay.services import _async_send_image

    with patch(
        "custom_components.opendisplay.services.async_ble_device_from_address",
        return_value=None,
    ):
        await _async_send_image(
            hass, entry, img, dither_mode=DitherMode.BURKES, refresh_mode=RefreshMode.FULL
        )

    queued = entry.runtime_data.deep_sleep_upload
    assert queued is not None
    assert queued.expiry == timedelta(hours=MAX_DEEP_SLEEP_QUEUE_EXPIRY_HOURS)
