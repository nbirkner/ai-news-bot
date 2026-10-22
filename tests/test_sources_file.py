"""Weekly sources file: schema, fallbacks, and the Exa gate. No network."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.discover import prepare_candidates
from src.exa_search import ExaBudget, resolve_cap, search_queries
from src.issue_run import generate_issue
from src.models import Article, FetchReport
from src.sources_file import (
    OPTIONAL_FIELDS,
    REQUIRED_FIELDS,
    SCHEMA,
    load_sources_file,
    sources_path_for,
    validate_sources,
    window_end_filename_date,
)
from src.summarize import _prompt, _rank_prompt

NOW = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)
MONDAY_PT = datetime(2026, 10, 19, 16, 0, tzinfo=timezone.utc)

FACTS = (
    "Use only facts from the provided public sources plus brief reflection. "
    "No invented facts."
)


def _block_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError(f"unexpected network call args={args!r} kwargs={kwargs!r}")

    monkeypatch.setattr("requests.get", boom)
    monkeypatch.setattr("requests.post", boom)
    monkeypatch.setattr("requests.Session.request", boom)


def _item(**overrides):
    row = {
        "title": "Anthropic publishes a workforce study",
        "url": "https://www.anthropic.com/news/workforce-study",
        "source": "Anthropic",
        "published": "2026-10-18T16:00:00Z",
        "summary": (
            "Anthropic published a workforce study of how coding agents change software teams. "
            "The study covers 12 companies."
        ),
        "reflection": "The lab post is the primary source.",
    }
    row.update(overrides)
    return row


def _rss():
    return Article(
        url="https://www.reuters.com/technology/openai-chip-order-2026-10-18/",
        title="Nvidia books a chip order from ByteDance",
        source_name="Reuters",
        source_domain="reuters.com",
        published_at=datetime(2026, 10, 18, 12, tzinfo=timezone.utc),
        content_preview="Nvidia booked a chip order from ByteDance worth tens of billions of dollars.",
        full_text=(
            "Nvidia booked a chip order from ByteDance worth tens of billions of dollars. "
            "Reuters reported the order on October 18."
        ),
        source_kind="news",
        source_priority=2,
    )


def _config():
    return {
        "max_items": 7,
        "together_model": "zai-org/GLM-5.3",
        "sources": [{}, {}, {}],
        "read_full_text": False,
        "read_candidates": 20,
        "exa_max_calls": 6,
    }


def _write(directory: Path, name: str, payload) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_window_end_date_is_the_pacific_monday():
    assert window_end_filename_date(NOW) == "2026-10-19"
    assert window_end_filename_date(MONDAY_PT) == "2026-10-19"
    assert window_end_filename_date(datetime(2026, 12, 14, 17, tzinfo=timezone.utc)) == "2026-12-14"
    # 06:30 UTC Tuesday is still Monday evening in Pacific daylight time.
    assert window_end_filename_date(datetime(2026, 10, 20, 6, 30, tzinfo=timezone.utc)) == "2026-10-19"
    assert sources_path_for(MONDAY_PT).as_posix() == "sources/2026-10-19.json"


def test_schema_lists_the_documented_fields():
    assert SCHEMA["type"] == "array"
    assert tuple(SCHEMA["items"]["required"]) == REQUIRED_FIELDS
    assert REQUIRED_FIELDS == ("title", "url", "source", "published", "summary")
    assert OPTIONAL_FIELDS == ("reflection",)
    assert set(SCHEMA["items"]["properties"]) == set(REQUIRED_FIELDS + OPTIONAL_FIELDS)


def test_valid_file_with_and_without_reflection(tmp_path):
    with_note = _write(tmp_path, "2026-10-19.json", [_item()])
    loaded = load_sources_file(with_note)
    assert loaded.status == "ok"
    assert loaded.error == ""
    assert len(loaded.articles) == 1
    article = loaded.articles[0]
    assert article.title == "Anthropic publishes a workforce study"
    assert article.url == "https://www.anthropic.com/news/workforce-study"
    assert article.source_name == "Anthropic"
    assert article.source_domain == "anthropic.com"
    assert article.published_at == datetime(2026, 10, 18, 16, tzinfo=timezone.utc)
    assert "12 companies" in article.full_text
    assert article.reflection == "The lab post is the primary source."
    assert article.from_sources_file is True

    bare = dict(_item())
    del bare["reflection"]
    bare["published"] = "2026-10-18"
    bare["note"] = "ignored extra field"
    loaded = load_sources_file(_write(tmp_path, "bare.json", [bare]))
    assert loaded.status == "ok"
    assert loaded.articles[0].reflection == ""
    assert loaded.articles[0].published_at == datetime(2026, 10, 18, tzinfo=timezone.utc)

    empty = load_sources_file(_write(tmp_path, "empty.json", []))
    assert empty.status == "ok"
    assert empty.articles == []
    assert validate_sources([]) == ""


def test_missing_file_is_not_an_error(tmp_path):
    path = tmp_path / "sources" / "2026-10-19.json"
    loaded = load_sources_file(path)
    assert loaded.status == "missing"
    assert loaded.articles == []
    assert loaded.error == "missing"
    assert not path.exists()


@pytest.mark.parametrize(
    "payload, reason",
    [
        ("{", "not valid JSON"),
        ("[", "not valid JSON"),
        ("null", "must be a JSON list"),
        ({"title": "not a list"}, "must be a JSON list"),
        (["just a string"], "item 0 must be an object"),
        ([_item(title="  ")], "item 0 title must be a non-empty string"),
        ([{k: v for k, v in _item().items() if k != "url"}], "item 0 missing url"),
        ([_item(url="ftp://example.com/a")], "item 0 url must be an http or https URL"),
        ([_item(url="https://example.com/a b")], "item 0 url must be an http or https URL"),
        ([_item(published="next Monday")], "item 0 published must be an ISO-8601 date or date-time"),
        ([_item(published="2026-13-40")], "item 0 published must be an ISO-8601 date or date-time"),
        ([_item(published=20261018)], "item 0 published must be an ISO-8601 date or date-time"),
        ([_item(summary="")], "item 0 summary must be a non-empty string"),
        ([_item(source=None)], "item 0 source must be a non-empty string"),
        ([_item(reflection=None)], "item 0 reflection must be a string"),
        ([_item(reflection=1)], "item 0 reflection must be a string"),
        ([_item(), _item(url="not a url")], "item 1 url must be an http or https URL"),
    ],
)
def test_malformed_file_is_invalid(tmp_path, payload, reason):
    path = _write(tmp_path, "bad.json", payload)
    loaded = load_sources_file(path)
    assert loaded.status == "invalid"
    assert loaded.articles == []
    assert loaded.error == reason


def _run_issue(tmp_path, monkeypatch, *, payload, fetcher, searcher, exa_key=None):
    _block_network(monkeypatch)
    monkeypatch.delenv("EXA_ENABLED", raising=False)
    monkeypatch.delenv("EXA_MAX_CALLS", raising=False)
    root = tmp_path / "sources"
    if payload is not None:
        _write(root, "2026-10-19.json", payload)
    else:
        root.mkdir()
    seen = {}

    def composer(articles, api_key, model, **kwargs):
        seen["articles"] = list(articles)
        seen["called"] = True
        return "A public lede.", []

    def wrapped_search(*args, **kwargs):
        seen["exa"] = seen.get("exa", 0) + 1
        seen["budget"] = args[3] if len(args) > 3 else kwargs.get("budget")
        return searcher(*args, **kwargs) if searcher else []

    issue, failures, discovered = generate_issue(
        _config(),
        since=SINCE,
        until=NOW,
        api_key="sk-test",
        exa_api_key=exa_key,
        fetcher=fetcher,
        searcher=wrapped_search,
        reader=lambda *args, **kwargs: None,
        composer=composer,
        sources_root=root,
        recent=[],
    )
    seen["issue"] = issue
    seen["failures"] = failures
    seen["discovered"] = discovered
    return seen


def test_newsletter_uses_sources_file_plus_rss_without_exa(tmp_path, monkeypatch):
    rss = _rss()

    def fetcher(config, since, now):
        return FetchReport(articles=[rss], failures=[], successes=3)

    seen = _run_issue(
        tmp_path,
        monkeypatch,
        payload=[_item()],
        fetcher=fetcher,
        searcher=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("exa")),
        exa_key="should-not-be-used",
    )
    titles = [article.title for article in seen["articles"]]
    assert "Anthropic publishes a workforce study" in titles
    assert rss.title in titles
    assert seen["discovered"].sources_status == "ok"
    assert seen["discovered"].curated_count == 1
    assert seen["discovered"].feed_count == 1
    assert seen["discovered"].exa_count == 0
    assert seen["discovered"].exa_calls == 0
    assert "EXA_ENABLED is not 1" in seen["discovered"].exa_note
    assert seen.get("exa", 0) == 0
    reflection = next(article.reflection for article in seen["articles"] if "workforce" in article.title)
    assert reflection == "The lab post is the primary source."


def test_missing_sources_file_falls_back_to_rss(tmp_path, monkeypatch):
    rss = _rss()
    seen = _run_issue(
        tmp_path,
        monkeypatch,
        payload=None,
        fetcher=lambda config, since, now: FetchReport(articles=[rss], failures=[], successes=3),
        searcher=None,
    )
    assert [article.title for article in seen["articles"]] == [rss.title]
    assert seen["discovered"].sources_status == "missing"
    assert seen["discovered"].curated_count == 0
    assert seen.get("exa", 0) == 0


def test_invalid_sources_file_falls_back_to_rss(tmp_path, monkeypatch):
    rss = _rss()
    seen = _run_issue(
        tmp_path,
        monkeypatch,
        payload=[_item(), {"title": "broken"}],
        fetcher=lambda config, since, now: FetchReport(articles=[rss], failures=[], successes=3),
        searcher=None,
    )
    assert [article.title for article in seen["articles"]] == [rss.title]
    assert seen["discovered"].sources_status == "invalid"
    assert "missing url" in seen["discovered"].sources_error
    assert seen["discovered"].curated_count == 0


def test_valid_sources_file_survives_a_feed_outage(tmp_path, monkeypatch):
    def fetcher(config, since, now):
        return FetchReport(articles=[], failures=["OpenAI: timeout"], successes=0)

    seen = _run_issue(
        tmp_path,
        monkeypatch,
        payload=[_item()],
        fetcher=fetcher,
        searcher=None,
    )
    assert seen["called"] is True
    assert [article.title for article in seen["articles"]] == ["Anthropic publishes a workforce study"]
    assert "Too many sources failed" not in " ".join(seen["failures"])


def test_exa_runs_only_when_enabled_and_stops_at_the_cap(tmp_path, monkeypatch):
    rss = _rss()
    monkeypatch.setenv("EXA_ENABLED", "1")
    monkeypatch.setenv("EXA_MAX_CALLS", "6")
    _block_network(monkeypatch)
    root = tmp_path / "sources"
    _write(root, "2026-10-19.json", [_item()])
    seen = {}

    def searcher(api_key, since, now, budget, **kwargs):
        seen["cap"] = budget.cap
        seen["calls"] = 0
        while budget.acquire():
            seen["calls"] += 1
        return []

    def composer(articles, api_key, model, **kwargs):
        seen["titles"] = [article.title for article in articles]
        return "", []

    discovered_holder = {}

    def fetcher(config, since, now):
        return FetchReport(articles=[rss], failures=[], successes=3)

    _issue, _failures, discovered = generate_issue(
        _config(),
        since=SINCE,
        until=NOW,
        api_key=None,
        exa_api_key="exa-test",
        fetcher=fetcher,
        searcher=searcher,
        reader=lambda *args, **kwargs: None,
        composer=composer,
        sources_root=root,
        recent=[],
    )
    discovered_holder["result"] = discovered
    assert seen["cap"] == 6
    assert seen["calls"] == 6
    assert discovered.exa_calls == 6
    assert discovered.exa_cap == 6
    assert "Anthropic publishes a workforce study" in seen["titles"]
    assert rss.title in seen["titles"]

    posts = []

    def post(url, headers=None, json=None, timeout=None):
        posts.append(url)
        return type("Response", (), {
            "status_code": 200,
            "text": "{}",
            "json": lambda self=None: {"results": [{
                "url": "https://www.cnbc.com/2026/10/18/chip-order",
                "title": "Nvidia chip order",
                "publishedDate": "2026-10-18T12:00:00.000Z",
                "text": "Nvidia booked the order.",
            }]},
        })()

    budget = ExaBudget(resolve_cap())
    found = search_queries(
        "exa-test",
        SINCE,
        NOW,
        budget,
        queries=tuple(f"query {index}" for index in range(20)),
        post=post,
    )
    assert budget.cap == 6
    assert budget.used <= 6
    assert len(posts) <= 6
    assert found


def test_exa_stays_off_for_prepare_without_the_flag(monkeypatch):
    monkeypatch.delenv("EXA_ENABLED", raising=False)
    called = {"search": 0}

    def searcher(*args, **kwargs):
        called["search"] += 1
        return []

    result = prepare_candidates(
        {"sources": [{}], "read_full_text": False, "read_candidates": 5, "exa_max_calls": 6},
        SINCE,
        NOW,
        exa_api_key="exa-test",
        fetcher=lambda config, since, now: FetchReport(articles=[_rss()], failures=[], successes=1),
        searcher=searcher,
        reader=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("reader")),
    )
    assert called["search"] == 0
    assert result.exa_count == 0
    assert result.exa_cap == 6
    assert "EXA_ENABLED is not 1" in result.exa_note


def test_briefing_is_rss_only_unless_exa_is_enabled(tmp_path, monkeypatch):
    from src.briefing import run_briefing

    _block_network(monkeypatch)
    monkeypatch.delenv("EXA_ENABLED", raising=False)
    root = tmp_path / "sources"
    _write(root, "2026-10-19.json", [_item(title="Curated story that briefing must ignore")])
    called = {"search": 0, "load": 0}

    def searcher(*args, **kwargs):
        called["search"] += 1
        return []

    def load_sources_file(*args, **kwargs):
        called["load"] += 1
        raise AssertionError("briefing loaded the weekly sources file")

    monkeypatch.setattr("src.sources_file.load_sources_file", load_sources_file)
    monkeypatch.setattr("src.issue_run.load_sources_file", load_sources_file)
    rss = _rss()
    text = run_briefing(
        {
            "lookback_fallback_hours": 80,
            "max_lookback_hours": 168,
            "state_retention_days": 21,
            "max_items": 7,
            "llm_rank": False,
            "material_score": 1,
            "paper_min_score": 1,
            "max_per_source_in_briefing": 2,
            "max_per_signature": 1,
            "together_model": "zai-org/GLM-5.3",
            "sources": [{}, {}, {}],
            "read_full_text": False,
        },
        dry_run=True,
        api_key=None,
        webhook_url=None,
        now=NOW,
        seen_path=tmp_path / "seen.json",
        last_run_path=tmp_path / "last.json",
        recent_path=tmp_path / "recent.json",
        fetcher=lambda config, since, now: FetchReport(articles=[rss], failures=[], successes=3),
        enricher=lambda items: None,
        exa_api_key="should-not-be-used",
        searcher=searcher,
        reader=lambda *args, **kwargs: None,
        since_override=SINCE,
    )
    assert called == {"search": 0, "load": 0}
    assert "Curated story" not in text
    assert "Nvidia" in text

    monkeypatch.setenv("EXA_ENABLED", "1")
    run_briefing(
        {
            "lookback_fallback_hours": 80,
            "max_lookback_hours": 168,
            "state_retention_days": 21,
            "max_items": 7,
            "llm_rank": False,
            "material_score": 1,
            "paper_min_score": 1,
            "max_per_source_in_briefing": 2,
            "max_per_signature": 1,
            "together_model": "zai-org/GLM-5.3",
            "sources": [{}, {}, {}],
            "read_full_text": False,
        },
        dry_run=True,
        api_key=None,
        webhook_url=None,
        now=NOW,
        seen_path=tmp_path / "seen.json",
        last_run_path=tmp_path / "last.json",
        recent_path=tmp_path / "recent.json",
        fetcher=lambda config, since, now: FetchReport(articles=[rss], failures=[], successes=3),
        enricher=lambda items: None,
        exa_api_key="exa-test",
        searcher=searcher,
        reader=lambda *args, **kwargs: None,
        since_override=SINCE,
    )
    assert called["search"] == 1
    assert called["load"] == 0


def test_writing_prompts_allow_only_sources_and_reflection():
    article = _rss()
    article.reflection = "Primary lab post, not a recap."
    rank = _rank_prompt([article], [], 7)
    write = _prompt([article])
    for prompt in (rank, write):
        assert FACTS in prompt
        assert "Primary lab post, not a recap." in prompt
    assert '"reflection": "Primary lab post, not a recap."' in rank


def test_readme_documents_the_schema_and_the_exa_gate():
    readme = Path("README.md").read_text()
    sources = Path("sources/README.md").read_text()
    for text in (readme, sources):
        for field in ("title", "url", "source", "published", "summary", "reflection"):
            assert f"`{field}`" in text
        assert "ISO-8601" in text
        assert "JSON list" in text
    assert "EXA_ENABLED=1" in readme
    assert "EXA_MAX_CALLS" in readme
    assert "default **6**" in readme
    workflow = Path(".github/workflows/briefing.yml").read_text()
    assert "EXA_ENABLED" in workflow
    assert "'6'" in workflow
    assert "main_briefing.py" in readme
