"""
Dry run — verifies fetching and classification work without posting to Slack.
Set TOGETHER_API_KEY if you want to test summarization too.
"""
import os
import sys
import yaml
from src.fetch import fetch_all
from src.classify import classify_articles
from src.state import load_seen, prune_old, is_seen

config = yaml.safe_load(open("config.yaml"))

seen = load_seen()
seen = prune_old(seen, retention_days=config["state_retention_days"])

# Test breaking check sources (2h window)
print("=== BREAKING CHECK (2h window) ===", file=sys.stderr)
articles = fetch_all(config, max_age_hours=config["breaking_lookback_hours"])
classified = classify_articles(articles, config)
new = [a for a in classified if not is_seen(seen, a.url)]
breaking = [a for a in new if a.is_breaking]
print(f"Total fetched: {len(articles)}", file=sys.stderr)
print(f"New (unseen): {len(new)}", file=sys.stderr)
print(f"Breaking: {len(breaking)}", file=sys.stderr)
for a in breaking:
    print(f"  BREAKING: {a.title} ({a.source_name})", file=sys.stderr)

# Test digest sources (24h window)
print("\n=== DIGEST (24h window) ===", file=sys.stderr)
articles_24h = fetch_all(config, max_age_hours=config["digest_lookback_hours"])
new_24h = [a for a in articles_24h if not is_seen(seen, a.url)]
print(f"Total fetched: {len(articles_24h)}", file=sys.stderr)
print(f"New (unseen): {len(new_24h)}", file=sys.stderr)
for a in new_24h[:5]:
    print(f"  {a.source_name}: {a.title[:80]}", file=sys.stderr)

print("\nDry run complete. No Slack messages sent.")
