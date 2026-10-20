"""MDX and email renders of one Issue. Formatting only; the words come from the issue."""

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from src.clean import slack_escape
from src.slack import render_briefing

WEB_ROOT = "https://www.nikibirkner.com/newsletter"
PACIFIC = ZoneInfo("America/Los_Angeles")
UNSUBSCRIBE = "{{{RESEND_UNSUBSCRIBE_URL}}}"


def web_url(issue) -> str:
    return f"{WEB_ROOT}/{issue.date}"


def render_slack(issue) -> str:
    until = issue.until or datetime.now(PACIFIC)
    since = issue.since or until
    return render_briefing(until, since, issue.items, lede=issue.summary)


def _yaml_quote(value: str) -> str:
    text = " ".join((value or "").split())
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def window_line(issue) -> str:
    """Mon Oct 19, 8:00 AM PT – Tue Oct 20, 11:00 AM PT, when the issue has both ends."""
    if not issue.since or not issue.until:
        return ""
    return f"Window: {_pt_stamp(issue.since)} to {_pt_stamp(issue.until)}"


def _pt_stamp(moment: datetime) -> str:
    local = moment.astimezone(PACIFIC)
    hour = str(int(local.strftime("%I")))
    return f"{local.strftime('%a %b')} {local.day}, {hour}:{local.strftime('%M %p')} PT"


def _also_line(issue) -> str:
    titles = [title.strip() for title in (getattr(issue, "also_in_window", ()) or ()) if title and title.strip()]
    if not titles:
        return ""
    return "Also in the window: " + "; ".join(titles) + "."


def render_mdx(issue) -> str:
    tags = ", ".join(issue.tags or ["ai"])
    lines = [
        "---",
        f"title: {_yaml_quote(issue.title)}",
        f"date: {issue.date}",
        f"summary: {_yaml_quote(issue.summary)}",
        f"tags: [{tags}]",
        "---",
        "",
    ]
    stamp = window_line(issue)
    if stamp:
        lines.extend([stamp, ""])
    if issue.summary:
        lines.extend([issue.summary, ""])
    for item in issue.items:
        lines.extend([
            f"**{item.what_happened}**",
            "",
            item.body,
            "",
            item.why_it_matters,
            "",
            f"[{item.source_name}]({item.url})",
            "",
        ])
    also = _also_line(issue)
    if also:
        lines.extend([also, ""])
    return "\n".join(lines).rstrip() + "\n"


def render_email(issue) -> str:
    """HTML body for a Resend broadcast. Unsubscribe is Resend's placeholder."""
    chunks = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f"<title>{html.escape(issue.title)}</title></head>",
        "<body>",
        f"<h1>{html.escape(issue.title)}</h1>",
        f'<p><a href="{html.escape(web_url(issue))}">Read this on the web</a></p>',
    ]
    stamp = window_line(issue)
    if stamp:
        chunks.append(f"<p>{html.escape(stamp)}</p>")
    if issue.summary:
        chunks.append(f"<p>{html.escape(issue.summary)}</p>")
    for item in issue.items:
        chunks.append(f"<h2>{html.escape(item.what_happened)}</h2>")
        for sentence in _paragraphs(item.body):
            chunks.append(f"<p>{html.escape(sentence)}</p>")
        chunks.append(f"<p>{html.escape(item.why_it_matters)}</p>")
        chunks.append(
            f'<p><a href="{html.escape(item.url)}">{html.escape(item.source_name)}</a></p>'
        )
    also = _also_line(issue)
    if also:
        chunks.append(f"<p>{html.escape(also)}</p>")
    chunks.extend([
        f'<p><a href="{html.escape(web_url(issue))}">Web version</a></p>',
        f'<p><a href="{UNSUBSCRIBE}">Unsubscribe</a></p>',
        "</body></html>",
        "",
    ])
    return "\n".join(chunks)


def _paragraphs(body: str) -> list:
    text = (body or "").strip()
    if not text:
        return []
    return [text]


def slack_contains_story(issue, slack_text: str) -> bool:
    """Used by tests. Slack escapes &, <, and >."""
    for item in issue.items:
        if slack_escape(item.what_happened) not in slack_text:
            return False
        if item.body and slack_escape(item.body) not in slack_text:
            return False
        if slack_escape(item.why_it_matters) not in slack_text:
            return False
    return True
