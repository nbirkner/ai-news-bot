"""Publish guardrails.

A failing check blocks Slack, the site archive, and email. The caller logs
the reasons and posts a short Slack alert instead of the issue.
"""
import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from src.dedupe import canonical_url

# A release headline can be short ("vLLM v0.32.0"). The why-line has to be a sentence.
MIN_WHAT = 8
MIN_WHY = 20
MIN_ISSUE_CHARS = 80

# Strong profanity and a few unsafe phrases. Ordinary news words
# ("attack", "kill", "hell") are left alone.
_PROFANITY = re.compile(
    r"\b(?:fuck(?:ing|er|ed)?|shit(?:ty)?|bitch|asshole|cunt|dickhead|"
    r"nigger|nigga|faggot|retard|whore)\b",
    re.I,
)
_UNSAFE = re.compile(
    r"\b(?:child\s+porn|csam|child\s+sexual|sexual\s+abuse\s+of\s+a\s+(?:child|minor)|"
    r"how\s+to\s+(?:make|build)\s+a\s+bomb)\b",
    re.I,
)
_COMPANY = re.compile(r"\bTogether(?:\s+AI|\.ai)\b|\btogether\.ai\b", re.I)
_PRIVATE_NEAR = re.compile(
    r"\b(?:revenue|arr|mrr|nrr|churn|bookings|pipeline|customers?|"
    r"internal|internals|confidential|unreleased|nonpublic)\b",
    re.I,
)
_FOR_TOGETHER = re.compile(r"\bfor together\b", re.I)
_COMPANY_PRIVATE = re.compile(
    r"\btogether(?:\s+ai)?(?:'s|’s)?\s+"
    r"(?:customers?|revenue|arr|mrr|internals?|confidential|pipeline|bookings)\b",
    re.I,
)
_OUR_PRIVATE = re.compile(
    r"\bour\s+(?:arr|mrr|revenue|customers|pipeline|bookings|internal\s+\w+)\b",
    re.I,
)
_SECRET = re.compile(
    r"hooks\.slack\.com|xox[baprs]-|(?:sk|ghp|github_pat)_[A-Za-z0-9]{8,}|"
    r"\b(?:api[_-]?key|token|password)\s*[:=]\s*\S+",
    re.I,
)
_EMAIL = re.compile(r"[\w.+-]+@together\.ai\b", re.I)
_URL_PRIVATE = re.compile(r"revenue|customer|internal|confidential", re.I)

UA = "ai-news-bot/1.0 (+https://www.nikibirkner.com/newsletter)"


@dataclass
class Violation:
    code: str
    detail: str


def structural_url_problem(url: str) -> str | None:
    if not url or not str(url).strip():
        return "missing source URL"
    parsed = urlparse(str(url).strip())
    if parsed.scheme not in {"http", "https"}:
        return "source URL must be http or https"
    if not parsed.hostname:
        return "source URL has no host"
    if parsed.username or parsed.password:
        return "source URL contains credentials"
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "0.0.0.0", "127.0.0.1"} or host.endswith(".local") or host.endswith(".internal"):
        return "source URL is not public"
    return None


def check_source_url(url: str, *, request=None) -> tuple:
    """Return (ok, detail). 404 and connection failures fail. A bot wall does not."""
    problem = structural_url_problem(url)
    if problem:
        return False, problem
    request = request or requests.request
    headers = {"User-Agent": UA}
    try:
        response = request("HEAD", url, headers=headers, allow_redirects=True, timeout=8)
        status = getattr(response, "status_code", 0)
        if status in (405, 501):
            response = request("GET", url, headers=headers, allow_redirects=True, timeout=8)
            status = getattr(response, "status_code", 0)
    except Exception as exc:
        return False, f"request failed ({type(exc).__name__})"
    if status in (404, 410):
        return False, f"source URL returned {status}"
    if 200 <= status < 400 or status >= 400:
        return True, "ok" if status < 400 else f"host responded {status}"
    return False, f"unexpected status {status}"


def check_source_urls(items, url_checker=None) -> Violation | None:
    checker = url_checker or check_source_url
    problems = []
    for index, item in enumerate(items, start=1):
        url = getattr(item, "url", "") or ""
        try:
            ok, detail = checker(url)
        except Exception as exc:
            ok, detail = False, f"request failed ({type(exc).__name__})"
        if not ok:
            problems.append(f"item {index}: {detail}")
    if not problems:
        return None
    return Violation("source_urls", "; ".join(problems))


