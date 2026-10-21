import json
from datetime import datetime, timezone

from src.issue_run import generate_issue, parse_window_end, previous_mwf_slot
from src.models import Article, BriefingItem, FetchReport, Issue
from src.newsletter import (
    _pt_stamp,
    email_headline,
    email_subject,
    render_email,
    render_mdx,
    render_plain,
    render_slack,
    slack_contains_story,
)
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
    assert "https://nikibirkner.com/ai-news/2026-10-19" in html
    assert "https://www.nikibirkner.com/newsletter" not in html
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in html
    assert "AI News" in html
    assert "Niki Birkner" in html
    assert "Monday, October 19, 2026" in html
    assert 'width="600"' in html
    assert "max-width:600px" in html
    plain = render_plain(issue)
    assert item.what_happened in plain
    assert "71.5%" in plain
    assert item.why_it_matters in plain
    assert item.url in plain
    assert "https://nikibirkner.com/ai-news/2026-10-19" in plain
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in plain
    assert "<html" not in plain.lower()
    assert "for Together" not in slack
    assert "for Together" not in mdx
    assert "for Together" not in html


def test_rank_prompt_has_no_together_angle():
    prompt = _rank_prompt([_article()], [], 7).lower()
    assert "people building with ai" in prompt
    assert "for together" not in prompt
    assert "together ai" not in prompt
    assert "headline" in prompt
    assert "2 to 3" in prompt
    assert "bring it up this week" in prompt
    assert "revenue" in prompt
    assert "worth a look" in prompt
    assert "most of the loss is non-cash" not in prompt


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


def test_window_start_overrides_the_previous_slot(monkeypatch):
    seen = {}

    def fake_generate(config, **kwargs):
        seen["since"] = kwargs["since"]
        seen["until"] = kwargs["until"]
        return _issue(), [], type("Discovered", (), {"exa_search_calls": 0, "exa_content_pages": 0})()

    monkeypatch.setattr("main_newsletter.generate_issue", fake_generate)
    monkeypatch.setenv("OUT_DIR", "/tmp/window-start-test")
    from main_newsletter import main
    code = main([
        "main_newsletter.py",
        "--dry-run",
        "--window-start",
        "2026-10-19T15:00:00Z",
        "--window-end",
        "2026-10-20T18:00:00Z",
    ])
    assert code == 0
    assert seen["since"] == datetime(2026, 10, 19, 15, tzinfo=timezone.utc)
    assert seen["until"] == datetime(2026, 10, 20, 18, tzinfo=timezone.utc)


def test_pacific_stamp_keeps_monday_morning_on_monday():
    assert _pt_stamp(datetime(2026, 10, 19, 10, 6, tzinfo=timezone.utc)) == "Mon Oct 19, 3:06 AM PT"
    assert _pt_stamp(datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)) == "Mon Oct 19, 5:00 AM PT"
    assert _pt_stamp(datetime(2026, 10, 19, 13, 0, tzinfo=timezone.utc)) == "Mon Oct 19, 6:00 AM PT"
    assert _pt_stamp(datetime(2026, 10, 20, 13, 0, tzinfo=timezone.utc)) == "Tue Oct 20, 6:00 AM PT"
    assert _pt_stamp(datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)) == "Mon Oct 19, 8:00 AM PT"
    assert _pt_stamp(datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)) == "Tue Oct 20, 11:00 AM PT"


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
    text = (tmp_path / "2026-10-19.txt").read_text()
    assert "Tokens and dollars are splitting" in text
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in text
    assert "https://nikibirkner.com/ai-news/2026-10-19" in text
    assert "sk-personal-test" not in text
    assert "sk-personal-test" not in mdx
    assert "sk-personal-test" not in html
    assert "sk-personal-test" not in usage
    assert "exa-test" not in usage
    assert "llm_calls: 0" in usage
    assert "exa_search_calls: 2" in usage
    assert "sends: none" in usage
    assert not (tmp_path / "2026-10-19.blocked.txt").exists()


def test_generate_issue_publishes_nothing_when_ranking_times_out(monkeypatch):
    import requests

    def boom(*args, **kwargs):
        raise requests.Timeout("read timeout=120")

    monkeypatch.setattr("src.summarize._call_together", boom)
    issue, failures, _discovered = generate_issue(
        {"max_items": 7, "together_model": "zai-org/GLM-5.3", "sources": [{}, {}, {}], "read_full_text": False},
        since=SINCE,
        until=NOW,
        api_key="sk-test",
        fetcher=lambda config, since, now: FetchReport(articles=[_article()], failures=[], successes=3),
        reader=lambda *args, **kwargs: None,
    )
    assert issue.items == []
    assert any("Not publishing" in reason for reason in failures)
    assert all("Skip Navigation" not in reason for reason in failures)
    assert all("71.5%" not in reason for reason in failures)


