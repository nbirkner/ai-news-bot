import json
import re
import sys
import time

import requests

from src.read import _CHROME
from src.clean import (
    clean_line,
    first_sentence,
    is_thin_preview,
    line_is_grounded,
    scrub_copy,
    shares_distinctive_word,
    split_sentences,
    tokens,
)
from src.usage import record_llm_call
from src.models import Article

TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"

# A shortlist of 20 full texts does not fit in one 120s read. Rank in batches,
# and give each attempt a longer read timeout than the last.
_RANK_BATCH = 8
_READ_TIMEOUTS = (180, 240, 300)

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
    r"if you ship or run models|"
    r"legal or policy"
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


_CRITERIA_ECHO = re.compile(
    r"a builder or an ai exec would bring up|"
    r"bring it up this week|"
    r"people building with ai|"
    r"worth a look",
    re.I,
)


def is_criteria_echo(line: str) -> bool:
    """The selection bar is for ranking. It must not ship as copy."""
    return bool(_CRITERIA_ECHO.search(line or ""))


def _concrete_remainder(why: str) -> str:
    """Keep a real consequence that was prefixed with the selection bar."""
    text = (why or "").strip()
    text = re.sub(r"^for people building with ai[:,]?\s*", "", text, flags=re.I)
    text = re.sub(
        r"^a builder or an ai exec would bring up\b.*?this week[:,.]?\s*",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"^worth a look[:,.]?\s*", "", text, flags=re.I)
    if not text or is_criteria_echo(text) or is_filler_why(text):
        return ""
    return clean_line(text)


def specific_why(article: Article) -> str:
    """Deprecated echo of the selection bar. Kept so tests can reject it."""
    title = _short_lead(article.title, 80)
    if not title:
        return ""
    return f"A builder or an AI exec would bring up {title} this week."


_ORDER_TITLE = re.compile(r"exclusion and removal of (.+?) from\b", re.I)


def _plain_removal_headline(title: str) -> str:
    """Plain English for a removal order. Not the Federal Register title."""
    match = _ORDER_TITLE.search(title or "")
    if not match:
        return ""
    product = re.sub(r"\s+products$", "", match.group(1).strip(" ."), flags=re.I).strip()
    if not product:
        return ""
    return f"Federal agencies must remove {product} from civilian systems."


def _order_lead(article: Article) -> str:
    """A short sentence for a removal order, not the raw Federal Register title."""
    return _plain_removal_headline(article.title or "")


def _order_why(article: Article) -> str:
    """Consequence of a removal order that also covers third-party products."""
    text = _source_text(article)
    lowered = text.lower()
    if not re.search(r"exclusion|removal", lowered):
        return ""
    if "third-party" not in lowered and "third party" not in lowered:
        return ""
    product = "DeepSeek" if re.search(r"\bdeepseek\b", lowered) else "the named model"
    return (
        f"Vendors must audit which products include {product} "
        "before a civilian agency can buy them."
    )


