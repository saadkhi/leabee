"""Lead scoring: first decide WHAT a post is, then score only real client gigs.

Step 1, kind (what is this?):
    gig      a client wants to hire / buy a remote service (freelance, contract, project, paid task)   -> scored
    role     a full-time employee job ad                                                               -> dropped unless keep_roles
    prospect an outbound business lead (e.g. Google Maps) with no explicit ask                         -> scored separately
    seller / seeker / advice / closed / promo / partner / offtopic                                     -> dropped

Step 2, score 0-100 for a gig, five parts, every point has a plain-language reason:
    Intent    0-35   how explicit and paid the request is
    Fit       0-25   does it name a service/skills you sell (uses your `profile:` in config.yaml)
    Value     0-15   stated budget / rate / size of the work
    Reach     0-15   can you contact them directly, or only reply on the platform
    Fresh     0-10   how recent (early replies win)
Grade: A >= 80 (reply now) | B 65-79 (strong) | C 50-64 (worth a look) | D < 50 (weak)
"""
from __future__ import annotations

import re
from datetime import datetime

I = re.IGNORECASE

MARKETPLACES = ("upwork", "freelancer", "guru", "peopleperhour", "fiverr")
TALENT_NETWORKS = re.compile(
    r"\b(?:toptal|turing|andela|crossover|mercor|outlier|alignerr|dataannotation|remotasks?|invisible|lemon\.io|micro1|karat|askable|braintrust|"
    r"gun\.io|arc\.dev|a\.team|flexiple|uplers|x-team|revelo|bairesdev|deel|remote\.com|imerit|surge ?ai|scale ?ai|labelbox|telus ?digital)\b|"
    r"\bhelps? companies (?:\w+\s+){0,4}hire\b|\b(?:talent|expert) (?:marketplace|network|community|pool)\b|"
    r"\b(?:join|part of) our (?:talent |expert )?(?:network|community|pool)\b|\bget certified for (?:future|upcoming)\b|"
    r"\bfuture project opportunities\b|\bai trainers?\b|\bterminal-?bench\b|\bai coding tasks?\b|\bevaluat\w+ (?:benchmarks?|ai agents?)\b|"
    r"\b(?:rlhf|data annotation|annotators?|llm (?:training|evaluation) (?:data|tasks?)|ai training|training (?:&|and) evaluation|prompt[- ]quality)\b|"
    r"\b(?:storetasker|codeable|codementor|hired\.com|vettery)\b|\blove to freelance with\b", I)

# Posts that are lists of jobs, not a request for a service.
JOBLIST_RE = re.compile(r"\b\d{1,3}\+?\s+(?:remote\s+|new\s+|fresh\s+|latest\s+)?(?:[\w\-/]+\s+){0,2}jobs?\b|\bjob (?:list|board|leads|openings|roundup|digest)\b|"
                        r"\blist of (?:recently |latest |new )?(?:opened|open|remote)\b|\bcurated (?:a )?list\b|\bopenings that just hit\b|"
                        r"\bhiring roundup\b|\bweekly (?:jobs|openings)\b|\b#getfedihired\b|\bjob (?:&|and) contract leads\b|"
                        r"zerotaxjobs|\btax-free!|\bsalary guide:|apply (?:&|and) more details", I)

# Roles that are not a service you sell: sales, recruiting, commission-only, affiliates.
NONSERVICE_ROLE = re.compile(r"\b(?:founding (?:sales|ae|account executive)|\(founding ae\)|operations? (?:lead|manager)|product manager|academic dean|"
                             r"sales (?:partner|rep|representative|development|manager|executive|associate)|sdrs?|bdr|business developer|"
                             r"closers?|account executive|recruiter|talent partner|commission[- ]only|affiliate (?:manager|partner)|"
                             r"customer success|renewals? associate|customer (?:service|support|care) (?:rep\w*|specialist|agent|associate)|(?:live )?technical support rep\w*|"
                             r"help ?desk|call cent(?:er|re)|business development specialist|interviewer|head of editorial)\b", I)

