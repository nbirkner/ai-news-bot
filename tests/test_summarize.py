import json
import re
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
    assert "body needs at least 2 sentences" in message
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


def test_filler_why_names_the_story_instead_of_a_slogan():
    from src.summarize import is_criteria_echo, is_filler_why, specific_why
    slogan = "For people building with AI, this is worth a look if you ship or run models."
    assert is_filler_why(slogan)
    echo = specific_why(_article("llm-d v1.0: distributed inference", "llm-d is a Kubernetes project."))
    assert "llm-d" in echo
    assert is_criteria_echo(echo)


def test_a_legal_or_policy_template_is_not_a_why_line():
    from src.summarize import _WHY, _usable_why, is_filler_why
    article = _article(
        "Nscale and Anthropic are in talks over a second cloud deal",
        "Nscale and Anthropic are discussing a second cloud agreement.",
    )
    template = _WHY["policy"]
    assert "legal or policy" in template
    assert is_filler_why(template)
    why = _usable_why(template, "Nscale and Anthropic are discussing a second cloud agreement.", article)
    assert why != template
    assert "legal or policy" not in (why or "").lower()


def test_ranking_timeout_publishes_nothing(monkeypatch):
    import requests
    article = _article(
        "Nscale and Anthropic are in talks over a second cloud deal",
        "Skip Navigation Markets. Nscale and Anthropic are in talks over a cloud deal worth about $45 billion.",
    )
    article.full_text = article.content_preview

    def boom(prompt, api_key, model):
        raise requests.Timeout("read timeout=120")

    monkeypatch.setattr("src.summarize._call_together", boom)
    with pytest.raises(SchemaError) as exc:
        compose_briefing(
            [article],
            "sk-test",
            "zai-org/GLM-5.3",
            strict_auth=True,
            require_full_body=True,
        )
    message = str(exc.value)
    assert "Not publishing" in message
    assert "Skip Navigation" not in message
    assert article.title not in message


def test_read_timeout_retries_with_backoff(monkeypatch):
    import requests
    timeouts = []
    sleeps = []

    def post(url, headers=None, json=None, timeout=None):
        timeouts.append(timeout)
        raise requests.Timeout("read timeout=120")

    monkeypatch.setattr("src.summarize.requests.post", post)
    monkeypatch.setattr("src.summarize.time.sleep", lambda seconds: sleeps.append(seconds))
    with pytest.raises(requests.Timeout):
        _call_together("hello", "sk-test", "zai-org/GLM-5.3")
    assert timeouts == [180, 240, 300]
    assert sleeps == [8, 16]


def test_a_long_shortlist_is_ranked_in_batches(monkeypatch):
    prompts = []

    def fake_call(prompt, api_key, model):
        prompts.append(prompt)
        return json.dumps({"lede": "", "items": []})

    articles = []
    for index in range(10):
        articles.append(_article(
            f"Lab {index} releases a model with a stated price",
            f"Lab {index} released a model. The price is ${index} per million tokens.",
        ))
        articles[-1].url = f"https://example.com/lab-{index}"
    monkeypatch.setattr("src.summarize._call_together", fake_call)
    lede, pairs = compose_briefing(
        articles,
        "sk-test",
        "zai-org/GLM-5.3",
        strict_auth=True,
        require_full_body=True,
    )
    assert lede == ""
    assert pairs == []
    assert len(prompts) == 2
    for prompt in prompts:
        start = prompt.find("Candidates:\n")
        blob = prompt[start + len("Candidates:\n"):]
        rows = json.loads(blob[: blob.find("\n\nReturn:")])
        assert 1 <= len(rows) <= 8


def test_a_prefixed_consequence_keeps_the_consequence():
    from src.summarize import _usable_why, is_criteria_echo
    article = _article(
        "Open-weight models took most of the tokens",
        "Open-weight models handled 71.5% of tokens.",
    )
    prefixed = "For people building with AI, a cheap token and an expensive closed model are different bills."
    why = _usable_why(prefixed, "Open-weight models handled 71.5% of tokens.", article)
    assert not is_criteria_echo(why)
    assert "different bills" in why