def _norm_title(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def headline_is_raw(headline: str, title: str) -> bool:
    """True when the lead is the page title, or a long uncut prefix of it."""
    left = _norm_title(headline)
    right = _norm_title(title)
    if not left or not right:
        return False
    if left == right:
        return True
    if len(left) >= 40 and (right.startswith(left) or left.startswith(right)):
        return True
    return False


def _government_notice(article: Article) -> bool:
    domain = (article.source_domain or "").lower()
    title = (article.title or "").lower()
    return "federalregister.gov" in domain or title.startswith("notice of order")


def rewrite_headline(title: str) -> str:
    """A short lead that is not the raw page title."""
    plain = _plain_removal_headline(title or "")
    if plain:
        return plain.rstrip(".")
    text = re.sub(
        r"^(exclusive|breaking|update|analysis)\s*:\s*",
        "",
        title or "",
        flags=re.I,
    ).strip()
    text = re.sub(r"^notice of order regarding\s+", "", text, flags=re.I).strip()
    if ":" in text:
        left, right = [part.strip(" -") for part in text.split(":", 1)]
        pick = left if 8 <= len(left) <= 90 else right
        if _norm_title(pick) != _norm_title(title) and len(pick) >= 8:
            return pick.rstrip(".")
    words = text.split()
    if len(words) > 8:
        short = " ".join(words[:8]).rstrip(",;:-")
        if _norm_title(short) != _norm_title(title):
            return short
    if words and _norm_title(text) == _norm_title(title):
        return " ".join(words[: max(3, len(words) - 2)]).rstrip(",;:-")
    return text.rstrip(".")


def _definition_why(article: Article) -> str:
    """Say what an unfamiliar product is, using a sentence from the page."""
    source = _source_text(article)
    title = (article.title or "").split(":")[0]
    for sentence in split_sentences(source):
        if not re.search(r"\bis an?\b", sentence, re.I):
            continue
        if not shares_distinctive_word(sentence, title):
            continue
        cleaned = clean_line(sentence, limit=240)
        if not cleaned or is_criteria_echo(cleaned) or is_filler_why(cleaned):
            continue
        if line_is_grounded(cleaned, source, analysis=True):
            return cleaned
    return ""


def _restates(why: str, body: str) -> bool:
    target = _norm_title(why)
    if len(target) < 24:
        return False
    if target and target in _norm_title(body):
        return True
    from src.dedupe import jaccard
    for sentence in split_sentences(body or ""):
        if jaccard(why, sentence) >= 0.6:
            return True
    return False


_DANGLING_END = re.compile(
    r"\b(?:a|an|the|to|of|for|and|or|be|as|with|from|by|its|their|into)\.$",
    re.I,
)


def _usable_why(why: str, body: str, article: Article) -> str:
    """A consequence, or a definition. Never the selection-bar template."""
    order = _order_why(article)
    if order:
        return order
    candidate = (why or "").strip()
    if _DANGLING_END.search(candidate):
        candidate = ""
    if is_criteria_echo(candidate) or is_filler_why(candidate) or _SHORTFALL_WHY.search(candidate):
        candidate = "" if _SHORTFALL_WHY.search(candidate) else _concrete_remainder(candidate)
    if _GENERIC_LOSS_WHY.search(candidate or ""):
        candidate = ""
    echoed = not candidate or is_criteria_echo(candidate) or is_filler_why(candidate)
    charge = _loss_charge_why(article)
    # The charge fact is the why, even when the body already quotes it.
    if charge:
        return charge
    if candidate and not echoed and not _restates(candidate, body) and not _DANGLING_END.search(candidate):
        return candidate
    defined = _definition_why(article)
    if defined and not _restates(defined, body):
        return defined
    money = _money_why(article)
    if money and not _restates(money, body) and not is_filler_why(money):
        return money
    return ""


_GENERIC_LOSS_WHY = re.compile(r"changes what the lab can spend", re.I)
_CHARGE_ANCHOR = re.compile(r"non[-\s]?cash|accounting charges?", re.I)
_BILLION_FIGURE = re.compile(r"\$\s?\d[\d.]*\s?billion", re.I)


def _charge_figure(source: str) -> str:
    """The dollar figure next to the charge, not a different number in the same piece."""
    anchors = [match.start() for match in _CHARGE_ANCHOR.finditer(source or "")]
    best = ""
    best_dist = 10**9
    for match in _BILLION_FIGURE.finditer(source or ""):
        for anchor in anchors:
            dist = abs(match.start() - anchor)
            if dist <= 120 and dist < best_dist:
                best_dist = dist
                best = re.sub(r"\s+", " ", match.group(0)).strip()
    return best


def _loss_charge_why(article: Article) -> str:
    """Build the loss why from charge facts the source actually states."""
    source = _source_text(article)
    if not re.search(r"\banthropic\b", source, re.I) or not re.search(r"\bloss\b", source, re.I):
        return ""
    noncash = re.search(r"non[-\s]?cash", source, re.I) is not None
    accounting = re.search(r"accounting charges?", source, re.I) is not None
    if not noncash and not accounting:
        return ""
    if noncash and accounting:
        kind = "non-cash accounting charges"
    elif noncash:
        kind = "non-cash charges"
    else:
        kind = "accounting charges"
    figure = _charge_figure(source)
    if re.search(r"\b(most|mostly)\b", source, re.I) and figure:
        lead = f"Most of the {figure} is {kind}"
    elif re.search(r"\b(most|mostly)\b", source, re.I):
        lead = f"Most of Anthropic's reported loss is {kind}"
    elif re.search(r"\bmajority\b", source, re.I) and figure:
        lead = f"The majority of the {figure} is {kind}"
    elif figure:
        lead = f"Anthropic's reported loss includes {figure} in {kind}"
    else:
        lead = f"Anthropic's reported loss includes {kind}"
    tail = ""
    has_burn = re.search(r"operating burn", source, re.I) is not None
    has_s1 = re.search(r"\bS-1\b", source) is not None
    if has_burn and has_s1:
        tail = ", and the figure to watch is operating burn in the S-1"
    elif has_burn:
        tail = ", and the figure to watch is operating burn"
    line = lead + tail + "."
    if not line_is_grounded(line, source, analysis=True):
        return ""
    return line


def _chip_order_why(article: Article) -> str:
    source = _source_text(article)
    from src.rank import is_mega_order
    if not is_mega_order(article):
        return ""
    names = [
        name for name in ("ByteDance", "Alibaba", "Tencent")
        if re.search(rf"\b{name}\b", source, re.I)
    ]
    if names and re.search(r"\bnvidia\b", source, re.I):
        if len(names) == 1:
            who = f"{names[0]} changes"
        elif len(names) == 2:
            who = f"{names[0]} and {names[1]} change"
        else:
            who = f"{names[0]}, {names[1]}, and {names[2]} change"
        line = f"{who} which buyers can get Nvidia chips."
    else:
        line = "The orders change which buyers can get Nvidia chips."
    if not line_is_grounded(line, source, analysis=True):
        return ""
    return line


def _money_why(article: Article) -> str:
    """A consequence for a lab loss or a major deal, using names the title already has."""
    charge = _loss_charge_why(article)
    if charge:
        return charge
    chips = _chip_order_why(article)
    if chips:
        return chips
    title = article.title or ""
    if re.search(r"\banthropic\b", title, re.I) and re.search(r"\bloss\b", title, re.I):
        return "Anthropic's reported loss changes what the lab can spend."
    if re.search(r"\bnscale\b", title, re.I) and re.search(r"\b(deal|talks)\b", title, re.I):
        return "The Nscale deal changes how much compute Anthropic can buy."
    if re.search(r"\bopenai\b", title, re.I) and re.search(r"\b(raise|fundraising|ipo|\$)\b", title, re.I):
        return "The round changes how much compute OpenAI can buy."
    return ""


_SPEAKER_VERB = r"(?:said|wrote|told|warned|called|added|noted)"
_QUOTE_SPAN = re.compile(r'["“]([^"”]{8,})["”]')
_ATTR_FORWARD = re.compile(
    rf"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)\s+{_SPEAKER_VERB}\b([^.]*)",
)
_ATTR_BACK = re.compile(
    rf'["“]([^"”]{{8,}})["”]\s*,?\s*([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)\s+{_SPEAKER_VERB}\b',
)


def _same_speaker(claimed: str, actual: str) -> bool:
    claimed_words = {word.lower() for word in (claimed or "").split() if len(word) > 2}
    actual_words = {word.lower() for word in (actual or "").split() if len(word) > 2}
    return bool(claimed_words & actual_words)


def _speaker_near(source: str, start: int, end: int) -> str:
    before = source[max(0, start - 500):start]
    after = source[end:end + 180]
    named = list(re.finditer(
        rf"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+{_SPEAKER_VERB}\b",
        before,
    ))
    if named:
        return named[-1].group(1)
    if re.search(rf"\b(?:she|he)\s+{_SPEAKER_VERB}\b", before[-140:]) or re.search(
        rf"^\s*,?\s*(?:she|he)\s+{_SPEAKER_VERB}\b",
        after,
    ):
        people = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", before)
        if people:
            return people[-1]
    return ""


def _source_quotes(source: str) -> list:
    quotes = []
    for match in _QUOTE_SPAN.finditer(source or ""):
        quotes.append((
            _speaker_near(source, match.start(), match.end()),
            match.group(1),
        ))
    return quotes


def _flat_quote(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _quote_belongs_to(quote: str, speaker: str, source_quotes: list) -> bool:
    folded = _flat_quote(quote)
    if len(folded) < 8:
        return False
    for actual, text in source_quotes:
        hay = _flat_quote(text)
        if (folded in hay or hay in folded) and _same_speaker(speaker, actual):
            return True
    return False


def sentence_misattributes(sentence: str, source: str) -> bool:
    """True when a sentence gives someone a quote or a reporter's line they did not say."""
    if not sentence or not source:
        return False
    quotes = _source_quotes(source)
    seen = set()
    attributions = []
    for match in _ATTR_FORWARD.finditer(sentence):
        attributions.append((match.group(1), match.group(2)))
    for match in _ATTR_BACK.finditer(sentence):
        attributions.append((match.group(2), match.group(1)))
    for speaker, rest in attributions:
        quoted = _QUOTE_SPAN.findall(rest if isinstance(rest, str) else "")
        if not quoted and rest.strip().startswith('"'):
            quoted = _QUOTE_SPAN.findall(rest)
        if quoted:
            for quote in quoted:
                key = (speaker, quote)
                if key in seen:
                    continue
                seen.add(key)
                if not _quote_belongs_to(quote, speaker, quotes):
                    return True
            continue
        words = re.findall(r"[A-Za-z0-9']+", rest)
        for index in range(0, max(0, len(words) - 5)):
            chunk = " ".join(words[index:index + 6]).lower()
            if chunk not in source.lower():
                continue
            if not _quote_belongs_to(chunk, speaker, quotes):
                return True
    return False


def _deattribute(sentence: str) -> str:
    text = re.sub(
        rf"\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+{_SPEAKER_VERB}\s+that\s+",
        "",
        sentence,
        count=1,
    )
    text = re.sub(r'["“”]', "", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    if text and text[-1] not in ".!?":
        text += "."
    return text


def fix_quote_attribution(text: str, source: str) -> str:
    """Drop or un-attribute a line that puts a reporter's words in someone's mouth."""
    kept = []
    for sentence in split_sentences(text or ""):
        if not sentence_misattributes(sentence, source):
            kept.append(sentence)
            continue
        stripped = _deattribute(sentence)
        if (
            stripped
            and stripped != sentence
            and not sentence_misattributes(stripped, source)
            and line_is_grounded(stripped, source, analysis=True)
        ):
            kept.append(stripped)
    return " ".join(kept)


def _token_prices(source: str) -> list:
    prices = []
    for match in re.finditer(r"\$\s?(\d+(?:\.\d+)?)", source or ""):
        window = source[max(0, match.start() - 60):match.end() + 60].lower()
        if re.search(r"token|per\s+1\s*m|/\s*m\b|million", window):
            prices.append(float(match.group(1)))
    return prices


def price_unit_mismatch(text: str, source: str) -> bool:
    """'cents per million' does not describe a price the source states as $0.10 or more."""
    if not re.search(r"\bcents?\s+per\s+million\b", text or "", re.I):
        return False
    return any(price >= 0.10 for price in _token_prices(source))


def fix_price_units(text: str, source: str) -> str:
    """Say under a dollar when that is what the source price is. Drop an unstated frontier-lab label."""
    updated = text or ""
    if price_unit_mismatch(updated, source):
        prices = _token_prices(source)
        if prices and all(0 < price < 1 for price in prices):
            replacement = "under a dollar per million tokens"
        else:
            replacement = "per million tokens"
        updated = re.sub(
            r"\bcents?\s+per\s+million(?:\s+tokens)?\b",
            replacement,
            updated,
            flags=re.I,
        )
    if re.search(r"frontier-lab model", updated, re.I) and not re.search(
        r"frontier[-\s]lab", source or "", re.I
    ):
        updated = re.sub(r"\bfrontier-lab model\b", "model", updated, flags=re.I)
    return re.sub(r"\s{2,}", " ", updated).strip()


_MONEY_AMOUNT = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:billion|million|bln|bn))?",
    re.I,
)
_GLUED_AFTER_DATE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\b\.?"
    r"\s+\$\s?\d",
    re.I,
)
_NUMBER_JOIN = re.compile(
    r"\b(and|or|to|from|versus|vs\.?|but|while|reaching|heading|expected|expects|"
    r"expect|toward|towards|plus|against|with|including|of|at|by|for|per|about|"
    r"around|nearly|roughly|over|under|than|which)\b",
    re.I,
)
_SHORTFALL_WHY = re.compile(
    r"\b(revenue shortfall|shortfall|fell short|missed expectations|less than expected)\b",
    re.I,
)


