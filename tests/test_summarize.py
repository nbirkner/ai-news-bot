import json
from datetime import datetime, timezone

import pytest

from src.models import Article
from src.summarize import (
    SchemaError,
    _accept,
    _call_together,
    _publishable_body,
    _source_text,
    compose_briefing,
    extractive_lines,
)
from src.usage import format_usage, reset_usage


def _article(title, preview, kind="lab"):
    return Article(
        url="https://blog.google/gemini",
        title=title,
        source_name="Google",
        source_domain="blog.google",
        published_at=datetime(2026, 10, 13, tzinfo=timezone.utc),
        content_preview=preview,
        source_kind=kind,
        source_priority=1,
    )


def test_extractive_uses_the_title_when_the_lede_is_first_person():
    article = _article(
        "Gemini 4 Argon: Built to solve your hardest problems",
        "We're giving developers access to Gemini 4 Argon, our latest frontier model for hard problems.",
    )
    what, why = extractive_lines(article)
    assert what.startswith("Gemini 4 Argon")
    assert "We're" not in what
    assert "model release" in why.lower()


def test_accept_keeps_a_version_sentence_when_the_preview_is_only_the_title():
    article = _article("vLLM v0.32.0", "vLLM v0.32.0", kind="release")
    article.full_text = "vLLM v0.32.0\nThis release adds new features and parsers."
    article.url = "https://github.com/vllm-project/vllm/releases/tag/v0.32.0"
    article.source_name = "vLLM"
    kept = _accept(
        "vLLM released version 0.32.0",
        article,
        analysis=False,
        fallback=article.title,
    )
    assert kept == "vLLM released version 0.32.0."


def test_grounding_source_includes_full_text():
    article = _article("DeepSeek-V4.1-Flash-1017", "DeepSeek-V4.1-Flash-1017")
    article.full_text = (
        "DeepSeek released DeepSeek-V4.1-Flash-1017 on 2026-10-17. "
        "The weights are on Hugging Face. Context length is 128000."
    )
    source = _source_text(article)
    assert "128000" in source
    from src.clean import line_is_grounded
    assert line_is_grounded(
        "DeepSeek-V4.1-Flash-1017 has a context length of 128000.",
        source,
        analysis=False,
    )
    assert not line_is_grounded(
        "DeepSeek-V4.1-Flash-1017 has a context length of 128000.",
        article.content_preview,
        analysis=False,
    )


def _full_body():
    return (
        "Vercel's October report says open-weight models handled 71.5% of tokens. "
        "Those models accounted for 23% of spend in the same report. "
        "DeepSeek V4.1 Flash handled nearly half of September's tokens for 2.6% of the dollars."
    )


def _rich_article():
    return Article(
        url="https://vercel.com/blog/ai-gateway-production-index-october-2026",
        title="Open-weight models took most of the tokens",
        source_name="Vercel",
        source_domain="vercel.com",
        published_at=datetime(2026, 10, 15, 16, tzinfo=timezone.utc),
        content_preview="Vercel published a gateway report.",
        full_text=_full_body(),
        source_kind="news",
        source_priority=2,
    )


class _Response:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}: {self.text}")

    def json(self):
        return self._payload


def _ok(content, finish="stop", completion=20, reasoning=4):
    return _Response(200, {
        "choices": [{"finish_reason": finish, "message": {"content": content}}],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": completion,
            "completion_tokens_details": {"reasoning_tokens": reasoning},
        },
    })


def test_call_uses_low_effort_and_leaves_room_for_the_answer(monkeypatch):
    captured = []

    def post(url, headers=None, json=None, timeout=None):
        captured.append(json)
        return _ok('{"items": []}')

    monkeypatch.setattr("src.summarize.requests.post", post)
    reset_usage()
    text = _call_together("hello", "sk-test", "zai-org/GLM-5.3")
    assert "items" in text
    assert len(captured) == 1
    assert captured[0]["reasoning_effort"] == "low"
    assert captured[0]["max_tokens"] >= 8192
    assert "reasoning" not in captured[0]
    assert "sk-test" not in format_usage(model="zai-org/GLM-5.3")
    reset_usage()