def test_a_criteria_echo_is_replaced_with_what_the_product_is():
    from src.summarize import _usable_why, is_criteria_echo
    echo = "A builder or an AI exec would bring up llm-d v1.0: distributed inference this week."
    assert is_criteria_echo(echo)
    article = _article(
        "llm-d v1.0: distributed inference",
        "llm-d is a Kubernetes-native stack for serving a model across nodes.",
    )
    article.full_text = (
        "llm-d is a Kubernetes-native stack for serving a model across nodes. "
        "Version 1.0 is now stable."
    )
    why = _usable_why(echo, "Version 1.0 is now stable.", article)
    assert not is_criteria_echo(why)
    assert "kubernetes" in why.lower()


def test_a_raw_page_title_is_rewritten():
    from src.summarize import headline_is_raw, rewrite_headline
    title = "llm-d v1.0: a long page title that should not ship as the headline unchanged"
    assert headline_is_raw(title, title)
    rewritten = rewrite_headline(title)
    assert rewritten != title
    assert len(rewritten) < len(title)


def test_expectation_framing_drops_unless_the_wire_says_it():
    from src.summarize import strip_unverified_expectation
    article = _article(
        "OpenAI races to raise $30 billion",
        "OpenAI is pitching $50 billion of annualized revenue.",
        kind="news",
    )
    article.source_domain = "pymnts.com"
    body = (
        "OpenAI is pitching investors on $50 billion of annualized revenue. "
        "That is about $20 billion less than expected."
    )
    cleaned = strip_unverified_expectation(body, article)
    assert "less than expected" not in cleaned.lower()
    assert "$50 billion" in cleaned


def test_a_lede_cut_off_mid_phrase_is_replaced():
    from datetime import timedelta
    from src.summarize import _DANGLING_END, fit_lede, theme_lede
    since = datetime(2026, 10, 19, 15, tzinfo=timezone.utc)
    until = since + timedelta(hours=27)
    chopped = "Since Monday morning, Microsoft told enterprises to treat AI agents as."
    assert _DANGLING_END.search(chopped)
    article = _article("Microsoft says AI agents are insider threats", "Defender covers 60 agent types.")
    lede = fit_lede(theme_lede([article]), since, until)
    assert not _DANGLING_END.search(lede)
    assert lede.endswith(".")


def test_a_generic_lede_is_replaced_with_the_top_stories():
    from datetime import timedelta
    from src.summarize import fit_lede, lede_names_stories, story_lede
    since = datetime(2026, 10, 19, 15, tzinfo=timezone.utc)
    until = since + timedelta(hours=27)
    loss = _article(
        "Anthropic logged net loss of over $80 billion",
        "Anthropic logged a net loss of over $80 billion.",
        kind="news",
    )
    nscale = _article(
        "Nscale and Anthropic in talks for a $45 billion deal",
        "Nscale and Anthropic are in talks.",
        kind="news",
    )
    nscale.url = "https://www.reuters.com/nscale"
    deep = _article(
        "Federal agencies must remove DeepSeek from civilian systems",
        "The order removes DeepSeek.",
        kind="news",
    )
    deep.url = "https://www.federalregister.gov/deepseek"
    rows = [
        (loss, "Anthropic logged net loss of over $80 billion", "", ""),
        (nscale, "Nscale and Anthropic in talks for a $45 billion deal", "", ""),
        (deep, "Federal agencies must remove DeepSeek from civilian systems", "", ""),
    ]
    generic = "This window is mostly about funding and policy."
    assert not lede_names_stories(generic, rows)
    named = story_lede(rows)
    assert "Anthropic's reported $80 billion loss" in named
    assert "the Nscale deal" in named
    assert "an order removing DeepSeek" in named
    assert "mostly about" not in named.lower()
    assert not re.search(r"\bare Anthropic logged\b", named)
    assert not re.search(r"\b(shows|logged|must)\b", named)
    broken = "The top stories are Anthropic's reported loss shows an $80 billion hole."
    assert not lede_names_stories(broken, rows)
    assert lede_names_stories(named, rows)
    fitted = fit_lede(named, since, until)
    assert fitted.startswith("Since Monday morning")
    assert "this week" not in fitted.lower()
    assert "\u2013" not in fitted and "\u2014" not in fitted


