"""Convert a Learning-bot news roundup into the weekly sources schema.

The roundup is the markdown file the Learning bot writes at
``ai-news-weekly/YYYY-MM-DD.md``. This module does not fetch pages and
does not call a model. One story that fails the sources schema rejects
the whole file.
"""
import json
import re
from datetime import datetime
from pathlib import Path

from src.models import BriefingItem, Issue
from src.sources_file import validate_sources

NEWS_WEEKLY_DIR = Path("/cursor/stores/bot/ai-news-weekly")

_ITALIC = re.compile(r"^\*(.+)\*$")
_SLACK_LINK = re.compile(r"^<((?:https?)://[^|>\s]+)\|([^>]+)>$")


class IngestError(Exception):
    def __init__(self, reason: str):
        self.reason = (reason or "roundup is invalid").strip()
        super().__init__(self.reason)


def news_weekly_dir() -> Path:
    import os
    raw = (os.environ.get("NEWS_WEEKLY_DIR") or "").strip()
    return Path(raw) if raw else NEWS_WEEKLY_DIR


def roundup_path(date: str, directory: Path | None = None) -> Path:
    return (directory or news_weekly_dir()) / f"{date}.md"


def _sections(text: str) -> list:
    """Blank lines separate the lede from each story. Lines inside a section stay separate."""
    sections = []
    current = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if not line.strip():
            if current:
                sections.append(current)
                current = []
            continue
        current.append(line.strip())
    if current:
        sections.append(current)
    return sections


def _italic(line: str) -> str:
    match = _ITALIC.fullmatch(line.strip())
    if not match:
        return ""
    return " ".join(match.group(1).split())


def _link(line: str) -> tuple:
    match = _SLACK_LINK.fullmatch(line.strip())
    if not match:
        return "", ""
    return match.group(1).strip(), " ".join(match.group(2).split())


def parse_roundup(text: str, date: str) -> dict:
    """Return lede plus source records, or raise IngestError. No partial list."""
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise IngestError("date must be YYYY-MM-DD") from exc
    sections = _sections(text)
    if not sections or not _italic(sections[0][0]):
        raise IngestError("roundup must start with the briefing line")
    lede_lines = [
        line for line in sections[0][1:]
        if not line.startswith("Window:")
    ]
    if not lede_lines:
        raise IngestError("missing the lede")
    lede = [" ".join(line.split()) for line in lede_lines]

    stories = []
    for index, lines in enumerate(sections[1:], start=1):
        title = _italic(lines[0])
        if not title:
            raise IngestError(f"story {index} is missing a headline")
        url, source = _link(lines[-1])
        if not url:
            raise IngestError(f"story {index} is missing a link")
        middle = [" ".join(line.split()) for line in lines[1:-1] if line.strip()]
        if not middle:
            raise IngestError(f"story {index} is missing a summary")
        stories.append({
            "title": title,
            "url": url,
            "source": source,
            "paragraphs": middle,
        })
    if not stories:
        raise IngestError("no stories in the roundup")

    records = []
    for index, story in enumerate(stories, start=1):
        paragraphs = story["paragraphs"]
        if not paragraphs:
            raise IngestError(f"story {index} is missing a summary")
        record = {
            "title": story["title"],
            "url": story["url"],
            "source": story["source"],
            "published": date,
            "summary": " ".join(paragraphs[:-1]) if len(paragraphs) > 1 else paragraphs[0],
        }
        if len(paragraphs) > 1 and paragraphs[-1]:
            record["reflection"] = paragraphs[-1]
        records.append(record)
    reason = validate_sources(records)
    if reason:
        raise IngestError(reason)
    return {
        "date": date,
        "lede": " ".join(lede),
        "sources": records,
    }


def sources_json(records: list) -> str:
    reason = validate_sources(records)
    if reason:
        raise IngestError(reason)
    return json.dumps(records, indent=2, ensure_ascii=False) + "\n"


def issue_from_roundup(parsed: dict) -> Issue:
    """The roundup text is the issue. Nothing is rewritten."""
    day = datetime.strptime(parsed["date"], "%Y-%m-%d")
    items = []
    for record in parsed["sources"]:
        items.append(BriefingItem(
            what_happened=record["title"],
            body=record["summary"],
            why_it_matters=record.get("reflection") or "",
            url=record["url"],
            source_name=record["source"],
        ))
    return Issue(
        title=f"AI briefing, {day.strftime('%B')} {day.day}",
        date=parsed["date"],
        summary=parsed["lede"],
        tags=["ai"],
        items=items,
    )


def load_roundup(path: Path, date: str) -> dict:
    path = Path(path)
    if not path.is_file():
        raise IngestError("roundup file is missing")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise IngestError("roundup file could not be read") from exc
    return parse_roundup(text, date)
