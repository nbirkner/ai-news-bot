import re

from src.dedupe import model_signature, same_event

# Points come from the headline only. Excerpts mention product names inside
# customer stories and would otherwise promote those posts.

_MODEL = re.compile(
    r"\b(gpt-\d|gpt \d|claude \d|gemini \d|llama \d|mistral large|deepseek-?\d)\b",
    re.I,
)
_STRONG_POLICY = re.compile(
    r"\b(regulat\w*|lawsuit|sues|sued|sue\b|executive order|export control|"
    r"antitrust|bankrupt\w*|midterms?|election|bans?|barred|exclusion)\b",
    re.I,
)
_MILD_POLICY = re.compile(r"\b(polic(?:y|ies)|legislation|memorandum|\bmou\b)\b", re.I)
_STRONG_SAFETY = re.compile(
    r"\b(jailbreaks?|vulnerabilit\w*|zero-day|cyber ?attacks?|espionage|"
    r"spies|spying|data breach|responsible scaling|insider threats?)\b",
    re.I,
)
_MILD_SAFETY = re.compile(r"\b(safety|security|misuse|bio-?risk)\b", re.I)
_INFRA = re.compile(
    r"\b(vllm|sg ?lang|inference|tensorrt|cuda|blackwell|gpu|chip|accelerator)\b",
    re.I,
)
_LAB_MONEY = re.compile(
    r"\b(?:openai|anthropic)\b.{0,90}(?:\$\s?\d[\d.]*\s?(?:billion|bln)|net loss|\bloss\b|fundraising|\bipo\b)|"
    r"(?:\$\s?\d[\d.]*\s?(?:billion|bln)|net loss|\bloss\b|fundraising|\bipo\b).{0,90}\b(?:openai|anthropic)\b|"
    r"\bnscale\b.{0,80}\b(?:deal|talks|multibillion|\$\s?\d)|"
    r"\b(?:deal|talks|multibillion)\b.{0,80}\bnscale\b",
    re.I,
)
_DEAL = re.compile(
    r"\b(acqui(?:res|red|sition)|merger|funding round|series [a-e]|raises \$|"
    r"raise[sd]?\s+\$|fundraising|valuation|ipo)\b|"
    r"\$\s?\d[\d.]*(?:\s?(?:billion|bln))|\bmultibillion\b",
    re.I,
)
_NAMED_MODEL = re.compile(r"\b(minimax|kimi)\b", re.I)
_AGENTS = re.compile(r"\bagents?\b|\bcomputer use\b|\btool use\b", re.I)
_OPEN_WEIGHTS = re.compile(r"\bopen[- ]weights?\b|\bopen source model\b", re.I)

_OPINION = re.compile(
    r"\b(podcast|newsletter|opinion|essay|commentary)\b|\bthe ai race\b",
    re.I,
)
_MARKET = re.compile(
    r"\b(nasdaq|wall street|stocks?|shares|s&p|dow jones|rout|markets?)\b",
    re.I,
)
_PRERELEASE = re.compile(
    r"(?<![A-Za-z])rc\d*\b|release candidate|pre-release|\bproto-",
    re.I,
)
_OFFICE = re.compile(r"\boffices?\b|\bnew hub\b", re.I)
_TAX = re.compile(r"\b(tax hike|proposition \d+|income tax)\b", re.I)
# A lab or an infrastructure deal. "AI" alone on a marketing acquisition is not one.
_CLEAR_AI_DEAL = re.compile(
    r"\b(openai|anthropic|deepseek|nvidia|mistral|minimax|gpt-\d|claude|"
    r"\bllm\b|gpu|chip|inference|data center|datacenter|neocloud|"
    r"foundation model|open[- ]weight)\b",
    re.I,
)
_GENERIC_DEAL = re.compile(r"\b(acqui\w*|buys|buying|merger|to buy|deal)\b", re.I)
_CUSTOMER = re.compile(
    r"^(?:How\s+)?[A-Z0-9][\w.&'-]*(?:\s+[A-Z0-9][\w.&'-]*){0,5}\s+"
    r"(?i:cuts|saves|uses|partners|brings|runs|deploys|takes|puts)\b"
)

