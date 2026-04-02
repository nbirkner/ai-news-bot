from dataclasses import replace
from datetime import datetime, timezone, timedelta
from src.models import Article


def is_breaking(article: Article, config: dict) -> bool:
    domain = article.source_domain.lower()
    tier1_domains = [d.lower() for d in config["tier1_sources"]]
    is_tier1 = any(t1 in domain for t1 in tier1_domains)

    if not is_tier1:
        return False

    text = (article.title + " " + article.content_preview).lower()
    keywords = [k.lower() for k in config["breaking_keywords"]]
    return any(kw in text for kw in keywords)


def classify_articles(articles: list, config: dict) -> list:
    result = []
    for article in articles:
        classified = replace(article, is_breaking=is_breaking(article, config))
        result.append(classified)
    return result


def _priority_score(article: Article, config: dict) -> int:
    """
    Score an article for digest ordering. Higher = more important.

    Points:
      +4  relevant_to_together (set by summarizer after API call)
      +3  tier-1 source (Anthropic, OpenAI, DeepMind, NVIDIA, Meta, Together)
      +2  breaking keyword match (signal of significance regardless of source)
      +1  published within the last 24h (freshness bonus)
    """
    score = 0
    if article.relevant_to_together:
        score += 4
    domain = article.source_domain.lower()
    tier1_domains = [d.lower() for d in config["tier1_sources"]]
    if any(t1 in domain for t1 in tier1_domains):
        score += 3
    text = (article.title + " " + article.content_preview).lower()
    keywords = [k.lower() for k in config["breaking_keywords"]]
    if any(kw in text for kw in keywords):
        score += 2
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    if article.published_at >= cutoff_24h:
        score += 1
    return score


def prioritize_articles(articles: list, config: dict, top_n: int = 7) -> list:
    """Return the top_n articles sorted by priority score (highest first)."""
    scored = sorted(articles, key=lambda a: _priority_score(a, config), reverse=True)
    return scored[:top_n]
