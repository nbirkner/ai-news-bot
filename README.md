# AI briefing

One public AI news issue, generated once, then rendered as site MDX and as email HTML. The Monday issue is broadcast with Resend. The Tuesday lesson is a separate broadcast, to a separate audience. Slack posting and the site publish are separate and are not turned on.

The copy is a general briefing for people building with AI. It does not take a Together product, customer, revenue, or competitor angle, and it does not use internal names. Sources are public pages with public URLs.

`TOGETHER_API_KEY` must be Niki's personal Together key. Do not put a Together company key in that variable. There is no second key and no fallback.

## Newsletter dry run

This command never posts to Slack, never calls Resend, and never commits to `nbirkner/site`. It writes `./out/`.

```bash
python3 -m pip install -r requirements.txt
export TOGETHER_API_KEY=...   # personal key, not a company key
python3 main_newsletter.py --dry-run --window-end 2026-10-12
python3 main_newsletter.py --dry-run --window-end 2026-10-16
python3 main_newsletter.py --dry-run --window-end 2026-10-19
```

A date means 15:00 UTC on that day (the Monday / Wednesday / Friday slot). The window opens at the previous slot, so `--window-end 2026-10-19` covers Friday 16 Oct 15:00 UTC through Monday 19 Oct 15:00 UTC.

### Weekly sources file

The Monday newsletter (Monday 9 AM PT) looks first for a committed file, `sources/<YYYY-MM-DD>.json`. The date is the window end on the Pacific calendar, so that Monday run reads `sources/2026-10-19.json` when `--window-end` is `2026-10-19`. The schema and an example are in [`sources/README.md`](sources/README.md).

The file is a JSON list. Each item is an object with this schema:

| Field | Required | Value |
| --- | --- | --- |
| `title` | yes | non-empty string |
| `url` | yes | absolute `http` or `https` URL |
| `source` | yes | non-empty string, the publication name |
| `published` | yes | ISO-8601 date (`YYYY-MM-DD`) or date-time (`YYYY-MM-DDThh:mm:ssZ`, or the same form with a numeric offset) |
| `summary` | yes | non-empty string, the public facts |
| `reflection` | no | string. A short note on the story. Omit the field when there is none. |

Other fields are ignored. If any item is invalid, the whole file is rejected. An empty list is valid and adds no stories.

When the file is present and valid, those items are candidate stories and RSS is still read. Ranking and writing still go through the model. When the file is missing or invalid, the run uses RSS only and says so in the log. Mon/Wed/Fri briefing runs (`main_briefing.py`) do not read this file. They use RSS only.

Exa is off unless `EXA_ENABLED=1`. With that set, and with `EXA_API_KEY` present, Exa search and contents share one per-run cap, `EXA_MAX_CALLS`, default **6**.

Each run writes:

| File | What |
| --- | --- |
| `out/YYYY-MM-DD.mdx` | Frontmatter `title`, `date`, `summary`, `tags`, then the stories |
| `out/YYYY-MM-DD.html` | The same stories as a 600px email, plus a web link and `{{{RESEND_UNSUBSCRIBE_URL}}}` |
| `out/YYYY-MM-DD.txt` | Plain text of the same issue, with the same web link and unsubscribe line |
| `out/YYYY-MM-DD.usage.txt` | Model, tokens per call, totals, and dollar cost |

If a guardrail blocks the issue, the command writes `out/YYYY-MM-DD.blocked.txt` instead of the MDX and HTML, and exits 2.

Without `TOGETHER_API_KEY` the run still builds an issue from the article text and logs `llm_calls: 0`. Exa stays off unless `EXA_ENABLED=1`. A missing weekly sources file, or an invalid one, uses the feeds only. The dry run does not read `RESEND_API_KEY` or `RESEND_AUDIENCE_ID`.

### Weekly send

Mondays at 9:00 AM America/Los_Angeles. The workflow runs two UTC crons, `0 16 * * 1` and `0 17 * * 1`. A guard step continues only when the current hour in America/Los_Angeles is 9, so daylight saving does not move the send off 9:00.

The window ends at 15:00 UTC that Monday. It opens seven days earlier, the previous Monday at 15:00 UTC.