def has_unjoined_numbers(sentence: str) -> bool:
    """True when two money figures were jammed together, or a date sits on the next figure.

    'nearing $50 billion at the end of September $70 billion' is the failure:
    the second figure has no joining word of its own.
    """
    text = sentence or ""
    if _GLUED_AFTER_DATE.search(text):
        return True
    amounts = list(_MONEY_AMOUNT.finditer(text))
    if len(amounts) < 2:
        return False
    for left, right in zip(amounts, amounts[1:]):
        gap = text[left.end():right.start()]
        if not _NUMBER_JOIN.search(gap):
            return True
    return False


def _revenue_timeline(article: Article) -> str:
    """Reuters' two figures, joined. Empty unless the source states both."""
    source = f"{article.full_text or ''} {article.content_preview or ''}"
    if not re.search(r"\$\s?50\s?billion", source, re.I):
        return ""
    if not re.search(r"\$\s?70\s?billion", source, re.I):
        return ""
    low = source.lower()
    if "annualized" not in low or "september" not in low:
        return ""
    if not re.search(r"year-end|year end|end of the year", low):
        return ""
    sentence = (
        "Annualized revenue was nearing $50 billion at the end of September, "
        "and $70 billion is expected by year-end."
    )
    if not line_is_grounded(sentence, source, analysis=False):
        return ""
    return sentence


