import json
import re
import sys

import requests

from src.clean import (
    clean_line,
    first_sentence,
    is_thin_preview,
    line_is_grounded,
    shares_distinctive_word,
    split_sentences,
)
from src.usage import record_llm_call
from src.models import Article

TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"

# GLM-5.3 always thinks, and the trace shares this budget with the answer.
# Default effort is "max". `reasoning: {enabled: false}` is ignored (HTTP 200),
# so a 4500 cap is spent on reasoning_content and `content` comes back empty
# or as a few dozen tokens. That fails the 3-sentence body check.
# "low" keeps the trace short. 8192 leaves room for the JSON answer.
_FIRST_MAX_TOKENS = 8192
_RETRY_MAX_TOKENS = 16384

_WHY = {
    "policy": "For people building with AI, a legal or policy move changes the constraints on shipping a product.",
    "safety": "For people building with AI, a safety or security development changes the risks you have to plan for.",
    "deal": "For people building with AI, a deal can shift who has the models, the compute, or the distribution.",
    "model": "For people building with AI, a model release changes what a product can do and what it costs to serve.",
    "infra": "For people building with AI, an inference or hardware change affects the cost and latency of running models.",
    "agents": "For people building with AI, an agent update changes how models connect to tools and longer tasks.",
    "paper": "For people building with AI, this is a research result worth a look if you train or evaluate models.",
    "release": "For people building with AI, a serving-stack release matters only when it changes how models are deployed.",
    "other": "For people building with AI, this is worth a look if you ship or run models.",
}

_TOPICS = (
    ("policy", re.compile(
        r"\b(sues?|sued|lawsuit|regulat\w*|midterms?|election|legislation|"
        r"bankrupt\w*|antitrust|polic(?:y|ies)|\bmou\b)\b",
        re.I,
    )),
    ("safety", re.compile(
        r"\b(safe\w*|secur\w*|jailbreak\w*|spies|spy\b|espionage|cyber\w*|"
        r"vulnerabilit\w*|misuse|breach)\b",
        re.I,
    )),
    ("deal", re.compile(r"\b(acqui\w*|merger|funding round|series [a-e])\b", re.I)),
    ("model", re.compile(
        r"\b(gpt-\d|claude \d|gemini \d|llama \d|mistral large|foundation model|"
        r"frontier model|\bmodel\b)\b",
        re.I,
    )),
    ("infra", re.compile(r"\b(vllm|sglang|inference|gpu|chip|cuda|blackwell|serving)\b", re.I)),
    ("agents", re.compile(r"\bagents?\b", re.I)),
)


class AuthError(Exception):
    """The Together key or model name was rejected. Do not post a fallback."""


class SchemaError(Exception):
    """The model reply is not the issue schema. Do not publish it."""


def _first_person(sentence: str) -> bool:
    return bool(re.match(r"^(we|we're|we’re|our|i|i'm|i’m|my)\b", sentence.strip(), re.I))


def _why_for(article: Article) -> str:
    title = article.title or ""
    if re.search(r"\b(sues?|sued|lawsuit)\b", title, re.I):
        return "A lawsuit changes the legal risk of shipping a model to the public."
    if re.search(r"\b(midterms?|election|voting|ballot)\b", title, re.I):
        return "Election season is a constraint on how public models are shipped and monitored."
    if re.search(r"\bbankrupt", title, re.I):
        return "A bankruptcy can decide what happens to the product and to the people who use it."
    if re.search(r"\b(spies|spy|espionage|jailbreak)\b", title, re.I):
        return "A security hole in model tools is a risk for any company that gives them access to internal systems."
    return _WHY[topic_for(article)]


def topic_for(article: Article) -> str:
    text = f"{article.title} {article.content_preview}"
    for name, pattern in _TOPICS:
        if pattern.search(text):
            return name
    if article.source_kind == "paper":
        return "paper"
    if article.source_kind == "release":
        return "release"
    return "other"


def _source_text(article: Article) -> str:
    parts = [article.title or ""]
    details = getattr(article, "details", "") or ""
    full_text = getattr(article, "full_text", "") or ""
    if details:
        parts.append(details)
    if full_text:
        parts.append(full_text)
    if article.content_preview:
        parts.append(article.content_preview)
    return "\n".join(parts)


