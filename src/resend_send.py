"""Resend delivery for one newsletter issue. No Slack and no site commit.

AI News broadcasts to ``RESEND_AUDIENCE_ID`` from ``FROM``.
AI Lessons broadcasts to ``RESEND_LESSONS_AUDIENCE_ID`` from ``LESSONS_FROM``.
The lessons id is never filled in from the news audience.
"""

import requests

API = "https://api.resend.com"
FROM = "Niki Birkner <ainews@nikibirkner.com>"
LESSONS_FROM = "Niki Birkner <ailessons@nikibirkner.com>"
LESSONS_SEGMENT_NAME = "AI Lessons"
# Live AI News segment. A lessons broadcast must not use this id, even when
# RESEND_AUDIENCE_ID is unset in the lessons workflow.
NEWS_AUDIENCE_PREFIX = "a2f59f11"


class SendError(Exception):
    """Resend refused the call. The issue was not marked sent."""


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


def create_broadcast(
    *,
    api_key: str,
    audience_id: str,
    subject: str,
    html: str,
    text: str,
    post=None,
    from_address: str = "",
) -> str:
    """Create a draft broadcast. Audiences moved to segments; the id is the same value."""
    post = post or requests.post
    sender = (from_address or "").strip() or FROM
    response = post(
        f"{API}/broadcasts",
        headers=_headers(api_key),
        json={
            "audience_id": audience_id,
            "segment_id": audience_id,
            "from": sender,
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
    from_address: str = "",
) -> None:
    """One message via /emails. This is not a broadcast and does not mark the issue sent."""
    post = post or requests.post
    sender = (from_address or "").strip() or FROM
    response = post(
        f"{API}/emails",
        headers=_headers(api_key),
        json={
            "from": sender,
            "to": [to],
            "subject": subject,
            "html": html,
            "text": text,
        },
        timeout=60,
    )
    _payload(response, "send email")


def assert_lessons_audience(audience_id: str, *, news_audience_id: str = "") -> str:
    """The lessons segment id, or SendError. Never substitutes the news audience."""
    audience = (audience_id or "").strip()
    news = (news_audience_id or "").strip()
    if not audience or any(ch.isspace() for ch in audience):
        raise SendError(
            "RESEND_LESSONS_AUDIENCE_ID is not set. Not sending. "
            "The AI News audience is not a fallback."
        )
    if news and audience == news:
        raise SendError(
            "RESEND_LESSONS_AUDIENCE_ID is the AI News audience. Not sending."
        )
    if audience.lower().startswith(NEWS_AUDIENCE_PREFIX):
        raise SendError(
            "RESEND_LESSONS_AUDIENCE_ID is the AI News audience. Not sending."
        )
    return audience


def lessons_from_address(raw: str = "") -> str:
    """Lessons From header. Blank uses LESSONS_FROM, not the AI News address."""
    sender = (raw or "").strip()
    if not sender:
        return LESSONS_FROM
    if "@" not in sender or any(ch in sender for ch in "\r\n"):
        raise SendError("RESEND_LESSONS_FROM is not an email address. Not sending.")
    return sender


def _segment_rows(data: dict) -> list:
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def list_segments(*, api_key: str, get=None) -> list:
    """Every segment. Does not send mail."""
    get = get or requests.get
    found = []
    after = ""
    for _ in range(20):
        url = f"{API}/segments?limit=100"
        if after:
            url += f"&after={after}"
        response = get(url, headers=_headers(api_key), timeout=60)
        data = _payload(response, "list segments")
        rows = _segment_rows(data)
        found.extend(rows)
        if not data.get("has_more") or not rows:
            return found
        after = str(rows[-1].get("id") or "").strip()
        if not after:
            return found
    return found


def create_lessons_segment(*, api_key: str, news_audience_id: str = "", get=None, post=None) -> str:
    """Id of the segment named AI Lessons. Creates it when missing. Does not send mail."""
    if not (api_key or "").strip():
        raise SendError("RESEND_API_KEY is not set. Not creating a segment.")
    matches = [
        row for row in list_segments(api_key=api_key, get=get)
        if str(row.get("name") or "").strip() == LESSONS_SEGMENT_NAME
    ]
    if len(matches) > 1:
        raise SendError("More than one AI Lessons segment. Not choosing one.")
    if len(matches) == 1:
        segment_id = str(matches[0].get("id") or "").strip()
        if not segment_id:
            raise SendError("The AI Lessons segment has no id.")
        return assert_lessons_audience(segment_id, news_audience_id=news_audience_id)
    post = post or requests.post
    response = post(
        f"{API}/segments",
        headers=_headers(api_key),
        json={"name": LESSONS_SEGMENT_NAME},
        timeout=60,
    )
    data = _payload(response, "create segment")
    segment_id = str(data.get("id") or "").strip()
    if not segment_id:
        raise SendError("Resend create segment returned no id")
    return assert_lessons_audience(segment_id, news_audience_id=news_audience_id)
