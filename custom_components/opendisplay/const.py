"""Constants for the OpenDisplay integration."""

DOMAIN = "opendisplay"
CONF_ENCRYPTION_KEY = "encryption_key"
CONF_CACHED_DEVICE_CONFIG = "cached_device_config"
CONF_CACHED_FIRMWARE = "cached_firmware"
CONF_CACHED_IS_FLEX = "cached_is_flex"
SIGNAL_IMAGE_UPDATED = f"{DOMAIN}_image_updated"
# Fallback expiry (seconds) used when the device reports deep_sleep_time_seconds = 0
DEFAULT_DEEP_SLEEP_EXPIRY_SECONDS = 14400  # 4 hours
