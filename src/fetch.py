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


def _parse_date_text(text: str) -> datetime | None:
    """
    Parse a human-readable date string like 'Feb 17, 2026' or 'March 5, 2026'
    into a UTC datetime. Returns None if parsing fails.
    """
    import re
    # Normalize: strip leading/trailing whitespace and compress internal spaces
    text = re.sub(r"\s+", " ", text.strip())
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_article_from_link(link, source_domain: str) -> tuple[str, datetime | None]:
    """
    Given a BeautifulSoup <a> tag, extract (title, published_at).

    Strategy (in order of preference):
    1. Look for <h3> or <h2> inside the link — cleanest title source
    2. Look for a <p> or <span> that is clearly a title (not date/category)
    3. Fall back to get_text() with aggressive cleaning

    For dates:
    1. Look for <time datetime="..."> inside or adjacent to the link
    2. Look for text matching date patterns in spans/divs near the link
    """
    import re

    # --- Title extraction ---
    title = None

    # Priority 1: heading tag inside the link
    for tag in ("h3", "h2", "h1"):
        heading = link.find(tag)
        if heading:
            title = heading.get_text(strip=True)
            break

    # Priority 2: a <p> or <span> that looks like a title (not short, not a date)
    if not title:
        date_pat = re.compile(
            r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|January|February|"
            r"March|April|May|June|July|August|September|October|November|December)"
            r"\s+\d{1,2},?\s+\d{4}$"
        )
        for tag in link.find_all(["p", "span"]):
            txt = tag.get_text(strip=True)
            if len(txt) > 20 and not date_pat.match(txt):
                title = txt[:150]
                break

    # Priority 3: full get_text with cleaning — strip known noise patterns
    if not title:
        raw = link.get_text(separator="\n", strip=True)
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        date_pat = re.compile(
            r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|January|February|"
            r"March|April|May|June|July|August|September|October|November|December)"
            r"\s+\d{1,2},?\s+\d{4}$"
        )
        # Filter out short category labels and date strings
        candidates = [l for l in lines if len(l) > 15 and not date_pat.match(l)]
        if candidates:
            # The actual title is usually the shortest meaningful candidate
            # (description text tends to be longer)
            title = min(candidates, key=len)[:150]
        elif lines:
            title = lines[0][:150]

    # --- Date extraction ---
    published_at = None

    # Look for <time> inside the link
    time_tag = link.find("time")
    if time_tag:
        dt_attr = time_tag.get("datetime", "")
        if dt_attr:
            try:
                published_at = datetime.fromisoformat(dt_attr.rstrip("Z")).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        if not published_at:
            published_at = _parse_date_text(time_tag.get_text(strip=True))

    # Look for date-like text in spans inside the link
    if not published_at:
        date_pat = re.compile(
            r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|January|February|"
            r"March|April|May|June|July|August|September|October|November|December)"
            r"\s+\d{1,2},?\s+\d{4}"
        )
        for tag in link.find_all(["span", "div", "p", "time"]):
            txt = tag.get_text(strip=True)
            m = date_pat.search(txt)
            if m:
                published_at = _parse_date_text(m.group(0))
                if published_at:
                    break

    return title or "", published_at


def fetch_scrape(source_name: str, source_url: str, source_domain: str) -> list:
    try:
        resp = requests.get(
            source_url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ai-news-bot/1.0)"},
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Warning: scrape failed for {source_url}: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    articles = []
    seen_urls = set()
    cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)

    for link in soup.find_all("a", href=True):
        href = link["href"]
        if href.startswith("/"):
            href = f"https://{source_domain}{href}"
        if not href.startswith("http"):
            continue
        if source_domain not in href:
            continue
        if not any(segment in href for segment in ["/news/", "/research/", "/blog/", "/press/"]):
            continue

        title, published_at = _extract_article_from_link(link, source_domain)

        if not title or len(title) < 15:
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)

        # If we got a real date, skip articles older than 7 days
        if published_at and published_at != datetime.fromtimestamp(0, tz=timezone.utc):
            if published_at < cutoff_7d:
                continue
        else:
            # No date found — use epoch so fetch_all's age filter doesn't drop it;
            # state-based deduplication prevents re-posting
            published_at = datetime.fromtimestamp(0, tz=timezone.utc)

        articles.append(Article(
            url=href,
            title=title,
            source_name=source_name,
            source_domain=source_domain,
            published_at=published_at,
            content_preview=title,
        ))

    return articles[:20]


def fetch_all(config: dict, max_age_hours: int) -> list:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    all_sources = config["sources"]["tier1"] + config["sources"]["standard"]
    max_per_source = config.get("max_articles_per_source", 3)
    # Use strict age-filtering only for breaking checks (short windows like 2h).
    # For digests (24h+), newsletters and weekly blogs publish less frequently and
    # their latest article is often 25-72h old — age filtering silences them entirely.
    # Instead, take the N most recent articles per source and let seen_articles dedup
    # handle re-posting prevention.
    use_age_filter = max_age_hours <= 4
    articles = []

    for source in all_sources:
        try:
            if source["type"] == "rss":
                fetched = fetch_rss(source["name"], source["url"], source["domain"])
                if use_age_filter:
                    # Breaking check: strict cutoff so we only alert on truly new items
                    recent = [a for a in fetched if a.published_at >= cutoff]
                else:
                    # Digest: take the N most recent articles regardless of age
                    sorted_fetched = sorted(fetched, key=lambda a: a.published_at, reverse=True)
                    recent = sorted_fetched[:max_per_source]
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
