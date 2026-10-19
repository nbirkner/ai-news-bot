from datetime import datetime, timezone

from src.models import Article
from src.summarize import _accept, _source_text, extractive_lines


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


def test_extractive_uses_a_source_why_line_when_it_is_present():
    article = _article(
        "AI tools are aiding would-be spies",
        "AI tools are now used to spy on employers. Why it matters: The same tools can expose internal systems.",
        kind="news",
    )
    what, why = extractive_lines(article)
    assert what.startswith("AI tools are now used")
    assert why.startswith("The same tools can expose")
