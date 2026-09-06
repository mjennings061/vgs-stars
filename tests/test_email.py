"""Checks for email rendering and the unsubscribe link."""

from datetime import date

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


def test_unsubscribe_link_only_when_url_given():
    """No batch ID means no link, in both HTML and plain text."""
    html_content, plain = email_service.render_email_template(_batch())
    assert "Unsubscribe" not in html_content and "Unsubscribe" not in plain

    url = "https://example.com/unsubscribe/abc123"
    html_content, plain = email_service.render_email_template(_batch(), url)
    assert f'href="{url}"' in html_content
    assert url in plain


def test_unsubscribe_url_uses_service_root():
    """The link hangs off the service root, not the Cloud Tasks path."""
    assert email_service.build_unsubscribe_url("abc123").endswith("/unsubscribe/abc123")
    assert "send_notification" not in email_service.build_unsubscribe_url("abc123")
