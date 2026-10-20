import re
from urllib.parse import urlparse

from src.clean import tokens


def canonical_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/")
    combined = host + path
    combined = re.sub(
        r"arxiv\.org/pdf/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?$",
        r"arxiv.org/abs/\1",
        combined,
        flags=re.I,
    )
    combined = re.sub(
        r"(arxiv\.org/abs/\d{4}\.\d{4,5})v\d+$",
        r"\1",
        combined,
        flags=re.I,
    )
    if "arxiv.org/" in combined.lower():
        return combined.lower().replace("export.arxiv.org", "arxiv.org")
    return combined


def jaccard(left: str, right: str) -> float:
    a = set(tokens(left))
    b = set(tokens(right))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


_LAB_NAMES = {
    "openai", "anthropic", "deepmind", "google", "meta", "mistral",
    "nvidia", "claude", "gemini", "chatgpt",
}


def _names(article) -> set:
    words = set(tokens(article.title))
    return {article.source_name.lower()} | {word for word in words if word in _LAB_NAMES}


def same_event(left, right) -> bool:
    """A lab post and a later recap that share a distinctive word."""
    if not (_names(left) & _names(right)):
        return False
    shared = {
        word for word in set(tokens(left.title)) & set(tokens(right.title))
        if len(word) >= 5 and word not in _LAB_NAMES
    }
    return bool(shared)


_MONEY_EVENT = re.compile(r"\b(raise|raises|raising|fundraising|ipo|valuation)\b", re.I)
_POLICY_EVENT = re.compile(r"\b(ban|bans|banned|barred|exclusion|removal)\b", re.I)
_ACTORS = re.compile(
    r"\b(openai|anthropic|deepseek|nvidia|microsoft|meta|google|minimax|mistral)\b",
    re.I,
)


def _actors(title: str) -> set:
    return {match.group(0).lower() for match in _ACTORS.finditer(title or "")}


def _same_named_event(left, right, pattern) -> bool:
    """A wire and its syndication often rewrite the verb. Match the labs and the event."""
    if not pattern.search(left.title or "") or not pattern.search(right.title or ""):
        return False
    shared = _actors(left.title) & _actors(right.title)
    return len(shared) >= 2 or (len(shared) == 1 and pattern == _POLICY_EVENT)


_ACQUI_EVENT = re.compile(r"\bacqui", re.I)
_ACQUI_SKIP = {
    "exclusive", "acquires", "acquired", "acquisition", "acquire", "startup", "company",
}


def _same_acquisition(left, right) -> bool:
    """Two outlets covering one lab buying one named company."""
    if not _ACQUI_EVENT.search(left.title or "") or not _ACQUI_EVENT.search(right.title or ""):
        return False
    if not (_actors(left.title) & _actors(right.title)):
        return False
    shared = {
        word
        for word in set(tokens(left.title)) & set(tokens(right.title))
        if len(word) >= 5 and word not in _ACQUI_SKIP and word not in _actors(left.title)
    }
    return bool(shared)


def same_story(left, right) -> bool:
    if canonical_url(left.url) == canonical_url(right.url):
        return True
    if jaccard(left.title, right.title) >= 0.55:
        return True
    return (
        _same_named_event(left, right, _MONEY_EVENT)
        or _same_named_event(left, right, _POLICY_EVENT)
        or _same_acquisition(left, right)
    )


_SIGNATURE = re.compile(
    r"\b(gpt|claude|gemini|llama|mistral)[-\s]?(\d+)",
    re.I,
)


def model_signature(title: str) -> str:
    match = _SIGNATURE.search(title or "")
    if not match:
        return ""
    return f"{match.group(1).lower()}-{match.group(2)}"


def dedupe_articles(articles: list) -> list:
    """Keep the primary source when two items are the same story."""
    ordered = sorted(
        articles,
        key=lambda article: (
            article.source_priority,
            -len(article.content_preview or ""),
            article.published_at.timestamp() * -1,
        ),
    )
    kept = []
    for article in ordered:
        if any(same_story(article, previous) for previous in kept):
            continue
        kept.append(article)
    return kept
