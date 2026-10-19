"""Discovery dry run. No Together calls. No Slack calls.

Reads public feeds and, when EXA_API_KEY is set, Exa. Fetches full text for
the shortlist and prints the candidate list the ranker would see.

From the repo root:

  python3 scripts/discovery_dry_run.py --since 2026-10-16T15:00:00Z
  python3 scripts/discovery_dry_run.py --since 80h --expect tests/fixtures/window_2026_10_19_expected.txt

LOOKBACK works the same way as --since. EXA_MAX_CALLS caps Exa calls (default 30).
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.discover import prepare_candidates  # noqa: E402
from src.expect import coverage_report, load_expect_file  # noqa: E402
from src.state import load_last_run, load_recent, load_seen, recent_items  # noqa: E402
from src.briefing import parse_since_value, window_start  # noqa: E402
from src.dedupe import canonical_url  # noqa: E402


def _since(argv, now):
    raw = ""
    if "--since" in argv:
        raw = argv[argv.index("--since") + 1]
    elif (os.environ.get("LOOKBACK") or "").strip():
        raw = os.environ["LOOKBACK"].strip()
    if not raw:
        return None
    return parse_since_value(raw, now)


def _expect(argv):
    lines = []
    if "--expect" in argv:
        lines.extend(load_expect_file(argv[argv.index("--expect") + 1]))
    return lines


def _print_candidate(index, article):
    published = article.published_at.isoformat()
    print(f"{index}. [{article.source_kind}] {article.source_name}: {article.title}")
    print(f"   {article.url}")
    print(f"   published {published}")
    if article.details:
        print(f"   details: {article.details}")
    text = (article.full_text or article.content_preview or "").strip()
    excerpt = text[:500]
    if excerpt:
        print(f"   text ({len(text)} chars): {excerpt}")
    else:
        print("   text: (empty)")
    print()


def main(argv=None) -> int:
    argv = sys.argv if argv is None else argv
    os.chdir(ROOT)
    config = yaml.safe_load(open(ROOT / "config.yaml"))
    # This script always reads full text. It never ranks with a model.
    config["read_full_text"] = True
    config["llm_rank"] = False
    now = datetime.now(timezone.utc)
    since = _since(argv, now)
    if since is None:
        since = window_start(
            load_last_run(ROOT / "state/last_briefing.json"),
            now,
            int(config["lookback_fallback_hours"]),
            int(config["max_lookback_hours"]),
        )
    print(f"Discovery window starts {since.isoformat()}", file=sys.stderr)
    seen = load_seen(ROOT / "state/seen_articles.json")
    normalized = {canonical_url(url): stamp for url, stamp in seen.items()}
    memory = recent_items(load_recent(ROOT / "state/recent_briefings.json"))
    discovered = prepare_candidates(
        config,
        since,
        now,
        exa_api_key=os.environ.get("EXA_API_KEY") or None,
        seen=normalized,
        recent_items=memory,
    )
    print(f"Exa calls: {discovered.exa_calls}/{discovered.exa_cap}")
    if discovered.exa_note:
        print(f"Exa note: {discovered.exa_note}")
    print(f"Feed items in window: {discovered.feed_count}")
    print(f"Exa items: {discovered.exa_count}")
    print(f"Candidates after dedupe: {len(discovered.pool)}")
    print(f"Shortlist (ranking inputs): {len(discovered.shortlist)}")
    print()
    if not discovered.shortlist:
        print("No candidates.")
    for index, article in enumerate(discovered.shortlist, start=1):
        _print_candidate(index, article)
    expected = _expect(argv)
    if expected:
        # "ranked" here means the headline is in the shortlist the model would see.
        print(coverage_report(expected, discovered.pool, discovered.shortlist))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
