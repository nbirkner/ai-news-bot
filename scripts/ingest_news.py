"""Turn a Learning-bot news roundup into sources JSON and, optionally, the email.

From the repo root:

    python3 scripts/ingest_news.py --date 2026-10-19 --render
    python3 scripts/ingest_news.py --date 2026-10-19 --roundup path.md --sources out/sources.json

The default roundup is ``$NEWS_WEEKLY_DIR/YYYY-MM-DD.md``, or
``/cursor/stores/bot/ai-news-weekly/YYYY-MM-DD.md``. A missing or invalid
roundup writes nothing and exits 2. This command does not send email.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.lesson_file import week_lesson  # noqa: E402
from src.newsletter import render_email, render_mdx, render_plain  # noqa: E402
from src.roundup import IngestError, load_roundup, news_weekly_dir, roundup_path, sources_json, issue_from_roundup  # noqa: E402


def _flag(argv, name: str) -> str:
    if name not in argv:
        return ""
    index = argv.index(name)
    if index + 1 >= len(argv):
        raise SystemExit(f"{name} needs a value")
    return argv[index + 1]


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    date = _flag(argv, "--date").strip()
    if not date:
        print(
            "Usage: python3 scripts/ingest_news.py --date YYYY-MM-DD "
            "[--roundup PATH] [--sources PATH] [--render] [--out DIR]",
            file=sys.stderr,
        )
        return 2
    raw_roundup = _flag(argv, "--roundup").strip()
    path = Path(raw_roundup) if raw_roundup else roundup_path(date, news_weekly_dir())
    try:
        parsed = load_roundup(path, date)
        payload = sources_json(parsed["sources"])
    except IngestError as exc:
        print(exc.reason, file=sys.stderr)
        return 2
    raw_sources = _flag(argv, "--sources").strip()
    sources_path = Path(raw_sources) if raw_sources else Path("sources") / f"{date}.json"
    sources_path.parent.mkdir(parents=True, exist_ok=True)
    sources_path.write_text(payload, encoding="utf-8")
    print(f"Wrote {sources_path}", file=sys.stderr)
    if "--render" not in argv:
        return 0
    lesson_dir = _flag(argv, "--lesson-dir").strip()
    lesson = week_lesson(date, Path(lesson_dir) if lesson_dir else None)
    issue = issue_from_roundup(parsed)
    out = Path(_flag(argv, "--out").strip() or os.environ.get("OUT_DIR") or "out")
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{date}.mdx").write_text(render_mdx(issue, lesson=lesson), encoding="utf-8")
    (out / f"{date}.html").write_text(render_email(issue, lesson=lesson), encoding="utf-8")
    (out / f"{date}.txt").write_text(render_plain(issue, lesson=lesson), encoding="utf-8")
    print(f"Wrote {out / (date + '.html')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
