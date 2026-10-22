"""Weekly committed sources for the Monday newsletter.

The newsletter run looks for ``sources/<YYYY-MM-DD>.json``, where the date
is the window end on the Pacific calendar (Monday 9 AM PT is that Monday).

The file is a JSON list. Each item is an object:

- ``title`` (required): non-empty string
- ``url`` (required): absolute ``http`` or ``https`` URL
- ``source`` (required): non-empty string, the publication name
- ``published`` (required): ISO-8601 date (``YYYY-MM-DD``) or date-time
  (``YYYY-MM-DDThh:mm:ssZ``, or the same with a numeric offset)
- ``summary`` (required): non-empty string of public facts
- ``reflection`` (optional): string. Omit it when there is no note.

Any other field is ignored. One invalid item rejects the whole file.
A missing or invalid file means RSS only. An empty list is valid.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
import json

from src.fetch import parse_iso
from src.models import Article

PACIFIC = ZoneInfo("America/Los_Angeles")

REQUIRED_FIELDS = ("title", "url", "source", "published", "summary")
OPTIONAL_FIELDS = ("reflection",)

# The file is a JSON list of these objects. `reflection` may be omitted.
SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": True,
        "required": list(REQUIRED_FIELDS),
        "properties": {
            "title": {"type": "string", "minLength": 1},
            "url": {
                "type": "string",
                "format": "uri",
                "pattern": "^https?://",
            },
            "source": {"type": "string", "minLength": 1},
            "published": {
                "type": "string",
                "description": (
                    "ISO-8601 date (YYYY-MM-DD) or date-time "
                    "(YYYY-MM-DDThh:mm:ssZ or a numeric offset)"
                ),
            },
            "summary": {"type": "string", "minLength": 1},
            "reflection": {"type": "string"},
        },
    },
}


@dataclass
class SourcesLoad:
    status: str  # "ok", "missing", or "invalid"
    articles: list = field(default_factory=list)
    error: str = ""
    path: Path | None = None


def window_end_filename_date(until: datetime) -> str:
    """Pacific calendar date of the window end. That date is the filename."""
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until.astimezone(PACIFIC).date().isoformat()


def sources_path_for(until: datetime, root: Path | str | None = None) -> Path:
    directory = Path(root) if root is not None else Path("sources")
    return directory / f"{window_end_filename_date(until)}.json"


def _nonempty(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text


def _valid_url(value) -> str | None:
    text = _nonempty(value)
    if text is None or any(char.isspace() for char in text):
        return None
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return text


def _item_error(item) -> str:
    if not isinstance(item, dict):
        return "must be an object"
    for key in REQUIRED_FIELDS:
        if key not in item:
            return f"missing {key}"
    if _nonempty(item.get("title")) is None:
        return "title must be a non-empty string"
    if _valid_url(item.get("url")) is None:
        return "url must be an http or https URL"
    if _nonempty(item.get("source")) is None:
        return "source must be a non-empty string"
    published = item.get("published")
    if not isinstance(published, str) or parse_iso(published.strip()) is None:
        return "published must be an ISO-8601 date or date-time"
    if _nonempty(item.get("summary")) is None:
        return "summary must be a non-empty string"
    if "reflection" in item and not isinstance(item.get("reflection"), str):
        return "reflection must be a string"
    return ""


def validate_sources(payload) -> str:
    """Empty string when `payload` matches the schema, otherwise the reason."""
    if not isinstance(payload, list):
        return "must be a JSON list"
    for index, item in enumerate(payload):
        reason = _item_error(item)
        if reason:
            return f"item {index} {reason}"
    return ""


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def article_from_source(item: dict) -> Article:
    url = _valid_url(item["url"]) or ""
    published = parse_iso(item["published"].strip())
    summary = " ".join(item["summary"].split())
    reflection = item.get("reflection")
    if not isinstance(reflection, str):
        reflection = ""
    reflection = " ".join(reflection.split())
    article = Article(
        url=url,
        title=" ".join(item["title"].split()),
        source_name=item["source"].strip(),
        source_domain=_host(url) or "example.com",
        published_at=published,
        content_preview=summary[:500],
        full_text=summary[:12000],
        source_kind="news",
        source_priority=1,
        reflection=reflection,
    )
    # The weekly file already chose this story for the window.
    article.from_sources_file = True
    return article


def load_sources_file(path: Path | str) -> SourcesLoad:
    """Read and validate a weekly sources file. Never raises for a bad file."""
    path = Path(path)
    if not path.exists():
        return SourcesLoad(status="missing", error="missing", path=path)
    if not path.is_file():
        return SourcesLoad(status="invalid", error="not a file", path=path)
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeError:
        return SourcesLoad(status="invalid", error="not valid JSON", path=path)
    except OSError as exc:
        return SourcesLoad(status="invalid", error=f"could not read file ({exc})", path=path)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return SourcesLoad(status="invalid", error="not valid JSON", path=path)
    reason = validate_sources(payload)
    if reason:
        return SourcesLoad(status="invalid", error=reason, path=path)
    articles = [article_from_source(item) for item in payload]
    return SourcesLoad(status="ok", articles=articles, path=path)
