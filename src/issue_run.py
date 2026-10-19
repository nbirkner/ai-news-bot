"""Build one public Issue. Does not post, send, or commit."""

import re
import sys
from datetime import datetime, timedelta, timezone

from src.discover import prepare_candidates
from src.fetch import fetch_all
from src.guardrails import item_block_reasons, item_drop_reasons, publishing_blocks
from src.models import BriefingItem, Issue
from src.state import load_recent, load_seen, recent_items
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
) -> tuple:
    """Return (issue, failures, discovery). Never posts or sends."""
    until = _utc(until)
    since = _utc(since)
    print(f"Newsletter window {since.isoformat()} to {until.isoformat()}", file=sys.stderr)

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
    )
    feeds_untrusted = discovered.successes == 0 or (
        discovered.feed_count == 0 and discovered.successes * 2 < discovered.source_count
    )
    if feeds_untrusted and discovered.exa_count == 0:
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