`--send` creates a Resend broadcast to the audience in `RESEND_AUDIENCE_ID`, from `Niki Birkner <ainews@nikibirkner.com>`. The subject is `email_subject()` (`AI News, October 19, 2026` for that issue). The HTML is `render_email(broadcast=True)` and the text is `render_plain(broadcast=True)`. Resend is then told to send that broadcast, using `RESEND_API_KEY`.

The send does not run when the issue has no stories, or when that issue date is already in `state/sent_issues.json`. The seen-article list, the recent briefings, and that sent-date file are written only after Resend accepts the send. The Actions cache is saved only then. A dry run, a test email, an empty issue, and a failed send leave the cache as it was.

Slack is not posted. The site repo is not updated. `SLACK_WEBHOOK_URL` and `SITE_REPO_TOKEN` are not used by this workflow.

## Ingest from the Learning bot

The Learning bot is the content source for the weekly files. The Monday send path (9:00 AM America/Los_Angeles, `--send`, `--test-to`, and `state/sent_issues.json`) is unchanged.

### News roundup

`scripts/ingest_news.py` reads `/cursor/stores/bot/ai-news-weekly/YYYY-MM-DD.md` (override the directory with `NEWS_WEEKLY_DIR` or pass `--roundup`). The file starts with an italic briefing line, a window line, a lede, then one block per story: an italic headline, the public facts, a reflection line, and a Slack link `<https://example.com/story|Publication>`.

It writes `sources/YYYY-MM-DD.json` in the weekly sources schema (`title`, `url`, `source`, `published`, `summary`, and `reflection` when the story has one). `published` is the file date. One broken story rejects the file. A missing roundup writes nothing and exits 2.

`--render` also writes `out/YYYY-MM-DD.mdx`, `.html`, and `.txt` in the Monday email template. The inbox title stays `AI News, October 19, 2026` for that date. The command does not send.

```bash
python3 scripts/ingest_news.py --date 2026-10-19 --render
```

### Lesson file

`python3 main_newsletter.py --mode lesson --dry-run --date 2026-10-20` reads `/cursor/stores/bot/ai-lessons-weekly/YYYY-MM-DD-slug.md` (`LESSON_WEEKLY_DIR` overrides the directory). The file is the public lesson format, including frontmatter `title`, `date`, `level` (`Foundations`, `Systems`, or `Frontier`), `slug`, and `summary`. A valid file is copied to `out/YYYY-MM-DD-slug.mdx`, `.html`, and `.txt`. The HTML uses the same letter as AI News: Roboto for the title and the body, Fragment Mono on the forward line, the byline, and the level. The subject is `AI Lessons, October 20, 2026` for that date. A missing file, a bad level, or a draft that skips a required section writes only `out/YYYY-MM-DD.blocked.txt` containing `BLOCKED: <reason>`.

`--post` and `--publish` exit 2. `--dry-run` never calls Resend. `--send` broadcasts only when `RESEND_LESSONS_AUDIENCE_ID` is set to a segment that is not the AI News audience. A missing id exits 2 and does not call Resend. The AI News id is not a fallback. An id equal to `RESEND_AUDIENCE_ID`, or any id that starts with `a2f59f11`, exits 2 and does not call Resend. `--test-to` sends one message through `/emails` and does not broadcast to either audience.

When the Monday newsletter renders and that week's lesson file is present and valid, the issue gains a **This week's lesson** section (title, summary, and the lesson link). No file means the section is left out and the issue still renders.

### Tuesday send

Tuesdays at 9:00 AM America/Los_Angeles. `.github/workflows/lessons.yml` runs two UTC crons, `0 16 * * 2` and `0 17 * * 2`. A guard continues only when the America/Los_Angeles hour is 9.

The scheduled run passes `--mode lesson --send`. It reads `RESEND_API_KEY` and `RESEND_LESSONS_AUDIENCE_ID`. It does not read `RESEND_AUDIENCE_ID`. From is `Niki Birkner <ailessons@nikibirkner.com>` unless `RESEND_LESSONS_FROM` is set. The subject is `lesson_email_subject()` (`AI Lessons, October 20, 2026` for that lesson). The HTML is `render_lesson_email(broadcast=True)` and the text is `render_lesson_plain(broadcast=True)`.

