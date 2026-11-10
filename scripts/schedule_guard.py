"""Decide whether this Actions run sends, skips, or fails.

Writes `action`, `reason`, and `issue_date` to `$GITHUB_OUTPUT` when that
file is set. Exits 0. A missed Monday is `action=fail`; the workflow turns
that into a failed job so a skipped send is not a green check.

`SCHEDULE_NOW` (ISO-8601) overrides the clock for tests. Production leaves
it unset.
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.schedule import as_utc, decide, load_send_from, parse_send_from, target_issue_date
from src.state import issue_already_sent, load_sent_issues


def current_time() -> datetime:
    raw = (os.environ.get("SCHEDULE_NOW") or "").strip()
    if not raw:
        return datetime.now(timezone.utc)
    return as_utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))


def sent_dates() -> set[str]:
    path = Path((os.environ.get("SENT_PATH") or "state/sent_issues.json").strip())
    return set(load_sent_issues(path))


def send_from_date():
    override = os.environ.get("SCHEDULE_SEND_FROM")
    if override is not None:
        return parse_send_from(override)
    path = Path((os.environ.get("SCHEDULE_FILE") or "schedule.yaml").strip())
    return load_send_from(path)


def write_output(action: str, reason: str, issue_date: str, missed: tuple) -> None:
    path = (os.environ.get("GITHUB_OUTPUT") or "").strip()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"action={action}\n")
        handle.write(f"issue_date={issue_date}\n")
        handle.write(f"missed={','.join(missed)}\n")
        handle.write(f"reason={reason}\n")


def summarize(action: str, reason: str) -> None:
    path = (os.environ.get("GITHUB_STEP_SUMMARY") or "").strip()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"## AI News schedule\n\n**{action}.** {reason}\n")


def main() -> int:
    event = (os.environ.get("EVENT_NAME") or "").strip()
    when = current_time()
    issue_date = target_issue_date(when)
    recorded = sent_dates()
    decision = decide(
        when,
        scheduled=event == "schedule",
        sent=issue_already_sent(issue_date, Path((os.environ.get("SENT_PATH") or "state/sent_issues.json").strip())),
        send_from=send_from_date(),
        sent_dates=recorded,
    )
    write_output(decision.action, decision.reason, decision.issue_date, decision.missed)
    summarize(decision.action, decision.reason)
    if decision.action == "skip":
        print(f"::notice::{decision.reason}")
    else:
        print(decision.reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
