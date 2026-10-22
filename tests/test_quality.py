"""Discovery, full-text ranking inputs, memory, and the Oct 16-19 window."""
import json
from datetime import datetime, timezone
from pathlib import Path

from src.clean import line_is_grounded
from src.discover import prepare_candidates
from src.expect import coverage_report, headline_matches, load_expect_file
from src.exa_search import (
    DISCOVERY_QUERIES,
    ExaBudget,
    article_from_result,
    fetch_contents,
    search_queries,
)
from src.fetch import parse_huggingface
from src.models import Article, FetchReport
from src.rank import score_article, select_items, shortlist_for_reading
from src.read import extract_details, format_details, read_articles
from src.state import load_recent, remember_briefing
from src.summarize import compose_briefing

NOW = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)
FIXTURE = Path("tests/fixtures/window_2026_10_19.json")
EXPECTED = Path("tests/fixtures/window_2026_10_19_expected.txt")


def _load_window():
    rows = json.loads(FIXTURE.read_text())
    articles = []
    for row in rows:
        published = datetime.fromisoformat(row["published_at"])
        data = dict(row)
        data["published_at"] = published
        articles.append(Article(**data))
    return articles


class _Response:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


def test_exa_cap_stops_search_and_keeps_a_contents_call():
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append((url, json["query"] if "query" in (json or {}) else json.get("urls")))
        if "query" in (json or {}):
            return _Response(200, {"results": [{
                "url": "https://www.politico.com/news/anthropic-bill",
                "title": "Anthropic pitches an AI safety bill",
                "publishedDate": "2026-10-17T14:00:00.000Z",
            }]})
        return _Response(200, {"results": [{"url": json["urls"][0], "text": "Full bill text."}]})

    budget = ExaBudget(2)
    found = search_queries(
        "exa-test",
        SINCE,
        NOW,
        budget,
        queries=("query one", "query two", "query three"),
        post=post,
    )
    assert len(found) == 1
    assert budget.used == 1
    mapping = fetch_contents("exa-test", [found[0].url], budget, post=post)
    assert mapping[found[0].url] == "Full bill text."
    assert budget.used == 2
    assert fetch_contents("exa-test", ["https://example.com/more"], budget, post=post) == {}
    assert len(calls) == 2
    assert "Exa calls: 2/2" in budget.status_line()


def test_exa_credits_error_stops_without_raising():
    def post(url, headers=None, json=None, timeout=None):
        return _Response(402, "You are out of credits")

    budget = ExaBudget(30)
    found = search_queries("exa-test", SINCE, NOW, budget, queries=("one", "two"), post=post)
    assert found == []
    assert budget.used == 1
    assert budget.stop_reason == "credits"


def test_exa_drops_social_and_old_dates():
    now = NOW
    kept = article_from_result({
        "url": "https://www.cnbc.com/story",
        "title": "Tenstorrent raises money for AI chips",
        "publishedDate": "2026-10-17T00:00:00.000Z",
        "text": "Tenstorrent raised $1.5 billion.",
    }, SINCE, now)
    assert kept is not None
    assert kept.source_name == "CNBC"
    assert "1.5 billion" in kept.full_text
    assert article_from_result({
        "url": "https://x.com/someone/status/1",
        "title": "Tenstorrent raises",
        "publishedDate": "2026-10-17T00:00:00.000Z",
    }, SINCE, now) is None
    assert article_from_result({
        "url": "https://www.cnbc.com/old",
        "title": "Old chip story",
        "publishedDate": "2026-09-01T00:00:00.000Z",
    }, SINCE, now) is None


def test_queries_cover_the_briefing_themes():
    blob = " ".join(DISCOVERY_QUERIES).lower()
    for needle in ("model release", "pricing", "inference", "open weights", "bill", "funding", "agent", "power"):
        assert needle in blob


