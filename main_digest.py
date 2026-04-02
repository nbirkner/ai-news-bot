"""
Entrypoint for the daily-digest GitHub Actions workflow.
"""
import os
import sys
import yaml
from src.fetch import fetch_all
from src.classify import classify_articles, prioritize_articles
from src.summarize import summarize_digest
from src.slack import format_digest, post_to_slack
from src.state import load_seen, save_seen, prune_old, mark_seen, is_seen


def main():
    config = yaml.safe_load(open("config.yaml"))
    api_key = os.environ["TOGETHER_API_KEY"]
    webhook_url = os.environ["SLACK_WEBHOOK_URL"]
    model = config["together_model"]
    top_n = config.get("max_digest_items", 7)

    seen = load_seen()
    seen = prune_old(seen, retention_days=config["state_retention_days"])

    articles = fetch_all(config, max_age_hours=config["digest_lookback_hours"])
    new_articles = [a for a in articles if not is_seen(seen, a.url)]
    print(f"Found {len(new_articles)} new articles for digest", file=sys.stderr)

    if not new_articles:
        print("No new articles. Skipping digest post.")
        return

    # Classify first so is_breaking flags are set (used in priority scoring)
    classified = classify_articles(new_articles, config)

    # Summarize all candidates so relevant_to_together is set before prioritizing
    # Cap at 2x top_n to avoid excessive API calls while still scoring a good pool
    pool = classified[:top_n * 2]
    summarized_pool = summarize_digest(pool, api_key, model)

    # Now prioritize: Together-relevant + tier-1 + keyword matches rise to the top
    prioritized = prioritize_articles(summarized_pool, config, top_n=top_n)
    summarized = prioritized

    payload = format_digest(summarized)
    if payload:
        post_to_slack(payload, webhook_url)
        print(f"Posted digest with {len(summarized)} articles.")

    for article in new_articles:
        seen = mark_seen(seen, article.url)

    save_seen(seen)


if __name__ == "__main__":
    main()
