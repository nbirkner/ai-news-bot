"""Send one issue through the Buttondown API.

BUTTONDOWN_API_KEY is optional. When it is unset this module logs a skip and
does not raise, so a digest can still reach Slack and the site archive.
"""
import os
import sys

import requests

BUTTONDOWN_EMAILS_URL = "https://api.buttondown.com/v1/emails"


def _redact(text: str, secret: str | None) -> str:
    if not text:
        return ""
    if secret:
        text = text.replace(secret, "[redacted]")
    return text[:300]


def send_issue(issue, *, api_key=None, post=None) -> dict:
    """Create an email with status about_to_send. No key means no network call."""
    if api_key is None:
        api_key = os.environ.get("BUTTONDOWN_API_KEY") or None
    if isinstance(api_key, str):
        api_key = api_key.strip() or None
    if not api_key:
        print("BUTTONDOWN_API_KEY is not set. Skipping the email send.", file=sys.stderr)
        return {"skipped": True, "reason": "BUTTONDOWN_API_KEY is not set"}

    from src.newsletter import render_email_markdown

    post = post or requests.post
    body = render_email_markdown(issue)
    response = post(
        BUTTONDOWN_EMAILS_URL,
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "subject": issue.title,
            "body": body,
            "status": "about_to_send",
        },
        timeout=30,
    )
    status = getattr(response, "status_code", 0)
    if status >= 400:
        detail = _redact(getattr(response, "text", "") or "", api_key)
        raise RuntimeError(f"Buttondown returned {status}: {detail}")
    print(f"Buttondown accepted the email ({status}).", file=sys.stderr)
    return {"skipped": False, "status": status}
