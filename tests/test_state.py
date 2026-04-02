import json
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from src.state import load_seen, save_seen, prune_old, mark_seen, is_seen


def test_load_seen_returns_empty_dict_when_file_missing(tmp_path):
    state_file = tmp_path / "seen.json"
    result = load_seen(state_file)
    assert result == {}


def test_load_seen_returns_existing_data(tmp_path):
    state_file = tmp_path / "seen.json"
    data = {"https://example.com/a": "2026-04-01T10:00:00+00:00"}
    state_file.write_text(json.dumps(data))
    result = load_seen(state_file)
    assert result == data


def test_save_seen_writes_json(tmp_path):
    state_file = tmp_path / "seen.json"
    seen = {"https://example.com/a": "2026-04-01T10:00:00+00:00"}
    save_seen(seen, state_file)
    assert json.loads(state_file.read_text()) == seen


def test_save_seen_creates_parent_dirs(tmp_path):
    state_file = tmp_path / "nested" / "dir" / "seen.json"
    save_seen({}, state_file)
    assert state_file.exists()


def test_prune_old_removes_entries_older_than_retention(tmp_path):
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=8)).isoformat()
    recent = (now - timedelta(days=1)).isoformat()
    seen = {
        "https://example.com/old": old,
        "https://example.com/recent": recent,
    }
    pruned = prune_old(seen, retention_days=7)
    assert "https://example.com/old" not in pruned
    assert "https://example.com/recent" in pruned


def test_prune_old_keeps_all_when_within_retention():
    now = datetime.now(timezone.utc)
    seen = {"https://example.com/a": (now - timedelta(days=3)).isoformat()}
    pruned = prune_old(seen, retention_days=7)
    assert len(pruned) == 1


def test_mark_seen_adds_url_with_timestamp():
    seen = {}
    result = mark_seen(seen, "https://example.com/a")
    assert "https://example.com/a" in result
    datetime.fromisoformat(result["https://example.com/a"])


def test_mark_seen_does_not_mutate_original():
    seen = {}
    result = mark_seen(seen, "https://example.com/a")
    assert seen == {}
    assert "https://example.com/a" in result


def test_is_seen_returns_true_for_known_url():
    seen = {"https://example.com/a": "2026-04-01T10:00:00+00:00"}
    assert is_seen(seen, "https://example.com/a") is True


def test_is_seen_returns_false_for_unknown_url():
    seen = {}
    assert is_seen(seen, "https://example.com/a") is False
