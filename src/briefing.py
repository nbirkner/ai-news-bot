import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.dedupe import canonical_url
from src.discover import prepare_candidates
from src.expect import coverage_report
from src.fetch import enrich_previews, fetch_all
from src.models import BriefingItem
from src.rank import score_article, select_items
from src.slack import post_to_slack, render_briefing, slack_payload
from src.state import (
    load_last_run,
    load_recent,
    load_seen,
    mark_seen,
    prune_old,
    recent_items,
    remember_briefing,
    save_last_run,
    save_seen,
)
from src.summarize import AuthError, compose_briefing, theme_lede, write_copies


def _story_fields(row):
    """(article, headline, body, why) or the older (article, headline, why)."""
    article = row[0]
    if len(row) >= 4:
        return article, row[1], row[2], row[3]
    return article, row[1], "", row[2]


class BriefingError(Exception):
    def __init__(self, message: str, text: str = ""):
        super().__init__(message)
        self.text = text


def window_start(
    last: datetime | None,
    now: datetime,
    fallback_hours: int,
    max_hours: int,
) -> datetime:
    """Cover the gap since the previous run, with a cap so a long outage stays bounded."""
    earliest = now - timedelta(hours=max_hours)
    if last is None:
        start = now - timedelta(hours=fallback_hours)
    else:
        start = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        start = start.astimezone(timezone.utc)
    if start < earliest:
        start = earliest
    if start > now:
        start = earliest
    return start


def parse_since_value(raw: str, now: datetime) -> datetime:
    """ISO-8601, or a trailing h/d window such as 80h."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty since value")
    relative = re.fullmatch(r"(\d+)([hd])", text)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        delta = timedelta(hours=amount) if unit == "h" else timedelta(days=amount)
        return now - delta
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_seen(seen: dict) -> dict:
    normalized = {}
    for url, stamp in seen.items():
        normalized[canonical_url(url)] = stamp
    return normalized


def run_briefing(
    config: dict,
    *,
    dry_run: bool,
    api_key: str | None,
    webhook_url: str | None,
    now: datetime | None = None,
    seen_path=None,
    last_run_path=None,
    recent_path=None,
    fetcher=fetch_all,
    enricher=enrich_previews,
    poster=post_to_slack,
    exa_api_key: str | None = None,
    since_override: datetime | None = None,
    expect_headlines: list | None = None,
    searcher=None,
    reader=None,
    composer=None,
) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    seen_path = Path(seen_path) if seen_path else Path("state/seen_articles.json")
    last_run_path = Path(last_run_path) if last_run_path else Path("state/last_briefing.json")
    recent_path = Path(recent_path) if recent_path else seen_path.parent / "recent_briefings.json"

    if since_override is not None:
        since = since_override
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        since = since.astimezone(timezone.utc)
    else:
        since = window_start(
            load_last_run(last_run_path),
            now,
            int(config["lookback_fallback_hours"]),
            int(config["max_lookback_hours"]),
        )
    print(f"Briefing window starts {since.isoformat()}", file=sys.stderr)

    seen = _normalize_seen(load_seen(seen_path))
    memory = recent_items(load_recent(recent_path))
    discovered = prepare_candidates(
        config,
        since,
        now,
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
        raise BriefingError(
            "Too many sources failed to trust a quiet note. Not posting."
        )

    fresh = discovered.fresh
    unique = discovered.pool
    limit = int(config.get("max_items", 6))
    lede = ""
    if config.get("llm_rank"):
        selected = list(discovered.shortlist)
        if not config.get("read_full_text"):
            enricher(selected)
        rank = composer or compose_briefing
        try:
            lede, pairs = rank(
                selected,
                api_key,
                config["together_model"],
                strict_auth=not dry_run,
                recent=memory,
                limit=limit,
            )
        except AuthError as exc:
            raise BriefingError(
                "Together API rejected the key or model name. Not posting."
            ) from exc
        selected = [row[0] for row in pairs]
        items = []
        for row in pairs:
            article, headline, body, why = _story_fields(row)
            items.append(BriefingItem(
                what_happened=headline,
                why_it_matters=why,
                url=article.url,
                source_name=article.source_name,
                body=body,
            ))
    else:
        selected = select_items(
            unique,
            limit=limit,
            material_score=int(config.get("material_score", 6)),
            paper_min_score=int(config.get("paper_min_score", 9)),
            max_per_source=int(config.get("max_per_source_in_briefing", 2)),
            max_per_signature=int(config.get("max_per_signature", 1)),
        )
        if selected:
            enricher(selected)
            try:
                copies = write_copies(
                    selected,
                    api_key,
                    config["together_model"],
                    strict_auth=not dry_run,
                )
            except AuthError as exc:
                raise BriefingError(
                    "Together API rejected the key or model name. Not posting."
                ) from exc
            items = [
                BriefingItem(
                    what_happened=what,
                    why_it_matters=why,
                    url=article.url,
                    source_name=article.source_name,
                )
                for article, (what, why) in zip(selected, copies)
            ]
            if config.get("theme_lede"):
                lede = theme_lede(selected)
        else:
            items = []

    print(
        f"Fetched feeds {discovered.feed_count}, exa {discovered.exa_count}, "
        f"unseen {len(fresh)}, deduped {len(unique)}, selected {len(selected)}",
        file=sys.stderr,
    )
    for article in selected:
        print(
            f"  {score_article(article):>2}  {article.source_name}: {article.title}",
            file=sys.stderr,
        )
    if not selected:
        print("Nothing material in the window. Quiet note.", file=sys.stderr)
    if expect_headlines:
        print(coverage_report(expect_headlines, unique, selected), file=sys.stderr)

    text = render_briefing(now, since, items, lede=lede)
    if dry_run:
        print("Dry run: not posting to Slack.", file=sys.stderr)
        return text

    if not api_key:
        raise BriefingError("TOGETHER_API_KEY is not set.", text=text)
    if not webhook_url:
        raise BriefingError("SLACK_WEBHOOK_URL is not set.", text=text)

    poster(slack_payload(text), webhook_url)
    updated = seen
    for article in fresh:
        updated = mark_seen(updated, canonical_url(article.url))
    updated = prune_old(updated, retention_days=int(config["state_retention_days"]))
    save_seen(updated, seen_path)
    save_last_run(now, last_run_path)
    remember_briefing(now, selected, recent_path)
    return text
