"""Application configuration using Pydantic Settings.

This module provides type-safe configuration management with automatic
environment variable loading and validation.
"""

import logging

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


class MongoConfig(BaseSettings):
    """MongoDB configuration settings."""

    model_config = SettingsConfigDict(
        env_prefix="MONGO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    uri: str = Field(..., description="MongoDB connection URI")
    db_name: str = Field(default="stars", description="Database name")

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


class Settings(BaseSettings):
    """Main application settings combining all configuration sections."""

    # Load from .env
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    stars: StarsConfig = Field(default_factory=StarsConfig)
    mongo: MongoConfig = Field(default_factory=MongoConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    app: AppConfig = Field(default_factory=AppConfig)

    def configure_logging(self) -> None:
        """Configure application logging based on settings."""
        numeric_level = getattr(logging, self.app.log_level.upper(), None)
        if not isinstance(numeric_level, int):
            numeric_level = logging.INFO

        logging.basicConfig(
            level=numeric_level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # Quiet noisy third-party loggers
        for noisy_logger in (
            "pymongo",
            "pymongo.ocsp_support",
            "pymongo.pool",
            "pymongo.topology",
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