def test_a_loss_why_names_charges_only_when_the_source_does():
    from src.summarize import _usable_why
    article = _article(
        "Anthropic logged net loss of over $80 billion in first half of 2026, WSJ reports",
        "Anthropic logged a net loss.",
        kind="news",
    )
    article.full_text = (
        "Anthropic logged a net loss of more than $80 billion in the first half. "
        "That includes $72 billion in accounting charges on convertible notes."
    )
    why = _usable_why("Anthropic's reported loss changes what the lab can spend.", article.full_text, article)
    assert "$72 billion in accounting charges" in why
    assert "non-cash" not in why.lower()
    cash = _article(
        "Anthropic logged net loss of over $80 billion, WSJ reports",
        "Most of the loss is non-cash charges.",
        kind="news",
    )
    cash.full_text = "Anthropic logged a net loss of over $80 billion. Most of the loss is non-cash charges."
    cash_why = _usable_why("The loss changes what the lab can spend.", cash.full_text, cash)
    assert "non-cash charges" in cash_why.lower()
    assert cash_why.lower().startswith("most of")
    plain = _article(
        "Anthropic logged net loss of over $80 billion, WSJ reports",
        "Anthropic logged a net loss of over $80 billion.",
        kind="news",
    )
    plain.full_text = (
        "Anthropic logged a net loss of over $80 billion in the first half. "
        "The figure was in a draft prospectus."
    )
    plain_why = _usable_why("", plain.full_text, plain)
    assert "non-cash" not in (plain_why or "").lower()
    assert "accounting charges" not in (plain_why or "").lower()
    rich = _article(
        "Anthropic logged net loss of over $80 billion in first half of 2026, WSJ reports",
        "Anthropic logged a net loss.",
        kind="news",
    )
    rich.full_text = (
        "Anthropic logged a net loss of more than $80 billion in the first half. "
        "Most of the $72 billion is non-cash accounting charges. "
        "The figure to watch is operating burn in the S-1."
    )
    rich_why = _usable_why(
        "Anthropic's reported loss changes what the lab can spend.",
        rich.full_text,
        rich,
    )
    assert "changes what the lab can spend" not in rich_why.lower()
    assert rich_why.lower().startswith("most of the $72 billion")
    assert "non-cash accounting charges" in rich_why.lower()
    assert "operating burn in the s-1" in rich_why.lower()
    wire = _article(
        "Anthropic logged net loss of over $80 billion in first half of 2026, WSJ reports",
        "Anthropic logged a net loss.",
        kind="news",
    )
    wire.full_text = (
        "Anthropic recorded a net loss of more than $80 billion in the first half, "
        "including $72 billion in accounting charges. "
        "The vast majority of this year's net loss, $72 billion, resulted from accounting charges."
    )
    wire_why = _usable_why("Anthropic's reported loss changes what the lab can spend.", wire.full_text, wire)
    assert "changes what the lab can spend" not in wire_why.lower()
    assert "$72 billion" in wire_why
    assert "accounting charges" in wire_why.lower()
    assert "non-cash" not in wire_why.lower()
    assert "s-1" not in wire_why.lower()
    assert "operating burn" not in wire_why.lower()


def test_a_quote_is_attributed_only_to_the_person_who_said_it():
    from src.summarize import fix_quote_attribution, sentence_misattributes
    source = (
        "Microsoft security chief Hayete Gallot was even more straightforward. "
        '"Unfortunately, frontier models, regardless of their origin, cannot be trusted," she wrote. '
        "On top of that, they are often given the keys to the kingdom, with access to data and tools that could do serious harm."
    )
    bad = 'Gallot wrote that agents are "often given access to data and tools that could do serious harm."'
    assert sentence_misattributes(bad, source)
    fixed = fix_quote_attribution(bad, source)
    assert not re.search(r"Gallot.{0,80}serious harm", fixed)
    good = 'Gallot wrote, "Unfortunately, frontier models, regardless of their origin, cannot be trusted."'
    assert not sentence_misattributes(good, source)
    kept = fix_quote_attribution(good, source)
    assert "Gallot" in kept
    assert "cannot be trusted" in kept


def test_a_dollar_price_is_not_called_cents_per_million():
    from src.summarize import fix_price_units, price_unit_mismatch
    source = "Flat pricing per 1M tokens: $0.15 input and $0.60 output."
    why = "This frontier-lab model costs cents per million tokens."
    assert price_unit_mismatch(why, source)
    fixed = fix_price_units(why, source)
    assert "cents" not in fixed.lower()
    assert "frontier-lab" not in fixed.lower()
    assert "under a dollar per million tokens" in fixed.lower()
    cheap_source = "Cached input is $0.02 per million tokens."
    cheap = "The cached price is 2 cents per million tokens."
    assert not price_unit_mismatch(cheap, cheap_source)
    assert "cents" in fix_price_units(cheap, cheap_source).lower()


