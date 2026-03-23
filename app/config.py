"""Application configuration loaded from environment variables.

All settings are read eagerly at import time via os.environ[] so that
missing variables surface immediately as KeyError on startup.
"""

import json
import logging
import os
import sys
from datetime import datetime as dt

# -- STARS API -----------------------------------------------------------------
STARS_URI = os.environ["STARS_URI"]
STARS_API_KEY = os.environ["STARS_API_KEY"]
STARS_ORG_UNIT_ID = os.environ["STARS_ORG_UNIT_ID"]

# -- Database (Firestore collection names) ------------------------------------
DATABASE_NOTIFICATIONS_COLLECTION = os.environ["DATABASE_NOTIFICATIONS_COLLECTION"]
DATABASE_NOTIFICATION_BATCHES_COLLECTION = os.environ[
    "DATABASE_NOTIFICATION_BATCHES_COLLECTION"
]
DATABASE_USERS_COLLECTION = os.environ["DATABASE_USERS_COLLECTION"]

# -- Email (SendGrid) ---------------------------------------------------------
SENDGRID_API_KEY = os.environ["SENDGRID_API_KEY"]
SENDGRID_FROM_EMAIL = os.environ["SENDGRID_FROM_EMAIL"]
SENDGRID_FROM_NAME = os.environ["SENDGRID_FROM_NAME"]

# -- Application ---------------------------------------------------------------
EXPIRY_WARNING_DAYS = int(os.environ["EXPIRY_WARNING_DAYS"])
LOG_LEVEL = os.environ["LOG_LEVEL"]
API_KEY_HEADER_NAME = os.environ["API_KEY_HEADER_NAME"]
CLOUD_TASKS_QUEUE_PATH = os.environ["CLOUD_TASKS_QUEUE_PATH"]

# -- Cloud Tasks ---------------------------------------------------------------
CLOUD_TASKS_TARGET_URL = os.environ["CLOUD_TASKS_TARGET_URL"]
CLOUD_TASKS_API_KEY = os.environ["CLOUD_TASKS_API_KEY"]
CLOUD_TASKS_DISPATCH_DELAY_SECONDS = int(
    os.environ["CLOUD_TASKS_DISPATCH_DELAY_SECONDS"]
)


def configure_logging() -> None:
    """Configure application logging.

    Uses Google Cloud Logging in Cloud Run for structured JSON logs.
    Uses standard Python logging locally for readable text output.
    """
    numeric_level = getattr(logging, LOG_LEVEL.upper(), None)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    is_cloud_run = os.getenv("K_SERVICE") is not None

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(numeric_level)

    if is_cloud_run:
        try:

            class CloudRunJsonFormatter(logging.Formatter):
                """JSON formatter for Cloud Run structured logging."""

                def format(self, record: logging.LogRecord) -> str:
                    log_entry = {
                        "severity": record.levelname,
                        "message": record.getMessage(),
                        "name": record.name,
                        "timestamp": dt.fromtimestamp(record.created).isoformat()
                        + "Z",
                    }
                    if record.exc_info:
                        log_entry["exc_info"] = self.formatException(record.exc_info)
                    return json.dumps(log_entry)

            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(CloudRunJsonFormatter())
            root_logger.addHandler(handler)
        except Exception:
            logging.basicConfig(
                level=numeric_level,
                format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            logging.exception("Failed to initialise Cloud Logging, using console")
    else:
        logging.basicConfig(
            level=numeric_level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    for noisy_logger in (
        "google.cloud.firestore_v1",
        "google.auth",
        "google.api_core",
        "grpc",
        "urllib3",
    ):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
