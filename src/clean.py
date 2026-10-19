import html
import re

HYPE_RE = re.compile(
    r"\b(game[- ]changer|revolutionary|revolutionize|unlock|delve|landscape|"
    r"pivotal|cutting[- ]edge|seamless|groundbreaking|unprecedented|"
    r"it's worth noting|worth noting|elevate|supercharge|next[- ]gen)\b",
    re.I,
)

_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "were",
    "has", "have", "its", "our", "their", "into", "over", "after", "before",
    "about", "your", "you", "not", "but", "can", "will", "just", "than", "then",
    "how", "what", "when", "who", "why", "all", "new", "now", "more", "most",
}

_ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z0-9&.+-]{1,}\b")
_ENTITY_OK = {"ai", "llm", "llms", "gpu", "gpus", "api", "apis", "ml", "rag"}
_COMMON_WORDS = {
    "a", "an", "the", "this", "that", "these", "those", "it", "its",
    "we", "they", "their", "teams", "people", "builders", "serving",
    "running", "shipping", "one", "new", "open", "using", "after",
    "before", "legal", "model", "models", "agent", "agents", "safety",
    "security", "inference", "hardware", "research", "policy", "worth",
    "with", "from", "when", "what", "how", "why", "who", "for", "and",
}
_QUOTE_RE = re.compile(r"[\"“]([^\"”]{3,})[\"”]")
# Longer dotted numbers (0.32.0, 4.1) win over a single decimal group.
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)+|\d[\d,]*")


def collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def plain_text(raw: str) -> str:
    """Strip tags without pulling in a parser. Fetch paths use BeautifulSoup."""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    return collapse_ws(html.unescape(text))


def clean_line(text: str, limit: int = 220) -> str:
    text = html.unescape(text or "")
    text = text.replace("\u2014", ", ").replace("\u2013", ", ")
    text = text.replace("—", ", ").replace("–", ", ")
    text = text.replace("*", "")
    text = collapse_ws(text).strip(" \t-")
    if not text:
        return ""
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;: ")
    if text and text[-1] not in ".!?":
        text += "."
    return text


def first_sentence(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("\u2014", ", ").replace("\u2013", ", ")
    text = text.replace("—", ", ").replace("–", ", ")
    text = collapse_ws(text)
    if not text:
        return ""
    protected = re.sub(r"\b([A-Z])\.", lambda match: match.group(1) + "\u0000", text)
    match = re.search(r"[.!?](?:\s|$)", protected)
    sentence = text[: match.end()].strip() if match else text
    return clean_line(sentence)


def shares_distinctive_word(left: str, right: str) -> bool:
    shared = set(tokens(left)) & set(tokens(right))
    return any(len(word) >= 5 for word in shared)


def is_thin_preview(preview: str, title: str) -> bool:
    body = collapse_ws(html.unescape(preview or "")).strip().lower().strip(".")
    if body in {"", "tbd", "overview", "undefined", "null", "none"}:
        return True
    if len(body) < 40:
        return True
    title_l = collapse_ws(title or "").lower().strip(".")
    return bool(title_l) and body == title_l


def tokens(text: str) -> list:
    text = html.unescape(text or "").lower().replace("’", "'").replace("`", "'")
    text = re.sub(r"[^a-z0-9\s-]", " ", text)
    out = []
    for word in text.split():
        word = word.strip("-")
        if len(word) <= 2 or word in _STOP:
            continue
        out.append(word)
    return out


def contains_word(haystack_lower: str, word_lower: str) -> bool:
    return re.search(rf"\b{re.escape(word_lower)}\b", haystack_lower) is not None


def numbers(text: str) -> set:
    return set(_NUMBER_RE.findall(text or ""))


def number_in_source(num: str, source: str) -> bool:
    """True when the same numeric token appears in the source, commas ignored."""
    compact = (num or "").replace(",", "")
    if not compact:
        return True
    haystack = (source or "").replace(",", "")
    # A sentence period may follow a number. Another digit, or a decimal
    # continuation, means this is a different figure.
    return re.search(
        rf"(?<![\d.]){re.escape(compact)}(?!\d)(?!\.\d)",
        haystack,
    ) is not None


def _looks_like_name(entity: str, line: str) -> bool:
    if re.search(r"[A-Z]", entity[1:]) or (entity.isupper() and len(entity) >= 2):
        return True
    stripped = line.lstrip("\"'“” ")
    if stripped.startswith(entity) and entity.lower() in _COMMON_WORDS:
        return False
    if stripped.startswith(entity):
        return True
    return True


def novel_entities(line: str, source: str) -> list:
    source_l = (source or "").lower()
    missing = []
    for entity in _ENTITY_RE.findall(line or ""):
        low = entity.lower().strip(".")
        if low in _ENTITY_OK:
            continue
        if not _looks_like_name(entity, line):
            continue
        if not contains_word(source_l, low):
            missing.append(entity)
    return missing


def line_is_grounded(line: str, source: str, *, analysis: bool) -> bool:
    """Keep a line when its numbers, quotes, and names all appear in the source.

    A paraphrase may use different verbs than the headline. That is allowed.
    Invented figures, quotations, proper names, and hype words are not.
    `analysis` remains in the signature so callers can mark interpretation.
    """
    del analysis  # interpretation may rephrase; it still cannot add facts
    if not line or HYPE_RE.search(line):
        return False
    for num in numbers(line):
        if not number_in_source(num, source):
            return False
    source_l = (source or "").lower()
    for quote in _QUOTE_RE.findall(line):
        if quote.lower() not in source_l:
            return False
    if novel_entities(line, source):
        return False
    return True


def slack_escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