# Homework / academic requests are not business leads.
ACADEMIC_RE = re.compile(r"\b(?:homework|coursework|thesis|dissertation|essays?|university student|college student|majoring in|"
                         r"my (?:professor|teacher|lecturer|assignments?|exams?|quizzes|classes))\b", I)

# ----------------------------------------------------------------------------- dropped kinds
CLOSED_RE = re.compile(r"^\W*(?:hired|filled|closed|taken)\b|\bhired!{1,}|\b(?:position|role|job|gig|project|spot)s? (?:has been |is |was |have been )?"
                       r"(?:already )?(?:filled|taken|closed)\b|\bno longer (?:available|hiring|needed|looking|accepting)\b|"
                       r"\balready (?:hired|found (?:someone|a|an))\b|\[\s*(?:closed|filled|hired)\s*\]", I)

OFFTOPIC_RE = re.compile(r"\[\s*[mfw]\s*4\s*[mfwr]\s*\]|\b(?:looking for (?:his|her) person|dating|girlfriend|boyfriend|"
                         r"looking for a (?:husband|wife|partner for life)|giveaway)\b", I)

HN_CANDIDATE_RE = re.compile(r"\bwilling to relocate\b|\bresume\s*/?\s*cv\s*:|^\s*seeking work\b", I | re.M)
HN_PROFILE_RE = re.compile(r"^\s*location:", I | re.M)       # + "Technologies:" below = a candidate profile, not a client

PARTNER_RE = re.compile(r"\bresell\w*|\bwhite[- ]label\b|\bco-?founders?\b|\bco-?developers?\b|\bbusiness partners?\b|\bpartners? (?:to|for) (?:grow|build|launch|join)\b|"
                        r"\bequity[- ]only\b|\bsweat equity\b|\brevenue[- ]share only\b|\b(?:build|make|create) (?:it|this|something) with\b|"
                        r"\bjoin me (?:to|in|and)\b|\bearly[- ]stage founders\b|\bvolunteers?\b|\bunpaid\b|\bno pay\b|\bpassion project\b|"
                        r"\bfor (?:free|exposure|credit)\b|\bcollaboration opportunit\w+|\bcollab\b", I)

PROMO_RE = re.compile(r"\b(?:make extra money|earn up to|limited spots|paid research|\d{1,2} (?:proven )?(?:tips|ways|reasons|mistakes|things)|"
                      r"feedback on pricing|my tool|our tool|beta testers?|early (?:access|adopters?)|test my (?:app|tool|platform|product)|free (?:to use|for (?:website )?owners)|"
                      r"use my (?:\w+\s+){0,3}(?:platform|tool|app|software)|waitlist|feedback on my (?:app|site|website|product|tool)|"
                      r"launching (?:a |my )?(?:paid )?beta|upvote|sign ?up (?:for|to) (?:my|our)|check out my|"
                      r"i work (?:at|for|with) (?:a|an)\b|\bhow we sell\b|\b(?:get|ask for) (?:these|this) in writing\b|\bred flags?\b|"
                      r"\bthings to (?:know|look for|ask)\b|\bchecklist\b)\b", I)

