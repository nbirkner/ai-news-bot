import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

DEFAULT_STATE_FILE = Path("state/seen_articles.json")


def load_seen(state_file: Path = DEFAULT_STATE_FILE) -> dict:
    if not state_file.exists():
        return {}
    return json.loads(state_file.read_text())


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
