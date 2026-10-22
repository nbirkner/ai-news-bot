"""Resend delivery for one newsletter issue. No Slack and no site commit."""

import requests

API = "https://api.resend.com"
FROM = "Niki Birkner <ainews@nikibirkner.com>"


class SendError(Exception):
    """Resend refused the call. The issue was not marked sent."""


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _payload(response, action: str) -> dict:
    if response.status_code not in (200, 201):
        raise SendError(f"Resend {action} failed ({response.status_code})")
    try:
        data = response.json()
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def create_broadcast(
    *,
    api_key: str,
    audience_id: str,
    subject: str,
    html: str,
    text: str,
    post=None,
) -> str:
    """Create a draft broadcast. Audiences moved to segments; the id is the same value."""
    post = post or requests.post
    response = post(
        f"{API}/broadcasts",
        headers=_headers(api_key),
        json={
            "audience_id": audience_id,
            "segment_id": audience_id,
            "from": FROM,
            "subject": subject,
            "html": html,
            "text": text,
            "name": subject,
        },
        timeout=60,
    )
    data = _payload(response, "create broadcast")
    broadcast_id = str(data.get("id") or "").strip()
    if not broadcast_id:
        raise SendError("Resend create broadcast returned no id")
    return broadcast_id


def send_broadcast(*, api_key: str, broadcast_id: str, post=None) -> None:
    post = post or requests.post
    response = post(
        f"{API}/broadcasts/{broadcast_id}/send",
        headers=_headers(api_key),
        json={},
        timeout=60,
    )
    _payload(response, "send broadcast")


def send_test_email(
    *,
    api_key: str,
    to: str,
    subject: str,
    html: str,
    text: str,
    post=None,
) -> None:
    """One message via /emails. This is not a broadcast and does not mark the issue sent."""
    post = post or requests.post
    response = post(
        f"{API}/emails",
        headers=_headers(api_key),
        json={
            "from": FROM,
            "to": [to],
            "subject": subject,
            "html": html,
            "text": text,
        },
        timeout=60,
    )
    _payload(response, "send email")