def test_the_lede_names_stories_in_rank_order():
    from src.summarize import lede_follows_rank, story_lede
    loss = _article(
        "Anthropic logged net loss of over $80 billion",
        "Anthropic logged a net loss.",
        kind="news",
    )
    nscale = _article(
        "Nscale and Anthropic in talks for a $45 billion deal",
        "Nscale and Anthropic are in talks.",
        kind="news",
    )
    deep = _article(
        "Federal agencies must remove DeepSeek from civilian systems",
        "The order removes DeepSeek.",
        kind="news",
    )
    rows = [
        (loss, "Anthropic logged net loss of over $80 billion", "", ""),
        (nscale, "Nscale and Anthropic in talks for a $45 billion deal", "", ""),
        (deep, "Federal agencies must remove DeepSeek from civilian systems", "", ""),
    ]
    named = story_lede(rows)
    assert lede_follows_rank(named, rows)
    swapped = (
        "The top stories are an order removing DeepSeek, the Nscale deal, "
        "and Anthropic's reported $80 billion loss."
    )
    assert not lede_follows_rank(swapped, rows)


def test_nvidia_china_orders_stay_with_anthropic_and_nscale():
    from src.summarize import _backfill_clear_bar
    loss = _article(
        "Anthropic logged net loss of over $80 billion in first half of 2026, WSJ reports",
        "Anthropic logged a net loss.",
        kind="news",
    )
    loss.url = "https://www.reuters.com/anthropic-loss"
    loss.full_text = (
        "Anthropic logged a net loss of more than $80 billion in the first half of 2026. "
        "That includes $72 billion in accounting charges on convertible notes."
    )
    nscale = _article(
        "Nvidia-backed Nscale, Anthropic in talks for new $45 billion AI cloud deal",
        "Nscale is in talks with Anthropic.",
        kind="news",
    )
    nscale.url = "https://www.reuters.com/nscale-anthropic"
    nscale.full_text = (
        "Nscale is in talks with Anthropic on a cloud deal worth about $45 billion. "
        "The talks cover a second site that would use Nvidia chips."
    )
    chips = _article(
        "ByteDance, Alibaba and Tencent order more than $35 billion of Nvidia chips",
        "ByteDance, Alibaba and Tencent ordered Nvidia chips.",
        kind="news",
    )
    chips.url = "https://www.reuters.com/nvidia-china-orders"
    chips.full_text = (
        "ByteDance, Alibaba and Tencent have ordered Nvidia chips priced at more than $35 billion. "
        "The orders are for inference hardware the companies cannot fill with older stock."
    )
    lesser = _article(
        "Otter.ai raises $320 million to expand its enterprise business",
        "Otter closed a financing round.",
        kind="news",
    )
    lesser.url = "https://example.com/otter"
    pairs = [
        (loss, "Anthropic logged a large loss", loss.full_text, "Anthropic's reported loss includes accounting charges."),
        (nscale, "Nscale and Anthropic are in talks", nscale.full_text, "The Nscale deal changes how much compute Anthropic can buy."),
        (lesser, "Otter raised $320 million", "Otter closed a $320 million financing round for enterprise software. The company said the round expands its sales team.", "The round funds a larger sales team."),
    ]
    ranked = _backfill_clear_bar([loss, nscale, chips, lesser], pairs, limit=3)
    titles = [row[0].title for row in ranked]
    assert any("net loss" in title for title in titles)
    assert any("Nscale" in title for title in titles)
    assert any("Nvidia" in title and "$35 billion" in title for title in titles)
    assert all("Otter" not in title for title in titles)


