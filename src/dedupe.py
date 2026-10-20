import re
from datetime import datetime, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

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


_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_DAY = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?\b",
    re.I,
)
_ACTION = re.compile(
    r"\b(sue[ds]?|sued|lawsuit|suits|filed|filing|complaint|introduced|"
    r"passed|signed|announced|released|ordered|banned|barred)\b",
    re.I,
)
_EVENT_KIND = (
    ("lawsuit", re.compile(r"\b(sue[ds]?|sued|lawsuit|suits|complaint|filed)\b", re.I)),
    ("bill", re.compile(r"\b(bill|legislation|statute)\b", re.I)),
    ("ban", re.compile(r"\b(ban|bans|banned|barred|exclusion|removal)\b", re.I)),
    ("funding", re.compile(r"\b(raise|raises|raising|fundraising|ipo|valuation)\b", re.I)),
    ("acquisition", re.compile(r"\bacqui", re.I)),
)


def _event_kind(text: str) -> str:
    for name, pattern in _EVENT_KIND:
        if pattern.search(text or ""):
            return name
    return ""


def event_date(article):
    """Calendar day the underlying event happened, when the text names one.

    A law-firm recap can be published inside the window about a suit that
    happened days earlier. The day in the text is the event, not the byline.
    """
    from datetime import date
    blob = " ".join([
        article.title or "",
        (article.content_preview or "")[:800],
        (article.full_text or "")[:800],
    ])
    year_hint = getattr(article.published_at, "year", None) or 2026
    found = None
    for match in _MONTH_DAY.finditer(blob):
        nearby = blob[max(0, match.start() - 80): match.end() + 40]
        if not _ACTION.search(nearby):
            continue
        month = _MONTHS.get(match.group(1).lower().rstrip("."))
        if not month:
            continue
        day = int(match.group(2))
        year = int(match.group(3)) if match.group(3) else year_hint
        try:
            found_date = date(year, month, day)
        except ValueError:
            continue
        if found is None or found_date < found:
            found = found_date
    return found


def event_already_happened(article, since) -> bool:
    """True when the event day is before the window, whatever the byline says."""
    if "federalregister.gov" in (article.source_domain or ""):
        return False
    if article.source_kind in {"lab", "release"}:
        return False
    happened = event_date(article)
    if happened is None or since is None:
        return False
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    start_day = since.astimezone(ZoneInfo("America/Los_Angeles")).date()
    return happened < start_day


def same_underlying_event(article, other_title: str) -> bool:
    """Same lawsuit, bill, ban, or round, even when the headline was rewritten."""
    if jaccard(article.title, other_title or "") >= 0.45:
        return True
    blob = f"{article.title or ''} {(article.content_preview or '')[:240]}"
    kind = _event_kind(blob)
    if not kind or kind != _event_kind(other_title or ""):
        return False
    return bool(_actors(blob) & _actors(other_title or ""))


_ROUND_WORD = re.compile(
    r"\b(raise|raises|raised|raising|valuation|funding|series [a-e])\b",
    re.I,
)
_ROUND_SKIP = {
    "billion", "million", "valuation", "funding", "raises", "raised", "raising",
    "round", "series", "startup", "company", "capital", "chips",
}


def _same_company_round(left, right) -> bool:
    """One company's round, even when a later writeup changes the verb."""
    if not _ROUND_WORD.search(left.title or "") or not _ROUND_WORD.search(right.title or ""):
        return False
    shared = {
        word
        for word in set(tokens(left.title)) & set(tokens(right.title))
        if len(word) >= 6 and word not in _ROUND_SKIP
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
        or _same_company_round(left, right)
    )


def round_already_announced(article, prior, since) -> bool:
    """A later writeup of a round that was already announced before the window."""
    if since is None or not prior:
        return False
    if "federalregister.gov" in (article.source_domain or ""):
        return False
    if article.source_kind in {"lab", "release"}:
        return False
    if not _ROUND_WORD.search(article.title or ""):
        return False
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    for old in prior:
        published = getattr(old, "published_at", None)
        if published is None:
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        if published >= since:
            continue
        if same_story(article, old):
            return True
    return False


_SIGNATURE = re.compile(
    r"\b(gpt|claude|gemini|llama|mistral)[-\s]?(\d+)",
    re.I,
)


def model_signature(title: str) -> str:
    match = _SIGNATURE.search(title or "")
    if not match:
        return ""
    return f"{match.group(1).lower()}-{match.group(2)}"


def _readable(article) -> bool:
    """A page we can actually quote. A 403 stub is the title and nothing else."""
    if len(article.full_text or "") >= 200:
        return True
    preview = (article.content_preview or "").strip()
    title = (article.title or "").strip()
    return len(preview) >= 180 and preview != title


def _wire_rank(article) -> int:
    """Lower wins. Reuters, then its Yahoo copy, then everyone else, then PYMNTS."""
    blob = f"{article.source_domain or ''} {article.source_name or ''}".lower()
    if "reuters" in blob:
        return 0
    if "yahoo" in blob:
        return 1
    if "pymnts" in blob:
        return 4
    return 2


def _official_record(article) -> bool:
    """The Federal Register is the order. A wire recap is not."""
    return "federalregister.gov" in (article.source_domain or "")


def _prefer(candidate, current) -> bool:
    """Whether candidate should replace current for the same event.

    A lab post or the Federal Register wins over a recap. Between two
    news copies, a readable syndication wins over a wire stub whose page
    returned 401 or 403, and Reuters wins over PYMNTS.
    """
    def primary(article) -> bool:
        if _official_record(article):
            return True
        return article.source_kind in {"lab", "release"} and article.source_priority <= 2

    cand_primary = primary(candidate)
    curr_primary = primary(current)
    if cand_primary != curr_primary:
        return cand_primary
    cand_read = _readable(candidate)
    curr_read = _readable(current)
    if cand_read != curr_read:
        return cand_read
    # A readable Reuters wire beats a PYMNTS recap of the same story.
    if _wire_rank(candidate) != _wire_rank(current):
        return _wire_rank(candidate) < _wire_rank(current)
    if candidate.source_priority != current.source_priority:
        return candidate.source_priority < current.source_priority
    cand_len = len(candidate.full_text or "") + len(candidate.content_preview or "")
    curr_len = len(current.full_text or "") + len(current.content_preview or "")
    return cand_len > curr_len


def dedupe_articles(articles: list) -> list:
    """Keep the primary source when two items are the same story."""
    ordered = sorted(
        articles,
        key=lambda article: (
            0 if article.source_kind in {"lab", "release"} or _official_record(article) else 1,
            article.source_priority,
            -len(article.full_text or ""),
            -len(article.content_preview or ""),
            article.published_at.timestamp() * -1,
        ),
    )
    kept = []
    for article in ordered:
        match = None
        for index, previous in enumerate(kept):
            if same_story(article, previous):
                match = index
                break
        if match is None:
            kept.append(article)
            continue
        if _prefer(article, kept[match]):
            kept[match] = article
    return kept
