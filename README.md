# AI briefing

One public AI news issue, generated once, then rendered as site MDX and as email HTML. The Monday issue is broadcast with Resend. Slack posting and the site publish are separate and are not turned on.

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
| `out/YYYY-MM-DD.html` | The same stories as a 600px email, plus a web link and `{{{RESEND_UNSUBSCRIBE_URL}}}` |
| `out/YYYY-MM-DD.txt` | Plain text of the same issue, with the same web link and unsubscribe line |
| `out/YYYY-MM-DD.usage.txt` | Model, tokens per call, totals, and dollar cost |

If a guardrail blocks the issue, the command writes `out/YYYY-MM-DD.blocked.txt` instead of the MDX and HTML, and exits 2.

Without `TOGETHER_API_KEY` the run still builds an issue from the article text and logs `llm_calls: 0`. Without `EXA_API_KEY` it uses the feeds only. The dry run does not read `RESEND_API_KEY` or `RESEND_AUDIENCE_ID`.

### Weekly send

Mondays at 9:00 AM America/Los_Angeles. The workflow runs two UTC crons, `0 16 * * 1` and `0 17 * * 1`. A guard step continues only when the current hour in America/Los_Angeles is 9, so daylight saving does not move the send off 9:00.

The window ends at 15:00 UTC that Monday. It opens seven days earlier, the previous Monday at 15:00 UTC.

`--send` creates a Resend broadcast to the audience in `RESEND_AUDIENCE_ID`, from `Niki Birkner <ainews@nikibirkner.com>`. The subject is `email_subject()` (`AI News, October 19, 2026` for that issue). The HTML is `render_email(broadcast=True)` and the text is `render_plain(broadcast=True)`. Resend is then told to send that broadcast, using `RESEND_API_KEY`.

The send does not run when the issue has no stories, or when that issue date is already in `state/sent_issues.json`. The seen-article list, the recent briefings, and that sent-date file are written only after Resend accepts the send. The Actions cache is saved only then. A dry run, a test email, an empty issue, and a failed send leave the cache as it was.

Slack is not posted. The site repo is not updated. `SLACK_WEBHOOK_URL` and `SITE_REPO_TOKEN` are not used by this workflow.

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

Stories that fail the one-liner or URL check are dropped. The other checks block the whole issue. A real send also refuses an issue date already listed in `state/sent_issues.json`. A dry run does not write that file.

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
| `RESEND_API_KEY` | A Resend API key that can create and send broadcasts. The Monday send reads this. A dry run does not. |
| `RESEND_AUDIENCE_ID` | The Resend audience (segment) id the broadcast is sent to. Same secret the site signup uses. |

`EXA_API_KEY` is optional. Without it the job uses the feeds only and says so in the log. With it, each run also searches Exa for model releases, pricing, inference and chips, open weights, policy, funding, agent launches and incidents, and data-center power. Searches are limited to the lookback window.

**Call cap.** Exa `/search` and `/contents` calls share one hard cap, default **30** per run. The log line is `Exa calls: N/30`. Override it with the `EXA_MAX_CALLS` environment variable, or with a repository variable of the same name (the workflow passes that variable through). If Exa errors or the account runs out of credits, the run keeps whatever Exa already returned and continues on the feeds.

Optional: repository variable `TOGETHER_MODEL` overrides the model in `config.yaml`.

No personal access token is required. Seen URLs, sent issue dates, and the last few briefings are stored in the Actions cache (`state/`), not in a commit on every run. The cache is written only after a successful broadcast. A dry run does not update it. If the cache is cold, the files committed in `state/` are the seed, and the lookback never exceeds 7 days.

### 4. Enable the workflow

On a new or forked repo, GitHub leaves Actions off until someone turns them on. Open the **Actions** tab and enable them if GitHub asks.

### 5. Dry-run before the first real post

**Actions → AI briefing → Run workflow**, leave **dry_run** checked, and run it. The log prints the issue and the artifact has the HTML. Nothing is sent, and the seen-article list is not updated.

