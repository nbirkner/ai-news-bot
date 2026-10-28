"""Tuesday AI Lessons send. HTTP is mocked. Nothing is delivered."""

import json
from pathlib import Path

import pytest

from src.lesson_file import load_lesson
from src.newsletter import lesson_email_subject, render_lesson_email, render_lesson_plain
from src.resend_send import LESSONS_FROM, SendError, assert_lessons_audience

ROOT = Path(__file__).resolve().parents[1]
LESSON = ROOT / "tests/fixtures/lesson_2026-10-20-lost-in-the-middle.md"
DATE = "2026-10-20"
LESSONS_AUDIENCE = "78261eea-8f8b-4381-83c6-79fa7120f1cf"
NEWS_AUDIENCE = "a2f59f11-1111-4111-8111-111111111111"


class Response:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _clean_lesson_env(monkeypatch):
    for name in (
        "DRY_RUN",
        "RESEND_API_KEY",
        "RESEND_AUDIENCE_ID",
        "RESEND_LESSONS_AUDIENCE_ID",
        "RESEND_LESSONS_FROM",
        "TEST_TO",
        "GITHUB_OUTPUT",
    ):
        monkeypatch.delenv(name, raising=False)


def _install(monkeypatch, tmp_path, post=None):
    lesson_dir = tmp_path / "lessons"
    lesson_dir.mkdir(exist_ok=True)
    (lesson_dir / "2026-10-20-lost-in-the-middle.md").write_text(
        LESSON.read_text(encoding="utf-8"), encoding="utf-8"
    )
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers or {}, "json": json})
        if post:
            return post(url, headers, json)
        if url.endswith("/broadcasts"):
            return Response(201, {"id": "br_lesson"})
        if url.endswith("/send"):
            return Response(200, {"id": "br_lesson"})
        if url.endswith("/emails"):
            return Response(200, {"id": "em_lesson"})
        raise AssertionError(url)

    monkeypatch.setattr("src.resend_send.requests.post", fake_post)
    monkeypatch.setenv("LESSON_WEEKLY_DIR", str(lesson_dir))
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    output = tmp_path / "github_output.txt"
    output.write_text("")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    return calls, output


def _forbid(monkeypatch):
    def forbid(url, headers=None, json=None, timeout=None):
        raise AssertionError(url)

    monkeypatch.setattr("src.resend_send.requests.post", forbid)


def test_assert_lessons_audience_refuses_the_news_list():
    with pytest.raises(SendError, match="not a fallback"):
        assert_lessons_audience("", news_audience_id=NEWS_AUDIENCE)
    with pytest.raises(SendError, match="AI News audience"):
        assert_lessons_audience(NEWS_AUDIENCE, news_audience_id=NEWS_AUDIENCE)
    with pytest.raises(SendError, match="AI News audience"):
        assert_lessons_audience(NEWS_AUDIENCE)
    with pytest.raises(SendError, match="AI News audience"):
        assert_lessons_audience("  a2f59f11  ")
    assert assert_lessons_audience(LESSONS_AUDIENCE, news_audience_id=NEWS_AUDIENCE) == LESSONS_AUDIENCE


def test_dry_run_writes_the_letter_and_does_not_call_resend(tmp_path, monkeypatch):
    calls, output = _install(monkeypatch, tmp_path)
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("RESEND_LESSONS_AUDIENCE_ID", LESSONS_AUDIENCE)
    monkeypatch.setenv("RESEND_AUDIENCE_ID", NEWS_AUDIENCE)
    monkeypatch.setenv("TEST_TO", "niki@example.com")
    from main_newsletter import main
    code = main(["main_newsletter.py", "--mode", "lesson", "--dry-run", "--send", "--date", DATE])
    assert code == 0
    assert calls == []
    assert not (tmp_path / "state" / "sent_lessons.json").exists()
    assert output.read_text() == ""
    html = (tmp_path / "out" / "2026-10-20-lost-in-the-middle.html").read_text(encoding="utf-8")
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in html
    assert NEWS_AUDIENCE not in html


