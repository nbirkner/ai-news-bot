from datetime import datetime, timezone

from src.dedupe import canonical_url, dedupe_articles, same_event, same_story
from src.models import Article


def article(url, title, *, priority=3, kind="news", preview=""):
    return Article(
        url=url,
        title=title,
        source_name="Example",
        source_domain="example.com",
        published_at=datetime(2026, 10, 14, 12, tzinfo=timezone.utc),
        content_preview=preview,
        source_kind=kind,
        source_priority=priority,
    )


def test_canonical_url_strips_www_and_tracking():
    assert canonical_url("https://www.Example.com/a/?utm_source=slack") == "example.com/a"
    assert canonical_url("https://example.com/a") == "example.com/a"


def test_canonical_url_normalizes_arxiv():
    assert (
        canonical_url("https://export.arxiv.org/pdf/2610.13989v1.pdf")
        == "arxiv.org/abs/2610.13989"
    )
    assert canonical_url("http://arxiv.org/abs/2610.13989v2") == "arxiv.org/abs/2610.13989"


def test_same_headline_is_one_story():
    left = article("https://blog.google/gemini", "Gemini 4 Argon: Built to solve your hardest problems")
    right = article("https://www.blog.google/gemini?utm_source=x", "Gemini 4 Argon: Built to solve your hardest problems")
    assert same_story(left, right)


def test_related_gemini_posts_stay_separate():
    safety = article("https://deepmind.google/safety", "Developing Gemini 4 Argon responsibly and safely")
    robotics = article("https://deepmind.google/robotics", "Gemini 4 Argon for Robotics")
    launch = article("https://blog.google/gemini", "Gemini 4 Argon: Built to solve your hardest problems")
    assert not same_story(safety, robotics)
    assert not same_story(safety, launch)
    assert not same_story(robotics, launch)


def test_lab_post_and_wire_recap_are_the_same_event():
    lab = article(
        "https://www.anthropic.com/news/2026-us-midterms",
        "Preparing for the 2026 US midterms",
        priority=1,
        kind="lab",
    )
    lab.source_name = "Anthropic"
    wire = article(
        "https://www.reuters.com/world/us/anthropic-add-us-voting-information-claude-before-midterms-2026-10-14/",
        "Anthropic to add US voting information to Claude before midterms",
        priority=2,
    )
    wire.source_name = "Reuters"
    assert same_event(lab, wire)


def test_unrelated_items_that_share_a_lab_name_stay_separate():
    launch = article("https://openai.com/gpt-6", "OpenAI releases GPT-6 for everyone", priority=1, kind="lab")
    launch.source_name = "OpenAI"
    lawsuit = article("https://www.reuters.com/sue", "Eight US states sue OpenAI, claiming ChatGPT harms children")
    lawsuit.source_name = "Reuters"
    assert not same_event(launch, lawsuit)


def test_a_rewritten_wire_is_the_same_funding_story():
    wire = article(
        "https://www.reuters.com/world/openai-raise",
        "OpenAI seeks to accelerate fundraising ahead of Anthropic IPO, sources say",
        priority=2,
    )
    recap = article(
        "https://www.pymnts.com/openai-raise",
        "OpenAI races to raise $30 billion ahead of Anthropic IPO",
        priority=2,
        preview="OpenAI is racing to raise $30 billion at a valuation above $1.4 trillion, Reuters reported.",
    )
    assert same_story(wire, recap)
    kept = dedupe_articles([wire, recap])
    assert len(kept) == 1
    assert kept[0].url == recap.url


def test_two_outlets_covering_one_acquisition_are_the_same_story():
    lab = article(
        "https://openai.com/index/openai-to-acquire-orchestra-bio",
        "OpenAI acquires Orchestra Bio",
        priority=1,
        kind="lab",
    )
    wire = article(
        "https://www.axios.com/2026/10/20/exclusive-openai-biotech-startup-drug-development",
        "Exclusive: OpenAI has acquired Orchestra Bio",
        priority=3,
    )
    assert same_story(lab, wire)
    other = article(
        "https://openai.com/other",
        "OpenAI acquires a chip design team",
        priority=1,
        kind="lab",
    )
    assert not same_story(lab, other)


def test_a_later_writeup_uses_the_event_date_not_the_byline():
    from src.dedupe import event_already_happened, event_date, same_underlying_event
    recap = article(
        "https://examplellp.com/client-alert",
        "Client alert: eight states sue OpenAI",
        preview="On October 14, 2026, eight states sued OpenAI over child safety.",
    )
    recap.published_at = datetime(2026, 10, 20, 16, tzinfo=timezone.utc)
    since = datetime(2026, 10, 19, 15, tzinfo=timezone.utc)
    assert event_date(recap).isoformat() == "2026-10-14"
    assert event_already_happened(recap, since)
    assert same_underlying_event(
        recap,
        "Eight US states sue OpenAI, claiming ChatGPT harms children",
    )
    fresh = article(
        "https://www.reuters.com/world/openai-raise",
        "OpenAI seeks to accelerate fundraising ahead of Anthropic IPO",
        preview="OpenAI is racing to raise $30 billion, Reuters reported on October 19, 2026.",
    )
    fresh.published_at = datetime(2026, 10, 19, 23, tzinfo=timezone.utc)
    assert not event_already_happened(fresh, since)


def test_a_model_launch_is_not_the_same_story_as_a_funding_round():
    launch = article("https://openai.com/gpt", "OpenAI releases a new model for coding", priority=1, kind="lab")
    money = article("https://www.reuters.com/raise", "OpenAI seeks to accelerate fundraising ahead of Anthropic IPO")
    assert not same_story(launch, money)


def test_a_readable_wire_copy_beats_a_403_stub():
    stub = article(
        "https://www.reuters.com/technology/openai-raise",
        "EXCLUSIVE: OpenAI seeks to accelerate fundraising ahead of Anthropic IPO",
        priority=2,
        preview="EXCLUSIVE: OpenAI seeks to accelerate fundraising ahead of Anthropic IPO",
    )
    readable = article(
        "https://www.pymnts.com/openai-raise",
        "OpenAI Racing to Raise $30 Billion Ahead of Anthropic Listing",
        priority=3,
        preview="OpenAI is pitching investors on $50 billion of annualized revenue. " * 4,
    )
    readable.full_text = readable.content_preview * 3
    readable.source_domain = "pymnts.com"
    kept = dedupe_articles([stub, readable])
    assert len(kept) == 1
    assert kept[0].url == readable.url


def test_dedupe_keeps_the_lab_over_the_recap():
    lab = article(
        "https://openai.com/news/model",
        "OpenAI releases GPT-6 for everyone",
        priority=1,
        kind="lab",
        preview="short",
    )
    news = article(
        "https://www.theverge.com/gpt-6",
        "OpenAI releases GPT-6 for everyone",
        priority=3,
        preview="a much longer recap of the same launch with extra words",
    )
    kept = dedupe_articles([news, lab])
    assert len(kept) == 1
    assert kept[0].url == "https://openai.com/news/model"