def test_huggingface_parser_keeps_a_model_created_in_the_window():
    source = {
        "name": "DeepSeek",
        "domain": "huggingface.co",
        "kind": "lab",
        "priority": 1,
        "trending": False,
    }
    payload = [{
        "id": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
        "createdAt": "2026-10-17T02:40:38.000Z",
        "pipeline_tag": "image-text-to-text",
        "private": False,
    }]
    articles = parse_huggingface(source, payload)
    assert len(articles) == 1
    assert articles[0].title == "DeepSeek-V4.1-Flash-1017"
    assert articles[0].url.endswith("deepseek-ai/DeepSeek-V4.1-Flash-1017")
    assert articles[0].published_at == datetime(2026, 10, 17, 2, 40, 38, tzinfo=timezone.utc)
    trending = dict(source, name="Hugging Face trending", trending=True, kind="release")
    speech = [{
        "id": "fishaudio/drama-3",
        "createdAt": "2026-10-17T00:00:00.000Z",
        "pipeline_tag": "text-to-speech",
    }]
    assert parse_huggingface(trending, speech) == []


def test_window_keyword_gate_drops_the_real_stories():
    articles = _load_window()
    selected = [article.title for article in select_items(articles, limit=6, material_score=6)]
    assert any(title.startswith("vLLM") for title in selected)
    assert any(title.startswith("SGLang") for title in selected)
    assert any("Tenstorrent" in title for title in selected)
    dropped = [
        "safety bill",
        "OpenCode",
        "Public Benefit",
        "DeepSeek",
        "Nova Scotia",
        "agent tools",
        "John Deere",
        "blocking AI",
        "Amazon expands",
    ]
    for needle in dropped:
        assert all(needle not in title for title in selected), needle
        assert score_article(next(article for article in articles if needle in article.title)) < 6


def test_shortlist_keeps_low_score_stories_and_drops_the_customer_story():
    articles = _load_window()
    short = shortlist_for_reading(articles, limit=15)
    titles = [article.title for article in short]
    for needle in (
        "safety bill",
        "OpenCode",
        "Public Benefit",
        "DeepSeek",
        "Nova Scotia",
        "Tenstorrent",
        "agent tools",
        "John Deere",
        "blocking AI",
        "Amazon expands",
        "vLLM",
        "SGLang",
    ):
        assert any(needle in title for title in titles), needle
    assert all("SentinelOne" not in title for title in titles)


def test_expected_match_does_not_treat_open_as_openai():
    article = Article(
        url="https://techcrunch.com/nvidia-agents",
        title="Nvidia launches new open models to run agents in the enterprise and on PCs",
        source_name="TechCrunch",
        source_domain="techcrunch.com",
        published_at=NOW,
        content_preview="Nvidia launched open-weight agent models.",
        source_kind="news",
    )
    article.full_text = (
        "Nvidia claims the model is faster than competing models from Anthropic and OpenAI "
        "when post-trained on agentic tasks."
    )
    assert not headline_matches("OpenAI agent incidents OpenCode key leak", article)
    assert not headline_matches("OpenAI Anthropic agent tool launches", article)


def test_a_sparse_source_keeps_its_only_story():
    from datetime import timedelta
    busy = []
    for index in range(12):
        busy.append(Article(
            url=f"https://techcrunch.com/{index}",
            title=f"TechCrunch AI agents item {index} launches a tool",
            source_name="TechCrunch",
            source_domain="techcrunch.com",
            published_at=NOW - timedelta(hours=index),
            source_kind="news",
            source_priority=2,
        ))
    bill = Article(
        url="https://www.politico.com/news/anthropic-bill",
        title="Anthropic quietly pitches its own AI safety bill to lawmakers",
        source_name="Politico",
        source_domain="politico.com",
        published_at=SINCE + timedelta(hours=2),
        source_kind="news",
        source_priority=2,
    )
    short = shortlist_for_reading(busy + [bill], limit=15)
    assert any("safety bill" in article.title for article in short)


def test_page_chrome_datelines_and_entities_are_stripped():
    from src.read import extract_html_text
    raw = (
        "<html><body>"
        "Skip Navigation Markets Stocks &amp;amp; Bonds. "
        "Published Mon, Oct 20 2026 9:00 AM EDT. "
        "Anthropic &amp; Nscale are in talks over a second multibillion dollar cloud deal this week."
        "</body></html>"
    )
    text = extract_html_text(raw)
    assert "Skip Navigation" not in text
    assert "Published" not in text
    assert "&amp;" not in text
    assert "Anthropic & Nscale" in text


