"""MDX and email renders of one Issue. Formatting only; the words come from the issue."""

import html
import re
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from src.clean import slack_escape
from src.slack import render_briefing

WEB_ROOT = "https://nikibirkner.com/ai-news"
PACIFIC = ZoneInfo("America/Los_Angeles")
# Resend replaces this on broadcasts only. Anywhere else it is a dead link.
UNSUBSCRIBE = "{{{RESEND_UNSUBSCRIBE_URL}}}"
UNSUBSCRIBE_FALLBACK = WEB_ROOT

# Tokens from nikibirkner.com (:root in the site CSS).
# Light --accent #C2410C, --g2 #FB923C, --g1 #FED7AA, --fg #111111,
# --muted #6B6B6B, --hairline #EAEAE8, --g0 #F1F1EF, --bg #FFFFFF.
# Dark --accent #FB923C, --g1 #5A2A12, --bg #0F0F0F, --fg #EDEDED,
# --muted #9A9A9A, --hairline #222222.
# Roboto is the brand title, headlines, and body. Gmail loads it.
# Fragment Mono stays on the meta lines (forward invite, byline, window).
SANS = "'Roboto',-apple-system,'Helvetica Neue',Helvetica,Arial,sans-serif"
MONO = "'Fragment Mono',ui-monospace,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"
INK = "#111111"
MUTED = "#6B6B6B"
HAIR = "#EAEAE8"
CANVAS = "#F1F1EF"
SHEET = "#FFFFFF"
ACCENT = "#C2410C"
ACCENT_DARK = "#FB923C"
WARM = "#FED7AA"
WARM_DARK = "#5A2A12"
# favicon.svg is the mark. Gmail does not render SVG, so this is the site PNG.
LOGO_URL = "https://nikibirkner.com/apple-touch-icon.png"
LOGO_PX = 36
# Archive index for people who were forwarded an issue. www, with the trailing slash.
SUBSCRIBE_URL = "https://www.nikibirkner.com/ai-news/"
SUBSCRIBE_LABEL = "nikibirkner.com/ai-news"
LESSONS_SUBSCRIBE_URL = "https://www.nikibirkner.com/ai-lessons/"
LESSONS_SUBSCRIBE_LABEL = "nikibirkner.com/ai-lessons"
FONT_LINK = "https://fonts.googleapis.com/css2?family=Fragment+Mono&amp;family=Roboto:wght@400;600&amp;display=swap"
_MD_TOKEN = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)|\*\*([^*]+)\*\*")


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


def _lesson_bits(lesson) -> tuple:
    """Title, summary, and public URL for an optional Monday lesson section."""
    if not lesson:
        return "", "", ""
    title = " ".join(str(getattr(lesson, "title", "") or "").split())
    summary = " ".join(str(getattr(lesson, "summary", "") or "").split())
    url = str(getattr(lesson, "web_url", "") or "").strip()
    if not title or not summary or not url.startswith("https://"):
        return "", "", ""
    return title, summary, url


