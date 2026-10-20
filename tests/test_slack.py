from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.models import BriefingItem
from src.slack import post_to_slack, render_briefing, slack_payload


NOW = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 10, 11, 7, 0, tzinfo=timezone.utc)


def _item(**kwargs):
    data = dict(
        what_happened="Eight US states sued OpenAI over harm to children.",
        why_it_matters="A legal or policy move changes the constraints on building and shipping AI products.",
        url="https://www.reuters.com/world/example",
        source_name="Reuters",
    )
    data.update(kwargs)
    return BriefingItem(**data)


def test_render_one_item():
    text = render_briefing(NOW, SINCE, [_item()])
    assert text == (
        "*AI briefing, Wednesday, October 14*\n"
        "Window: Sun Oct 11, 12:00 AM to Wed Oct 14, 8:00 AM PT\n"
        "\n"
        "*Eight US states sued OpenAI over harm to children.*\n"
        "A legal or policy move changes the constraints on building and shipping AI products.\n"
        "<https://www.reuters.com/world/example|Reuters>\n"
    )


def test_render_quiet_note():
    text = render_briefing(NOW, SINCE, [])
    assert text.startswith("*AI briefing, Wednesday, October 14*\n")
    assert "Quiet since Sunday, October 11." in text
    assert "http" not in text


def test_render_has_no_em_dash():
    text = render_briefing(NOW, SINCE, [_item(what_happened="A model, then a follow-up.")])
    assert "\u2014" not in text
    assert "\u2013" not in text


def test_render_escapes_slack_markup_in_prose():
    text = render_briefing(NOW, SINCE, [_item(what_happened="AT&T <model> uses chips.")])
    assert "AT&amp;T" in text
    assert "&lt;model&gt;" in text


def test_two_items_have_a_blank_line_between_them():
    text = render_briefing(
        NOW,
        SINCE,
        [
            _item(),
            _item(
                what_happened="Google introduced Gemini 4.",
                url="https://blog.google/gemini",
                source_name="Google",
            ),
        ],
    )
    assert "Reuters>\n\n*Google introduced Gemini 4.*" in text


def test_payload_disables_unfurl():
    payload = slack_payload("hello")
    assert payload["unfurl_links"] is False
    assert payload["unfurl_media"] is False


def test_post_to_slack_calls_requests_post(mocker):
    mock_post = mocker.patch("src.slack.requests.post")
    mock_post.return_value.raise_for_status = MagicMock()
    post_to_slack({"text": "hello"}, "https://hooks.slack.com/services/T/B/test")
    args, kwargs = mock_post.call_args
    assert args[0] == "https://hooks.slack.com/services/T/B/test"
    assert "hello" in kwargs["data"]


def test_post_to_slack_raises_on_http_error(mocker):
    mock_post = mocker.patch("src.slack.requests.post")
    mock_post.return_value.raise_for_status.side_effect = Exception("HTTP 500")
    try:
        post_to_slack({"text": "hello"}, "https://hooks.slack.com/services/T/B/test")
        assert False, "expected raise"
    except Exception as exc:
        assert "HTTP 500" in str(exc)