_KIND_BONUS = {"lab": 3, "release": 2, "news": 1, "paper": 0}


_ROUNDUP_HOSTS = (
    "dailyaibrief",
    "therundown",
    "bensbites",
    "thebatch",
    "importai",
)
_LAW_FIRM = re.compile(r"\b(llp|law firm|attorneys at law|client alert)\b", re.I)


def is_low_trust(article) -> bool:
    """Roundups and law-firm blogs lose to a wire or a primary document."""
    host = (article.source_domain or "").lower()
    if any(bit in host for bit in _ROUNDUP_HOSTS):
        return True
    blob = f"{host} {article.source_name or ''} {article.title or ''}"
    return _LAW_FIRM.search(blob) is not None


def score_article(article) -> int:
    title = article.title or ""
    score = _KIND_BONUS.get(article.source_kind, 0)
    has_model = _MODEL.search(title) is not None
    if has_model or _NAMED_MODEL.search(title):
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
        # A frontier-lab round outranks a generic $320 million financing.
        if re.search(r"\b(openai|anthropic)\b", title, re.I):
            score += 3
    # A reported lab loss or a stated mega-deal outranks a serving-stack release.
    if _LAB_MONEY.search(title):
        score += 4
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
    if is_low_trust(article):
        score -= 4
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


def hard_noise(article) -> bool:
    """Customer stories, market blurbs, release candidates, opinion, office news."""
    title = article.title or ""
    # Lab blogs title customer explainers "How Acme is building...".
    if re.match(r"^how\b", title.strip(), re.I):
        return True
    if _CUSTOMER.search(title) or _MARKET.search(title) or _PRERELEASE.search(title):
        return True
    if _OPINION.search(title):
        return True
    if _OFFICE.search(title) and not _STRONG_POLICY.search(title) and not _DEAL.search(title):
        return True
    if _TAX.search(title):
        return True
    if _GENERIC_DEAL.search(title) and not _CLEAR_AI_DEAL.search(title):
        return True
    return False


# Money and rule changes outrank patch notes when the shortlist is tight.
_BUCKET_ORDER = ("money", "policy", "model", "infra", "agents", "other")


def bucket_for(article) -> str:
    """Coarse topic, so a low keyword score cannot crowd out a whole theme."""
    title = article.title or ""
    if re.search(
        r"deepseek|qwen|kimi|moonshot|minimax|gpt-\d|claude \d|gemini \d|llama \d|"
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
    """Pick what a builder would rank first, then fill out the other themes.

    Hard noise is dropped. Keyword score is not a hard cutoff: a quiet
    headline still gets a slot when the high-score items do not fill the list.
    One feed cannot take more than five of the score-ranked slots.
    """
    pool = [article for article in articles if not hard_noise(article)]
    ranked = sorted(
        pool,
        key=lambda article: (score_article(article), article.published_at.timestamp()),
        reverse=True,
    )
    chosen = []
    seen = set()
    per_source = {}

    def add(article, *, cap: int | None) -> bool:
        key = (article.source_name, article.title)
        if key in seen or len(chosen) >= limit:
            return False
        if cap is not None and per_source.get(article.source_name, 0) >= cap:
            return False
        seen.add(key)
        chosen.append(article)
        per_source[article.source_name] = per_source.get(article.source_name, 0) + 1
        return True

    for article in ranked:
        add(article, cap=5)
        if len(chosen) >= limit:
            break
    if len(chosen) < limit:
        buckets = {name: [] for name in _BUCKET_ORDER}
        for article in ranked:
            buckets[bucket_for(article)].append(article)
        while len(chosen) < limit:
            progressed = False
            for name in _BUCKET_ORDER:
                while buckets[name]:
                    article = buckets[name].pop(0)
                    if add(article, cap=None):
                        progressed = True
                        break
                if len(chosen) >= limit:
                    break
            if not progressed:
                break
    return chosen