def extractive_lines(article: Article) -> tuple:
    """Fact line from the source text, plus a short builder note with no new facts."""
    preview = article.content_preview or ""
    why_from_source = ""
    body = preview
    match = re.search(r"Why it matters:\s*(.+)", preview, re.I | re.S)
    if match:
        why_from_source = first_sentence(match.group(1))
        body = preview[: match.start()]
    lede = first_sentence(body) if not is_thin_preview(body, article.title) else ""
    title_line = clean_line(article.title)
    lede_ok = (
        bool(lede)
        and not _first_person(lede)
        and shares_distinctive_word(lede, article.title)
        and lede.lower().strip(".") != title_line.lower().strip(".")
    )
    what = lede if lede_ok else title_line
    source = _source_text(article)
    if why_from_source and line_is_grounded(why_from_source, source, analysis=True):
        why = why_from_source
    else:
        why = _why_for(article)
    return what, why


def _completion_meta(body: dict) -> dict:
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = body.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    return {
        "content": (message.get("content") or "").strip(),
        "finish_reason": choice.get("finish_reason") or "",
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "reasoning_tokens": int(details.get("reasoning_tokens") or 0),
    }


def _answer_was_cut_off(meta: dict) -> bool:
    """True when the answer never landed, or length cut the JSON in half."""
    if not meta["content"]:
        return True
    if meta["finish_reason"] != "length":
        return False
    return _parse_json_object(meta["content"]) is None


def _truncation_error(meta: dict) -> SchemaError:
    return SchemaError(
        "LLM output failed schema validation: body was truncated "
        f"(finish_reason={meta['finish_reason'] or 'unknown'}, "
        f"completion_tokens={meta['completion_tokens']}, "
        f"reasoning_tokens={meta['reasoning_tokens']}). "
        "The answer budget was used up before a full body was written."
    )


def _call_together(prompt: str, api_key: str, model: str) -> str:
    def send(payload: dict):
        return requests.post(
            TOGETHER_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=90,
        )

    def once(max_tokens: int) -> dict:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            # Thinking stays on. Low effort is the short trace this write-up needs.
            "reasoning_effort": "low",
        }
        response = send(payload)
        # A non-GLM override may reject the effort dial. Drop only that field.
        if response.status_code == 400 and "reasoning" in (response.text or "").lower():
            payload.pop("reasoning_effort", None)
            response = send(payload)
        if response.status_code in (401, 403):
            raise AuthError(response.text[:300])
        if response.status_code == 400 and "model" in response.text.lower():
            raise AuthError(response.text[:300])
        response.raise_for_status()
        meta = _completion_meta(response.json())
        record_llm_call(model, meta["prompt_tokens"], meta["completion_tokens"])
        return meta

    meta = once(_FIRST_MAX_TOKENS)
    if _answer_was_cut_off(meta):
        print(
            "Warning: Together answer was cut off "
            f"(finish_reason={meta['finish_reason'] or 'unknown'}, "
            f"completion_tokens={meta['completion_tokens']}, "
            f"reasoning_tokens={meta['reasoning_tokens']}). "
            f"Retrying with max_tokens={_RETRY_MAX_TOKENS}.",
            file=sys.stderr,
        )
        meta = once(_RETRY_MAX_TOKENS)
    if _answer_was_cut_off(meta):
        raise _truncation_error(meta)
    return meta["content"]


def _parse_json_object(text: str) -> dict | None:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _item_payload(article: Article, index: int) -> dict:
    text = getattr(article, "full_text", "") or article.content_preview or ""
    return {
        "index": index,
        "source": article.source_name,
        "title": article.title,
        "url": article.url,
        "published": article.published_at.isoformat(),
        "details": getattr(article, "details", "") or "",
        "text": text[:4000],
    }


