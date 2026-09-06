"""Email service for sending authorisation expiry notifications via Resend.

Provides functions to send notification emails using the Resend API
with HTML and plain text templates.
"""

import html
import logging
from urllib.parse import urljoin

import resend

from app.config import CLOUD_TASKS_TARGET_URL, EMAIL_FROM, RESEND_API_KEY
from app.models.notifications import NotificationBatch

logger = logging.getLogger(__name__)

resend.api_key = RESEND_API_KEY


class EmailServiceError(Exception):
    """Exception raised for email service errors."""


def build_unsubscribe_url(batch_id: str) -> str:
    """Build the unsubscribe URL for a notification batch.

    Args:
        batch_id: Notification batch ID, used as the unguessable token.

    Returns:
        Absolute URL to the unsubscribe endpoint.
    """
    # Cloud Tasks calls this same service, so its target URL gives us our own base.
    return urljoin(CLOUD_TASKS_TARGET_URL, f"/unsubscribe/{batch_id}")


def render_email_template(
    batch: NotificationBatch, unsubscribe_url: str | None = None
) -> tuple[str, str]:
    """Generate HTML and plain text email content from notification batch.

    Args:
        batch: NotificationBatch with authorisations to include.
        unsubscribe_url: Link to include in the footer, if available.

    Returns:
        Tuple of (html_content, plain_text_content).
    """
    # Sort auths by expiry date (earliest first)
    sorted_auths = sorted(batch.auths, key=lambda a: a.expiry_date)
    earliest_expiry = sorted_auths[0].expiry_date if sorted_auths else None

    # Plain text version
    plain_lines = [
        f"Dear {batch.resource_name},",
        "",
        (
            f"This is a notification that you have {len(batch.auths)} "
            "STARS authorisation(s) expiring soon."
        ),
        "",
    ]

    if earliest_expiry:
        plain_lines.append(f"Earliest expiry: {earliest_expiry.strftime('%d %B %Y')}")
        plain_lines.append("")

    plain_lines.append("Authorisations expiring:")
    plain_lines.append("-" * 60)

    for auth in sorted_auths:
        expiry_str = auth.expiry_date.strftime("%d %B %Y")
        plain_lines.append(f"- {auth.auth_name}")
        plain_lines.append(f"  Expiry: {expiry_str}")
        plain_lines.append("")

    plain_lines.extend(
        [
            "-" * 60,
            "",
            "Please renew your authorisations via your QESO.",
            "",
            "This is an automated notification from the 661 VGS STARS system.",
        ]
    )

    if unsubscribe_url:
        plain_lines.append(f"Unsubscribe: {unsubscribe_url}")

    plain_lines.extend(["", "https://github.com/mjennings061/vgs-stars"])

    plain_text = "\n".join(plain_lines)

    # HTML version. Names come from STARS, so escape them.
    html_lines = [
        "<html>",
        "<head>",
        "<style>",
        "body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; }",
        "h2 { color: #2c3e50; }",
        "table { border-collapse: collapse; width: 100%; margin: 20px 0; }",
        "th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }",
        "th { background-color: #3498db; color: white; }",
        "tr:nth-child(even) { background-color: #f2f2f2; }",
        ".warning { color: #e74c3c; font-weight: bold; }",
        ".footer { margin-top: 30px; font-size: 0.9em; color: #7f8c8d; }",
        "</style>",
        "</head>",
        "<body>",
        f"<h2>Dear {html.escape(batch.resource_name)},</h2>",
        (
            f"<p>This is a notification that you have <strong>{len(batch.auths)}"
            "</strong> STARS authorisation(s) expiring soon.</p>"
        ),
    ]

    if earliest_expiry:
        html_lines.append(
            (
                f'<p class="warning">Earliest expiry: '
                f'{earliest_expiry.strftime("%d %B %Y")}</p>'
            )
        )

    html_lines.extend(
        [
            "<h3>Authorisations Expiring:</h3>",
            "<table>",
            "<tr>",
            "<th>Authorisation</th>",
            "<th>Expiry Date</th>",
            "</tr>",
        ]
    )

    for auth in sorted_auths:
        expiry_str = auth.expiry_date.strftime("%d %B %Y")
        html_lines.append("<tr>")
        html_lines.append(f"<td>{html.escape(auth.auth_name)}</td>")
        html_lines.append(f"<td>{expiry_str}</td>")
        html_lines.append("</tr>")

    footer_parts = [
        '<p class="footer">This is an automated notification from the ',
        '<a href="https://github.com/mjennings061/vgs-stars">',
        "661 VGS STARS system</a>.",
    ]
    if unsubscribe_url:
        footer_parts.append(
            f'<br><a href="{html.escape(unsubscribe_url)}">Unsubscribe</a>'
        )
    footer_parts.append("</p>")

    html_lines.extend(
        [
            "</table>",
            "<p>Please renew your authorisations via your QESO.</p>",
            "".join(footer_parts),
            "</body>",
            "</html>",
        ]
    )

    html_content = "\n".join(html_lines)

    return html_content, plain_text


def send_notification_email(
    batch: NotificationBatch, batch_id: str | None = None
) -> bool:
    """Send batched notification email via Resend.

    Args:
        batch: NotificationBatch with user and authorisation details.
        batch_id: Persisted batch ID. Enables the unsubscribe link and
            de-duplicates Cloud Tasks retries.

    Returns:
        True if email sent successfully.

    Raises:
        EmailServiceError: If email sending fails.
    """
    logger.info(
        "Sending notification email to %s for %d auths",
        batch.user_email,
        len(batch.auths),
    )

    # Rendered outside the try so a template bug is not reported as a send failure.
    unsubscribe_url = build_unsubscribe_url(batch_id) if batch_id else None
    html_content, plain_text = render_email_template(batch, unsubscribe_url)

    params: resend.Emails.SendParams = {
        "from": EMAIL_FROM,
        "to": [batch.user_email],
        "subject": batch.subject,
        "html": html_content,
        "text": plain_text,
    }

    if unsubscribe_url:
        # RFC 8058 one-click. POST is the endpoint that actually unsubscribes.
        params["headers"] = {
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }

    # Idempotency key stops Cloud Tasks retries sending the email twice.
    options: resend.Emails.SendOptions = (
        {"idempotency_key": f"auth-expiry/{batch_id}"} if batch_id else {}
    )

    try:
        email = resend.Emails.send(params, options)
    except Exception as e:
        logger.error("Error sending email to %s: %s", batch.user_email, e)
        raise EmailServiceError(f"Failed to send email: {e}") from e

    logger.info("Email sent to %s (id: %s)", batch.user_email, email.get("id"))
    return True