def render_mdx(issue, *, lesson=None) -> str:
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
    lesson_title, lesson_summary, lesson_url = _lesson_bits(lesson)
    if lesson_title:
        lines.extend([
            "## This week's lesson",
            "",
            f"**{lesson_title}**",
            "",
            lesson_summary,
            "",
            f"[Read the lesson]({lesson_url})",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def unsubscribe_href(broadcast: bool = True) -> str:
    """Placeholder on a Resend broadcast; the archive otherwise."""
    return UNSUBSCRIBE if broadcast else UNSUBSCRIBE_FALLBACK


def issue_date_long(issue) -> str:
    raw = str(getattr(issue, "date", "") or "").strip()
    try:
        day = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return raw
    return f"{day.strftime('%A, %B')} {day.day}, {day.year}"


def email_headline(issue) -> str:
    """Inbox line for the letter. Month D, YYYY from the issue date, no weekday."""
    raw = str(getattr(issue, "date", "") or "").strip()
    try:
        day = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return ""
    return f"AI News, {day.strftime('%B')} {day.day}, {day.year}"


def email_subject(issue) -> str:
    """Same line as the headline. The MDX title stays whatever the issue carries."""
    return email_headline(issue)


def lesson_email_subject(lesson) -> str:
    """Inbox line for one lesson. Month D, YYYY, no weekday."""
    raw = str(getattr(lesson, "date", "") or "").strip()
    try:
        day = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return ""
    return f"AI Lessons, {day.strftime('%B')} {day.day}, {day.year}"


def lesson_byline(lesson) -> str:
    raw = str(getattr(lesson, "date", "") or "").strip()
    try:
        day = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return "by Niki Birkner"
    return f"by Niki Birkner · {day.strftime('%A, %B')} {day.day}, {day.year}"


def _byline(issue) -> str:
    date = issue_date_long(issue)
    if date:
        return f"by Niki Birkner · {date}"
    return "by Niki Birkner"


def _e(value) -> str:
    return html.escape(value or "", quote=True)


def _http_href(url: str) -> str:
    raw = (url or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return html.escape(raw, quote=True)
    return ""


def _source_label(name: str, url: str) -> str:
    """Publication name. The host only when a story has no name."""
    label = (name or "").strip()
    if label:
        return label
    try:
        host = urlparse(url or "").netloc.lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _paragraphs(body: str) -> list:
    text = (body or "").strip()
    if not text:
        return []
    parts = [part.strip() for part in text.split("\n\n") if part.strip()]
    return parts or [text]


def _type(size: int, weight: int, color: str, line: str, *, bottom: int = 0, tracking: str = "", family: str = "") -> str:
    css = [
        "margin:0",
        f"font-family:{family or SANS}",
        f"font-size:{size}px",
        f"font-weight:{weight}",
        f"line-height:{line}",
        f"color:{color}",
    ]
    if bottom:
        css[0] = f"margin:0 0 {bottom}px"
    if tracking:
        css.append(f"letter-spacing:{tracking}")
    return ";".join(css)


def _link_style(size: int = 14, color: str = ACCENT, underline: str = "", family: str = "") -> str:
    line = underline or color
    return (
        f"color:{color};text-decoration:underline;text-decoration-color:{line};"
        f"text-underline-offset:3px;text-decoration-thickness:1px;"
        f"font-family:{family or SANS};font-size:{size}px;font-weight:400;line-height:1.5"
    )


def _link(href: str, label: str, size: int = 14, color: str = ACCENT, underline: str = "", klass: str = "link", family: str = "") -> str:
    return f'<a class="{klass}" href="{href}" style="{_link_style(size, color, underline, family)}">{_e(label)}</a>'


def _row(inner: str, padding: str, border: bool = False, klass: str = "", extra: str = "", bgcolor: str = "") -> str:
    classes = " ".join(part for part in (("rule" if border else ""), klass) if part)
    class_attr = f' class="{classes}"' if classes else ""
    rule = f"border-top:1px solid {HAIR};" if border else ""
    bg_attr = f' bgcolor="{bgcolor}"' if bgcolor else ""
    return (
        "<tr>"
        f'<td align="left"{class_attr}{bg_attr} style="padding:{padding};{rule}{extra}margin:0;">'
        f"{inner}</td></tr>"
    )


def _story_html(item) -> str:
    parts = [
        f'<h2 class="fg" style="{_type(18, 600, INK, "1.35", bottom=12, tracking="-0.015em")};padding:0;">'
        f"{_e(item.what_happened)}</h2>"
    ]
    paragraphs = _paragraphs(item.body)
    why = (item.why_it_matters or "").strip()
    for index, paragraph in enumerate(paragraphs):
        last = index == len(paragraphs) - 1 and not why
        parts.append(
            f'<p class="fg" style="{_type(16, 400, INK, "1.65", bottom=0 if last else 14)}">{_e(paragraph)}</p>'
        )
    if why:
        parts.append(f'<p class="fg" style="{_type(16, 400, INK, "1.65")}">{_e(why)}</p>')
    label = _source_label(item.source_name, item.url)
    href = _http_href(item.url)
    if href and label:
        parts.append(f'<p style="margin:16px 0 0;">{_link(href, label)}</p>')
    elif label:
        parts.append(f'<p class="muted" style="{_type(14, 400, MUTED, "1.5")};margin-top:16px;">{_e(label)}</p>')
    return "".join(parts)


def _font_face_css() -> str:
    """Latin files for Apple Mail and iOS. Gmail drops this and uses the stack."""
    return """
    @font-face{font-family:'Fragment Mono';font-style:normal;font-weight:400;font-display:swap;src:url(https://fonts.gstatic.com/s/fragmentmono/v6/4iCr6K5wfMRRjxp0DA6-2CLnB4NHhqcL71Q.woff2) format('woff2');}
    @font-face{font-family:'Roboto';font-style:normal;font-weight:400;font-display:swap;src:url(https://fonts.gstatic.com/s/roboto/v51/KFO7CnqEu92Fr1ME7kSn66aGLdTylUAMa3yUBHMdazQ.woff2) format('woff2');}
    @font-face{font-family:'Roboto';font-style:normal;font-weight:600;font-display:swap;src:url(https://fonts.gstatic.com/s/roboto/v51/KFO7CnqEu92Fr1ME7kSn66aGLdTylUAMa3yUBHMdazQ.woff2) format('woff2');}
    """


def _dark_mode_css() -> str:
    """iOS Mail honors this. Gmail drops <head> CSS, so light styles stay inline.

    The visited rule stops iOS from painting links purple. Dark mode comes
    after it and wins on the same selector.
    """
    return f"""
    {_font_face_css()}
    :root {{ color-scheme: light dark; supported-color-schemes: light dark; }}
    a[x-apple-data-detectors] {{ color: inherit !important; text-decoration: inherit !important; }}
    a.link, a.link:visited {{ color: {ACCENT}; text-decoration-color: {ACCENT}; }}
    @media (prefers-color-scheme: dark) {{
      .bg {{ background-color: #0F0F0F !important; }}
      .sheet {{ background-color: #161616 !important; border-color: #222222 !important; }}
      .fg {{ color: #EDEDED !important; }}
      .muted {{ color: #9A9A9A !important; }}
      .rule {{ border-color: #222222 !important; }}
      .accent {{ color: {ACCENT_DARK} !important; }}
      .accent-bar {{ background-color: {ACCENT_DARK} !important; }}
      .foot {{ background-color: {WARM_DARK} !important; }}
      a.link, a.link:visited {{ color: {ACCENT_DARK} !important; text-decoration-color: {ACCENT_DARK} !important; }}
      a.footlink, a.footlink:visited {{ color: #EDEDED !important; text-decoration-color: {ACCENT_DARK} !important; }}
    }}
    @media only screen and (max-width: 620px) {{
      .wrap {{ padding-left: 8px !important; padding-right: 8px !important; }}
      .px {{ padding-left: 22px !important; padding-right: 22px !important; }}
    }}
    """


def _lesson_html(lesson) -> str:
    title, summary, url = _lesson_bits(lesson)
    if not title:
        return ""
    return (
        f'<h2 class="fg" style="{_type(18, 600, INK, "1.35", bottom=12, tracking="-0.015em")};padding:0;">'
        f"This week's lesson</h2>"
        f'<p class="fg" style="{_type(16, 600, INK, "1.45", bottom=8)}">{_e(title)}</p>'
        f'<p class="fg" style="{_type(16, 400, INK, "1.65")}">{_e(summary)}</p>'
        f'<p style="margin:16px 0 0;">{_link(_e(url), "Read the lesson")}</p>'
    )


def _inline_html(text: str) -> str:
    parts = []
    pos = 0
    for match in _MD_TOKEN.finditer(text or ""):
        parts.append(_e(text[pos:match.start()]))
        if match.group(1) is not None:
            parts.append(_link(html.escape(match.group(2), quote=True), match.group(1)))
        else:
            parts.append(f"<strong>{_e(match.group(3))}</strong>")
        pos = match.end()
    parts.append(_e((text or "")[pos:]))
    return "".join(parts)


def _inline_plain(text: str) -> str:
    def repl(match):
        if match.group(1) is not None:
            return f"{match.group(1)} ({match.group(2)})"
        return match.group(3)

    return _MD_TOKEN.sub(repl, text or "").strip()


def _lesson_body_blocks(body: str) -> list:
    """Headings, paragraphs, and lists. A leading # title is left out."""
    lines = (body or "").replace("\r\n", "\n").split("\n")
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines) and lines[index].startswith("# ") and not lines[index].startswith("## "):
        index += 1
    blocks = []
    paragraph = []
    items = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        text = " ".join(part.strip() for part in paragraph).strip()
        paragraph = []
        if text:
            blocks.append(("p", text))

    def flush_items() -> None:
        nonlocal items
        if items:
            blocks.append(("ul", list(items)))
            items = []

    for line in lines[index:]:
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            flush_items()
            continue
        if stripped.startswith("## ") or stripped.startswith("### "):
            flush_paragraph()
            flush_items()
            heading = stripped.lstrip("#").strip()
            if heading:
                blocks.append(("h", heading))
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            flush_paragraph()
            items.append(stripped[2:].strip())
            continue
        if items:
            flush_items()
        paragraph.append(stripped)
    flush_paragraph()
    flush_items()
    return blocks


def _lesson_body_html(body: str) -> str:
    chunks = []
    for kind, value in _lesson_body_blocks(body):
        if kind == "h":
            chunks.append(
                f'<h2 class="fg" style="{_type(18, 600, INK, "1.35", bottom=12, tracking="-0.015em")};padding:0;">'
                f"{_inline_html(value)}</h2>"
            )
        elif kind == "p":
            chunks.append(
                f'<p class="fg" style="{_type(16, 400, INK, "1.65", bottom=14)}">{_inline_html(value)}</p>'
            )
        else:
            lis = "".join(
                f'<li class="fg" style="{_type(16, 400, INK, "1.65", bottom=8)}">{_inline_html(item)}</li>'
                for item in value
            )
            chunks.append(f'<ul style="margin:0 0 14px;padding:0 0 0 18px;">{lis}</ul>')
    return "".join(chunks)


def _lesson_body_plain(body: str) -> list:
    lines = []
    for kind, value in _lesson_body_blocks(body):
        if kind == "ul":
            for item in value:
                lines.append(f"- {_inline_plain(item)}")
            lines.append("")
        else:
            lines.extend([_inline_plain(value), ""])
    return lines


def _wrap_email(title: str, preview: str, rows: list) -> str:
    """Shared letter shell. Light styles are inline. The head holds fonts and dark mode."""
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
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<meta name="x-apple-disable-message-reformatting">
<meta name="format-detection" content="telephone=no,date=no,address=no,email=no">
<title>{_e(title)}</title>
<link rel="stylesheet" href="{FONT_LINK}">
<style>{_dark_mode_css()}</style>
</head>
<body class="bg" bgcolor="{CANVAS}" style="{body_style}">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:{CANVAS};">{_e(preview)}</div>
<table role="presentation" class="bg" width="100%" bgcolor="{CANVAS}" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;background-color:{CANVAS};">
<tr><td class="wrap" align="center" valign="top" style="padding:32px 16px;margin:0;">
<!--[if mso]><table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" align="center"><tr><td><![endif]-->
<table role="presentation" class="sheet" width="600" bgcolor="{SHEET}" cellpadding="0" cellspacing="0" border="0" style="{sheet_style}">
<tr><td class="sheet px" align="left" bgcolor="{SHEET}" style="padding:40px 36px 36px;background-color:{SHEET};overflow-wrap:break-word;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;">
{''.join(rows)}
</table>
</td></tr>
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr>
</table>
</body>
</html>
"""


def render_email(issue, *, broadcast: bool = True, lesson=None) -> str:
    """HTML letter for Resend.

    Every light-mode style is inline. The head block adds the site fonts,
    the dark-mode palette, and a narrower phone padding. Gmail drops that
    block and keeps the inline fallbacks.
    Broadcasts unsubscribe through Resend's placeholder. Other renders use
    the archive URL so the control is not a dead link.
    """
    page = web_url(issue)
    unsub = html.escape(unsubscribe_href(broadcast), quote=True)
    preview = (issue.summary or "").strip() or "AI News by Niki Birkner"
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
        f'<p class="accent" style="{_type(18, 600, ACCENT, "1.2", tracking="-0.02em")}">AI News</p>'
        f'<p class="muted" style="{_type(13, 400, MUTED, "1.45", family=MONO)};margin-top:5px;">{_e(_byline(issue))}</p>'
        "</td></tr></table>"
    )
    subscribe = (
        f'<p class="muted" style="{_type(12, 400, MUTED, "1.45", family=MONO)}">'
        "Forwarded this? Subscribe at "
        + _link(
            html.escape(SUBSCRIBE_URL, quote=True),
            SUBSCRIBE_LABEL,
            size=12,
            color=ACCENT,
            family=MONO,
        )
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
    ]
    opened = True

    def add(inner: str, padding: str) -> None:
        nonlocal opened
        rows.append(_row(inner, padding, border=not opened))
        opened = True

    title = email_headline(issue)
    if title:
        add(
            f'<h1 class="fg" style="{_type(26, 600, INK, "1.25", tracking="-0.02em")};padding:0;">{_e(title)}</h1>',
            "24px 0 0",
        )
    stamp = window_line(issue)
    if stamp:
        add(
            f'<p class="muted" style="{_type(13, 400, MUTED, "1.45", family=MONO)}">{_e(stamp)}</p>',
            "10px 0 0" if opened else "24px 0 0",
        )
    summary = (issue.summary or "").strip()
    if summary:
        add(
            f'<p class="fg" style="{_type(17, 400, INK, "1.65")}">{_e(summary)}</p>',
            "16px 0 0" if opened else "24px 0 0",
        )
    add(
        f'<p style="margin:0;">{_link(_e(page), "Read this on the web")}</p>',
        "16px 0 26px" if opened else "24px 0 26px",
    )
    for item in issue.items:
        rows.append(_row(_story_html(item), "24px 0 20px", border=True))
    also = _also_line(issue)
    if also:
        rows.append(_row(
            f'<p class="muted" style="{_type(14, 400, MUTED, "1.6")}">{_e(also)}</p>',
            "22px 0",
            border=True,
        ))
    lesson_html = _lesson_html(lesson)
    if lesson_html:
        rows.append(_row(lesson_html, "24px 0 20px", border=True))
    rows.append(_row(
        "<p class=\"fg\" style=\"" + _type(14, 400, INK, "1.5") + "\">AI News by Niki Birkner</p>"
        + f'<p style="margin:12px 0 0;">{_link(_e(page), "Read this on the web", color=INK, underline=ACCENT, klass="footlink")}</p>'
        + f'<p style="margin:8px 0 0;">{_link(unsub, "Unsubscribe", color=INK, underline=ACCENT, klass="footlink")}</p>',
        "16px 16px",
        border=True,
        klass="foot",
        extra=f"background-color:{WARM};",
        bgcolor=WARM,
    ))
    return _wrap_email(email_subject(issue) or "AI News", preview, rows)


def render_plain(issue, *, broadcast: bool = True, lesson=None) -> str:
    """Plain-text twin of render_email. Same words, no markup."""
    lines = [
        f"Forwarded this? Subscribe at {SUBSCRIBE_LABEL}",
        SUBSCRIBE_URL,
        "",
        "AI News",
        _byline(issue),
        "",
    ]
    headline = email_headline(issue)
    if headline:
        lines.extend([headline, ""])
    stamp = window_line(issue)
    if stamp:
        lines.extend([stamp, ""])
    summary = (issue.summary or "").strip()
    if summary:
        lines.extend([summary, ""])
    lines.extend(["Read this on the web", web_url(issue), ""])
    for item in issue.items:
        lines.extend(["-" * 24, "", (item.what_happened or "").strip(), ""])
        for paragraph in _paragraphs(item.body):
            lines.extend([paragraph, ""])
        why = (item.why_it_matters or "").strip()
        if why:
            lines.extend([why, ""])
        label = _source_label(item.source_name, item.url)
        if label:
            lines.append(label)
        url = (item.url or "").strip()
        if url:
            lines.append(url)
        lines.append("")
    also = _also_line(issue)
    if also:
        lines.extend(["-" * 24, "", also, ""])
    lesson_title, lesson_summary, lesson_url = _lesson_bits(lesson)
    if lesson_title:
        lines.extend([
            "-" * 24,
            "",
            "This week's lesson",
            "",
            lesson_title,
            "",
            lesson_summary,
            "",
            "Read the lesson",
            lesson_url,
            "",
        ])
    lines.extend([
        "-" * 24,
        "",
        "AI News by Niki Birkner",
        web_url(issue),
        "",
        "Unsubscribe",
        unsubscribe_href(broadcast),
        "",
    ])
    return "\n".join(lines).rstrip() + "\n"


def render_lesson_email(lesson, *, broadcast: bool = True) -> str:
    """HTML letter for one AI Lessons issue. Same shell as AI News.

    Roboto is the title, the lesson title, and the body. Fragment Mono stays
    on the forward line, the byline, and the level. Broadcasts unsubscribe
    through Resend's placeholder. A test render uses the lesson URL.
    """
    page = str(getattr(lesson, "web_url", "") or "").strip()
    unsub = html.escape(UNSUBSCRIBE if broadcast else page, quote=True)
    subject = lesson_email_subject(lesson) or "AI Lessons"
    summary = " ".join(str(getattr(lesson, "summary", "") or "").split())
    preview = summary or "AI Lessons by Niki Birkner"
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
        f'<p class="muted" style="{_type(13, 400, MUTED, "1.45", family=MONO)};margin-top:5px;">'
        f"{_e(lesson_byline(lesson))}</p>"
        "</td></tr></table>"
    )
    subscribe = (
        f'<p class="muted" style="{_type(12, 400, MUTED, "1.45", family=MONO)}">'
        "Forwarded this? Subscribe at "
        + _link(
            html.escape(LESSONS_SUBSCRIBE_URL, quote=True),
            LESSONS_SUBSCRIBE_LABEL,
            size=12,
            color=ACCENT,
            family=MONO,
        )
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
    ]
    opened = True

    def add(inner: str, padding: str) -> None:
        nonlocal opened
        rows.append(_row(inner, padding, border=not opened))
        opened = True

    add(
        f'<h1 class="fg" style="{_type(26, 600, INK, "1.25", tracking="-0.02em")};padding:0;">{_e(subject)}</h1>',
        "24px 0 0",
    )
    level = " ".join(str(getattr(lesson, "level", "") or "").split())
    if level:
        add(
            f'<p class="muted" style="{_type(13, 400, MUTED, "1.45", family=MONO)}">{_e(level)}</p>',
            "10px 0 0",
        )
    if summary:
        add(
            f'<p class="fg" style="{_type(17, 400, INK, "1.65")}">{_e(summary)}</p>',
            "16px 0 0",
        )
    if page:
        add(
            f'<p style="margin:0;">{_link(_e(page), "Read this on the web")}</p>',
            "16px 0 26px",
        )
    title = " ".join(str(getattr(lesson, "title", "") or "").split())
    body_html = _lesson_body_html(str(getattr(lesson, "body", "") or ""))
    parts = []
    if title:
        parts.append(
            f'<h2 class="fg" style="{_type(18, 600, INK, "1.35", bottom=12, tracking="-0.015em")};padding:0;">'
            f"{_e(title)}</h2>"
        )
    if body_html:
        parts.append(body_html)
    if parts:
        rows.append(_row("".join(parts), "24px 0 20px", border=True))
    footer_web = ""
    if page:
        footer_web = (
            f'<p style="margin:12px 0 0;">{_link(_e(page), "Read this on the web", color=INK, underline=ACCENT, klass="footlink")}</p>'
        )
    rows.append(_row(
        "<p class=\"fg\" style=\"" + _type(14, 400, INK, "1.5") + "\">AI Lessons by Niki Birkner</p>"
        + footer_web
        + f'<p style="margin:8px 0 0;">{_link(unsub, "Unsubscribe", color=INK, underline=ACCENT, klass="footlink")}</p>',
        "16px 16px",
        border=True,
        klass="foot",
        extra=f"background-color:{WARM};",
        bgcolor=WARM,
    ))
    return _wrap_email(subject, preview, rows)


def render_lesson_plain(lesson, *, broadcast: bool = True) -> str:
    """Plain-text twin of render_lesson_email."""
    page = str(getattr(lesson, "web_url", "") or "").strip()
    unsub = UNSUBSCRIBE if broadcast else page
    summary = " ".join(str(getattr(lesson, "summary", "") or "").split())
    lines = [
        f"Forwarded this? Subscribe at {LESSONS_SUBSCRIBE_LABEL}",
        LESSONS_SUBSCRIBE_URL,
        "",
        "AI Lessons",
        lesson_byline(lesson),
        "",
    ]
    subject = lesson_email_subject(lesson)
    if subject:
        lines.extend([subject, ""])
    level = " ".join(str(getattr(lesson, "level", "") or "").split())
    if level:
        lines.extend([level, ""])
    if summary:
        lines.extend([summary, ""])
    if page:
        lines.extend(["Read this on the web", page, ""])
    title = " ".join(str(getattr(lesson, "title", "") or "").split())
    if title:
        lines.extend([title, ""])
    lines.extend(_lesson_body_plain(str(getattr(lesson, "body", "") or "")))
    lines.extend([
        "AI Lessons by Niki Birkner",
        page,
        "",
        "Unsubscribe",
        unsub,
        "",
    ])
    return "\n".join(lines).rstrip() + "\n"


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
