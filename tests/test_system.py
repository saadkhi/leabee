from datetime import datetime, timezone

from leabee.collectors import boards, mastodon, reddit, tinyfish
from leabee.pipeline import item_to_lead
from leabee.storage import Store
from leabee.textutil import canonical_url, lead_id, strip_html
from leabee.web import create_app

CFG = {"min_score": 35, "max_age_days": 10 ** 5, "hot_score": 70}

REDDIT_XML = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><author><name>/u/acme</name><uri>https://www.reddit.com/user/acme</uri></author>
<category term="forhire" label="r/forhire"/>
<content type="html">&lt;p&gt;Need a Shopify developer. Budget $800. Email: boss@acme.io&lt;/p&gt;</content>
<link href="https://www.reddit.com/r/forhire/comments/abc/hiring_shopify_dev/"/>
<updated>2026-11-08T01:00:00+00:00</updated><title>[HIRING] Shopify developer</title></entry>
<entry><author><name>/u/dev</name></author><category term="forhire" label="r/forhire"/>
<content type="html">&lt;p&gt;Full stack dev, 8 yrs&lt;/p&gt;</content>
<link href="https://www.reddit.com/r/forhire/comments/def/for_hire_dev/"/>
<updated>2026-11-08T01:00:00+00:00</updated><title>[FOR HIRE] Full stack developer</title></entry>
</feed>"""


def test_reddit_parse_and_flair():
    items = reddit.parse_feed(REDDIT_XML)
    assert len(items) == 2
    assert items[0].force_intent == "buyer" and items[1].force_intent == "seller"
    leads = [item_to_lead(i, CFG, datetime(2026, 11, 8, 2, tzinfo=timezone.utc)) for i in items]
    assert leads[0] and leads[1] is None
    assert "boss@acme.io" in leads[0]["contact"]
    assert leads[0]["budget_usd"] == 800


def test_bad_reddit_xml():
    assert reddit.parse_feed("<html>blocked") == []


def test_boards_parsers():
    ok = boards.parse_remoteok([{"position": "Python Developer", "company": "Foo", "url": "https://r/1", "epoch": 1794000000,
                                 "description": "<p>Contract python developer</p>", "tags": ["python"], "location": "Worldwide"}])
    assert ok[0].lead_type == "job" and ok[0].location_hint == "Worldwide"
    rem = boards.parse_remotive({"jobs": [{"title": "Writer", "company_name": "Bar", "url": "https://r/2", "category": "Writing",
                                          "publication_date": "2026-11-06T08:51:01", "description": "<p>blog</p>", "tags": [],
                                          "candidate_required_location": "USA", "job_type": "contract"}]})
    assert rem[0].posted_at is not None
    assert boards.parse_wwr("not xml") == []
    assert boards.parse_himalayas({"jobs": [{"title": "Dev", "companyName": "X", "guid": "https://h/1", "pubDate": 1794090855,
                                             "description": "d", "categories": ["Dev"]}]})[0].url == "https://h/1"


def test_mastodon_skip_reblog_and_forhire():
    s = {"content": "<p>#forhire I am a dev</p>", "url": "https://m/1", "created_at": "2026-11-08T00:00:00Z",
         "account": {"acct": "a", "url": "https://m/@a"}, "tags": [{"name": "forhire"}]}
    assert mastodon.parse_status(s, "x").force_intent == "seller"


def test_tinyfish_result_filters_and_queries():
    assert tinyfish.result_to_item({"url": "https://www.upwork.com/freelancers/~abc", "title": "Bob"}) is None
    assert tinyfish.result_to_item({"url": "https://www.linkedin.com/in/bob", "title": "Bob"}) is None
    it = tinyfish.result_to_item({"url": "https://www.reddit.com/r/x/comments/1/a/", "title": "Need dev", "snippet": "s"})
    assert it and it.platform == "Reddit"
    from leabee.config import load_config
    qs = tinyfish.build_queries(load_config()["tinyfish"])
    assert 100 < len(qs) <= 400
    assert all("include_domains" in q for q in qs)
    assert not any("fiverr" in q["include_domains"] for q in qs)


def test_canonical_and_id():
    a = canonical_url("https://www.reddit.com/r/x/comments/1/a/?utm_source=z#c")
    b = canonical_url("https://old.reddit.com/r/x/comments/1/a")
    assert a == b and lead_id(a) == lead_id(b)
    assert canonical_url("https://news.ycombinator.com/item?id=5") .endswith("item?id=5")
    assert strip_html("<p>a &amp; b</p><br>c").split() == ["a", "&", "b", "c"]


def _lead(i="1", score=60, **kw):
    d = dict(id=i, source="reddit", platform="Reddit", lead_type="buyer", title=f"t{i}", text="x", url=f"https://u/{i}",
             author="", author_url="", posted_at="2026-11-08T00:00:00+00:00", categories="web_development,automation",
             country="India", budget_text="", budget_usd=None, contact="", score=score, reasons="", enriched=0)
    d.update(kw)
    return d


def test_store_upsert_dedupe_and_user_fields():
    s = Store(":memory:")
    assert s.upsert(_lead()) is True
    assert s.upsert(_lead(score=80)) is False       # duplicate -> refresh, not new
    assert s.get("1")["score"] == 80
    s.update_user_fields("1", status="contacted", notes="called")
    s.upsert(_lead(score=90))
    got = s.get("1")
    assert got["status"] == "contacted" and got["notes"] == "called"   # user data survives re-collection
    try:
        s.update_user_fields("1", status="bogus")
        assert False
    except ValueError:
        pass


def test_store_filters():
    s = Store(":memory:")
    s.upsert(_lead("1", 90, country="India", contact="a@b.co"))
    s.upsert(_lead("2", 50, country="Germany", categories="content_writing"))
    s.upsert(_lead("3", 40, country="", lead_type="job"))
    assert s.query(min_score=60)[1] == 1
    assert s.query(category="content_writing")[1] == 1
    assert s.query(category="content")[1] == 0          # no partial-category match
    assert s.query(country="Unknown")[1] == 1
    assert s.query(has_contact=True)[1] == 1
    assert s.query(lead_type="job")[1] == 1
    assert s.query(q="t2")[1] == 1
    assert s.query(since_hours=24 * 3650)[1] == 3
    s.update_user_fields("1", status="ignored")
    assert s.query()[1] == 2
    assert s.stats()["total"] == 2
    assert "score" in s.to_csv(s.query()[0]).splitlines()[0]


def test_web_api():
    s = Store(":memory:")
    s.upsert(_lead("1", 90))
    app = create_app(CFG, s).test_client()
    assert app.get("/").status_code == 200
    j = app.get("/api/leads?min_score=50").get_json()
    assert j["total"] == 1 and j["leads"][0]["categories"] == ["web_development", "automation"]
    assert app.patch("/api/leads/1", json={"status": "won"}).status_code == 200
    assert app.patch("/api/leads/1", json={"status": "zzz"}).status_code == 400
    assert app.patch("/api/leads/nope", json={"status": "won"}).status_code == 404
    assert app.get("/api/leads?min_score=abc").status_code == 400
    assert app.get("/export.csv").data.decode("utf-8-sig").startswith("score")
    assert app.get("/api/stats").get_json()["total"] == 1


def test_crosspost_dedupe():
    s = Store(":memory:")
    a = _lead("1", author="bob", title="[Hiring] Zoho developer remote india")
    b = _lead("2", author="bob", title="[Hiring] Zoho Developer Remote India", url="https://u/2")
    assert s.upsert(a) is True
    assert s.upsert(b) is False                      # cross-post not stored
    s.upsert(_lead("3", author="eve", title="short"))
    s.upsert(_lead("4", author="eve", title="short", url="https://u/4"))   # too short to dedupe
    assert s.query()[1] == 3
    # existing duplicates are cleaned by dedupe()
    s._conn.execute("INSERT INTO leads (id,source,platform,lead_type,title,text,url,found_at,author) VALUES ('9','r','R','buyer','[Hiring] Zoho developer remote india','', 'https://u/9','2099-01-01','bob')")
    s._conn.commit()
    assert s.dedupe() == 1 and s.get("9") is None