def test_missing_audience_does_not_fall_back_to_news(tmp_path, monkeypatch, capsys):
    _install(monkeypatch, tmp_path)
    _forbid(monkeypatch)
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("RESEND_AUDIENCE_ID", NEWS_AUDIENCE)
    from main_newsletter import main
    code = main(["main_newsletter.py", "--mode", "lesson", "--send", "--date", DATE])
    assert code == 2
    err = capsys.readouterr().err
    assert "RESEND_LESSONS_AUDIENCE_ID is not set" in err
    assert "not a fallback" in err
    assert "re_test" not in err
    assert not (tmp_path / "state" / "sent_lessons.json").exists()
    assert (tmp_path / "out" / "2026-10-20-lost-in-the-middle.html").exists()


def test_news_audience_id_is_refused(tmp_path, monkeypatch, capsys):
    _install(monkeypatch, tmp_path)
    _forbid(monkeypatch)
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("RESEND_LESSONS_AUDIENCE_ID", NEWS_AUDIENCE)
    monkeypatch.setenv("RESEND_AUDIENCE_ID", NEWS_AUDIENCE)
    from main_newsletter import main
    code = main(["main_newsletter.py", "--mode", "lesson", "--send", "--date", DATE])
    assert code == 2
    assert "AI News audience" in capsys.readouterr().err
    assert not (tmp_path / "state" / "sent_lessons.json").exists()


def test_broadcast_uses_the_lessons_audience_and_fonts(tmp_path, monkeypatch, capsys):
    calls, output = _install(monkeypatch, tmp_path)
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("RESEND_LESSONS_AUDIENCE_ID", LESSONS_AUDIENCE)
    monkeypatch.setenv("RESEND_AUDIENCE_ID", NEWS_AUDIENCE)
    from main_newsletter import main
    code = main(["main_newsletter.py", "--mode", "lesson", "--send", "--date", DATE])
    assert code == 0
    assert len(calls) == 2
    created, sent = calls
    assert created["url"] == "https://api.resend.com/broadcasts"
    assert created["headers"]["Authorization"] == "Bearer re_test"
    body = created["json"]
    assert body["from"] == LESSONS_FROM
    assert "ainews@" not in body["from"]
    assert body["subject"] == "AI Lessons, October 20, 2026"
    assert body["audience_id"] == LESSONS_AUDIENCE
    assert body["segment_id"] == LESSONS_AUDIENCE
    assert body["audience_id"] != NEWS_AUDIENCE
    assert NEWS_AUDIENCE not in body["html"]
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in body["html"]
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in body["text"]
    assert ">AI Lessons</p>" in body["html"]
    assert ">AI News</p>" not in body["html"]
    assert "AI News" not in body["html"]
    assert "nikibirkner.com/ai-news" not in body["html"]
    assert 'href="https://www.nikibirkner.com/ai-lessons/"' in body["html"]
    headline = body["html"].split("<h1", 1)[1].split("</h1>", 1)[0]
    assert "Roboto" in headline
    assert "Fragment Mono" not in headline
    assert "AI Lessons, October 20, 2026" in headline
    byline = body["html"].split("by Niki Birkner", 1)[0].rsplit('style="', 1)[1].split('"', 1)[0]
    assert "Fragment Mono" in byline
    assert "Roboto" not in byline
    level = body["html"].split(">Foundations<", 1)[0].rsplit('style="', 1)[1]
    assert "Fragment Mono" in level
    assert "Roboto" not in level
    hook = body["html"].split("You paste a long note", 1)[0].rsplit('style="', 1)[1]
    assert "Roboto" in hook
    assert "Fragment Mono" not in hook
    assert "How it works" in body["html"]
    assert 'href="https://arxiv.org/abs/2307.03172"' in body["html"]
    assert "](https://" not in body["html"]
    assert "family=Roboto:wght@400;600" in body["html"]
    assert sent["url"] == "https://api.resend.com/broadcasts/br_lesson/send"
    saved = json.loads((tmp_path / "state" / "sent_lessons.json").read_text())
    assert saved["issues"][DATE]["broadcast_id"] == "br_lesson"
    assert not (tmp_path / "state" / "sent_issues.json").exists()
    assert not (tmp_path / "state" / "seen_articles.json").exists()
    assert "sent=true" in output.read_text()
    assert "re_test" not in capsys.readouterr().err

    again, _output_again = _install(monkeypatch, tmp_path)
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("RESEND_LESSONS_AUDIENCE_ID", LESSONS_AUDIENCE)
    code = main(["main_newsletter.py", "--mode", "lesson", "--send", "--date", DATE])
    assert code == 0
    assert again == []


