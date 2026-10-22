"""Weekly AI lesson. Public sources only. Fail closed.

The Monday news command is a different mode. This one picks the next topic
from lessons/queue.json, opens public sources, and drafts one lesson.
It never sends email, never posts to Slack, and never publishes the site.
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

from src.clean import split_sentences
from src.dedupe import canonical_url
from src.fetch import http_get, parse_iso
from src.newsletter import (
    ACCENT,
    CANVAS,
    FONT_LINK,
    HAIR,
    INK,
    LOGO_PX,
    LOGO_URL,
    MONO,
    MUTED,
    SANS,
    SHEET,
    UNSUBSCRIBE,
    WARM,
    _dark_mode_css,
    _e,
    _link,
    _row,
    _type,
    _yaml_quote,
)
from src.read import extract_html_text
from src.summarize import TOGETHER_API_URL, _completion_meta

PACIFIC = ZoneInfo("America/Los_Angeles")
LEVELS = ("Foundations", "Systems", "Frontier")
WEB_ROOT = "https://nikibirkner.com/ai-lessons"
SUBSCRIBE_URL = "https://www.nikibirkner.com/ai-lessons/"
SUBSCRIBE_LABEL = "nikibirkner.com/ai-lessons"

SYSTEM_PROMPT = (
    "You write a weekly AI lesson for a public newsletter read by curious non-experts. "
    "Tone: fun, plain, warm. Teach exactly one mechanism per lesson with a hook, a clear "
    "explanation, concrete numbers from real sources, one everyday analogy that explains "
    "how the thing works, a \"Try this\" exercise anyone can do in 15 minutes without code, "
    "a one-line \"Use it at dinner\" takeaway, and 2 to 5 source links. Use only facts you "
    "found in sources you opened, and copy numbers exactly. Never use em dashes. Never "
    "mention the author, any employer, careers, bots, or a curriculum. Write 800 to 1,200 "
    "words. Output the file in the given frontmatter format. If you can't verify at least "
    "two sources, output BLOCKED with the reason instead."
)

FACTS_RULE = (
    "Use only facts from the provided public sources plus brief reflection. No invented facts."
)

_BANNED = (
    (re.compile(r"line for work", re.I), "line for work"),
    (re.compile(r"staff\s*/\s*pm", re.I), "staff/pm"),
    (re.compile(r"staff-pm", re.I), "staff-pm"),
    (re.compile(r"platform pm", re.I), "platform pm"),
    (re.compile(r"written by ai", re.I), "written by ai"),
    (re.compile(r"\bcurriculum\b", re.I), "curriculum"),
    (re.compile(r"\bday\s+\d+\b", re.I), "a day number"),
    (re.compile(r"\bbots?\b", re.I), "bots"),
    (re.compile(r"\bcareers?\b", re.I), "career"),
    (re.compile(r"\btogether\b", re.I), "an employer name"),
    (re.compile(r"\bniki\b", re.I), "the author"),
)
_EM_DASH = re.compile(r"—| -- ")
_ACRONYM = re.compile(r"\b[A-Z]{3,}\b")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){1,4}$")
_PRIVATE_HOST = (
    "notion.so",
    "notion.site",
    "slack.com",
    "docs.google.com",
    "twitter.com",
    "x.com",
    "linkedin.com",
)
_DENIED = re.compile(
    r"access denied|enable javascript|captcha|sign in to continue|just a moment",
    re.I,
)
_HEADINGS = (
    ("idea", re.compile(r"^##\s+.*(idea|how it works)", re.I | re.M)),
    ("evidence", re.compile(r"^##\s+.*(numbers|evidence)", re.I | re.M)),
    ("analogy", re.compile(r"^##\s+an analogy\b", re.I | re.M)),
    ("try", re.compile(r"^##\s+try this\s*$", re.I | re.M)),
    ("dinner", re.compile(r"^##\s+use it at dinner\s*$", re.I | re.M)),
    ("sources", re.compile(r"^##\s+sources\s*$", re.I | re.M)),
)


class LessonBlocked(Exception):
    def __init__(self, reason: str):
        self.reason = (reason or "research failed").strip()
        super().__init__(self.reason)

    @property
    def line(self) -> str:
        return f"BLOCKED: {self.reason}"


def pacific_today(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(PACIFIC).date().isoformat()


def lesson_email_title(date: str) -> str:
    """Inbox and page title. 'AI Lessons, October 27, 2026'."""
    try:
        day = datetime.strptime(str(date).strip(), "%Y-%m-%d")
    except ValueError:
        return ""
    return f"AI Lessons, {day.strftime('%B')} {day.day}, {day.year}"


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?", text or ""))


def load_json(path: Path, default):
    path = Path(path)
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LessonBlocked(f"could not read {path.name}") from exc


def load_queue(path: Path | None = None) -> list:
    payload = load_json(Path(path) if path else Path("lessons/queue.json"), [])
    if not isinstance(payload, list):
        raise LessonBlocked("topic queue is not a list")
    return payload


def load_prior(path: Path | None = None) -> list:
    payload = load_json(Path(path) if path else Path("lessons/prior.json"), [])
    if not isinstance(payload, list):
        raise LessonBlocked("prior lessons are not a list")
    return payload


def load_published_slugs(path: Path | None = None) -> set:
    payload = load_json(Path(path) if path else Path("lessons/published.json"), [])
    slugs = set()
    if isinstance(payload, dict):
        payload = payload.get("slugs") or payload.get("published") or []
    if not isinstance(payload, list):
        raise LessonBlocked("published lessons are not a list")
    for item in payload:
        if isinstance(item, str) and item.strip():
            slugs.add(item.strip())
        elif isinstance(item, dict) and item.get("slug"):
            slugs.add(str(item["slug"]).strip())
    return slugs


def next_topic(queue: list, prior: list, published: set) -> dict | None:
    """First queue item that is not already a prior or published lesson."""
    done = {str(item.get("slug") or "").strip() for item in prior if isinstance(item, dict)}
    done.update(published or set())
    done.discard("")
    for topic in queue:
        if not isinstance(topic, dict):
            continue
        slug = str(topic.get("slug") or "").strip()
        if slug and slug not in done:
            return topic
    return None


def _within_two_years(item: dict, now: datetime) -> bool:
    raw = item.get("published") or ""
    if not raw:
        return True
    parsed = parse_iso(str(raw))
    if parsed is None:
        return False
    return parsed >= now - timedelta(days=730)


def arxiv_candidates(topic: dict, *, now: datetime) -> list:
    """Public arXiv search for the topic. The last 24 months only."""
    from src.fetch import fetch_arxiv

    words = " ".join(str(topic.get("title") or "").split())
    articles = fetch_arxiv({
        "name": "arXiv",
        "url": "https://export.arxiv.org/api/query",
        "query": f"all:{words}",
        "max_results": 8,
        "domain": "arxiv.org",
        "kind": "paper",
        "priority": 4,
    })
    rows = []
    for article in articles:
        if article.published_at < now - timedelta(days=730):
            continue
        rows.append({
            "title": article.title,
            "url": article.url,
            "source": article.source_name or "arXiv",
            "summary": article.content_preview or "",
            "published": article.published_at.isoformat(),
            "reflection": str(topic.get("reflection") or ""),
        })
        if len(rows) >= 8:
            break
    return rows


def open_public_url(url: str) -> str:
    response = http_get(url)
    raw = getattr(response, "text", "") or ""
    if "<" in raw[:800].lower():
        text = extract_html_text(raw)
    else:
        text = raw
    return text[:12000]


def research_sources(topic: dict, candidates: list, opener, *, now: datetime) -> list:
    """Open up to 8 public URLs. A short, empty, or blocked page does not count."""
    opened = []
    for item in candidates:
        if len(opened) >= 8:
            break
        if not isinstance(item, dict) or not _within_two_years(item, now):
            continue
        url = str(item.get("url") or "").strip()
        if not url.startswith("http://") and not url.startswith("https://"):
            continue
        try:
            text = opener(url)
        except Exception as exc:
            print(f"Lesson source did not open: {url} ({exc.__class__.__name__})", file=sys.stderr)
            continue
        text = " ".join(str(text or "").split())
        if len(text) < 80 or _DENIED.search(text[:500]):
            continue
        reflection = str(item.get("reflection") or topic.get("reflection") or "").strip()
        opened.append({
            "title": str(item.get("title") or "").strip(),
            "url": url,
            "source": str(item.get("source") or item.get("source_name") or "").strip(),
            "text": text[:8000],
            "reflection": reflection,
        })
    return opened


def lesson_prompt(topic: dict, prior: list, sources: list, date: str) -> str:
    prior_rows = [
        {
            "title": item.get("title") or "",
            "slug": item.get("slug") or "",
            "summary": item.get("summary") or "",
            "level": item.get("level") or "",
        }
        for item in prior
        if isinstance(item, dict) and item.get("title")
    ]
    packets = [
        {
            "title": item["title"],
            "url": item["url"],
            "source": item["source"],
            "reflection": item.get("reflection") or "",
            "text": item["text"],
        }
        for item in sources
    ]
    return f"""{SYSTEM_PROMPT}