SEEKER_RE = [re.compile(p, I) for p in [
    r"\blooking to (?:take on|pick up|find|land|get)\b.{0,50}\b(?:freelance|projects?|clients?|work|gigs?|job|opportunit\w+)\b",
    r"\b(?:i'?m|i am|im)\b[^.\n]{0,40}\blooking for\b[^.\n]{0,45}\b(?:jobs?|work|opportunit\w+|clients?|role|position|gigs?)\b",
    r"\blooking for\b[^.\n]{0,40}\b(?:long[- ]term |remote |new |genuine |freelance )?opportunit(?:y|ies)\b",
    r"\blooking for (?:a |an |my )?[\w/\-& ]{0,35}\bjob\b",
    r"\blooking for my (?:next|new|first)\b",
    r"\b(?:ищу работу|ищу заказы|ищу подработку|ищу проекты?|ищут работу)\b",
    r"\bhow to (?:start|begin|launch) (?:my|a) (?:career|freelanc\w+)\b|\badvice and recommendations on how to start\b",
    r"\blaid off\b.{0,80}\b(?:looking|seeking)\b|\b(?:looking|seeking)\b.{0,80}\blaid off\b",
    r"^\s*available\b|\bavailable (?:ui|ux|web|graphic|full|front|back|mobile|freelance|video|seo|content|copy)\w*\b",
    r"\blooking for\s+(?:developers?|designers?|agencies|startups|clients|businesses|companies|founders|owners|brands)\b.{0,40}\bwho (?:need|want|are looking)\b",
    r"\banyone who needs?\b.{0,30}\b(?:developer|designer|writer|editor|expert|freelancer)\b",
    r"\bneed (?:a|an|your)\b[^.\n?]{0,60}\?\s*(?:dm|pm|message|contact|hit) me\b",
    r"\b(?:dm|message|contact) me (?:for|if you need)\b",
    r"\bopen to (?:work|new|freelance|opportunit\w+)\b|\bfor hire\b|\bhire me\b",
    r"\(\s*\d+\+?\s*(?:yrs?|years?|yoe)\b|\b\d+\s*yoe\b|\bresume review\b|\bcv review\b",
    r"^\W*\[\s*hire\s*\]",
]]

ADVICE_RE = re.compile(r"\b(?:advice|tips?|best practices?|where (?:do i|to|should i) start|how (?:do|should|can|to|would) (?:i|we|you)?\b|"
                       r"what(?:'s| is| are) the (?:best|most|right|difference)|recommendations? (?:on|for|about)|is it (?:worth|normal|possible)|"
                       r"thoughts\?|any (?:ideas|suggestions|tips)|help me (?:understand|decide|figure)|struggling|i'?m stuck|i feel stuck|"
                       r"can someone explain|need (?:some )?(?:help|advice|ideas|tips|feedback) (?:with|on|getting|understanding|figuring|to)|"
                       r"need (?:some )?(?:help|advice|ideas)\b(?!\s+(?:building|developing|hiring|finding|with (?:a |my )?(?:project|build|website|app|automation)))|"
                       r"are there any good|which (?:one|tool|framework|platform)|why (?:is|does|do|won'?t|isn'?t)|what should i|should i|"
                       r"has anyone interviewed|hiring process|is it worth the time|founders who|has anyone|did anyone|how many of you|who else|anyone else|am i the only|curious (?:how|what|if)|"
                       r"do i (?:spend|hire|need|really|pay)|should (?:i|we) hire|is it worth (?:hiring|paying)|torn|would appreciate (?:some )?advice|any advice|"
                       r"what (?:customer|problem|mistake|lesson|tool)s?\b|what do you (?:think|use)|your (?:experience|thoughts)|"
                       r"not working|doesn'?t work|isn'?t working|missing (?:parts|text)|approval|rejected|rate my|roast my|review my)\b", I)

