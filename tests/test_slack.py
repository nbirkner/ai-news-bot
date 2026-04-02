from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import pytest
from src.models import Article
from src.slack import format_breaking, format_digest, post_to_slack


def make_article(**kwargs):
    defaults = dict(
        url="https://anthropic.com/news/claude-4",
        title="Claude 4 is here",
        source_name="Anthropic Blog",
        source_domain="anthropic.com",
        published_at=datetime.now(timezone.utc),
        content_preview="Anthropic launches Claude 4",
        is_breaking=True,
        summary="Anthropic released Claude 4 today, a major upgrade.",
        why_it_matters="Direct competitor to Together AI hosted models.",
        relevant_to_together=True,
    )
    defaults.update(kwargs)
    return Article(**defaults)


# --- format_breaking ---

def test_format_breaking_contains_title():
    article = make_article()
    payload = format_breaking(article)
    assert "Claude 4 is here" in payload["text"]


def test_format_breaking_title_has_breaking_prefix():
    article = make_article(title="Anthropic cuts prices 30%")
    payload = format_breaking(article)
    assert payload["text"].startswith("🚨 BREAKING: Anthropic cuts prices 30%")


def test_format_breaking_contains_summary():
    article = make_article()
    payload = format_breaking(article)
    assert "Anthropic released Claude 4 today" in payload["text"]


def test_format_breaking_contains_why_it_matters():
    article = make_article()
    payload = format_breaking(article)
    assert "Why it matters for Together: Direct competitor" in payload["text"]


def test_format_breaking_why_it_matters_omitted_when_absent():
    article = make_article(why_it_matters=None)
    payload = format_breaking(article)
    assert "Why it matters" not in payload["text"]


def test_format_breaking_contains_url():
    article = make_article()
    payload = format_breaking(article)
    assert "https://anthropic.com/news/claude-4" in payload["text"]


def test_format_breaking_url_and_source_on_same_line():
    article = make_article(url="https://anthropic.com/news/pricing", source_name="Anthropic Blog")
    payload = format_breaking(article)
    lines = payload["text"].split("\n")
    url_line = next(l for l in lines if "https://anthropic.com/news/pricing" in l)
    assert "Anthropic Blog" in url_line
    assert "  —  " in url_line


def test_format_breaking_blank_line_before_url():
    article = make_article(url="https://anthropic.com/news/claude-4")
    payload = format_breaking(article)
    lines = payload["text"].split("\n")
    url_idx = next(i for i, l in enumerate(lines) if "https://anthropic.com/news/claude-4" in l)
    assert lines[url_idx - 1] == ""


def test_format_breaking_is_dict_with_text_key():
    article = make_article()
    payload = format_breaking(article)
    assert isinstance(payload, dict)
    assert "text" in payload


# --- format_digest ---

def test_format_digest_skipped_when_no_articles():
    result = format_digest([])
    assert result is None


def test_format_digest_header_present():
    articles = [make_article()]
    result = format_digest(articles)
    assert "📰 AI News Digest" in result["text"]


def test_format_digest_contains_all_titles():
    articles = [
        make_article(title="Story One", is_breaking=False, why_it_matters=None),
        make_article(title="Story Two", url="https://example.com/2",
                     is_breaking=False, why_it_matters=None),
    ]
    payload = format_digest(articles)
    assert "Story One" in payload["text"]
    assert "Story Two" in payload["text"]


def test_format_digest_article_title_has_pin_emoji():
    articles = [make_article(title="NVIDIA GB300 ships Q3")]
    result = format_digest(articles)
    assert "📌 NVIDIA GB300 ships Q3" in result["text"]


def test_format_digest_url_present_per_article():
    articles = [make_article(url="https://nvidia.com/gb300")]
    result = format_digest(articles)
    assert "https://nvidia.com/gb300" in result["text"]


def test_format_digest_source_name_present_per_article():
    articles = [make_article(source_name="NVIDIA News", url="https://nvidia.com/gb300")]
    result = format_digest(articles)
    lines = result["text"].split("\n")
    url_line = next(l for l in lines if "https://nvidia.com/gb300" in l)
    assert "NVIDIA News" in url_line


