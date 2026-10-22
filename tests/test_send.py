"""The Monday newsletter send. HTTP is mocked. Nothing is posted to Slack."""

import json
from datetime import datetime, timezone

import pytest

from src.issue_run import pacific_monday, parse_window_end, weekly_window_start
from src.models import BriefingItem, Issue
from src.newsletter import email_subject

NOW = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
AUDIENCE = "audience-test"


class Response:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def _issue():
    return Issue(
        title="AI briefing, October 19",
        date="2026-10-19",
        summary="Tokens and dollars moved apart in this window.",
        tags=["models"],
        items=[BriefingItem(
            what_happened="Tokens and dollars are splitting",
            body="Open-weight models took 71.5% of tokens. Those models were 23% of spend. DeepSeek handled the rest.",
            why_it_matters="A cheap token and an expensive closed model are different bills.",
            url="https://vercel.com/blog/ai-gateway-production-index-october-2026",
            source_name="Vercel",
        )],
        since=SINCE,
        until=NOW,
    )


class Discovered:
    exa_search_calls = 0
    exa_content_pages = 0


@pytest.fixture(autouse=True)
def _clean_send_env(monkeypatch):
    for name in ("DRY_RUN", "RESEND_API_KEY", "RESEND_AUDIENCE_ID", "TEST_TO", "GITHUB_OUTPUT"):
        monkeypatch.delenv(name, raising=False)


def _install(monkeypatch, tmp_path, issue=None, failures=None, post=None):
    calls = []
    generated = []

    def fake_generate(config, **kwargs):
        generated.append(kwargs)
        return issue if issue is not None else _issue(), list(failures or []), Discovered()

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers or {}, "json": json})
        if post:
            return post(url, headers, json)
        if url.endswith("/broadcasts"):
            return Response(201, {"id": "br_123"})
        if url.endswith("/send"):
            return Response(200, {"id": "br_123"})
        if url.endswith("/emails"):
            return Response(200, {"id": "em_123"})
        raise AssertionError(url)

    monkeypatch.setattr("main_newsletter.generate_issue", fake_generate)
    monkeypatch.setattr("src.resend_send.requests.post", fake_post)
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    output = tmp_path / "github_output.txt"
    output.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    return calls, generated, output


def _send_env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("RESEND_AUDIENCE_ID", AUDIENCE)


def test_weekly_window_opens_on_the_previous_monday():
    assert weekly_window_start(parse_window_end("2026-10-19")) == datetime(2026, 10, 12, 15, tzinfo=timezone.utc)
    wednesday = datetime(2026, 10, 21, 16, tzinfo=timezone.utc)
    assert pacific_monday(wednesday) == "2026-10-19"
    assert pacific_monday(datetime(2026, 10, 19, 16, tzinfo=timezone.utc)) == "2026-10-19"


def test_dry_run_does_not_call_resend_or_save_state(tmp_path, monkeypatch, capsys):
    calls, _generated, output = _install(monkeypatch, tmp_path)
    _send_env(monkeypatch)
    monkeypatch.setenv("TEST_TO", "niki@example.com")
    from main_newsletter import main
    code = main(["main_newsletter.py", "--dry-run", "--send", "--window-end", "2026-10-19"])
    assert code == 0
    assert calls == []
    assert not (tmp_path / "state" / "sent_issues.json").exists()
    assert output.read_text() == ""
    assert "Dry run: not sending the test email." in capsys.readouterr().err
    html = (tmp_path / "out" / "2026-10-19.html").read_text()
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in html


