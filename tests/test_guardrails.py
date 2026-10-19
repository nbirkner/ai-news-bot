"""One test per publish guardrail, plus the channel no-op paths."""
import base64
from datetime import datetime, timezone

import pytest

from src.briefing import BriefingError, run_briefing
from src.guardrails import (
    check_idempotency,
    check_safety,
    check_schema,
    check_source_url,
    check_source_urls,
    check_substance,
    check_together_private,
    evaluate_issue,
)
from src.models import Article, BriefingItem, FetchReport
from src.newsletter import build_issue, frontmatter, render_email_markdown, render_mdx
from src.summarize import SchemaError, _rank_prompt, compose_briefing

NOW = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
WEBHOOK = "https://hooks.slack.com/services/T/B/test"


def _item(what, why, url="https://blog.google/gemini-4-argon", source="Google"):
    return BriefingItem(what_happened=what, why_it_matters=why, url=url, source_name=source)


def _long_item(**kwargs):
    return _item(
        "Google published Gemini 4 Argon, a new frontier model.",
        "A model release changes what AI systems can do for people who use them.",
        **kwargs,
    )


def _response(status, body=""):
    class Response:
        status_code = status
        text = body

        def json(self):
            return {}

    return Response()


def test_source_url_blocks_a_missing_url_a_404_and_a_connection_error():
    missing = check_source_urls([_long_item(url="")], url_checker=check_source_url)
    assert missing is not None and missing.code == "source_urls"
    assert "missing" in missing.detail

    def not_found(method, url, **kwargs):
        return _response(404)

    ok, detail = check_source_url("https://example.com/missing", request=not_found)
    assert ok is False and "404" in detail

    def boom(method, url, **kwargs):
        raise OSError("timed out")

    ok, detail = check_source_url("https://example.com/slow", request=boom)
    assert ok is False and "request failed" in detail

    def wall(method, url, **kwargs):
        return _response(403)

    ok, detail = check_source_url("https://example.com/article", request=wall)
    assert ok is True


def test_together_private_blocks_customers_revenue_and_secrets_only():
    blocked = check_together_private("Together AI revenue grew and customers expanded.")
    assert blocked is not None and blocked.code == "together_private"
    angle = check_together_private("This note is for Together and not for readers.")
    assert angle is not None
    secret = check_together_private("See hooks.slack.com/services/T/B/secret for the channel.")
    assert secret is not None and "secret" in secret.detail
    public = check_together_private("Anthropic published a new model. A reader should care.")
    assert public is None
    english = check_together_private("The stories fit together. Customers of other firms were not named.")
    assert english is None


def test_substance_blocks_empty_short_and_duplicate_issues():
    empty = check_substance([], "", None)
    assert empty is not None and "empty" in empty.detail
    short = check_substance([_item("Short.", "Also short.")], "Short. Also short.", None)
    assert short is not None and short.code == "substance"
    release = _item(
        "vLLM v0.32.0.",
        "A software release affects how people run open models.",
    )
    release_prose = release.what_happened + " " + release.why_it_matters + " A second sentence keeps the issue long enough to publish."
    assert check_substance([release], release_prose, None) is None
    item = _long_item()
    prose = item.what_happened + " " + item.why_it_matters
    first = check_substance([item], prose, None)
    assert first is None
    duplicate = check_substance(
        [item],
        prose,
        {"urls": [item.url], "fingerprint": "different"},
    )
    assert duplicate is not None and "last issue" in duplicate.detail


def test_safety_blocks_profanity_and_allows_ordinary_news_words():
    blocked = check_safety("The announcement was fucking late and still vague.")
    assert blocked is not None and blocked.code == "safety"
    unsafe = check_safety("The post linked to child porn forums.")
    assert unsafe is not None
    allowed = check_safety("A security researcher described an attack on an agent tool.")
    assert allowed is None


def test_schema_blocks_a_missing_lede_and_accepts_a_valid_payload():
    bad = check_schema({"items": []})
    assert bad is not None and bad.code == "schema" and "lede" in bad.detail
    good = check_schema({
        "lede": "A quiet week.",
        "items": [{"index": 0, "what_happened": "A lab posted a note.", "why_it_matters": "Readers can ignore it."}],
    })
    assert good is None
    assert check_schema("nope") is not None


def test_idempotency_blocks_a_second_issue_in_the_same_window():
    key = "issue-2026-10-14"
    assert check_idempotency(key, []) is None
    again = check_idempotency(key, [{"idempotency_key": key}])
    assert again is not None and again.code == "idempotency"
    assert check_idempotency("issue-2026-10-16", [{"idempotency_key": key}]) is None


