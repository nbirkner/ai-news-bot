"""Monday send window for the GitHub Actions schedule.

GitHub has started the 16:00 and 17:00 UTC crons five to seven hours late
(verified 26 Oct, 2 Nov, and 9 Nov 2026). A run that is still on Monday in
America/Los_Angeles, from 09:00 through 23:59, sends. A run after that Monday
fails when the issue was not sent, so the workflow is not a green skip.
A second run the same Monday does not send again.

The crons do not guarantee a 9:00 start. If GitHub delays every run until the
next Monday, this function sees that later Monday and does not fail the
missed week. The missed week then has no failed job.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
SEND_START_HOUR = 9


@dataclass(frozen=True)
class Decision:
    action: str  # "send", "skip", or "fail"
    reason: str
    issue_date: str


def as_utc(when: datetime) -> datetime:
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def target_issue_date(when: datetime) -> str:
    """Pacific Monday of the week that contains `when`."""
    pacific = as_utc(when).astimezone(PACIFIC)
    monday = pacific.date() - timedelta(days=pacific.weekday())
    return monday.isoformat()


def decide(when: datetime, *, scheduled: bool, sent: bool) -> Decision:
    """What a workflow run should do at `when`.

    Manual runs always continue. The newsletter command still refuses a
    second broadcast when the issue date is already recorded.
    """
    issue_date = target_issue_date(when)
    if sent:
        return Decision(
            "skip",
            f"Already sent {issue_date}. Not sending again.",
            issue_date,
        )
    if not scheduled:
        return Decision(
            "send",
            "Manual run. The Monday 09:00-23:59 America/Los_Angeles window applies to the schedule only.",
            issue_date,
        )
    pacific = as_utc(when).astimezone(PACIFIC)
    if pacific.weekday() == 0 and pacific.hour >= SEND_START_HOUR:
        return Decision(
            "send",
            (
                f"Monday {pacific.strftime('%H:%M')} America/Los_Angeles "
                "is inside the 09:00-23:59 catch-up window."
            ),
            issue_date,
        )
    if pacific.weekday() == 0:
        return Decision(
            "skip",
            (
                f"Monday {pacific.strftime('%H:%M')} America/Los_Angeles "
                "is before 09:00. Not sending."
            ),
            issue_date,
        )
    return Decision(
        "fail",
        (
            f"Monday AI News {issue_date} was not sent. This run is "
            f"{pacific.strftime('%A %H:%M')} America/Los_Angeles, after the Monday window."
        ),
        issue_date,
    )
