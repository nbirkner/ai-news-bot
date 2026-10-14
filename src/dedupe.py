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


def same_story(left, right) -> bool:
    if canonical_url(left.url) == canonical_url(right.url):
        return True
    return jaccard(left.title, right.title) >= 0.55


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
