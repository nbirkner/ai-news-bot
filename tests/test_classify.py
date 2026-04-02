from datetime import datetime, timezone
import pytest
from src.models import Article
from src.classify import is_breaking, classify_articles


def make_article(url="https://anthropic.com/news/claude-4",
                 title="Claude 4 release",
                 source_domain="anthropic.com",
                 content_preview="Anthropic announces the launch of Claude 4"):
    return Article(
        url=url,
        title=title,
        source_name="Anthropic Blog",
        source_domain=source_domain,
        published_at=datetime.now(timezone.utc),
        content_preview=content_preview,
    )


CONFIG = {
    "tier1_sources": ["anthropic.com", "openai.com", "deepmind.google",
                      "nvidianews.nvidia.com", "ai.meta.com", "together.ai"],
    "breaking_keywords": ["release", "launch", "announce", "introduces",
                          "price cut", "pricing", "acqui", "raises", "funding",
                          "gpu", "h200", "b200", "gb300", "data center"],
}


def test_tier1_source_with_breaking_keyword_is_breaking():
    article = make_article(source_domain="anthropic.com", title="Claude 4 launch")
    assert is_breaking(article, CONFIG) is True


def test_tier1_source_without_keyword_is_not_breaking():
    article = make_article(source_domain="anthropic.com",
                           title="A blog post about culture",
                           content_preview="We care about our team")
    assert is_breaking(article, CONFIG) is False


def test_standard_source_with_keyword_is_not_breaking():
    article = make_article(source_domain="tldr.tech",
                           title="OpenAI announces GPT-6")
    assert is_breaking(article, CONFIG) is False


def test_keyword_match_is_case_insensitive():
    article = make_article(source_domain="openai.com",
                           title="LAUNCH of new model")
    assert is_breaking(article, CONFIG) is True


def test_keyword_in_content_preview_triggers_breaking():
    article = make_article(
        source_domain="together.ai",
        title="Together AI update",
        content_preview="We are raising a new funding round"
    )
    assert is_breaking(article, CONFIG) is True


def test_domain_substring_match_works():
    article = make_article(source_domain="blog.openai.com", title="GPT-5 release")
    assert is_breaking(article, CONFIG) is True


def test_classify_articles_sets_is_breaking_flag():
    articles = [
        make_article(source_domain="anthropic.com", title="Claude 4 launch"),
        make_article(source_domain="tldr.tech", title="Weekly digest"),
    ]
    result = classify_articles(articles, CONFIG)
    assert result[0].is_breaking is True
    assert result[1].is_breaking is False


def test_classify_articles_does_not_mutate_originals():
    article = make_article(source_domain="anthropic.com", title="Claude 4 launch")
    classify_articles([article], CONFIG)
    assert article.is_breaking is False
