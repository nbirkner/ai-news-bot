# AI briefing

One public AI news issue, generated once, then rendered as site MDX and as email HTML. Slack uses the same issue object; posting to Slack is not turned on yet.

The copy is a general briefing for people building with AI. It does not take a Together product, customer, revenue, or competitor angle, and it does not use internal names. Sources are public pages with public URLs.

`TOGETHER_API_KEY` must be Niki's personal Together key. Do not put a Together company key in that variable. There is no second key and no fallback.

## Newsletter dry run

This command never posts to Slack, never calls Resend, and never commits to `nbirkner/site`. It writes `./out/`.

```bash
python3 -m pip install -r requirements.txt
export TOGETHER_API_KEY=...   # personal key, not a company key
export EXA_API_KEY=...
python3 main_newsletter.py --dry-run --window-end 2026-10-12
python3 main_newsletter.py --dry-run --window-end 2026-10-16
python3 main_newsletter.py --dry-run --window-end 2026-10-19
```

A date means 15:00 UTC on that day (the Monday / Wednesday / Friday slot). The window opens at the previous slot, so `--window-end 2026-10-19` covers Friday 16 Oct 15:00 UTC through Monday 19 Oct 15:00 UTC.

Each run writes:

| File | What |
| --- | --- |
| `out/YYYY-MM-DD.mdx` | Frontmatter `title`, `date`, `summary`, `tags`, then the stories |
| `out/YYYY-MM-DD.html` | The same stories, plus a web link and `{{{RESEND_UNSUBSCRIBE_URL}}}` |
| `out/YYYY-MM-DD.usage.txt` | Model, tokens per call, totals, and dollar cost |

If a guardrail blocks the issue, the command writes `out/YYYY-MM-DD.blocked.txt` instead of the MDX and HTML, and exits 2.

Without `TOGETHER_API_KEY` the run still builds an issue from the article text and logs `llm_calls: 0`. Without `EXA_API_KEY` it uses the feeds only. `RESEND_API_KEY` and `RESEND_AUDIENCE_ID` are not read by this command.

### Cost lines in the usage log

Together GLM-5.3 (`zai-org/GLM-5.3`), from [together.ai/pricing](https://www.together.ai/pricing) on 19 Oct 2026: **$1.40 / 1M input tokens**, **$4.40 / 1M output tokens**. The log multiplies the token totals by those rates and by 13 for a Monday / Wednesday / Friday month.

Exa, from [exa.ai/docs/admin/pricing](https://exa.ai/docs/admin/pricing): search type `auto` is **$7 / 1,000 requests** ($0.007 each, this bot asks for 5 results). Contents text is **$1 / 1,000 pages** ($0.001 each). The free tier is **$10 of credits each month**. The log shows the run cost, the 13-run month, and what remains after that credit.

## Guardrails

Publishing stops, and this dry run writes a blocked file, when:

- a story has no public `http` or `https` URL
- the text mentions Together internals, customers, revenue, competitors, or other non-public material
- the body is a generic one-liner such as "It adds new features" (fewer than three concrete sentences)
- the issue is empty or too short, or it repeats the last issue's URLs
- the text is unsafe or contains a secret
- the model reply fails the issue schema (headline, body of at least three sentences, why it matters, integer index)
- a second send would land in the same Monday / Wednesday / Friday slot (checked only on a real send; this command never sends)

Stories that fail the one-liner or URL check are dropped. The other checks block the whole issue.

## Site changes

This repo does not edit `nbirkner/site`. The MDX files are meant for `src/content/newsletter/` on that Astro site. See the end of this file for the page and signup work.

A standalone GitHub Actions job will post the same issue to Slack later. It reads public lab blogs, news wires, model hubs, and Exa web search. It does not use email, calendars, Notion, or any private notes.

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
| `TOGETHER_API_KEY` | Niki's **personal** Together API key from [api.together.ai/settings/api-keys](https://api.together.ai/settings/api-keys). Not a Together company key. |
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

The Monday / Wednesday / Friday schedule is paused. The workflow only runs when someone starts it, and it runs `main_newsletter.py --dry-run`. Nothing is sent.

A lede, then four to seven stories when the window supports it, and fewer when it does not. Stories are ranked for people building with AI, with the last five issues in mind so a repeat link is skipped. Slack, the site, and the email are three renders of that same issue.

- frontier models, open weights, and price changes
- agents and agent security
- inference, chips, and power
- policy and major legal moves
- large deals

Each story is a specific headline, three to five sentences of facts and numbers from the full article, one line on why it matters for people building with AI, and the public source link. Prices, dates, and availability stay when they are in the source, including a caveat when the source calls a figure company-reported or unaudited. Customer stories, market blurbs, release candidates, and office openings are left out. A release note that only says it adds new features is dropped.

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

## Site archive (not edited from this repo)

`nbirkner/site` is Astro + MDX and is live at [www.nikibirkner.com](https://www.nikibirkner.com). The bot writes MDX; the site still needs:

1. A content collection `src/content/newsletter/` whose schema accepts `title` (string), `date` (date), `summary` (string), and `tags` (list of strings). Drop each `out/YYYY-MM-DD.mdx` in that folder. The filename is the slug.
2. `/newsletter`, an index of issues, newest first. Each row is the date, the title, and the summary, linking to `/newsletter/YYYY-MM-DD`. Use the same type and spacing as the rest of the site (the monospace aside, the quiet calendar palette). Do not invent a second visual system.
3. `/newsletter/[slug]`, one page per issue that renders the MDX. The email's "Read this on the web" link points at `https://www.nikibirkner.com/newsletter/YYYY-MM-DD`.
4. A signup box on the index and on the home page, matching the existing form styling if the site already has inputs, otherwise a single email field and a button in the site's current type.
5. A small Vercel function, not a client-side call to Resend. `POST /api/newsletter/subscribe` with `{ email }` creates a Resend contact in `RESEND_AUDIENCE_ID` as unsubscribed and emails a confirm link. `GET /api/newsletter/confirm?token=...` sets the contact to subscribed. That is the double opt-in. The function reads `RESEND_API_KEY` from Vercel env. The browser never sees the key.
6. Resend domain verification for `nikibirkner.com` (SPF, DKIM, and the return-path Resend shows in the dashboard) before any broadcast from `newsletter@nikibirkner.com`. The dry run does not send, so this can wait until a send command exists.

When a send path is added later, it should broadcast the same HTML this command writes, to that audience, and commit the same MDX to `src/content/newsletter/` with a GitHub token that can push to `nbirkner/site`. It must keep using the personal `TOGETHER_API_KEY` only.
