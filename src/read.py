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
        if len(text) >= 40:
            paragraphs.append(text)
    if paragraphs and sum(len(part) for part in paragraphs) >= 80:
        return "\n".join(paragraphs)
    return collapse_ws(node.get_text(" ", strip=True))


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


def read_articles(articles: list, *, exa_api_key: str | None = None, budget=None, getter=None, contents=None) -> None:
    """Fill full_text and details in place. Failures stay on the preview."""
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
            continue
        if not raw:
            continue
        text = extract_html_text(raw) if "<" in raw[:500].lower() else collapse_ws(raw)
        if len(text) > len(article.full_text or ""):
            article.full_text = text[:12000]
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