def test_broadcast_uses_the_issue_subject_then_sends_once(tmp_path, monkeypatch, capsys):
    calls, generated, output = _install(monkeypatch, tmp_path)
    _send_env(monkeypatch)
    from main_newsletter import main
    code = main(["main_newsletter.py", "--send", "--window-end", "2026-10-19"])
    assert code == 0
    assert len(generated) == 1
    assert len(calls) == 2
    created, sent = calls
    assert created["url"] == "https://api.resend.com/broadcasts"
    assert created["headers"]["Authorization"] == "Bearer re_test"
    body = created["json"]
    assert body["from"] == "Niki Birkner <ainews@nikibirkner.com>"
    assert body["subject"] == "AI News, October 19, 2026"
    assert body["subject"] == email_subject(_issue())
    assert body["audience_id"] == AUDIENCE
    assert body["segment_id"] == AUDIENCE
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in body["html"]
    assert ">AI News, October 19, 2026</h1>" in body["html"]
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in body["text"]
    assert "AI News, October 19, 2026" in body["text"]
    assert sent["url"] == "https://api.resend.com/broadcasts/br_123/send"
    assert sent["json"] == {}
    saved = json.loads((tmp_path / "state" / "sent_issues.json").read_text())
    assert saved["issues"]["2026-10-19"]["broadcast_id"] == "br_123"
    seen = (tmp_path / "state" / "seen_articles.json").read_text()
    assert "vercel.com/blog/ai-gateway-production-index-october-2026" in seen
    recent = json.loads((tmp_path / "state" / "recent_briefings.json").read_text())
    assert recent["briefings"][-1]["date"] == "2026-10-19"
    assert recent["briefings"][-1]["items"][0]["title"] == "Tokens and dollars are splitting"
    assert (tmp_path / "state" / "last_briefing.json").exists()
    assert "sent=true" in output.read_text()
    err = capsys.readouterr().err
    assert "re_test" not in err

    again, generated_again, _output_again = _install(monkeypatch, tmp_path)
    _send_env(monkeypatch)
    code = main(["main_newsletter.py", "--send", "--window-end", "2026-10-19"])
    assert code == 0
    assert again == []
    assert generated_again == []


def test_guardrail_failure_is_not_sent(tmp_path, monkeypatch):
    calls, _generated, output = _install(
        monkeypatch, tmp_path, failures=["duplicate of the last issue"]
    )
    _send_env(monkeypatch)
    from main_newsletter import main
    code = main(["main_newsletter.py", "--send", "--window-end", "2026-10-19"])
    assert code == 2
    assert calls == []
    assert not (tmp_path / "state" / "sent_issues.json").exists()
    assert "sent=true" not in output.read_text()


def test_empty_issue_is_not_sent(tmp_path, monkeypatch):
    issue = _issue()
    issue.items = []
    calls, _generated, output = _install(monkeypatch, tmp_path, issue=issue, failures=[])
    _send_env(monkeypatch)
    from main_newsletter import main
    code = main(["main_newsletter.py", "--send", "--window-end", "2026-10-19"])
    assert code == 2
    assert calls == []
    assert not (tmp_path / "state" / "sent_issues.json").exists()
    assert "no stories" in (tmp_path / "out" / "2026-10-19.blocked.txt").read_text()
    assert "sent=true" not in output.read_text()


def test_failed_send_does_not_record_state(tmp_path, monkeypatch):
    def post(url, headers, payload):
        if url.endswith("/broadcasts"):
            return Response(201, {"id": "br_123"})
        return Response(500, {"message": "no"})

    calls, _generated, output = _install(monkeypatch, tmp_path, post=post)
    _send_env(monkeypatch)
    from main_newsletter import main
    code = main(["main_newsletter.py", "--send", "--window-end", "2026-10-19"])
    assert code == 2
    assert len(calls) == 2
    assert not (tmp_path / "state" / "sent_issues.json").exists()
    assert not (tmp_path / "state" / "seen_articles.json").exists()
    assert "sent=true" not in output.read_text()


def test_test_to_sends_one_email_and_skips_the_broadcast(tmp_path, monkeypatch):
    calls, _generated, output = _install(monkeypatch, tmp_path)
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("TEST_TO", "niki@example.com")
    from main_newsletter import main
    code = main(["main_newsletter.py", "--send", "--window-end", "2026-10-19"])
    assert code == 0
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://api.resend.com/emails"
    assert call["json"]["from"] == "Niki Birkner <ainews@nikibirkner.com>"
    assert call["json"]["to"] == ["niki@example.com"]
    assert call["json"]["subject"] == "AI News, October 19, 2026"
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" not in call["json"]["html"]
    assert "https://nikibirkner.com/ai-news" in call["json"]["html"]
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" not in call["json"]["text"]
    assert not (tmp_path / "state" / "sent_issues.json").exists()
    assert not (tmp_path / "state" / "recent_briefings.json").exists()
    assert "sent=true" not in output.read_text()


def test_readme_names_the_resend_secrets():
    from pathlib import Path
    readme = Path("README.md").read_text()
    assert "RESEND_API_KEY" in readme
    assert "RESEND_AUDIENCE_ID" in readme