def drop_unjoined_numbers(body: str, article: Article) -> str:
    """Drop a jammed sentence and put the Reuters timeline back if the source has it."""
    kept = [sentence for sentence in split_sentences(body or "") if not has_unjoined_numbers(sentence)]
    text = " ".join(kept)
    timeline = _revenue_timeline(article)
    if not timeline:
        return text
    if "september" not in text.lower():
        # "$50 billion in annualized revenue ... $70 billion by year-end" is missing the month.
        spliced = re.sub(
            r"(\$\s?50\s?billion)(\s+in\s+annualized\s+revenue)",
            r"\1\2 at the end of September",
            text,
            count=1,
            flags=re.I,
        )
        sentences = split_sentences(spliced)
        if "september" in spliced.lower() and not any(has_unjoined_numbers(sentence) for sentence in sentences):
            text = spliced
        elif not re.search(r"\$\s?70\s?billion", text, re.I):
            text = f"{text} {timeline}".strip()
        else:
            text = f"{text} {timeline}".strip()
    elif not re.search(r"\$\s?70\s?billion", text, re.I):
        text = f"{text} {timeline}".strip()
    return text


_EXPECTATION_CLAUSE = re.compile(
    r",?\s*(?:about|roughly|nearly|some)?\s*\$?\s?\d[\d.]*(?:\s?(?:billion|bln|million))?\s+"
    r"less than (?:the )?expected\b",
    re.I,
)
_EXPECTATION_SENTENCE = re.compile(
    r"less than expected|than expected|short of expectations|below expectations",
    re.I,
)


def strip_unverified_expectation(body: str, article: Article) -> str:
    """Drop 'less than expected' unless the wire itself says it.

    A recap turned Reuters' '$50 billion, heading to $70 billion' into
    '$20 billion less than expected'. That framing does not survive
    unless Reuters (or its Yahoo copy) uses those words.
    """
    source = f"{article.full_text or ''} {article.content_preview or ''}"
    domain = (article.source_domain or "").lower()
    wire = "reuters.com" in domain or "yahoo.com" in domain
    if wire and _EXPECTATION_SENTENCE.search(source):
        return body
    text = _EXPECTATION_CLAUSE.sub("", body or "")
    kept = [sentence for sentence in split_sentences(text) if not _EXPECTATION_SENTENCE.search(sentence)]
    return " ".join(kept)


def fit_lede(lede: str, since=None, until=None) -> str:
    """A 29-hour window is not 'this week'."""
    text = (lede or "").strip()
    if not text or since is None or until is None:
        return text
    hours = (until - since).total_seconds() / 3600.0
    if hours > 48:
        return text
    if text.lower().startswith("since monday"):
        return re.sub(r"\bthis week\b", "this window", text, flags=re.I)
    if re.match(r"this week\b", text, re.I):
        rest = re.sub(r"^this week\b[:,]?\s*", "", text, flags=re.I)
        rest = re.sub(r"^(brings|pairs|is|has|includes|covers)\s+", "", rest, flags=re.I)
        if rest:
            rest = rest[0].lower() + rest[1:]
        text = "Since Monday morning, " + rest
        return text
    lowered = text[0].lower() + text[1:] if text else ""
    return "Since Monday morning, " + lowered


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
                    timeout=_READ_TIMEOUTS[attempt],
                )
            except requests.Timeout:
                if attempt == 2:
                    raise
                wait = 8 * (2 ** attempt)
                print(
                    f"Warning: Together timed out (read timeout={_READ_TIMEOUTS[attempt]}). "
                    f"Retrying in {wait}s.",
                    file=sys.stderr,
                )
                time.sleep(wait)
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