{FACTS_RULE}

Format rules:
- Frontmatter fields, in order: title, date, level, slug, summary.
- title is one sentence, a claim. date is {date}. level is {topic.get("level")}. slug is {topic.get("slug")}.
- summary is one or two plain sentences.
- Body: a hook of 2 to 4 sentences, then these sections: How it works (or The idea), The numbers or the evidence, An analogy, Try this, Use it at dinner, Sources.
- Define every term the first time it appears, in the same sentence. Define an acronym as words (ACRONYM) on first use.
- Bold the key numbers. Copy every number exactly from an opened source. A number that is not in a source must be labeled illustrative.
- Sources: 2 to 5 markdown links, each one an opened URL below. No private links.
- 800 to 1,200 words. Talk to "you." No em dashes. No career framing. No author, employer, bots, curriculum, or day numbers.
- Cross-link an earlier lesson by its name, never by number or day.
- If you cannot keep those rules, output a single line: BLOCKED: <reason>

Topic: {topic.get("title") or ""}
Level: {topic.get("level") or ""}
Slug: {topic.get("slug") or ""}
Brief: {topic.get("brief") or ""}
Reflection: {topic.get("reflection") or ""}

Prior lessons:
{json.dumps(prior_rows, ensure_ascii=True)}

Opened public sources:
{json.dumps(packets, ensure_ascii=True)}
"""


def call_lesson_model(prompt: str, api_key: str, model: str) -> str:
    """One completion. Markdown, not JSON. No send, no tool call."""
    response = requests.post(
        TOGETHER_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 8192,
            "temperature": 0.2,
        },
        timeout=180,
    )
    if response.status_code in (401, 403):
        raise LessonBlocked("Together rejected the key")
    if response.status_code >= 400:
        raise LessonBlocked("research failed")
    try:
        meta = _completion_meta(response.json())
    except (ValueError, TypeError) as exc:
        raise LessonBlocked("research failed") from exc
    if not meta["content"] or meta["finish_reason"] == "length":
        raise LessonBlocked("the draft was cut off")
    return meta["content"]


def _strip_fence(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:markdown|mdx|md)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _split_frontmatter(text: str) -> tuple:
    if not text.startswith("---"):
        raise LessonBlocked("the draft has no frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        raise LessonBlocked("the draft has no frontmatter")
    raw = text[4:end]
    body = text[end + 4:].strip()
    try:
        meta = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise LessonBlocked("the draft frontmatter is not valid") from exc
    if not isinstance(meta, dict):
        raise LessonBlocked("the draft frontmatter is not valid")
    return meta, body


def _as_date(value) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "").strip()


def _sources_section(body: str) -> str:
    match = re.search(r"^##\s+sources\s*$", body, re.I | re.M)
    if not match:
        return ""
    return body[match.end():]


def _prose_without_urls(body: str) -> str:
    return _LINK.sub(lambda match: match.group(1), body)


def _check_numbers(body: str, corpus: str) -> str:
    prose = _prose_without_urls(body)
    for sentence in split_sentences(prose):
        if re.search(r"\billustrative\b", sentence, re.I):
            continue
        for number in _NUMBER.findall(sentence):
            plain = number.replace(",", "")
            if number not in corpus and plain not in corpus and number.rstrip("%") not in corpus:
                return f"the number {number} is not in an opened source"
    return ""


def _undefined_acronyms(body: str) -> str:
    seen = set()
    for match in _ACRONYM.finditer(body):
        token = match.group(0)
        if token in seen:
            continue
        seen.add(token)
        window = body[max(0, match.start() - 180): match.end() + 1]
        if f"({token})" not in window:
            return f"{token} is not defined on first use"
    return ""


def _private_url(url: str) -> bool:
    host = canonical_url(url).split("/")[0]
    return any(host == bit or host.endswith("." + bit) for bit in _PRIVATE_HOST)


def validate_lesson(text: str, *, topic: dict, sources: list, date: str) -> dict:
    """Return the normalized lesson, or raise LessonBlocked. Never returns a partial."""
    cleaned = _strip_fence(text)
    if cleaned.startswith("BLOCKED:"):
        reason = cleaned.splitlines()[0].split(":", 1)[1].strip() or "research failed"
        raise LessonBlocked(reason)
    meta, body = _split_frontmatter(cleaned)
    title = " ".join(str(meta.get("title") or "").split())
    summary = " ".join(str(meta.get("summary") or "").split())
    level = str(meta.get("level") or "").strip()
    slug = str(meta.get("slug") or "").strip()
    found_date = _as_date(meta.get("date"))
    if not title or len(split_sentences(title)) != 1:
        raise LessonBlocked("title must be one sentence")
    if level not in LEVELS or level != str(topic.get("level") or ""):
        raise LessonBlocked("level must be Foundations, Systems, or Frontier for this topic")
    if slug != str(topic.get("slug") or "") or not _SLUG.match(slug):
        raise LessonBlocked("slug must be the topic slug, 2 to 5 kebab-case words")
    if found_date != date:
        raise LessonBlocked("date must be the lesson date")
    summary_sentences = split_sentences(summary)
    if not summary or not 1 <= len(summary_sentences) <= 2:
        raise LessonBlocked("summary must be one or two sentences")
    if not body.startswith("# "):
        raise LessonBlocked("the lesson needs a title heading")
    first_section = re.search(r"^##\s+", body, re.M)
    hook = body[: first_section.start()] if first_section else body
    hook = re.sub(r"^#.+\n+", "", hook).strip()
    hook_count = len(split_sentences(hook))
    if not 2 <= hook_count <= 4:
        raise LessonBlocked("the hook must be 2 to 4 sentences")
    for name, pattern in _HEADINGS:
        if not pattern.search(body):
            raise LessonBlocked(f"missing the {name} section")
    links = _LINK.findall(_sources_section(body))
    if not 2 <= len(links) <= 5:
        raise LessonBlocked("sources must be 2 to 5 links")
    opened = {canonical_url(item["url"]) for item in sources}
    for _label, url in _LINK.findall(body):
        if _private_url(url) or canonical_url(url) not in opened:
            raise LessonBlocked("a source link was not opened this run")
    prose = f"{title}\n{summary}\n{body}"
    if _EM_DASH.search(prose):
        raise LessonBlocked("the draft uses an em dash")
    for pattern, label in _BANNED:
        if pattern.search(_prose_without_urls(prose)):
            raise LessonBlocked(f"the draft uses banned wording ({label})")
    count = word_count(body)
    if not 800 <= count <= 1200:
        raise LessonBlocked(f"the draft is {count} words, and it needs 800 to 1,200")
    corpus = "\n".join(item["text"] + "\n" + (item.get("reflection") or "") for item in sources)
    number_error = _check_numbers(body, corpus)
    if number_error:
        raise LessonBlocked(number_error)
    acronym_error = _undefined_acronyms(body)
    if acronym_error:
        raise LessonBlocked(acronym_error)
    return {
        "title": title,
        "date": date,
        "level": level,
        "slug": slug,
        "summary": summary,
        "body": body.strip(),
    }


def render_lesson_mdx(lesson: dict) -> str:
    lines = [
        "---",
        f"title: {_yaml_quote(lesson['title'])}",
        f"date: {lesson['date']}",
        f"level: {lesson['level']}",
        f"slug: {lesson['slug']}",
        f"summary: {_yaml_quote(lesson['summary'])}",
        "---",
        "",
        lesson["body"].rstrip(),
        "",
    ]
    return "\n".join(lines)


def _inline(text: str) -> str:
    parts = []
    cursor = 0
    pattern = re.compile(r"\*\*(.+?)\*\*|\[([^\]]+)\]\((https?://[^)\s]+)\)")
    for match in pattern.finditer(text or ""):
        parts.append(_e(text[cursor:match.start()]))
        if match.group(1) is not None:
            parts.append(f"<strong>{_e(match.group(1))}</strong>")
        else:
            parts.append(_link(_e(match.group(3)), match.group(2), size=16))
        cursor = match.end()
    parts.append(_e((text or "")[cursor:]))
    return "".join(parts)


def markdown_to_html(body: str) -> str:
    """Enough of the lesson markdown for the email body. The shell is the news template."""
    blocks = []
    paragraph = []
    bullets = []

    def flush_paragraph() -> None:
        if not paragraph:
            return
        text = " ".join(paragraph).strip()
        paragraph.clear()
        if text:
            blocks.append(
                f'<p class="fg" style="{_type(16, 400, INK, "1.65", bottom=14)}">{_inline(text)}</p>'
            )

    def flush_bullets() -> None:
        if not bullets:
            return
        items = "".join(
            f'<li style="margin:0 0 8px;">{_inline(item)}</li>' for item in bullets
        )
        bullets.clear()
        blocks.append(
            f'<ul class="fg" style="{_type(16, 400, INK, "1.65", bottom=14)};padding-left:22px;">{items}</ul>'
        )

    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line:
            flush_paragraph()
            flush_bullets()
            continue
        if line.startswith("## "):
            flush_paragraph()
            flush_bullets()
            blocks.append(
                f'<h2 class="fg" style="{_type(18, 600, INK, "1.35", bottom=12, tracking="-0.015em")};padding:0;">'
                f"{_inline(line[3:])}</h2>"
            )
            continue
        if line.startswith("# "):
            flush_paragraph()
            flush_bullets()
            blocks.append(
                f'<h2 class="fg" style="{_type(22, 600, INK, "1.3", bottom=12, tracking="-0.02em")};padding:0;">'
                f"{_inline(line[2:])}</h2>"
            )
            continue
        if line.startswith("- "):
            flush_paragraph()
            bullets.append(line[2:].strip())
            continue
        flush_bullets()
        paragraph.append(line)
    flush_paragraph()
    flush_bullets()
    return "".join(blocks)


def render_lesson_email(lesson: dict) -> str:
    """The news letter shell, with the lesson title 'AI Lessons, Month D, Year'."""
    title = lesson_email_title(lesson["date"])
    page = f"{WEB_ROOT}/{lesson['date']}-{lesson['slug']}"
    long_date = datetime.strptime(lesson["date"], "%Y-%m-%d")
    byline = f"by Niki Birkner · {long_date.strftime('%A, %B')} {long_date.day}, {long_date.year}"
    logo = (
        f'<a href="{_e(page)}" style="text-decoration:none;border:0;">'
        f'<img src="{LOGO_URL}" width="{LOGO_PX}" height="{LOGO_PX}" alt="Niki Birkner" '
        f'style="display:block;border:0;outline:none;text-decoration:none;width:{LOGO_PX}px;height:{LOGO_PX}px;">'
        f"</a>"
    )
    masthead = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        "<tr>"
        f'<td width="{LOGO_PX}" valign="middle" style="width:{LOGO_PX}px;padding:0 12px 0 0;">{logo}</td>'
        '<td valign="middle" align="left">'
        f'<p class="accent" style="{_type(18, 600, ACCENT, "1.2", tracking="-0.02em")}">AI Lessons</p>'
        f'<p class="muted" style="{_type(13, 400, MUTED, "1.45", family=MONO)};margin-top:5px;">{_e(byline)}</p>'
        "</td></tr></table>"
    )
    subscribe = (
        f'<p class="muted" style="{_type(12, 400, MUTED, "1.45", family=MONO)}">'
        "Forwarded this? Subscribe at "
        + _link(SUBSCRIBE_URL, SUBSCRIBE_LABEL, size=12, color=ACCENT, family=MONO)
        + "</p>"
    )
    rows = [
        _row(subscribe, "0 0 14px"),
        _row(masthead, "0 0 18px"),
        (
            "<tr><td class=\"accent-bar\" bgcolor=\"" + ACCENT + "\" height=\"3\" "
            f'style="height:3px;line-height:3px;font-size:3px;background-color:{ACCENT};padding:0;margin:0;">'
            "&nbsp;</td></tr>"
        ),
        _row(
            f'<h1 class="fg" style="{_type(26, 600, INK, "1.25", tracking="-0.02em")};padding:0;">{_e(title)}</h1>',
            "24px 0 0",
        ),
        _row(
            f'<p class="fg" style="{_type(17, 400, INK, "1.65")}">{_e(lesson["summary"])}</p>',
            "16px 0 0",
        ),
        _row(
            f'<p style="margin:0;">{_link(_e(page), "Read this on the web")}</p>',
            "16px 0 26px",
        ),
        _row(markdown_to_html(lesson["body"]), "8px 0 12px", border=True),
        _row(
            "<p class=\"fg\" style=\"" + _type(14, 400, INK, "1.5") + "\">AI Lessons by Niki Birkner</p>"
            + f'<p style="margin:12px 0 0;">{_link(_e(page), "Read this on the web", color=INK, underline=ACCENT, klass="footlink")}</p>'
            + f'<p style="margin:8px 0 0;">{_link(UNSUBSCRIBE, "Unsubscribe", color=INK, underline=ACCENT, klass="footlink")}</p>',
            "16px 16px",
            border=True,
            klass="foot",
            extra=f"background-color:{WARM};",
            bgcolor=WARM,
        ),
    ]
    body_style = (
        f"margin:0;padding:0;background-color:{CANVAS};color:{INK};"
        f"font-family:{SANS};-webkit-text-size-adjust:100%;text-size-adjust:100%;"
        "-webkit-font-smoothing:antialiased;"
    )
    sheet_style = (
        f"width:100%;max-width:600px;border:1px solid {HAIR};border-collapse:separate;"
        f"background-color:{SHEET};"
    )
    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{_e(title)}</title>
<link rel="stylesheet" href="{FONT_LINK}">
<style>{_dark_mode_css()}</style>
</head>
<body class="bg" bgcolor="{CANVAS}" style="{body_style}">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{_e(lesson["summary"])}</div>
<table role="presentation" class="bg" width="100%" bgcolor="{CANVAS}" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;background-color:{CANVAS};">
<tr><td class="wrap" align="center" valign="top" style="padding:32px 16px;margin:0;">
<table role="presentation" class="sheet" width="600" bgcolor="{SHEET}" cellpadding="0" cellspacing="0" border="0" style="{sheet_style}">
<tr><td class="sheet px" align="left" bgcolor="{SHEET}" style="padding:40px 36px 36px;background-color:{SHEET};overflow-wrap:break-word;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
{''.join(rows)}
</table>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


def render_lesson_plain(lesson: dict) -> str:
    title = lesson_email_title(lesson["date"])
    page = f"{WEB_ROOT}/{lesson['date']}-{lesson['slug']}"
    lines = [
        title,
        "",
        lesson["summary"],
        "",
        lesson["body"].rstrip(),
        "",
        "Read this on the web",
        page,
        "",
        "AI Lessons by Niki Birkner",
        "Unsubscribe",
        UNSUBSCRIBE,
        "",
    ]
    return "\n".join(lines)


def generate_lesson(
    *,
    date: str,
    api_key: str | None,
    model: str,
    queue_path: Path | None = None,
    prior_path: Path | None = None,
    published_path: Path | None = None,
    now: datetime | None = None,
    candidates=None,
    opener=None,
    composer=None,
) -> dict:
    """Draft the next lesson, or raise LessonBlocked. Does not send or publish."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    queue = load_queue(queue_path)
    prior = load_prior(prior_path)
    published = load_published_slugs(published_path)
    topic = next_topic(queue, prior, published)
    if topic is None:
        raise LessonBlocked(
            "topic queue is empty. Approve new topics before they join the queue."
        )
    if not (api_key or "").strip():
        raise LessonBlocked("TOGETHER_API_KEY is not set")
    try:
        found = candidates(topic) if candidates is not None else arxiv_candidates(topic, now=moment)
    except LessonBlocked:
        raise
    except Exception as exc:
        raise LessonBlocked("research failed") from exc
    open_url = opener or open_public_url
    try:
        sources = research_sources(topic, found or [], open_url, now=moment)
    except LessonBlocked:
        raise
    except Exception as exc:
        raise LessonBlocked("research failed") from exc
    if len(sources) < 2:
        raise LessonBlocked("fewer than 2 sources could be opened")
    prompt = lesson_prompt(topic, prior, sources, date)
    write = composer or call_lesson_model
    try:
        raw = write(prompt, api_key, model)
    except LessonBlocked:
        raise
    except Exception as exc:
        raise LessonBlocked("research failed") from exc
    lesson = validate_lesson(raw, topic=topic, sources=sources, date=date)
    lesson["email_title"] = lesson_email_title(date)
    lesson["markdown"] = render_lesson_mdx(lesson)
    lesson["html"] = render_lesson_email(lesson)
    lesson["plain"] = render_lesson_plain(lesson)
    lesson["topic"] = topic
    return lesson
