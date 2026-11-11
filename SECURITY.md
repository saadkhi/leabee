# Security

Nobody can promise software is "secure from all attacks". This document says what Leabee protects against, what you must do yourself, and what is
not covered. Run `python -m leabee audit` any time to check your own setup.

## Quick checklist (10 minutes)

1. **Set a strong dashboard password** before anything is reachable from the internet: 12+ random characters in `LEABEE_PASSWORD`, or better a hash:
   `python -m leabee hash-password` then put `LEABEE_PASSWORD_HASH=...` in `.env` / Vercel and delete the plain password. Without one, Vercel serves nothing,
   and `serve --host 0.0.0.0` refuses to start.
2. **Rotate any key you ever pasted into a chat or a commit** (TinyFish key from the early conversation).
3. Put keys only in `.env` (git-ignored), Vercel environment variables or GitHub secrets. Never in `config.yaml`, code, issues or chat.
4. Turn on the commit guard: `git config core.hooksPath .githooks` (blocks commits containing keys, tokens or database passwords).
5. Keep the repo free of data: no `data/`, `vercel_data/`, `*.db`, exports or labelled posts. If the repo is public, treat everything in it as public.
6. Encrypt the disk that holds `data/` and `.env` (BitLocker / FileVault); Neon, Vercel and GitHub already encrypt at rest and use TLS in transit.
7. Run `python -m leabee audit` (and `--online` to see whether the repo is public). CI runs pip-audit, Bandit, the secret scan and the tests on every push.
8. Self-hosting: put the app behind HTTPS (Caddy / nginx / Cloudflare Tunnel), set `LEABEE_ALLOWED_HOSTS=your.domain`, `LEABEE_TRUST_PROXY=1` only if a proxy
   you control sets `X-Forwarded-For`.

## What is protected, and how

| Threat | Protection | Where |
|---|---|---|
| Open dashboard | Password required on Vercel; fail-closed on `0.0.0.0`; scrypt hash option; 12-char minimum; timing-safe compare | `web.py`, `security.py`, `cli.py`, `serverless.py` |
| Password guessing | Lockout after 8 wrong logins per IP for 15 minutes; global rate limit; stricter limits on search, collect, copilot and drafts | `web.py` |
| CSRF (browser sends Basic-auth automatically) | Writes need `application/json` and a same-origin `Origin` / `Sec-Fetch-Site`; no CORS headers are sent | `web.py` |
| DNS rebinding / foreign Host header | Local app answers only to localhost unless `LEABEE_ALLOWED_HOSTS` is set | `security.py` |
| XSS, clickjacking, MIME sniffing | Strict CSP with a fresh nonce per request (no inline handlers), `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy: no-referrer`, all output escaped | `web.py`, templates |
| Cached lead data | `Cache-Control: no-store`; HSTS over HTTPS | `web.py` |
| SQL injection | Every value is a bound parameter; sort/filters are whitelisted; injection strings are tested to be inert | `storage.py`, `tests/test_security.py` |
| SSRF (scraped URLs pointing at internal addresses) | Public http(s) only; every redirect hop re-checked; the connected peer IP re-checked (DNS rebinding); no proxies/cookies; 2 MB cap; Crawl4AI final URL checked | `extractors/` |
| Hostile pages (huge tokens, ReDoS, XML bombs) | Bounded regexes and text caps; `defusedxml` for feeds; time-boxed tests | `classify.py`, collectors |
| Secrets in logs / errors | Redaction filter on all logs (keys, bot tokens, database URLs); DB URL never shown in error pages | `security.py` |
| Secrets in git | Secret scanner, pre-commit hook, audit command, CI scan, `.env` and `data/` ignored | `audit.py`, `.githooks/` |
| Database in clear text | `sslmode=require` forced for remote Postgres; Neon encrypts at rest | `dbconn.py` |
| Prompt injection (posts that talk to the AI) | Scraped text is fenced as untrusted data; AI answers are validated (fixed JSON verdicts); drafts containing links you did not supply are rejected; copilot status changes capped at 5/min; copilot only drafts, never sends | `security.py`, `ai.py`, `outreach.py`, `copilot/` |
| Spreadsheet formula injection | Exports neutralise cells starting with `=`, `+`, `-`, `@` | `storage.py` |
| Supply chain | Version floors, Dependabot, weekly pip-audit + Bandit in CI, Docker runs as non-root | `.github/`, `Dockerfile` |
| Trojan-source characters | A test fails if invisible/bidi characters appear in source | `tests/test_security.py` |
| Oversized / malformed requests | 1 MB body limit; typed validation on every write; generic JSON errors, no stack traces | `web.py` |

## Not covered, or only partly

* **Serverless rate limits are per instance.** On Vercel each instance counts separately, so limits slow abuse rather than stop it. Use Vercel's
  firewall / rate limiting for hard limits, or a long password.
* **HTTP Basic auth** is simple, not single-sign-on: no 2FA, no per-user accounts or audit trail. For teams, put the app behind Cloudflare Access or an
  identity-aware proxy.
* **Local files are not encrypted by Leabee.** `data/leads.db`, backups and `.env` rely on your disk encryption.
* **Public data about people.** Leads contain public posters' names and contact details. You are responsible for GDPR / CAN-SPAM / CASL and platform
  rules; delete a person's record when asked.
* **Third parties** (Neon, Vercel, GitHub, OpenRouter, map services) have their own security; their tokens are only as safe as where you store them.
* **LLM output is untrusted.** The guards above reduce prompt-injection risk; they cannot make a language model perfectly obedient.
* **Browser Use / Crawl4AI** run a real browser: only point them at public sites; they never log in or submit forms.
* A static scan and tests are not a penetration test. For a high-stakes deployment, get an independent review.

## Reporting a problem

Open a private security advisory on the repository (Security tab) rather than a public issue, and do not include real keys or personal data.
