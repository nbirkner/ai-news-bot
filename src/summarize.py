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
    "policy": "A legal or policy move changes the constraints on building and shipping AI products.",
    "safety": "A safety or security development changes the risks teams have to plan for.",
    "deal": "A deal can shift who has the models, the compute, or the distribution.",
    "model": "A model release changes what products can do and what serving stacks need to run.",
    "infra": "An inference or hardware change affects the cost and latency of running models.",
    "agents": "An agent update changes how teams connect models to tools and longer tasks.",
    "paper": "A research result worth a look if you train or evaluate models.",
    "release": "A serving-stack release affects how teams deploy open models.",
    "other": "Worth a look for teams that build or run AI systems.",
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
    return f"{article.title}\n{article.content_preview or ''}"


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
    response = requests.post(
        TOGETHER_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 900,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    if response.status_code in (401, 403):
        raise AuthError(response.text[:300])
    if response.status_code == 400 and "model" in response.text.lower():
        raise AuthError(response.text[:300])
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


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


def _prompt(articles: list) -> str:
    items = []
    for index, article in enumerate(articles):
        items.append(
            {
                "index": index,
                "source": article.source_name,
                "title": article.title,
                "excerpt": (article.content_preview or "")[:500],
            }
        )
    return f"""You write lines for a Slack briefing read by people who build AI products and infrastructure.

Rules:
- Use only the title and excerpt. Do not add facts, numbers, quotes, or names that are not in that text.
- what_happened: one plain sentence on what the source says happened. If the excerpt is empty or useless, restate the title and stop.
- why_it_matters: one plain sentence on why a builder should care. Interpretation is allowed. New facts are not.
- No hype. No em dashes. Do not use game-changer, unlock, delve, landscape, pivotal, or groundbreaking.
- Keep each sentence under 220 characters.
- Return JSON only.

Items:
{json.dumps(items, ensure_ascii=True)}

Return:
{{"items": [{{"index": 0, "what_happened": "...", "why_it_matters": "..."}}]}}"""


def _accept(line: str, article: Article, *, analysis: bool, fallback: str) -> str:
    cleaned = clean_line(line)
    if is_thin_preview(article.content_preview, article.title) and not analysis:
        return fallback
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
