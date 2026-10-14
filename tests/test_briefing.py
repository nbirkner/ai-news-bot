from datetime import datetime, timedelta, timezone

import pytest

from src.briefing import BriefingError, run_briefing, window_start
from src.models import Article, FetchReport


NOW = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
CONFIG = {
    "lookback_fallback_hours": 80,
    "max_lookback_hours": 168,
    "state_retention_days": 21,
    "max_items": 6,
    "material_score": 6,
    "paper_min_score": 9,
    "max_per_source_in_briefing": 2,
    "max_per_signature": 1,
    "together_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "sources": [{"name": "Google"}, {"name": "Reuters"}, {"name": "OpenAI"}],
}


def _article(**kwargs):
    data = dict(
        url="https://blog.google/gemini-4-argon",
        title="Gemini 4 Argon: Built to solve your hardest problems",
        source_name="Google",
        source_domain="blog.google",
        published_at=datetime(2026, 10, 13, 16, tzinfo=timezone.utc),
        content_preview="Google published Gemini 4 Argon, a new frontier model.",
        source_kind="lab",
        source_priority=1,
    )
    data.update(kwargs)
    return Article(**data)


def _report(articles):
    return FetchReport(articles=articles, failures=[], successes=3)


def _run(tmp_path, articles, *, dry_run, api_key=None, webhook=None, poster=None, seen=None):
    calls = []

    def default_poster(payload, url):
        calls.append((payload, url))

    if seen:
        (tmp_path / "seen.json").write_text(seen)
    text = run_briefing(
        CONFIG,
        dry_run=dry_run,
        api_key=api_key,
        webhook_url=webhook,
        now=NOW,
        seen_path=tmp_path / "seen.json",
        last_run_path=tmp_path / "last.json",
        fetcher=lambda config, since, now: _report(articles),
        enricher=lambda items: None,
        poster=poster or default_poster,
    )
    return text, calls


def test_window_uses_last_run_and_caps_outages():
    assert window_start(None, NOW, 80, 168) == NOW - timedelta(hours=80)
    recent = NOW - timedelta(hours=48)
    assert window_start(recent, NOW, 80, 168) == recent
    ancient = NOW - timedelta(days=30)
    assert window_start(ancient, NOW, 80, 168) == NOW - timedelta(hours=168)


def test_dry_run_prints_and_does_not_post_or_save_state(tmp_path):
    def boom(*args, **kwargs):
        raise AssertionError("Slack was called during a dry run")

    text, _calls = _run(
        tmp_path,
        [_article()],
        dry_run=True,
        webhook="https://hooks.slack.com/services/T/B/secret",
        poster=boom,
    )
    assert "Gemini 4 Argon" in text
    assert "Quiet since" not in text
    assert "\u2014" not in text
    assert not (tmp_path / "seen.json").exists()
    assert not (tmp_path / "last.json").exists()


def test_quiet_note_when_nothing_material(tmp_path):
    noise = _article(
        url="https://openai.com/customer",
        title="SentinelOne cuts malware analysis from weeks to hours",
        source_name="OpenAI",
        content_preview="A customer story.",
    )
    text, calls = _run(tmp_path, [noise], dry_run=True)
    assert "Quiet since" in text
    assert calls == []


def test_live_post_saves_state_and_posts_once(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.briefing.write_copies",
        lambda articles, api_key, model, strict_auth: [
            ("Google published Gemini 4 Argon.", "A model release changes what products can do and what serving stacks need to run.")
        ],
    )
    text, calls = _run(
        tmp_path,
        [_article()],
        dry_run=False,
        api_key="sk-test",
        webhook="https://hooks.slack.com/services/T/B/test",
    )
    assert len(calls) == 1
    payload, url = calls[0]
    assert url == "https://hooks.slack.com/services/T/B/test"
    assert payload["unfurl_links"] is False
    assert "Gemini 4 Argon" in payload["text"]
    assert "Gemini 4 Argon" in text
    assert (tmp_path / "last.json").exists()
    seen = (tmp_path / "seen.json").read_text()
    assert "blog.google/gemini-4-argon" in seen


def test_missing_webhook_does_not_post_or_save_state(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.briefing.write_copies",
        lambda articles, api_key, model, strict_auth: [("Google published Gemini 4 Argon.", "Because.")],
    )
    with pytest.raises(BriefingError, match="SLACK_WEBHOOK_URL"):
        _run(tmp_path, [_article()], dry_run=False, api_key="sk-test", webhook=None)
    assert not (tmp_path / "seen.json").exists()
    assert not (tmp_path / "last.json").exists()


def test_seen_urls_are_not_briefed_again(tmp_path):
    import json
    seen = json.dumps({"blog.google/gemini-4-argon": "2026-10-13T16:00:00+00:00"})
    text, _calls = _run(tmp_path, [_article()], dry_run=True, seen=seen)
    assert "Quiet since" in text


def test_source_outage_does_not_post_a_quiet_note(tmp_path):
    def fetcher(config, since, now):
        return FetchReport(articles=[], failures=["OpenAI: timeout"], successes=1)

    with pytest.raises(BriefingError, match="Too many sources failed"):
        run_briefing(
            CONFIG,
            dry_run=False,
            api_key="sk-test",
            webhook_url="https://hooks.slack.com/services/T/B/test",
            now=NOW,
            seen_path=tmp_path / "seen.json",
            last_run_path=tmp_path / "last.json",
            fetcher=fetcher,
            enricher=lambda items: None,
            poster=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("posted")),
        )


def test_workflow_schedule_and_secrets():
    from pathlib import Path
    text = Path(".github/workflows/briefing.yml").read_text()
    assert "0 15 * * 1,3,5" in text
    assert "SLACK_WEBHOOK_URL" in text
    assert "TOGETHER_API_KEY" in text
    assert "github.token" in text
    assert "dry_run" in text
    assert not Path(".github/workflows/breaking-check.yml").exists()
    assert not Path(".github/workflows/daily-digest.yml").exists()
