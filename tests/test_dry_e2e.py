"""Dry end-to-end runs. No network, no Resend, no secrets."""
from datetime import datetime, timezone

import pytest

from src.discover import prepare_candidates
from src.models import Article, FetchReport


def _article(title, url, published, text):
    return Article(
        url=url,
        title=title,
        source_name="Reuters",
        source_domain="reuters.com",
        published_at=published,
        content_preview=text,
        full_text=text,
        source_kind="news",
        source_priority=2,
    )


def _block_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError(f"network call args={args!r} kwargs={kwargs!r}")

    monkeypatch.setattr("requests.get", boom)
    monkeypatch.setattr("requests.post", boom)
    monkeypatch.setattr("requests.Session.request", boom)


def test_missing_nov9_sources_dry_run_keeps_fresh_rss_and_does_not_send(tmp_path, monkeypatch):
    _block_network(monkeypatch)
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("TOGETHER_API_KEY", raising=False)
    fresh = _article(
        "Nvidia books a chip order",
        "https://www.reuters.com/technology/nvidia-chip-order-2026-11-08/",
        datetime(2026, 11, 8, 16, tzinfo=timezone.utc),
        "Nvidia booked a chip order worth tens of billions of dollars. Reuters reported the order on November 8.",
    )
    stale = _article(
        "Old October chip note",
        "https://www.reuters.com/technology/old-chip-note-2026-10-01/",
        datetime(2026, 10, 1, 16, tzinfo=timezone.utc),
        "An October note about chips. It is outside this window.",
    )
    seen = {}

    def fetcher(config, since, now):
        from src.fetch import in_window
        articles = [item for item in (fresh, stale) if in_window(item.published_at, since, now)]
        return FetchReport(articles=articles, failures=["Reuters sitemap: 401"], successes=8, prior=[stale])

    def wrapped(config, since, now, **kwargs):
        kwargs["fetcher"] = fetcher
        result = prepare_candidates(config, since, now, **kwargs)
        seen["status"] = result.sources_status
        seen["titles"] = [article.title for article in result.shortlist]
        return result

    def composer(articles, api_key, model, **kwargs):
        article = articles[0]
        return (
            "Nvidia booked another large chip order.",
            [(
                article,
                "Nvidia books a chip order",
                "Nvidia booked a chip order worth tens of billions of dollars. Reuters reported the order on November 8.",
                "People building with AI feel this in how much capacity they can rent.",
            )],
        )

    monkeypatch.setattr("src.issue_run.prepare_candidates", wrapped)
    monkeypatch.setattr("src.issue_run.compose_briefing", composer)
    from main_newsletter import main
    code = main([
        "main_newsletter.py",
        "--dry-run",
        "--window-end",
        "2026-11-09",
        "--window-start",
        "2026-11-02T15:00:00Z",
    ])
    assert code == 0
    assert seen["status"] == "missing"
    assert seen["titles"] == ["Nvidia books a chip order"]
    html = (tmp_path / "out" / "2026-11-09.html").read_text()
    assert "Nvidia books a chip order" in html
    assert "Old October" not in html
    assert "confidential" not in html.lower()
    assert "together's internal" not in html.lower()
    assert not (tmp_path / "state" / "sent_issues.json").exists()
    assert not list((tmp_path / "out").glob("*.blocked.txt"))


def test_missing_sources_and_no_fresh_rss_blocks_without_sending(tmp_path, monkeypatch):
    _block_network(monkeypatch)
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))

    def fetcher(config, since, now):
        return FetchReport(
            articles=[],
            failures=[],
            successes=len(config.get("sources") or []),
            prior=[],
        )

    def wrapped(config, since, now, **kwargs):
        kwargs["fetcher"] = fetcher
        return prepare_candidates(config, since, now, **kwargs)

    monkeypatch.setattr("src.issue_run.prepare_candidates", wrapped)

    def composer(articles, api_key, model, **kwargs):
        assert articles == []
        return "", []

    monkeypatch.setattr("src.issue_run.compose_briefing", composer)
    from main_newsletter import main
    code = main(["main_newsletter.py", "--dry-run", "--window-end", "2026-11-09"])
    assert code == 2
    blocked = (tmp_path / "out" / "2026-11-09.blocked.txt").read_text()
    assert "too short" in blocked or "no stories" in blocked
    assert not list((tmp_path / "out").glob("*.html"))
    assert not (tmp_path / "state" / "sent_issues.json").exists()


def test_private_together_copy_blocks_the_dry_run(tmp_path, monkeypatch):
    _block_network(monkeypatch)
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    fresh = _article(
        "A public chip order",
        "https://www.reuters.com/technology/public-chip-order-2026-11-08/",
        datetime(2026, 11, 8, 16, tzinfo=timezone.utc),
        "Nvidia booked a public chip order. Reuters reported the order on November 8.",
    )

    def fetcher(config, since, now):
        return FetchReport(articles=[fresh], failures=[], successes=8, prior=[])

    def wrapped(config, since, now, **kwargs):
        kwargs["fetcher"] = fetcher
        return prepare_candidates(config, since, now, **kwargs)

    def composer(articles, api_key, model, **kwargs):
        article = articles[0]
        return (
            "A public lede.",
            [(
                article,
                "A public chip order",
                "Together's internal revenue was discussed beside the chip order. That figure is not public.",
                "People building with AI would be guessing.",
            )],
        )

    monkeypatch.setattr("src.issue_run.prepare_candidates", wrapped)
    monkeypatch.setattr("src.issue_run.compose_briefing", composer)
    from main_newsletter import main
    code = main(["main_newsletter.py", "--dry-run", "--window-end", "2026-11-09"])
    assert code == 2
    blocked = (tmp_path / "out" / "2026-11-09.blocked.txt").read_text().lower()
    assert "together" in blocked
    assert not list((tmp_path / "out").glob("*.html"))


def test_workflow_yaml_parses_and_a_miss_fails_the_job():
    from pathlib import Path
    import yaml
    text = Path(".github/workflows/briefing.yml").read_text()
    data = yaml.safe_load(text)
    assert data["concurrency"]["group"] == "ai-briefing"
    assert data["concurrency"]["cancel-in-progress"] is False
    assert "send_from: \"\"" in Path("schedule.yaml").read_text()
    dispatch = data[True]["workflow_dispatch"]["inputs"]
    assert dispatch["dry_run"]["default"] is True
    assert dispatch["allow_off_monday"]["default"] is False
    crons = [item["cron"] for item in data[True]["schedule"]]
    assert "0 16-23 * * 1" in crons
    assert "0 0-7 * * 2" in crons
    assert "30 8 * * 2,3,4" in crons
    steps = data["jobs"]["newsletter"]["steps"]
    miss = next(step for step in steps if step.get("name") == "Monday issue was not sent")
    assert "exit 1" in miss["run"]
    assert "::error title=Monday AI News was not sent::" in miss["run"]
    assert "does not send email" in miss["run"]