def _prompt(articles: list) -> str:
    items = [_item_payload(article, index) for index, article in enumerate(articles)]
    return f"""You write a public AI news briefing for a general audience of people building with AI.

Rules:
- Use only the title, details, and text for that item. Do not add facts, numbers, quotes, or names that are not in that text.
- headline: one specific claim, not a version number alone.
- body: 3 to 5 sentences of concrete facts from that item's text. A body under 3 sentences is invalid. Keep prices, benchmark numbers, dates, and availability. If the text says a figure is company-reported or unaudited, keep that caveat. If the text cannot support 3 factual sentences, omit the item.
- why_it_matters: one plain sentence for people building with AI. Interpretation is allowed. New facts are not.
- Public information only. No private companies' internal notes, customers, or revenue, and no product pitch.
- No hype. No em dashes. Do not use game-changer, unlock, delve, landscape, pivotal, or groundbreaking.
- Do not write a generic line such as "it adds new features" unless the text names the features.
- Keep each sentence under 380 characters.
- Return JSON only.

Items:
{json.dumps(items, ensure_ascii=True)}

Return:
{{"items": [{{"index": 0, "headline": "...", "body": "...", "why_it_matters": "..."}}]}}"""


def _accept(line: str, article: Article, *, analysis: bool, fallback: str) -> str:
    cleaned = clean_line(line)
    # Thin excerpts used to force the bare headline. Check the full source instead.
    if line_is_grounded(cleaned, _source_text(article), analysis=analysis):
        return cleaned
    return fallback


def write_copies(articles: list, api_key: str | None, model: str, *, strict_auth: bool) -> list:
    fallbacks = [extractive_lines(article) for article in articles]
    if not articles or not api_key:
        return fallbacks
    try:
        raw = _call_together(_prompt(articles), api_key, model)
    except AuthError:
        if strict_auth:
            raise
        print("Warning: Together auth failed. Using source text.", file=sys.stderr)
        return fallbacks
    except Exception as exc:
        print(f"Warning: Together request failed ({exc}). Using source text.", file=sys.stderr)
        return fallbacks

    payload = _parse_json_object(raw)
    rows = payload.get("items") if payload else None
    if not isinstance(rows, list):
        print("Warning: Together returned no items. Using source text.", file=sys.stderr)
        return fallbacks

    by_index = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("index"), int):
            by_index[row["index"]] = row

    copies = []
    for index, article in enumerate(articles):
        fallback_what, fallback_why = fallbacks[index]
        row = by_index.get(index)
        if not row:
            copies.append((fallback_what, fallback_why))
            continue
        what = _accept(str(row.get("what_happened") or ""), article, analysis=False, fallback=fallback_what)
        why = _accept(str(row.get("why_it_matters") or ""), article, analysis=True, fallback=fallback_why)
        copies.append((what, why))
    return copies


def theme_lede(articles: list) -> str:
    """One sentence from themes that are actually in the titles. No new names."""
    blob = " ".join(article.title or "" for article in articles).lower()
    themes = []
    if re.search(r"model|gpt-|claude|gemini|deepseek|qwen|kimi|llama|weights", blob):
        themes.append("new models")
    if re.search(r"agent", blob):
        themes.append("agent tools")
    if re.search(r"chip|gpu|inference|data center|power|grid", blob):
        themes.append("infrastructure")
    if re.search(r"bill|regulat|lawsuit|policy|safety|workforce", blob):
        themes.append("policy")
    if re.search(r"raise|funding|billion|acqui", blob):
        themes.append("funding")
    if not themes:
        return "A handful of developments for people building and running AI systems."
    if len(themes) == 1:
        body = themes[0]
    else:
        body = ", ".join(themes[:-1]) + " and " + themes[-1]
    return f"For people building and running AI systems, this window is mostly about {body}."


