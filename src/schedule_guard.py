"""Write the Monday schedule decision for GitHub Actions. Stdlib only. No send."""

import os
import sys
from datetime import datetime

from src.schedule import PACIFIC, decide


def current_moment() -> datetime:
    """The workflow clock. SCHEDULE_NOW is how tests pin it. Actions leaves it unset."""
    raw = (os.environ.get("SCHEDULE_NOW") or "").strip()
    if raw:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=PACIFIC)
        return parsed
    return datetime.now(PACIFIC)


def _write(path: str, text: str) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)


def main() -> int:
    event = (os.environ.get("EVENT_NAME") or os.environ.get("GITHUB_EVENT_NAME") or "").strip()
    decision = decide(current_moment(), event)
    proceed = "true" if decision.proceed else "false"
    print(f"proceed={proceed} reason={decision.reason}")
    _write(
        (os.environ.get("GITHUB_OUTPUT") or "").strip(),
        f"proceed={proceed}\nwindow_end={decision.window_end}\nreason={decision.reason}\n",
    )
    _write(
        (os.environ.get("GITHUB_STEP_SUMMARY") or "").strip(),
        f"Monday send guard: proceed={proceed}. {decision.reason}\n",
    )
    return decision.exit_code


if __name__ == "__main__":
    sys.exit(main())
