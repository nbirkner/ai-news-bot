"""Friday catch-up decisions. No network and no email."""
from datetime import datetime, timezone

from src.schedule import decide


def _at(stamp: str, *, scheduled: bool = True, sent: bool = False, send_from: str = "issue", sent_dates=None):
    from datetime import date
    from src.schedule import parse_send_from, target_issue_date
    when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)
    if send_from == "issue":
        armed = date.fromisoformat(target_issue_date(when))
    else:
        armed = parse_send_from(send_from)
    return decide(when, scheduled=scheduled, sent=sent, send_from=armed, sent_dates=sent_dates)


def test_nov13_late_runs_still_send():
    # 2026-11-13 15:58 and 16:30 Pacific. Both are inside the Friday catch-up window.
    first = _at("2026-11-13T23:58:09Z")
    second = _at("2026-11-14T00:30:39Z")
    assert first.action == "send"
    assert second.action == "send"
    assert first.issue_date == "2026-11-13"
    assert second.issue_date == "2026-11-13"


def test_eleven_am_pacific_would_send_if_a_runner_had_started():
    decision = _at("2026-11-13T19:00:00Z")
    assert decision.action == "send"
    assert decision.issue_date == "2026-11-13"


def test_eight_am_pacific_standard_time_waits():
    decision = _at("2026-11-13T16:00:00Z")
    assert decision.action == "skip"
    assert "before 09:00" in decision.reason


def test_nine_am_pacific_sends_in_both_seasons():
    assert _at("2026-11-13T17:00:00Z").action == "send"
    assert _at("2026-10-30T16:00:00Z").action == "send"


def test_october_30_afternoon_is_inside_the_catch_up_window():
    # 13:54 PDT on Friday. The old guard required hour 9 and skipped afternoon runs.
    decision = _at("2026-10-30T20:54:30Z")
    assert decision.action == "send"
    assert decision.issue_date == "2026-10-30"


def test_after_friday_without_a_send_fails():
    # 00:30 PST Saturday.
    decision = _at("2026-11-14T08:30:00Z")
    assert decision.action == "fail"
    assert decision.issue_date == "2026-11-13"
    assert "was not sent" in decision.reason


def test_after_friday_with_a_send_does_not_fail_or_send_again():
    decision = _at("2026-11-14T08:30:00Z", sent=True)
    assert decision.action == "skip"
    assert "Already sent 2026-11-13" in decision.reason


def test_daylight_and_standard_time_boundaries():
    # Friday 13 Mar 2026 is after the spring-forward on March 8.
    assert _at("2026-03-13T16:00:00Z").action == "send"  # 09:00 PDT
    assert _at("2026-03-14T06:00:00Z").action == "send"  # 23:00 PDT Friday
    assert _at("2026-03-14T07:00:00Z").action == "fail"  # 00:00 PDT Saturday
    # November 2026 is standard time. 16:00 UTC is 08:00, before the send.
    assert _at("2026-11-06T16:00:00Z").action == "skip"
    assert _at("2026-11-06T17:00:00Z").action == "send"  # 09:00 PST
    assert _at("2026-11-07T07:00:00Z").action == "send"  # 23:00 PST Friday
    assert _at("2026-11-07T08:30:00Z").action == "fail"  # 00:30 PST Saturday
    # Midsummer Friday, daylight time.
    assert _at("2026-07-17T16:00:00Z").action == "send"  # 09:00 PDT
    assert _at("2026-07-18T07:00:00Z").action == "fail"  # 00:00 PDT Saturday


def test_a_run_delayed_until_the_next_friday_does_not_fail_the_missed_week():
    """GitHub can delay a cron until the following Friday.

    With send_from set to that new Friday, the missed prior week is before
    activation and is not an alarm. The clock is the new issue.
    """
    decision = _at("2026-11-20T17:00:00Z", send_from="2026-11-20")
    assert decision.action == "send"
    assert decision.issue_date == "2026-11-20"
    assert decision.missed == ()


def test_the_following_friday_reports_an_unsent_armed_week_and_not_older_ones():
    later = _at("2026-11-27T17:00:00Z", send_from="2026-11-20", sent_dates=set())
    assert later.action == "send"
    assert later.issue_date == "2026-11-27"
    assert later.missed == ("2026-11-20",)
    assert "2026-11-13" not in later.missed
    assert "2026-10-30" not in later.missed


def test_monday_alarm_cron_still_names_the_friday_that_was_missed():
    # 08:30 UTC Monday is 00:30 PST Monday, the last alarm slot. The issue is last Friday.
    decision = _at("2026-11-16T08:30:00Z")
    assert decision.action == "fail"
    assert decision.issue_date == "2026-11-13"
    assert "Friday AI News was not sent" in decision.reason


def test_empty_send_from_does_not_send_or_alarm_on_friday_evening():
    # 2026-11-14 03:00 UTC is 19:00 Friday Nov 13 Pacific. A merge at that
    # hour must not send and must not alarm about weeks already missed.
    decision = _at("2026-11-14T03:00:00Z", send_from="")
    assert decision.action == "skip"
    assert decision.missed == ()
    assert "not activated" in decision.reason


def test_a_weekend_manual_send_is_refused_without_the_override():
    from src.schedule import manual_send_allowed
    when = datetime.fromisoformat("2026-11-14T18:00:00+00:00")
    allowed, reason = manual_send_allowed(when, "2026-11-13", allow_off_day=False)
    assert allowed is False
    assert "Saturday" in reason
    assert "Friday" in reason
    allowed, reason = manual_send_allowed(when, "2026-11-13", allow_off_day=True)
    assert allowed is True
    bad, reason = manual_send_allowed(when, "2026-11-11", allow_off_day=True)
    assert bad is False
    assert "not a Friday" in reason


def test_past_monday_dispatches_follow_the_monday_rule():
    """2026-10-26, 2026-11-02, and 2026-11-09 stay sendable.

    On a Monday they send without the override. On any other day they need
    allow_off_day. A Friday clock does not make a Monday window end legal.
    """
    from src.schedule import manual_send_allowed
    monday = datetime.fromisoformat("2026-11-09T18:00:00+00:00")
    friday = datetime.fromisoformat("2026-11-13T18:00:00+00:00")
    saturday = datetime.fromisoformat("2026-11-14T18:00:00+00:00")
    for issue in ("2026-10-26", "2026-11-02", "2026-11-09"):
        allowed, reason = manual_send_allowed(monday, issue, allow_off_day=False)
        assert allowed is True, reason
        refused, reason = manual_send_allowed(friday, issue, allow_off_day=False)
        assert refused is False
        assert "Monday" in reason
        refused, reason = manual_send_allowed(saturday, issue, allow_off_day=False)
        assert refused is False
        assert "Saturday" in reason
        allowed, reason = manual_send_allowed(saturday, issue, allow_off_day=True)
        assert allowed is True, reason


def test_a_manual_run_is_not_bound_by_the_friday_window():
    decision = _at("2026-11-14T18:00:00Z", scheduled=False)
    assert decision.action == "send"
    assert decision.issue_date == "2026-11-13"


def test_guard_script_writes_the_nov13_decision(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_NAME", "schedule")
    monkeypatch.setenv("SCHEDULE_SEND_FROM", "2026-11-13")
    monkeypatch.setenv("SCHEDULE_NOW", "2026-11-13T23:58:09Z")
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
    assert "issue_date=2026-11-13" in text
    assert "send" in summary.read_text()
