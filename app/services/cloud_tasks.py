"""Cloud Tasks service for queuing notification send requests."""

import datetime
import json
import logging
from typing import Any, Dict

from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2

from app.config import get_settings

logger = logging.getLogger(__name__)


def enqueue_send_notification(batch_id: str, delay_seconds: int) -> str:
    """Queue a Cloud Task to send a notification batch.

    Args:
        batch_id: Notification batch ID to send.
        delay_seconds: Delay from now before dispatching the task.

    Returns:
        Cloud Tasks task name.
    """
    settings = get_settings()
    client = tasks_v2.CloudTasksClient()

    parent = settings.app.cloud_tasks_queue_path

    # Include the API key header so Cloud Tasks can call protected endpoints.
    header_name = settings.app.api_key_header_name
    headers = {
        "Content-Type": "application/json",
        header_name: settings.cloud_tasks.api_key,
    }

    task: Dict[str, Any] = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": settings.cloud_tasks.target_url,
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

    response = client.create_task(request={"parent": parent, "task": task})
    logger.info("Queued task %s for batch %s", response.name, batch_id)
    return response.name
