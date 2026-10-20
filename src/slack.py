import json
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from src.clean import slack_escape
from src.models import BriefingItem

PACIFIC = ZoneInfo("America/Los_Angeles")


def _when(moment: datetime) -> str:
    local = moment.astimezone(PACIFIC)
    return f"{local.strftime('%A, %B')} {local.day}"


def _clock(moment: datetime) -> str:
    local = moment.astimezone(PACIFIC)
    hour = str(int(local.strftime("%I")))
    return f"{local.strftime('%a %b')} {local.day}, {hour}:{local.strftime('%M %p')}"


def slack_link(url: str, label: str) -> str:
    safe_url = slack_escape(url)
    safe_label = slack_escape(label).replace("|", "")
    return f"<{safe_url}|{safe_label}>"


def render_briefing(now: datetime, since: datetime, items: list, lede: str = "") -> str:
    header = f"*AI briefing, {_when(now)}*"
    if not items:
        return (
            f"{header}\n"
            f"Quiet since {_when(since)}. No model release, infra change, or "
            "policy news worth flagging from the sources we watch.\n"
        )
    # A lede replaces the "Since ..." line. An empty lede keeps the old layout.
    stamp = f"Window: {_clock(since)} to {_clock(now)} PT"
    if lede:
        lines = [header, stamp, slack_escape(lede), ""]
    else:
        lines = [header, stamp, ""]
    for item in items:
        lines.append(f"*{slack_escape(item.what_happened)}*")
        if getattr(item, "body", ""):
            lines.append(slack_escape(item.body))
        lines.append(slack_escape(item.why_it_matters))
        links = [(item.url, item.source_name)]
        for extra in getattr(item, "also", ()) or ():
            if isinstance(extra, (tuple, list)) and len(extra) == 2:
                links.append((extra[0], extra[1]))
        lines.append(" | ".join(slack_link(url, label) for url, label in links))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def slack_payload(text: str) -> dict:
    return {"text": text, "unfurl_links": False, "unfurl_media": False}


def post_to_slack(payload: dict, webhook_url: str) -> None:
    response = requests.post(
        webhook_url,
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    response.raise_for_status()
