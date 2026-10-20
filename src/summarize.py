import json
import re
import sys
import time

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


_FILLER_WHY = re.compile(
    r"\b("
    r"worth a look|"
    r"for people building with ai|"
    r"changes what a product can do|"
    r"changes the constraints on shipping|"
    r"changes the risks you have to plan for|"
    r"can shift who has the models|"
    r"affects the cost and latency|"
    r"changes how models connect to tools|"
    r"a research result worth|"
    r"matters only when it changes how models are deployed|"
    r"if you ship or run models"
    r")\b",
    re.I,
)


def is_filler_why(line: str) -> bool:
    """Category slogans. A why-line has to name this story."""
    return bool(_FILLER_WHY.search(line or ""))


def _short_lead(text: str, limit: int = 110) -> str:
    cleaned = clean_line(text or "").rstrip(".")
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
    while cut.split() and cut.split()[-1].lower() in {
        "from", "of", "the", "and", "or", "a", "to", "for", "in", "on", "with",
    }:
        cut = cut.rsplit(" ", 1)[0]
    return cut or cleaned[:limit]


def specific_why(article: Article) -> str:
    """One line a builder or an exec could repeat. Uses the headline, no new facts."""
    title = _short_lead(article.title, 80)
    if not title:
        return ""
    return f"A builder or an AI exec would bring up {title} this week."


_ORDER_TITLE = re.compile(r"exclusion and removal of (.+?) from\b", re.I)


def _order_lead(article: Article) -> str:
    """A short lead for a removal order, using only words from the document title."""
    match = _ORDER_TITLE.search(article.title or "")
    if not match:
        return ""
    product = match.group(1).strip(" .")
    lead = f"Order excludes and removes {product} from federal civilian systems."
    if line_is_grounded(lead, _source_text(article), analysis=False):
        return lead.rstrip(".")
    return ""


def _order_why(article: Article) -> str:
    """Consequence of a removal order, still using only the document's own words."""
    match = _ORDER_TITLE.search(article.title or "")
    if not match:
        return ""
    product = match.group(1).strip(" .")
    line = (
        f"The order takes {product} out of federal civilian procurements "
        "and out of systems already covered by the notice."
    )
    if line_is_grounded(line, _source_text(article), analysis=True):
        return line
    return ""


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
        response = None
        for attempt in range(3):
            try:
                response = requests.post(
                    TOGETHER_API_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=120,
                )
            except requests.Timeout:
                if attempt == 2:
                    raise
                print("Warning: Together timed out. Retrying.", file=sys.stderr)
                time.sleep(5)
                continue
            if response.status_code == 429 and attempt < 2:
                print("Warning: Together rate limited. Retrying.", file=sys.stderr)
                time.sleep(8 * (attempt + 1))
                continue
            return response
        return response

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
- body: 2 to 3 sentences of concrete facts from that item's text. A body under 2 sentences is invalid. Keep prices, benchmark numbers, dates, and availability. If a benchmark comes from the company that built the model, label it company-reported, or vendor-reported on a vendor post. If the text cannot support 2 factual sentences, omit the item.
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

The bar for keeping a story: would a builder or an AI exec bring it up this week?
Prefer, in this order:
1. A frontier-lab move from OpenAI, Anthropic, Google, Meta, Microsoft, xAI, Mistral, DeepSeek, or MiniMax.
2. Big money: revenue, ARR, a funding round, or an IPO path, when the text states the figure.
3. Policy that changes the rules people ship under (a lawsuit, a statute, an export rule, a ban, a removal order).
4. Security that changes what a team has to defend (a breach, an agent treated as an insider).
5. An infrastructure shift that changes what it costs or how a team serves a model this week.
Skip routine patch notes, office openings, customer case studies, market blurbs, and commentary that does not change what someone can ship, pay, or comply with.
Include a release note only when the text names a concrete change a team would act on this week.
Skip a note that only says new features or bug fixes.
Skip stories that are already in the recent briefings unless the new piece is a real development.
Do not return two items that are the same event from different outlets. Keep the primary document or the wire, and use the version whose text actually states the figures.
Return between 4 and {limit} items when that many clear the bar. A federal exclusion or removal order clears the bar. A stated mega-round, revenue figure, or IPO path clears the bar. A security change that treats agents as insiders, or a breach with a stated scale, clears the bar. A frontier model with a stated price, size, or benchmark clears the bar. Include those ahead of a narrower product SDK or a small acquisition when both are in the candidates. Return fewer only when the window does not contain that many. Return an empty list when every candidate is noise. Never invent an item to fill the count.
Public sources only. Do not mention a vendor's private customer list or unpublished revenue. Public revenue, ARR, funding, and IPO figures stay when that item's text states them. Do not add a name or number that is not in that item's text.
A hard number stays only when it appears in that item's text. Prefer the lab's own post, the Federal Register, or a wire (Reuters, Bloomberg, the Financial Times, the Wall Street Journal, CNBC, the Associated Press) over a recap that does not show the figure. When a benchmark or accuracy figure comes from the company that built the model, write company-reported next to it. On a vendor's own post, vendor-reported is the same caveat.