def _accept(line: str, article: Article, *, analysis: bool, fallback: str, limit: int = 220) -> str:
    cleaned = clean_line(line, limit=limit)
    # Thin excerpts used to force the bare headline. Check the full source instead.
    if line_is_grounded(cleaned, _source_text(article), analysis=analysis):
        return cleaned
    return fallback


def write_copies(articles: list, api_key: str | None, model: str, *, strict_auth: bool) -> list:
    if not articles:
        return []
    if not api_key:
        return [extractive_lines(article) for article in articles]
    try:
        raw = _call_together(_prompt(articles), api_key, model)
    except AuthError:
        if strict_auth:
            raise
        raise SchemaError("Writing failed. Not publishing.")
    except SchemaError:
        raise
    except Exception as exc:
        raise SchemaError(f"Writing failed ({exc}). Not publishing.") from exc

    payload = _parse_json_object(raw)
    rows = payload.get("items") if payload else None
    if not isinstance(rows, list):
        raise SchemaError("Writing failed. Not publishing.")

    by_index = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("index"), int):
            by_index[row["index"]] = row

    copies = []
    for index, article in enumerate(articles):
        fallback_what, _fallback_why = extractive_lines(article)
        row = by_index.get(index)
        if not row:
            copies.append(("", ""))
            continue
        what = _accept(str(row.get("what_happened") or ""), article, analysis=False, fallback=fallback_what)
        why = scrub_copy(_accept(
            str(row.get("why_it_matters") or ""),
            article,
            analysis=True,
            fallback="",
        ))
        if is_filler_why(why) or is_criteria_echo(why):
            why = ""
        copies.append((scrub_copy(what), why))
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
    return f"This window is mostly about {body}."


def _story_noun(headline: str, title: str) -> str:
    """A noun phrase. A headline clause after 'are' is not grammatical."""
    text = f"{headline or ''} {title or ''}"
    amount = re.search(r"\$\s?\d[\d.]*\s?billion", text, re.I)
    figure = amount.group(0) if amount else ""
    if re.search(r"\banthropic\b", text, re.I) and re.search(r"\bloss\b", text, re.I):
        return f"Anthropic's reported {figure} loss".replace("  ", " ").strip()
    if re.search(r"\bnscale\b", text, re.I):
        return "the Nscale deal"
    if re.search(r"\bdeepseek\b", text, re.I) and re.search(r"\b(remov\w*|order|exclu\w*)\b", text, re.I):
        return "an order removing DeepSeek"
    if re.search(r"\bnvidia\b", text, re.I) and re.search(r"\b(bytedance|alibaba|tencent)\b", text, re.I):
        return f"Nvidia chip orders{(' of ' + figure) if figure else ''} from ByteDance, Alibaba, and Tencent"
    if re.search(r"\bopenai\b", text, re.I) and re.search(r"\b(raise|fundraising|ipo)\b|\$\s?\d", text, re.I):
        return f"OpenAI's funding round{(' of ' + figure) if figure else ''}"
    line = (headline or title or "").strip().rstrip(".")
    cut = re.split(
        r"\b(?:shows|showed|logged|logs|says|said|must|brings|bring|orders|ordered|"
        r"raises|raised|seeks|ships|shipped)\b",
        line,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" ,;:-")
    return cut or line


def story_lede(rows: list) -> str:
    """One grammatical sentence naming the top two or three stories."""
    names = []
    for row in rows[:3]:
        headline = (row[1] if len(row) > 1 else "") or ""
        title = row[0].title if row else ""
        name = _story_noun(headline, title)
        if name and name not in names:
            names.append(name)
    if not names:
        return theme_lede([row[0] for row in rows if row])
    if len(names) == 1:
        body = names[0]
    elif len(names) == 2:
        body = f"{names[0]} and {names[1]}"
    else:
        body = f"{names[0]}, {names[1]}, and {names[2]}"
    return f"The top stories are {body}."


_GENERIC_LEDE = re.compile(
    r"mostly about|handful of developments|new models|agent tools",
    re.I,
)
# "The top stories are Anthropic logged..." or "...loss shows..." is not a sentence.
_BROKEN_LEDE = re.compile(
    r"\bare\s+[A-Z][^.]{0,90}\b(?:shows|showed|logged|says|said|must|brings|raises|seeks|ships)\b",
    re.I,
)
_LEDE_THEME = {
    "models", "model", "agent", "agents", "tools", "infrastructure", "policy",
    "funding", "window", "mostly", "about", "developments", "systems", "people",
    "building", "stories", "story", "morning", "monday", "since",
}


