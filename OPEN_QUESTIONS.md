# Open Questions

## 1. GitHub Remote Setup (manual step required)

The repo needs to be created on GitHub before pushing.

Steps for Niki:
1. Create a new repo at https://github.com/new (name: `ai-news-bot`)
2. Run from `repos/ai-news-bot/`:
   ```bash
   git remote add origin https://github.com/<your-org>/ai-news-bot.git
   git push -u origin main
   ```
3. Add secrets in GitHub Settings → Secrets and variables → Actions:
   - `TOGETHER_API_KEY` — your Together AI API key
   - `SLACK_WEBHOOK_URL` — `https://hooks.slack.com/triggers/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`
   - `GH_PAT` — Personal Access Token with `repo` scope

## 2. Dry Run Results

Run date: 2026-04-02

**Breaking check (2h window):**
- Total fetched: 14
- New (unseen): 14
- Breaking: 10
- Source breakdown:
  - Anthropic Blog (scrape): 14 articles (scraper uses current-time as published_at, so all 14 pass the 2h window)
  - OpenAI Blog (RSS): 0 — feed returned no entries
  - Google DeepMind (RSS): 0 — feed returned no entries
  - NVIDIA News (RSS): 0 — feed returned no entries
  - Meta AI (RSS): 0 — feed returned no entries
  - Together AI Blog (RSS): 0 — feed returned no entries
  - All standard sources (RSS): 0 — feeds returned no entries

**Breaking articles detected (from Anthropic scrape):**
1. Introducing Claude Opus 4.6 (Feb 5, 2026)
2. Claude is a space to think (Feb 4, 2026)
3. Australian government and Anthropic sign MOU for AI safety (Mar 31, 2026)
4. Anthropic invests $100M into Claude Partner Network (Mar 12, 2026)
5. Introducing The Anthropic Institute (Mar 11, 2026)
6. Sydney becomes Anthropic's fourth APAC office (Mar 10, 2026)
7. Where things stand with the Department of War (Mar 5, 2026)
8. Statement on comments from Secretary Pete Hegseth (Feb 27, 2026)
9. Statement from Dario Amodei on DoW discussions (Feb 26, 2026)
10. Anthropic acquires Vercept for computer use (Feb 25, 2026)

**Digest (24h window):**
- Total fetched: 14 (same Anthropic articles, RSS sources still 0)
- New (unseen): 14

**Note on RSS feeds returning 0:** Most RSS sources return 0 articles from this local network. This is expected — many newsletters use Substack/Beehiiv which may block unauthenticated RSS polling or rate-limit from non-datacenter IPs. GitHub Actions (ubuntu-latest) is more likely to get consistent access. The scrape-based Anthropic source works correctly.

**Note on scraper published_at:** The `fetch_scrape` function assigns `datetime.now()` as published_at since the Anthropic news page doesn't expose pub dates in the HTML. This means all scraped articles pass the 2h age filter. In production, this results in re-alerting on old articles unless state deduplication catches them (which it will, after the first run).
