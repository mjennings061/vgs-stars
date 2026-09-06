"""Public unsubscribe endpoint."""

import html
import logging

import resend
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.services import database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/unsubscribe", tags=["unsubscribe"])

PAGE = """<html><head><title>Unsubscribe</title><style>
body {{ font-family: Arial, sans-serif; max-width: 30em; margin: 4em auto; color: #333; }}
button {{ font-size: 1em; padding: 0.6em 1.2em; }}
</style></head><body>{body}</body></html>"""


async def _email_for_batch(batch_id: str) -> str:
    """Look up the recipient address for a notification batch.

    Args:
        batch_id: Notification batch ID from the unsubscribe link.

    Returns:
        The recipient email address.

    Raises:
        HTTPException: If the batch or its email address is missing.
    """
    batch = await database.get_notification_batch(batch_id)
    email = (batch or {}).get("userEmail")
    if not email:
        raise HTTPException(status_code=404, detail="Unknown unsubscribe link")
    return email


@router.get("/{batch_id}", response_class=HTMLResponse)
async def confirm_unsubscribe(batch_id: str) -> str:
    """Show a confirmation page so link scanners cannot unsubscribe by accident."""
    email = await _email_for_batch(batch_id)
    return PAGE.format(
        body=(
            f"<h2>Unsubscribe {html.escape(email)}?</h2>"
            "<p>You will stop receiving STARS authorisation expiry notifications.</p>"
            f'<form method="post" action="/unsubscribe/{html.escape(batch_id)}">'
            '<button type="submit">Unsubscribe</button></form>'
        )
    )


@router.post("/{batch_id}", response_class=HTMLResponse)
async def do_unsubscribe(batch_id: str) -> str:
    """Suppress the address in Resend. Also the RFC 8058 one-click target."""
    email = await _email_for_batch(batch_id)

    try:
        resend.Suppressions.add({"email": email})
    except Exception as e:
        logger.error("Failed to suppress %s: %s", email, e)
        raise HTTPException(status_code=502, detail="Unsubscribe failed") from e

    logger.info("Unsubscribed %s via batch %s", email, batch_id)
    return PAGE.format(
        body=f"<h2>Unsubscribed</h2><p>{html.escape(email)} has been removed.</p>"
    )
