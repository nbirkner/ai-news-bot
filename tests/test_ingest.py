"""Ingest Learning-bot files. No network and no model calls."""
import json
from pathlib import Path

import pytest
import requests

from scripts.ingest_news import main as ingest_main
from src.lesson_file import LessonBlocked, lesson_for_date, load_lesson, week_lesson
from src.newsletter import render_email
from src.roundup import IngestError, load_roundup, parse_roundup
from src.sources_file import load_sources_file, validate_sources

ROOT = Path(__file__).resolve().parents[1]
ROUNDUP = (ROOT / "tests/fixtures/roundup_2026-10-19.md").read_text(encoding="utf-8")
LESSON = ROOT / "tests/fixtures/lesson_2026-10-20-lost-in-the-middle.md"
DATE = "2026-10-19"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("network is disabled in ingest tests")

    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(requests.Session, "request", boom)


def test_roundup_becomes_validated_sources():
    parsed = parse_roundup(ROUNDUP, DATE)
    assert validate_sources(parsed["sources"]) == ""
    assert parsed["sources"][0]["title"] == "Anthropic publishes a workforce study."
    assert parsed["sources"][0]["source"] == "Anthropic"
    assert parsed["sources"][0]["url"].startswith("https://www.anthropic.com/")
    assert parsed["sources"][0]["published"] == DATE
    assert "27%" in parsed["sources"][0]["summary"]
    assert parsed["sources"][0]["reflection"].startswith("It is the first")
    assert "grid" in parsed["lede"]
    assert len(parsed["sources"]) == 2


def test_bad_roundup_writes_nothing(tmp_path):
    broken = ROUNDUP.replace(
        "<https://www.theguardian.com/uk-news/2026/oct/18/microsoft-datacentre|The Guardian>",
        "no link here",
    )
    with pytest.raises(IngestError, match="missing a link"):
        parse_roundup(broken, DATE)
    source = tmp_path / "roundup.md"
    source.write_text(broken, encoding="utf-8")
    code = ingest_main([
        "ingest_news.py",
        "--date", DATE,
        "--roundup", str(source),
        "--sources", str(tmp_path / "sources.json"),
        "--render",
        "--out", str(tmp_path / "out"),
    ])
    assert code == 2
    assert not (tmp_path / "sources.json").exists()
    assert not (tmp_path / "out").exists()


def test_missing_roundup_is_rejected(tmp_path):
    with pytest.raises(IngestError, match="roundup file is missing"):
        load_roundup(tmp_path / "missing.md", DATE)


def test_script_writes_sources_and_email_with_optional_lesson(tmp_path):
    lesson_dir = tmp_path / "lessons"
    lesson_dir.mkdir()
    (lesson_dir / "2026-10-20-lost-in-the-middle.md").write_text(
        LESSON.read_text(encoding="utf-8"), encoding="utf-8"
    )
    out = tmp_path / "out"
    sources = tmp_path / "sources" / f"{DATE}.json"
    code = ingest_main([
        "ingest_news.py",
        "--date", DATE,
        "--roundup", str(ROOT / "tests/fixtures/roundup_2026-10-19.md"),
        "--sources", str(sources),
        "--render",
        "--out", str(out),
        "--lesson-dir", str(lesson_dir),
    ])
    assert code == 0
    loaded = load_sources_file(sources)
    assert loaded.status == "ok"
    assert len(loaded.articles) == 2
    payload = json.loads(sources.read_text(encoding="utf-8"))
    assert set(payload[0]) == {"title", "url", "source", "published", "summary", "reflection"}
    html = (out / f"{DATE}.html").read_text(encoding="utf-8")
    mdx = (out / f"{DATE}.mdx").read_text(encoding="utf-8")
    plain = (out / f"{DATE}.txt").read_text(encoding="utf-8")
    assert "AI News, October 19, 2026" in html
    assert "Anthropic publishes a workforce study." in html
    assert "https://www.anthropic.com/research/employment-at-anthropic" in html
    assert "#C2410C" in html
    assert "Schibsted Grotesk" in html
    assert "This week's lesson" in html
    assert "Models miss the middle of a long prompt." in html
    assert "https://nikibirkner.com/ai-lessons/2026-10-20-lost-in-the-middle" in html
    assert "This week's lesson" in mdx
    assert "This week's lesson" in plain
    assert "AI Lessons," not in html


