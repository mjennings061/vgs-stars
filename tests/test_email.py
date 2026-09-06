"""Checks for email rendering and the unsubscribe link."""

from datetime import date
from unittest.mock import patch

from app.models.notifications import AuthSummary, NotificationBatch, NotificationType
from app.services import email_service


def _batch(resource_name="Test User", auth_name="TEST01 Auth"):
    """Build a minimal NotificationBatch for rendering."""
    return NotificationBatch(
        userId="u1",
        userEmail="user@example.com",
        resourceId="R:1",
        resourceName=resource_name,
        notificationType=NotificationType.EXPIRING_SOON,
        subject="Auths expiring",
        auths=[
            AuthSummary(
                authId=1, mapId=2, authName=auth_name, expiryDate=date(2026, 1, 31)
            )
        ],
    )


def test_names_from_stars_are_escaped():
    """A name containing HTML must not break the markup."""
    html_content, _ = email_service.render_email_template(
        _batch(resource_name="A <b>Bold</b> Name", auth_name="X < Y")
    )
    assert "<b>Bold</b>" not in html_content
    assert "&lt;b&gt;Bold&lt;/b&gt;" in html_content
    assert "X &lt; Y" in html_content


def test_unsubscribe_link_only_when_batch_id_given():
    """A batch ID adds the link, the one-click headers and the idempotency key."""
    with patch("app.services.email_service.resend.Emails.send") as send:
        email_service.send_notification_email(_batch())
        params, options = send.call_args[0]
        assert "Unsubscribe" not in params["html"]
        assert "Unsubscribe" not in params["text"]
        assert "headers" not in params and options == {}

        email_service.send_notification_email(_batch(), batch_id="abc123")
        params, options = send.call_args[0]
        url = email_service.build_unsubscribe_url("abc123")
        assert f'href="{url}"' in params["html"] and url in params["text"]
        assert params["headers"]["List-Unsubscribe"] == f"<{url}>"
        assert options["idempotency_key"] == "auth-expiry/abc123"


def test_unsubscribe_url_uses_service_root():
    """The link hangs off the service root, not the Cloud Tasks path."""
    assert email_service.build_unsubscribe_url("abc123").endswith("/unsubscribe/abc123")
    assert "send_notification" not in email_service.build_unsubscribe_url("abc123")
