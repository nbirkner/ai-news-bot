"""The Monday send window, pinned to the runs that skipped."""

from datetime import datetime, timezone

import pytest

from src.schedule import decide
from src.schedule_guard import main

UTC = timezone.utc


def _at(raw: str):
    return datetime.fromisoformat(raw).replace(tzinfo=UTC)


def test_manual_run_is_not_held_to_the_monday_window():
    decision = decide(_at("2026-11-11T18:00:00"), "workflow_dispatch")
    assert decision.proceed is True
    assert decision.exit_code == 0
    assert decision.window_end == ""


@pytest.mark.parametrize(
    ("raw", "window_end"),
    [
        # Oct 26, both crons, about five hours late. Pacific hour 13 and 14.
        ("2026-10-26T20:54:30", "2026-10-26"),
        ("2026-10-26T21:16:18", "2026-10-26"),
        # Nov 2, about seven hours late. Pacific hour 15. The 09-12 window skipped both.
        ("2026-11-02T23:20:05", "2026-11-02"),
        ("2026-11-02T23:44:45", "2026-11-02"),
        # Nov 9, runs 42724646151 and 42728847715. Hours 15 and 16, still Monday.
        ("2026-11-09T23:58:09", "2026-11-09"),
        ("2026-11-10T00:30:39", "2026-11-09"),
    ],
)
def test_the_late_monday_runs_that_skipped_now_send(raw, window_end):
    decision = decide(_at(raw), "schedule")
    assert decision.proceed is True
    assert decision.exit_code == 0
    assert decision.window_end == window_end


def test_winter_eight_am_waits_for_the_nine_am_cron():
    # 16:00 UTC is 08:00 PST. That cron is the summer 09:00 slot.
    decision = decide(_at("2026-11-09T16:00:00"), "schedule")
    assert decision.proceed is False
    assert decision.exit_code == 0
    assert decision.window_end == ""


def test_nine_am_pacific_sends_in_winter_and_summer():
    winter = decide(_at("2026-11-09T17:00:00"), "schedule")
    summer = decide(_at("2026-10-19T16:00:00"), "schedule")
    assert winter.proceed is True and winter.window_end == "2026-11-09"
    assert summer.proceed is True and summer.window_end == "2026-10-19"


def test_tuesday_morning_still_sends_mondays_issue():
    decision = decide(_at("2026-11-10T20:59:00"), "schedule")
    assert decision.proceed is True
    assert decision.exit_code == 0
    assert decision.window_end == "2026-11-09"
    missed = decide(_at("2026-11-10T21:00:00"), "schedule")
    assert missed.proceed is False
    assert missed.exit_code == 1
    assert missed.window_end == ""


def test_a_run_that_lands_on_wednesday_fails():
    decision = decide(_at("2026-11-11T17:00:00"), "schedule")
    assert decision.proceed is False
    assert decision.exit_code == 1


def test_cli_records_the_nov_9_late_run_and_fails_a_wednesday(tmp_path, monkeypatch):
    output = tmp_path / "output.txt"
    summary = tmp_path / "summary.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("EVENT_NAME", "schedule")
    monkeypatch.setenv("SCHEDULE_NOW", "2026-11-09T23:58:09Z")
    assert main() == 0
    text = output.read_text(encoding="utf-8")
    assert "proceed=true" in text
    assert "window_end=2026-11-09" in text
    assert "2026-11-09" in summary.read_text(encoding="utf-8")

    output.write_text("", encoding="utf-8")
    monkeypatch.setenv("SCHEDULE_NOW", "2026-11-11T17:00:00Z")
    assert main() == 1
    assert "proceed=false" in output.read_text(encoding="utf-8")
