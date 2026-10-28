"""Create the AI Lessons Resend segment and print its id.

Does not send email. Does not broadcast. If a segment named "AI Lessons"
already exists, its id is printed and nothing new is created.

The id must not be the AI News audience (RESEND_AUDIENCE_ID, or any id
that starts with a2f59f11). This script exits 2 in that case.

    RESEND_API_KEY=re_... python3 scripts/create_lessons_audience.py

Stdout is the segment id only. Put that value in:

- GitHub Actions secret RESEND_LESSONS_AUDIENCE_ID on nbirkner/ai-news-bot
- Vercel env RESEND_LESSONS_AUDIENCE_ID on the nikibirkner.com project

Optional GitHub secret RESEND_LESSONS_FROM overrides
Niki Birkner <ailessons@nikibirkner.com>.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.resend_send import (  # noqa: E402
    LESSONS_FROM,
    SendError,
    create_lessons_segment,
)


def main(argv=None) -> int:
    del argv
    api_key = (os.environ.get("RESEND_API_KEY") or "").strip()
    news = (os.environ.get("RESEND_AUDIENCE_ID") or "").strip()
    try:
        segment_id = create_lessons_segment(api_key=api_key, news_audience_id=news)
    except SendError as exc:
        print(str(exc), file=sys.stderr)
        print(
            "Create a segment named AI Lessons in the Resend dashboard "
            "(Segments, not the AI News audience), then set "
            "RESEND_LESSONS_AUDIENCE_ID to its id.",
            file=sys.stderr,
        )
        return 2
    print(segment_id)
    print(
        "Set RESEND_LESSONS_AUDIENCE_ID to the id above in GitHub Actions "
        "and in the Vercel project for nikibirkner.com. "
        f"Optional from address RESEND_LESSONS_FROM, default {LESSONS_FROM}. "
        "Do not copy the AI News audience.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
