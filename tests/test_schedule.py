"""Monday catch-up decisions. No network and no email."""
from datetime import datetime, timezone

from src.schedule import decide


def _at(stamp: str, *, scheduled: bool = True, sent: bool = False):
    when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)
    return decide(when, scheduled=scheduled, sent=sent)


def test_nov9_late_runs_still_send():
    # 2026-11-09 15:58 and 16:30 Pacific. Both were green skips under the 09-12 window.
    first = _at("2026-11-09T23:58:09Z")
    second = _at("2026-11-10T00:30:39Z")
    assert first.action == "send"
    assert second.action == "send"
    assert first.issue_date == "2026-11-09"
    assert second.issue_date == "2026-11-09"


def test_eleven_am_pacific_would_send_if_a_runner_had_started():
    decision = _at("2026-11-09T19:00:00Z")
    assert decision.action == "send"
    assert decision.issue_date == "2026-11-09"


def test_eight_am_pacific_standard_time_waits():
    decision = _at("2026-11-09T16:00:00Z")
    assert decision.action == "skip"
    assert "before 09:00" in decision.reason


def test_nine_am_pacific_sends_in_both_seasons():
    assert _at("2026-11-09T17:00:00Z").action == "send"
    assert _at("2026-10-26T16:00:00Z").action == "send"


def test_october_26_afternoon_is_inside_the_catch_up_window():
    # 13:54 PDT. The old guard required hour 9 and skipped this run.
    decision = _at("2026-10-26T20:54:30Z")
    assert decision.action == "send"
    assert decision.issue_date == "2026-10-26"


def test_after_monday_without_a_send_fails():
    # 00:30 PST Tuesday.
    decision = _at("2026-11-10T08:30:00Z")
    assert decision.action == "fail"
    assert decision.issue_date == "2026-11-09"
    assert "was not sent" in decision.reason


def test_after_monday_with_a_send_does_not_fail_or_send_again():
    decision = _at("2026-11-10T08:30:00Z", sent=True)
    assert decision.action == "skip"
    assert "Already sent 2026-11-09" in decision.reason


def test_daylight_and_standard_time_boundaries():
    # Second Monday of March 2026 is after the spring-forward on March 8.
    assert _at("2026-03-09T16:00:00Z").action == "send"  # 09:00 PDT
    assert _at("2026-03-10T06:00:00Z").action == "send"  # 23:00 PDT Monday
    assert _at("2026-03-10T07:00:00Z").action == "fail"  # 00:00 PDT Tuesday
    # November 2026 is standard time. 16:00 UTC is 08:00, before the send.
    assert _at("2026-11-02T16:00:00Z").action == "skip"
    assert _at("2026-11-02T17:00:00Z").action == "send"  # 09:00 PST
    assert _at("2026-11-03T07:00:00Z").action == "send"  # 23:00 PST Monday
    assert _at("2026-11-03T08:30:00Z").action == "fail"  # 00:30 PST Tuesday
    # Midsummer Monday, daylight time.
    assert _at("2026-07-13T16:00:00Z").action == "send"  # 09:00 PDT
    assert _at("2026-07-14T07:00:00Z").action == "fail"  # 00:00 PDT Tuesday


def test_a_run_delayed_until_the_next_monday_does_not_fail_the_missed_week():
    """GitHub can delay a cron until the following Monday.

    That clock is a new issue date, not an alert for the week that was missed.
    """
    decision = _at("2026-11-16T17:00:00Z")
    assert decision.action == "send"
    assert decision.issue_date == "2026-11-16"


def test_a_manual_run_is_not_bound_by_the_monday_window():
    decision = _at("2026-11-10T18:00:00Z", scheduled=False)
    assert decision.action == "send"


def test_guard_script_writes_the_nov9_decision(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_NAME", "schedule")
    monkeypatch.setenv("SCHEDULE_NOW", "2026-11-09T23:58:09Z")
    monkeypatch.setenv("SENT_PATH", str(tmp_path / "missing.json"))
    output = tmp_path / "output.txt"
    output.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    summary = tmp_path / "summary.md"
    summary.write_text("")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    from scripts.schedule_guard import main
    assert main() == 0
    text = output.read_text()
    assert "action=send" in text
    assert "issue_date=2026-11-09" in text
    assert "send" in summary.read_text()
