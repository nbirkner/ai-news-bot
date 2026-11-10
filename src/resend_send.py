"""Resend delivery for one newsletter issue. No Slack and no site commit.

AI News broadcasts to ``RESEND_AUDIENCE_ID`` from ``FROM``.
"""

import requests

API = "https://api.resend.com"
FROM = "Niki Birkner <ainews@nikibirkner.com>"


class SendError(Exception):
    """Resend refused the call. The issue was not marked sent."""


# Broadcast status "sent" means Resend accepted the broadcast. It is not
# inbox delivery. Delivery is last_event on GET /emails, and "sent" there
# is still not delivered.
_SUBMITTED_BROADCAST = {"sent", "queued", "sending"}
_DELIVERED_EVENTS = {"delivered", "opened", "clicked"}
_SUBMITTED_EVENTS = {"sent", "queued", "scheduled", "delivery_delayed"}


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "ai-news-bot",
    }


def _payload(response, action: str) -> dict:
    if response.status_code not in (200, 201):
        raise SendError(f"Resend {action} failed ({response.status_code})")
    try:
        data = response.json()
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def classify_last_event(last_event: str) -> str:
    """Map a Resend email last_event to submitted, delivered, or not_delivered.

    `sent` means Resend accepted the message. It does not mean a mailbox
    accepted it. `delivered`, `opened`, and `clicked` are the events that do.
    """
    event = (last_event or "").strip().lower()
    if event in _DELIVERED_EVENTS:
        return "delivered"
    if event in _SUBMITTED_EVENTS:
        return "submitted"
    if not event:
        return "unknown"
    return "not_delivered"


def classify_email(row: dict) -> dict:
    last_event = str((row or {}).get("last_event") or "").strip().lower()
    delivery = classify_last_event(last_event)
    return {
        "id": str((row or {}).get("id") or "").strip(),
        "subject": str((row or {}).get("subject") or "").strip(),
        "last_event": last_event,
        "delivery": delivery,
        "delivered": delivery == "delivered",
    }


def _email_rows(data: dict) -> list:
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def list_emails(*, api_key: str, get=None, page_size: int = 100, max_pages: int = 5) -> tuple[list, bool]:
    """GET /emails. Returns (rows, complete).

    `complete` is true when the last page reports has_more false. A truncated
    scan is not treated as proof that an issue was never submitted.
    """
    get = get or requests.get
    rows = []
    after = ""
    for _ in range(max_pages):
        params = {"limit": str(page_size)}
        if after:
            params["after"] = after
        try:
            response = get(
                f"{API}/emails",
                headers=_headers(api_key),
                params=params,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise SendError(f"Resend list emails failed ({exc})") from exc
        if response.status_code == 404:
            return rows, True
        data = _payload(response, "list emails")
        chunk = _email_rows(data)
        rows.extend(chunk)
        has_more = bool(data.get("has_more")) if isinstance(data, dict) else False
        if not has_more:
            return rows, True
        last_id = str(chunk[-1].get("id") or "").strip() if chunk else ""
        if not last_id or last_id == after:
            return rows, False
        after = last_id
    return rows, False


def find_email(*, api_key: str, subject: str, get=None) -> dict | None:
    """The newest listed email with this subject, or None when the list is complete.

    Raises SendError when the list cannot be read or is truncated before a match.
    An empty complete list means Resend has no such email object.
    """
    wanted = (subject or "").strip()
    if not wanted:
        return None
    rows, complete = list_emails(api_key=api_key, get=get)
    for row in rows:
        if str(row.get("subject") or "").strip() != wanted:
            continue
        found = classify_email(row)
        if found["id"]:
            return found
    if not complete:
        raise SendError("Resend email list was truncated before a matching subject")
    return None


def get_email(*, api_key: str, email_id: str, get=None) -> dict:
    """GET /emails/{id}. Delivery comes from last_event, not from the send call."""
    get = get or requests.get
    email_id = (email_id or "").strip()
    if not email_id:
        raise SendError("Resend get email needs an id")
    try:
        response = get(
            f"{API}/emails/{email_id}",
            headers=_headers(api_key),
            timeout=30,
        )
    except requests.RequestException as exc:
        raise SendError(f"Resend get email failed ({exc})") from exc
    data = _payload(response, "get email")
    found = classify_email(data)
    if not found["id"]:
        found["id"] = email_id
    return found


def find_broadcast(*, api_key: str, subject: str, get=None) -> dict | None:
    """Return an existing broadcast with this subject, or None.

    `submitted` means Resend already accepted or queued that broadcast.
    That is not inbox delivery. `draft` means a previous attempt created
    the broadcast and did not send it.
    """
    get = get or requests.get
    wanted = (subject or "").strip()
    if not wanted:
        return None
    try:
        response = get(
            f"{API}/broadcasts",
            headers=_headers(api_key),
            params={"limit": "50"},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise SendError(f"Resend list broadcasts failed ({exc})") from exc
    if response.status_code == 404:
        return None
    data = _payload(response, "list broadcasts")
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("name") or row.get("subject") or "").strip()
        if label != wanted:
            continue
        broadcast_id = str(row.get("id") or "").strip()
        if not broadcast_id:
            continue
        status = str(row.get("status") or "").strip().lower()
        return {
            "id": broadcast_id,
            "status": status,
            "submitted": status in _SUBMITTED_BROADCAST,
            "draft": status == "draft",
        }
    return None


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
) -> str:
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
    data = _payload(response, "send email")
    return str(data.get("id") or "").strip()
