import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

DEFAULT_STATE_FILE = Path("state/seen_articles.json")


def load_seen(state_file: Path = DEFAULT_STATE_FILE) -> dict:
    if not state_file.exists():
        return {}
    try:
        data = json.loads(state_file.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_seen(seen: dict, state_file: Path = DEFAULT_STATE_FILE) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(seen, indent=2, sort_keys=True))


def prune_old(seen: dict, retention_days: int = 7) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    return {
        url: ts for url, ts in seen.items()
        if datetime.fromisoformat(ts) >= cutoff
    }


def mark_seen(seen: dict, url: str) -> dict:
    result = dict(seen)
    result[url] = datetime.now(timezone.utc).isoformat()
    return result


def is_seen(seen: dict, url: str) -> bool:
    return url in seen


def load_last_run(state_file: Path = Path("state/last_briefing.json")) -> datetime | None:
    if not state_file.exists():
        return None
    try:
        payload = json.loads(state_file.read_text())
    except json.JSONDecodeError:
        return None
    raw = payload.get("last_briefing_at")
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def save_last_run(when: datetime, state_file: Path = Path("state/last_briefing.json")) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    payload = {"last_briefing_at": when.astimezone(timezone.utc).isoformat()}
    state_file.write_text(json.dumps(payload, indent=2) + "\n")


def load_recent(state_file: Path = Path("state/recent_briefings.json"), *, keep: int = 5) -> list:
    """Last posted briefings, oldest first. Missing or corrupt files yield []."""
    if not state_file.exists():
        return []
    try:
        data = json.loads(state_file.read_text())
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        rows = data.get("briefings") or []
    elif isinstance(data, list):
        rows = data
    else:
        return []
    cleaned = [row for row in rows if isinstance(row, dict)]
    return cleaned[-keep:]


def save_recent(briefings: list, state_file: Path = Path("state/recent_briefings.json"), *, keep: int = 5) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"briefings": list(briefings)[-keep:]}
    state_file.write_text(json.dumps(payload, indent=2) + "\n")


def remember_briefing(when: datetime, articles: list, state_file: Path, *, keep: int = 5) -> None:
    """Append one real post. Dry runs must not call this."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    rows = load_recent(state_file, keep=keep)
    rows.append({
        "posted_at": when.isoformat(),
        "items": [
            {"title": article.title, "url": article.url}
            for article in articles
            if getattr(article, "title", "") and getattr(article, "url", "")
        ],
    })
    save_recent(rows, state_file, keep=keep)


def recent_items(briefings: list) -> list:
    items = []
    for briefing in briefings:
        for item in briefing.get("items") or []:
            if isinstance(item, dict) and item.get("title") and item.get("url"):
                items.append({"title": item["title"], "url": item["url"]})
    return items
