import json
from datetime import datetime, timezone

from src.issue_run import generate_issue, parse_window_end, previous_mwf_slot
from src.models import Article, BriefingItem, FetchReport, Issue
from src.newsletter import render_email, render_mdx, render_slack, slack_contains_story
from src.summarize import SchemaError, _rank_prompt, compose_briefing


NOW = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)


def _article():
    return Article(
        url="https://vercel.com/blog/ai-gateway-production-index-october-2026",
        title="Open-weight models took most of the tokens",
        source_name="Vercel",
        source_domain="vercel.com",
        published_at=datetime(2026, 10, 15, 16, tzinfo=timezone.utc),
        content_preview="Vercel published a gateway report.",
        full_text=(
            "Vercel's October report says open-weight models handled 71.5% of tokens. "
            "Those models were 23% of spend. "
            "DeepSeek V4.1 Flash handled nearly half of September's tokens for 2.6% of the dollars."
        ),
        source_kind="news",
        source_priority=2,
    )


def _issue():
    return Issue(
        title="AI briefing, October 19",
        date="2026-10-19",
        summary="Tokens and dollars moved apart in this window.",
        tags=["models"],
        items=[BriefingItem(
            what_happened="Tokens and dollars are splitting",
            body=_article().full_text,
            why_it_matters="For people building with AI, a cheap token and an expensive closed model are different bills.",
            url=_article().url,
            source_name="Vercel",
        )],
        since=SINCE,
        until=NOW,
    )


def test_slack_mdx_and_email_come_from_the_same_issue():
    issue = _issue()
    slack = render_slack(issue)
    mdx = render_mdx(issue)
    html = render_email(issue)
    item = issue.items[0]
    assert slack_contains_story(issue, slack)
    for text in (mdx, html, slack):
        assert item.what_happened in text
        assert "71.5%" in text
        assert item.why_it_matters in text
    assert item.url in mdx
    assert item.url in html
    assert f'title: "AI briefing, October 19"' in mdx
    assert "date: 2026-10-19" in mdx
    assert "summary:" in mdx
    assert "tags: [models]" in mdx
    assert "https://www.nikibirkner.com/newsletter/2026-10-19" in html
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in html
    assert "for Together" not in slack
    assert "for Together" not in mdx
    assert "for Together" not in html


def test_rank_prompt_has_no_together_angle():
    prompt = _rank_prompt([_article()], [], 7).lower()
    assert "people building with ai" in prompt
    assert "for together" not in prompt
    assert "together ai" not in prompt
    assert "headline" in prompt
    assert "3 to 5" in prompt


def test_schema_failure_raises_when_a_full_body_is_required(monkeypatch):
    def fake_call(prompt, api_key, model):
        return json.dumps({"lede": "Hi", "items": [{"index": 0, "what_happened": "Only one line."}]})

    monkeypatch.setattr("src.summarize._call_together", fake_call)
    try:
        compose_briefing(
            [_article()],
            "sk-test",
            "zai-org/GLM-5.3",
            strict_auth=True,
            require_full_body=True,
        )
        assert False, "expected SchemaError"
    except SchemaError as exc:
        assert "schema validation" in str(exc)


def test_window_end_dates_match_the_mwf_cadence():
    assert parse_window_end("2026-10-19") == NOW
    assert previous_mwf_slot(parse_window_end("2026-10-19")) == datetime(2026, 10, 16, 15, tzinfo=timezone.utc)
    assert previous_mwf_slot(parse_window_end("2026-10-16")) == datetime(2026, 10, 14, 15, tzinfo=timezone.utc)
    assert previous_mwf_slot(parse_window_end("2026-10-12")) == datetime(2026, 10, 9, 15, tzinfo=timezone.utc)


