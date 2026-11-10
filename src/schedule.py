"""Whether a scheduled Actions run should still send the Monday issue.

GitHub's UTC cron does not start at 9:00 America/Los_Angeles. The scheduled
runs on 2026-10-26, 2026-11-02, and 2026-11-09 started four to eight hours
late. The 09:00-12:00 window marked those jobs successful without sending.
A late start still sends for the rest of that Monday and through Tuesday
12:59. After that the job fails, so a missed issue is not a green run.

This module does not send mail and does not call the network.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class ScheduleDecision:
    proceed: bool
    exit_code: int
    window_end: str
    reason: str


def decide(moment: datetime, event_name: str) -> ScheduleDecision:
    """Return the send decision for one workflow start.

    `event_name` is `schedule` for the cron and anything else for a manual
    run. Manual runs always proceed. The window end is the Pacific Monday
    the issue belongs to, or empty when this start should not send.
    """
    name = (event_name or "").strip()
    if name != "schedule":
        label = name or "local"
        return ScheduleDecision(
            True,
            0,
            "",
            f"manual {label}; the Monday recovery window applies to schedule only",
        )
    local = moment.astimezone(PACIFIC)
    weekday = local.weekday()
    hour = local.hour
    clock = local.strftime("%Y-%m-%d %H:%M %Z")
    if weekday == 0 and hour < 9:
        return ScheduleDecision(
            False,
            0,
            "",
            f"{clock} is Monday before 09:00 America/Los_Angeles. "
            "The 09:00 cron still owns this send.",
        )
    if weekday == 0:
        end = local.date().isoformat()
        return ScheduleDecision(
            True,
            0,
            end,
            f"{clock} is Monday at or after 09:00 America/Los_Angeles. "
            f"Sending the {end} issue.",
        )
    if weekday == 1 and hour <= 12:
        end = (local.date() - timedelta(days=1)).isoformat()
        return ScheduleDecision(
            True,
            0,
            end,
            f"{clock} is a delayed Monday cron. Sending the Monday {end} issue once.",
        )
    return ScheduleDecision(
        False,
        1,
        "",
        f"{clock} missed the Monday send. "
        "The recovery window is Monday 09:00 through Tuesday 12:59 America/Los_Angeles. "
        "This run is a failure.",
    )
