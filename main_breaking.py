"""
Entrypoint for the breaking-check GitHub Actions workflow.
"""
import os
import sys
import yaml
from dataclasses import replace
from src.fetch import fetch_all
from src.classify import is_breaking
from src.summarize import summarize_breaking
from src.slack import format_breaking, post_to_slack
from src.state import load_seen, save_seen, prune_old, mark_seen, is_seen


def main():
    config = yaml.safe_load(open("config.yaml"))
    api_key = os.environ["TOGETHER_API_KEY"]
    webhook_url = os.environ["SLACK_WEBHOOK_URL"]
    model = config["together_model"]

    seen = load_seen()
    seen = prune_old(seen, retention_days=config["state_retention_days"])

    articles = fetch_all(config, max_age_hours=config["breaking_lookback_hours"])
    print(f"Fetched {len(articles)} total articles", file=sys.stderr)

    breaking_count = 0
    for article in articles:
        if is_seen(seen, article.url):
            continue
        if not is_breaking(article, config):
            continue

        summary, why_it_matters = summarize_breaking(article, api_key, model)
        enriched = replace(
            article,
            is_breaking=True,
            summary=summary,
            why_it_matters=why_it_matters,
        )

        payload = format_breaking(enriched)
        post_to_slack(payload, webhook_url)
        seen = mark_seen(seen, article.url)
        breaking_count += 1
        print(f"Posted breaking: {article.title}", file=sys.stderr)

    save_seen(seen)
    print(f"Done. Posted {breaking_count} breaking articles.")


if __name__ == "__main__":
    main()
