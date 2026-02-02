"""Application configuration using Pydantic Settings.

This module provides type-safe configuration management with automatic
environment variable loading and validation.
"""

import logging
import os

from google.cloud import logging as cloud_logging
from google.cloud.logging.handlers import CloudLoggingHandler
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class StarsConfig(BaseSettings):
    """STARS API configuration settings."""

    model_config = SettingsConfigDict(
        env_prefix="STARS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    uri: str = Field(..., description="STARS API base URI")
    api_key: str = Field(..., description="STARS API authentication key")
    org_unit_id: str = Field(..., description="Default organisation unit ID")


class DatabaseConfig(BaseSettings):
    """Database configuration settings for Firestore."""

    model_config = SettingsConfigDict(
        env_prefix="DATABASE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Collection names
    notifications_collection: str = Field(
        default="auths_notification",
        description="Collection for individual notifications",
    )
    notification_batches_collection: str = Field(
        default="auth_notification_batches",
        description="Collection for batched notifications",
    )
    users_collection: str = Field(
        default="users",
        description="Collection for API users and hashed API keys",
    )


class EmailConfig(BaseSettings):
    """Email service configuration settings."""

    model_config = SettingsConfigDict(
        env_prefix="SENDGRID_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str = Field(..., description="SendGrid API key")
    from_email: str = Field(..., description="Sender email address")
    from_name: str = Field(default="STARS Expiry", description="Sender name")


class AppConfig(BaseSettings):
    """General application configuration settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    expiry_warning_days: int = Field(
        default=30,
        description="Number of days before expiry to send warnings",
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    api_key_header_name: str = Field(
        default="X-API-Key",
        description="HTTP header name used to pass the API key",
    )
    cloud_tasks_queue_path: str = Field(
        ...,
        description="Cloud Tasks queue path (projects/.../locations/.../queues/...)",
    )


class CloudTasksConfig(BaseSettings):
    """Google Cloud Tasks configuration settings."""

    model_config = SettingsConfigDict(
        env_prefix="CLOUD_TASKS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    target_url: str = Field(
        ..., description="Full URL for the send-notification endpoint"
    )
    api_key: str = Field(..., description="API key to call protected endpoints")
    dispatch_delay_seconds: int = Field(
        default=20,
        description="Delay between queued tasks in seconds",
    )


class Settings(BaseSettings):
    """Main application settings combining all configuration sections."""

    # Load from .env
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    stars: StarsConfig = Field(default_factory=StarsConfig)  # type: ignore
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)  # type: ignore
    email: EmailConfig = Field(default_factory=EmailConfig)  # type: ignore
    app: AppConfig = Field(default_factory=AppConfig)  # type: ignore
    cloud_tasks: CloudTasksConfig = Field(
        default_factory=CloudTasksConfig  # type: ignore
    )

    def configure_logging(self) -> None:
        """Configure application logging based on settings.

        Uses Google Cloud Logging in Cloud Run for structured JSON logs.
        Uses standard Python logging locally for readable text output.
        """
        # Determine log level
        numeric_level = getattr(logging, self.app.log_level.upper(), None)
        if not isinstance(numeric_level, int):
            numeric_level = logging.INFO

        # Detect Cloud Run environment
        is_cloud_run = os.getenv("K_SERVICE") is not None

        # Get root logger and clear existing handlers
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.setLevel(numeric_level)

        if is_cloud_run:
            # Cloud Run: Use Google Cloud Logging for structured JSON logs
            try:
                client = cloud_logging.Client()
                handler = CloudLoggingHandler(client)
                handler.setLevel(numeric_level)
                root_logger.addHandler(handler)
            except Exception:
                # Fallback to console logging if Cloud Logging fails
                logging.basicConfig(
                    level=numeric_level,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
                logging.exception("Failed to initialise Cloud Logging, using console")
        else:
            # Local: Use standard text logging for readability
            logging.basicConfig(
                level=numeric_level,
                format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

        # Quiet noisy third-party loggers in both environments
        for noisy_logger in (
            "google.cloud.firestore_v1",
            "google.auth",
            "google.api_core",
        ):
            logging.getLogger(noisy_logger).setLevel(logging.WARNING)


# Global settings instance
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get or create the global settings instance.

    Returns:
        Settings instance with loaded configuration.
    """
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.configure_logging()
    return _settings
