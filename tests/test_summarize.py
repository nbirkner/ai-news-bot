from datetime import datetime, timezone

from src.models import Article
from src.summarize import extractive_lines


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


def test_extractive_uses_a_source_why_line_when_it_is_present():
    article = _article(
        "AI tools are aiding would-be spies",
        "AI tools are now used to spy on employers. Why it matters: The same tools can expose internal systems.",
        kind="news",
    )
    what, why = extractive_lines(article)
    assert what.startswith("AI tools are now used")
    assert why.startswith("The same tools can expose")