def _rank_prompt(articles: list, recent: list, limit: int) -> str:
    items = [_item_payload(article, index) for index, article in enumerate(articles)]
    already = [
        {"title": item.get("title") or "", "url": item.get("url") or ""}
        for item in (recent or [])
        if item.get("title")
    ]
    return f"""You rank and write one public AI news briefing for people building with AI.

Compare the candidates against each other. Order is not a ranking.
Choose stories that change what someone can ship, what it costs to run a model, or what they have to comply with.
Prefer a material story over a routine patch note. Include a release note only when the text names a concrete change that matters this week.
Skip a note that only says new features or bug fixes.
Skip stories that are already in the recent briefings unless the new piece is a real development.
Return between 4 and {limit} items when that many are genuinely material. Return fewer when the window is thin. Return an empty list when every candidate is noise. Never invent an item to fill the count.
Public sources only. Do not mention private customers, revenue, or internal names.

For each chosen item:
- headline: a specific claim a reader can scan. Not a bare version number.
- body: 3 to 5 sentences of concrete facts from that item's text only. A body under 3 sentences is invalid. Keep prices, dates, benchmark numbers, and availability. Keep a caveat when the text says a figure is company-reported or unaudited. If the text cannot support 3 factual sentences, omit the item. Do not return a one-sentence or two-sentence body.
- why_it_matters: one sentence for people building with AI. Interpretation is allowed. New facts are not.
Use only names, numbers, and quotes that appear in that item's text.

lede: one plain sentence that connects the chosen themes. No new facts. No hype. No em dashes.
Do not use game-changer, unlock, delve, landscape, pivotal, or groundbreaking.
Keep each sentence under 380 characters.
Return JSON only.

Already briefed:
{json.dumps(already, ensure_ascii=True)}

Candidates:
{json.dumps(items, ensure_ascii=True)}

Return:
{{"lede": "...", "items": [{{"index": 0, "headline": "...", "body": "...", "why_it_matters": "..."}}]}}"""


_BOILER = re.compile(
    r"\b(subscribe|cookie|sign up for|share this|read more|all rights reserved|"
    r"advertisement|related articles|skip to content)\b",
    re.I,
)
_THIN_RELEASE = re.compile(
    r"\b(adds new features|it adds new|new features and|various improvements|"
    r"bug fixes|this release includes)\b",
    re.I,
)


def factual_body(article: Article) -> str:
    """Up to five concrete sentences from the article, in source order."""
    source = "\n".join([
        getattr(article, "full_text", "") or "",
        article.content_preview or "",
    ])
    title = (article.title or "").strip().lower().rstrip(".")
    kept = []
    seen = set()
    for sentence in split_sentences(source):
        cleaned = clean_line(sentence, limit=420)
        key = cleaned.lower()
        if not cleaned or key in seen or len(cleaned) < 40:
            continue
        if _BOILER.search(cleaned):
            continue
        if cleaned.lower().rstrip(".") == title:
            continue
        if _THIN_RELEASE.search(cleaned) and not re.search(r"\d", cleaned):
            continue
        seen.add(key)
        kept.append(cleaned)
    numbered = [sentence for sentence in kept if re.search(r"\d", sentence)]
    chosen = numbered if len(numbered) >= 3 else kept
    return " ".join(chosen[:5])


def _body_length_note(row: dict) -> str:
    text = str(row.get("body") or "")
    count = len(split_sentences(text))
    tokens = max(1, round(len(text) / 4)) if text.strip() else 0
    return f"index {row.get('index')} has {count} sentence(s), about {tokens} tokens"


def _require_schema(payload) -> None:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise SchemaError("LLM output failed schema validation: items must be a list")
    for row in payload["items"]:
        if not isinstance(row, dict):
            raise SchemaError("LLM output failed schema validation: item is not an object")
        if isinstance(row.get("index"), bool) or not isinstance(row.get("index"), int):
            raise SchemaError("LLM output failed schema validation: index must be an integer")
        for key in ("headline", "body", "why_it_matters"):
            if not str(row.get(key) or "").strip():
                raise SchemaError(f"LLM output failed schema validation: missing {key}")
        if len(split_sentences(str(row["body"]))) < 3:
            raise SchemaError(
                "LLM output failed schema validation: body needs at least 3 sentences "
                f"({_body_length_note(row)})"
            )


def _repair_prompt(prompt: str, payload) -> str:
    notes = []
    rows = payload.get("items") if isinstance(payload, dict) else None
    for row in rows or []:
        if isinstance(row, dict) and len(split_sentences(str(row.get("body") or ""))) < 3:
            notes.append(_body_length_note(row))
    detail = "; ".join(notes) if notes else "a body was under 3 sentences"
    return prompt + f"""

The previous draft was rejected: {detail}.
Write the JSON again. Every body must be 3 to 5 complete sentences using only that item's text.
If an item cannot support 3 factual sentences, omit it.
Do not stop after one or two sentences. Return JSON only.
"""


