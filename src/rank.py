import re

from src.dedupe import model_signature, same_event

# Points come from the headline only. A customer story, a market blurb, or a
# vendor-internal note is not a general news item, and an excerpt that names a
# product inside one of those posts must not promote it.

_MODEL = re.compile(
    r"\b(gpt-\d|gpt \d|claude \d|gemini \d|llama \d|mistral large|deepseek-?\d)\b",
    re.I,
)
_STRONG_POLICY = re.compile(
    r"\b(regulat\w*|lawsuit|sues|sued|sue\b|executive order|export control|"
    r"antitrust|bankrupt\w*|midterms?|election)\b",
    re.I,
)
_MILD_POLICY = re.compile(r"\b(polic(?:y|ies)|legislation|memorandum|\bmou\b)\b", re.I)
_STRONG_SAFETY = re.compile(
    r"\b(jailbreaks?|vulnerabilit\w*|zero-day|cyber ?attacks?|espionage|"
    r"spies|spying|data breach|responsible scaling)\b",
    re.I,
)
_MILD_SAFETY = re.compile(r"\b(safety|security|misuse|bio-?risk)\b", re.I)
_INFRA = re.compile(
    r"\b(vllm|sg ?lang|inference|tensorrt|cuda|blackwell|gpu|chip|accelerator)\b",
    re.I,
)
_DEAL = re.compile(
    r"\b(acqui(?:res|red|sition)|merger|funding round|series [a-e]|raises \$)\b",
    re.I,
)
_AGENTS = re.compile(r"\bagents?\b|\bcomputer use\b|\btool use\b", re.I)
_OPEN_WEIGHTS = re.compile(r"\bopen[- ]weights?\b|\bopen source model\b", re.I)

_OPINION = re.compile(
    r"\b(podcast|newsletter|opinion|essay|commentary)\b|\bthe ai race\b",
    re.I,
)
_MARKET = re.compile(
    r"\b(nasdaq|wall street|stocks?|shares|s&p|dow jones|rout)\b",
    re.I,
)
_PRERELEASE = re.compile(
    r"(?<![A-Za-z])rc\d*\b|release candidate|pre-release|\bproto-",
    re.I,
)
_OFFICE = re.compile(r"\boffices?\b|\bnew hub\b", re.I)
_CUSTOMER = re.compile(
    r"^(?:How\s+)?[A-Z0-9][\w.&'-]*(?:\s+[A-Z0-9][\w.&'-]*){0,5}\s+"
    r"(?i:cuts|saves|uses|partners|brings|runs|deploys|takes|puts)\b"
)

_KIND_BONUS = {"lab": 3, "release": 2, "news": 1, "paper": 0}


def score_article(article) -> int:
    title = article.title or ""
    score = _KIND_BONUS.get(article.source_kind, 0)
    has_model = _MODEL.search(title) is not None
    if has_model:
        score += 8
    if _STRONG_POLICY.search(title):
        score += 6
    elif _MILD_POLICY.search(title):
        score += 4
    if _STRONG_SAFETY.search(title):
        score += 6
    elif _MILD_SAFETY.search(title) and not has_model:
        score += 3
    if _INFRA.search(title):
        score += 4
    if _DEAL.search(title):
        score += 5
    if _AGENTS.search(title):
        score += 2
    if _OPEN_WEIGHTS.search(title) and not has_model:
        score += 3
    if _OPINION.search(title):
        score -= 6
    if _MARKET.search(title):
        score -= 6
    if _PRERELEASE.search(title):
        score -= 5
    if _OFFICE.search(title):
        score -= 4
    if _CUSTOMER.search(title):
        score -= 6
    return score


def select_items(
    articles: list,
    *,
    limit: int = 6,
    material_score: int = 6,
    paper_min_score: int = 9,
    max_per_source: int = 2,
    max_per_signature: int = 1,
) -> list:
    """Highest score first. On a tie, keep the earlier piece (the announcement)."""
    ranked = sorted(
        articles,
        key=lambda article: (
            score_article(article),
            -article.published_at.timestamp(),
        ),
        reverse=True,
    )
    chosen = []
    per_source = {}
    per_signature = {}
    papers = 0
    for article in ranked:
        score = score_article(article)
        if score < material_score:
            break
        if article.source_kind == "paper":
            if score < paper_min_score or papers >= 1:
                continue
        if per_source.get(article.source_name, 0) >= max_per_source:
            continue
        signature = model_signature(article.title)
        if signature and per_signature.get(signature, 0) >= max_per_signature:
            continue
        if any(
            same_event(article, previous)
            and article.source_priority >= previous.source_priority
            for previous in chosen
        ):
            continue
        chosen.append(article)
        per_source[article.source_name] = per_source.get(article.source_name, 0) + 1
        if signature:
            per_signature[signature] = per_signature.get(signature, 0) + 1
        if article.source_kind == "paper":
            papers += 1
        if len(chosen) >= limit:
            break
    return chosen