def test_format_digest_url_and_source_on_same_line():
    articles = [make_article(url="https://nvidia.com/gb300", source_name="NVIDIA News")]
    result = format_digest(articles)
    lines = result["text"].split("\n")
    url_line = next(l for l in lines if "https://nvidia.com/gb300" in l)
    assert "NVIDIA News" in url_line
    assert "  —  " in url_line


def test_format_digest_blank_line_between_articles():
    articles = [
        make_article(title="Article One", url="https://example.com/one", relevant_to_together=False),
        make_article(title="Article Two", url="https://example.com/two", relevant_to_together=False),
    ]
    result = format_digest(articles)
    lines = result["text"].split("\n")
    idx = next(i for i, l in enumerate(lines) if "https://example.com/one" in l)
    assert lines[idx + 1] == ""


def test_format_digest_together_section_header():
    articles = [make_article(relevant_to_together=True)]
    result = format_digest(articles)
    assert "━━━ RELEVANT TO TOGETHER AI ━━━" in result["text"]


def test_format_digest_industry_section_header():
    articles = [make_article(relevant_to_together=False)]
    result = format_digest(articles)
    assert "━━━ INDUSTRY ━━━" in result["text"]


def test_format_digest_separates_together_relevant():
    together_article = make_article(title="GPU prices rise", relevant_to_together=True, why_it_matters=None)
    industry_article = make_article(
        title="Consumer chatbot launched", url="https://example.com/2",
        relevant_to_together=False, why_it_matters=None
    )
    payload = format_digest([together_article, industry_article])
    text = payload["text"]
    together_pos = text.index("RELEVANT TO TOGETHER AI")
    industry_pos = text.index("INDUSTRY")
    assert together_pos < industry_pos


def test_format_digest_no_together_section_when_none():
    articles = [make_article(relevant_to_together=False)]
    result = format_digest(articles)
    assert "RELEVANT TO TOGETHER AI" not in result["text"]


def test_format_digest_no_industry_section_when_none():
    articles = [make_article(relevant_to_together=True)]
    result = format_digest(articles)
    assert "INDUSTRY" not in result["text"]


def test_format_digest_sources_footer_present():
    articles = [make_article(source_name="Anthropic Blog")]
    result = format_digest(articles)
    assert "Sources: Anthropic Blog" in result["text"]


def test_format_digest_sources_footer_lists_all_unique_sources():
    articles = [
        make_article(source_name="Anthropic Blog", relevant_to_together=True),
        make_article(source_name="NVIDIA News", url="https://nvidia.com/gb300", relevant_to_together=True),
        make_article(source_name="TLDR AI", url="https://tldr.tech/ai", relevant_to_together=False),
    ]
    result = format_digest(articles)
    footer = next(l for l in result["text"].split("\n") if l.startswith("Sources:"))
    assert "Anthropic Blog" in footer
    assert "NVIDIA News" in footer
    assert "TLDR AI" in footer


def test_format_digest_under_3000_chars():
    articles = [make_article(url=f"https://example.com/{i}") for i in range(10)]
    result = format_digest(articles)
    assert len(result["text"]) < 3000


# --- post_to_slack ---

def test_post_to_slack_calls_requests_post(mocker):
    mock_post = mocker.patch("src.slack.requests.post")
    mock_post.return_value.status_code = 200
    mock_post.return_value.raise_for_status = MagicMock()
    post_to_slack({"text": "hello"}, "https://hooks.slack.com/test")
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "https://hooks.slack.com/test"


def test_post_to_slack_raises_on_http_error(mocker):
    mock_post = mocker.patch("src.slack.requests.post")
    mock_post.return_value.raise_for_status.side_effect = Exception("HTTP 500")
    with pytest.raises(Exception, match="HTTP 500"):
        post_to_slack({"text": "hello"}, "https://hooks.slack.com/test")
