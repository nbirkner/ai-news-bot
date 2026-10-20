"""Full text and concrete details for the shortlist.

Direct fetch first. Exa contents covers thin pages and hosts that often
block a plain GET. Extraction is regex-only so the discovery dry run
can show prices, dates, and caveats without calling a model.
"""
import re
import sys
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src.clean import collapse_ws
from src.exa_search import fetch_contents
from src.fetch import DATE_RE, http_get

_PRICE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s?(?:billion|million|bn)\b",
    re.I,
)
_PER_TOKEN = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s*(?:/|per)\s*(?:1\s?m|million|1,?000)\s*tokens",
    re.I,
)
_BENCH = re.compile(
    r"\b(MMLU(?:-Pro)?|GPQA|SWE-bench|HumanEval|LiveCodeBench|AIME|MATH|"
    r"Terminal-Bench|HLE)\b[^.]{0,90}",
    re.I,
)
_AVAIL = re.compile(
    r"\b(generally available|public preview|research preview|waitlist|"
    r"open[- ]weights|API access|now available|weights are (?:on|available))\b",
    re.I,
)
_CAVEAT = re.compile(
    r"\b(company-reported|self-reported|internally measured|"
    r"not independently audited|unaudited|preliminary|"
    r"our (?:internal )?evaluation|vendor[- ]reported)\b",
    re.I,
)
_ISO = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
_PAYWALL = (
    "reuters.com",
    "wsj.com",
    "ft.com",
    "bloomberg.com",
    "nytimes.com",
    "politico.com",
)
# Lab and company posts often 403 a bot. A reader proxy is the fallback.
_READER_HOSTS = (
    "openai.com",
    "minimax.io",
    "engineering.fb.com",
    "microsoft.com",
    "axios.com",
    "investing.com",
    "reuters.com",
)
# Pages that regularly refuse a bot. A wire syndication is the next read.
_BLOCKED_HOSTS = (
    "reuters.com",
    "investing.com",
    "openai.com",
    "axios.com",
)
_SYNDICATION_HOSTS = (
    "finance.yahoo.com",
    "news.yahoo.com",
    "yahoo.com",
    "pymnts.com",
    "marketscreener.com",
)


_CHROME = re.compile(
    r"\b(skip navigation|skip to (?:main )?content|watch now|livestream menu|"
    r"investing club|all rights reserved|sign up for our)\b",
    re.I,
)


def _without_chrome(text: str) -> str:
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", collapse_ws(text or "")):
        if sentence and not _CHROME.search(sentence):
            kept.append(sentence)
    return " ".join(kept)


def extract_html_text(raw: str) -> str:
    """Prefer the article body over nav chrome such as 'Skip Navigation $2'."""
    soup = BeautifulSoup(raw or "", "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript", "svg", "header", "form", "aside"]):
        tag.decompose()
    preferred = None
    for class_name in ("markdown-body", "article-body", "post-content", "entry-content", "article__content"):
        preferred = soup.find(class_=re.compile(class_name))
        if preferred is not None:
            break
    if preferred is None:
        preferred = soup.find(attrs={"itemprop": "articleBody"})
    node = preferred or soup.find("article") or soup.find("main") or soup.body or soup
    paragraphs = []
    for tag in node.find_all(["p", "li", "h1", "h2", "h3", "pre"]):
        text = collapse_ws(tag.get_text(" ", strip=True))
        if len(text) >= 40 and not _CHROME.search(text):
            paragraphs.append(text)
    if paragraphs and sum(len(part) for part in paragraphs) >= 80:
        return "\n".join(paragraphs)
    return _without_chrome(node.get_text(" ", strip=True))


def extract_details(text: str) -> dict:
    blob = text or ""
    return {
        "prices": _unique(_PRICE.findall(blob) + _PER_TOKEN.findall(blob)),
        "benchmarks": _unique(match.group(0) for match in _BENCH.finditer(blob)),
        "dates": _unique(
            [match.group(0) for match in DATE_RE.finditer(blob)] + _ISO.findall(blob)
        ),
        "availability": _unique(_AVAIL.findall(blob)),
        "caveats": _unique(_CAVEAT.findall(blob)),
    }


def format_details(details: dict) -> str:
    parts = []
    for label, key in (
        ("Prices", "prices"),
        ("Benchmarks", "benchmarks"),
        ("Dates", "dates"),
        ("Availability", "availability"),
        ("Caveats", "caveats"),
    ):
        values = details.get(key) or []
        if values:
            parts.append(label + ": " + "; ".join(values[:6]))
    return " | ".join(parts)


def _unique(values) -> list:
    seen = set()
    out = []
    for value in values:
        cleaned = collapse_ws(str(value))
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _paywalled(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == bit or host.endswith("." + bit) for bit in _PAYWALL)


def _huggingface_readme(url: str) -> str:
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    if len(parts) < 2:
        return ""
    return f"https://huggingface.co/{parts[0]}/{parts[1]}/raw/main/README.md"


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _blocked_host(url: str) -> bool:
    host = _host(url)
    return any(host == bit or host.endswith("." + bit) for bit in _BLOCKED_HOSTS)


def _syndication_label(url: str) -> str:
    host = _host(url)
    if "yahoo" in host:
        return "Yahoo Finance"
    if "pymnts" in host:
        return "PYMNTS"
    if "marketscreener" in host:
        return "MarketScreener"
    return host.split(".")[0] or "Web"