def test_a_ranking_failure_writes_a_quiet_note_and_exits(tmp_path, monkeypatch):
    issue = _issue()
    issue.items = []

    class Discovered:
        exa_search_calls = 0
        exa_content_pages = 0

    def fake_generate(config, **kwargs):
        return issue, ["Ranking failed (read timeout=120). Not publishing."], Discovered()

    monkeypatch.setattr("main_newsletter.generate_issue", fake_generate)
    monkeypatch.setenv("TOGETHER_API_KEY", "sk-personal-test")
    monkeypatch.setenv("OUT_DIR", str(tmp_path))
    from main_newsletter import main
    code = main(["main_newsletter.py", "--dry-run", "--window-end", "2026-10-19"])
    assert code == 2
    note = (tmp_path / "2026-10-19.blocked.txt").read_text()
    assert "Not publishing" in note
    assert not (tmp_path / "2026-10-19.mdx").exists()
    assert not (tmp_path / "2026-10-19.html").exists()
    assert not (tmp_path / "2026-10-19.txt").exists()


def test_email_is_inline_table_layout_with_dark_mode_and_unsubscribe_fallback():
    issue = _issue()
    issue.title = "<script>alert(1)</script>"
    issue.items[0].what_happened = "A <b>tag</b> & more"
    issue.items[0].url = "javascript:alert(1)"
    html = render_email(issue)
    assert "<script>" not in html
    assert "alert(1)" not in html
    assert "<title>AI News, October 19, 2026</title>" in html
    assert "A &lt;b&gt;tag&lt;/b&gt; &amp; more" in html
    assert "javascript:" not in html
    assert 'role="presentation"' in html
    assert "color-scheme" in html
    assert "prefers-color-scheme: dark" in html
    assert "#111111" in html
    assert "#F1F1EF" in html
    assert "#0F0F0F" in html
    assert "Unsubscribe" in html
    assert html.count("{{{RESEND_UNSUBSCRIBE_URL}}}") == 1
    assert 'style="' in html

    preview = render_email(issue, broadcast=False)
    plain = render_plain(issue, broadcast=False)
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" not in preview
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" not in plain
    assert 'href="https://nikibirkner.com/ai-news"' in preview
    assert "Unsubscribe\nhttps://nikibirkner.com/ai-news\n" in plain
    assert "Vercel" in preview
    assert ">Vercel</a>" not in preview


def test_email_uses_the_site_mark_orange_and_fonts():
    html = render_email(_issue())
    assert 'src="https://nikibirkner.com/apple-touch-icon.png"' in html
    assert 'alt="Niki Birkner"' in html
    assert 'width="36"' in html
    assert "#C2410C" in html
    assert "#FED7AA" in html
    assert "#FB923C" in html
    assert "Schibsted Grotesk" in html
    assert "Fragment Mono" in html
    assert "Helvetica Neue" in html
    assert "@font-face" in html
    assert "fonts.googleapis.com" in html
    assert 'class="accent"' in html
    assert ">AI News</p>" in html


def test_forwarded_readers_are_invited_to_subscribe():
    html = render_email(_issue())
    plain = render_plain(_issue())
    top = html[html.index("Forwarded this?"):html.index(">AI News</p>")]
    assert 'href="https://www.nikibirkner.com/ai-news/"' in top
    assert ">nikibirkner.com/ai-news</a>" in top
    assert "color:#C2410C" in top
    assert "Fragment Mono" in top
    assert plain.startswith(
        "Forwarded this? Subscribe at nikibirkner.com/ai-news\n"
        "https://www.nikibirkner.com/ai-news/\n"
    )


def test_email_headline_and_subject_use_the_issue_date():
    issue = _issue()
    issue.title = "AI briefing, Monday, October 19"
    assert email_headline(issue) == "AI News, October 19, 2026"
    assert email_subject(issue) == "AI News, October 19, 2026"
    html = render_email(issue)
    plain = render_plain(issue)
    assert "<title>AI News, October 19, 2026</title>" in html
    assert ">AI News, October 19, 2026</h1>" in html
    assert "AI briefing" not in html
    assert "AI briefing" not in plain
    assert "AI News, October 19, 2026" in plain
    issue.date = "2026-10-05"
    assert email_headline(issue) == "AI News, October 5, 2026"
    assert "Monday" not in email_subject(issue)


def test_source_link_is_the_publication_name():
    html = render_email(_issue())
    assert 'href="https://vercel.com/blog/ai-gateway-production-index-october-2026"' in html
    assert ">Vercel</a>" in html
    assert "Vercel · vercel.com" not in html


def test_cli_refuses_send_without_resend_secrets(monkeypatch):
    from main_newsletter import main
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_AUDIENCE_ID", raising=False)
    monkeypatch.setattr(
        "main_newsletter.generate_issue",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not generate")),
    )
    assert main(["main_newsletter.py", "--send", "--window-end", "2026-10-19"]) == 2