For each chosen item:
- headline: a short scannable lead, under 90 characters, with the spec or the number (dollars, percent, parameter count, or date). Not a bare version number, not a document title, and not a full paragraph.
- body: 2 to 3 sentences of prose from that item's text only. A body under 2 sentences is invalid. Keep prices, dates, benchmark numbers, and availability. Do not repeat a sentence. Do not paste a table, a markdown heading, or the raw page. If the text cannot support 2 factual sentences, omit the item.
- why_it_matters: one specific sentence naming the consequence for this story (the number, the rule, the product, or the cost). Interpretation is allowed. New facts are not. Do not repeat a sentence from the body. No filler. Do not write "worth a look" or a generic line about people building with AI.
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
        if cleaned.startswith("|") or cleaned.lower().startswith("license:"):
            continue
        seen.add(key)
        kept.append(cleaned)
    numbered = [sentence for sentence in kept if re.search(r"\d", sentence)]
    chosen = numbered if len(numbered) >= 2 else kept
    return " ".join(chosen[:3])


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
        if len(split_sentences(str(row["body"]))) < 2:
            raise SchemaError(
                "LLM output failed schema validation: body needs at least 2 sentences "
                f"({_body_length_note(row)})"
            )


def _repair_prompt(prompt: str, payload) -> str:
    notes = []
    rows = payload.get("items") if isinstance(payload, dict) else None
    for row in rows or []:
        if isinstance(row, dict) and len(split_sentences(str(row.get("body") or ""))) < 2:
            notes.append(_body_length_note(row))
    detail = "; ".join(notes) if notes else "a body was under 2 sentences"
    return prompt + f"""

The previous draft was rejected: {detail}.
Write the JSON again. Every body must be 2 to 3 complete sentences using only that item's text.
If an item cannot support 2 factual sentences, omit it.
Do not stop after one sentence. Return JSON only.
"""


def _schema_or_retry(payload, prompt: str, api_key: str, model: str):
    """Accept a full body. One rewrite if a body is still under 3 sentences."""
    try:
        _require_schema(payload)
        return payload
    except SchemaError as exc:
        if "at least 2 sentences" not in str(exc):
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


_MARKDOWN_DUMP = re.compile(r"(\| ---|```|^# |license:\s*mit)", re.I | re.M)


