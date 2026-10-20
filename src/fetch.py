import html
import re
import sys
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import feedparser
import requests
from bs4 import BeautifulSoup

from src.clean import collapse_ws, is_thin_preview, shares_distinctive_word
from src.models import Article, FetchReport

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ai-news-bot/1.0; +https://github.com/nbirkner/ai-news-bot)",
    "Accept": "text/html,application/xml,application/rss+xml,application/atom+xml,*/*",
}

AI_TERMS = re.compile(
    r"\b(ai|a\.i\.|artificial intelligence|openai|anthropic|deepmind|gemini|"
    r"chatgpt|gpt-\d|claude|llms?|machine learning|nvidia|gpu|mistral|"
    r"vllm|sglang|deepseek|qwen|kimi|moonshot|tenstorrent|huggingface|"
    r"data centers?|datacenters?|chips?|semiconductor|inference)\b",
    re.I,
)
SKIP_REUTERS_PATH = re.compile(
    r"reuters\.com/(pt|es|fr|de|it|ja|jp|ar|zh|br|latam|cn|mx|podcasts)/",
    re.I,
)
SPANISH_HINT = re.compile(
    r"\b(del|los|las|que|para|con|una|unos|alegando|estados|sobre|como)\b",
    re.I,
)
DATE_RE = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+\d{4}\b",
    re.I,
)
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_SKIP_TITLES = {
    "learn more", "featured", "next", "blog", "read more", "news", "press",
}


def http_get(url: str, params: dict | None = None) -> requests.Response:
    response = requests.get(url, headers=HEADERS, params=params, timeout=20)
    response.raise_for_status()
    return response


