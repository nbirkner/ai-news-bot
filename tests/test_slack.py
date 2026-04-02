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


def test_format_breaking_contains_title():
    article = make_article()
    payload = format_breaking(article)
    assert "Claude 4 is here" in str(payload)


def test_format_breaking_contains_summary():
    article = make_article()
    payload = format_breaking(article)
    assert "Anthropic released Claude 4 today" in str(payload)


def test_format_breaking_contains_why_it_matters():
    article = make_article()
    payload = format_breaking(article)
    assert "Direct competitor" in str(payload)


def test_format_breaking_contains_url():
    article = make_article()
    payload = format_breaking(article)
    assert "https://anthropic.com/news/claude-4" in str(payload)


def test_format_breaking_is_dict_with_text_key():
    article = make_article()
    payload = format_breaking(article)
    assert isinstance(payload, dict)
    assert "text" in payload


def test_format_digest_skipped_when_no_articles():
    result = format_digest([])
    assert result is None


def test_format_digest_contains_all_titles():
    articles = [
        make_article(title="Story One", is_breaking=False, why_it_matters=None),
        make_article(title="Story Two", url="https://example.com/2",
                     is_breaking=False, why_it_matters=None),
    ]
    payload = format_digest(articles)
    assert "Story One" in str(payload)
    assert "Story Two" in str(payload)


def test_format_digest_separates_together_relevant():
    together_article = make_article(
        title="GPU prices rise", relevant_to_together=True, why_it_matters=None
    )
    industry_article = make_article(
        title="Consumer chatbot launched", url="https://example.com/2",
        relevant_to_together=False, why_it_matters=None
    )
    payload = format_digest([together_article, industry_article])
    text = str(payload)
    assert "Relevant to Together" in text
    assert "Industry" in text


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