def test_extract_html_ignores_nav_chrome_and_keeps_the_body():
    from src.read import extract_html_text
    raw = """
    <header>Repository navigation</header>
    <nav>Skip Navigation $2 Markets Stocks</nav>
    <div class="markdown-body">
      <p>vLLM v0.32.0 adds a generic parser endpoint for structured model output in this release.</p>
    </div>
    """
    text = extract_html_text(raw)
    assert "generic parser" in text
    assert "Skip Navigation" not in text
    assert "$2" not in text


def test_expected_headlines_match_the_fixture():
    articles = _load_window()
    expected = load_expect_file(EXPECTED)
    assert len(expected) == 7
    report = coverage_report(expected, articles)
    assert "NOT FOUND" not in report
    assert "7 of 7" in report
    assert headline_matches(expected[3], articles[3])


def test_extract_details_pulls_price_date_and_caveat():
    text = (
        "Tenstorrent raises $1.5 billion on October 17, 2026. "
        "MMLU score was 88. The figure is company-reported and not independently audited. "
        "Weights are open weights."
    )
    details = extract_details(text)
    rendered = format_details(details)
    assert "$1.5 billion" in rendered
    assert "October 17, 2026" in rendered
    assert "MMLU" in rendered
    assert "company-reported" in rendered
    assert "open weights" in rendered.lower()
    noisy = format_details(extract_details("Skip Navigation $2 Markets. " + text))
    assert "$2" not in noisy
    assert "$1.5 billion" in noisy


def test_read_articles_uses_direct_text_then_exa_for_thin_pages(monkeypatch):
    rich = Article(
        url="https://example.com/rich",
        title="Rich",
        source_name="Example",
        source_domain="example.com",
        published_at=NOW,
        content_preview="",
        full_text="",
    )
    thin = Article(
        url="https://www.reuters.com/thin",
        title="Thin",
        source_name="Reuters",
        source_domain="reuters.com",
        published_at=NOW,
        content_preview="short",
        full_text="",
    )

    def getter(url):
        assert "reuters" not in url
        paragraph = "Tenstorrent raises $1.5 billion. Company-reported. " + ("More detail. " * 40)
        return f"<article><p>{paragraph}</p></article>"

    seen = {}

    def contents(api_key, urls, budget):
        seen["urls"] = urls
        budget.acquire()
        return {urls[0]: "Reuters says Amazon expanded its AI chip program on October 18, 2026."}

    budget = ExaBudget(5)
    read_articles([rich, thin], exa_api_key="k", budget=budget, getter=getter, contents=contents)
    assert "1.5 billion" in rich.full_text
    assert "Prices" in rich.details
    assert seen["urls"] == [thin.url]
    assert "chip program" in thin.full_text
    assert budget.used == 1


