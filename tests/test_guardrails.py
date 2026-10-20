from datetime import datetime, timezone

from src.guardrails import (
    is_generic_oneliner,
    is_public_http_url,
    item_drop_reasons,
    publishing_blocks,
    slot_key,
)
from src.models import BriefingItem, Issue


def _item(**kwargs):
    data = dict(
        what_happened="Open-weight models took most of the tokens",
        body=(
            "Vercel's October report says open-weight models handled 71.5% of tokens. "
            "Those models were 23% of spend. "
            "DeepSeek V4.1 Flash handled nearly half of September's tokens."
        ),
        why_it_matters="For people building with AI, token price and model choice are now separate decisions.",
        url="https://vercel.com/blog/ai-gateway-production-index-october-2026",
        source_name="Vercel",
    )
    data.update(kwargs)
    return BriefingItem(**data)


def _issue(items=None, **kwargs):
    data = dict(
        title="AI briefing, October 19",
        date="2026-10-19",
        summary="A public briefing.",
        tags=["models"],
        items=items if items is not None else [_item()],
        since=datetime(2026, 10, 16, 15, tzinfo=timezone.utc),
        until=datetime(2026, 10, 19, 15, tzinfo=timezone.utc),
    )
    data.update(kwargs)
    return Issue(**data)


def test_generic_oneliner_rejects_the_release_note():
    assert is_generic_oneliner("vLLM v0.32.0", "It adds new features.")
    assert not is_generic_oneliner(
        "DeepSeek removed from federal systems",
        "DHS published an order removing DeepSeek from civilian agency systems. "
        "The notice covers procurements and products already installed.",
    )
    assert "generic one-liner" in item_drop_reasons(
        _item(what_happened="vLLM v0.32.0", body="It adds new features.")
    )


def test_three_concrete_sentences_are_not_generic():
    assert not is_generic_oneliner(_item().what_happened, _item().body)
    assert item_drop_reasons(_item()) == []


def test_missing_or_private_url_is_dropped():
    assert not is_public_http_url("")
    assert not is_public_http_url("http://localhost/secret")
    assert not is_public_http_url("https://hooks.slack.com/services/T/B/x")
    assert not is_public_http_url("https://notes.internal/post")
    assert is_public_http_url("https://www.reuters.com/world/example")
    assert "missing public source url" in item_drop_reasons(_item(url="not a url"))


def test_together_internal_blocks_the_issue():
    item = _item(why_it_matters="Why it matters for Together: revenue from customers is up.")
    reasons = publishing_blocks(_issue([item]))
    assert "together internal or non-public" in reasons


def test_unsafe_or_secret_text_blocks_the_issue():
    secret = _item(body="The token is sk-" + "a" * 24 + ". " + _item().body)
    assert "unsafe content" in publishing_blocks(_issue([secret]))
    private = _item(why_it_matters="This note is confidential and internal only.")
    assert "unsafe content" in publishing_blocks(_issue([private]))


def test_empty_issue_is_too_short():
    assert "too short" in publishing_blocks(_issue([]))


def test_duplicate_of_the_last_issue_blocks():
    issue = _issue()
    recent = [{"items": [{"title": issue.items[0].what_happened, "url": issue.items[0].url}]}]
    assert "duplicate of the last issue" in publishing_blocks(issue, recent=recent)


def test_schema_failure_is_reported():
    reasons = publishing_blocks(
        _issue([]),
        schema_error="LLM output failed schema validation: missing body",
    )
    assert any(reason.startswith("LLM output failed schema validation") for reason in reasons)


def test_second_send_in_the_same_slot_is_blocked_only_when_sending():
    now = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)
    slot = slot_key(now)
    assert slot == "2026-10-19T15:00:00Z"
    issue = _issue()
    assert "idempotency" not in publishing_blocks(issue, sending=False, last_slot=slot, now=now)
    assert "idempotency" in publishing_blocks(issue, sending=True, last_slot=slot, now=now)
    assert "idempotency" not in publishing_blocks(
        issue, sending=True, last_slot="2026-10-16T15:00:00Z", now=now
    )
