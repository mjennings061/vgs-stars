"""Pytest configuration and fixtures for testing."""

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session", autouse=True)
def setup_test_env():
    """Set up test environment variables before any tests run."""
    # STARS API Configuration
    os.environ["STARS_URI"] = "https://test.stars.api/api"
    os.environ["STARS_API_KEY"] = "test_api_key"
    os.environ["STARS_ORG_UNIT_ID"] = "test_org_unit_id"

    # Email Configuration
    os.environ["SENDGRID_API_KEY"] = "test_sendgrid_key"
    os.environ["SENDGRID_FROM_EMAIL"] = "test@example.com"
    os.environ["SENDGRID_FROM_NAME"] = "Test Sender"

    # Application Configuration
    os.environ["EXPIRY_WARNING_DAYS"] = "30"
    os.environ["LOG_LEVEL"] = "INFO"

    yield

    # Cleanup is optional since these are just test environment variables


@pytest.fixture
def test_client():
    """Create a test client for the FastAPI application."""

    client = TestClient(app)

    return client
