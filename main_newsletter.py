"""
Write one public AI briefing as site MDX and email HTML.

A dry run never posts to Slack, never calls Resend, and never commits to the
site repo. `--send` broadcasts the issue through Resend. It does not post to
Slack and it does not publish the site.

    python main_newsletter.py --dry-run --window-end 2026-10-19
    python main_newsletter.py --send --window-end 2026-10-19
    python main_newsletter.py --mode lesson --dry-run --date 2026-10-20
    python main_newsletter.py --mode lesson --send --date 2026-10-20

`--mode lesson` reads one Learning-bot lesson file and writes site MDX and
the lesson email. `--send` broadcasts that email to RESEND_LESSONS_AUDIENCE_ID.
It does not read RESEND_AUDIENCE_ID as the destination. A missing lessons
audience, or the AI News audience, fails closed and does not call Resend.
The default mode is still the Monday news issue.

Reads TOGETHER_API_KEY (a personal key, not a company key) and EXA_API_KEY
from the environment. A Monday send also reads RESEND_API_KEY and
RESEND_AUDIENCE_ID. A Tuesday lesson send reads RESEND_API_KEY and
RESEND_LESSONS_AUDIENCE_ID. Missing model keys skip that source and still write ./out/.
The Monday run looks for sources/<YYYY-MM-DD>.json (the window end on the
Pacific calendar) and uses it plus RSS. A missing or invalid file falls back
to RSS only. Exa runs only when EXA_ENABLED=1, capped by EXA_MAX_CALLS
(default 6).
A date window end is 15:00 UTC on that day. Without --window-start the window
opens at the previous Monday, Wednesday, or Friday slot. The Monday workflow
passes the previous Monday as the start.
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.dedupe import canonical_url
from src.issue_run import generate_issue, parse_window_end, previous_mwf_slot
from src.lesson_file import LessonBlocked, lesson_for_date, pacific_today, render_lesson_mdx, week_lesson
from src.newsletter import (
    email_subject,
    lesson_email_subject,
    render_email,
    render_lesson_email,
    render_lesson_plain,
    render_mdx,
    render_plain,
)
from src.resend_send import (
    SendError,
    assert_lessons_audience,
    create_broadcast,
    lessons_from_address,
    send_broadcast,
    send_test_email,
)
from src.state import (
    issue_already_sent,
    load_seen,
    mark_issue_sent,
    mark_seen,
    prune_old,
    remember_issue,
    save_last_run,
    save_seen,
)
from src.usage import format_usage, reset_usage


def _flag(argv, name: str) -> str:
    if name not in argv:
        return ""
    index = argv.index(name)
    if index + 1 >= len(argv):
        raise SystemExit(f"{name} needs a value")
    return argv[index + 1]


def _truthy(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes"}


def send_enabled(argv) -> bool:
    """A send happens only with --send, and never when a dry run is also requested."""
    if "--dry-run" in argv or _truthy(os.environ.get("DRY_RUN", "")):
        return False
    return "--send" in argv


def test_recipient(argv) -> str:
    flagged = _flag(argv, "--test-to").strip()
    if flagged:
        return flagged
    return (os.environ.get("TEST_TO") or "").strip()


def _state_dir() -> Path:
    return Path(os.environ.get("STATE_DIR") or "state")


def _note_sent() -> None:
    path = (os.environ.get("GITHUB_OUTPUT") or "").strip()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("sent=true\n")


def _valid_email(address: str) -> bool:
    text = (address or "").strip()
    return "@" in text and " " not in text and not text.startswith("@") and not text.endswith("@")


def record_successful_send(issue, state_dir: Path, *, broadcast_id: str, retention_days: int) -> None:
    """Persist the issue date and the article memory. Only after Resend accepts the send."""
    now = datetime.now(timezone.utc)
    mark_issue_sent(
        issue.date,
        state_dir / "sent_issues.json",
        broadcast_id=broadcast_id,
        when=now,
    )
    seen_path = state_dir / "seen_articles.json"
    seen = load_seen(seen_path)
    for item in issue.items:
        if item.url:
            seen = mark_seen(seen, canonical_url(item.url))
    save_seen(prune_old(seen, retention_days=retention_days), seen_path)
    save_last_run(issue.until or now, state_dir / "last_briefing.json")
    remember_issue(issue, state_dir / "recent_briefings.json", when=now)


def run_lesson(argv) -> int:
    """Write one lesson as site MDX and email. Broadcast only to its own audience."""
    if "--post" in argv or "--publish" in argv:
        print("Lesson mode does not post or publish.", file=sys.stderr)
        return 2
    raw_date = _flag(argv, "--date")
    if raw_date:
        try:
            datetime.strptime(raw_date, "%Y-%m-%d")
        except ValueError:
            print("--date must be YYYY-MM-DD", file=sys.stderr)
            return 2
        date = raw_date
    else:
        date = pacific_today()
    sending = send_enabled(argv)
    if not sending and (test_recipient(argv) or "--test-to" in argv):
        print("Dry run: not sending the test email.", file=sys.stderr)
    out = Path(os.environ.get("OUT_DIR") or "out")
    out.mkdir(parents=True, exist_ok=True)
    try:
        lesson = lesson_for_date(date)
    except LessonBlocked as exc:
        blocked = out / f"{date}.blocked.txt"
        blocked.write_text(exc.line + "\n", encoding="utf-8")
        print(exc.line)
        return 2
    stem = f"{lesson.date}-{lesson.slug}"
    (out / f"{stem}.mdx").write_text(render_lesson_mdx(lesson), encoding="utf-8")
    (out / f"{stem}.html").write_text(render_lesson_email(lesson, broadcast=True), encoding="utf-8")
    (out / f"{stem}.txt").write_text(render_lesson_plain(lesson, broadcast=True), encoding="utf-8")
    print(f"Wrote {out / (stem + '.mdx')}", file=sys.stderr)
    print(f"Wrote {out / (stem + '.html')}", file=sys.stderr)
    print(f"Wrote {out / (stem + '.txt')}", file=sys.stderr)
    if not sending:
        print("Dry run: nothing was posted or sent.", file=sys.stderr)
        return 0
    recipient = test_recipient(argv)
    api_key = (os.environ.get("RESEND_API_KEY") or "").strip()
    if not api_key:
        print("RESEND_API_KEY is not set. Not sending.", file=sys.stderr)
        return 2
    if recipient and not _valid_email(recipient):
        print("--test-to needs one email address.", file=sys.stderr)
        return 2
    try:
        sender = lessons_from_address(os.environ.get("RESEND_LESSONS_FROM") or "")
    except SendError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    subject = lesson_email_subject(lesson)
    if not subject:
        print("The lesson has no subject. Not sending.", file=sys.stderr)
        return 2
    state_dir = _state_dir()
    sent_path = state_dir / "sent_lessons.json"
    if not recipient:
        try:
            audience_id = assert_lessons_audience(
                os.environ.get("RESEND_LESSONS_AUDIENCE_ID") or "",
                news_audience_id=os.environ.get("RESEND_AUDIENCE_ID") or "",
            )
        except SendError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if issue_already_sent(lesson.date, sent_path):
            print(f"Already sent {lesson.date}. Not sending again.", file=sys.stderr)
            return 0
    try:
        if recipient:
            send_test_email(
                api_key=api_key,
                to=recipient,
                subject=subject,
                html=render_lesson_email(lesson, broadcast=False),
                text=render_lesson_plain(lesson, broadcast=False),
                from_address=sender,
            )
            print(f"Sent a test email to {recipient}. The broadcast was not sent.", file=sys.stderr)
            return 0
        broadcast_id = create_broadcast(
            api_key=api_key,
            audience_id=audience_id,
            subject=subject,
            html=render_lesson_email(lesson, broadcast=True),
            text=render_lesson_plain(lesson, broadcast=True),
            from_address=sender,
        )
        send_broadcast(api_key=api_key, broadcast_id=broadcast_id)
    except SendError as exc:
        print(str(exc), file=sys.stderr)
        print("Not recording the lesson as sent.", file=sys.stderr)
        return 2
    mark_issue_sent(
        lesson.date,
        sent_path,
        broadcast_id=broadcast_id,
        when=datetime.now(timezone.utc),
    )
    _note_sent()
    print(f"Sent {lesson.date} as broadcast {broadcast_id}.", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    mode = (_flag(argv, "--mode") or "news").strip().lower()
    if mode == "lesson":
        return run_lesson(argv)
    if mode != "news":
        print("--mode must be news or lesson.", file=sys.stderr)
        return 2
    if "--post" in argv:
        print("main_newsletter.py does not post to Slack.", file=sys.stderr)
        return 2
    raw_end = _flag(argv, "--window-end")
    if not raw_end:
        print(
            "Usage: python main_newsletter.py --dry-run "
            "--window-end 2026-10-19 [--window-start 2026-10-12T15:00:00Z]\n"
            "       python main_newsletter.py --send --window-end 2026-10-19",
            file=sys.stderr,
        )
        return 2
    try:
        until = parse_window_end(raw_end)
    except ValueError as exc:
        print(f"Could not parse --window-end: {exc}", file=sys.stderr)
        return 2
    raw_start = _flag(argv, "--window-start")
    if raw_start:
        try:
            since = parse_window_end(raw_start)
        except ValueError as exc:
            print(f"Could not parse --window-start: {exc}", file=sys.stderr)
            return 2
        if since >= until:
            print("--window-start must be earlier than --window-end", file=sys.stderr)
            return 2
    else:
        since = previous_mwf_slot(until)

    sending = send_enabled(argv)
    recipient = test_recipient(argv) if sending else ""
    if not sending and (test_recipient(argv) or "--test-to" in argv):
        print("Dry run: not sending the test email.", file=sys.stderr)
    api_key = (os.environ.get("RESEND_API_KEY") or "").strip()
    audience_id = (os.environ.get("RESEND_AUDIENCE_ID") or "").strip()
    state_dir = _state_dir()
    sent_path = state_dir / "sent_issues.json"
    if sending and not recipient and issue_already_sent(until.strftime("%Y-%m-%d"), sent_path):
        print(f"Already sent {until.strftime('%Y-%m-%d')}. Not sending again.", file=sys.stderr)
        return 0
    if sending and recipient and not _valid_email(recipient):
        print("--test-to needs one email address.", file=sys.stderr)
        return 2
    if sending and not api_key:
        print("RESEND_API_KEY is not set. Not sending.", file=sys.stderr)
        return 2
    if sending and not recipient and not audience_id:
        print("RESEND_AUDIENCE_ID is not set. Not sending.", file=sys.stderr)
        return 2

    config = yaml.safe_load(open("config.yaml"))
    override = (os.environ.get("TOGETHER_MODEL") or "").strip()
    if override:
        config["together_model"] = override

    reset_usage()
    issue, failures, discovered = generate_issue(
        config,
        since=since,
        until=until,
        api_key=(os.environ.get("TOGETHER_API_KEY") or "").strip() or None,
        exa_api_key=(os.environ.get("EXA_API_KEY") or "").strip() or None,
        seen_path=state_dir / "seen_articles.json",
        recent_path=state_dir / "recent_briefings.json",
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
    if not issue.items and not any(reason in {"too short", "no stories"} for reason in failures):
        failures.append("no stories")
    if failures or not issue.items:
        (out / f"{issue.date}.blocked.txt").write_text("\n".join(failures) + "\n")
        print("Blocked. Not sending.", file=sys.stderr)
        for reason in failures:
            print(f"  {reason}", file=sys.stderr)
        return 2
    lesson = week_lesson(issue.date)
    (out / f"{issue.date}.mdx").write_text(render_mdx(issue, lesson=lesson))
    (out / f"{issue.date}.html").write_text(render_email(issue, lesson=lesson))
    (out / f"{issue.date}.txt").write_text(render_plain(issue, lesson=lesson))
    print(f"Wrote {out / (issue.date + '.mdx')}", file=sys.stderr)
    print(f"Wrote {out / (issue.date + '.html')}", file=sys.stderr)
    print(f"Wrote {out / (issue.date + '.txt')}", file=sys.stderr)
    if not sending:
        print("Dry run: nothing was posted or sent.", file=sys.stderr)
        return 0
    if issue_already_sent(issue.date, sent_path):
        print(f"Already sent {issue.date}. Not sending again.", file=sys.stderr)
        return 0

    subject = email_subject(issue)
    if not subject:
        print("The issue has no subject. Not sending.", file=sys.stderr)
        return 2
    try:
        if recipient:
            send_test_email(
                api_key=api_key,
                to=recipient,
                subject=subject,
                html=render_email(issue, broadcast=False, lesson=lesson),
                text=render_plain(issue, broadcast=False, lesson=lesson),
            )
            print(f"Sent a test email to {recipient}. The broadcast was not sent.", file=sys.stderr)
            return 0
        broadcast_id = create_broadcast(
            api_key=api_key,
            audience_id=audience_id,
            subject=subject,
            html=render_email(issue, broadcast=True, lesson=lesson),
            text=render_plain(issue, broadcast=True, lesson=lesson),
        )
        send_broadcast(api_key=api_key, broadcast_id=broadcast_id)
    except SendError as exc:
        print(str(exc), file=sys.stderr)
        print("Not recording the issue as sent.", file=sys.stderr)
        return 2

    record_successful_send(
        issue,
        state_dir,
        broadcast_id=broadcast_id,
        retention_days=int(config.get("state_retention_days") or 21),
    )
    _note_sent()
    print(f"Sent {issue.date} as broadcast {broadcast_id}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
