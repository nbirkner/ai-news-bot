"""Public web discovery through Exa.

Each POST to /search or /contents counts as one call. EXA_MAX_CALLS
(default 30) is a hard cap for the whole run. Callers log budget.used.
A missing key, a credit error, or any other Exa failure must not sink
the briefing: feeds still run.
"""
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from src.fetch import in_window, parse_iso
from src.models import Article

EXA_SEARCH_URL = "https://api.exa.ai/search"
EXA_CONTENTS_URL = "https://api.exa.ai/contents"

# Evergreen queries. The date filter, not the wording, scopes the window.
DISCOVERY_QUERIES = (
    "new AI model release OpenAI Anthropic Google DeepMind Meta DeepSeek Qwen Moonshot Mistral",
    "AI model API pricing change or new token price",
    "LLM inference GPU custom chip or serving stack announcement",
    "open weights language model release",
    "AI regulation federal bill lawsuit or export control",
    "AI lab funding round valuation acquisition revenue ARR or IPO",
    "frontier AI lab revenue lawsuit child safety or policy change",
    "AI agent product launch security incident or leaked API key",
    "AI data center power grid energy deal",
)

_SKIP_HOST_BITS = (
    "twitter.com",
    "x.com",
    "facebook.com",
    "linkedin.com",
    "reddit.com",
    "youtube.com",
    "instagram.com",
    "tiktok.com",
    "wikipedia.org",
    "news.ycombinator.com",
)

# host suffix -> (source name, kind, priority). Lower priority wins dedupe.
_HOSTS = (
    ("openai.com", "OpenAI", "lab", 1),
    ("anthropic.com", "Anthropic", "lab", 1),
    ("deepmind.google", "Google DeepMind", "lab", 1),
    ("blog.google", "Google", "lab", 1),
    ("ai.meta.com", "Meta AI", "lab", 1),
    ("ai.facebook.com", "Meta AI", "lab", 1),
    ("mistral.ai", "Mistral", "lab", 1),
    ("nvidia.com", "NVIDIA", "lab", 1),
    ("deepseek.com", "DeepSeek", "lab", 1),
    ("qwen.ai", "Qwen", "lab", 1),
    ("moonshot.ai", "Moonshot", "lab", 1),
    ("huggingface.co", "Hugging Face", "release", 2),
    ("arxiv.org", "arXiv", "paper", 4),
    ("github.com", "GitHub", "release", 2),
    ("reuters.com", "Reuters", "news", 2),
    ("politico.com", "Politico", "news", 2),
    ("cnbc.com", "CNBC", "news", 2),
    ("techcrunch.com", "TechCrunch", "news", 2),
    ("fortune.com", "Fortune", "news", 2),
    ("theverge.com", "The Verge", "news", 3),
    ("axios.com", "Axios", "news", 3),
    ("investing.com", "Investing.com", "news", 2),
    ("federalregister.gov", "Federal Register", "news", 1),
    ("engineering.fb.com", "Meta Engineering", "lab", 1),
    ("minimax.io", "MiniMax", "lab", 1),
    ("pymnts.com", "PYMNTS", "news", 2),
)


class ExaCreditsError(Exception):
    """The key is valid and the account is out of credits or over budget."""


class ExaRequestError(Exception):
    """Exa rejected the call or the network failed. Fall back to feeds."""


class ExaBudget:
    def __init__(self, cap: int):
        self.cap = max(0, int(cap))
        self.used = 0
        self.search_calls = 0
        self.content_pages = 0
        self.stop_reason = ""

    def acquire(self) -> bool:
        if self.used >= self.cap:
            if not self.stop_reason:
                self.stop_reason = "cap"
            return False
        self.used += 1
        return True

    def status_line(self) -> str:
        note = f" ({self.stop_reason})" if self.stop_reason else ""
        return f"Exa calls: {self.used}/{self.cap}{note}"


