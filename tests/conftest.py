"""Shared pytest fixtures and configuration.

Stubs out heavy Home Assistant selector / media components that are not
needed for unit tests and may cause import errors due to version
mismatches in the test environment.
"""

from __future__ import annotations

import sys
import types
from enum import IntEnum
from unittest.mock import MagicMock


def _stub_opendisplay() -> None:
    """Install a minimal opendisplay stub so unit tests run without the real library."""
    if "opendisplay" in sys.modules:
        return

    # --- exception types ---
    class OpenDisplayError(Exception):
        pass

    class BLEConnectionError(OpenDisplayError):
        pass

    class BLETimeoutError(OpenDisplayError):
        pass

    class AuthenticationFailedError(OpenDisplayError):
        pass

    class AuthenticationRequiredError(OpenDisplayError):
        pass

    # --- enums ---
    class DitherMode(IntEnum):
        BURKES = 0
        FLOYD_STEINBERG = 1
        NONE = 2
        ORDERED = 3

    class RefreshMode(IntEnum):
        FULL = 0
        PARTIAL = 1

    class FitMode(IntEnum):
        CONTAIN = 0
        COVER = 1
        FILL = 2

    class Rotation(IntEnum):
        ROTATE_0 = 0
        ROTATE_90 = 90
        ROTATE_180 = 180
        ROTATE_270 = 270

    # --- device / config types ---
    class GlobalConfig:
        pass

    class OpenDisplayDevice:
        def __init__(self, **kwargs):
            self.is_flex = False
            self.config = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def read_firmware_version(self):
            return {"major": 1, "minor": 0}

        async def upload_image(self, *args, **kwargs):
            pass

    class LedFlashConfig:
        pass

    class LedFlashStep:
        pass

    class BuzzerActivateConfig:
        pass

    class AdvertisementTracker:
        def update(self, *args, **kwargs):
            return []

    class AdvertisementData:
        pass

    class ButtonChangeEvent:
        pass

    class TouchChangeEvent:
        pass

    class TouchTracker:
        def update(self, *args, **kwargs):
            return []

    MANUFACTURER_ID = 0x0B9B

    def parse_advertisement(data):
        return AdvertisementData()

    def voltage_to_percent(v):
        return 50

    # Build the opendisplay package and sub-modules in sys.modules
    pkg = types.ModuleType("opendisplay")
    pkg.OpenDisplayError = OpenDisplayError
    pkg.BLEConnectionError = BLEConnectionError
    pkg.BLETimeoutError = BLETimeoutError
    pkg.AuthenticationFailedError = AuthenticationFailedError
    pkg.AuthenticationRequiredError = AuthenticationRequiredError
    pkg.DitherMode = DitherMode
    pkg.RefreshMode = RefreshMode
    pkg.FitMode = FitMode
    pkg.Rotation = Rotation
    pkg.GlobalConfig = GlobalConfig
    pkg.OpenDisplayDevice = OpenDisplayDevice
    pkg.LedFlashConfig = LedFlashConfig
    pkg.LedFlashStep = LedFlashStep
    pkg.BuzzerActivateConfig = BuzzerActivateConfig
    pkg.AdvertisementTracker = AdvertisementTracker
    pkg.MANUFACTURER_ID = MANUFACTURER_ID
    pkg.parse_advertisement = parse_advertisement
    pkg.voltage_to_percent = voltage_to_percent

    # opendisplay.models
    models_mod = types.ModuleType("opendisplay.models")
    models_mod.FirmwareVersion = dict  # TypedDict equivalent
    pkg.models = models_mod

    # opendisplay.models.advertisement
    adv_mod = types.ModuleType("opendisplay.models.advertisement")
    adv_mod.AdvertisementData = AdvertisementData
    adv_mod.ButtonChangeEvent = ButtonChangeEvent
    adv_mod.TouchChangeEvent = TouchChangeEvent
    adv_mod.TouchTracker = TouchTracker

    # opendisplay.models.enums
    enums_mod = types.ModuleType("opendisplay.models.enums")

    class CapacityEstimator(IntEnum):
        UNKNOWN = 0

    class PowerMode(IntEnum):
        NORMAL = 0
        DEEP_SLEEP = 1

    enums_mod.CapacityEstimator = CapacityEstimator
    enums_mod.PowerMode = PowerMode

    # opendisplay.models.firmware
    firmware_mod = types.ModuleType("opendisplay.models.firmware")
    firmware_mod.firmware_release_repo = MagicMock()

    # Register all sub-modules
    sys.modules["opendisplay"] = pkg
    sys.modules["opendisplay.models"] = models_mod
    sys.modules["opendisplay.models.advertisement"] = adv_mod
    sys.modules["opendisplay.models.enums"] = enums_mod
    sys.modules["opendisplay.models.firmware"] = firmware_mod

    # Also stub epaper_dithering and odl_renderer used in services.py
    if "epaper_dithering" not in sys.modules:
        epaper_mod = types.ModuleType("epaper_dithering")

        class ColorScheme(IntEnum):
            BW = 0

        epaper_mod.ColorScheme = ColorScheme
        sys.modules["epaper_dithering"] = epaper_mod

    if "odl_renderer" not in sys.modules:
        odl_mod = types.ModuleType("odl_renderer")
        odl_mod.generate_image = MagicMock()
        sys.modules["odl_renderer"] = odl_mod


_stub_opendisplay()



def _stub_ha_selector() -> None:
    """Stub out homeassistant.helpers.selector to avoid voluptuous schema errors."""
    selector_mod = sys.modules.get("homeassistant.helpers.selector")
    if selector_mod is None:
        return

    class _NumberSelectorMode:
        BOX = "box"

    class _NumberSelectorConfig:
        def __init__(self, **kwargs):
            pass

    class _NumberSelector:
        def __init__(self, config=None):
            pass

        def __call__(self, value):
            return value

    class _MediaSelectorConfig:
        def __init__(self, **kwargs):
            pass

    class _MediaSelector:
        def __init__(self, config=None):
            pass

        def __call__(self, value):
            return value

    selector_mod.NumberSelectorMode = _NumberSelectorMode
    selector_mod.NumberSelectorConfig = _NumberSelectorConfig
    selector_mod.NumberSelector = _NumberSelector
    selector_mod.MediaSelectorConfig = _MediaSelectorConfig
    selector_mod.MediaSelector = _MediaSelector


_stub_ha_selector()
