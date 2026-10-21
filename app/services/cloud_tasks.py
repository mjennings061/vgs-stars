"""Cloud Tasks service for queuing notification and sign-in code sends."""

import datetime
import json
import logging
from typing import Any, Dict
from urllib.parse import urljoin

from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2

from app.config import (
    API_KEY_HEADER_NAME,
    CLOUD_TASKS_API_KEY,
    CLOUD_TASKS_QUEUE_PATH,
    CLOUD_TASKS_SIGNIN_QUEUE_PATH,
    CLOUD_TASKS_TARGET_URL,
)

logger = logging.getLogger(__name__)


def _enqueue(queue: str, url: str, body: dict, delay_seconds: int = 0) -> str:
    """Queue a Cloud Task that POSTs a JSON body back to this service.

    Args:
        queue: Full resource path of the queue.
        url: Target URL the task calls.
        body: JSON body to send.
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
            "url": url,
            "headers": headers,
            "body": json.dumps(body).encode(),
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

    response = client.create_task(request={"parent": queue, "task": task})
    return response.name


def enqueue_send_notification(batch_id: str, delay_seconds: int) -> str:
    """Queue a Cloud Task to send a notification batch.

    Args:
        batch_id: Notification batch ID to send.
        delay_seconds: Delay from now before dispatching the task.

    Returns:
        Cloud Tasks task name.
    """
    name = _enqueue(
        CLOUD_TASKS_QUEUE_PATH,
        CLOUD_TASKS_TARGET_URL,
        {"batch_id": batch_id},
        delay_seconds,
    )
    logger.info("Queued task %s for batch %s", name, batch_id)
    return name


def enqueue_signin_code(email: str, name: str, code: str, key: str) -> str:
    """Queue a Cloud Task to email a sign-in code, so the request need not wait.

    Args:
        email: Where to send the code.
        name: Name of the person signing in.
        code: The six-digit code.
        key: Hashed nonce, which de-duplicates retries.

    Returns:
        Cloud Tasks task name.
    """
    # Cloud Tasks calls this same service, so its target URL gives us our own base.
    url = urljoin(CLOUD_TASKS_TARGET_URL, "/roster/tasks/send-code")
    body = {"email": email, "name": name, "code": code, "key": key}
    task_name = _enqueue(CLOUD_TASKS_SIGNIN_QUEUE_PATH, url, body)
    logger.info("Queued sign-in code task %s", task_name)
    return task_name
