import sys
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
import feedparser
import requests
from bs4 import BeautifulSoup
from src.models import Article


def _extract_domain(url: str) -> str:
    return urlparse(url).netloc.lstrip("www.")


def _parse_datetime(entry) -> datetime:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def fetch_rss(source_name: str, source_url: str, source_domain: str) -> list:
    feed = feedparser.parse(source_url)
    articles = []
    for entry in feed.entries:
        content = ""
        if hasattr(entry, "summary"):
            content = entry.summary
        elif hasattr(entry, "description"):
            content = entry.description
        soup = BeautifulSoup(content, "html.parser")
        content_preview = soup.get_text(separator=" ", strip=True)[:500]

        articles.append(Article(
            url=entry.get("link", ""),
            title=entry.get("title", ""),
            source_name=source_name,
            source_domain=source_domain,
            published_at=_parse_datetime(entry),
            content_preview=content_preview,
        ))
    return [a for a in articles if a.url and a.title]


def _clean_scraped_title(raw_text: str) -> str:
    """
    Clean up link text that may contain category + date + title + description
    concatenated together (common on Anthropic-style news pages).
    Heuristic: split on sentence-ending punctuation or newlines, take the
    longest non-date, non-category fragment under 120 chars.
    """
    import re
    # Split on newlines first
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    # Remove lines that look like dates (e.g. "Feb 17, 2026") or short category labels
    date_pattern = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}$")
    cleaned = [l for l in lines if not date_pattern.match(l) and len(l) > 10]
    if not cleaned:
        # Fallback: just take first 120 chars of raw
        return raw_text[:120].strip()
    # Prefer the longest line that's not suspiciously short
    best = max(cleaned, key=len)
    return best[:120]


def fetch_scrape(source_name: str, source_url: str, source_domain: str) -> list:
    try:
        resp = requests.get(
            source_url,
            timeout=15,
            headers={"User-Agent": "ai-news-bot/1.0 (internal tool)"},
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Warning: scrape failed for {source_url}: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    articles = []
    seen_urls = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]
        if href.startswith("/"):
            href = f"https://{source_domain}{href}"
        if not href.startswith("http"):
            continue
        if source_domain not in href:
            continue
        if any(segment in href for segment in ["/news/", "/research/", "/blog/", "/press/"]):
            raw_text = link.get_text(strip=True)
            title = _clean_scraped_title(raw_text)
            if len(title) > 15 and href not in seen_urls:
                seen_urls.add(href)
                # Scraped articles have no reliable publish date — use epoch so
                # age filtering doesn't apply; deduplication via seen_articles handles recurrence
                articles.append(Article(
                    url=href,
                    title=title,
                    source_name=source_name,
                    source_domain=source_domain,
                    published_at=datetime.fromtimestamp(0, tz=timezone.utc),
                    content_preview=title,
                ))

    return articles[:20]


def fetch_all(config: dict, max_age_hours: int) -> list:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    all_sources = config["sources"]["tier1"] + config["sources"]["standard"]
    articles = []

    for source in all_sources:
        try:
            if source["type"] == "rss":
                fetched = fetch_rss(source["name"], source["url"], source["domain"])
                # Age-filter RSS articles (they have real publish dates)
                recent = [a for a in fetched if a.published_at >= cutoff]
            else:
                fetched = fetch_scrape(source["name"], source["url"], source["domain"])
                # Skip age-filter for scraped articles (no reliable date) — dedup handles recurrence
                recent = fetched

            articles.extend(recent)
            print(f"Fetched {len(recent)} articles from {source['name']}", file=sys.stderr)

        except Exception as e:
            print(f"Warning: failed to fetch {source['name']}: {e}", file=sys.stderr)

    seen = set()
    unique = []
    for a in articles:
        if a.url not in seen:
            seen.add(a.url)
            unique.append(a)

    return unique