# Evidence that a general-community post is actually a request to hire / pay (needed outside marketplaces and hiring tags).
HIRE_EVIDENCE = re.compile(
    r"\b(?:(?:looking|need|want|planning|trying|wanting|have) to hire\b|we(?:'re| are) hiring\b|hiring (?:a|an|for|freelanc\w+|remote|contract\w*|developers?|designers?|writers?|editors?)\b|"
    r"to hire (?:a|an)\b|hire (?:a|an) (?:freelanc\w+|developer|designer|writer|editor)\b|freelancers? (?:needed|wanted)|paid (?:work|gig|project|task|job|trial|test)|"
    r"pay(?:ing)? (?:you|for)|i(?:'ll| will) pay|willing to pay|budget|quote|per hour|/hr|hourly|"
    r"(?:developer|designer|writer|editor|expert|specialist|assistant|freelancer|engineer|programmer|copywriter|va)s? (?:needed|wanted|required)|"
    r"(?:looking for|need|seeking|searching for|want)\s+(?:(?:a|an|some|one)\s+)?(?:[\w\-/&\.]+\s+){0,5}?(?:developer|designer|writer|editor|expert|specialist|assistant|"
    r"freelancer|engineer|programmer|copywriter|agency|consultant|contractor|va|animator|translator|marketer|analyst|manager)|"
    r"who can (?:build|make|create|design|develop|write|set up|automate|fix)|can (?:anyone|someone) (?:build|make|create|design|develop|write|fix|automate)|"
    r"send (?:me )?(?:your )?(?:portfolio|rates?|proposal|cv|resume|samples?)|dm me (?:your|with)|"
    r"(?:busco|necesito|se busca|buscamos|procuro|preciso de|suche|brauche|cherche|besoin d|cerco|szukam|ищу|нужен|требуется|mencari|butuh|dibutuhkan|dicari|مطلوب|ابحث عن|أبحث عن|محتاج))\b"
    r"|\[\s*(?:hiring|task|paid)\s*\]|[$€£₹]\s?\d|\b(?:usd|eur|gbp|inr)\s?\d|مطلوب|ابحث عن|أبحث عن", I)

# ----------------------------------------------------------------------------- role (employee) vs gig
FREELANCE_CUE = re.compile(r"\b(?:freelanc\w+|contract(?:or|ual)?|part[- ]?time|project[- ]based|hourly|per hour|gig|one[- ]off|short[- ]term|"
                           r"fixed[- ]price|paid (?:project|trial|test|task)|\[\s*task\s*\]|ongoing work|retainer)\b|/\s?(?:hr|hour)\b", I)
GIG_STRICT = re.compile(r"\b(?:freelanc\w+|contract(?:or|ual)?|part[- ]?time|project[- ]based|gig|one[- ]off|short[- ]term|fixed[- ]price|"
                        r"paid (?:project|trial|test|task)|\[\s*task\s*\]|per (?:clip|video|article|task|project))\b", I)
GIG_BODY = re.compile(r"\b(?:this is an? (?:freelance|contract|part[- ]time|project[- ]based|one[- ]off)|(?:freelance|contract|part[- ]time|project[- ]based) "
                      r"(?:role|position|basis|opportunity|work|engagement|writer|developer|designer|editor|project)|independent contractor|1099|"
                      r"contractor role|looking for (?:a |an )?freelanc\w+|seeking (?:a |an )?freelanc\w+|per (?:clip|video|article|task|project)|"
                      r"(?:one|single)[- ]time (?:task|project|job))\b|\(\s*(?:contract|freelance|part[- ]time)\s*\)|"
                      r"[,|/(\-\u2013]\s*(?:freelance|contract(?:or)?|part[- ]time)\b", I)
EMPLOYMENT_CUE = re.compile(r"\b(?:full[- ]?time|permanent|salary|salaried|benefits|401\s?\(?k\)?|pto|paid time off|health insurance|equity|stock options|"
                            r"years? of experience (?:required|in|with)|\d\+?\s*years?|join our (?:growing |amazing |awesome )?team|"
                            r"to join (?:our|the) team|apply (?:now|today|here|at)|send (?:your )?(?:cv|resume)|responsibilities|qualifications|"
                            r"requirements|position:|job type|remote job|vacanc\w+|career opportunity|we offer|what we offer|about the role)\b", I)