The send does not run when the lesson file is missing or invalid, when the audience id is missing or is the AI News audience, or when that lesson date is already in `state/sent_lessons.json`. That file is written only after Resend accepts the broadcast. A dry run, a test email, and a failed send leave it unchanged. The lessons cache is separate from the Monday cache.

Slack is not posted. The site repo is not updated.

A manual run is a dry run unless **dry_run** is unchecked. **test_to** sends one email to that address instead of the broadcast. It is ignored while dry_run is checked.

### AI Lessons audience (one-time)

Resend calls the list a segment. The id still goes in `RESEND_LESSONS_AUDIENCE_ID`, which is the name the site signup expects. This repo does not create the segment during a send. Creating one does not send email.

Do this before merging the Tuesday workflow, so the first scheduled run has an audience. Until the secret is set, the Tuesday job fails closed and sends nothing.

1. In the [Resend segments dashboard](https://resend.com/segments), create a segment named `AI Lessons`. Leave the AI News segment alone. Its id starts with `a2f59f11`. The empty General segment is not the lessons list.
2. Or, with a Resend key that can manage segments:

   ```bash
   RESEND_API_KEY=re_... python3 scripts/create_lessons_audience.py
   ```

   The command prints the segment id and does not send mail. If the id matches `RESEND_AUDIENCE_ID` or starts with `a2f59f11`, it exits 2 and tells you not to use it.

3. GitHub repo **Settings → Secrets and variables → Actions** on `nbirkner/ai-news-bot`:

| Secret | Value |
| --- | --- |
| `RESEND_LESSONS_AUDIENCE_ID` | The new segment id. Not the AI News id. |
| `RESEND_LESSONS_FROM` | Optional. Blank uses `Niki Birkner <ailessons@nikibirkner.com>`. `nikibirkner.com` is already the verified domain used by `ainews@nikibirkner.com`. |
| `RESEND_API_KEY` | Already set for the Monday send. The Tuesday workflow reads the same secret. |

4. Vercel → the nikibirkner.com project → **Settings → Environment Variables** (Production, and Preview if signup should work there):

| Name | Value |
| --- | --- |
| `RESEND_LESSONS_AUDIENCE_ID` | The same segment id. |
| `RESEND_API_KEY` | Already set for the news confirm flow. |

The lessons signup route (`POST /api/lessons/subscribe`, or whichever route the site already uses) must add the contact to `RESEND_LESSONS_AUDIENCE_ID` only. If that variable is empty, the route should fail. It must not write the address into `RESEND_AUDIENCE_ID`. Mirror the news double opt-in: create the contact as unsubscribed, email a confirm link, and subscribe on confirm. This bot does not edit the site repo.

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

Stories that fail the one-liner or URL check are dropped. The other checks block the whole issue. A real Monday send also refuses an issue date already listed in `state/sent_issues.json`. A real Tuesday send refuses a lesson date already listed in `state/sent_lessons.json`. A dry run does not write either file.

## Site changes

This repo does not edit `nbirkner/site`. The MDX files are meant for `src/content/newsletter/` on that Astro site. See the end of this file for the page and signup work.

A standalone GitHub Actions job will post the same issue to Slack later. It reads public lab blogs, news wires, model hubs, and Exa web search. It does not use email, calendars, Notion, or any private notes.

If nothing important landed since the previous run, it posts a one-line quiet note instead of filling the channel.

Ranking and the write-up use Together. The default model is `zai-org/GLM-5.3` (structured output, strongest general instruct model on Together as of October 2026). Set `TOGETHER_MODEL` to override it. `meta-llama/Llama-3.3-70B-Instruct-Turbo` is a cheaper fallback. `TOGETHER_API_KEY` is the only model credential.

Every number, quote, and proper name in the lede and in each item has to appear in that item's source text (full text when we have it, otherwise the excerpt) or in its brief reflection. A paraphrase can use different verbs. A figure, name, or quotation that is not in the source or the reflection is dropped and that sentence falls back to the headline or a generic builder note. The writing prompt tells the model to use only facts from the provided public sources plus brief reflection, and not to invent facts.

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
| `EXA_API_KEY` | An Exa key from [dashboard.exa.ai/api-keys](https://dashboard.exa.ai/api-keys). Used only when `EXA_ENABLED=1`. |
| `RESEND_API_KEY` | A Resend API key that can create and send broadcasts. The Monday send and the Tuesday lesson send read this. A dry run does not. |
| `RESEND_AUDIENCE_ID` | The Resend segment id for AI News. The Monday broadcast is sent to it. The Tuesday lesson workflow does not read it. |
| `RESEND_LESSONS_AUDIENCE_ID` | The Resend segment id for AI Lessons. Required for the Tuesday broadcast. Never the AI News id. Same value as the Vercel env of the same name. |
| `RESEND_LESSONS_FROM` | Optional. From address for the lesson. Blank uses `Niki Birkner <ailessons@nikibirkner.com>`. |

`EXA_API_KEY` is optional. Exa is off unless the environment variable `EXA_ENABLED` is `1` (the workflow passes the `EXA_ENABLED` repository variable, default `0`). The Monday newsletter otherwise uses `sources/<YYYY-MM-DD>.json` plus RSS, and falls back to RSS when that file is missing or invalid. Mon/Wed/Fri briefings use RSS only. With Exa enabled, each run also searches for model releases, pricing, inference and chips, open weights, policy, funding, agent launches and incidents, and data-center power. Searches are limited to the lookback window.

**Call cap.** When Exa is on, `/search` and `/contents` calls share one hard cap, default **6** per run. The log line is `Exa calls: N/6`. Override it with the `EXA_MAX_CALLS` environment variable, or with a repository variable of the same name (the workflow passes that variable through, default `6`). If Exa errors or the account runs out of credits, the run keeps whatever Exa already returned and continues on the feeds and the weekly sources file.

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

Discovery only (feeds, optional Exa, full text, and the shortlist the ranker would see). No Together call and no Slack call. This script does not read the weekly sources file.

```bash
python3 scripts/discovery_dry_run.py --since 2026-10-16T15:00:00Z
EXA_ENABLED=1 EXA_API_KEY=your-key python3 scripts/discovery_dry_run.py --since 80h --expect tests/fixtures/window_2026_10_19_expected.txt
```

Without `EXA_ENABLED=1` the script still prints feed candidates and logs `Exa calls: 0/6`.

## Schedule

| | Monday AI News | Tuesday AI Lessons |
| --- | --- | --- |
| Clock | 9:00 AM America/Los_Angeles, year-round | 9:00 AM America/Los_Angeles, year-round |
| UTC crons | `0 16 * * 1` and `0 17 * * 1` | `0 16 * * 2` and `0 17 * * 2` |
| Guard | Continue only when the America/Los_Angeles hour is 9 | Same guard |
| Audience | `RESEND_AUDIENCE_ID` | `RESEND_LESSONS_AUDIENCE_ID` |

GitHub's scheduler is UTC and does not follow daylight saving. The two crons fire an hour apart, and the guard drops the one that is not 9:00 on the Pacific clock. A manual run is not subject to the guard.

The scheduled window ends at 15:00 UTC that Monday and opens at 15:00 UTC the Monday before. A local dry run without `--window-start` still opens at the previous Monday, Wednesday, or Friday slot.

## What gets posted

The Monday workflow sends the AI News email. The Tuesday workflow sends the AI Lessons email to its own audience. Neither posts to Slack or commits the MDX to the site. A manual run with **dry_run** checked sends nothing.

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
- The Monday newsletter also reads `sources/<YYYY-MM-DD>.json` (see above) before the feeds
- Exa search across the same themes, scoped to the window, only when `EXA_ENABLED=1`

To add a source, append an entry. `type` is `rss`, `html` (`parser: anthropic` or `parser: meta`), `reuters`, `arxiv`, or `huggingface`. `priority` decides which copy wins when two sources run the same story (lower number wins). `kind` is `lab`, `news`, `release`, or `paper`.

After dedupe, the top candidates (configurable with `read_candidates`, 20 in `config.yaml`) are read in full. When `EXA_ENABLED=1`, Exa contents fills in pages that block a direct fetch, inside the same call cap. The model ranks that shortlist. It sees the full extracted text, not a 500-character snippet, and it may use a story's brief reflection. It must not invent facts.

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
