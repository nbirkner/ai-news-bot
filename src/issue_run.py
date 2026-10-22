"""Build one public Issue. Does not post, send, or commit."""

import re
import sys
from datetime import datetime, timedelta, timezone

from src.dedupe import canonical_url, jaccard
from src.discover import discovery_blocked, prepare_candidates
from src.fetch import AI_TERMS, fetch_all
from src.guardrails import item_block_reasons, item_drop_reasons, publishing_blocks
from src.models import BriefingItem, Issue
from src.state import load_recent, load_seen, recent_items
from src.sources_file import load_sources_file, sources_path_for
from src.summarize import AuthError, SchemaError, compose_briefing

_TAGS = (
    ("models", re.compile(r"model|gpt-|claude|gemini|deepseek|qwen|weights", re.I)),
    ("agents", re.compile(r"\bagents?\b", re.I)),
    ("infrastructure", re.compile(r"chip|gpu|inference|data center|power|grid", re.I)),
    ("policy", re.compile(r"bill|regulat|lawsuit|policy|safety", re.I)),
    ("funding", re.compile(r"raise|funding|\$|billion|acqui", re.I)),
)


def previous_mwf_slot(end: datetime) -> datetime:
    """The scheduled run before `end`. Slots are Monday, Wednesday, Friday at 15:00 UTC."""
    end = _utc(end)
    day = end - timedelta(days=1)
    for _ in range(14):
        if day.weekday() in (0, 2, 4):
            slot = day.replace(hour=15, minute=0, second=0, microsecond=0)
            if slot < end:
                return slot
        day -= timedelta(days=1)
    return end - timedelta(hours=80)


def weekly_window_start(end: datetime) -> datetime:
    """Seven days before `end`. The Monday letter covers the previous Monday through this one."""
    return _utc(end) - timedelta(days=7)


def pacific_monday(moment: datetime | None = None) -> str:
    """Monday of the America/Los_Angeles week that contains `moment`, as YYYY-MM-DD."""
    from zoneinfo import ZoneInfo
    zone = ZoneInfo("America/Los_Angeles")
    local = (moment or datetime.now(zone)).astimezone(zone)
    monday = local - timedelta(days=local.weekday())
    return monday.strftime("%Y-%m-%d")