def test_rank_prompt_is_a_general_public_briefing():
    article = Article(
        url="https://openai.com/news",
        title="OpenAI publishes a note",
        source_name="OpenAI",
        source_domain="openai.com",
        published_at=NOW,
        content_preview="OpenAI published a short public note.",
    )
    prompt = _rank_prompt([article], [], 7)
    assert "general reader" in prompt
    assert "for a builder" not in prompt.lower()
    assert "for Together angle" in prompt
    assert "Slack briefing for people who build" not in prompt


def test_strict_schema_failure_raises_instead_of_falling_back(monkeypatch):
    article = Article(
        url="https://openai.com/news",
        title="OpenAI publishes a note",
        source_name="OpenAI",
        source_domain="openai.com",
        published_at=NOW,
        content_preview="OpenAI published a short public note about a model.",
    )
    monkeypatch.setattr("src.summarize._call_together", lambda *args, **kwargs: "not json")
    with pytest.raises(SchemaError):
        compose_briefing([article], "sk-test", "model", strict_auth=True)
    lede, pairs = compose_briefing([article], "sk-test", "model", strict_auth=False)
    assert pairs and "OpenAI" in pairs[0][1]


def _config():
    return {
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


def _article():
    return Article(
        url="https://blog.google/gemini-4-argon",
        title="Gemini 4 Argon: Built to solve your hardest problems",
        source_name="Google",
        source_domain="blog.google",
        published_at=datetime(2026, 10, 13, 16, tzinfo=timezone.utc),
        content_preview="Google published Gemini 4 Argon, a new frontier model.",
        source_kind="lab",
        source_priority=1,
    )


def _run(tmp_path, *, copies, checker, poster, site, email, dry_run=False):
    def fetcher(config, since, now):
        return FetchReport(articles=[_article()], failures=[], successes=3)

    def write_copies(articles, api_key, model, strict_auth):
        return copies

    return run_briefing(
        _config(),
        dry_run=dry_run,
        api_key="sk-test",
        webhook_url=WEBHOOK,
        now=NOW,
        seen_path=tmp_path / "seen.json",
        last_run_path=tmp_path / "last.json",
        recent_path=tmp_path / "recent.json",
        published_path=tmp_path / "published.json",
        fetcher=fetcher,
        enricher=lambda items: None,
        poster=poster,
        url_checker=checker,
        site_publisher=site,
        email_publisher=email,
        composer=None,
    )


def test_each_guardrail_blocks_publish_and_alerts(tmp_path, monkeypatch):
    cases = {
        "source_urls": (
            [("Google published Gemini 4 Argon.", "A model release changes what AI systems can do.")],
            lambda url: (False, "source URL returned 404"),
        ),
        "together_private": (
            [(
                "Together AI revenue grew in the quarter.",
                "Customers were told about the figure in an internal note.",
            )],
            lambda url: (True, "ok"),
        ),
        "substance": (
            [("Too short.", "No.")],
            lambda url: (True, "ok"),
        ),
        "safety": (
            [(
                "The post was fucking late and still short on detail.",
                "A reader can skip a late note that adds nothing new.",
            )],
            lambda url: (True, "ok"),
        ),
    }
    for code, (copies, checker) in cases.items():
        alerts = []

        def poster(payload, url, alerts=alerts):
            alerts.append(payload["text"])

        def banned(issue):
            raise AssertionError("published despite " + code)

        monkeypatch.setattr("src.briefing.write_copies", lambda *args, **kwargs: copies)
        with pytest.raises(BriefingError, match=code):
            _run(
                tmp_path,
                copies=copies,
                checker=checker,
                poster=poster,
                site=banned,
                email=banned,
            )
        assert len(alerts) == 1
        assert "was not published" in alerts[0]
        assert code in alerts[0]
        assert "sk-test" not in alerts[0]
        assert not (tmp_path / "seen.json").exists()


def test_schema_failure_alerts_and_does_not_publish(tmp_path):
    alerts = []

    def poster(payload, url):
        alerts.append(payload["text"])

    def composer(*args, **kwargs):
        raise SchemaError("lede must be a string")

    config = _config()
    config["llm_rank"] = True
    with pytest.raises(BriefingError, match="schema"):
        run_briefing(
            config,
            dry_run=False,
            api_key="sk-test",
            webhook_url=WEBHOOK,
            now=NOW,
            seen_path=tmp_path / "seen.json",
            last_run_path=tmp_path / "last.json",
            fetcher=lambda config, since, now: FetchReport(articles=[_article()], failures=[], successes=3),
            enricher=lambda items: None,
            poster=poster,
            composer=composer,
            site_publisher=lambda issue: (_ for _ in ()).throw(AssertionError("site")),
            email_publisher=lambda issue: (_ for _ in ()).throw(AssertionError("email")),
        )
    assert alerts and "schema" in alerts[0]
    assert not (tmp_path / "seen.json").exists()


def test_idempotency_guardrail_blocks_a_second_live_issue(tmp_path, monkeypatch):
    published = tmp_path / "published.json"
    published.write_text('{"issues": [{"idempotency_key": "issue-2026-10-14", "urls": ["https://other.example/a"]}]}')
    alerts = []
    monkeypatch.setattr(
        "src.briefing.write_copies",
        lambda *args, **kwargs: [(
            "Google published Gemini 4 Argon.",
            "A model release changes what AI systems can do.",
        )],
    )
    with pytest.raises(BriefingError, match="idempotency"):
        run_briefing(
            _config(),
            dry_run=False,
            api_key="sk-test",
            webhook_url=WEBHOOK,
            now=NOW,
            seen_path=tmp_path / "seen.json",
            last_run_path=tmp_path / "last.json",
            published_path=published,
            fetcher=lambda config, since, now: FetchReport(articles=[_article()], failures=[], successes=3),
            enricher=lambda items: None,
            poster=lambda payload, url: alerts.append(payload["text"]),
            url_checker=lambda url: (True, "ok"),
            site_publisher=lambda issue: (_ for _ in ()).throw(AssertionError("site")),
            email_publisher=lambda issue: (_ for _ in ()).throw(AssertionError("email")),
        )
    assert alerts and "idempotency" in alerts[0]


def test_mdx_frontmatter_and_email_have_no_frontmatter():
    issue = build_issue(
        NOW,
        "This window is mostly about new models.",
        [_long_item()],
        articles=[_article()],
    )
    mdx = render_mdx(issue)
    data = frontmatter(mdx)
    assert data["title"].startswith("AI news")
    assert data["date"] == "2026-10-14"
    assert "summary" in data and data["summary"]
    assert "models" in data["tags"]
    email = render_email_markdown(issue)
    assert not email.startswith("---")
    assert "https://blog.google/gemini-4-argon" in email
    assert "hooks.slack.com" not in mdx
    assert "hooks.slack.com" not in email


def test_buttondown_skips_without_a_key_and_hides_the_key(capsys, monkeypatch):
    from src.buttondown import send_issue

    issue = build_issue(NOW, "A public lede about new models.", [_long_item()], articles=[_article()])
    monkeypatch.delenv("BUTTONDOWN_API_KEY", raising=False)
    skipped = send_issue(issue, api_key=None, post=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("posted")))
    assert skipped["skipped"] is True
    assert "BUTTONDOWN_API_KEY is not set" in capsys.readouterr().err

    captured = {}

    class Response:
        status_code = 201
        text = ""

    def post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return Response()

    secret = "bd_test_key_value"
    result = send_issue(issue, api_key=secret, post=post)
    assert result["skipped"] is False
    assert captured["url"] == "https://api.buttondown.com/v1/emails"
    assert captured["headers"]["Authorization"] == f"Token {secret}"
    assert captured["json"]["status"] == "about_to_send"
    assert not captured["json"]["body"].startswith("---")
    err = capsys.readouterr().err
    assert secret not in err


