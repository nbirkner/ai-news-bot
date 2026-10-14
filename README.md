# AI briefing

A standalone GitHub Actions job that posts a short AI briefing to a public Slack channel on Monday, Wednesday, and Friday morning. It reads public lab blogs, news wires, inference release notes, and a narrow arXiv query. It does not use email, calendars, Notion, or any private notes.

If nothing important landed since the previous run, it posts a one-line quiet note instead of filling the channel.

Summaries are written by Together (`meta-llama/Llama-3.3-70B-Instruct-Turbo`, changeable in `config.yaml`). Every number, quote, and proper name in those lines has to appear in the source text. If the model adds something that is not there, that line is replaced with the headline or a generic builder note that states no new facts.

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

To move the briefing, create a new webhook for the other channel and replace the secret. Treat the URL like a password. If it was ever pasted into git, chat, or a doc, delete that webhook in Slack and create a new one.

### 3. Add two Actions secrets

Repo **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Value |
| --- | --- |
| `TOGETHER_API_KEY` | A Together API key from [api.together.ai/settings/api-keys](https://api.together.ai/settings/api-keys) |
| `SLACK_WEBHOOK_URL` | The incoming webhook from step 2 |

No personal access token is required. The workflow commits its seen-article list with the built-in `GITHUB_TOKEN`. If that commit is rejected with a 403, add an optional secret `GH_PAT` (fine-grained, contents write on this repo). The workflow uses it when it is present. You can delete `GH_PAT` if the default token push succeeds.

### 4. Enable the workflow

On a new or forked repo, GitHub leaves Actions off until someone turns them on. Open the **Actions** tab and enable them if GitHub asks.

### 5. Dry-run before the first real post

**Actions → AI briefing → Run workflow**, leave **dry_run** checked, and run it. The log prints the briefing. Nothing is posted, and the seen-article list is not updated.

When the log looks right, run the workflow again with **dry_run** unchecked. That post is the first live message. After that, the schedule takes over.

Local equivalent, which also never posts:

```bash
python3 -m pip install -r requirements.txt
python3 dry_run.py
```

`DRY_RUN=1` or `--dry-run` on `python3 main_briefing.py` does the same thing. A dry run ignores `SLACK_WEBHOOK_URL` even if it is set.

## Schedule

| | |
| --- | --- |
| Days | Monday, Wednesday, Friday |
| Clock | 15:00 UTC |
| Pacific daylight (roughly March to November) | 8:00 AM |
| Pacific standard | 7:00 AM |

GitHub's scheduler is UTC and does not shift for daylight saving, so winter runs are an hour earlier on the Pacific clock.

Each run covers items published since the previous successful post. The Monday run therefore includes the weekend. The first run, or a run with no saved timestamp, looks back 80 hours. A long outage never looks back more than 7 days.

The job saves `state/seen_articles.json` and `state/last_briefing.json` after a successful post so the next run does not repeat items. A failed run does not advance that state.

## What gets posted

Three to six items, sometimes fewer, ranked for people building AI products and infrastructure:

- frontier models
- agents
- inference and hardware
- safety and security
- policy and major legal moves
- large deals

Each item is one sentence on what happened, one sentence on why it matters, and a link to the public source. Customer stories, market blurbs, release candidates, and office openings are left out. arXiv papers are included only when the title is unusually close to serving, safety, or a named model family.

Sources live in `config.yaml`:

- OpenAI, Anthropic, Google DeepMind, the Google AI blog, Meta AI, Mistral, NVIDIA
- Reuters (latest-news sitemap, AI headlines only), Axios (AI items only), The Verge AI
- vLLM and SGLang GitHub releases
- arXiv (`cs.CL`, `cs.LG`, `cs.AI`, title keywords only)

To add a source, append an entry. `type` is `rss`, `html` (`parser: anthropic` or `parser: meta`), `reuters`, or `arxiv`. `priority` decides which copy wins when two sources run the same story (lower number wins). `kind` is `lab`, `news`, `release`, or `paper`.

## Tests

```bash
python3 -m pytest tests/ -v
```

Tests do not call Slack or Together.
