"""Run collectors -> classify/score -> store; plus TinyFish Fetch enrichment."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Callable

from .classify import score_item
from .collectors import registry
from .http import Http
from .models import RawItem
from .storage import Store, now_iso
from .textutil import canonical_url, clip, lead_id

log = logging.getLogger("leabee.pipeline")
ALL_SOURCES = ["reddit", "hackernews", "boards", "mastodon", "github", "tinyfish", "gmaps"]


def item_to_lead(item: RawItem, cfg: dict, now: datetime | None = None) -> dict | None:
    now = now or datetime.now(timezone.utc)
    if item.source == "tinyfish":
        from .collectors.tinyfish import SELLER_URL
        if SELLER_URL.search(item.url):
            return None
    if item.lead_type == "prospect":
        from .prospects import score_prospect
        sc = score_prospect(item, cfg)
    else:
        sc = score_item(item, now, cfg)
    if sc is None or sc["score"] < cfg.get("min_score", 35):
        return None
    cf = cfg.get("countries_filter") or []
    if cf and sc["country"] not in cf:
        return None
    url = canonical_url(item.url)
    posted = sc.get("posted_at") or item.posted_at
    return {
        "id": lead_id(url), "source": item.source, "platform": item.platform, "lead_type": item.lead_type,
        "title": clip(item.title, 300), "text": clip(item.text, 6000), "url": url,
        "author": item.author, "author_url": item.author_url,
        "posted_at": posted.isoformat(timespec="seconds") if posted else None,
        "found_at": now_iso(), "categories": ",".join(sc["categories"]), "country": sc["country"],
        "budget_text": sc["budget_text"], "budget_usd": sc["budget_usd"], "contact": sc["contact"],
        "score": sc["score"], "reasons": " | ".join(sc["reasons"]), "enriched": 0, "language": sc["language"],
        "kind": sc["kind"], "grade": sc["grade"], "next_action": sc["next_action"],
        "components": json.dumps(sc["components"]), "details": json.dumps(sc["details"], ensure_ascii=False),
    }


def run_source(name: str, cfg: dict, store: Store, http: Http | None = None,
               progress: Callable[[str], None] | None = None) -> dict:
    http = http or Http(delay=float(cfg.get("request_delay", 1.5)))
    started = now_iso()
    fetched = kept = new = 0
    error = ""
    run_cfg = cfg
    if name == "reddit" and cfg.get("_partial"):
        run_cfg = {**cfg, "_reddit_offset": int(store.get_meta("reddit_offset", "0") or 0)}
        store.set_meta("reddit_offset", str(int(run_cfg["_reddit_offset"]) + 1))
    try:
        collect = registry()[name]
        for item in collect(run_cfg, http):
            fetched += 1
            lead = item_to_lead(item, cfg)
            if lead is None:
                continue
            kept += 1
            if store.upsert(lead):
                new += 1
    except Exception as e:  # one broken source must never stop the others
        error = f"{e.__class__.__name__}: {e}"
        log.exception("source %s failed", name)
    store.log_run(name, started, fetched, kept, new, error)
    msg = f"{name}: fetched={fetched} kept={kept} new={new}" + (f" ERROR={error}" if error else "")
    log.info(msg)
    if progress:
        progress(msg)
    return {"source": name, "fetched": fetched, "kept": kept, "new": new, "error": error}


def run_all(cfg: dict, store: Store, sources: list[str] | None = None,
            progress: Callable[[str], None] | None = None, partial: bool = False) -> list[dict]:
    if partial:
        cfg = {**cfg, "_partial": True}
    http = Http(delay=float(cfg.get("request_delay", 1.5)))
    return [run_source(s, cfg, store, http, progress) for s in (sources or ALL_SOURCES)]


def rescore(cfg: dict, store: Store) -> dict:
    """Re-run classification on everything stored (after tuning rules). Drops leads that no longer
    qualify unless you have already worked them (status != new)."""
    from .textutil import parse_dt
    rows, _ = store.query(limit=10 ** 7, exclude_ignored=False)
    kept = dropped = 0
    for row in rows:
        item = RawItem(source=row["source"], platform=row["platform"], lead_type=row["lead_type"],
                       title=row["title"], text=row["text"], url=row["url"], author=row["author"],
                       author_url=row["author_url"], posted_at=parse_dt(row["posted_at"]),
                       location_hint=row["country"] or "")
        lead = item_to_lead(item, {**cfg, "max_age_days": 10 ** 6})
        if lead is None:
            if row["status"] == "new":
                store.delete(row["id"])
                dropped += 1
            continue
        lead["id"] = row["id"]
        lead["enriched"] = row["enriched"]
        lead["found_at"] = row["found_at"]
        store.upsert(lead)
        kept += 1
    return {"kept": kept, "dropped": dropped, "crossposts_removed": store.dedupe()}


def enrich(cfg: dict, store: Store, limit: int = 100, progress: Callable[[str], None] | None = None) -> dict:
    """Fetch full page text for top TinyFish-sourced leads, then re-score them."""
    from .collectors.tinyfish import TinyFishClient, TinyFishError

    client = TinyFishClient()
    if not client.enabled:
        return {"enriched": 0, "dropped": 0, "error": "no TINYFISH_API_KEY"}
    rows = store.unenriched(limit)
    done = dropped = 0
    err = ""
    for i in range(0, len(rows), 10):
        batch = rows[i:i + 10]
        try:
            results, _errors = client.fetch([r["url"] for r in batch])
        except TinyFishError as e:
            err = str(e)
            break
        by_url = {canonical_url(r.get("url", "")): r for r in results}
        by_url.update({canonical_url(r.get("final_url", "")): r for r in results if r.get("final_url")})
        for row in batch:
            res = by_url.get(canonical_url(row["url"]))
            if not res or not res.get("text"):
                store.upsert({**row, "enriched": 1})
                continue
            text = clip((res.get("title") or row["title"]) + "\n" + res["text"], 6000)
            item = RawItem(source="tinyfish", platform=row["platform"], lead_type=row["lead_type"],
                           title=row["title"], text=text, url=row["url"], author=row["author"],
                           posted_at=None, location_hint="")
            lead = item_to_lead(item, cfg)
            if lead is None:
                store.update_user_fields(row["id"], status="ignored")   # turned out not to be a buyer
                store.upsert({**row, "text": text, "enriched": 1})
                dropped += 1
            else:
                lead["id"] = row["id"]
                lead["enriched"] = 1
                store.upsert(lead)
                done += 1
        if progress:
            progress(f"enrich: {done} enriched, {dropped} dropped")
    return {"enriched": done, "dropped": dropped, "error": err}


def enrich_sites(cfg: dict, store: Store, limit: int = 25, min_score: int = 50, use_agents: bool = False,
                 progress: Callable[[str], None] | None = None) -> dict:
    """Crawl the company website of top leads (home + contact pages) for verified emails, phones and social links.

    Uses plain HTTP first, then Crawl4AI (a real browser) for JavaScript sites, and only then, if enabled and keyed,
    an AI agent (AgentQL / Stagehand / Browser Use / Skyvern). Updates the lead's contact, re-scores it (direct contact
    raises Reach) and stores what was found under details.site."""
    from .extractors import enrich_site
    from .textutil import parse_dt

    rows = store.for_site_enrichment(limit, min_score)
    done = found = 0
    for row in rows:
        try:
            details = json.loads(row.get("details") or "{}")
        except ValueError:
            details = {}
        site = details.get("website")
        if not site:
            continue
        info = enrich_site(site, cfg, use_agents=use_agents)
        done += 1
        hint = " ".join(filter(None, [info.get("best_email", "")] + info.get("phones", [])[:1] + list(info.get("socials", {}).values())[:2]))
        details["site"] = {k: info[k] for k in ("ok", "backend", "title", "best_email", "phones", "socials", "pages", "error") if k in info}
        details["site"]["emails"] = [e["email"] for e in info.get("emails", [])][:5]
        if info.get("owner_or_contact_name"):
            details["owner"] = info["owner_or_contact_name"]
        item = RawItem(source=row["source"], platform=row["platform"], lead_type=row["lead_type"], title=row["title"], text=row["text"],
                       url=row["url"], author=row["author"], author_url=row["author_url"], posted_at=parse_dt(row["posted_at"]),
                       location_hint=row["country"] or "", contact_hint=(row["contact"] or "") + " " + hint)
        if row["lead_type"] == "prospect":
            item.meta = {}                  # prospect records are re-scored from the stored text only when freshly collected
        lead = item_to_lead(item, {**cfg, "max_age_days": 10 ** 6}) if row["lead_type"] != "prospect" else None
        if lead is None:
            lead = {**row}
            contacts = [c for c in [row["contact"], info.get("best_email", ""), *(info.get("phones", [])[:1])] if c]
            lead["contact"] = " | ".join(dict.fromkeys(" | ".join(contacts).split(" | ")))
            if info.get("best_email"):
                lead["score"] = min(100, row["score"] + 5)
        else:
            lead["id"], lead["enriched"], lead["found_at"] = row["id"], row["enriched"], row["found_at"]
        lead["details"] = json.dumps({**json.loads(lead.get("details") or "{}"), **details}, ensure_ascii=False)
        store.upsert(lead)
        found += bool(info.get("best_email") or info.get("phones"))
        if progress and done % 5 == 0:
            progress(f"sites: {done} crawled, {found} with contact details")
    return {"crawled": done, "with_contact": found}
