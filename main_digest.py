"""
Entrypoint for the daily-digest GitHub Actions workflow.
"""
import os
import sys
import yaml
from src.fetch import fetch_all
from src.summarize import summarize_digest
from src.slack import format_digest, post_to_slack
from src.state import load_seen, save_seen, prune_old, mark_seen, is_seen


def main():
    config = yaml.safe_load(open("config.yaml"))
    api_key = os.environ["TOGETHER_API_KEY"]
    webhook_url = os.environ["SLACK_WEBHOOK_URL"]
    model = config["together_model"]

    seen = load_seen()
    seen = prune_old(seen, retention_days=config["state_retention_days"])

    articles = fetch_all(config, max_age_hours=config["digest_lookback_hours"])
    new_articles = [a for a in articles if not is_seen(seen, a.url)]
    print(f"Found {len(new_articles)} new articles for digest", file=sys.stderr)

    if not new_articles:
        print("No new articles. Skipping digest post.")
        return

    capped = new_articles[:config["max_digest_items"]]
    summarized = summarize_digest(capped, api_key, model)

    payload = format_digest(summarized)
    if payload:
        post_to_slack(payload, webhook_url)
        print(f"Posted digest with {len(summarized)} articles.")

    for article in new_articles:
        seen = mark_seen(seen, article.url)

    save_seen(seen)


if __name__ == "__main__":
    main()
