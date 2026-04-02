from dataclasses import replace
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
