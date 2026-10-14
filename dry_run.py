"""
Print a briefing from live public sources. Never posts to Slack.

TOGETHER_API_KEY is optional. Without it, each item uses the source headline
and a short builder note that does not add facts. With it, Together writes
the two lines and they are kept only when they stay grounded in the source.
"""
import os

os.environ["DRY_RUN"] = "1"

from main_briefing import main  # noqa: E402


if __name__ == "__main__":
    main()
