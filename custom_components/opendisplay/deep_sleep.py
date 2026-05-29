"""Deep-sleep upload queue data structures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from opendisplay import OpenDisplayDevice


@dataclass
class DeepSleepQueuedUpload:
    """A pending upload waiting for a sleeping device to wake up."""

    action: Callable[["OpenDisplayDevice"], Awaitable[None]]
    jpeg_bytes: bytes
    queued_at: datetime
    expiry: timedelta

    @property
    def is_expired(self) -> bool:
        """Return True if the upload has passed its expiry window."""
        return (datetime.now() - self.queued_at) > self.expiry


def supports_deep_sleep(device_config: object) -> bool:
    """Return whether the device configuration exposes deep sleep support."""
    power = getattr(device_config, "power", None)
    return hasattr(power, "deep_sleep_time_seconds")


def deep_sleep_seconds(device_config: object) -> int:
    """Return configured deep sleep seconds, clamped to non-negative values."""
    power = getattr(device_config, "power", None)
    raw_value = getattr(power, "deep_sleep_time_seconds", 0)
    try:
        return max(0, int(raw_value))
    except (TypeError, ValueError):
        return 0


def deep_sleep_enabled(device_config: object) -> bool:
    """Return whether deep sleep is currently enabled in device config."""
    return supports_deep_sleep(device_config) and deep_sleep_seconds(device_config) > 0
