"""Monday send window for the GitHub Actions schedule.

GitHub has started the 16:00 and 17:00 UTC crons five to seven hours late
(verified 26 Oct, 2 Nov, and 9 Nov 2026). A run that is still on Monday in
America/Los_Angeles, from 09:00 through 23:59, may send only after
``send_from``. A later run reports every expected Monday from that date
through the current week that is still unsent.

The crons do not guarantee a 9:00 start. GitHub can delay or drop every run.
``send_from`` empty means a scheduled run does not send and does not alarm,
including for weeks that already passed before this schedule was armed.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
SEND_START_HOUR = 9


@dataclass(frozen=True)
class Decision:
    action: str  # "send", "skip", or "fail"
    reason: str
    issue_date: str
    missed: tuple = ()


def as_utc(when: datetime) -> datetime:
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def target_issue_date(when: datetime) -> str:
    """Pacific Monday of the week that contains `when`."""
    pacific = as_utc(when).astimezone(PACIFIC)
    monday = pacific.date() - timedelta(days=pacific.weekday())
    return monday.isoformat()


def load_send_from(path) -> date | None:
    """Read ``send_from`` from schedule.yaml without requiring PyYAML.

    The guard runs before pip install, so this stays on the standard library.
    """
    file_path = path if hasattr(path, "read_text") else __import__("pathlib").Path(path)
    if not file_path.exists():
        return None
    for line in file_path.read_text(encoding="utf-8").splitlines():
        body = line.split("#", 1)[0].strip()
        if not body.startswith("send_from:"):
            continue
        return parse_send_from(body.split(":", 1)[1].strip().strip("'\""))
    return None


def parse_send_from(value: str | None) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    return date.fromisoformat(raw)


def first_expected_monday(send_from: date) -> date:
    """The first Monday on or after the activation date."""
    return send_from + timedelta(days=(0 - send_from.weekday()) % 7)


def expected_mondays(send_from: date | None, through: date) -> list[str]:
    """Monday issue dates from activation through `through`, inclusive.

    Dates before activation are not expected, so old missed weeks do not alarm.
    """
    if send_from is None or through < first_expected_monday(send_from):
        return []
    day = first_expected_monday(send_from)
    found = []
    while day <= through:
        found.append(day.isoformat())
        day += timedelta(days=7)
    return found


def manual_send_allowed(when: datetime, issue_date: str, *, allow_off_monday: bool) -> tuple[bool, str]:
    """A manual audience send has to name a Monday, and it has to be Monday locally.

    ``allow_off_monday`` is the explicit override for a catch-up the owner asked
    for. A weekend dispatch without it does not send.
    """
    try:
        day = date.fromisoformat((issue_date or "").strip())
    except ValueError:
        return False, "The issue date is not YYYY-MM-DD. Not sending."
    if day.weekday() != 0:
        return False, f"{issue_date} is not a Monday. Not sending."
    pacific = as_utc(when).astimezone(PACIFIC)
    if pacific.weekday() != 0 and not allow_off_monday:
        return (
            False,
            f"Today is {pacific.strftime('%A')} in America/Los_Angeles. "
            "A manual send waits for Monday unless allow_off_monday is set.",
        )
    return True, ""


def decide(
    when: datetime,
    *,
    scheduled: bool,
    sent: bool,
    send_from: date | None = None,
    sent_dates: set | None = None,
) -> Decision:
    """What a workflow run should do at `when`.

    ``send_from`` None disarms scheduled sends and missed-week alarms.
    Manual runs still continue; ``manual_send_allowed`` is the weekend check.
    """
    issue_date = target_issue_date(when)
    recorded = set(sent_dates or ())
    if sent:
        recorded.add(issue_date)
    current = date.fromisoformat(issue_date)
    priors = tuple(
        day for day in expected_mondays(send_from, current)
        if day != issue_date and day not in recorded
    )
    if not scheduled:
        return Decision(
            "send",
            "Manual run. Scheduled send windows do not apply. Weekend sends are refused unless allow_off_monday is set.",
            issue_date,
            missed=priors,
        )
    if send_from is None:
        return Decision(
            "skip",
            "Scheduled sends are not activated (send_from is empty). Not sending and not alerting.",
            issue_date,
        )
    if current < first_expected_monday(send_from):
        return Decision(
            "skip",
            f"{issue_date} is before send_from {send_from.isoformat()}. Not sending and not alerting.",
            issue_date,
        )
    pacific = as_utc(when).astimezone(PACIFIC)
    in_window = pacific.weekday() == 0 and pacific.hour >= SEND_START_HOUR
    current_unsent = issue_date not in recorded
    if priors and in_window and current_unsent:
        return Decision(
            "send",
            (
                f"Monday {pacific.strftime('%H:%M')} America/Los_Angeles is inside the catch-up window. "
                f"Earlier unsent issues: {', '.join(priors)}."
            ),
            issue_date,
            missed=priors,
        )
    if priors and in_window:
        return Decision(
            "fail",
            f"Earlier Monday issues were not sent: {', '.join(priors)}.",
            issue_date,
            missed=priors,
        )
    if priors or (not in_window and pacific.weekday() != 0 and current_unsent):
        missed = priors + ((issue_date,) if current_unsent and pacific.weekday() != 0 else ())
        return Decision(
            "fail",
            f"Monday AI News was not sent: {', '.join(missed)}.",
            issue_date,
            missed=missed,
        )
    if in_window and current_unsent:
        return Decision(
            "send",
            (
                f"Monday {pacific.strftime('%H:%M')} America/Los_Angeles "
                "is inside the 09:00-23:59 catch-up window."
            ),
            issue_date,
        )
    if not current_unsent:
        return Decision(
            "skip",
            f"Already sent {issue_date}. Not sending again.",
            issue_date,
        )
    return Decision(
        "skip",
        (
            f"Monday {pacific.strftime('%H:%M')} America/Los_Angeles "
            "is before 09:00. Not sending."
        ),
        issue_date,
    )
