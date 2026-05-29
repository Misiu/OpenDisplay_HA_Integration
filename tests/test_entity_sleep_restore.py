"""Tests for sleeping-device entity behavior (assumed state + restore)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from custom_components.opendisplay.sensor import (
    OpenDisplaySensorEntity,
    OpenDisplaySensorEntityDescription,
)


def _make_coordinator(*, available: bool, deep_sleep_seconds: int, data=None):
    """Create a minimal coordinator-like object for entity unit tests."""
    device_config = SimpleNamespace(
        power=SimpleNamespace(deep_sleep_time_seconds=deep_sleep_seconds)
    )
    runtime_data = SimpleNamespace(device_config=device_config)
    config_entry = SimpleNamespace(runtime_data=runtime_data)
    return SimpleNamespace(
        available=available,
        data=data,
        address="AA:BB:CC:DD:EE:FF",
        config_entry=config_entry,
    )


def _make_description() -> OpenDisplaySensorEntityDescription:
    """Return a minimal sensor description for tests."""
    return OpenDisplaySensorEntityDescription(
        key="temperature",
        value_fn=lambda upd: upd.advertisement.temperature_c,
    )


def _build_entity(*, available: bool, deep_sleep_seconds: int, data=None) -> OpenDisplaySensorEntity:
    """Build sensor entity with patched coordinator base initializer."""
    coordinator = _make_coordinator(
        available=available,
        deep_sleep_seconds=deep_sleep_seconds,
        data=data,
    )
    with patch(
        "custom_components.opendisplay.entity.PassiveBluetoothCoordinatorEntity.__init__",
        lambda self, coordinator: setattr(self, "coordinator", coordinator),
    ):
        return OpenDisplaySensorEntity(coordinator, _make_description())


def test_sleeping_device_is_available_with_assumed_state() -> None:
    """Sleeping device should stay available with assumed_state=True."""
    entity = _build_entity(available=False, deep_sleep_seconds=300)

    assert entity.available is True
    assert entity.assumed_state is True


def test_non_sleeping_offline_device_is_unavailable() -> None:
    """Offline device without deep sleep should be unavailable."""
    entity = _build_entity(available=False, deep_sleep_seconds=0)

    assert entity.available is False
    assert entity.assumed_state is False


def test_online_device_not_assumed() -> None:
    """Online devices should never report assumed state."""
    entity = _build_entity(available=True, deep_sleep_seconds=300)

    assert entity.available is True
    assert entity.assumed_state is False


def test_sensor_native_value_restores_last_state_when_sleeping() -> None:
    """When coordinator has no fresh data, sensor falls back to restored value."""
    entity = _build_entity(available=False, deep_sleep_seconds=300)
    entity._restored_data = SimpleNamespace(native_value=22.5)

    assert entity.native_value == 22.5


def test_sensor_native_value_prefers_live_data_over_restored() -> None:
    """Fresh coordinator data must override restored value."""
    data = SimpleNamespace(advertisement=SimpleNamespace(temperature_c=19.8))
    entity = _build_entity(available=True, deep_sleep_seconds=300, data=data)
    entity._restored_data = SimpleNamespace(native_value=22.5)

    assert entity.native_value == 19.8
