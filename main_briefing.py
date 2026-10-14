"""
Mon/Wed/Fri AI briefing.

Prints the message. Posts to Slack only when DRY_RUN is off and both
TOGETHER_API_KEY and SLACK_WEBHOOK_URL are set. A dry run never posts.
"""
import os
import sys

import yaml

from src.briefing import BriefingError, run_briefing


def dry_run_enabled(argv=None) -> bool:
    argv = sys.argv if argv is None else argv
    flag = os.environ.get("DRY_RUN", "").strip().lower()
    return flag in {"1", "true", "yes"} or "--dry-run" in argv


def main(argv=None) -> None:
    config = yaml.safe_load(open("config.yaml"))
    try:
        text = run_briefing(
            config,
            dry_run=dry_run_enabled(argv),
            api_key=os.environ.get("TOGETHER_API_KEY") or None,
            webhook_url=os.environ.get("SLACK_WEBHOOK_URL") or None,
        )
    except BriefingError as exc:
        if exc.text:
            print(exc.text, end="" if exc.text.endswith("\n") else "\n")
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(text, end="" if text.endswith("\n") else "\n")


if __name__ == "__main__":
    main()
