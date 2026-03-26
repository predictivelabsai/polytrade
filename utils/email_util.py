"""
Email Utility — Postmark

Sends emails via the Postmark HTTP API.
Requires POSTMARK_API_KEY, FROM_EMAIL env vars.
"""

import os
import logging

import requests

logger = logging.getLogger(__name__)


def send_email_to(to_email: str, subject: str, body_html: str) -> bool:
    """Send an email to a specific recipient via Postmark."""
    api_key = os.getenv("POSTMARK_API_KEY")
    from_email = os.getenv("FROM_EMAIL")

    if not all([api_key, from_email]):
        logger.warning("Postmark env vars not set (POSTMARK_API_KEY, FROM_EMAIL)")
        return False

    try:
        resp = requests.post(
            "https://api.postmarkapp.com/email",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Postmark-Server-Token": api_key,
            },
            json={
                "From": from_email,
                "To": to_email,
                "Subject": subject,
                "HtmlBody": body_html,
                "MessageStream": "outbound",
            },
            timeout=15,
        )
        resp.raise_for_status()
        logger.info(f"Email sent to {to_email}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False
