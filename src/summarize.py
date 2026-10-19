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
)
from src.models import Article

TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"

_WHY = {
    "policy": "A legal or policy move changes the rules for how AI is built and used.",
    "safety": "A safety or security development changes the risks people have to plan for.",
    "deal": "A deal can shift who has the models, the compute, or the distribution.",
    "model": "A model release changes what AI systems can do.",
    "infra": "An infrastructure change affects the cost and availability of running models.",
    "agents": "An agent update changes how people connect models to tools and longer tasks.",
    "paper": "A research result worth a look if you follow how models are trained or evaluated.",
    "release": "A software release affects how people run open models.",
    "other": "Worth a look for anyone following AI.",
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
    """Model output missed the briefing schema. Do not publish a fallback."""


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_items(items) -> list:
    """Structural checks for the items array. Empty is valid."""
    if not isinstance(items, list):
        return ["items must be a list"]
    errors = []
    for index, row in enumerate(items):
        if not isinstance(row, dict):
            errors.append(f"items[{index}] must be an object")
            continue
        if not _is_int(row.get("index")):
            errors.append(f"items[{index}].index must be an integer")
        for field in ("what_happened", "why_it_matters"):
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"items[{index}].{field} must be a non-empty string")
    return errors


def validate_ranking(payload) -> list:
    """Schema for the rank-and-write response. Field errors only, never the raw text."""
    if not isinstance(payload, dict):
        return ["response is not a JSON object"]
    errors = []
    if not isinstance(payload.get("lede"), str):
        errors.append("lede must be a string")
    errors.extend(validate_items(payload.get("items")))
    return errors


def _first_person(sentence: str) -> bool:
    return bool(re.match(r"^(we|we're|we’re|our|i|i'm|i’m|my)\b", sentence.strip(), re.I))


def _why_for(article: Article) -> str:
    title = article.title or ""
    if re.search(r"\b(sues?|sued|lawsuit)\b", title, re.I):
        return "A lawsuit changes the legal risk around a model the public can use."
    if re.search(r"\b(midterms?|election|voting|ballot)\b", title, re.I):
        return "Election season is a constraint on how public models are shipped and monitored."
    if re.search(r"\bbankrupt", title, re.I):
        return "A bankruptcy can decide what happens to the product and to the people who use it."
    if re.search(r"\b(spies|spy|espionage|jailbreak)\b", title, re.I):
        return "A security hole in model tools is a risk for anyone who connects those tools to their own systems."
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

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2200,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        # Instruct path. Reasoning models otherwise spend the budget on a trace.
        "reasoning": {"enabled": False},
    }
    response = send(payload)
    if response.status_code == 400 and "reasoning" in (response.text or "").lower():
        payload.pop("reasoning", None)
        response = send(payload)
    if response.status_code in (401, 403):
        raise AuthError(response.text[:300])
    if response.status_code == 400 and "model" in response.text.lower():
        raise AuthError(response.text[:300])
    response.raise_for_status()
    message = response.json()["choices"][0]["message"]
    content = (message.get("content") or "").strip()
    if not content:
        raise ValueError("Together returned an empty content field")
    return content


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
    return f"""You write lines for a general public AI news briefing.

Rules:
- This is public news. It is not written for a company, a customer list, or an internal audience.
- Do not mention Together AI products, customers, revenue, or private information. Do not use a for Together angle or an internal name.
- Use only the title, details, and text for that item. Do not add facts, numbers, quotes, or names that are not in that text.
- what_happened: one plain sentence on what the source says happened. Keep concrete details that are in the text: prices, benchmark numbers, dates, and availability. If the text says a figure is company-reported or unaudited, keep that caveat.
- why_it_matters: one plain sentence on why a general reader should care. Interpretation is allowed. New facts are not.
- No hype. No em dashes. Do not use game-changer, unlock, delve, landscape, pivotal, or groundbreaking.
- Plain, warm tone.
- Keep each sentence under 220 characters.
- Return JSON only.

Items:
{json.dumps(items, ensure_ascii=True)}

Return:
{{"items": [{{"index": 0, "what_happened": "...", "why_it_matters": "..."}}]}}"""


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
    errors = validate_items(payload.get("items") if isinstance(payload, dict) else None)
    if payload is None:
        errors = ["response is not a JSON object"]
    if errors:
        if strict_auth:
            raise SchemaError("; ".join(errors))
        print(
            f"Warning: model output failed schema validation ({'; '.join(errors)}). Using source text.",
            file=sys.stderr,
        )
        return fallbacks

    rows = payload["items"]
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
        return "A handful of AI developments from the sources we watch."
    if len(themes) == 1:
        body = themes[0]
    else:
        body = ", ".join(themes[:-1]) + " and " + themes[-1]
    return f"This window is mostly about {body}."


