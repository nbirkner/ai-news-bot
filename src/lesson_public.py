"""Gates that keep a lesson send on the public site.

A send fetches the live page and reuses the Monday public-text checks on
the whole lesson. A dry run does not fetch the page and does not call Resend.
"""
import html
import re
from urllib.parse import urlparse

import requests

from src.guardrails import public_text_reasons
from src.lesson_file import LessonBlocked

_H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_URL = re.compile(r"https?://[^\s)>\]]+")
_NOTION_HOST = ("notion.so", "notion.site")


def lesson_text(lesson) -> str:
    return "\n".join([
        str(getattr(lesson, "title", "") or ""),
        str(getattr(lesson, "summary", "") or ""),
        str(getattr(lesson, "body", "") or ""),
    ])


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def notion_url_in(text: str) -> bool:
    """True when the text contains a Notion URL, in or out of the Sources list."""
    for raw in _URL.findall(text or ""):
        host = _host(raw)
        if any(host == name or host.endswith("." + name) for name in _NOTION_HOST):
            return True
    return False


def lesson_body_blocks(lesson) -> list:
    """Monday public-text blocks, plus a Notion URL anywhere in the lesson."""
    blob = lesson_text(lesson)
    reasons = public_text_reasons(blob)
    if notion_url_in(blob):
        reasons.append("a Notion URL is in the lesson")
    return reasons


def _plain(value: str) -> str:
    return " ".join(html.unescape(value or "").split())


def page_h1(page: str) -> str:
    match = _H1.search(page or "")
    if not match:
        return ""
    return _plain(_TAG.sub("", match.group(1)))


def assert_lesson_is_live(lesson, get=None):
    """The live page is 200 and its h1 is the lesson title.

    Raises LessonBlocked otherwise. Does not call Resend.
    """
    url = str(getattr(lesson, "web_url", "") or "").strip()
    title = _plain(str(getattr(lesson, "title", "") or ""))
    if not url or not title:
        raise LessonBlocked("the live lesson page is not published")
    getter = get or requests.get
    try:
        response = getter(url, timeout=30, headers={"User-Agent": "ai-news-bot"})
    except requests.RequestException as exc:
        raise LessonBlocked("the live lesson page could not be fetched") from exc
    status = getattr(response, "status_code", None)
    if status != 200:
        raise LessonBlocked("the live lesson page is not published")
    found = page_h1(getattr(response, "text", "") or "")
    if found != title:
        raise LessonBlocked("the live lesson title does not match")
    return url