def test_failed_send_does_not_record_the_lesson(tmp_path, monkeypatch):
    def post(url, headers, payload):
        if url.endswith("/broadcasts"):
            return Response(201, {"id": "br_lesson"})
        return Response(500, {"message": "no"})

    calls, output = _install(monkeypatch, tmp_path, post=post)
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("RESEND_LESSONS_AUDIENCE_ID", LESSONS_AUDIENCE)
    from main_newsletter import main
    code = main(["main_newsletter.py", "--mode", "lesson", "--send", "--date", DATE])
    assert code == 2
    assert len(calls) == 2
    assert not (tmp_path / "state" / "sent_lessons.json").exists()
    assert "sent=true" not in output.read_text()


def test_test_to_sends_one_email_and_skips_both_audiences(tmp_path, monkeypatch):
    calls, output = _install(monkeypatch, tmp_path)
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("RESEND_AUDIENCE_ID", NEWS_AUDIENCE)
    monkeypatch.setenv("TEST_TO", "niki@example.com")
    from main_newsletter import main
    code = main(["main_newsletter.py", "--mode", "lesson", "--send", "--date", DATE])
    assert code == 0
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://api.resend.com/emails"
    assert "/broadcasts" not in call["url"]
    assert call["json"]["from"] == LESSONS_FROM
    assert call["json"]["to"] == ["niki@example.com"]
    assert call["json"]["subject"] == "AI Lessons, October 20, 2026"
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" not in call["json"]["html"]
    assert "https://nikibirkner.com/ai-lessons/2026-10-20-lost-in-the-middle" in call["json"]["html"]
    assert NEWS_AUDIENCE not in call["json"]["html"]
    assert not (tmp_path / "state" / "sent_lessons.json").exists()
    assert "sent=true" not in output.read_text()


def test_custom_from_and_invalid_from(tmp_path, monkeypatch, capsys):
    calls, _output = _install(monkeypatch, tmp_path)
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("RESEND_LESSONS_AUDIENCE_ID", LESSONS_AUDIENCE)
    monkeypatch.setenv("RESEND_LESSONS_FROM", "Niki Birkner <lessons@nikibirkner.com>")
    from main_newsletter import main
    code = main(["main_newsletter.py", "--mode", "lesson", "--send", "--date", DATE])
    assert code == 0
    assert calls[0]["json"]["from"] == "Niki Birkner <lessons@nikibirkner.com>"

    _install(monkeypatch, tmp_path)
    _forbid(monkeypatch)
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("RESEND_LESSONS_AUDIENCE_ID", LESSONS_AUDIENCE)
    monkeypatch.setenv("RESEND_LESSONS_FROM", "not-an-email")
    code = main(["main_newsletter.py", "--mode", "lesson", "--send", "--date", DATE])
    assert code == 2
    assert "RESEND_LESSONS_FROM" in capsys.readouterr().err


def test_post_and_publish_stay_off(tmp_path, monkeypatch):
    _install(monkeypatch, tmp_path)
    _forbid(monkeypatch)
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("RESEND_LESSONS_AUDIENCE_ID", LESSONS_AUDIENCE)
    from main_newsletter import main
    assert main(["main_newsletter.py", "--mode", "lesson", "--post", "--date", DATE]) == 2
    assert main(["main_newsletter.py", "--mode", "lesson", "--publish", "--date", DATE]) == 2