def test_compose_ranks_from_full_text_and_checks_facts(monkeypatch):
    articles = _load_window()
    captured = {}

    def fake_call(prompt, api_key, model):
        captured["prompt"] = prompt
        captured["model"] = model
        start = prompt.find("Candidates:\n")
        blob = prompt[start + len("Candidates:\n"):]
        end = blob.find("\n\nReturn:")
        rows = json.loads(blob[:end]) if start >= 0 and end > 0 else []
        by_title = {row["title"]: row["index"] for row in rows}
        items = []
        deep = next((title for title in by_title if title.startswith("DeepSeek")), "")
        ten = next((title for title in by_title if title.startswith("Tenstorrent")), "")
        if deep:
            items.append({
                "index": by_title[deep],
                "headline": "DeepSeek released DeepSeek-V4.1-Flash-1017 as open weights.",
                "why_it_matters": "Serving stacks have a new open checkpoint to evaluate.",
            })
        if ten:
            items.append({
                "index": by_title[ten],
                "headline": "Tenstorrent raised $9 billion.",
                "why_it_matters": "A larger round would change who can build training chips.",
            })
        return json.dumps({
            "lede": "Open models, agent incidents, and a chip round landed in the same window.",
            "items": items,
        })

    monkeypatch.setattr("src.summarize._call_together", fake_call)
    lede, pairs = compose_briefing(
        articles,
        "sk-test",
        "zai-org/GLM-5.3",
        strict_auth=True,
        recent=[{"title": "Gemini 4 Argon", "url": "https://blog.google/gemini"}],
        limit=7,
    )
    assert "DeepSeek-V4.1-Flash-1017" in captured["prompt"]
    assert "The weights are open weights" in captured["prompt"]
    assert "Gemini 4 Argon" in captured["prompt"]
    assert captured["model"] == "zai-org/GLM-5.3"
    assert pairs[0][0].title.startswith("DeepSeek")
    assert "open weights" in pairs[0][1]
    # $9 billion is not in the source, so the what-line falls back.
    assert "9" not in pairs[1][1]
    assert "1.5" in pairs[1][1] or "Tenstorrent" in pairs[1][1]
    assert line_is_grounded(lede, "\n".join(row[0].full_text for row in pairs), analysis=True) or "mostly about" in lede


def test_llm_rank_path_posts_a_low_score_story_on_dry_run(tmp_path):
    from src.briefing import run_briefing

    articles = _load_window()
    config = {
        "lookback_fallback_hours": 80,
        "max_lookback_hours": 168,
        "state_retention_days": 21,
        "max_items": 7,
        "material_score": 6,
        "paper_min_score": 9,
        "max_per_source_in_briefing": 2,
        "max_per_signature": 1,
        "together_model": "zai-org/GLM-5.3",
        "llm_rank": True,
        "read_full_text": False,
        "read_candidates": 15,
        "sources": [{"name": "Politico"}, {"name": "DeepSeek"}, {"name": "vLLM"}],
    }

    def composer(candidates, api_key, model, *, strict_auth, recent, limit, **kwargs):
        chosen = [article for article in candidates if "safety bill" in article.title or article.title.startswith("DeepSeek")]
        assert any(len(article.full_text) > 80 for article in candidates)
        return (
            "A policy draft and an open model are the pieces that matter for people shipping AI systems.",
            [
                (article, article.title, "Builders should read the primary source before changing a stack.")
                for article in chosen
            ],
        )

    def boom(*args, **kwargs):
        raise AssertionError("Slack was called")

    text = run_briefing(
        config,
        dry_run=True,
        api_key="sk-test",
        webhook_url="https://hooks.slack.com/services/T/B/secret",
        now=NOW,
        seen_path=tmp_path / "seen.json",
        last_run_path=tmp_path / "last.json",
        recent_path=tmp_path / "recent.json",
        fetcher=lambda config, since, now: FetchReport(articles=articles, failures=[], successes=3),
        poster=boom,
        composer=composer,
        expect_headlines=["Anthropic federal bill Politico", "DeepSeek V4.1-Flash-1017"],
    )
    assert "safety bill" in text
    assert "DeepSeek" in text
    assert "For people building" in text or "policy draft" in text
    assert "\u2014" not in text
    assert not (tmp_path / "seen.json").exists()
    assert not (tmp_path / "recent.json").exists()


def test_since_override_is_passed_to_the_fetcher(tmp_path):
    from src.briefing import run_briefing

    seen = {}

    def fetcher(config, since, now):
        seen["since"] = since
        return FetchReport(articles=[], failures=[], successes=3)

    config = {
        "lookback_fallback_hours": 80,
        "max_lookback_hours": 168,
        "state_retention_days": 21,
        "max_items": 6,
        "together_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "sources": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
    }
    run_briefing(
        config,
        dry_run=True,
        api_key=None,
        webhook_url=None,
        now=NOW,
        seen_path=tmp_path / "seen.json",
        last_run_path=tmp_path / "last.json",
        fetcher=fetcher,
        since_override=SINCE,
    )
    assert seen["since"] == SINCE


