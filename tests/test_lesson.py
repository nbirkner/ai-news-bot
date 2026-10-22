"""Lesson mode. Network stays mocked. Sending stays off."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.lesson import (
    FACTS_RULE,
    SYSTEM_PROMPT,
    LessonBlocked,
    call_lesson_model,
    generate_lesson,
    lesson_email_title,
    load_prior,
    load_queue,
    next_topic,
    research_sources,
    word_count,
)

DATE = "2026-10-27"
NOW = datetime(2026, 10, 27, 16, 0, tzinfo=timezone.utc)
ROOT = Path("lessons")
SENTINEL = "UNIQUE_SCRAPE_SENTINEL the middle position lost the fact."
PAD = (
    "You keep the one fact you care about near the start or the end, "
    "because the middle of a long note is where a model looks least carefully."
)


def _block_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError(f"unexpected network call args={args!r}")

    monkeypatch.setattr("requests.get", boom)
    monkeypatch.setattr("requests.post", boom)
    monkeypatch.setattr("requests.Session.request", boom)


def _sources():
    return [
        {
            "title": "Lost in the Middle",
            "url": "https://arxiv.org/abs/2307.03172",
            "source": "arXiv",
            "published": "2026-06-01T00:00:00Z",
            "summary": "A paper on long contexts.",
            "reflection": "Primary paper, not a recap.",
            "text": (
                "The measured drop was 75 percent for facts placed in the middle of a long context. "
                + SENTINEL
            ),
        },
        {
            "title": "Attention Is All You Need",
            "url": "https://arxiv.org/abs/1706.03762",
            "source": "arXiv",
            "published": "2026-05-01T00:00:00Z",
            "summary": "The transformer paper.",
            "reflection": "",
            "text": (
                "The transformer reads the whole context at once and weighs earlier tokens. "
                "Vaswani and colleagues showed that attention can look across the full note."
            ),
        },
    ]


def _topic():
    return {
        "slug": "lost-in-the-middle",
        "level": "Foundations",
        "title": "Lost in the middle: why models miss details in huge prompts, and why RAG didn't die",
        "brief": "Explain the middle of a long prompt.",
        "reflection": "Teach the mechanism, not a list of papers.",
    }


def _queue(path: Path):
    path.write_text(json.dumps([_topic(), {
        "slug": "temperature-and-grounding",
        "level": "Foundations",
        "title": "Why turning temperature down only half-fixes hallucination",
        "brief": "Next topic.",
    }]), encoding="utf-8")


def _prior(path: Path):
    path.write_text(json.dumps([{
        "title": "A context window is working memory for one request, not a filing cabinet of past chats.",
        "slug": "context-window",
        "summary": "Models read tokens, not words.",
        "level": "Foundations",
        "date": "2026-08-22",
    }]), encoding="utf-8")


def _draft(extra=""):
    hook = (
        "You have felt a chatbot stall, then answer from the start and the end of your note "
        "while the middle quietly disappears. That is the mechanism this lesson stays with."
    )
    how = (
        "retrieval augmented generation (RAG) keeps a short list of passages in front of the model. "
        "RAG does not stop the middle of that list from getting less attention."
    )
    numbers = "The measured drop was 75 percent for facts placed in the middle."
    analogy = "Picture a crowded notebook where the first line and the last line are the ones you reread."
    try_this = "Open any chatbot and paste a long note with one odd fact in the middle, then ask for that fact."
    dinner = '"The middle of a long prompt is the easy place to lose a fact."'
    while True:
        body = f"""# Models miss the middle of a long prompt.

{hook}

## How it works

{how}

## The numbers

{numbers}

## An analogy: a crowded notebook

{analogy}

## Try this

{try_this}

## Use it at dinner

{dinner}

## Sources