def test_render_without_a_lesson_omits_the_section(tmp_path):
    out = tmp_path / "out"
    code = ingest_main([
        "ingest_news.py",
        "--date", DATE,
        "--roundup", str(ROOT / "tests/fixtures/roundup_2026-10-19.md"),
        "--sources", str(tmp_path / "sources.json"),
        "--render",
        "--out", str(out),
        "--lesson-dir", str(tmp_path / "empty"),
    ])
    assert code == 0
    html = (out / f"{DATE}.html").read_text(encoding="utf-8")
    assert "AI News, October 19, 2026" in html
    assert "This week's lesson" not in html


def test_lesson_file_becomes_site_mdx(tmp_path, monkeypatch):
    lesson_dir = tmp_path / "lessons"
    lesson_dir.mkdir()
    target = lesson_dir / "2026-10-20-lost-in-the-middle.md"
    target.write_text(LESSON.read_text(encoding="utf-8"), encoding="utf-8")
    lesson = load_lesson(target, expected_date="2026-10-20")
    assert lesson.level == "Foundations"
    assert lesson.slug == "lost-in-the-middle"
    monkeypatch.setenv("LESSON_WEEKLY_DIR", str(lesson_dir))
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    from main_newsletter import main
    code = main(["main_newsletter.py", "--mode", "lesson", "--dry-run", "--date", "2026-10-20"])
    assert code == 0
    mdx = (tmp_path / "out" / "2026-10-20-lost-in-the-middle.mdx").read_text(encoding="utf-8")
    assert "level: Foundations" in mdx
    assert "slug: lost-in-the-middle" in mdx
    assert mdx.startswith("---\n")
    assert "## Try this" in mdx
    refused = main(["main_newsletter.py", "--mode", "lesson", "--send", "--date", "2026-10-20"])
    assert refused == 2


def test_missing_or_bad_lesson_is_blocked(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LESSON_WEEKLY_DIR", str(tmp_path / "missing"))
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    from main_newsletter import main
    code = main(["main_newsletter.py", "--mode", "lesson", "--dry-run", "--date", "2026-10-20"])
    assert code == 2
    blocked = (tmp_path / "out" / "2026-10-20.blocked.txt").read_text(encoding="utf-8")
    assert blocked == "BLOCKED: lesson file is missing\n"
    assert capsys.readouterr().out == blocked
    assert not list((tmp_path / "out").glob("*.mdx"))

    lesson_dir = tmp_path / "lessons"
    lesson_dir.mkdir()
    bad = LESSON.read_text(encoding="utf-8").replace("level: Foundations", "level: Staff")
    (lesson_dir / "2026-10-20-lost-in-the-middle.md").write_text(bad, encoding="utf-8")
    with pytest.raises(LessonBlocked, match="Foundations, Systems, or Frontier"):
        lesson_for_date("2026-10-20", lesson_dir)


def test_week_lesson_is_optional_and_skips_other_weeks(tmp_path):
    lesson_dir = tmp_path / "lessons"
    lesson_dir.mkdir()
    (lesson_dir / "2026-10-20-lost-in-the-middle.md").write_text(
        LESSON.read_text(encoding="utf-8"), encoding="utf-8"
    )
    found = week_lesson("2026-10-19", lesson_dir)
    assert found.slug == "lost-in-the-middle"
    assert week_lesson("2026-10-26", lesson_dir) is None
    assert week_lesson("2026-10-19", tmp_path / "absent") is None
    html = render_email(
        __import__("src.roundup", fromlist=["issue_from_roundup"]).issue_from_roundup(
            parse_roundup(ROUNDUP, DATE)
        ),
        lesson=found,
    )
    assert "This week's lesson" in html


def test_lessons_workflow_is_tuesday_dry_run_only():
    text = Path(".github/workflows/lessons.yml").read_text(encoding="utf-8")
    assert 'cron: "0 16 * * 2"' in text
    assert 'cron: "0 17 * * 2"' in text
    assert '[ "$hour" = "09" ]' in text
    assert "America/Los_Angeles" in text
    assert "--mode lesson" in text
    assert "--dry-run" in text
    assert 'DRY_RUN: "1"' in text
    assert "contents: read" in text
    assert "--send" not in text
    assert "RESEND" not in text
    assert "SLACK" not in text
    assert "SITE_REPO" not in text
    assert "git commit" not in text
    assert "git push" not in text


def test_monday_send_workflow_is_unchanged():
    text = Path(".github/workflows/briefing.yml").read_text(encoding="utf-8")
    assert 'cron: "0 16 * * 1"' in text
    assert 'cron: "0 17 * * 1"' in text
    assert "--send" in text
    assert "--test-to" not in text
    assert "test_to:" in text
    assert "TEST_TO:" in text
    assert "RESEND_API_KEY" in text
    assert "RESEND_AUDIENCE_ID" in text
    assert "main_newsletter.py" in text