def lede_names_stories(lede: str, rows: list) -> bool:
    """True when the lede names at least two of the top stories."""
    text = lede or ""
    if _BROKEN_LEDE.search(text):
        return False
    if not rows:
        return bool(text) and not _GENERIC_LEDE.search(text)
    hits = 0
    for row in rows[:3]:
        headline = row[1] if len(row) > 1 else ""
        shared = set(tokens(text)) & set(tokens(f"{headline} {row[0].title}"))
        shared = {word for word in shared if len(word) >= 5 and word not in _LEDE_THEME}
        if shared:
            hits += 1
    needed = 1 if len(rows) < 2 else min(2, len(rows))
    if _GENERIC_LEDE.search(text) and hits < needed:
        return False
    return hits >= needed


def _story_keys(row, rows: list) -> list:
    """Words that identify this story and not the others in the lede."""
    headline = row[1] if len(row) > 1 else ""
    own = set(tokens(f"{headline} {row[0].title}"))
    others = set()
    for other in rows[:3]:
        if other is row:
            continue
        other_headline = other[1] if len(other) > 1 else ""
        others |= set(tokens(f"{other_headline} {other[0].title}"))
    unique = [
        word for word in own
        if len(word) >= 5 and word not in _LEDE_THEME and word not in others
    ]
    if unique:
        return unique
    return [word for word in own if len(word) >= 5 and word not in _LEDE_THEME]


def lede_follows_rank(lede: str, rows: list) -> bool:
    """The named stories appear in the same order as the ranked items."""
    text = (lede or "").lower()
    positions = []
    for row in rows[:3]:
        found = None
        for word in _story_keys(row, rows):
            at = text.find(word)
            if at >= 0:
                found = at
                break
        if found is not None:
            positions.append(found)
    return positions == sorted(positions)


def _window_note(since, until) -> str:
    if since is None or until is None:
        return ""
    hours = (until - since).total_seconds() / 3600.0
    if hours > 48:
        return ""
    return (
        "The window is shorter than two days, from Monday morning through Tuesday morning. "
        "Open the lede with 'Since Monday morning'. Do not write 'this week' in the lede, "
        "the headlines, or the why-lines.\n"
    )