# "Together AI releases a model" can be public news. Revenue, customers, and
# internal notes are not, and neither is a "for Together" angle.
_TOGETHER_ANGLE = re.compile(
    r"\bfor together\b|"
    r"\btogether(?:\s+ai)?(?:'s|’s)?\s+(?:customers?|revenue|arr|mrr|internals?|confidential)\b",
    re.I,
)


def hard_noise(article) -> bool:
    """Customer stories, market blurbs, release candidates, opinion, office news."""
    title = article.title or ""
    if _TOGETHER_ANGLE.search(title):
        return True
    # Lab blogs title customer explainers "How Acme is building...".
    if re.match(r"^how\b", title.strip(), re.I):
        return True
    if _CUSTOMER.search(title) or _MARKET.search(title) or _PRERELEASE.search(title):
        return True
    if _OPINION.search(title):
        return True
    if _OFFICE.search(title) and not _STRONG_POLICY.search(title) and not _DEAL.search(title):
        return True
    return False


_BUCKET_ORDER = ("model", "agents", "infra", "policy", "money", "other")


def bucket_for(article) -> str:
    """Coarse topic, so a low keyword score cannot crowd out a whole theme."""
    title = article.title or ""
    if re.search(
        r"deepseek|qwen|kimi|moonshot|gpt-\d|claude \d|gemini \d|llama \d|"
        r"open[- ]weights?|foundation model",
        title,
        re.I,
    ):
        return "model"
    if _DEAL.search(title) or re.search(r"\braises?\b|\$\s?\d", title, re.I):
        return "money"
    if (
        _STRONG_POLICY.search(title)
        or _MILD_POLICY.search(title)
        or re.search(r"\bbill\b|lawmakers|workforce|legislation|public benefit|\bfund\b", title, re.I)
    ):
        return "policy"
    if _INFRA.search(title) or re.search(
        r"data center|datacenter|power|grid|chip|nscale|blackwell",
        title,
        re.I,
    ):
        return "infra"
    if _AGENTS.search(title) or re.search(r"\bagents?\b", title, re.I):
        return "agents"
    if _MODEL.search(title):
        return "model"
    if article.source_kind == "release":
        return "infra"
    return "other"


def shortlist_for_reading(articles: list, *, limit: int = 15) -> list:
    """Pick a diverse set for full-text reading. The model ranks this set.

    Keyword score is not a cutoff. Hard noise is dropped. Within a topic,
    newer items come first so a quiet headline is not buried under patch notes.
    """
    pool = [article for article in articles if not hard_noise(article)]
    buckets = {name: [] for name in _BUCKET_ORDER}
    chosen = []
    seen = set()

    def add(article) -> None:
        key = (article.source_name, article.title)
        if key in seen or len(chosen) >= limit:
            return
        seen.add(key)
        chosen.append(article)

    # A source with one or two items in the window is usually the reason that
    # feed exists. Keep those even when a busier feed published later.
    counts = {}
    for article in pool:
        counts[article.source_name] = counts.get(article.source_name, 0) + 1
    sparse = sorted(
        (article for article in pool if counts[article.source_name] <= 2),
        key=lambda article: (article.source_priority, -article.published_at.timestamp()),
    )
    for article in sparse:
        add(article)
        if len(chosen) >= min(limit, 8):
            break
    ordered = sorted(pool, key=lambda article: article.published_at, reverse=True)
    for article in ordered:
        buckets[bucket_for(article)].append(article)
    while len(chosen) < limit:
        progressed = False
        for name in _BUCKET_ORDER:
            # Skip items the sparse pass already kept, and take the next new one.
            # Stopping on an all-seen pass would drop later stories in that topic.
            while buckets[name]:
                article = buckets[name].pop(0)
                key = (article.source_name, article.title)
                if key in seen:
                    continue
                seen.add(key)
                chosen.append(article)
                progressed = True
                break
            if len(chosen) >= limit:
                break
        if not progressed:
            break
    return chosen