The dispatch form also has:

- **window_end**: `YYYY-MM-DD`. The window ends at 15:00 UTC that day. Leave it blank to use the Monday of the current America/Los_Angeles week.
- **dry_run**: checked by default. A manual run sends only when this is unchecked.
- **test_to**: an email address. When dry_run is unchecked, the run sends one email to that address via Resend `/emails` instead of the broadcast. Unsubscribe in that copy points at the archive, because Resend fills `{{{RESEND_UNSUBSCRIBE_URL}}}` on a broadcast only. A test email does not mark the issue sent and it does not save the cache. The field is ignored while dry_run is checked.

The Monday schedule sends the broadcast on its own. Uncheck **dry_run** only when a manual run should send. That send is the email, not a Slack post and not a site commit.

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
| Day | Monday |
| Clock | 9:00 AM America/Los_Angeles, year-round |
| UTC crons | `0 16 * * 1` and `0 17 * * 1` |
| Guard | Continue only when the America/Los_Angeles hour is 9 |

GitHub's scheduler is UTC and does not follow daylight saving. The two crons fire an hour apart, and the guard drops the one that is not 9:00 on the Pacific clock. A manual run is not subject to the guard.

The scheduled window ends at 15:00 UTC that Monday and opens at 15:00 UTC the Monday before. A local dry run without `--window-start` still opens at the previous Monday, Wednesday, or Friday slot.

## What gets posted

The Monday workflow sends the email. It does not post to Slack and it does not commit the MDX to the site. A manual run with **dry_run** checked sends nothing.

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

`state/recent_briefings.json` keeps the titles and links of the last five real sends. `state/sent_issues.json` records each issue date that was broadcast, so the same date is not sent twice. A dry run and a test email do not write these files. On GitHub Actions they live in the Actions cache together with `state/seen_articles.json` and `state/last_briefing.json`. The cache is saved only after a successful broadcast. A routine send does not open a commit.

## Tests

```bash
python3 -m pytest tests/ -v
```

Tests do not call Slack, Together, or Exa. HTTP is mocked. Pull requests and pushes to `main` run this command in GitHub Actions (`.github/workflows/tests.yml`), with no API keys.

## Site archive (not edited from this repo)

`nbirkner/site` is Astro + MDX and is live at [www.nikibirkner.com](https://www.nikibirkner.com). The bot writes MDX; the site still needs:

1. A content collection `src/content/newsletter/` whose schema accepts `title` (string), `date` (date), `summary` (string), and `tags` (list of strings). Drop each `out/YYYY-MM-DD.mdx` in that folder. The filename is the slug.
2. `/newsletter`, an index of issues, newest first. Each row is the date, the title, and the summary, linking to `/newsletter/YYYY-MM-DD`. Use the same type and spacing as the rest of the site (the monospace aside, the quiet calendar palette). Do not invent a second visual system.
3. `/newsletter/[slug]`, one page per issue that renders the MDX. The email's "Read this on the web" link points at `https://nikibirkner.com/ai-news/YYYY-MM-DD`.
4. A signup box on the index and on the home page, matching the existing form styling if the site already has inputs, otherwise a single email field and a button in the site's current type.
5. A small Vercel function, not a client-side call to Resend. `POST /api/newsletter/subscribe` with `{ email }` creates a Resend contact in `RESEND_AUDIENCE_ID` as unsubscribed and emails a confirm link. `GET /api/newsletter/confirm?token=...` sets the contact to subscribed. That is the double opt-in. The function reads `RESEND_API_KEY` from Vercel env. The browser never sees the key.
6. Resend domain verification for `nikibirkner.com` (SPF, DKIM, and the return-path Resend shows in the dashboard) for mail from `ainews@nikibirkner.com`.

The Monday send broadcasts the HTML this command writes. It does not commit the MDX to `src/content/newsletter/`. That publish step stays off until it is approved on its own. The model credential remains the personal `TOGETHER_API_KEY` only.