def test_generate_issue_drops_a_generic_release_and_writes_nothing_external(monkeypatch):
    rich = _article()
    thin = Article(
        url="https://github.com/vllm-project/vllm/releases/tag/v0.32.0",
        title="vLLM v0.32.0",
        source_name="vLLM",
        source_domain="github.com",
        published_at=datetime(2026, 10, 17, 9, tzinfo=timezone.utc),
        full_text="vLLM v0.32.0. It adds new features.",
        source_kind="release",
        source_priority=2,
    )

    def composer(articles, api_key, model, **kwargs):
        return "", [
            (rich, "Tokens and dollars are splitting", rich.full_text, "For people building with AI, price and volume split."),
            (thin, "vLLM v0.32.0", "It adds new features.", "For people building with AI, a release note with no details can wait."),
        ]

    def boom(*args, **kwargs):
        raise AssertionError("network post")

    monkeypatch.setattr("src.issue_run.post_to_slack", boom, raising=False)
    issue, failures, discovered = generate_issue(
        {"max_items": 7, "together_model": "zai-org/GLM-5.3", "sources": [{}, {}, {}], "read_full_text": False},
        since=SINCE,
        until=NOW,
        api_key="sk-test-should-not-leak",
        composer=composer,
        fetcher=lambda config, since, now: FetchReport(articles=[rich, thin], failures=[], successes=3),
        reader=lambda *args, **kwargs: None,
    )
    assert failures == []
    assert len(issue.items) == 1
    assert issue.items[0].url == rich.url
    assert "vLLM" not in issue.items[0].what_happened
    assert discovered.successes == 3


def test_generate_issue_blocks_together_framing(monkeypatch):
    article = _article()

    def composer(articles, api_key, model, **kwargs):
        return "A public lede.", [
            (
                article,
                "Tokens and dollars are splitting",
                article.full_text,
                "Why it matters for Together: customer revenue is the story.",
            )
        ]

    issue, failures, _discovered = generate_issue(
        {"max_items": 7, "together_model": "zai-org/GLM-5.3", "sources": [{}, {}, {}]},
        since=SINCE,
        until=NOW,
        api_key=None,
        composer=composer,
        fetcher=lambda config, since, now: FetchReport(articles=[article], failures=[], successes=3),
        reader=lambda *args, **kwargs: None,
    )
    assert issue.items == []
    assert "together internal or non-public" in failures


def test_cli_writes_out_and_never_sends(tmp_path, monkeypatch):
    issue = _issue()

    class Discovered:
        exa_search_calls = 2
        exa_content_pages = 1

    def fake_generate(config, **kwargs):
        assert kwargs["until"] == NOW
        assert kwargs["since"] == SINCE
        assert kwargs["api_key"] == "sk-personal-test"
        return issue, [], Discovered()

    monkeypatch.setattr("main_newsletter.generate_issue", fake_generate)
    monkeypatch.setenv("TOGETHER_API_KEY", "sk-personal-test")
    monkeypatch.setenv("EXA_API_KEY", "exa-test")
    monkeypatch.setenv("OUT_DIR", str(tmp_path))
    posted = []
    monkeypatch.setattr("src.slack.requests.post", lambda *args, **kwargs: posted.append(args))

    from main_newsletter import main
    code = main(["main_newsletter.py", "--dry-run", "--window-end", "2026-10-19"])
    assert code == 0
    assert posted == []
    mdx = (tmp_path / "2026-10-19.mdx").read_text()
    html = (tmp_path / "2026-10-19.html").read_text()
    usage = (tmp_path / "2026-10-19.usage.txt").read_text()
    assert "Tokens and dollars are splitting" in mdx
    assert "Tokens and dollars are splitting" in html
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in html
    assert "sk-personal-test" not in mdx
    assert "sk-personal-test" not in html
    assert "sk-personal-test" not in usage
    assert "exa-test" not in usage
    assert "llm_calls: 0" in usage
    assert "exa_search_calls: 2" in usage
    assert "sends: none" in usage
    assert not (tmp_path / "2026-10-19.blocked.txt").exists()


def test_cli_refuses_send_flag(monkeypatch):
    from main_newsletter import main
    monkeypatch.setattr(
        "main_newsletter.generate_issue",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not generate")),
    )
    assert main(["main_newsletter.py", "--send", "--window-end", "2026-10-19"]) == 2