def cap_from_env(default: int = 30) -> int:
    raw = os.environ.get("EXA_MAX_CALLS", "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        print(f"Warning: EXA_MAX_CALLS={raw!r} is not an integer. Using {default}.", file=sys.stderr)
        return default


def resolve_cap(config: dict | None = None) -> int:
    if os.environ.get("EXA_MAX_CALLS", "").strip():
        return cap_from_env()
    if config and config.get("exa_max_calls") is not None:
        return max(0, int(config["exa_max_calls"]))
    return 30


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _credits(response) -> bool:
    if response.status_code == 402:
        return True
    text = (getattr(response, "text", "") or "").lower()
    return "credit" in text or "payment required" in text or "quota" in text


def _post_json(url: str, api_key: str, payload: dict, post) -> dict:
    try:
        response = post(
            url,
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=45,
        )
    except requests.RequestException as exc:
        raise ExaRequestError(str(exc)) from exc
    if _credits(response):
        raise ExaCreditsError((response.text or "")[:300])
    if response.status_code >= 400:
        raise ExaRequestError(f"{response.status_code} {(response.text or '')[:300]}")
    try:
        body = response.json()
    except ValueError as exc:
        raise ExaRequestError("Exa returned non-JSON") from exc
    if not isinstance(body, dict):
        raise ExaRequestError("Exa returned an unexpected payload")
    return body


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _skip_host(host: str) -> bool:
    return any(host == bit or host.endswith("." + bit) for bit in _SKIP_HOST_BITS)


def _classify(host: str) -> tuple:
    for suffix, name, kind, priority in _HOSTS:
        if host == suffix or host.endswith("." + suffix):
            return name, kind, priority
    label = host.split(".")[0] if host else "Web"
    return label[:40] or "Web", "news", 3


def article_from_result(result: dict, since: datetime, now: datetime) -> Article | None:
    if not isinstance(result, dict):
        return None
    url = (result.get("url") or "").strip()
    title = (result.get("title") or "").strip()
    if not url or not title or not url.startswith("http"):
        return None
    host = _host(url)
    if not host or _skip_host(host):
        return None
    published = parse_iso(result.get("publishedDate") or "")
    if published is None:
        published = now
    elif not in_window(published, since, now):
        return None
    name, kind, priority = _classify(host)
    text = result.get("text") or ""
    if not isinstance(text, str):
        text = ""
    preview = " ".join(text.split())[:500]
    article = Article(
        url=url,
        title=" ".join(title.split()),
        source_name=name,
        source_domain=host,
        published_at=published,
        content_preview=preview,
        source_kind=kind,
        source_priority=priority,
        full_text=text[:12000],
    )
    return article


def search_queries(
    api_key: str,
    since: datetime,
    now: datetime,
    budget: ExaBudget,
    *,
    num_results: int = 5,
    queries: tuple = DISCOVERY_QUERIES,
    post=None,
    contents_reserve: int | None = None,
) -> list:
    """Run the query list until the cap, credits, or a hard error.

    `contents_reserve` calls are left unspent so a later contents request
    can still run. They are not consumed here.
    """
    post = post or requests.post
    if contents_reserve is None:
        contents_reserve = 2 if budget.cap >= 4 else (1 if budget.cap >= 2 else 0)
    search_limit = max(0, budget.cap - contents_reserve)
    found = []
    for query in queries:
        if budget.used >= search_limit or budget.stop_reason in {"credits", "error"}:
            if budget.used >= search_limit and not budget.stop_reason:
                budget.stop_reason = "cap"
            break
        if not budget.acquire():
            break
        budget.search_calls += 1
        payload = {
            "query": query,
            "type": "auto",
            "numResults": num_results,
            "startPublishedDate": _iso(since),
            "endPublishedDate": _iso(now),
        }
        try:
            body = _post_json(EXA_SEARCH_URL, api_key, payload, post)
        except ExaCreditsError as exc:
            budget.stop_reason = "credits"
            print(f"Exa credits exhausted ({exc}). Using feeds.", file=sys.stderr)
            break
        except ExaRequestError as exc:
            budget.stop_reason = "error"
            print(f"Exa search failed ({exc}). Using feeds.", file=sys.stderr)
            break
        batch = []
        for result in body.get("results") or []:
            article = article_from_result(result, since, now)
            if article is not None:
                batch.append(article)
        print(f"Exa search: {len(batch)} hits for {query[:72]}", file=sys.stderr)
        found.extend(batch)
    return found


def fetch_contents(api_key: str, urls: list, budget: ExaBudget, *, post=None) -> dict:
    """One contents call for many URLs. Returns url -> text. Empty on failure."""
    post = post or requests.post
    cleaned = []
    seen = set()
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            cleaned.append(url)
    if not cleaned or not budget.acquire():
        if cleaned and budget.stop_reason == "cap":
            print("Exa contents skipped: call cap reached.", file=sys.stderr)
        return {}
    pages = cleaned[:15]
    budget.content_pages += len(pages)
    payload = {
        "urls": pages,
        "text": {"maxCharacters": 8000, "verbosity": "standard"},
    }
    try:
        body = _post_json(EXA_CONTENTS_URL, api_key, payload, post)
    except ExaCreditsError as exc:
        budget.stop_reason = "credits"
        print(f"Exa contents out of credits ({exc}).", file=sys.stderr)
        return {}
    except ExaRequestError as exc:
        print(f"Exa contents failed ({exc}).", file=sys.stderr)
        return {}
    mapping = {}
    for result in body.get("results") or []:
        if not isinstance(result, dict):
            continue
        url = result.get("url") or result.get("id") or ""
        text = result.get("text") or ""
        if url and isinstance(text, str) and text.strip():
            mapping[url] = text
    print(f"Exa contents: text for {len(mapping)} of {len(cleaned[:15])} urls", file=sys.stderr)
    return mapping