def _unique_sentences(text: str) -> str:
    kept = []
    seen = set()
    for sentence in split_sentences(text or ""):
        key = re.sub(r"\W+", " ", sentence.lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(sentence)
    return " ".join(kept)


def _limit_sentences(text: str, count: int = 3) -> str:
    return " ".join(split_sentences(text or "")[:count])


def _drop_repeated_why(body: str, why: str) -> str:
    why_key = re.sub(r"\W+", " ", (why or "").lower()).strip()
    if not why_key:
        return body
    kept = []
    for sentence in split_sentences(body or ""):
        key = re.sub(r"\W+", " ", sentence.lower()).strip()
        if key and key != why_key:
            kept.append(sentence)
    return " ".join(kept)


_BENCH_SENTENCE = re.compile(
    r"evaluat|benchmark|accuracy|pass rate|\bscore\b",
    re.I,
)


def label_benchmark(body: str, article: Article) -> str:
    """Mark a lab or vendor accuracy figure the model left unlabeled.

    A price or cost percent is not a benchmark, so the caveat stays on the
    sentence that states the score.
    """
    text = body or ""
    if not re.search(r"\d+(?:\.\d+)?\s?%", text):
        return text
    domain = (article.source_domain or "").lower()
    vendor = "minimax." in domain
    lowered = text.lower()
    if vendor and "vendor-reported" not in lowered and "company-reported" in lowered:
        return re.sub("company-reported", "vendor-reported", text, count=1, flags=re.I)
    if "company-reported" in lowered or "vendor-reported" in lowered:
        return text
    if article.source_kind != "lab" and not vendor:
        return text
    source = f"{article.full_text or ''} {article.content_preview or ''}"
    if not _BENCH_SENTENCE.search(source):
        return text
    word = "vendor-reported" if vendor else "company-reported"
    labeled = False
    out = []
    for sentence in split_sentences(text):
        has_percent = re.search(r"\d+(?:\.\d+)?\s?%", sentence) is not None
        if not labeled and has_percent and _BENCH_SENTENCE.search(sentence):
            stripped = sentence.rstrip(".")
            out.append(f"{stripped}, a {word} figure.")
            labeled = True
        else:
            out.append(sentence if sentence.endswith((".", "!", "?")) else sentence)
    if not labeled:
        return text
    return " ".join(out)


def _publishable_body(body: str, article: Article) -> str:
    """Keep grounded sentences. If that filter leaves under 2, use the source."""
    grounded = _unique_sentences(_grounded_body(body, article))
    # A model card paste is not a briefing. Prefer sentences taken from the page.
    if _MARKDOWN_DUMP.search(grounded or ""):
        grounded = ""
    if len(split_sentences(grounded)) >= 2:
        return _limit_sentences(grounded)
    sourced = factual_body(article)
    if len(split_sentences(sourced)) >= 2:
        return _limit_sentences(sourced)
    return _limit_sentences(grounded or sourced)


def _grounded_body(body: str, article: Article) -> str:
    source = _source_text(article)
    kept = []
    for sentence in split_sentences(body or ""):
        cleaned = clean_line(sentence, limit=420)
        if cleaned and line_is_grounded(cleaned, source, analysis=False):
            kept.append(cleaned)
    return " ".join(kept)


_CLEAR_BAR = re.compile(
    r"\b(exclusion|barred)\b|\$\s?\d[\d.]*\s?(?:billion|bln)\b",
    re.I,
)


def _backfill_clear_bar(articles: list, pairs: list, limit: int) -> list:
    """Keep a stated mega-round or removal order when the draft skipped it.

    The ranker already treats those as the bar. A single sample should not
    drop one to make room for a second copy of a smaller item.
    """
    from src.dedupe import same_story
    from src.rank import score_article

    unique = []
    for row in pairs:
        if any(same_story(row[0], previous[0]) for previous in unique):
            continue
        unique.append(row)
    pairs = unique
    skipped = []
    for article in articles:
        if any(same_story(article, row[0]) for row in pairs):
            continue
        if score_article(article) < 6 or not _CLEAR_BAR.search(article.title or ""):
            continue
        skipped.append(article)
    skipped.sort(key=score_article, reverse=True)
    for article in skipped:
        if len(pairs) >= limit:
            break
        what, why = extractive_lines(article)
        headline = _order_lead(article) or _short_lead(what or article.title, 110)
        body = label_benchmark(factual_body(article), article)
        if len(split_sentences(body)) < 2:
            continue
        why_line = _order_why(article) or why
        if is_filler_why(why_line):
            why_line = specific_why(article)
        if not line_is_grounded(why_line, _source_text(article), analysis=True):
            continue
        pairs.append((article, headline, body, why_line))
    return pairs


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
        order_lead = _order_lead(article)
        if order_lead and (len(headline) > 90 or headline.lower().startswith("the ")):
            headline = order_lead
        elif len(headline) > 110:
            headline = _short_lead(headline, 110) or _short_lead(article.title, 110)
        body = label_benchmark(_publishable_body(str(row.get("body") or ""), article), article)
        why = _accept(str(row.get("why_it_matters") or ""), article, analysis=True, fallback=why_fb)
        body = _drop_repeated_why(body, why)
        if is_filler_why(why) or (order_lead and why.lower().startswith("a builder or an ai exec")):
            cited = _order_why(article) or specific_why(article)
            if line_is_grounded(cited, _source_text(article), analysis=True):
                why = cited
        pairs.append((article, headline, body, why))
        if len(pairs) >= limit:
            break

    if not pairs:
        # The model looked and found nothing worth posting.
        return "", []

    pairs = _backfill_clear_bar(articles, pairs, limit)

    combined = "\n".join(_source_text(row[0]) for row in pairs)
    lede = clean_line(str(payload.get("lede") or ""), limit=320)
    if not line_is_grounded(lede, combined, analysis=True):
        lede = theme_lede([row[0] for row in pairs])
    return lede, pairs
