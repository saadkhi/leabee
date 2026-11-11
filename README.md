# Leabee

Worldwide lead finder for remote services (software, automation, AI, content, design, marketing, data, VA work…).
It collects **people and companies that are publicly asking for those services**, scores them 0–100, and shows them in a
dashboard you can filter, work through, and export.

## Sources

| Source | How | Notes |
|---|---|---|
| **TinyFish Search** | `api.search.tinyfish.ai` | ~300 queries/run across Reddit, Facebook, X, LinkedIn, Quora, Upwork, Freelancer, PeoplePerHour, Indie Hackers, HN, Product Hunt… in 30+ countries and 15 languages. Free up to 12,000 searches/day. |
| **TinyFish Fetch** | `api.fetch.tinyfish.ai` | `enrich` step pulls the full page of top leads to find budgets and contact details. Free up to 1,000 URLs/day. |
| Reddit | public RSS feeds | 34 subreddits + 28 site-wide search phrases. Reddit rate-limits, so this is the slowest source (~6–10 min). |
| Hacker News | Algolia API | monthly *Freelancer? Seeking freelancer?* and *Who is hiring?* threads + intent search |
| Job boards | RemoteOK, Remotive, We Work Remotely, Himalayas | remote openings, filtered to sellable service roles |
| Mastodon | public hashtag timelines | #hiring, #freelancejob, … |

Facebook, LinkedIn and X are read **only through public search-result snippets** (TinyFish Search), never logged-in scraping.

## Setup

```bash
python -m pip install -r requirements.txt
```

Create `.env` (git-ignored) with your key:

```
TINYFISH_API_KEY=your_key_here
```

## Use

```bash
python -m leabee collect                 # all sources, then dashboard shows results
python -m leabee collect -s tinyfish --enrich 100
python -m leabee collect -s reddit,boards
python -m leabee serve                   # dashboard at http://127.0.0.1:8765
python -m leabee list --min-score 70 --contact
python -m leabee export -o leads.csv --min-score 50
python -m leabee stats
python -m leabee watch --every 60        # re-collect every hour
```

The dashboard has a **Collect new leads** button, filters (service, country, platform, age, score, has-contact),
per-lead status (new → contacted → replied → won/lost/ignored), notes and CSV export.

## Features

* **Leads page** (`/`): filters (service, country, language, platform, type, status, age, score, AI verdict, has-contact,
  follow-ups due), saved filters (★ chips), bulk select → set status / export selected, per-lead notes, follow-up date and deal value.
* **Draft reply** button: short DM, longer email and follow-up templates per service. Set `LEABEE_YOUR_NAME` in `.env`.
  Nothing is ever sent automatically; you copy it and reply on the original post.
* **Board** (`/board`): drag leads between New / Contacted / Replied / Won / Lost. Moving to Contacted sets a follow-up
  3 days out; moving to Won asks for the deal value.
* **Dashboard** (`/insights`): categories, types, score bands, platforms, countries, languages, 7-day category trends,
  platform performance (worked / replied / won / win rate / revenue), follow-ups due and recent runs. Every bar links to the filtered list.
* **Better country detection**: besides named places it uses currency (₹, £, PKR…), email/site domain, phone code, timezone and language.
* **AI second opinion** (optional): `python -m leabee ai-review`, uses the free LLM setup below. Borderline leads (score 35–75) get a
  buyer / maybe / reject verdict and a one-line summary; rejects are hidden, confirmed buyers get +5.
* **Alerts**: new leads scoring 80+ are pushed to Telegram, Discord or email (see `.env.example`); `python -m leabee alert`
  or automatically after each collection. The first run only baselines existing leads.
* **Scheduler**: `python -m leabee serve --auto-collect 120` (or `schedule.enabled: true` in `config.yaml`) collects in the
  background. Scheduled runs take a rotating slice of Reddit (`reddit.per_run`) so each run is short; manual runs sweep everything.
* **GitHub issues** source (bounties / "looking for a developer"), `GITHUB_TOKEN` optional.
* **Backups**: a daily copy of the database in `data/backups` (14 kept); `python -m leabee backup` on demand.
* **Password**: set `LEABEE_PASSWORD` in `.env` to require HTTP Basic login (any username). Do this before exposing it online.
* **Run anywhere**: `run.bat` on Windows, or `docker compose up -d` (data persists in `./data`).