- [Lost in the Middle](https://arxiv.org/abs/2307.03172)
- [Attention Is All You Need](https://arxiv.org/abs/1706.03762)
"""
        if extra:
            body = body.replace("## Sources", extra + "\n\n## Sources", 1)
        count = word_count(body)
        if 800 <= count <= 1200:
            text = f"""---
title: "Models miss the middle of a long prompt."
date: {DATE}
level: Foundations
slug: lost-in-the-middle
summary: "A long prompt can bury the fact you need. The model still reads the ends more carefully than the middle."
---

{body}"""
            return text
        if count > 1200:
            raise AssertionError(f"draft grew to {count} words")
        how += " " + PAD
        analogy += " " + PAD
        try_this += " " + PAD


def _generate(tmp_path, monkeypatch, *, raw=None, sources=None, opener=None, published=None):
    _block_network(monkeypatch)
    queue = tmp_path / "queue.json"
    prior = tmp_path / "prior.json"
    done = tmp_path / "published.json"
    _queue(queue)
    _prior(prior)
    done.write_text(json.dumps(published or []), encoding="utf-8")
    seen = {}

    def candidates(topic):
        seen["topic"] = topic["slug"]
        return list(sources if sources is not None else _sources())

    def open_url(url):
        seen.setdefault("opened", []).append(url)
        for item in _sources():
            if item["url"] == url:
                return item["text"]
        raise OSError("missing")

    def composer(prompt, api_key, model):
        seen["prompt"] = prompt
        if raw == "RAISE":
            raise RuntimeError(SENTINEL)
        return raw if raw is not None else _draft()

    try:
        lesson = generate_lesson(
            date=DATE,
            api_key="sk-test",
            model="zai-org/GLM-5.3",
            queue_path=queue,
            prior_path=prior,
            published_path=done,
            now=NOW,
            candidates=candidates,
            opener=opener or open_url,
            composer=composer,
        )
    except LessonBlocked as exc:
        seen["blocked"] = exc.line
        return seen
    seen["lesson"] = lesson
    return seen


def test_queue_starts_at_lost_in_the_middle_and_skips_published():
    queue = load_queue(ROOT / "queue.json")
    prior = load_prior(ROOT / "prior.json")
    assert len(queue) == 18
    assert len(prior) == 29
    assert [item["level"] for item in queue][0] == "Foundations"
    assert set(item["level"] for item in queue) <= {"Foundations", "Systems", "Frontier"}
    topic = next_topic(queue, prior, set())
    assert topic["slug"] == "lost-in-the-middle"
    assert topic["level"] == "Foundations"
    skipped = next_topic(queue, prior, {"lost-in-the-middle"})
    assert skipped["slug"] == "temperature-and-grounding"
    assert next_topic([], prior, set()) is None


def test_lesson_drafts_from_public_sources_and_reflection(tmp_path, monkeypatch):
    seen = _generate(tmp_path, monkeypatch)
    lesson = seen["lesson"]
    assert seen["topic"] == "lost-in-the-middle"
    assert seen["opened"] == [
        "https://arxiv.org/abs/2307.03172",
        "https://arxiv.org/abs/1706.03762",
    ]
    assert FACTS_RULE in seen["prompt"]
    assert "curious non-experts" in seen["prompt"]
    assert SYSTEM_PROMPT in seen["prompt"]
    assert "Primary paper, not a recap." in seen["prompt"]
    assert "context window" in seen["prompt"].lower()
    assert lesson["email_title"] == "AI Lessons, October 27, 2026"
    assert lesson_email_title(DATE) == lesson["email_title"]
    assert lesson["markdown"].startswith("---\n")
    for field in ("title:", "date: 2026-10-27", "level: Foundations", "slug: lost-in-the-middle", "summary:"):
        assert field in lesson["markdown"]
    html = lesson["html"]
    assert "<h1" in html and "AI Lessons, October 27, 2026" in html
    assert "<title>AI Lessons, October 27, 2026</title>" in html
    assert "AI News" not in html
    assert "#C2410C" in html
    assert 'width="600"' in html
    assert "Schibsted Grotesk" in html
    assert "apple-touch-icon.png" in html
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in html
    assert "https://arxiv.org/abs/2307.03172" in html
    assert "Models miss the middle of a long prompt." in html
    assert SENTINEL not in lesson["markdown"]
    assert SENTINEL not in html


def test_fewer_than_two_opened_sources_is_blocked(tmp_path, monkeypatch):
    def opener(url):
        if url.endswith("2307.03172"):
            return _sources()[0]["text"]
        return "too short"

    seen = _generate(tmp_path, monkeypatch, opener=opener)
    assert seen["blocked"] == "BLOCKED: fewer than 2 sources could be opened"
    assert "prompt" not in seen
    assert SENTINEL not in seen["blocked"]


def test_old_sources_do_not_count(tmp_path):
    old = dict(_sources()[0], published="2020-01-01T00:00:00Z", url="https://arxiv.org/abs/1111.11111")
    fresh = _sources()
    opened = []

    def opener(url):
        opened.append(url)
        return _sources()[0]["text"]

    found = research_sources(_topic(), [old, *fresh], opener, now=NOW)
    assert "https://arxiv.org/abs/1111.11111" not in opened
    assert len(found) == 2


def test_research_failure_and_bad_drafts_are_blocked(tmp_path, monkeypatch):
    def boom(topic):
        raise RuntimeError(SENTINEL)

    _block_network(monkeypatch)
    queue = tmp_path / "queue.json"
    prior = tmp_path / "prior.json"
    _queue(queue)
    _prior(prior)
    with pytest.raises(LessonBlocked, match="research failed"):
        generate_lesson(
            date=DATE,
            api_key="sk-test",
            model="m",
            queue_path=queue,
            prior_path=prior,
            published_path=tmp_path / "missing.json",
            now=NOW,
            candidates=boom,
            composer=lambda *args: (_ for _ in ()).throw(AssertionError("composed")),
        )

    for raw, reason in (
        (_draft().replace("75 percent", "99 percent"), "99"),
        (_draft().replace("disappears.", "disappears — today."), "em dash"),
        (_draft().replace("## Try this", "## Skip this"), "try"),
        ("BLOCKED: could not verify the figure\n" + SENTINEL, "could not verify the figure"),
    ):
        seen = _generate(tmp_path, monkeypatch, raw=raw)
        assert seen["blocked"].startswith("BLOCKED:")
        assert reason in seen["blocked"]
        assert SENTINEL not in seen["blocked"]
        assert "lesson" not in seen


def test_empty_queue_is_blocked(tmp_path, monkeypatch):
    _block_network(monkeypatch)
    queue = tmp_path / "queue.json"
    queue.write_text("[]", encoding="utf-8")
    prior = tmp_path / "prior.json"
    _prior(prior)
    with pytest.raises(LessonBlocked, match="topic queue is empty") as caught:
        generate_lesson(
            date=DATE,
            api_key="sk-test",
            model="m",
            queue_path=queue,
            prior_path=prior,
            published_path=tmp_path / "none.json",
            now=NOW,
            candidates=lambda topic: (_ for _ in ()).throw(AssertionError("researched")),
        )
    assert "BLOCKED:" in caught.value.line
    assert SENTINEL not in caught.value.line


def test_cli_writes_mdx_and_html_and_refuses_to_send(tmp_path, monkeypatch, capsys):
    _block_network(monkeypatch)
    monkeypatch.setenv("OUT_DIR", str(tmp_path))
    monkeypatch.setenv("TOGETHER_API_KEY", "sk-test")
    monkeypatch.setattr("src.lesson.arxiv_candidates", lambda topic, now: _sources())
    monkeypatch.setattr("src.lesson.open_public_url", lambda url: next(item["text"] for item in _sources() if item["url"] == url))
    monkeypatch.setattr("src.lesson.call_lesson_model", lambda prompt, api_key, model: _draft())
    sent = []
    monkeypatch.setattr(
        "main_newsletter.create_broadcast",
        lambda **kwargs: sent.append(kwargs),
    )

    from main_newsletter import main
    code = main(["main_newsletter.py", "--mode", "lesson", "--dry-run", "--date", DATE])
    assert code == 0
    assert sent == []
    mdx = (tmp_path / "2026-10-27-lost-in-the-middle.mdx").read_text()
    html = (tmp_path / "2026-10-27-lost-in-the-middle.html").read_text()
    assert "level: Foundations" in mdx
    assert "AI Lessons, October 27, 2026" in html
    assert not (tmp_path / "2026-10-27.blocked.txt").exists()

    refused = main(["main_newsletter.py", "--mode", "lesson", "--send", "--date", DATE])
    assert refused == 2
    assert sent == []
    err = capsys.readouterr().err
    assert "never sends" in err


def test_cli_blocked_file_is_only_the_reason(tmp_path, monkeypatch, capsys):
    _block_network(monkeypatch)
    monkeypatch.setenv("OUT_DIR", str(tmp_path))
    monkeypatch.setenv("TOGETHER_API_KEY", "sk-test")
    monkeypatch.setattr("src.lesson.arxiv_candidates", lambda topic, now: _sources()[:1])
    monkeypatch.setattr("src.lesson.open_public_url", lambda url: _sources()[0]["text"])
    monkeypatch.setattr(
        "src.lesson.call_lesson_model",
        lambda *args: (_ for _ in ()).throw(AssertionError("model")),
    )
    from main_newsletter import main
    code = main(["main_newsletter.py", "--mode", "lesson", "--date", DATE])
    assert code == 2
    blocked = (tmp_path / f"{DATE}.blocked.txt").read_text()
    assert blocked == "BLOCKED: fewer than 2 sources could be opened\n"
    assert SENTINEL not in blocked
    assert capsys.readouterr().out == blocked
    assert not list(tmp_path.glob("*.mdx"))
    assert not list(tmp_path.glob("*.html"))


def test_lesson_model_call_is_plain_text(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    def post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return Response()

    monkeypatch.setattr("src.lesson.requests.post", post)
    assert call_lesson_model("prompt", "sk-test", "zai-org/GLM-5.3") == "hello"
    assert "response_format" not in captured["json"]
    assert captured["json"]["messages"][0]["content"] == "prompt"


def test_lessons_workflow_is_tuesday_9am_and_dry_run_only():
    text = Path(".github/workflows/lessons.yml").read_text()
    assert 'cron: "0 16 * * 2"' in text
    assert 'cron: "0 17 * * 2"' in text
    assert '[ "$hour" = "09" ]' in text
    assert "America/Los_Angeles" in text
    assert "--mode lesson" in text
    assert "--dry-run" in text
    assert 'DRY_RUN: "1"' in text
    assert "--send" not in text
    assert "RESEND" not in text
    assert "SLACK" not in text
    assert "git push" not in text
    assert "git commit" not in text
    assert "contents: read" in text
    assert "This input does not enable sending." in text
