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
