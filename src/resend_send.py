"""Resend delivery for one newsletter issue. No Slack and no site commit.

AI News broadcasts to ``RESEND_AUDIENCE_ID`` from ``FROM``.
The live account's history is individual emails, not broadcasts. A missing
broadcast list is not proof that an issue was never submitted. Exact subject
match on GET /emails is the submission check. ``last_event`` of sent, queued,
scheduled, or delivery_delayed is not inbox delivery.

Idempotency, from https://resend.com/docs/dashboard/emails/idempotency-keys
as fetched for this change:

- ``Idempotency-Key`` is documented for POST /emails, POST /emails/batch,
  and the inbox send, reply, and forward calls. It is not documented for
  POST /broadcasts or POST /broadcasts/{id}/send. This module does not send
  the header on those broadcast calls. A retry of create or send broadcast
  can create or send another broadcast if the list check does not see the
  first one yet.
- On POST /emails, the same key and the same payload within 24 hours returns
  the original response and does not send again. The key expires after 24
  hours, so the same key the next day is a new send.
- The same key with a different payload returns 409 ``invalid_idempotent_request``.
  The new body is not sent. This module does not mint a new key to push that
  body through.
- 409 ``concurrent_idempotent_requests`` means the first request is still in
  progress. It is not retried as a second send.
"""

import time

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
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


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


def same_issue_subject(wanted: str, candidate: str) -> bool:
    """True only when the subjects are the same issue line.

    A font test or a prefixed test send is a different subject. Substring
    and prefix matches are not this issue.
    """
    left = (wanted or "").strip()
    right = (candidate or "").strip()
    return bool(left) and left == right


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
        if not same_issue_subject(wanted, str(row.get("subject") or "")):
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
        label = str(row.get("name") or row.get("subject") or "")
        if not same_issue_subject(wanted, label):
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


def post_with_retry(
    post,
    url: str,
    *,
    headers: dict,
    payload: dict,
    timeout: int,
    idempotency_key: str,
    action: str,
):
    """POST up to three times on a timeout or a temporary status.

    The idempotency key stays the same across attempts so a retry after the
    provider accepted the first request does not create a second resource.
    """
    send_headers = dict(headers)
    if idempotency_key:
        send_headers["Idempotency-Key"] = idempotency_key
    response = None
    for attempt in range(3):
        try:
            response = post(url, headers=send_headers, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            if attempt == 2:
                raise SendError(f"Resend {action} failed ({exc})") from exc
            time.sleep(0)
            continue
        if response.status_code in _RETRYABLE_STATUS:
            if attempt == 2:
                raise SendError(f"Resend {action} failed ({response.status_code})")
            time.sleep(0)
            continue
        return response
    raise SendError(f"Resend {action} failed")


def create_broadcast(
    *,
    api_key: str,
    audience_id: str,
    subject: str,
    html: str,
    text: str,
    post=None,
) -> str:
    """Create a draft broadcast. Audiences moved to segments; the id is the same value.

    Resend does not document Idempotency-Key on this endpoint, so none is sent.
    """
    post = post or requests.post
    response = post_with_retry(
        post,
        f"{API}/broadcasts",
        headers=_headers(api_key),
        payload={
            "audience_id": audience_id,
            "segment_id": audience_id,
            "from": FROM,
            "subject": subject,
            "html": html,
            "text": text,
            "name": subject,
        },
        timeout=60,
        idempotency_key="",
        action="create broadcast",
    )
    data = _payload(response, "create broadcast")
    broadcast_id = str(data.get("id") or "").strip()
    if not broadcast_id:
        raise SendError("Resend create broadcast returned no id")
    return broadcast_id


def send_broadcast(*, api_key: str, broadcast_id: str, post=None) -> None:
    """Send one API-created broadcast.

    Resend does not document Idempotency-Key here. Duplicate protection is the
    list check in the caller, not this header.
    """
    post = post or requests.post
    response = post_with_retry(
        post,
        f"{API}/broadcasts/{broadcast_id}/send",
        headers=_headers(api_key),
        payload={},
        timeout=60,
        idempotency_key="",
        action="send broadcast",
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
    idempotency_key: str = "",
) -> str:
    """One message via /emails. This is not a broadcast and does not mark the issue sent.

    ``idempotency_key`` is the POST /emails contract. Retries send this same
    payload. A different payload under this key is a 409 and is not sent.
    """
    post = post or requests.post
    response = post_with_retry(
        post,
        f"{API}/emails",
        headers=_headers(api_key),
        payload={
            "from": FROM,
            "to": [to],
            "subject": subject,
            "html": html,
            "text": text,
        },
        timeout=60,
        idempotency_key=idempotency_key,
        action="send email",
    )
    data = _payload(response, "send email")
    return str(data.get("id") or "").strip()
