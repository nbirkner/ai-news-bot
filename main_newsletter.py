"""
Write one public AI briefing as site MDX and email HTML.

This command is a dry run. It never posts to Slack, never sends email,
and never commits to the site repo.

    python main_newsletter.py --dry-run --window-end 2026-10-19

Reads TOGETHER_API_KEY (a personal key, not a company key) and EXA_API_KEY
from the environment. Missing keys skip that source and still write ./out/.
A date window end is 15:00 UTC on that Monday, Wednesday, or Friday.
The window opens at the previous scheduled slot.
"""
import os
import sys
from pathlib import Path

import yaml

from src.issue_run import generate_issue, parse_window_end, previous_mwf_slot
from src.newsletter import render_email, render_mdx
from src.usage import format_usage, reset_usage


def _flag(argv, name: str) -> str:
    if name not in argv:
        return ""
    index = argv.index(name)
    if index + 1 >= len(argv):
        raise SystemExit(f"{name} needs a value")
    return argv[index + 1]


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--send" in argv or "--post" in argv:
        print("main_newsletter.py never sends or posts.", file=sys.stderr)
        return 2
    raw_end = _flag(argv, "--window-end")
    if not raw_end:
        print("Usage: python main_newsletter.py --dry-run --window-end 2026-10-19", file=sys.stderr)
        return 2
    try:
        until = parse_window_end(raw_end)
    except ValueError as exc:
        print(f"Could not parse --window-end: {exc}", file=sys.stderr)
        return 2
    since = previous_mwf_slot(until)

    config = yaml.safe_load(open("config.yaml"))
    override = (os.environ.get("TOGETHER_MODEL") or "").strip()
    if override:
        config["together_model"] = override

    reset_usage()
    # DRY_RUN is the only mode. The flags are accepted so the documented command works.
    os.environ["DRY_RUN"] = "1"
    issue, failures, discovered = generate_issue(
        config,
        since=since,
        until=until,
        api_key=(os.environ.get("TOGETHER_API_KEY") or "").strip() or None,
        exa_api_key=(os.environ.get("EXA_API_KEY") or "").strip() or None,
    )
    usage = format_usage(
        model=config.get("together_model") or "",
        exa_search_calls=getattr(discovered, "exa_search_calls", 0),
        exa_content_pages=getattr(discovered, "exa_content_pages", 0),
    )
    out = Path(os.environ.get("OUT_DIR") or "out")
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{issue.date}.usage.txt").write_text(usage)
    print(usage, end="")
    if failures:
        (out / f"{issue.date}.blocked.txt").write_text("\n".join(failures) + "\n")
        print("Blocked. Not writing MDX or HTML.", file=sys.stderr)
        for reason in failures:
            print(f"  {reason}", file=sys.stderr)
        return 2
    (out / f"{issue.date}.mdx").write_text(render_mdx(issue))
    (out / f"{issue.date}.html").write_text(render_email(issue))
    print(f"Wrote {out / (issue.date + '.mdx')}", file=sys.stderr)
    print(f"Wrote {out / (issue.date + '.html')}", file=sys.stderr)
    print("Dry run: nothing was posted or sent.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