def test_live_post_remembers_titles_and_keeps_five(tmp_path):
    path = tmp_path / "recent.json"
    moment = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for index in range(6):
        article = Article(
            url=f"https://example.com/{index}",
            title=f"Story {index}",
            source_name="Example",
            source_domain="example.com",
            published_at=moment,
        )
        remember_briefing(moment, [article], path)
    rows = load_recent(path)
    assert len(rows) == 5
    titles = [row["items"][0]["title"] for row in rows]
    assert titles == ["Story 1", "Story 2", "Story 3", "Story 4", "Story 5"]
    assert "https://example.com/5" in rows[-1]["items"][0]["url"]


def test_prepare_falls_back_when_exa_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("EXA_ENABLED", "1")
    monkeypatch.delenv("EXA_MAX_CALLS", raising=False)
    feed_article = _load_window()[0]

    def fetcher(config, since, now):
        return FetchReport(articles=[feed_article], failures=["CNBC: timeout"], successes=2)

    def searcher(api_key, since, now, budget, **kwargs):
        budget.acquire()
        budget.stop_reason = "credits"
        return []

    config = {"sources": [{}, {}, {}], "read_full_text": False, "read_candidates": 15, "exa_max_calls": 30}
    result = prepare_candidates(
        config,
        SINCE,
        NOW,
        exa_api_key="k",
        fetcher=fetcher,
        searcher=searcher,
    )
    assert any("safety bill" in article.title for article in result.pool)
    assert result.exa_count == 0
    assert result.exa_calls == 1
    assert result.stop_reason if False else result.exa_note == "credits"


def test_config_and_workflow_name_the_new_sources():
    import yaml
    config = yaml.safe_load(Path("config.yaml").read_text())
    names = {source["name"] for source in config["sources"]}
    for name in (
        "Politico", "CNBC", "TechCrunch", "Fortune", "DeepSeek", "Qwen", "Moonshot",
        "Hugging Face trending", "Investing.com", "Federal Register", "Meta Engineering", "MiniMax",
    ):
        assert name in names
    assert config["together_model"] == "zai-org/GLM-5.3"
    assert config["llm_rank"] is True
    assert config["read_candidates"] == 20
    assert config["exa_max_calls"] == 6
    workflow = Path(".github/workflows/briefing.yml").read_text()
    assert "EXA_API_KEY" in workflow
    assert "EXA_MAX_CALLS" in workflow
    assert "actions/cache/restore" in workflow
    assert "actions/cache/save" in workflow
    assert "git commit" not in workflow
    script = Path("scripts/discovery_dry_run.py").read_text()
    assert "post_to_slack" not in script
    assert "SLACK_WEBHOOK" not in script
    assert "api.together" not in script
    assert "compose_briefing" not in script
    assert "TOGETHER_API_KEY" not in script
    readme = Path("README.md").read_text()
    assert "EXA_API_KEY" in readme
    assert "EXA_MAX_CALLS" in readme
    assert "scripts/discovery_dry_run.py" in readme
    assert "--since" in readme


def test_discovery_main_prints_shortlist_without_posting(monkeypatch, capsys):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    article = _load_window()[3]
    from src.discover import DiscoveryResult
    import scripts.discovery_dry_run as discovery

    def fake_prepare(config, since, now, **kwargs):
        assert kwargs.get("exa_api_key") in (None, "")
        assert config["read_full_text"] is True
        return DiscoveryResult(
            pool=[article],
            shortlist=[article],
            feed_count=1,
            exa_count=0,
            exa_calls=0,
            exa_cap=30,
            exa_note="no EXA_API_KEY, feeds only",
        )

    monkeypatch.setattr(discovery, "prepare_candidates", fake_prepare)
    code = discovery.main([
        "discovery_dry_run.py",
        "--since",
        "2026-10-16T15:00:00Z",
        "--expect",
        str(EXPECTED),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "DeepSeek-V4.1-Flash-1017" in out
    assert "Exa calls: 0/30" in out
    assert "FOUND" in out
    assert "hooks.slack.com" not in out