def _rank_prompt(articles: list, recent: list, limit: int) -> str:
    items = [_item_payload(article, index) for index, article in enumerate(articles)]
    already = [
        {"title": item.get("title") or "", "url": item.get("url") or ""}
        for item in (recent or [])
        if item.get("title")
    ]
    return f"""You rank and write a general public AI news briefing.

Compare the candidates against each other. Order is not a ranking.
Choose items a general reader would want: a model release, a policy or legal move, a research result, a major product launch, funding, or an infrastructure change that affects who can use AI.
Do not rank a story higher because it helps one inference vendor. Skip customer stories.
Do not mention Together AI products, customers, revenue, or private information. Do not use a for Together angle or an internal name.
Prefer a material story over a routine patch note. Include a release note only when it changes how models run in a way that matters this week.
Skip stories that are already in the recent briefings unless the new piece is a real development.
Return between 4 and {limit} items when that many are genuinely material. Return fewer when the window is thin. Return an empty list when every candidate is noise. Never invent an item to fill the count.

For each chosen item:
- what_happened: one plain sentence. Keep prices, benchmark numbers, dates, and availability when they are in that item's text. If the text says a figure is company-reported or unaudited, keep that caveat.
- why_it_matters: one plain sentence on why a general reader should care. Interpretation is allowed. New facts are not.
Use only names, numbers, and quotes that appear in that item's text.

lede: one plain sentence that connects the chosen themes. No new facts. No hype. No em dashes.
Do not use game-changer, unlock, delve, landscape, pivotal, or groundbreaking.
Keep each sentence under 240 characters.
Return JSON only.

Already briefed:
{json.dumps(already, ensure_ascii=True)}

Candidates:
{json.dumps(items, ensure_ascii=True)}

Return:
{{"lede": "...", "items": [{{"index": 0, "what_happened": "...", "why_it_matters": "..."}}]}}"""


def _fallback_pairs(articles: list, limit: int) -> tuple:
    chosen = list(articles)[:limit]
    pairs = []
    for article in chosen:
        what, why = extractive_lines(article)
        pairs.append((article, what, why))
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
) -> tuple:
    """Rank the shortlist and write the lede plus items. Returns (lede, pairs).

    pairs is a list of (article, what_happened, why_it_matters) in rank order.
    """
    if not articles:
        return "", []
    if not api_key:
        return _fallback_pairs(articles, limit)
    try:
        raw = _call_together(_rank_prompt(articles, recent or [], limit), api_key, model)
    except AuthError:
        if strict_auth:
            raise
        print("Warning: Together auth failed. Using the shortlist order.", file=sys.stderr)
        return _fallback_pairs(articles, limit)
    except Exception as exc:
        print(f"Warning: Together ranking failed ({exc}). Using the shortlist order.", file=sys.stderr)
        return _fallback_pairs(articles, limit)

    payload = _parse_json_object(raw)
    errors = validate_ranking(payload)
    if errors:
        if strict_auth:
            raise SchemaError("; ".join(errors))
        print(
            f"Warning: model output failed schema validation ({'; '.join(errors)}). Using the shortlist order.",
            file=sys.stderr,
        )
        return _fallback_pairs(articles, limit)

    rows = payload["items"]
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
        what = _accept(str(row.get("what_happened") or ""), article, analysis=False, fallback=what_fb)
        why = _accept(str(row.get("why_it_matters") or ""), article, analysis=True, fallback=why_fb)
        pairs.append((article, what, why))
        if len(pairs) >= limit:
            break

    if not pairs:
        # The model looked and found nothing worth posting.
        return "", []

    combined = "\n".join(_source_text(article) for article, _, _ in pairs)
    lede = clean_line(str(payload.get("lede") or ""), limit=240)
    if not line_is_grounded(lede, combined, analysis=True):
        lede = theme_lede([article for article, _, _ in pairs])
    return lede, pairs