def test_a_major_deal_is_kept_ahead_of_llm_d():
    from src.summarize import _backfill_clear_bar
    loss = _article(
        "Anthropic logged net loss of over $80 billion in first half of 2026, WSJ reports",
        "Anthropic logged a net loss.",
        kind="news",
    )
    loss.url = "https://www.reuters.com/anthropic-loss"
    loss.source_domain = "reuters.com"
    loss.full_text = (
        "Anthropic logged a net loss of over $80 billion in the first half of 2026. "
        "The Wall Street Journal cited a draft prospectus shared with investors."
    )
    nscale = _article(
        "Nvidia-backed Nscale, Anthropic in talks for new $45 billion AI cloud deal",
        "Nscale is in talks with Anthropic.",
        kind="news",
    )
    nscale.url = "https://www.reuters.com/nscale-anthropic"
    nscale.source_domain = "reuters.com"
    nscale.full_text = (
        "Nscale is in talks with Anthropic on a cloud deal worth about $45 billion. "
        "The talks cover a second site that would use Nvidia chips."
    )
    llmd = _article(
        "llm-d v1.0 brings distributed inference to Kubernetes",
        "llm-d v1.0 is a serving release.",
        kind="release",
    )
    llmd.url = "https://github.com/llm-d/llm-d"
    llmd.full_text = (
        "llm-d v1.0 is a Kubernetes-native stack for serving a model across nodes. "
        "The release adds a scheduler for distributed inference."
    )
    pairs = [(
        llmd,
        "llm-d v1.0 adds a scheduler",
        llmd.full_text,
        "Teams serving a model across nodes have a new scheduler to try.",
    )]
    ranked = _backfill_clear_bar([loss, nscale, llmd], pairs, limit=3)
    titles = [row[0].title for row in ranked]
    assert any("net loss" in title for title in titles)
    assert any("Nscale" in title for title in titles)
    assert titles[-1] == llmd.title
    assert titles.index(loss.title) < titles.index(llmd.title)
    tight = _backfill_clear_bar([loss, nscale, llmd], pairs, limit=2)
    tight_titles = [row[0].title for row in tight]
    assert llmd.title not in tight_titles
    assert any("net loss" in title for title in tight_titles)
    assert any("Nscale" in title for title in tight_titles)


def test_a_short_window_lede_does_not_say_this_week():
    from datetime import timedelta
    from src.summarize import fit_lede
    since = datetime(2026, 10, 19, 15, tzinfo=timezone.utc)
    until = since + timedelta(hours=27)
    lede = fit_lede("This week brings a federal order and a funding round.", since, until)
    assert lede.startswith("Since Monday morning")
    assert "this week" not in lede.lower()


def test_a_truncated_why_does_not_ship():
    from src.summarize import _usable_why
    article = _article("Otter.ai raises $320 million", "Otter closed the round.", kind="news")
    chopped = "The repayable structure will test whether meeting transcription can be a."
    assert _usable_why(chopped, "Otter closed a $320 million round.", article) != chopped
    assert not (_usable_why(chopped, "Otter closed a $320 million round.", article) or "").endswith(" a.")


def test_syndication_search_drops_the_exclusive_label():
    from src.read import syndication_queries
    queries = syndication_queries(
        "EXCLUSIVE: OpenAI seeks to accelerate fundraising ahead of Anthropic IPO, sources say"
    )
    assert queries
    assert "EXCLUSIVE" not in queries[0]
    assert "OpenAI seeks to accelerate fundraising" in queries[0]


def test_syndication_prefers_marketscreener_and_skips_the_homepage():
    from src.read import choose_syndication, syndication_preference
    home = "https://www.marketscreener.com/quote/stock/ORCHESTRA-BIOMED-INC-1/"
    yahoo = "https://finance.yahoo.com/news/nscale-anthropic-deal-1.html"
    screen = "https://www.marketscreener.com/news/nscale-anthropic-deal-1/"
    assert syndication_preference(screen) < syndication_preference(yahoo)
    text, url = choose_syndication([
        ("Y" * 500, yahoo),
        ("H" * 500, home),
        ("M" * 450, screen),
    ])
    assert url == screen
    assert text.startswith("M")


def test_investing_com_is_read_from_syndication(monkeypatch):
    from src.fetch import _readable_excerpt
    from src.read import _syndication_first

    assert _syndication_first("https://www.investing.com/news/reuters-nscale")
    assert _syndication_first("https://www.reuters.com/technology/nscale")
    assert not _syndication_first("https://www.cnbc.com/2026/10/20/nscale.html")

    def boom(*args, **kwargs):
        raise AssertionError("Investing.com was fetched directly")

    monkeypatch.setattr("src.fetch.http_get", boom)
    monkeypatch.setattr(
        "src.read.syndication_text",
        lambda title, original_url="": (
            "Nscale and Anthropic are in talks. " * 30,
            "https://uk.marketscreener.com/news/nscale-anthropic-45-billion/",
        ),
    )
    text, url, name, domain = _readable_excerpt(
        "https://www.investing.com/news/reuters-nscale",
        "Nscale and Anthropic in talks for a cloud deal",
    )
    assert "marketscreener.com" in url
    assert name == "MarketScreener"
    assert "Nscale" in text
    assert "investing.com" not in (domain or "")


