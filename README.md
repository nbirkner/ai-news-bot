# AI briefing

A standalone GitHub Actions job that posts a short AI briefing to a public Slack channel on Monday, Wednesday, and Friday morning. It reads public lab blogs, news wires, model hubs, and Exa web search. It does not use email, calendars, Notion, or any private notes.

If nothing important landed since the previous run, it posts a one-line quiet note instead of filling the channel.

Ranking and the write-up use Together. The default model is `zai-org/GLM-5.3` (structured output, strongest general instruct model on Together as of October 2026). Set `TOGETHER_MODEL` to override it. `meta-llama/Llama-3.3-70B-Instruct-Turbo` is a cheaper fallback. `TOGETHER_API_KEY` is the only model credential.

Every number, quote, and proper name in the lede and in each item has to appear in that item's source text (full text when we have it, otherwise the excerpt). A paraphrase can use different verbs. A figure, name, or quotation that is not in the source is dropped and that sentence falls back to the headline or a generic builder note.

## Setup

### 1. Put the repo on GitHub

Workflows run from this repository. Use the default branch after this change is merged.

### 2. Create a Slack incoming webhook for the public channel

The webhook URL is the channel. The bot cannot pick a channel later.

1. Open [api.slack.com/apps](https://api.slack.com/apps) and create an app in the company workspace.
2. Turn on **Incoming Webhooks**.
3. Click **Add New Webhook to Workspace**.
4. Choose the **public** channel that should receive the briefing. Check the channel name in that dialog before you allow it.
5. Copy the webhook URL. It looks like `https://hooks.slack.com/services/...`.

To move the briefing, create a new webhook for the other channel and replace the secret. Treat the URL like a password. If it was ever pasted into git, chat, or a doc, delete that webhook in Slack and create a new one. This repository's history contains an old Slack webhook URL. Revoke that webhook in the Slack app before relying on the channel, and do not reuse it.

### 3. Add Actions secrets

Repo **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Value |
| --- | --- |
| `TOGETHER_API_KEY` | A Together API key from [api.together.ai/settings/api-keys](https://api.together.ai/settings/api-keys) |
| `SLACK_WEBHOOK_URL` | The incoming webhook from step 2 |
| `EXA_API_KEY` | An Exa key from [dashboard.exa.ai/api-keys](https://dashboard.exa.ai/api-keys) |

`EXA_API_KEY` is optional. Without it the job uses the feeds only and says so in the log. With it, each run also searches Exa for model releases, pricing, inference and chips, open weights, policy, funding, agent launches and incidents, and data-center power. Searches are limited to the lookback window.

**Call cap.** Exa `/search` and `/contents` calls share one hard cap, default **30** per run. The log line is `Exa calls: N/30`. Override it with the `EXA_MAX_CALLS` environment variable, or with a repository variable of the same name (the workflow passes that variable through). If Exa errors or the account runs out of credits, the run keeps whatever Exa already returned and continues on the feeds.

Optional: repository variable `TOGETHER_MODEL` overrides the model in `config.yaml`.

No personal access token is required. Seen URLs and the last few briefings are stored in the Actions cache (`state/`), not in a commit on every run. The cache is written only after a real post. A dry run does not update it. If the cache is cold, the files committed in `state/` are the seed, and the lookback never exceeds 7 days.

### 4. Enable the workflow

On a new or forked repo, GitHub leaves Actions off until someone turns them on. Open the **Actions** tab and enable them if GitHub asks.

### 5. Dry-run before the first real post

**Actions → AI briefing → Run workflow**, leave **dry_run** checked, and run it. The log prints the briefing. Nothing is posted, and the seen-article list is not updated.

The workflow dispatch form also has:

- **since**: dry run only. An ISO start such as `2026-10-16T15:00:00Z`, or a span such as `80h`. This is `LOOKBACK`. It is ignored on a live post.
- **expect_file**: dry run only. A path in the repo, one expected headline per line. The log says which of those headlines were found, and which of the found ones were ranked into the briefing.

When the log looks right, run the workflow again with **dry_run** unchecked. That post is the first live message. After that, the schedule takes over.

Local equivalent, which also never posts:

```bash
python3 -m pip install -r requirements.txt
python3 dry_run.py --since 2026-10-16T15:00:00Z --expect tests/fixtures/window_2026_10_19_expected.txt
```

`DRY_RUN=1` or `--dry-run` on `python3 main_briefing.py` does the same thing. `LOOKBACK` is the same override as `--since`. A dry run ignores `SLACK_WEBHOOK_URL` even if it is set.

Discovery only (feeds, Exa, full text, and the shortlist the ranker would see). No Together call and no Slack call:

```bash
EXA_API_KEY=your-key python3 scripts/discovery_dry_run.py --since 2026-10-16T15:00:00Z
EXA_API_KEY=your-key python3 scripts/discovery_dry_run.py --since 80h --expect tests/fixtures/window_2026_10_19_expected.txt
```

Without `EXA_API_KEY` the script still prints feed candidates and logs `Exa calls: 0/30`.

## Schedule

| | |
| --- | --- |
| Days | Monday, Wednesday, Friday |
| Clock | 15:00 UTC |
| Pacific daylight (roughly March to November) | 8:00 AM |
| Pacific standard | 7:00 AM |

GitHub's scheduler is UTC and does not follow daylight saving, so winter runs are an hour earlier on the Pacific clock.

Each run covers items published since the previous successful post. The Monday run therefore includes the weekend. The first run, or a run with no saved timestamp, looks back 80 hours. A long outage never looks back more than 7 days. `--since` / `LOOKBACK` replaces that window for a dry run.

## What gets posted

A one-line lede, then four to seven items when the week supports it, and fewer when it does not. Items are ranked against each other for people building AI products and infrastructure, with the last five posted briefings in mind so a repeat title is skipped.

- frontier models, open weights, and price changes
- agents and agent security
- inference, chips, and power
- policy and major legal moves
- large deals

Each item is one sentence on what happened, one sentence on why it matters, and a link to the public source. The sentences keep prices, benchmark numbers, dates, and availability when those are in the source, including a caveat when the source calls a figure company-reported or unaudited. Customer stories, market blurbs, release candidates, and office openings are left out. Routine patch notes are included only when they change how teams serve models this week.

Sources live in `config.yaml`:

- OpenAI, Anthropic, Google DeepMind, the Google AI blog, Meta AI, Mistral, NVIDIA
- Reuters (latest-news sitemap, AI headlines only), Axios (AI items only), The Verge AI
- Politico technology, CNBC technology, TechCrunch AI, TechCrunch security (AI items only)
- Fortune's main feed, kept only when the headline is about AI (Fortune does not publish a working AI-section feed)
- DeepSeek, Qwen, and Moonshot model releases on Hugging Face, plus Hugging Face trending text models created in the window
- vLLM and SGLang GitHub releases
- arXiv (`cs.CL`, `cs.LG`, `cs.AI`, title keywords only)
- Exa search across the same themes, scoped to the window

To add a source, append an entry. `type` is `rss`, `html` (`parser: anthropic` or `parser: meta`), `reuters`, `arxiv`, or `huggingface`. `priority` decides which copy wins when two sources run the same story (lower number wins). `kind` is `lab`, `news`, `release`, or `paper`.

After dedupe, the top 15 candidates (configurable with `read_candidates`) are read in full. Exa contents fills in pages that block a direct fetch. The model ranks that shortlist. It sees the full extracted text, not a 500-character snippet.

## Memory

`state/recent_briefings.json` keeps the titles and links of the last five real posts. A dry run does not write it. On GitHub Actions the file lives in the Actions cache together with `state/seen_articles.json` and `state/last_briefing.json`, so a routine post does not open a commit.

## Tests

```bash
python3 -m pytest tests/ -v
```

Tests do not call Slack, Together, or Exa. HTTP is mocked.