def parse_human_date(text: str) -> datetime | None:
    match = DATE_RE.search(collapse_ws(text or ""))
    if not match:
        return None
    parts = match.group(0).replace(",", "").split()
    if len(parts) != 3:
        return None
    month = _MONTHS.get(parts[0].lower())
    if not month:
        return None
    try:
        return datetime(int(parts[2]), month, int(parts[1]), tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _entry_datetime(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    return None


def _entry_link(entry) -> str:
    link = entry.get("link") or ""
    if link:
        return link
    for item in entry.get("links") or []:
        href = item.get("href")
        if href:
            return href
    return ""


def _plain(raw: str) -> str:
    text = raw or ""
    if "<" in text or "&" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    return collapse_ws(html.unescape(text))


def _article(source: dict, url: str, title: str, published: datetime, preview: str = "") -> Article:
    title = _plain(title)
    if source.get("kind") == "release" and source["name"].lower() not in title.lower():
        title = f"{source['name']} {title}".strip()
    return Article(
        url=url,
        title=title,
        source_name=source["name"],
        source_domain=source["domain"],
        published_at=published,
        content_preview=_plain(preview)[:500],
        source_kind=source.get("kind", "news"),
        source_priority=int(source.get("priority", 5)),
    )


def _mostly_english(title: str) -> bool:
    if len(SPANISH_HINT.findall(title)) >= 2:
        return False
    letters = [char for char in title if char.isalpha()]
    if not letters:
        return True
    non_ascii = sum(1 for char in letters if not char.isascii())
    return non_ascii / len(letters) <= 0.08


def articles_from_feed(source: dict, content: bytes) -> list:
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"not a feed ({getattr(feed, 'bozo_exception', 'parse error')})")
    require_ai = bool(source.get("require_ai"))
    articles = []
    seen = set()
    for entry in feed.entries:
        title = _plain(entry.get("title") or "")
        link = _entry_link(entry)
        published = _entry_datetime(entry)
        if not title or not link or published is None:
            continue
        preview = ""
        if getattr(entry, "summary", None):
            preview = entry.summary
        elif getattr(entry, "description", None):
            preview = entry.description
        preview = _plain(preview)
        if require_ai and not AI_TERMS.search(f"{title} {preview}"):
            continue
        if "arxiv.org" in link:
            match = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", link)
            if match:
                link = f"https://arxiv.org/abs/{match.group(1)}"
        if link in seen:
            continue
        seen.add(link)
        articles.append(_article(source, link, title, published, preview))
    return articles


def fetch_rss(source: dict) -> list:
    response = http_get(source["url"])
    return articles_from_feed(source, response.content)


def fetch_arxiv(source: dict) -> list:
    response = http_get(
        source["url"],
        params={
            "search_query": source["query"],
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": int(source.get("max_results", 8)),
        },
    )
    return articles_from_feed(source, response.content)


def parse_reuters_sitemap(source: dict, content: bytes) -> list:
    root = ElementTree.fromstring(content)
    articles = []
    seen = set()
    for node in root.iter():
        if _local(node.tag) != "url":
            continue
        loc = ""
        title = ""
        published = None
        language = "en"
        for child in node.iter():
            name = _local(child.tag)
            if name == "loc" and not loc:
                loc = (child.text or "").strip()
            elif name == "title" and child.text and not title:
                title = collapse_ws(child.text)
            elif name == "publication_date" and child.text and published is None:
                published = parse_iso(child.text)
            elif name == "language" and child.text:
                language = child.text.strip().lower()
        if not loc or not title or published is None:
            continue
        if language and not language.startswith("en"):
            continue
        if SKIP_REUTERS_PATH.search(loc):
            continue
        if not _mostly_english(title):
            continue
        if not AI_TERMS.search(title):
            continue
        if loc in seen:
            continue
        seen.add(loc)
        articles.append(_article(source, loc, title, published, ""))
    return articles


def fetch_reuters(source: dict) -> list:
    response = http_get(source["url"])
    return parse_reuters_sitemap(source, response.content)


def parse_anthropic_html(source: dict, html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    seen = set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "/news/" not in href:
            continue
        url = urljoin("https://www.anthropic.com", href)
        path = urlparse(url).path.rstrip("/")
        if path in {"", "/news"} or url in seen:
            continue
        time_tag = link.find("time")
        if not time_tag:
            continue
        published = parse_human_date(time_tag.get_text(" ", strip=True))
        if published is None:
            continue
        title = ""
        for span in link.find_all("span"):
            classes = " ".join(span.get("class") or [])
            if "title" in classes.split() or classes.endswith("title") or "__title" in classes:
                title = _plain(span.get_text(" ", strip=True))
                break
        if len(title) < 15:
            continue
        seen.add(url)
        articles.append(_article(source, url, title, published, title))
    return articles


def _date_near(tag) -> datetime | None:
    node = tag
    for _ in range(5):
        node = getattr(node, "parent", None)
        if node is None:
            break
        blob = collapse_ws(node.get_text(" ", strip=True))
        if len(blob) > 350:
            break
        published = parse_human_date(blob)
        if published is not None:
            return published
    return None


def parse_meta_html(source: dict, html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "/blog/" not in href:
            continue
        title = _plain(link.get_text(" ", strip=True))
        if len(title) < 20 or title.lower() in _SKIP_TITLES:
            continue
        published = _date_near(link)
        if published is None:
            continue
        url = urljoin(source["url"], href)
        current = found.get(url)
        if current and len(current.title) >= len(title):
            continue
        found[url] = _article(source, url, title, published, title)
    return list(found.values())


_HTML_PARSERS = {
    "anthropic": parse_anthropic_html,
    "meta": parse_meta_html,
}


_HF_TEXT = {
    "text-generation",
    "image-text-to-text",
    "any-to-any",
    "text2text-generation",
}


def _hf_text_model(row: dict) -> bool:
    if (row.get("pipeline_tag") or "") in _HF_TEXT:
        return True
    tags = " ".join(row.get("tags") or []).lower()
    return "text-generation" in tags or "image-text-to-text" in tags


def parse_huggingface(source: dict, payload) -> list:
    """Turn a Hugging Face /api/models payload into articles. Dates are createdAt."""
    if isinstance(payload, dict):
        payload = payload.get("models") or payload.get("items") or []
    if not isinstance(payload, list):
        return []
    articles = []
    seen = set()
    trending = bool(source.get("trending"))
    for row in payload:
        if not isinstance(row, dict) or row.get("private"):
            continue
        model_id = (row.get("id") or row.get("modelId") or "").strip()
        if not model_id or model_id in seen:
            continue
        if trending and not _hf_text_model(row):
            continue
        published = parse_iso(row.get("createdAt") or "")
        if published is None:
            continue
        seen.add(model_id)
        name = model_id.split("/", 1)[-1]
        pipeline = row.get("pipeline_tag") or "model"
        preview = f"{name} on Hugging Face. Pipeline: {pipeline}. Created {row.get('createdAt')}."
        articles.append(_article(
            source,
            f"https://huggingface.co/{model_id}",
            name,
            published,
            preview,
        ))
    return articles


def fetch_huggingface(source: dict) -> list:
    params = {
        "limit": int(source.get("max_results", 8)),
        "sort": source.get("sort") or "createdAt",
        "direction": -1,
    }
    author = (source.get("author") or "").strip()
    if author:
        params["author"] = author
    response = http_get(source["url"], params=params)
    return parse_huggingface(source, response.json())


def fetch_html(source: dict) -> list:
    parser = _HTML_PARSERS.get(source.get("parser", ""))
    if parser is None:
        raise ValueError(f"no HTML parser configured for {source.get('name')}")
    response = http_get(source["url"])
    return parser(source, response.text)


_FETCHERS = {
    "rss": fetch_rss,
    "reuters": fetch_reuters,
    "arxiv": fetch_arxiv,
    "html": fetch_html,
    "huggingface": fetch_huggingface,
}


def in_window(published: datetime, since: datetime, now: datetime) -> bool:
    if published is None:
        return False
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return since <= published <= now + timedelta(hours=6)


def fetch_all(config: dict, since: datetime, now: datetime | None = None) -> FetchReport:
    now = now or datetime.now(timezone.utc)
    max_per_source = int(config.get("max_per_source", 8))
    articles = []
    failures = []
    successes = 0
    for source in config["sources"]:
        fetcher = _FETCHERS.get(source.get("type", ""))
        if fetcher is None:
            failures.append(f"{source.get('name', '?')}: unknown type {source.get('type')}")
            continue
        try:
            fetched = fetcher(source)
            successes += 1
        except Exception as exc:
            failures.append(f"{source['name']}: {exc}")
            print(f"Warning: {source['name']} failed: {exc}", file=sys.stderr)
            continue
        fresh = [item for item in fetched if in_window(item.published_at, since, now)]
        fresh.sort(key=lambda item: item.published_at, reverse=True)
        kept = fresh[:max_per_source]
        print(
            f"{source['name']}: {len(kept)} in window ({len(fetched)} fetched)",
            file=sys.stderr,
        )
        articles.extend(kept)
    return FetchReport(articles=articles, failures=failures, successes=successes)


def meta_description(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for attrs in (
        {"property": "og:description"},
        {"name": "description"},
        {"name": "twitter:description"},
    ):
        tag = soup.find("meta", attrs=attrs)
        content = (tag.get("content") if tag else "") or ""
        text = _plain(content)
        if len(text) >= 40 and text.lower() not in {"home", "blog", "news"}:
            return text[:500]
    return ""


def enrich_previews(articles: list, getter=None) -> None:
    """Fill empty excerpts from the page's own description. Fail soft."""
    getter = getter or (lambda url: http_get(url).text)
    for article in articles:
        if not is_thin_preview(article.content_preview, article.title):
            continue
        host = urlparse(article.url).netloc.lower()
        if host.endswith("reuters.com"):
            continue
        try:
            description = meta_description(getter(article.url))
        except Exception as exc:
            print(f"Warning: preview fetch failed for {article.url}: {exc}", file=sys.stderr)
            continue
        if description and shares_distinctive_word(description, article.title):
            article.content_preview = description


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
