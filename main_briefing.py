"""
Mon/Wed/Fri AI briefing.

Prints the message. A live run posts to Slack, commits an MDX issue to the
site archive, and sends the Buttondown email when those credentials are set.
BUTTONDOWN_API_KEY and SITE_GITHUB_TOKEN are optional: a missing one skips
that channel and does not fail the run. A dry run never publishes.

Dry-run window override (ignored on a live post):
  --since 2026-10-16T15:00:00Z
  LOOKBACK=80h

Dry-run coverage check:
  --expect tests/fixtures/window_2026_10_19_expected.txt
  --expect-headline "DeepSeek V4.1-Flash-1017"
"""
import os
import sys

import yaml

from src.briefing import BriefingError, parse_since_value, run_briefing
from src.expect import load_expect_file


def dry_run_enabled(argv=None) -> bool:
    argv = sys.argv if argv is None else argv
    flag = os.environ.get("DRY_RUN", "").strip().lower()
    return flag in {"1", "true", "yes"} or "--dry-run" in argv


def since_from_args(argv, environ=None, now=None):
    from datetime import datetime, timezone
    environ = os.environ if environ is None else environ
    raw = ""
    if "--since" in argv:
        index = argv.index("--since")
        if index + 1 >= len(argv):
            raise SystemExit("--since needs an ISO timestamp or a window like 80h")
        raw = argv[index + 1]
    elif (environ.get("LOOKBACK") or "").strip():
        raw = environ["LOOKBACK"].strip()
    if not raw:
        return None
    moment = now or datetime.now(timezone.utc)
    try:
        return parse_since_value(raw, moment)
    except ValueError as exc:
        raise SystemExit(f"Could not parse --since / LOOKBACK {raw!r}: {exc}") from exc


def expect_from_args(argv) -> list:
    lines = []
    if "--expect" in argv:
        index = argv.index("--expect")
        if index + 1 >= len(argv):
            raise SystemExit("--expect needs a file path")
        lines.extend(load_expect_file(argv[index + 1]))
    index = 0
    while index < len(argv):
        if argv[index] == "--expect-headline" and index + 1 < len(argv):
            lines.append(argv[index + 1])
            index += 2
            continue
        index += 1
    return lines


def main(argv=None) -> None:
    argv = sys.argv if argv is None else argv
    config = yaml.safe_load(open("config.yaml"))
    override = (os.environ.get("TOGETHER_MODEL") or "").strip()
    if override:
        config["together_model"] = override
    dry_run = dry_run_enabled(argv)
    since = since_from_args(argv) if dry_run else None
    if not dry_run and ("--since" in argv or (os.environ.get("LOOKBACK") or "").strip()):
        print("Ignoring --since / LOOKBACK because this is not a dry run.", file=sys.stderr)
    try:
        text = run_briefing(
            config,
            dry_run=dry_run,
            api_key=os.environ.get("TOGETHER_API_KEY") or None,
            webhook_url=os.environ.get("SLACK_WEBHOOK_URL") or None,
            exa_api_key=os.environ.get("EXA_API_KEY") or None,
            since_override=since,
            expect_headlines=expect_from_args(argv) or None,
            artifact_dir=os.environ.get("ARTIFACT_DIR") if dry_run else None,
        )
    except BriefingError as exc:
        if exc.text:
            print(exc.text, end="" if exc.text.endswith("\n") else "\n")
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(text, end="" if text.endswith("\n") else "\n")


if __name__ == "__main__":
    main()