def parse_window_end(raw: str) -> datetime:
    text = (raw or "").strip()
    if not text:
        raise ValueError("window end is empty")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        year, month, day = (int(part) for part in text.split("-"))
        return datetime(year, month, day, 15, 0, tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def tags_for(items: list) -> list:
    blob = " ".join(f"{item.what_happened} {item.body}" for item in items)
    found = [name for name, pattern in _TAGS if pattern.search(blob)]
    return found or ["ai"]


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _empty(until: datetime, since: datetime) -> Issue:
    return Issue(
        title=f"AI briefing, {until.strftime('%B')} {until.day}",
        date=until.strftime("%Y-%m-%d"),
        summary="",
        tags=["ai"],
        items=[],
        since=since,
        until=until,
    )


def _shares_long_words(left: str, right: str) -> bool:
    from src.clean import tokens
    shared = {word for word in set(tokens(left)) & set(tokens(right)) if len(word) >= 6}
    return len(shared) >= 3


def _also_titles(items: list, shortlist: list, limit: int = 8) -> tuple:
    chosen = {canonical_url(item.url) for item in items if item.url}
    chosen_titles = [item.what_happened or "" for item in items]
    extras = []
    for article in shortlist or []:
        if canonical_url(article.url) in chosen:
            continue
        title = (article.title or "").strip()
        title = title.replace("\u2014", ", ").replace("\u2013", ", ").replace("—", ", ").replace("–", ", ")
        if not title or not AI_TERMS.search(title):
            continue
        if any(jaccard(title, previous) >= 0.45 for previous in chosen_titles + extras):
            continue
        if any(_shares_long_words(title, previous) for previous in chosen_titles + extras):
            continue
        extras.append(title)
        if len(extras) >= limit:
            break
    return tuple(extras)


def _story(row) -> BriefingItem:
    article = row[0]
    if len(row) >= 4:
        headline, body, why = row[1], row[2], row[3]
    else:
        headline, body, why = row[1], "", row[2]
    return BriefingItem(
        what_happened=headline,
        body=body or "",
        why_it_matters=why,
        url=article.url,
        source_name=article.source_name,
    )


def generate_issue(
    config: dict,
    *,
    since: datetime,
    until: datetime,
    api_key: str | None,
    exa_api_key: str | None = None,
    fetcher=fetch_all,
    searcher=None,
    reader=None,
    composer=None,
    seen_path=None,
    recent_path=None,
    recent=None,
    sending: bool = False,
    last_slot: str = "",
    sources_root=None,
) -> tuple:
    """Return (issue, failures, discovery). Never posts or sends.

    The Monday newsletter reads ``sources/<window end>.json`` first. A missing
    or invalid file falls back to RSS. Exa runs only when EXA_ENABLED=1.
    """
    until = _utc(until)
    since = _utc(since)
    print(f"Newsletter window {since.isoformat()} to {until.isoformat()}", file=sys.stderr)
    sources_path = sources_path_for(until, sources_root)
    loaded = load_sources_file(sources_path)
    if loaded.status == "ok":
        print(
            f"Sources file {sources_path}: {len(loaded.articles)} stories",
            file=sys.stderr,
        )
    elif loaded.status == "missing":
        print(f"Sources file {sources_path} is missing. RSS only.", file=sys.stderr)
    else:
        print(
            f"Sources file {sources_path} is invalid ({loaded.error}). RSS only.",
            file=sys.stderr,
        )

    seen = {}
    if seen_path is not None:
        from src.briefing import _normalize_seen
        seen = _normalize_seen(load_seen(seen_path))
    if recent is None:
        memory = recent_items(load_recent(recent_path)) if recent_path else []
    else:
        memory = list(recent)

    discovered = prepare_candidates(
        config,
        since,
        until,
        exa_api_key=exa_api_key,
        seen=seen,
        recent_items=memory,
        fetcher=fetcher,
        searcher=searcher,
        reader=reader,
        curated=loaded.articles,
        sources_status=loaded.status,
        sources_error=loaded.error,
    )
    if discovery_blocked(discovered):
        return _empty(until, since), ["Too many sources failed to trust an empty issue."], discovered

    limit = int(config.get("max_items", 7))
    rank = composer or compose_briefing
    schema_error = ""
    try:
        lede, pairs = rank(
            list(discovered.shortlist),
            api_key,
            config.get("together_model") or "",
            strict_auth=bool(api_key),
            recent=memory,
            limit=limit,
            require_full_body=bool(api_key),
            since=since,
            until=until,
        )
    except AuthError as exc:
        return _empty(until, since), ["Together API rejected the key or model name. Not publishing."], discovered
    except SchemaError as exc:
        schema_error = str(exc)
        lede, pairs = "", []

    items = []
    blocked = []
    for row in pairs:
        item = _story(row)
        hard = item_block_reasons(item)
        if hard:
            blocked.extend(hard)
            print(f"Blocked story: {item.what_happened}", file=sys.stderr)
            continue
        drops = item_drop_reasons(item)
        if drops:
            print(f"Dropped ({', '.join(drops)}): {item.what_happened}", file=sys.stderr)
            continue
        items.append(item)

    if blocked or schema_error:
        issue = _empty(until, since)
    else:
        issue = Issue(
            title=f"AI briefing, {until.strftime('%B')} {until.day}",
            date=until.strftime("%Y-%m-%d"),
            summary=lede or "",
            tags=tags_for(items),
            items=items,
            since=since,
            until=until,
            also_in_window=_also_titles(items, discovered.shortlist),
        )
    stored = load_recent(recent_path) if recent_path is not None else []
    failures = []
    if schema_error:
        failures.append(schema_error)
    failures.extend(blocked)
    if not schema_error and not blocked:
        failures.extend(publishing_blocks(
            issue,
            recent=stored,
            sending=sending,
            last_slot=last_slot,
            now=until,
        ))
    print(
        f"Issue {issue.date}: {len(issue.items)} stories, failures={len(failures)}",
        file=sys.stderr,
    )
    return issue, failures, discovered
