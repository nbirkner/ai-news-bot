import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.dedupe import canonical_url, dedupe_articles
from src.fetch import enrich_previews, fetch_all
from src.models import BriefingItem
from src.rank import score_article, select_items
from src.slack import post_to_slack, render_briefing, slack_payload
from src.state import load_last_run, load_seen, mark_seen, prune_old, save_last_run, save_seen
from src.summarize import AuthError, write_copies


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
    fetcher=fetch_all,
    enricher=enrich_previews,
    poster=post_to_slack,
) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    seen_path = Path(seen_path) if seen_path else Path("state/seen_articles.json")
    last_run_path = Path(last_run_path) if last_run_path else Path("state/last_briefing.json")

    since = window_start(
        load_last_run(last_run_path),
        now,
        int(config["lookback_fallback_hours"]),
        int(config["max_lookback_hours"]),
    )
    print(f"Briefing window starts {since.isoformat()}", file=sys.stderr)

    report = fetcher(config, since, now)
    for failure in report.failures:
        print(f"Source failed: {failure}", file=sys.stderr)
    source_count = len(config.get("sources") or [])
    if report.successes == 0 or (
        not report.articles and report.successes * 2 < source_count
    ):
        raise BriefingError(
            "Too many sources failed to trust a quiet note. Not posting."
        )

    seen = _normalize_seen(load_seen(seen_path))
    fresh = [article for article in report.articles if canonical_url(article.url) not in seen]
    unique = dedupe_articles(fresh)
    selected = select_items(
        unique,
        limit=int(config.get("max_items", 6)),
        material_score=int(config.get("material_score", 6)),
        paper_min_score=int(config.get("paper_min_score", 9)),
        max_per_source=int(config.get("max_per_source_in_briefing", 2)),
        max_per_signature=int(config.get("max_per_signature", 1)),
    )
    print(
        f"Fetched {len(report.articles)}, unseen {len(fresh)}, "
        f"deduped {len(unique)}, selected {len(selected)}",
        file=sys.stderr,
    )
    for article in selected:
        print(
            f"  {score_article(article):>2}  {article.source_name}: {article.title}",
            file=sys.stderr,
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
    else:
        items = []
        print("Nothing material in the window. Quiet note.", file=sys.stderr)

    text = render_briefing(now, since, items)
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
    return text