## Run everything on the web, free (Vercel + Neon + GitHub Actions)

Your PC is not needed after setup, so you can wipe the project folder whenever you like.

| Piece | Does | Free tier |
|---|---|---|
| **Neon** | the shared, persistent Postgres database | yes |
| **Vercel** | the dashboard: leads, board, dashboard, drafts, edits, the Collect button | Hobby (personal use) |
| **GitHub Actions** | collects leads every 6 hours and when you press Collect; AI review and alerts run here too | yes |

No lead data is ever committed to git, so the repository can stay **public**.
Every secret lives in **GitHub Actions secrets** or **Vercel environment variables**; nothing sensitive is in the repo.

**Copy your existing leads first** (before cleaning your folder): put your Neon connection string in a local `.env` as
`DATABASE_URL=...`, then run `python -m leabee migrate-to-remote`. It is safe to re-run. After that every CLI command uses Neon.

### Where each variable goes

| Variable | Vercel env var | GitHub Actions secret | Notes |
|---|:-:|:-:|---|
| `DATABASE_URL` | yes | yes | Neon connection string. On Vercel use the **pooled** one (host has `-pooler`); either works on GitHub |
| `LEABEE_PASSWORD` | **yes (required)** | | Dashboard login (any username). The site refuses to serve without it |
| `GITHUB_DISPATCH_TOKEN` | yes | | Fine-grained token for this repo, permission **Actions: Read and write**; powers the Collect button |
| `GITHUB_REPO` | yes | | `owner/repo`, e.g. `saadkhi/leabee` |
| `LEABEE_YOUR_NAME` | optional | | Signs your reply drafts |
| `TINYFISH_API_KEY` | | **yes** | TinyFish search and fetch |
| `OPENROUTER_API_KEY` | | optional (free) | AI review and AI-written replies on free OpenRouter models |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO` | | optional | Alerts for new hot leads (any one channel is enough) |

`GITHUB_TOKEN` (for the GitHub issues source) is provided to Actions automatically. Add secrets under repo → Settings →
Secrets and variables → Actions; add env vars under the Vercel project → Settings → Environment Variables, then redeploy.

### Deploy
1. Import the repo at vercel.com/new (no build settings; `vercel.json` routes everything to `api/index.py`), add the Vercel variables, deploy.
2. Open the site, log in, press **Collect new leads** (or wait up to 6 hours). The Actions tab shows the run.

Notes: Reddit often blocks GitHub's servers; Leabee stops asking a blocked host after 3 refusals and still finds Reddit posts
through TinyFish. Tables are created automatically on first connect (an advisory lock keeps simultaneous cold starts safe).
Match regions for speed: Neon in `us-east` pairs with Vercel's default region. The dashboard makes about 20 small queries per load.

**Fallbacks**: without `DATABASE_URL` the Vercel app serves a read-only snapshot (`python -m leabee snapshot`, see
`leabee/serverless.py`), and `python -m leabee publish` can commit that snapshot to a **private** repo. `python -m leabee backup`
pulls a local SQLite copy of the Neon database.

## How leads are judged (scoring v2)

**Step 1: what is this post?** Only real client gigs are scored. Everything else is dropped with a reason: employee job ads
(`role`, unless `keep_roles: true`), sellers, job seekers, advice/discussion posts, filled posts, co-founder/unpaid requests,
promotions, job lists, aggregator spam, recruiter/talent-network pages, sales and customer-service roles, homework.
Outbound businesses from Google Maps are `prospect`s with their own rules.

**Step 2: score 0-100, five parts, every point explained** (click "Why 82?" on a lead):

| Part | Max | Meaning |
|---|---|---|
| Intent | 35 | how explicit and paid the request is (marketplace project, [HIRING]/[TASK], clear hire phrases, pay signal) |
| Fit | 25 | names a service and concrete skills; uses your `profile:` (services, skills, avoid, languages, countries) |
| Value | 15 | stated budget or rate, ongoing or high-volume work |
| Reach | 15 | direct contact (email, WhatsApp, Telegram, booking link) beats "reply on the platform" |
| Freshness | 10 | recent posts win (post time is also read from "posted Nov 3" and "4d ago" text) |

Grades: **A** 80+ reply now, **B** 65-79 strong, **C** 50-64 worth a look, **D** below 50. Each lead also gets a plain-language **next action**
and structured **details** (company, website, pay type, hours, duration, deadline, experience, tech stack, volume, timezone).
Set your `profile:` in `config.yaml` to make scoring specific to what you sell.

### Measured accuracy (hand-labelled, never committed)
`python -m leabee eval` re-scores hand-labelled posts in `data/eval/*.json` (git-ignored, they contain real people's posts).
`python -m leabee dump-raw` collects raw feeds and `python -m leabee eval-sample` draws a fresh blind sample to label.
Results with the current rules, at score >= 50 (precision = share of returned leads that are real buyers):

| Set | Posts | Real buyers | Precision | Recall |
|---|---|---|---|---|
| A (used for tuning) | 100 | 42 | 97% | 88% |
| B, C (seen during tuning) | 100 / 80 | 13 / 21 | 80% / 81% | 92% / 81% |
| D (first measured blind: 39% precision, then fixed) | 60 | 13 | 65% | 85% |

Treat 65-80% as the realistic precision on unseen raw feeds at grade C; grade B and above is cleaner. Raw feeds are mostly job ads and noise.

## Extractors and tools (maximum detail, cheapest first)

`python -m leabee tools` shows what is ready. Order: plain HTTP, then **Crawl4AI** (open source; free local browser; set `LEABEE_BROWSER_CHANNEL=msedge` or
`chrome` to reuse the browser you already have), then TinyFish Fetch (optional, free tier), then **Browser Use** (open source, MIT) as a last resort,
opt-in with `extractors.use_agents`, driven by a free LLM (below). The Browser Use adapter follows its documented API and is unit-tested with a fake SDK;
it was not run live. AgentQL, Skyvern and Stagehand were removed because they need paid or hosted API keys. Agents never log in, submit forms or send
messages. Every fetched URL passes an SSRF guard (public http(s) addresses only).
`python -m leabee enrich-sites` crawls the company website of top leads for verified emails, phones and socials (also runs inside every collection).

## Google Maps prospects

The omkarcloud **Google Maps Extractor** is a *desktop app* (download from omkar.cloud, free account, 200 searches a month) with a local API on
port 8000; its GitHub repo only holds docs. Either start the app and list searches under `gmaps.searches` in `config.yaml`, or drop its CSV/JSON
exports into `data/maps/` (or `python -m leabee import-maps FILE`). Businesses are scored as prospects: **Need** (no website, weak reviews, unclaimed
listing), **Fit** (category), **Value** (reviews), **Reach** (email/phone), **Signal**. If your app's API input fields differ, set `gmaps.task_template`.
Note: scraping Google Maps is against Google's terms and cold outreach is regulated (GDPR, CAN-SPAM, CASL); keep volumes modest and outreach relevant.

## Free stack (no paid services)

| Need | Free / open-source choice | Key |
|---|---|---|
| LLM (reply drafts, AI review, chat) | **OpenRouter free models** (`:free`) or fully local **Ollama** | `OPENROUTER_API_KEY` (free account) or none for Ollama |
| Businesses for a prompt | **OpenStreetMap** (Nominatim + Overpass) and **Overture Maps** (open dataset, read with PyArrow) | none |
| Websites, emails, phones | plain HTTP + **Crawl4AI** | none |
| Chat agent | **Parlant** (open source) on OpenRouter | `OPENROUTER_API_KEY` |
| Database / hosting / jobs | Neon free, Vercel Hobby, GitHub Actions | free accounts |
| Lead sources | Reddit, Hacker News, job boards, Mastodon, GitHub, UK tenders; TinyFish and the omkar Maps app are optional (free tiers) | optional |

LLM setup, pick one. OpenRouter: create a free account, set `OPENROUTER_API_KEY` (free models such as `meta-llama/llama-3.3-70b-instruct:free`; override
with `LEABEE_LLM_MODEL`, a comma list tried in order). Local and offline: install Ollama, `ollama pull llama3.2`, then set
`LEABEE_LLM_BASE_URL=http://localhost:11434/v1` and `LEABEE_LLM_MODEL=llama3.2`. Without any LLM everything still works with rules and templates.

## Find real businesses from a prompt ("plumbers in North Carolina")

`python -m leabee find "provide me the details of plumbers in north carolina" -n 12 --csv out.csv [--save]`, the **Find businesses** button in the dashboard,
`POST /api/discover`, or just ask the Copilot. How it works: the place is looked up on Nominatim; **OpenStreetMap** and **Overture Maps** are searched in
parallel; duplicates are merged across sources; each business's own website is crawled for email, phone and socials; then every field is checked (website
reachable, email domain exists, email domain matches the site) and the most complete 10-15 are returned with their source. Nothing is invented: a missing
field is shown as "not found", and the answer says how many businesses were found and how complete they are. Results are cached for a week.

Measured live: *12 electricians in Raleigh, NC* gave 12 of 12 with phone, reachable website and email (about 80 s); *plumbers in North Carolina* gave 12 of 12
from 368 mapped (about 2 min). Real limits: coverage of open map data varies by trade and area (OpenStreetMap alone had only 2 electricians in Raleigh;
Overture added 110), many small businesses publish no email, and public map servers are shared, so keep usage modest. Searches take 1-3 minutes, so run
them locally or on a small server, not inside Vercel's 30-second functions (cached results are instant everywhere). `pip install pyarrow` is needed for
Overture (`requirements-extras.txt`). Data licences: (c) OpenStreetMap contributors (ODbL), (c) Overture Maps Foundation (CDLA Permissive 2.0 / ODbL).
Cold outreach is regulated (GDPR, CAN-SPAM, CASL): keep it relevant and honour opt-outs.

## Replies, alerts, digest, copilot

* **AI-written replies**: with a free LLM configured, the Draft button writes from the lead's own words and your `profile:` (name, pitch, portfolio_url, rate);
  otherwise templates. Prospects get a pitch about their specific gap. Nothing is ever sent automatically.
* **Telegram**: new hot leads arrive one per message with Draft / Contacted / Ignore buttons. Run `python -m leabee bot` to make the buttons work
  (and `/top`, `/stats`, `/due`); only your `TELEGRAM_CHAT_ID` is answered.
* **Digest**: `python -m leabee digest` (daily workflow in `.github/workflows/digest.yml`) sends top new leads and due follow-ups to your alert channels.
* **Tenders**: UK Contracts Finder (free, no key) for IT, software, marketing and design tenders.
* **Copilot (Parlant)**: `pip install parlant`, then `python -m leabee copilot` starts a Parlant agent (port 8800) with explicit policies
  (drafts only, confirm before status changes, stay in scope, no invented data). The dashboard's Copilot button talks to it. It uses the free LLM setup below and
  was not run live here; its tool functions and policies are unit-tested.

Tune everything in `config.yaml` (subreddits, queries, languages, `profile`, `min_score`, `max_age_days`).

## Security

Read **SECURITY.md** (threat table, checklist, limits). Short version: set a strong `LEABEE_PASSWORD` (or `LEABEE_PASSWORD_HASH` from
`python -m leabee hash-password`), keep keys in `.env` / Vercel / GitHub secrets, enable the commit guard with
`git config core.hooksPath .githooks`, and run `python -m leabee audit`. The app sends strict security headers (CSP with nonce, no framing),
blocks cross-site writes, locks out repeated bad logins, rate-limits expensive actions, validates every input, hardens all outbound fetching
against SSRF, redacts secrets from logs, and fences scraped text before it reaches an LLM.

Stronger login (all optional, all free):

```bash
python -m leabee hash-password     # LEABEE_PASSWORD_HASH instead of a plain password
python -m leabee totp-setup        # LEABEE_TOTP_SECRET: authenticator-app 2FA
python -m leabee gen-data-key      # LEABEE_DATA_KEY: encrypt notes and backups
python -m leabee audit-log         # who logged in, failed, exported, searched
```

Login is a form with a signed session cookie and CSRF token. Scripts that need HTTP Basic auth must set `LEABEE_ALLOW_BASIC=1`.

## Responsible use

Only public data is used. Respect each site's terms, reply on the platform where the person asked, include an
unsubscribe line if you email, and follow GDPR/CAN-SPAM/CASL. Don't mass-DM; Reddit and Facebook ban for it.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

The Postgres tests are skipped unless `LEABEE_TEST_PG` points at a **throwaway** Postgres (tables are dropped), for example
a local PGlite/Docker instance: `LEABEE_TEST_PG=postgresql://postgres:postgres@127.0.0.1:5544/postgres?sslmode=disable`.