def test_lesson_letter_matches_the_news_type(tmp_path):
    target = tmp_path / "2026-10-20-lost-in-the-middle.md"
    target.write_text(LESSON.read_text(encoding="utf-8"), encoding="utf-8")
    lesson = load_lesson(target, expected_date=DATE)
    html = render_lesson_email(lesson)
    plain = render_lesson_plain(lesson)
    assert lesson_email_subject(lesson) == "AI Lessons, October 20, 2026"
    assert "<title>AI Lessons, October 20, 2026</title>" in html
    assert "Tuesday, October 20, 2026" in html
    assert "Roboto" in html
    assert "Fragment Mono" in html
    assert "Plus Jakarta Sans" not in html
    assert "Schibsted Grotesk" not in html
    assert "Try this" in plain
    assert "Lost in the Middle (arXiv) (https://arxiv.org/abs/2307.03172)" in plain
    assert plain.startswith(
        "Forwarded this? Subscribe at nikibirkner.com/ai-lessons\n"
        "https://www.nikibirkner.com/ai-lessons/\n"
    )
    preview = render_lesson_email(lesson, broadcast=False)
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" not in preview
    assert "Unsubscribe\nhttps://nikibirkner.com/ai-lessons/2026-10-20-lost-in-the-middle\n" in render_lesson_plain(
        lesson, broadcast=False
    )


def test_create_lessons_segment_reuses_the_named_segment_and_skips_general(monkeypatch):
    calls = []
    pages = [
        Response(200, {"object": "list", "has_more": True, "data": [{"id": "seg_general", "name": "General"}]}),
        Response(200, {"object": "list", "has_more": False, "data": [{"id": LESSONS_AUDIENCE, "name": "AI Lessons"}]}),
    ]

    def get(url, headers=None, timeout=None):
        calls.append(("GET", url))
        return pages.pop(0)

    def post(url, headers=None, json=None, timeout=None):
        raise AssertionError(url)

    monkeypatch.setattr("src.resend_send.requests.get", get)
    monkeypatch.setattr("src.resend_send.requests.post", post)
    from src.resend_send import create_lessons_segment
    found = create_lessons_segment(api_key="re_test", news_audience_id=NEWS_AUDIENCE, get=get, post=post)
    assert found == LESSONS_AUDIENCE
    assert calls[0][1] == "https://api.resend.com/segments?limit=100"
    assert "after=seg_general" in calls[1][1]
    assert all("/broadcasts" not in url and "/emails" not in url for _method, url in calls)


def test_create_lessons_segment_posts_when_missing():
    def get(url, headers=None, timeout=None):
        return Response(200, {"data": [{"id": "seg_general", "name": "General"}], "has_more": False})

    def post(url, headers=None, json=None, timeout=None):
        assert url == "https://api.resend.com/segments"
        assert json == {"name": "AI Lessons"}
        assert "/broadcasts" not in url
        return Response(201, {"id": LESSONS_AUDIENCE, "name": "AI Lessons", "object": "segment"})

    from src.resend_send import create_lessons_segment
    assert create_lessons_segment(api_key="re_test", get=get, post=post) == LESSONS_AUDIENCE


def test_create_script_prints_the_id_and_refuses_the_news_audience(monkeypatch, capsys):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.delenv("RESEND_AUDIENCE_ID", raising=False)

    def get(url, headers=None, timeout=None):
        assert headers["Authorization"] == "Bearer re_test"
        return Response(200, {"data": [{"id": LESSONS_AUDIENCE, "name": "AI Lessons"}], "has_more": False})

    def post(*args, **kwargs):
        raise AssertionError("created")

    monkeypatch.setattr("src.resend_send.requests.get", get)
    monkeypatch.setattr("src.resend_send.requests.post", post)
    from scripts.create_lessons_audience import main
    assert main([]) == 0
    out, err = capsys.readouterr()
    assert out.strip() == LESSONS_AUDIENCE
    assert "RESEND_LESSONS_AUDIENCE_ID" in err
    assert "re_test" not in out
    assert "re_test" not in err

    monkeypatch.setenv("RESEND_AUDIENCE_ID", LESSONS_AUDIENCE)
    assert main([]) == 2
    err = capsys.readouterr().err
    assert "AI News audience" in err
    assert "re_test" not in err


def test_create_script_does_not_call_resend_without_a_key(monkeypatch, capsys):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    def boom(*args, **kwargs):
        raise AssertionError("network")

    monkeypatch.setattr("src.resend_send.requests.get", boom)
    monkeypatch.setattr("src.resend_send.requests.post", boom)
    from scripts.create_lessons_audience import main
    assert main([]) == 2
    err = capsys.readouterr().err
    assert "RESEND_API_KEY is not set" in err
    assert "AI Lessons" in err
