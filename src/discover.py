"""Feeds, the weekly sources file, and optional Exa, then a shortlist.

Exa runs only when EXA_ENABLED=1. This module does not call Together
and does not post to Slack.
"""
import sys
from dataclasses import dataclass, field
from datetime import datetime

from src.dedupe import (
    canonical_url,
    dedupe_articles,
    event_already_happened,
    round_already_announced,
    same_underlying_event,
)
from src.guardrails import is_homepage_url
from src.rank import is_low_trust
from src.exa_search import ExaBudget, exa_enabled, resolve_cap, search_queries
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
    curated_count: int = 0
    sources_status: str = ""
    sources_error: str = ""
    exa_count: int = 0
    exa_calls: int = 0
    exa_cap: int = 6
    exa_search_calls: int = 0
    exa_content_pages: int = 0
    exa_note: str = ""
    since: datetime | None = None


def _already_posted(article, items: list) -> bool:
    for item in items:
        if canonical_url(article.url) == canonical_url(item.get("url") or ""):
            return True
        if same_underlying_event(article, item.get("title") or ""):
            return True
    return False


def discovery_blocked(result: DiscoveryResult) -> bool:
    """True when feeds failed and neither the sources file nor Exa contributed."""
    if result.curated_count > 0 or result.exa_count > 0:
        return False
    if result.successes == 0:
        return True
    if result.feed_count == 0 and result.successes * 2 < result.source_count:
        return True
    return False


def _remember_reflections(pool: list, curated: list) -> None:
    """Keep a curated reflection when dedupe prefers another copy of the URL."""
    notes = {}
    for article in curated or []:
        note = getattr(article, "reflection", "") or ""
        if note:
            notes[canonical_url(article.url)] = note
    if not notes:
        return
    for article in pool:
        if getattr(article, "reflection", ""):
            continue
        note = notes.get(canonical_url(article.url))
        if note:
            article.reflection = note


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
    curated: list | None = None,
    sources_status: str = "",
    sources_error: str = "",
) -> DiscoveryResult:
    """Discover, dedupe, and read. `searcher` and `reader` are test seams.

    `curated` is the weekly sources file. Exa runs only when EXA_ENABLED=1
    and an API key is set. The per-run cap is EXA_MAX_CALLS (default 6).
    """
    seen = seen or {}
    recent_items = recent_items or []
    curated = list(curated or [])
    result = DiscoveryResult(since=since, source_count=len(config.get("sources") or []))
    result.curated_count = len(curated)
    result.sources_status = sources_status
    result.sources_error = sources_error
    cap = resolve_cap(config)
    budget = ExaBudget(cap)
    result.exa_cap = budget.cap

    report = fetcher(config, since, now)
    result.successes = report.successes
    result.failures = list(report.failures)
    result.feed_count = len(report.articles)
    for failure in report.failures:
        print(f"Source failed: {failure}", file=sys.stderr)

    exa_on = exa_enabled()
    use_exa = bool(exa_on and exa_api_key)
    exa_articles = []
    if use_exa:
        search = searcher or search_queries
        exa_articles = search(exa_api_key, since, now, budget)
        result.exa_count = len(exa_articles)
    elif not exa_on:
        result.exa_note = "EXA_ENABLED is not 1, Exa off"
        print("Exa skipped: EXA_ENABLED is not 1. Feeds only.", file=sys.stderr)
    else:
        result.exa_note = "no EXA_API_KEY, feeds only"
        print("Exa skipped: EXA_API_KEY is not set. Feeds only.", file=sys.stderr)

    combined = list(curated) + list(report.articles) + list(exa_articles)
    prior = list(getattr(report, "prior", None) or [])
    fresh = []
    for article in combined:
        if canonical_url(article.url) in seen:
            continue
        if is_homepage_url(article.url):
            print(f"Skip, homepage url: {article.title}", file=sys.stderr)
            continue
        chosen = getattr(article, "from_sources_file", False)
        if not chosen and (
            event_already_happened(article, since) or round_already_announced(article, prior, since)
        ):
            print(f"Skip, event is older than the window: {article.title}", file=sys.stderr)
            continue
        if _already_posted(article, recent_items):
            print(f"Skip, already briefed: {article.title}", file=sys.stderr)
            continue
        if is_low_trust(article):
            article.source_priority = max(article.source_priority, 5)
        fresh.append(article)
    if config.get("read_full_text"):
        from src.read import fill_denied_stubs
        fill_denied_stubs(fresh)
    result.fresh = fresh
    result.pool = dedupe_articles(fresh)
    _remember_reflections(result.pool, curated)
    limit = int(config.get("read_candidates", 15))
    result.shortlist = shortlist_for_reading(result.pool, limit=limit)

    if config.get("read_full_text"):
        read = reader or read_articles
        read(
            result.shortlist,
            exa_api_key=exa_api_key if use_exa else None,
            budget=budget if use_exa else None,
        )
        kept = []
        for article in result.shortlist:
            if not getattr(article, "from_sources_file", False) and event_already_happened(article, since):
                print(f"Skip, event is older than the window: {article.title}", file=sys.stderr)
                continue
            if is_homepage_url(article.url):
                print(f"Skip, homepage url: {article.title}", file=sys.stderr)
                continue
            kept.append(article)
        result.shortlist = kept

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
