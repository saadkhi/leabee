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

## Run everything on the web, free (Vercel + Turso + GitHub Actions)

Your PC is not needed after setup, so you can wipe the project folder whenever you like.

| Piece | Does | Free tier |
|---|---|---|
| **Turso** | the shared, persistent database (hosted SQLite) | yes |
| **Vercel** | the dashboard: leads, board, dashboard, drafts, edits, the Collect button | Hobby (personal use) |
| **GitHub Actions** | collects leads every 6 hours and when you press Collect; AI review and alerts run here too | yes |

No lead data is ever committed to git, so the repository can stay **public**.

1. **Turso**: sign up at turso.tech, create a database, then copy its URL (`libsql://…turso.io`) and create an auth token.
2. **Copy your existing leads** (do this before cleaning your folder): put `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` in `.env`,
   then run `python -m leabee migrate-to-remote`. It is safe to re-run. From then on every CLI command uses Turso too.
3. **GitHub secrets** (repo → Settings → Secrets and variables → Actions): `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`,
   `TINYFISH_API_KEY`; optional `ANTHROPIC_API_KEY` and the Telegram / Discord / SMTP alert settings from `.env.example`.
4. **A GitHub token for the Collect button**: github.com → Settings → Developer settings → Fine-grained tokens → this repository only,
   permission **Actions: Read and write**. This is `GITHUB_DISPATCH_TOKEN`.
5. **Vercel**: import the repo at vercel.com/new and add these Environment Variables (Production), then deploy:

   | Name | Value |
   |---|---|
   | `LEABEE_PASSWORD` | a strong password (required; any username) |
   | `TURSO_DATABASE_URL` | from step 1 |
   | `TURSO_AUTH_TOKEN` | from step 1 |
   | `GITHUB_DISPATCH_TOKEN` | from step 4 |
   | `GITHUB_REPO` | `owner/repo`, e.g. `saadkhi/leabee` |
   | `LEABEE_YOUR_NAME` | optional, signs your reply drafts |

6. Open the site, log in, press **Collect new leads** (or wait up to 6 hours). Open the Actions tab to watch the run.

Notes: Reddit often blocks GitHub's servers; Leabee stops asking a blocked host after 3 refusals and still finds Reddit posts
through TinyFish. Weekly cleanup is safe: nothing lives in the folder except code and `.env`; re-clone and re-create `.env` any time.
Every request to Turso is one HTTP call (the dashboard stats are batched into a single call), so pages load in about a second.

**Fallbacks**: without the Turso variables the Vercel app serves a read-only snapshot (`python -m leabee snapshot`, see
`leabee/serverless.py`), and `python -m leabee publish` can commit that snapshot to a **private** repo. `python -m leabee backup`
pulls a local copy of the Turso database.

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