def _syndication_host(url: str) -> bool:
    host = _host(url)
    return any(host == bit or host.endswith("." + bit) for bit in _SYNDICATION_HOSTS)


def syndication_text(title: str, original_url: str = "") -> tuple:
    """Readable wire copy when the primary page returns 403. (text, url)."""
    from src.fetch import decode_google_news_url
    cleaned = " ".join((title or "").split())
    if len(cleaned) < 12:
        return "", ""
    query = (
        f"\"{cleaned}\" (site:finance.yahoo.com OR site:news.yahoo.com "
        "OR site:pymnts.com OR site:marketscreener.com)"
    )
    try:
        raw = http_get(
            "https://news.google.com/rss/search",
            params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
        ).content
    except Exception as exc:
        print(f"Warning: syndication search failed: {exc}", file=sys.stderr)
        return "", ""
    import feedparser
    feed = feedparser.parse(raw)
    origin = _host(original_url)
    for entry in feed.entries[:4]:
        link = entry.get("link") or ""
        if "news.google.com" not in link:
            continue
        try:
            url = decode_google_news_url(link)
        except Exception as exc:
            print(f"Warning: syndication decode failed: {exc}", file=sys.stderr)
            continue
        if not url or not _syndication_host(url) or _host(url) == origin:
            continue
        try:
            page = http_get(url).text
        except Exception as exc:
            print(f"Warning: syndication fetch failed for {url}: {exc}", file=sys.stderr)
            continue
        text = extract_html_text(page)
        if len(text) >= 400:
            return text[:12000], url
    return "", ""


def _cache_text(url: str) -> str:
    """Google's cache of a page that returned 403. A shell page is not an article."""
    try:
        raw = http_get(
            "https://webcache.googleusercontent.com/search",
            params={"q": "cache:" + url},
        ).text
    except Exception as exc:
        print(f"Warning: cache fallback failed for {url}: {exc}", file=sys.stderr)
        return ""
    text = extract_html_text(raw)
    head = text[:600].lower()
    if "before you continue" in head or "enable javascript" in head:
        return ""
    if len(text) < 400:
        return ""
    return text[:12000]


def _reader_text(url: str) -> str:
    """Fallback when a lab page returns 403 to a plain GET."""
    try:
        raw = http_get("https://r.jina.ai/" + url).text
    except Exception as exc:
        print(f"Warning: reader fallback failed for {url}: {exc}", file=sys.stderr)
        return ""
    if "Markdown Content:" in raw:
        raw = raw.split("Markdown Content:", 1)[1]
    if "captcha" in raw[:400].lower():
        return ""
    return collapse_ws(raw)


def read_articles(articles: list, *, exa_api_key: str | None = None, budget=None, getter=None, contents=None) -> None:
    """Fill full_text and details in place. Failures stay on the preview."""
    live_fetch = getter is None
    getter = getter or (lambda url: http_get(url).text)
    contents = contents or fetch_contents
    for article in articles:
        if len(article.full_text or "") >= 400:
            continue
        if _paywalled(article.url):
            continue
        target = article.url
        if "huggingface.co" in urlparse(article.url).netloc.lower():
            readme = _huggingface_readme(article.url)
            target = readme or article.url
        try:
            raw = getter(target)
        except Exception as exc:
            print(f"Warning: full text fetch failed for {article.url}: {exc}", file=sys.stderr)
            if live_fetch and _blocked_host(article.url) and ("403" in str(exc) or "401" in str(exc)):
                extra, via = syndication_text(article.title, article.url)
                if len(extra) > len(article.full_text or ""):
                    article.full_text = extra
                    if via:
                        article.url = via
                        article.source_domain = _host(via)
                        article.source_name = _syndication_label(via)
                else:
                    cached = _cache_text(article.url)
                    preview = article.content_preview or ""
                    if len(cached) > len(article.full_text or ""):
                        article.full_text = cached
                    elif len(preview) > len(article.full_text or ""):
                        article.full_text = preview
            continue
        if not raw:
            continue
        text = extract_html_text(raw) if "<" in raw[:500].lower() else collapse_ws(raw)
        if len(text) > len(article.full_text or ""):
            article.full_text = text[:12000]
    for article in articles:
        if len(article.full_text or "") >= 400:
            continue
        host = urlparse(article.url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if not any(host == bit or host.endswith("." + bit) for bit in _READER_HOSTS):
            continue
        extra = _reader_text(article.url)
        if len(extra) > len(article.full_text or ""):
            article.full_text = extra[:12000]
    thin = [article for article in articles if len(article.full_text or "") < 400]
    if thin and exa_api_key and budget is not None:
        mapping = contents(exa_api_key, [article.url for article in thin], budget)
        for article in thin:
            extra = mapping.get(article.url) or ""
            if len(extra) > len(article.full_text or ""):
                article.full_text = collapse_ws(extra)[:12000]
    for article in articles:
        blob = "\n".join([
            article.title or "",
            article.full_text or "",
            article.content_preview or "",
        ])
        article.details = format_details(extract_details(blob))
        if article.full_text and len(article.content_preview or "") < 40:
            article.content_preview = article.full_text[:500]
