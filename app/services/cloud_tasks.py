"""Cloud Tasks service for queuing notification send requests."""

import datetime
import json
import logging
from typing import Any, Dict

from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2

from app.config import (
    API_KEY_HEADER_NAME,
    CLOUD_TASKS_API_KEY,
    CLOUD_TASKS_QUEUE_PATH,
    CLOUD_TASKS_TARGET_URL,
)

logger = logging.getLogger(__name__)


def enqueue_send_notification(batch_id: str, delay_seconds: int) -> str:
    """Queue a Cloud Task to send a notification batch.

    Args:
        batch_id: Notification batch ID to send.
        delay_seconds: Delay from now before dispatching the task.

    Returns:
        Cloud Tasks task name.
    """
    client = tasks_v2.CloudTasksClient()

    headers = {
        "Content-Type": "application/json",
        API_KEY_HEADER_NAME: CLOUD_TASKS_API_KEY,
    }

    task: Dict[str, Any] = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": CLOUD_TASKS_TARGET_URL,
            "headers": headers,
            "body": json.dumps({"batch_id": batch_id}).encode(),
        }
    }

    # Apply a schedule time to stagger dispatches.
    if delay_seconds > 0:
        timestamp = timestamp_pb2.Timestamp()
        timestamp.FromDatetime(
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=delay_seconds)
        )
        task["schedule_time"] = timestamp

    response = client.create_task(request={"parent": CLOUD_TASKS_QUEUE_PATH, "task": task})
    logger.info("Queued task %s for batch %s", response.name, batch_id)
    return response.name
