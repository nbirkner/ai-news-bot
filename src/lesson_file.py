"""Read one Learning-bot lesson file. No model call and no network.

Files live in ``ai-lessons-weekly/YYYY-MM-DD-slug.md`` and use the public
lesson frontmatter: title, date, level, slug, summary. ``level`` is
Foundations, Systems, or Frontier. A missing or invalid file fails closed.
"""
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import yaml

from src.newsletter import _yaml_quote

LESSON_WEEKLY_DIR = Path("/cursor/stores/bot/ai-lessons-weekly")
PACIFIC = ZoneInfo("America/Los_Angeles")
LEVELS = ("Foundations", "Systems", "Frontier")
WEB_ROOT = "https://nikibirkner.com/ai-lessons"
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){1,4}$")
_FILENAME = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+){1,4})\.md$"
)
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_HEADINGS = (
    ("how it works", re.compile(r"^##\s+.*(how it works|the idea)\b", re.I | re.M)),
    ("the numbers", re.compile(r"^##\s+.*(numbers|evidence)\b", re.I | re.M)),
    ("an analogy", re.compile(r"^##\s+an analogy\b", re.I | re.M)),
    ("try this", re.compile(r"^##\s+try this\s*$", re.I | re.M)),
    ("use it at dinner", re.compile(r"^##\s+use it at dinner\s*$", re.I | re.M)),
    ("sources", re.compile(r"^##\s+sources\s*$", re.I | re.M)),
)
_PRIVATE_HOST = (
    "notion.so",
    "notion.site",
    "slack.com",
    "docs.google.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
)


class LessonBlocked(Exception):
    def __init__(self, reason: str):
        self.reason = (reason or "lesson file is invalid").strip()
        super().__init__(self.reason)

    @property
    def line(self) -> str:
        return f"BLOCKED: {self.reason}"


class LessonFile:
    def __init__(self, title, date, level, slug, summary, body):
        self.title = title
        self.date = date
        self.level = level
        self.slug = slug
        self.summary = summary
        self.body = body

    @property
    def web_url(self) -> str:
        return f"{WEB_ROOT}/{self.date}-{self.slug}"


def lesson_weekly_dir() -> Path:
    raw = (os.environ.get("LESSON_WEEKLY_DIR") or "").strip()
    return Path(raw) if raw else LESSON_WEEKLY_DIR


def pacific_today(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(PACIFIC).date().isoformat()


def _as_date(value) -> str:
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value.isoformat()[:10]
    return str(value or "").strip()


def _split_frontmatter(text: str) -> tuple:
    cleaned = (text or "").lstrip("\ufeff").strip()
    if not cleaned.startswith("---"):
        raise LessonBlocked("missing frontmatter")
    parts = cleaned.split("---", 2)
    if len(parts) < 3 or not parts[2].strip():
        raise LessonBlocked("frontmatter is not closed")
    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        raise LessonBlocked("frontmatter is not valid YAML") from exc
    if not isinstance(meta, dict):
        raise LessonBlocked("frontmatter must be a mapping")
    return meta, parts[2].strip()


def _sources_section(body: str) -> str:
    match = re.search(r"^##\s+sources\s*$", body, re.I | re.M)
    if not match:
        return ""
    rest = body[match.end():]
    nxt = re.search(r"^##\s+", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def _private(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == bit or host.endswith("." + bit) for bit in _PRIVATE_HOST)


def load_lesson(path: Path, *, expected_date: str) -> LessonFile:
    """Validate one TEMPLATE-format lesson. Raises LessonBlocked and returns nothing partial."""
    path = Path(path)
    match = _FILENAME.fullmatch(path.name)
    if not match:
        raise LessonBlocked("filename must be YYYY-MM-DD-slug.md")
    if match.group("date") != expected_date:
        raise LessonBlocked("filename date must be the lesson date")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise LessonBlocked("lesson file could not be read") from exc
    meta, body = _split_frontmatter(text)
    title = " ".join(str(meta.get("title") or "").split())
    summary = " ".join(str(meta.get("summary") or "").split())
    level = str(meta.get("level") or "").strip()
    slug = str(meta.get("slug") or "").strip()
    found_date = _as_date(meta.get("date"))
    if not title:
        raise LessonBlocked("title is missing")
    if level not in LEVELS:
        raise LessonBlocked("level must be Foundations, Systems, or Frontier")
    if not _SLUG.fullmatch(slug) or slug != match.group("slug"):
        raise LessonBlocked("slug must match the filename and be 2 to 5 kebab-case words")
    if found_date != expected_date:
        raise LessonBlocked("date must be the lesson date")
    if not summary:
        raise LessonBlocked("summary is missing")
    if not body.startswith("# "):
        raise LessonBlocked("the lesson needs a title heading")
    for name, pattern in _HEADINGS:
        if not pattern.search(body):
            raise LessonBlocked(f"missing the {name} section")
    links = _LINK.findall(_sources_section(body))
    if not 2 <= len(links) <= 5:
        raise LessonBlocked("sources must be 2 to 5 links")
    for _label, url in links:
        if _private(url):
            raise LessonBlocked("a source link is private")
    return LessonFile(title, expected_date, level, slug, summary, body)


def lesson_for_date(date: str, directory: Path | None = None) -> LessonFile:
    """The one lesson file for ``date``. Missing or ambiguous fails closed."""
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise LessonBlocked("date must be YYYY-MM-DD") from exc
    directory = Path(directory) if directory is not None else lesson_weekly_dir()
    if not directory.is_dir():
        raise LessonBlocked("lesson file is missing")
    matches = sorted(path for path in directory.glob(f"{date}-*.md") if path.is_file())
    if not matches:
        raise LessonBlocked("lesson file is missing")
    if len(matches) > 1:
        raise LessonBlocked(f"more than one lesson file for {date}")
    return load_lesson(matches[0], expected_date=date)


def week_lesson(issue_date: str, directory: Path | None = None) -> LessonFile | None:
    """Lesson for the Monday-Sunday week of ``issue_date``, or None.

    The Monday newsletter uses this. A missing directory, a missing file,
    or a file that does not validate omits the section. It does not fail
    the news issue.
    """
    try:
        day = datetime.strptime(issue_date, "%Y-%m-%d").date()
    except ValueError:
        return None
    directory = Path(directory) if directory is not None else lesson_weekly_dir()
    if not directory.is_dir():
        return None
    monday = day - timedelta(days=day.weekday())
    sunday = monday + timedelta(days=6)
    found = []
    for path in sorted(directory.glob("*.md")):
        if not path.is_file():
            continue
        match = _FILENAME.fullmatch(path.name)
        if not match:
            continue
        try:
            file_day = datetime.strptime(match.group("date"), "%Y-%m-%d").date()
        except ValueError:
            continue
        if not monday <= file_day <= sunday:
            continue
        try:
            found.append(load_lesson(path, expected_date=match.group("date")))
        except LessonBlocked:
            continue
    if not found:
        return None
    found.sort(key=lambda item: (item.date, item.slug))
    return found[0]


def render_lesson_mdx(lesson: LessonFile) -> str:
    lines = [
        "---",
        f"title: {_yaml_quote(lesson.title)}",
        f"date: {lesson.date}",
        f"level: {lesson.level}",
        f"slug: {lesson.slug}",
        f"summary: {_yaml_quote(lesson.summary)}",
        "---",
        "",
        lesson.body.rstrip(),
        "",
    ]
    return "\n".join(lines)
