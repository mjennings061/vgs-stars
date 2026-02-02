"""Email service for sending authorisation expiry notifications via SendGrid.

Provides functions to send notification emails using the SendGrid API
with HTML and plain text templates.
"""

import logging

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Asm, Content, Email, Mail, To

from app.config import get_settings
from app.models.notifications import NotificationBatch

logger = logging.getLogger(__name__)

UNSUBSCRIBE_GROUP_ID = 27661
# Preference center raw URL (two-step unsubscribe to avoid bot clicks)
ASM_PREFERENCES_URL_TAG = "<%asm_preferences_raw_url%>"


class EmailServiceError(Exception):
    """Exception raised for email service errors."""


def render_email_template(batch: NotificationBatch) -> tuple[str, str]:
    """Generate HTML and plain text email content from notification batch.

    Args:
        batch: NotificationBatch with authorisations to include.

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
            f"Manage preferences: {ASM_PREFERENCES_URL_TAG}",
            "",
            "https://github.com/mjennings061/vgs-stars",
        ]
    )

    plain_text = "\n".join(plain_lines)

    # HTML version
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
        f"<h2>Dear {batch.resource_name},</h2>",
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
        html_lines.append(f"<td>{auth.auth_name}</td>")
        html_lines.append(f"<td>{expiry_str}</td>")
        html_lines.append("</tr>")

    footer_text = (
        '<p class="footer">This is an automated notification from the '
        '<a href="https://github.com/mjennings061/vgs-stars">'
        "661 VGS STARS system</a>.<br>"
        f'<a href="{ASM_PREFERENCES_URL_TAG}">Manage preferences</a>'
        "</p>"
    )

    html_lines.extend(
        [
            "</table>",
            "<p>Please renew your authorisations via your QESO.</p>",
            footer_text,
            "</body>",
            "</html>",
        ]
    )

    html_content = "\n".join(html_lines)

    return html_content, plain_text


def send_notification_email(batch: NotificationBatch) -> bool:
    """Send batched notification email via SendGrid.

    Args:
        batch: NotificationBatch with user and authorisation details.

    Returns:
        True if email sent successfully, False otherwise.

    Raises:
        EmailServiceError: If email sending fails.
    """
    settings = get_settings()

    logger.info(
        "Sending notification email to %s for %d auths",
        batch.user_email,
        len(batch.auths),
    )

    try:
        # Render email templates
        html_content, plain_text = render_email_template(batch)

        # Create SendGrid mail object
        from_email = Email(settings.email.from_email, settings.email.from_name)
        to_email = To(batch.user_email)
        subject = batch.subject

        # Create message with both HTML and plain text
        mail = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject=subject,
            plain_text_content=Content("text/plain", plain_text),
            html_content=Content("text/html", html_content),
        )

        # Use preferences page (two-step) to avoid auto-unsub from link scanners
        mail.asm = Asm(
            group_id=UNSUBSCRIBE_GROUP_ID, groups_to_display=[UNSUBSCRIBE_GROUP_ID]
        )

        # Send via SendGrid
        sg = SendGridAPIClient(settings.email.api_key)
        response = sg.send(mail)

        if not 200 <= response.status_code < 300:
            logger.error(
                "Failed to send email to %s: %d - %s",
                batch.user_email,
                response.status_code,
                response.body,
            )
            raise EmailServiceError(f"SendGrid returned status {response.status_code}")

        logger.info(
            "Email sent successfully to %s (status: %d)",
            batch.user_email,
            response.status_code,
        )
        return True

    except Exception as e:
        logger.error("Error sending email to %s: %s", batch.user_email, e)
        raise EmailServiceError(f"Failed to send email: {e}") from e