def _schema_or_retry(payload, prompt: str, api_key: str, model: str):
    """Accept a full body. One rewrite if a body is still under 3 sentences."""
    try:
        _require_schema(payload)
        return payload
    except SchemaError as exc:
        if "at least 3 sentences" not in str(exc):
            raise
        print(f"Warning: {exc} Retrying once.", file=sys.stderr)
        try:
            raw = _call_together(_repair_prompt(prompt, payload), api_key, model)
        except SchemaError as again:
            raise SchemaError(
                f"{again} Retried once; the body is still too short to publish."
            ) from again
        retried = _parse_json_object(raw)
        try:
            _require_schema(retried)
        except SchemaError as again:
            raise SchemaError(
                f"{again} Retried once; the body is still too short to publish."
            ) from again
        return retried


def _publishable_body(body: str, article: Article) -> str:
    """Keep grounded sentences. If that filter leaves under 3, use the source."""
    grounded = _grounded_body(body, article)
    if len(split_sentences(grounded)) >= 3:
        return grounded
    sourced = factual_body(article)
    if len(split_sentences(sourced)) >= 3:
        return sourced
    return grounded or sourced


def _grounded_body(body: str, article: Article) -> str:
    source = _source_text(article)
    kept = []
    for sentence in split_sentences(body or ""):
        cleaned = clean_line(sentence, limit=420)
        if cleaned and line_is_grounded(cleaned, source, analysis=False):
            kept.append(cleaned)
    return " ".join(kept)


def _fallback_pairs(articles: list, limit: int) -> tuple:
    chosen = list(articles)[:limit]
    pairs = []
    for article in chosen:
        what, why = extractive_lines(article)
        pairs.append((article, what, factual_body(article), why))
    lede = theme_lede(chosen) if chosen else ""
    return lede, pairs


def compose_briefing(
    articles: list,
    api_key: str | None,
    model: str,
    *,
    strict_auth: bool,
    recent=None,
    limit: int = 7,
    require_full_body: bool = False,
) -> tuple:
    """Rank the shortlist and write the lede plus items. Returns (lede, pairs).

    pairs is a list of (article, headline, body, why_it_matters) in rank order.
    A 3-tuple from an older composer is still accepted by the briefing runner.
    """
    if not articles:
        return "", []
    if not api_key:
        return _fallback_pairs(articles, limit)
    prompt = _rank_prompt(articles, recent or [], limit)
    try:
        raw = _call_together(prompt, api_key, model)
    except AuthError:
        if strict_auth:
            raise
        print("Warning: model auth failed. Using the shortlist order.", file=sys.stderr)
        return _fallback_pairs(articles, limit)
    except SchemaError:
        if require_full_body:
            raise
        print("Warning: model answer was cut off. Using the shortlist order.", file=sys.stderr)
        return _fallback_pairs(articles, limit)
    except Exception as exc:
        print(f"Warning: model ranking failed ({exc}). Using the shortlist order.", file=sys.stderr)
        return _fallback_pairs(articles, limit)

    payload = _parse_json_object(raw)
    if require_full_body:
        payload = _schema_or_retry(payload, prompt, api_key, model)
    rows = payload.get("items") if payload else None
    if not isinstance(rows, list):
        print("Warning: model returned no ranking. Using the shortlist order.", file=sys.stderr)
        return _fallback_pairs(articles, limit)

    pairs = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("index"), int):
            continue
        index = row["index"]
        if index in seen or index < 0 or index >= len(articles):
            continue
        seen.add(index)
        article = articles[index]
        what_fb, why_fb = extractive_lines(article)
        headline_raw = str(row.get("headline") or row.get("what_happened") or "")
        headline = _accept(headline_raw, article, analysis=False, fallback=what_fb)
        body = _publishable_body(str(row.get("body") or ""), article)
        why = _accept(str(row.get("why_it_matters") or ""), article, analysis=True, fallback=why_fb)
        pairs.append((article, headline, body, why))
        if len(pairs) >= limit:
            break

    if not pairs:
        # The model looked and found nothing worth posting.
        return "", []

    combined = "\n".join(_source_text(row[0]) for row in pairs)
    lede = clean_line(str(payload.get("lede") or ""), limit=320)
    if not line_is_grounded(lede, combined, analysis=True):
        lede = theme_lede([row[0] for row in pairs])
    return lede, pairs