EMPLOYMENT_STRONG = re.compile(r"\b(?:work authori[sz]ation|sponsorship|visa sponsor\w*|junior to mid|salary|salaried|benefits|401\s?\(?k\)?|pto|paid time off|health insurance|stock options|equity|"
                               r"join our (?:growing |amazing |awesome )?team|to join (?:our|the) team|apply (?:now|today|here|at)|"
                               r"send (?:your )?(?:cv|resume)|position:|job type|remote job|vacanc\w+|"
                               r"career opportunity|we offer|what we offer|about the role|years? of experience (?:required|in|with)|"
                               r"join us|per year|/\s?(?:yr|year)|annually|annual salary|roles? (?:only )?available|full[- ]?time (?:role|position|job))\b|"
                               r"[$€£]\s?\d{2,3}(?:,\d{3}|\s?k)\s*[-\u2013]\s*[$€£]?\s?\d{2,3}(?:,\d{3}|\s?k)(?!\s*/?\s*(?:hr|hour|h\b|per hour))", I)
SENIORITY_TITLE = re.compile(r"\b(?:senior|sr\.?|staff|principal|tech leads?|team lead|lead (?:engineer|developer|designer)|head of|director|founding|vp of|manager)\b", I)
FULLTIME_TITLE = re.compile(r"\bfull[- ]?time\b|\bpermanent\b", I)
HIRING_HEADER = re.compile(r"^\W*(?:we(?:'|\u2019)?re|we are)\s+hiring\b|^\W*hiring for\b", I)

# ----------------------------------------------------------------------------- specific skills = concrete requests
SKILL_RE = re.compile(
    r"\b(?:python|javascript|typescript|react(?:\s?native)?|next\.?js|node(?:\.?js)?|django|flask|fastapi|laravel|php|wordpress|woocommerce|shopify|"
    r"webflow|wix|squarespace|bubble|flutterflow|flutter|swift(?:ui)?|kotlin|android|ios|n8n|zapier|make\.com|integromat|airtable|notion|hubspot|"
    r"salesforce|odoo|openai|gpt-?\d?|claude|llm|rag|chatbots?|langchain|ai agents?|agentic|aws|gcp|azure|docker|kubernetes|terraform|ci/cd|"
    r"seo|aeo|google ads|meta ads|facebook ads|figma|canva|photoshop|illustrator|premiere|after effects|davinci|capcut|power ?bi|tableau|excel|"
    r"google sheets|sql|postgres|mongodb|firebase|supabase|stripe|api|web ?scrap\w+|scraper|crawler|etl|data entry|transcription|translation|"
    r"voice ?over|podcast|blog|newsletter|copywriting|ghostwrit\w+|unity|unreal|blender|3d|motion graphics|ux|ui|erp|crm|saas|mvp|landing page|"
    r"e-?commerce|dashboard|bot|chrome extension|browser extension|mobile app|website|web app)\b", I)

ONGOING_RE = re.compile(r"\b(?:long[- ]term|ongoing|monthly|retainer|recurring|weekly|more than 6 months|\d+\+?\s*(?:videos|articles|posts|pages|projects|clips))\b", I)
URGENT_RE = re.compile(r"\b(?:asap|urgent(?:ly)?|immediately|right away|this week|today|tomorrow|as soon as possible|deadline)\b", I)
OWNER_RE = re.compile(r"\b(?:my|our)\s+(?:business|startup|company|store|shop|agency|website|site|app|saas|product|brand|clients?|customers?|"
                      r"firm|restaurant|clinic|practice|e-?commerce|channel|podcast|course|platform|team)\b", I)


_BOILER = re.compile(r"\bfreelance jobs?(?:\s+in\s+[\w&/ ,\-]{2,60}?)?(?=\s*(?:-|\n|$|\.))|\blog in\b|\bsign up\b|\bpost a project\b|"
                     r"\bjob in\b|\bupwork\b|\bfreelancer\.com\b", I)
_REL_AGE = re.compile(r"\b(\d{1,3})\s*(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?|w|wks?|weeks?)\s+ago\b", I)


def relative_age_hours(text: str) -> float | None:
    """'4d ago', '10 hours ago' inside scraped page text -> hours (used when the real post time is missing)."""
    m = _REL_AGE.search((text or "")[:700])
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    return n / 60 if unit.startswith("m") else n if unit.startswith("h") else n * 24 if unit.startswith("d") else n * 168