def test_a_401_uses_the_same_fallback_as_a_403():
    from requests import HTTPError
    from src.read import _access_denied

    class Response:
        status_code = 401

    denied = HTTPError("401 Client Error: Unauthorized")
    denied.response = Response()
    assert _access_denied(denied)
    blocked = HTTPError("403 Client Error: Forbidden")
    blocked.response = Response()
    blocked.response.status_code = 403
    assert _access_denied(blocked)


def test_unjoined_money_figures_are_rejected_and_the_timeline_is_restored():
    from src.summarize import drop_unjoined_numbers, has_unjoined_numbers, _usable_why
    garbled = (
        "OpenAI's annualized revenue was nearing $50 billion at the end of September $70 billion."
    )
    assert has_unjoined_numbers(garbled)
    joined = (
        "Annualized revenue was nearing $50 billion at the end of September, "
        "and $70 billion is expected by year-end."
    )
    assert not has_unjoined_numbers(joined)
    article = _article(
        "OpenAI races to raise $30 billion",
        "Reuters reported the figures.",
        kind="news",
    )
    article.source_domain = "reuters.com"
    article.full_text = (
        "OpenAI's annualized revenue was nearing $50 billion at the end of September. "
        "The company expects annualized revenue of $70 billion by year-end."
    )
    cleaned = drop_unjoined_numbers(garbled, article)
    assert "September $" not in cleaned
    assert "$50 billion" in cleaned and "$70 billion" in cleaned
    assert " and " in cleaned
    partial = (
        "OpenAI is pitching a round of about $30 billion. "
        "It had reached $50 billion in annualized revenue and expects $70 billion by year-end."
    )
    restored = drop_unjoined_numbers(partial, article)
    assert "end of September" in restored
    assert "$30 billion" in restored
    from src.clean import split_sentences
    assert all(not has_unjoined_numbers(sentence) for sentence in split_sentences(restored))
    shortfall = "The revenue shortfall changes how the round is priced."
    assert _usable_why(shortfall, cleaned, article) != shortfall
    assert "shortfall" not in (_usable_why(shortfall, cleaned, article) or "").lower()


def test_a_removal_order_gets_a_short_lead_and_a_specific_why():
    from src.summarize import _order_lead, _order_why
    title = (
        "Notice of Order Regarding Exclusion and Removal of DeepSeek Products "
        "From Federal Civilian Executive Branch Information Technology Systems and Procurements"
    )
    article = _article(title, title, kind="news")
    article.source_domain = "federalregister.gov"
    lead = _order_lead(article)
    why = _order_why(article)
    assert lead == "Federal agencies must remove DeepSeek from civilian systems."
    assert lead.endswith(".")
    assert lead != title
    assert "Notice of Order" not in lead
    assert "Exclusion and Removal" not in lead
    assert not lead.lower().startswith("order excludes")
    article.full_text = (
        "The order removes DeepSeek products and third-party products developed "
        "using DeepSeek from civilian agency information technology."
    )
    why = _order_why(article)
    assert "audit" in why.lower()
    assert "DeepSeek" in why
    assert why != "Vendors must audit which models sit under their products."
    assert "bring up" not in why
    from src.summarize import _usable_why
    paraphrase = "Vendors selling into federal agencies must check every product they offer."
    assert _usable_why(paraphrase, "The order removes the products.", article) == why


def test_a_cost_cut_is_not_labeled_as_a_benchmark():
    from src.summarize import label_benchmark
    article = _article(
        "MiniMax M3.1 Flash",
        "The evaluation reports 73.7% on the coding benchmark.",
        kind="lab",
    )
    article.source_domain = "minimax.io"
    article.full_text = "The evaluation reports 73.7% on the coding benchmark."
    cost_only = "It cuts agentic costs by 70% at $0.15 per million tokens. Weights ship later."
    assert "vendor-reported" not in label_benchmark(cost_only, article)
    scored = "It scores 73.7% on the coding benchmark. Weights ship later."
    labeled = label_benchmark(scored, article)
    assert "benchmark, a company-reported figure." in labeled
    assert "Weights ship later." in labeled


def test_extractive_uses_a_source_why_line_when_it_is_present():
    article = _article(
        "AI tools are aiding would-be spies",
        "AI tools are now used to spy on employers. Why it matters: The same tools can expose internal systems.",
        kind="news",
    )
    what, why = extractive_lines(article)
    assert what.startswith("AI tools are now used")
    assert why.startswith("The same tools can expose")
