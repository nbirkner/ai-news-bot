"""Blocks that stop a public issue from being published.

Item drops (generic one-liners, non-public URLs) remove a story.
Issue blocks (Together-internal copy, unsafe text, an empty issue,
a duplicate, a schema failure, a second send in the same slot) stop
the whole issue. The newsletter command never sends either way.
"""

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from src.clean import split_sentences
from src.dedupe import canonical_url

_GENERIC = re.compile(
    r"\b(adds new features|it adds new|new features and(?: bug)? fixes|"
    r"various improvements|see the changelog|minor bug fixes)\b",
    re.I,
)
_TOGETHER = re.compile(
    r"\bfor together\b|"
    r"why it matters for together|"
    r"relevant to together|"
    r"together(?:\s+ai)?(?:'s|’s)?\s+(?:customers?|revenue|competitors?|pipeline|internals?|arr|mrr)\b|"
    r"\bcompetitors? (?:of|to) together\b|"
    r"\btogether(?:\s+ai)?\s+(?:customers?|revenue|internal)\b",
    re.I,
)
_UNSAFE = re.compile(
    r"\b(child sexual|csam|sexual exploitation of minors)\b",
    re.I,
)
_SECRET = re.compile(
    r"hooks\.slack\.com/|xox[baprs]-|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\bsk-[A-Za-z0-9]{20,}\b",
    re.I,
)
_PRIVATE_WORDS = re.compile(
    r"\b(confidential|internal only|not for distribution|do not share)\b",
    re.I,
)


def is_homepage_url(url: str) -> bool:
    """A site root or a quote page is not an article.

    MarketScreener story URLs contain ``/news/``. A ticker such as
    ``/quote/stock/...`` is the homepage for that symbol.
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    if path in {"", "/"}:
        return True
    lowered = path.lower()
    if "marketscreener.com" in host and "/news" not in lowered:
        return True
    if "/quote/" in lowered or lowered.startswith("/quote"):
        return True
    return False


def is_public_http_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    if not host or "." not in host:
        return False
    if host in {"localhost"} or host.endswith(".local") or host.endswith(".internal"):
        return False
    if host == "hooks.slack.com" or host.endswith(".hooks.slack.com"):
        return False
    if host.startswith("127.") or host.startswith("10.") or host.startswith("192.168."):
        return False
    return True


def is_generic_oneliner(headline: str, body: str) -> bool:
    """The failure mode is 'vLLM v0.32.0. It adds new features.'"""
    sentences = [sentence for sentence in split_sentences(body or "") if len(sentence) > 20]
    if len(sentences) < 2:
        return True
    if _GENERIC.search(f"{headline or ''} {body or ''}") and not re.search(r"\d", body or ""):
        return True
    return False


def issue_text(issue) -> str:
    parts = [issue.summary or "", issue.title or ""]
    for item in issue.items:
        parts.extend([
            item.what_happened or "",
            item.body or "",
            item.why_it_matters or "",
            item.url or "",
        ])
    return "\n".join(parts)


def item_block_reasons(item) -> list:
    """Hard blocks. A hit here stops the issue, it does not just drop the story."""
    text = " ".join([
        item.what_happened or "",
        item.body or "",
        item.why_it_matters or "",
        item.url or "",
    ])
    reasons = []
    if _TOGETHER.search(text):
        reasons.append("together internal or non-public")
    if _UNSAFE.search(text) or _SECRET.search(text) or _PRIVATE_WORDS.search(text):
        reasons.append("unsafe content")
    return reasons


def item_drop_reasons(item) -> list:
    reasons = []
    if not is_public_http_url(item.url):
        reasons.append("missing public source url")
    elif is_homepage_url(item.url):
        reasons.append("homepage url")
    if is_generic_oneliner(item.what_happened, item.body):
        reasons.append("generic one-liner")
    return reasons


def is_duplicate_issue(issue, recent: list | None) -> bool:
    if not recent or not issue.items:
        return False
    last = recent[-1]
    last_items = last.get("items") or []
    last_urls = {canonical_url(item.get("url") or "") for item in last_items if item.get("url")}
    these = {canonical_url(item.url) for item in issue.items if item.url}
    return bool(these) and these == last_urls


def slot_key(moment: datetime) -> str:
    """Most recent Monday/Wednesday/Friday 15:00 UTC at or before moment."""
    moment = moment.astimezone(timezone.utc)
    day = moment
    for _ in range(10):
        if day.weekday() in (0, 2, 4):
            slot = day.replace(hour=15, minute=0, second=0, microsecond=0)
            if slot <= moment:
                return slot.strftime("%Y-%m-%dT%H:%M:%SZ")
        day = day - timedelta(days=1)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def publishing_blocks(
    issue,
    *,
    recent: list | None = None,
    sending: bool = False,
    last_slot: str = "",
    now: datetime | None = None,
    schema_error: str = "",
) -> list:
    """Reasons that stop a publish. Dry runs pass sending=False."""
    reasons = []
    if schema_error:
        reasons.append(schema_error if schema_error.startswith("LLM") else f"schema validation: {schema_error}")
    for item in issue.items:
        reasons.extend(item_block_reasons(item))
    blob = issue_text(issue)
    if _TOGETHER.search(blob) and "together internal or non-public" not in reasons:
        reasons.append("together internal or non-public")
    if (_UNSAFE.search(blob) or _SECRET.search(blob) or _PRIVATE_WORDS.search(blob)) and "unsafe content" not in reasons:
        reasons.append("unsafe content")
    if not issue.items:
        reasons.append("too short")
    elif is_duplicate_issue(issue, recent):
        reasons.append("duplicate of the last issue")
    if sending and last_slot and now is not None and last_slot == slot_key(now):
        reasons.append("idempotency")
    # Preserve order, drop repeats.
    unique = []
    for reason in reasons:
        if reason not in unique:
            unique.append(reason)
    return unique