def is_marketplace(platform: str) -> bool:
    p = (platform or "").lower()
    return any(m in p for m in MARKETPLACES)


def grade(score: int) -> str:
    return "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "D"


def classify_kind(item, title: str, text: str, buy_strong: int, intent: str, intent_reasons: list[str],
                  cfg: dict) -> tuple[str, list[str], dict]:
    """Return (kind, reasons, evidence). `buy_strong` and `intent` come from classify.classify_intent."""
    text = _BOILER.sub(" ", text)
    head = f"{title}\n{text[:900]}"
    first = f"{title}\n{text[:300]}"
    ev = {"marketplace": is_marketplace(item.platform), "tag": bool(re.search(r"\[\s*(?:hiring|task|paid|wanted)\s*\]", title, I)),
          "strong": buy_strong, "pay": bool(HIRE_EVIDENCE.search(head))}

    if CLOSED_RE.search(first):
        return "closed", ["filled/closed"], ev
    if OFFTOPIC_RE.search(head):
        return "offtopic", ["not a business request"], ev
    if not ev["marketplace"] and (JOBLIST_RE.search(title) or JOBLIST_RE.search(first)):
        return "joblist", ["list of other companies' jobs"], ev
    if ACADEMIC_RE.search(head) and not ev["marketplace"]:
        return "offtopic", ["homework / academic request"], ev
    if HN_CANDIDATE_RE.search(head) or (item.source == "hackernews" and HN_PROFILE_RE.search(head) and re.search(r"\btechnologies:", head, I)):
        return "seeker", ["candidate profile (seeking work)"], ev
    if intent == "seller":
        return "seller", intent_reasons or ["offering services"], ev
    if not ev["marketplace"] and any(rx.search(head) for rx in SEEKER_RE):
        return "seeker", ["job seeker / self-promotion"], ev
    if ev["marketplace"] and re.search(r"^\s*available\b", text, I):
        return "seller", ["freelancer profile, not a client"], ev
    if NONSERVICE_ROLE.search(title) and not ev["marketplace"]:
        return "offtopic", ["sales / recruiting / commission role, not a service you sell"], ev
    paid_words = re.search(r"\bpaid|salary|\$\s?\d|budget\b|\brate\b|per (?:hour|clip|video|article|task)\b", head, I)
    if PARTNER_RE.search(title) and not paid_words:
        return "partner", ["co-founder / partner search (no pay)"], ev
    if PARTNER_RE.search(first) and not paid_words and not ev["marketplace"]:
        return "partner", ["unpaid / partner / co-builder request"], ev
    if PROMO_RE.search(head) and not ev["marketplace"] and not ev["tag"]:
        return "promo", ["promotion / feedback request"], ev

    # Is this really a request to hire/pay, or a question / discussion?
    sure = ev["marketplace"] or ev["tag"] or item.force_intent == "buyer" or item.lead_type == "job"
    explicit = sure or ev["strong"] >= 2 or ev["pay"]
    if not explicit:
        return "advice", ["no explicit hire or pay signal"], ev
    hire_in_title = bool(re.search(r"\b(?:hire|hiring|looking for|need(?:ed)?|wanted|seeking|recommend\w*|who (?:can|could|knows?)|"
                                   r"(?:can|could) (?:anyone|someone|somebody)|anyone (?:know|recommend)\w*|how much (?:would|does|to)|cost)\b", title, I))
    if not sure and title.strip().endswith("?") and not hire_in_title:
        return "advice", ["question / discussion post"], ev
    if not sure and ADVICE_RE.search(title) and not re.search(r"\b(?:hire|hiring|paid|budget|quote|\$\s?\d|per hour|/hr)\b", head, I):
        return "advice", ["question / advice post"], ev
    if not sure and ev["strong"] < 2 and ADVICE_RE.search(first) and not ev["pay"]:
        return "advice", ["question / advice post"], ev

    # employee job ad / talent network vs freelance gig
    if TALENT_NETWORKS.search(f"{title} {text[:400]}"):
        return "role", ["talent-network / AI-data recruiting (not a client)"], ev
    if ev["marketplace"]:
        return "gig", [], ev                                  # a client posted a project on a freelance marketplace
    board = item.lead_type == "job" or item.source in ("boards", "remoteok", "remotive", "weworkremotely", "himalayas", "hackernews")
    strong_emp = bool(EMPLOYMENT_STRONG.search(head)) or bool(HIRING_HEADER.search(title))
    weak_emp = bool(EMPLOYMENT_CUE.search(head))
    gig_title = bool(GIG_STRICT.search(title))
    gig_body = bool(GIG_BODY.search(text[:700]))
    hourly = bool(re.search(r"/\s?(?:hr|hour)\b|\bper hour\b|\bhourly\b", head, I))
    fulltime_title = bool(FULLTIME_TITLE.search(title))
    wants_fulltime = bool(re.search(r"\bwe(?:'re| are)\b[^.\n]{0,20}(?:looking for|hiring|seeking) (?:a |an )?full[- ]?time\b", text[:500], I))
    if fulltime_title and not re.search(r"\b(?:freelanc\w+|part[- ]?time|per (?:clip|video|article|task|project))\b|\[\s*task\s*\]", title, I):
        return "role", ["employee job ad (full-time in the title)"], ev          # "Contract or Full Time", "Full-time contractor" are jobs
    if SENIORITY_TITLE.search(title) and not gig_title and not gig_body:
        return "role", ["employee job ad (senior / lead role)"], ev
    if wants_fulltime and not gig_title:
        return "role", ["employee job ad (asks for a full-time hire)"], ev
    if gig_title or (ev["tag"] and re.search(r"\[\s*task\s*\]", title, I)):
        return "gig", [], ev
    if board:
        if gig_body and not strong_emp:
            return "gig", [], ev
        return "role", ["employee job ad"], ev                # job boards: employee unless the headline says freelance/contract
    if strong_emp and not gig_body:
        return "role", ["employee job ad"], ev
    return "gig", [], ev


