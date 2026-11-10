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
* **AI second opinion** (optional): `python -m leabee ai-review`, needs `ANTHROPIC_API_KEY`. Borderline leads (score 35–75) get a
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
| `ANTHROPIC_API_KEY` | | optional | AI review of borderline leads |
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

## How leads are judged

* **Buyer vs seller**: "looking for a developer / need someone to build / [HIRING] / willing to pay…" count; "[FOR HIRE] / I will build / my services /
  looking for work…" are discarded. Job seekers, showcases, tutorials and on-site jobs are discarded.
* **Service match**: 16 categories (web, mobile, custom software, automation, AI, content, design, marketing/SEO, data, video, VA, devops, …).
* **Score** = intent (≤40) + service match (≤20) + freshness (≤20) + budget (≤10) + contact (≤5) + owner/urgency hints.
  Job openings are capped at 88 and contract/freelance roles score higher than full-time ones.
* Budget, contact details (public emails, Telegram, WhatsApp, Discord, booking links) and country are extracted automatically.

Tune everything in `config.yaml` (subreddits, queries, languages, `min_score`, `max_age_days`).

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
