"""MDX archive pages and the email preview for one issue."""
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.summarize import topic_for

PACIFIC = ZoneInfo("America/Los_Angeles")

_TAGS = {
    "policy": "policy",
    "safety": "safety",
    "deal": "funding",
    "model": "models",
    "infra": "infrastructure",
    "agents": "agents",
    "paper": "research",
    "release": "releases",
    "other": "ai",
}


@dataclass
class Issue:
    title: str
    date: str
    summary: str
    tags: list
    lede: str
    items: list
    idempotency_key: str
    articles: list = field(default_factory=list)


def issue_date(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=PACIFIC)
    return now.astimezone(PACIFIC).strftime("%Y-%m-%d")


def idempotency_key_for(now: datetime) -> str:
    """One key per Pacific calendar day, which is the Mon/Wed/Fri morning slot."""
    return f"issue-{issue_date(now)}"


def issue_title(now: datetime) -> str:
    local = now.astimezone(PACIFIC) if now.tzinfo else now.replace(tzinfo=PACIFIC)
    local = local.astimezone(PACIFIC)
    return f"AI news, {local.strftime('%A, %B')} {local.day}"


def tags_for(articles) -> list:
    tags = []
    for article in articles or []:
        tag = _TAGS.get(topic_for(article), "ai")
        if tag not in tags:
            tags.append(tag)
    specific = [tag for tag in tags if tag != "ai"]
    return specific or ["ai"]


def yaml_quote(value: str) -> str:
    escaped = (value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{escaped}"'


def mdx_text(value: str) -> str:
    text = value or ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace("{", "&#123;")


def build_issue(now: datetime, lede: str, items: list, articles=None) -> Issue:
    articles = list(articles or [])
    summary = (lede or "").strip()
    if not summary and items:
        summary = items[0].what_happened
    if len(summary) > 220:
        summary = summary[:220].rsplit(" ", 1)[0].rstrip(",;: ")
    return Issue(
        title=issue_title(now),
        date=issue_date(now),
        summary=summary,
        tags=tags_for(articles),
        lede=(lede or "").strip(),
        items=list(items),
        idempotency_key=idempotency_key_for(now),
        articles=articles,
    )


def render_mdx(issue: Issue) -> str:
    tags = "\n".join(f"  - {tag}" for tag in issue.tags) or "  - ai"
    blocks = []
    if issue.lede:
        blocks.append(mdx_text(issue.lede))
        blocks.append("")
    for item in issue.items:
        blocks.append(f"## {mdx_text(item.what_happened)}")
        blocks.append("")
        blocks.append(mdx_text(item.why_it_matters))
        blocks.append("")
        label = mdx_text(item.source_name or "Source")
        blocks.append(f"[{label}]({item.url})")
        blocks.append("")
    body = "\n".join(blocks).strip() + "\n"
    return (
        "---\n"
        f"title: {yaml_quote(issue.title)}\n"
        f"date: {issue.date}\n"
        f"summary: {yaml_quote(issue.summary)}\n"
        "tags:\n"
        f"{tags}\n"
        "---\n\n"
        f"{body}"
    )


def render_email_markdown(issue: Issue) -> str:
    """Buttondown markdown. Must not start with YAML frontmatter."""
    lines = []
    if issue.lede:
        lines.extend([issue.lede, ""])
    for item in issue.items:
        lines.append(f"**{item.what_happened}**")
        lines.append("")
        lines.append(item.why_it_matters)
        lines.append("")
        lines.append(f"[{item.source_name}]({item.url})")
        lines.append("")
    lines.append("The archive of this briefing is at https://www.nikibirkner.com/newsletter/.")
    text = "\n".join(lines).strip() + "\n"
    if text.startswith("---"):
        text = "AI news\n\n" + text
    return text


def render_email_html(issue: Issue) -> str:
    """Standalone preview using the site's type and colors."""
    def esc(value: str) -> str:
        return (
            (value or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    items_html = []
    for item in issue.items:
        items_html.append(
            "<section style=\"padding:18px 0;border-top:1px solid #EAEAE8\">"
            f"<p style=\"margin:0 0 8px;font-weight:600\">{esc(item.what_happened)}</p>"
            f"<p style=\"margin:0 0 8px;color:#333\">{esc(item.why_it_matters)}</p>"
            f"<p style=\"margin:0;font-family:'Fragment Mono',ui-monospace,monospace;font-size:12px\">"
            f"<a href=\"{esc(item.url)}\" style=\"color:#C2410C;text-decoration:none\">{esc(item.source_name)}</a>"
            "</p></section>"
        )
    lede = f"<p style=\"margin:0 0 8px;color:#333\">{esc(issue.lede)}</p>" if issue.lede else ""
    tags = " · ".join(esc(tag) for tag in issue.tags)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(issue.title)}</title>
</head>
<body style="margin:0;background:#FFFFFF;color:#111111;font:15px/1.6 'Schibsted Grotesk',ui-sans-serif,system-ui,sans-serif">
<main style="max-width:720px;margin:0 auto;padding:48px 24px 64px">
<p style="margin:0 0 8px;font-family:'Fragment Mono',ui-monospace,monospace;font-size:12px;color:#6B6B6B">{esc(issue.date)} · {tags}</p>
<h1 style="margin:0 0 12px;font-size:17px;font-weight:600;line-height:1.4">{esc(issue.title)}</h1>
{lede}
{''.join(items_html)}
<p style="margin:24px 0 0;padding-top:18px;border-top:1px solid #EAEAE8;font-family:'Fragment Mono',ui-monospace,monospace;font-size:12px;color:#6B6B6B">
Archive: <a href="https://www.nikibirkner.com/newsletter/" style="color:#C2410C;text-decoration:none">nikibirkner.com/newsletter</a>
</p>
</main>
</body>
</html>
"""


def archive_path(issue: Issue) -> str:
    return f"src/content/newsletter/{issue.date}.mdx"


def write_artifacts(directory, issue: Issue, violations=()) -> None:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    mdx = render_mdx(issue)
    html = render_email_html(issue)
    (directory / f"{issue.date}.mdx").write_text(mdx)
    (directory / f"{issue.date}-email.html").write_text(html)
    (directory / "sample-issue.mdx").write_text(mdx)
    (directory / "sample-email.html").write_text(html)
    if violations:
        report = "\n".join(f"{item.code}: {item.detail}" for item in violations) + "\n"
    else:
        report = "guardrails: pass\n"
    (directory / "guardrails.txt").write_text(report)


def frontmatter(issue_mdx: str) -> dict:
    """Small reader for tests. Not a general YAML parser."""
    match = re.match(r"^---\n(.*?)\n---\n", issue_mdx, re.S)
    if not match:
        return {}
    data = {}
    tags = []
    in_tags = False
    for line in match.group(1).splitlines():
        if in_tags:
            if line.startswith("  - "):
                tags.append(line[4:].strip())
                continue
            in_tags = False
        if line.startswith("tags:"):
            in_tags = True
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip().strip('"')
    data["tags"] = tags
    return data