def test_site_publish_skips_without_a_token_and_hides_the_token(capsys):
    from src.site_publish import publish_mdx

    mdx = "---\ntitle: \"AI news\"\ndate: 2026-10-14\nsummary: \"A note.\"\ntags:\n  - ai\n---\n\nHello.\n"
    skipped = publish_mdx(
        mdx,
        path="src/content/newsletter/2026-10-14.mdx",
        token="",
        request=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("called")),
    )
    assert skipped["skipped"] is True
    assert "SITE_GITHUB_TOKEN is not set" in capsys.readouterr().err

    calls = []

    def request(method, url, headers=None, params=None, json=None, timeout=None):
        calls.append((method, url, headers, json))
        if method == "GET":
            return _response(404, "missing")
        return _response(201, "created")

    token = "ghp_test_token_value"
    result = publish_mdx(
        mdx,
        path="src/content/newsletter/2026-10-14.mdx",
        token=token,
        repo="nbirkner/site",
        branch="main",
        request=request,
    )
    assert result["skipped"] is False
    assert calls[0][0] == "GET"
    assert calls[1][0] == "PUT"
    assert calls[1][2]["Authorization"] == f"Bearer {token}"
    decoded = base64.b64decode(calls[1][3]["content"]).decode()
    assert decoded == mdx
    assert token not in capsys.readouterr().err


def test_evaluate_issue_reports_every_failing_check():
    item = _item(
        "Together AI revenue was shared with customers.",
        "fuck this note.",
        url="",
    )
    violations = evaluate_issue(
        title="AI news",
        lede="",
        items=[item],
        idempotency_key="issue-2026-10-14",
        published=[{"idempotency_key": "issue-2026-10-14", "urls": []}],
        url_checker=lambda url: (False, "missing source URL"),
    )
    codes = {item.code for item in violations}
    assert {"source_urls", "together_private", "substance", "safety", "idempotency"} <= codes