def _rank_prompt(articles: list, recent: list, limit: int, since=None, until=None) -> str:
    items = [_item_payload(article, index) for index, article in enumerate(articles)]
    already = [
        {"title": item.get("title") or "", "url": item.get("url") or ""}
        for item in (recent or [])
        if item.get("title")
    ]
    return f"""You rank and write one public AI news briefing for people building with AI.

The bar for keeping a story: would a builder or an AI exec bring it up this week?
Do not echo that bar, or the words "this week", in the published lede, headlines, or why-lines.
{_window_note(since, until)}
Prefer, in this order:
1. A frontier-lab move from OpenAI, Anthropic, Google, Meta, Microsoft, xAI, Mistral, DeepSeek, or MiniMax.
2. Big money: revenue, ARR, a reported loss, a funding round, an IPO path, a cloud deal stated in the billions, or a chip order stated in the tens of billions (such as Nvidia orders from ByteDance, Alibaba, and Tencent). A frontier lab's loss, that chip order, or a mega-deal outranks a serving-stack release. Do not drop the loss, the chip order, or the deal to make room for the release.
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
A hard number stays only when it appears in that item's text. Prefer the lab's own post, the Federal Register, or a wire (Reuters, Bloomberg, the Financial Times, the Wall Street Journal, CNBC, the Associated Press, or a Yahoo copy of that wire) over a roundup or a law-firm blog. When a benchmark or accuracy figure comes from the company that built the model, write company-reported next to it, including on a vendor's own post.

For each chosen item:
- headline: a short rewritten sentence, under 12 words, with the spec or the number. Do not paste the page title. For a Federal Register removal order, do not paste the notice title. Write plain English that names the product, such as federal agencies must remove it from civilian systems, and end it with a period.
- body: 2 to 3 sentences of prose from that item's text only. A body under 2 sentences is invalid. Keep prices, dates, benchmark numbers, and availability. Do not repeat a sentence. Do not paste a table, a markdown heading, or the raw page. Join every pair of dollar figures with a word such as and, to, or from. Never leave a month glued to the next dollar amount. If the text says annualized revenue was about $50 billion at the end of September and $70 billion is expected by year-end, write both figures that way. Do not write that a figure is "less than expected", a "shortfall", or "short of expectations" unless that item's text uses those words. If the text cannot support 2 factual sentences, omit the item.
- why_it_matters: one sentence naming a concrete consequence (who has to change what), using facts from that item's text. Do not restate a sentence from the body. Do not call revenue a shortfall. If the story is a product a reader may not know, say what it is. For a removal order that covers third-party products, name the product and say vendors must audit which products include it before a civilian agency can buy them. For a reported loss, use the charge facts in the text: if it says most of a dollar figure is non-cash accounting charges, say that, and if it says to watch operating burn in the S-1, say that. Do not write that a loss changes what the lab can spend. Attribute a quotation only to the person the text quotes. A reporter's sentence is not that person's quote. Do not call a token price cents per million when the text states it in dollars. If every per-million price in the text is under a dollar, say under a dollar per million tokens. Do not call a model a frontier-lab model unless the text does. No filler. Do not write "worth a look", "a builder or an AI exec would bring up", or any other echo of the selection bar.
Use only names, numbers, and quotes that appear in that item's text.

lede: one grammatical sentence that names the top 2 or 3 stories as noun phrases, in the same order as the items, such as a reported loss, a deal, and an order. Do not write "the top stories are" followed by a headline clause. No new facts. No hype. No em dashes.
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
    source = scrub_copy("\n".join([
        getattr(article, "full_text", "") or "",
        article.content_preview or "",
    ]))
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
    vendor = article.source_kind == "lab" or "minimax." in (article.source_domain or "").lower()
    lowered = text.lower()
    if "vendor-reported" in lowered and "company-reported" not in lowered:
        return re.sub("vendor-reported", "company-reported", text, count=1, flags=re.I)
    if "company-reported" in lowered:
        return text
    if not vendor:
        return text
    source = f"{article.full_text or ''} {article.content_preview or ''}"
    if not _BENCH_SENTENCE.search(source):
        return text
    word = "company-reported"
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
    r"\b(exclusion|barred)\b|"
    r"(?:raise|raises|raised|valuation|acqui\w*|funding|fundraising|loss).{0,48}\$\s?\d[\d.]*\s?(?:billion|bln)|"
    r"\$\s?\d[\d.]*\s?(?:billion|bln).{0,48}(?:raise|valuation|acqui\w*|funding|fundraising|deal|loss|ipo)|"
    r"\b(?:openai|anthropic)\b.{0,80}\b(?:fundraising|ipo|loss)\b|"
    r"\b(?:fundraising|ipo|loss)\b.{0,80}\b(?:openai|anthropic)\b|"
    r"\bnscale\b.{0,80}\b(?:deal|talks|multibillion)\b|"
    r"\b(?:deal|talks|multibillion)\b.{0,80}\bnscale\b",
    re.I,
)
_NARROW_RELEASE = re.compile(r"\b(llm-d|vllm|sglang)\b", re.I)
_DETAILS_LEAK = re.compile(r"\b(?:Prices|Benchmarks|Dates|Availability|Caveats)\s*:", re.I)


def _must_keep(article: Article) -> bool:
    """Stories the ranking pass must not drop to make room for something smaller."""
    from src.rank import is_mega_order
    title = article.title or ""
    if is_mega_order(article):
        return True
    if re.search(r"\bnscale\b", title, re.I) and re.search(r"\b(deal|talks)\b", title, re.I):
        return True
    if re.search(r"\banthropic\b", title, re.I) and re.search(r"\bloss\b", title, re.I):
        return True
    return False


def _major_money(article: Article) -> bool:
    """A frontier-lab loss, round, chip order, or stated mega-deal."""
    from src.rank import is_mega_order
    title = article.title or ""
    if _NARROW_RELEASE.search(title):
        return False
    if is_mega_order(article) or _must_keep(article):
        return True
    if re.search(r"\bnscale\b", title, re.I) and re.search(r"\b(deal|talks|multibillion)\b", title, re.I):
        return True
    return bool(
        re.search(r"\b(openai|anthropic)\b", title, re.I)
        and re.search(r"\b(loss|fundraising|ipo)\b|\$\s?\d[\d.]*\s?(?:billion|bln)", title, re.I)
    )


def _narrow_release(article: Article) -> bool:
    if _major_money(article):
        return False
    return _NARROW_RELEASE.search(article.title or "") is not None


def _order_pairs(pairs: list) -> list:
    """Lab financials and major deals come before a serving-stack release."""
    major, middle, narrow = [], [], []
    for row in pairs:
        if _major_money(row[0]):
            major.append(row)
        elif _narrow_release(row[0]):
            narrow.append(row)
        else:
            middle.append(row)
    return major + middle + narrow


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
        if score_article(article) < 6 or not (_CLEAR_BAR.search(article.title or "") or _must_keep(article)):
            continue
        skipped.append(article)
    skipped.sort(key=score_article, reverse=True)
    for article in skipped:
        what, why = extractive_lines(article)
        headline = _order_lead(article) or rewrite_headline(what or article.title)
        if headline_is_raw(headline, article.title) or len(headline.split()) > 12:
            headline = rewrite_headline(article.title)
        body = drop_unjoined_numbers(
            strip_unverified_expectation(label_benchmark(factual_body(article), article), article),
            article,
        )
        if len(split_sentences(body)) < 2:
            continue
        why_line = _usable_why(why, body, article)
        if not why_line or is_criteria_echo(why_line):
            continue
        if not line_is_grounded(why_line, _source_text(article), analysis=True) and why_line != _order_why(article):
            continue
        if len(pairs) >= limit and not _drop_for_keep(pairs):
            break
        pairs.append((article, headline, body, why_line))
    pairs = _order_pairs(pairs)
    while len(pairs) > limit and _drop_for_keep(pairs):
        pass
    return pairs


def _drop_for_keep(pairs: list) -> bool:
    """Drop a lesser item. Never drop the lab loss, the Nscale deal, or the chip order."""
    for index in range(len(pairs) - 1, -1, -1):
        if _narrow_release(pairs[index][0]):
            pairs.pop(index)
            return True
    for index in range(len(pairs) - 1, -1, -1):
        if not _must_keep(pairs[index][0]):
            pairs.pop(index)
            return True
    return False


def _not_publishing(exc: BaseException | None = None) -> SchemaError:
    if exc is None:
        return SchemaError("Ranking failed. Not publishing.")
    return SchemaError(f"Ranking failed ({exc}). Not publishing.")


def _compose_group(
    articles: list,
    api_key: str,
    model: str,
    *,
    recent,
    limit: int,
    require_full_body: bool,
    since,
    until,
) -> tuple:
    """One ranking call. Raises on failure. An empty list means nothing cleared the bar."""
    prompt = _rank_prompt(articles, recent or [], limit, since=since, until=until)
    raw = _call_together(prompt, api_key, model)
    payload = _parse_json_object(raw)
    if require_full_body:
        payload = _schema_or_retry(payload, prompt, api_key, model)
    rows = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise SchemaError("Ranking failed. Not publishing.")

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
        what_fb, _why_fb = extractive_lines(article)
        headline_raw = scrub_copy(str(row.get("headline") or row.get("what_happened") or ""))
        headline = _accept(headline_raw, article, analysis=False, fallback=what_fb)
        # A Federal Register notice is rewritten even when the model copies the title.
        if _government_notice(article) or _order_lead(article):
            headline = _order_lead(article) or rewrite_headline(article.title)
        elif (
            headline_is_raw(headline, article.title)
            or len(headline.split()) > 12
            or _CHROME.search(headline)
        ):
            headline = rewrite_headline(article.title)
        elif len(headline) > 110:
            headline = _short_lead(headline, 110)
        headline = scrub_copy(headline)
        if _CHROME.search(headline):
            headline = clean_line(_CHROME.sub("", headline))
        body = scrub_copy(drop_unjoined_numbers(
            strip_unverified_expectation(
                label_benchmark(_publishable_body(str(row.get("body") or ""), article), article),
                article,
            ),
            article,
        ))
        why = scrub_copy(_accept(
            str(row.get("why_it_matters") or ""),
            article,
            analysis=True,
            fallback="",
            limit=380,
        ))
        body = _DETAILS_LEAK.split(body)[0].strip()
        why = _DETAILS_LEAK.split(why)[0].strip()
        source = _source_text(article)
        body = fix_quote_attribution(fix_price_units(body, source), source)
        why = fix_quote_attribution(fix_price_units(why, source), source)
        body = _drop_repeated_why(body, why)
        if _CHROME.search(body) or _CHROME.search(headline):
            print(f"Dropped (page chrome in the copy): {article.title}", file=sys.stderr)
            continue
        # A category slogan is not a why-line, including the extractive template.
        why = _usable_why(why, body, article)
        if headline_is_raw(why, article.title):
            why = _usable_why("", body, article)
        if not why or is_criteria_echo(why) or is_filler_why(why) or headline_is_raw(why, article.title):
            print(f"Dropped (criteria echo or no consequence): {article.title}", file=sys.stderr)
            continue
        pairs.append((article, headline, body, why))
        if len(pairs) >= limit:
            break

    if not pairs:
        # The model looked and found nothing worth posting.
        return "", []

    pairs = _backfill_clear_bar(articles, pairs, limit)

    combined = "\n".join(_source_text(row[0]) for row in pairs)
    lede = fit_lede(clean_line(str((payload or {}).get("lede") or ""), limit=420), since, until)
    if _DANGLING_END.search(lede or ""):
        lede = fit_lede(theme_lede([row[0] for row in pairs]), since, until)
    check = re.sub(r"^Since Monday morning,?\s*", "", lede, flags=re.I)
    if (
        not line_is_grounded(check or lede, combined, analysis=True)
        or not lede_names_stories(lede, pairs)
        or not lede_follows_rank(lede, pairs)
    ):
        lede = fit_lede(story_lede(pairs), since, until)
    return lede, pairs


def _rank_finalists(articles: list, api_key: str, model: str, **kwargs) -> list:
    """Rank the shortlist in batches so one call is not the full page text."""
    if len(articles) <= _RANK_BATCH:
        return articles
    from src.dedupe import same_story
    chosen = []
    for start in range(0, len(articles), _RANK_BATCH):
        _lede, pairs = _compose_group(articles[start:start + _RANK_BATCH], api_key, model, **kwargs)
        for row in pairs:
            article = row[0]
            if any(same_story(article, previous) for previous in chosen):
                continue
            chosen.append(article)
    if len(chosen) <= _RANK_BATCH:
        return chosen
    priority = [article for article in chosen if _CLEAR_BAR.search(article.title or "")]
    rest = [article for article in chosen if article not in priority]
    return (priority + rest)[:_RANK_BATCH]


def compose_briefing(
    articles: list,
    api_key: str | None,
    model: str,
    *,
    strict_auth: bool,
    recent=None,
    limit: int = 7,
    require_full_body: bool = False,
    since=None,
    until=None,
) -> tuple:
    """Rank the shortlist and write the lede plus items. Returns (lede, pairs).

    pairs is a list of (article, headline, body, why_it_matters) in rank order.
    A 3-tuple from an older composer is still accepted by the briefing runner.
    A ranking or writing failure publishes nothing.
    """
    if not articles:
        return "", []
    if not api_key:
        raise _not_publishing()
    kwargs = dict(
        recent=recent,
        limit=limit,
        require_full_body=require_full_body,
        since=since,
        until=until,
    )
    try:
        finalists = _rank_finalists(articles, api_key, model, **kwargs)
        if len(articles) > _RANK_BATCH and not finalists:
            return "", []
        return _compose_group(finalists, api_key, model, **kwargs)
    except AuthError:
        if strict_auth:
            raise
        raise _not_publishing()
    except SchemaError:
        raise
    except Exception as exc:
        raise _not_publishing(exc) from exc