def together_private_detail(text: str) -> str | None:
    """Together AI internals, customers, revenue, or other private material."""
    if not text:
        return None
    if _SECRET.search(text):
        return "text contains a secret-like token"
    if _EMAIL.search(text):
        return "text contains a Together email address"
    if _FOR_TOGETHER.search(text):
        return "text uses a for Together angle"
    if _COMPANY_PRIVATE.search(text):
        return "text mentions Together customers, revenue, or internals"
    if _OUR_PRIVATE.search(text):
        return "text speaks as the company about private commercial details"
    for match in _COMPANY.finditer(text):
        start = max(0, match.start() - 60)
        end = min(len(text), match.end() + 60)
        if _PRIVATE_NEAR.search(text[start:end]):
            return "text mentions Together next to private or commercial details"
    return None


def check_together_private(text: str, urls=()) -> Violation | None:
    detail = together_private_detail(text)
    if detail:
        return Violation("together_private", detail)
    for url in urls:
        if _SECRET.search(url or ""):
            return Violation("together_private", "a source URL contains a secret-like token")
        if re.search(r"together\.ai", url or "", re.I) and _URL_PRIVATE.search(url or ""):
            return Violation("together_private", "a source URL points at Together private material")
    return None


def issue_prose(title: str, lede: str, items) -> str:
    parts = [title or "", lede or ""]
    for item in items:
        parts.append(getattr(item, "what_happened", "") or "")
        parts.append(getattr(item, "why_it_matters", "") or "")
        parts.append(getattr(item, "source_name", "") or "")
    return "\n".join(parts)


def content_fingerprint(items, prose: str) -> str:
    urls = sorted(canonical_url(getattr(item, "url", "") or "") for item in items)
    blob = "\n".join(urls) + "\n" + re.sub(r"\s+", " ", prose).strip().lower()
    return hashlib.sha256(blob.encode()).hexdigest()


def check_substance(items, prose: str, last_issue: dict | None) -> Violation | None:
    if not items:
        return Violation("substance", "issue is empty")
    short = []
    for index, item in enumerate(items, start=1):
        what = (getattr(item, "what_happened", "") or "").strip()
        why = (getattr(item, "why_it_matters", "") or "").strip()
        if len(what) < MIN_WHAT or len(why) < MIN_WHY:
            short.append(f"item {index} is too short")
    if short:
        return Violation("substance", "; ".join(short))
    if len(re.sub(r"\s+", " ", prose).strip()) < MIN_ISSUE_CHARS:
        return Violation("substance", "issue text is too short")
    if last_issue:
        previous_urls = {canonical_url(url) for url in (last_issue.get("urls") or [])}
        current_urls = {canonical_url(getattr(item, "url", "") or "") for item in items}
        if previous_urls and current_urls == previous_urls:
            return Violation("substance", "issue repeats the URLs in the last issue")
        if last_issue.get("fingerprint") and last_issue["fingerprint"] == content_fingerprint(items, prose):
            return Violation("substance", "issue repeats the last issue")
    return None


def check_safety(text: str) -> Violation | None:
    if _PROFANITY.search(text or "") or _UNSAFE.search(text or ""):
        return Violation("safety", "text contains profanity or unsafe content")
    return None


def check_schema(payload) -> Violation | None:
    from src.summarize import validate_ranking

    errors = validate_ranking(payload)
    if not errors:
        return None
    return Violation("schema", "; ".join(errors))


def check_idempotency(key: str, published: list) -> Violation | None:
    if not key:
        return Violation("idempotency", "missing idempotency key")
    for row in published or []:
        if row.get("idempotency_key") == key:
            return Violation("idempotency", f"an issue was already published for {key}")
    return None


def evaluate_issue(
    *,
    title: str,
    lede: str,
    items,
    idempotency_key: str,
    published: list | None = None,
    url_checker=None,
    llm_payload=None,
) -> list:
    """Run every guardrail. Schema runs only when model output is present."""
    prose = issue_prose(title, lede, items)
    urls = [getattr(item, "url", "") or "" for item in items]
    last = published[-1] if published else None
    checks = [
        check_source_urls(items, url_checker=url_checker),
        check_together_private(prose, urls),
        check_substance(items, prose, last),
        check_safety(prose),
        check_idempotency(idempotency_key, published or []),
    ]
    if llm_payload is not None:
        checks.append(check_schema(llm_payload))
    return [violation for violation in checks if violation is not None]


def alert_text(violations: list) -> str:
    lines = [
        "*AI news was not published.*",
        "Guardrails blocked this issue. Nothing was sent to Slack as a digest, the site, or email.",
    ]
    for violation in violations:
        lines.append(f"• {violation.code}: {violation.detail}")
    return "\n".join(lines) + "\n"
