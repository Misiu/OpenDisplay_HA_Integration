"""Constants for the OpenDisplay integration."""

DOMAIN = "opendisplay"
CONF_ENCRYPTION_KEY = "encryption_key"
SIGNAL_IMAGE_UPDATED = f"{DOMAIN}_image_updated"
CONF_DEEP_SLEEP_QUEUE_EXPIRY_HOURS = "deep_sleep_queue_expiry_hours"
DEFAULT_DEEP_SLEEP_QUEUE_EXPIRY_HOURS = 4
MIN_DEEP_SLEEP_QUEUE_EXPIRY_HOURS = 1
MAX_DEEP_SLEEP_QUEUE_EXPIRY_HOURS = 24
