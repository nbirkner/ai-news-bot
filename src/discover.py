"""Feeds plus Exa, then a diverse shortlist with full text.

This module does not call Together and does not post to Slack.
"""
import sys
from dataclasses import dataclass, field
from datetime import datetime

from src.dedupe import canonical_url, dedupe_articles, jaccard
from src.exa_search import ExaBudget, resolve_cap, search_queries
from src.fetch import fetch_all
from src.rank import shortlist_for_reading
from src.read import read_articles


@dataclass
class DiscoveryResult:
    pool: list = field(default_factory=list)
    shortlist: list = field(default_factory=list)
    fresh: list = field(default_factory=list)
    successes: int = 0
    failures: list = field(default_factory=list)
    source_count: int = 0
    feed_count: int = 0
    exa_count: int = 0
    exa_calls: int = 0
    exa_cap: int = 30
    exa_search_calls: int = 0
    exa_content_pages: int = 0
    exa_note: str = ""
    since: datetime | None = None


def _already_posted(article, items: list) -> bool:
    for item in items:
        if canonical_url(article.url) == canonical_url(item.get("url") or ""):
            return True
        if jaccard(article.title, item.get("title") or "") >= 0.55:
            return True
    return False


def prepare_candidates(
    config: dict,
    since: datetime,
    now: datetime,
    *,
    exa_api_key: str | None = None,
    seen: dict | None = None,
    recent_items: list | None = None,
    fetcher=fetch_all,
    searcher=None,
    reader=None,
) -> DiscoveryResult:
    """Discover, dedupe, and read. `searcher` and `reader` are test seams."""
    seen = seen or {}
    recent_items = recent_items or []
    result = DiscoveryResult(since=since, source_count=len(config.get("sources") or []))
    cap = resolve_cap(config)
    budget = ExaBudget(cap)
    result.exa_cap = budget.cap

    report = fetcher(config, since, now)
    result.successes = report.successes
    result.failures = list(report.failures)
    result.feed_count = len(report.articles)
    for failure in report.failures:
        print(f"Source failed: {failure}", file=sys.stderr)

    exa_articles = []
    if exa_api_key:
        search = searcher or search_queries
        exa_articles = search(exa_api_key, since, now, budget)
        result.exa_count = len(exa_articles)
    else:
        result.exa_note = "no EXA_API_KEY, feeds only"
        print("Exa skipped: EXA_API_KEY is not set. Feeds only.", file=sys.stderr)

    if config.get("read_full_text") and exa_api_key:
        # Contents calls happen inside the reader and share this budget.
        pass

    combined = list(report.articles) + list(exa_articles)
    fresh = []
    for article in combined:
        if canonical_url(article.url) in seen:
            continue
        if _already_posted(article, recent_items):
            print(f"Skip, already briefed: {article.title}", file=sys.stderr)
            continue
        fresh.append(article)
    result.fresh = fresh
    result.pool = dedupe_articles(fresh)
    limit = int(config.get("read_candidates", 15))
    result.shortlist = shortlist_for_reading(result.pool, limit=limit)

    if config.get("read_full_text"):
        read = reader or read_articles
        read(
            result.shortlist,
            exa_api_key=exa_api_key,
            budget=budget if exa_api_key else None,
        )

    result.exa_calls = budget.used
    result.exa_search_calls = budget.search_calls
    result.exa_content_pages = budget.content_pages
    result.exa_note = result.exa_note or budget.stop_reason
    extra = ""
    if result.exa_note and result.exa_note != budget.stop_reason:
        extra = f" ({result.exa_note})"
    print(budget.status_line() + extra, file=sys.stderr)
    print(
        f"Pool {len(result.pool)} after dedupe, shortlist {len(result.shortlist)} for reading",
        file=sys.stderr,
    )
    for index, article in enumerate(result.shortlist, start=1):
        print(f"  {index}. {article.source_name}: {article.title}", file=sys.stderr)
    return result