def test_cut_off_answer_retries_then_raises(monkeypatch):
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(json["max_tokens"])
        return _ok("", finish="length", completion=4500, reasoning=4500)

    monkeypatch.setattr("src.summarize.requests.post", post)
    with pytest.raises(SchemaError) as exc:
        _call_together("hello", "sk-test", "zai-org/GLM-5.3")
    assert calls == [8192, 16384]
    message = str(exc.value)
    assert "truncated" in message
    assert "reasoning_tokens=4500" in message
    assert "at least 3 sentences" not in message or "truncated" in message


def test_rejected_reasoning_effort_is_dropped(monkeypatch):
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(dict(json))
        if "reasoning_effort" in json:
            return _Response(400, text="unknown parameter reasoning_effort")
        return _ok('{"ok": true}')

    monkeypatch.setattr("src.summarize.requests.post", post)
    text = _call_together("hello", "sk-test", "zai-org/GLM-5.3")
    assert "ok" in text
    assert "reasoning_effort" not in calls[1]


def test_short_body_retries_once_and_keeps_the_full_rewrite(monkeypatch):
    article = _rich_article()
    short = {
        "lede": "Tokens and dollars moved apart.",
        "items": [{
            "index": 0,
            "headline": "Open-weight models took most of the tokens",
            "body": "Open-weight models handled 71.5% of tokens.",
            "why_it_matters": "A cheap token and an expensive closed model are different bills.",
        }],
    }
    full = {
        "lede": "Tokens and dollars moved apart.",
        "items": [{
            "index": 0,
            "headline": "Open-weight models took most of the tokens",
            "body": _full_body(),
            "why_it_matters": "For people building with AI, a cheap token and an expensive closed model are different bills.",
        }],
    }
    prompts = []

    def fake_call(prompt, api_key, model):
        prompts.append(prompt)
        return json.dumps(short if len(prompts) == 1 else full)

    monkeypatch.setattr("src.summarize._call_together", fake_call)
    lede, pairs = compose_briefing(
        [article],
        "sk-test",
        "zai-org/GLM-5.3",
        strict_auth=True,
        require_full_body=True,
    )
    assert len(prompts) == 2
    assert "previous draft was rejected" in prompts[1].lower()
    assert "71.5%" in pairs[0][2]
    assert "23%" in pairs[0][2]
    assert "2.6%" in pairs[0][2]
    assert lede


def test_still_short_body_raises_a_clear_error(monkeypatch):
    article = _rich_article()

    def fake_call(prompt, api_key, model):
        return json.dumps({
            "lede": "Tokens and dollars moved apart.",
            "items": [{
                "index": 0,
                "headline": "Open-weight models took most of the tokens",
                "body": "Open-weight models handled 71.5% of tokens.",
                "why_it_matters": "A cheap token and an expensive closed model are different bills.",
            }],
        })

    monkeypatch.setattr("src.summarize._call_together", fake_call)
    with pytest.raises(SchemaError) as exc:
        compose_briefing(
            [article],
            "sk-test",
            "zai-org/GLM-5.3",
            strict_auth=True,
            require_full_body=True,
        )
    message = str(exc.value)
    assert "body needs at least 3 sentences" in message
    assert "about" in message and "tokens" in message
    assert "Retried once" in message
    assert "still too short to publish" in message


def test_grounding_that_drops_sentences_falls_back_to_the_source():
    article = _rich_article()
    # A novel name is stripped, which would otherwise leave a one-sentence body.
    drafted = (
        "Vercel's October report says open-weight models handled 71.5% of tokens. "
        "Pat Pending invented a second figure of 99% that is not in the source."
    )
    body = _publishable_body(drafted, article)
    from src.clean import split_sentences
    assert len(split_sentences(body)) >= 3
    assert "71.5%" in body
    assert "Pat Pending" not in body


def test_extractive_uses_a_source_why_line_when_it_is_present():
    article = _article(
        "AI tools are aiding would-be spies",
        "AI tools are now used to spy on employers. Why it matters: The same tools can expose internal systems.",
        kind="news",
    )
    what, why = extractive_lines(article)
    assert what.startswith("AI tools are now used")
    assert why.startswith("The same tools can expose")