def score_gig(item, ev: dict, blob: str, strong_cats: list[str], title_cats: list[str], budget_text: str,
              budget_usd, contacts: list[str], age_h, language: str, country: str, cfg: dict, kind: str) -> tuple[int, dict, list[str]]:
    """Return (score, components, reasons). `kind` is 'gig', 'role' or 'prospect'."""
    prof = cfg.get("profile") or {}
    comp: dict[str, int] = {}
    why: list[str] = []

    # ---- Intent 0-35
    if ev["marketplace"]:
        base, label = 28, f"client project on {item.platform}"
    elif ev["tag"]:
        base, label = 28, "explicit [HIRING]/[TASK] post"
    elif item.force_intent == "buyer":
        base, label = 26, "posted in a hiring community"
    elif ev["strong"] >= 3:
        base, label = 30, "multiple clear hire phrases"
    elif ev["strong"] == 2:
        base, label = 26, "clear hire phrases"
    else:
        base, label = 22, "asks for a service"
    bonus = 5 if (ev["pay"] and (budget_text or re.search(r"\b(?:paid|pay|budget|hire|hiring|quote)\b", blob, I))) else 0
    if kind == "role":
        base, bonus, label = min(base, 10), 0, "employee job (not a client gig)"
    comp["intent"] = min(35, base + bonus)
    why.append(f"+{comp['intent']} intent: {label}" + (" + pay signal" if bonus else ""))

    # ---- Fit 0-25
    fit = 12 if title_cats else 8
    why_fit = [("service in title: " if title_cats else "service in post: ") + ", ".join((title_cats or strong_cats)[:3])]
    if len(strong_cats) >= 2:
        fit += 3
    skills = {m.lower() for m in SKILL_RE.findall(blob)}
    if skills:
        sk = min(8, 2 * len(skills))
        fit += sk
        why_fit.append("skills: " + ", ".join(sorted(skills)[:4]))
    services = set(prof.get("services") or [])
    if services:
        if services & set(strong_cats):
            fit += 5
            why_fit.append("matches your services")
        else:
            fit -= 10
            why_fit.append("outside your services")
    mine = [s.lower() for s in (prof.get("skills") or [])]
    hit = [s for s in mine if re.search(rf"\b{re.escape(s)}\b", blob, I)]
    if hit:
        fit += min(6, 3 * len(hit))
        why_fit.append("your skills: " + ", ".join(hit[:3]))
    langs = prof.get("languages") or []
    if langs and language and language not in langs:
        fit -= 8
        why_fit.append(f"language {language} not in your list")
    prefer = prof.get("countries_preferred") or []
    if prefer and country in prefer:
        fit += 3
        why_fit.append(f"preferred country {country}")
    comp["fit"] = max(0, min(25, fit))
    why.append(f"+{comp['fit']} fit: " + "; ".join(why_fit))

    # ---- Value 0-15
    val = 0
    vlabel = "no budget stated"
    if budget_usd is not None:
        val = 15 if budget_usd >= 2000 else 12 if budget_usd >= 500 else 9 if budget_usd >= 150 else 6 if budget_usd >= 50 else 3
        vlabel = f"budget {budget_text or int(budget_usd)}"
    elif budget_text:
        val, vlabel = 8, f"rate {budget_text}"
    if ONGOING_RE.search(blob):
        val += 3
        vlabel += " + ongoing/volume work"
    comp["value"] = min(15, val)
    why.append(f"+{comp['value']} value: {vlabel}")

    # ---- Reach 0-15
    if contacts:
        reach, rlabel = 15, "direct contact: " + contacts[0].split(":")[0]
    elif item.author_url or ev["marketplace"]:
        reach, rlabel = 10, "reply on " + (item.platform or "platform")
    else:
        reach, rlabel = 6, "reply on the post only"
    comp["reach"] = reach
    why.append(f"+{reach} reach: {rlabel}")

    # ---- Freshness 0-10
    if age_h is None:
        rel = relative_age_hours(item.text)
        age_h = rel
    if age_h is None:
        fresh, flabel = 4, "post time unknown"
    else:
        fresh = 10 if age_h < 3 else 9 if age_h < 12 else 8 if age_h < 24 else 6 if age_h < 48 else 5 if age_h < 72 else 3 if age_h < 168 else 1 if age_h < 336 else 0
        flabel = f"posted {age_h:.0f}h ago" if age_h < 72 else f"posted {age_h / 24:.0f}d ago"
    comp["fresh"] = fresh
    why.append(f"+{fresh} freshness: {flabel}")

    score = sum(comp.values())
    # small, explainable adjustments
    if len((item.text or "").strip()) < 60 and not ev["marketplace"]:
        score -= 6
        why.append("-6 very short post")
    if OWNER_RE.search(blob):
        score += 2
        why.append("+2 owner of the business")
    if URGENT_RE.search(blob):
        score += 2
        why.append("+2 urgent")
    if kind == "role":
        score = min(score, 45)
        why.append("capped at 45: employee job, not a client")
    return max(0, min(100, score)), comp, why


def next_action(item, kind: str, contacts: list[str], score: int) -> str:
    plat = item.platform or "the platform"
    if kind == "prospect":
        return "Call or email the owner with a short pitch for their gap (website, reviews, automation)"
    if kind == "role":
        return "Employee role: only worth it if they accept contract work"
    if contacts:
        first = contacts[0]
        if "@" in first and ":" not in first:
            return f"Email {first} now with a tailored reply"
        if first.startswith("http"):
            return "Use their booking / contact link"
        return f"Message them directly ({first.split(':')[0]})"
    if is_marketplace(plat):
        return f"Send a tailored proposal on {plat}"
    if "reddit" in plat.lower():
        return "Reply in the thread or DM the poster (follow subreddit rules)"
    return f"Reply on {plat} with a short, specific offer"
