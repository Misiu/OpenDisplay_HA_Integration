from datetime import datetime, timedelta, timezone

import pytest

from custom_components.opendisplay.coordinator import Hub
from custom_components.opendisplay.upload import DeepSleepUploadQueue


@pytest.mark.asyncio
async def test_deep_sleep_queue_replaces_existing_image() -> None:
    """Queue keeps only the latest image for a sleeping tag."""
    queue = DeepSleepUploadQueue()

    async def upload_a():
        return None

    async def upload_b():
        return None

    await queue.queue_upload("aa:bb", upload_a, "first")
    await queue.queue_upload("AA:BB", upload_b, "second")

    queued = await queue.pop_upload("aa:bb")
    assert queued is not None
    assert queued.upload_func is upload_b
    assert queued.args == ("second",)


@pytest.mark.asyncio
async def test_deep_sleep_queue_expires_after_30_minutes() -> None:
    """Queued image is dropped after expiration."""
    queue = DeepSleepUploadQueue()

    async def upload():
        return None

    await queue.queue_upload("aa:bb", upload, "payload")
    queue._pending_by_tag["AA:BB"].queued_at = datetime.now() - timedelta(minutes=31)

    queued = await queue.pop_upload("aa:bb")
    assert queued is None


def test_hub_should_queue_image_upload_for_sleeping_deep_sleep_tag() -> None:
    """Deep-sleeping tag should use pending upload queue."""
    now = datetime.now(timezone.utc).timestamp()
    hub = Hub.__new__(Hub)
    hub._data = {
        "AA:BB": {
            "modecfgjson": {"deepsleep": 1, "maxsleep": 60},
            "next_checkin": now + 60,
        }
    }

    assert hub.is_tag_in_deep_sleep("aa:bb")
    assert hub.is_tag_currently_sleeping("aa:bb")
    assert hub.should_queue_image_upload("aa:bb")


def test_hub_should_not_queue_when_tag_not_sleeping() -> None:
    """Awake tag should upload immediately."""
    now = datetime.now(timezone.utc).timestamp()
    hub = Hub.__new__(Hub)
    hub._data = {
        "AA:BB": {
            "modecfgjson": {"deepsleep": 1, "maxsleep": 60},
            "next_checkin": now - 5,
        }
    }

    assert hub.is_tag_in_deep_sleep("aa:bb")
    assert not hub.is_tag_currently_sleeping("aa:bb")
    assert not hub.should_queue_image_upload("aa:bb")
