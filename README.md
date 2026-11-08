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
python -m pytest -q
```
