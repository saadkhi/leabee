"""Rule-based lead classification: buyer vs seller, service category, budget,
country, contact details and score. Pure functions, easy to test."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import RawItem

I = re.IGNORECASE

# --------------------------------------------------------------------------- #
# Service categories (everything that can be done remotely)
# --------------------------------------------------------------------------- #
CATEGORY_PATTERNS: dict[str, re.Pattern] = {k: re.compile(v, I) for k, v in {
    "content_writing": r"\b(content[- ]writ\w*|copy ?writ\w*|blog(ger| post| writ\w*)?|article writ\w*|ghost ?writ\w*|"
                       r"technical writ\w*|seo (content|writ\w*|article)|newsletter|script ?writ\w*|proofread\w*|"
                       r"editor|translat\w+|transcri\w+|white ?paper|case stud(y|ies)|press release|"
                       r"redactor|redactor de contenidos|redator|texter|rédacteur|redacteur|copywriter|contenido|conteúdo)\b",
    "web_development": r"\b(web ?(site|app|application|dev\w*|design\w*|master)|wordpress|woocommerce|shopify|webflow|wix|"
                       r"squarespace|landing ?page|front[- ]?end|back[- ]?end|full[- ]?stack|react(\.js)?|next\.?js|vue|angular|"
                       r"laravel|django|flask|node\.?js|php|html|css|javascript|typescript|e-?commerce (store|site|developer)|"
                       r"online store|portfolio site|página web|pagina web|sitio web|website|webseite|homepage|hemsida|"
                       r"strona internetowa|web sitesi|situs web|trang web)\b",
    "mobile_apps": r"\b(mobile app\w*|ios app|android app|flutter|react native|swift ?ui|kotlin|app developer|"
                    r"iphone app|app development|aplicativo|aplicación móvil|app entwickler|application mobile)\b",
    "custom_software": r"\b(saas|mvp|custom software|software (dev\w*|engineer\w*|solution)|erp|crm|internal tool|"
                        r"admin panel|dashboard|api (integration|dev\w*)|backend developer|microservice|"
                        r"desktop app|chrome extension|browser extension|plugin|"
                        r"tech(nical)? co-?founder|cto|programmer|programador|programmeur|entwickler|sviluppatore|"
                        r"desenvolvedor|desarrollador|developer|\bdev\b|software)\b",
    "automation": r"\b(automat\w*|zapier|make\.com|integromat|n8n|workflows?|rpa|scrap(e|er|ers|ing)|crawler|"
                   r"bots?|chat ?bots?|airtable|google (apps )?script|python script|power automate|"
                   r"no[- ]?code|low[- ]?code|bubble\.io|retool|automatizar|automatisieren|automatiser)\b",
    "ai_ml": r"\b(ai|a\.i\.|llm|gpt|chatgpt|openai|claude|gemini|machine learning|deep learning|ai agents?|rag|"
              r"computer vision|nlp|prompt engineer\w*|generative|fine[- ]?tun\w+|langchain|voice (bot|agent)|"
              r"inteligencia artificial|künstliche intelligenz|intelligence artificielle)\b",
    "design": r"\b(ui/?ux|ux/?ui|ui designer|ux designer|graphic design\w*|logo|branding|figma|illustrat\w+|"
               r"web designer|product designer|motion graphics|3d (model|render|artist)|designer|diseñador|designer gráfico|"
               r"grafikdesign\w*|infographic|packaging design|brand identity)\b",
    "marketing_seo": r"\b(seo|sem|ppc|google ads|facebook ads|meta ads|social media (manager|marketing|management)|"
                      r"digital marketing|email marketing|growth (hacker|marketer)|lead gen\w*|cold email|link ?building|"
                      r"influencer|community manager|marketing (manager|specialist|expert)|performance marketing)\b",
    "data": r"\b(data (entry|analy\w+|scien\w+|engineer\w*|visuali\w+|clean\w*)|excel|spreadsheet|google sheets|power ?bi|"
             r"tableau|etl|sql|database|big ?query|looker|dbt|web data|data pipeline)\b",
    "video_audio": r"\b(video (edit\w*|production|creator)|youtube (editor|thumbnail)|motion|animation|animator|"
                    r"voice ?over|voice actor|podcast (edit\w*|produc\w*)|audio (edit\w*|engineer)|after effects|premiere|"
                    r"tiktok|reels|shorts editor|videographer)\b",
    "virtual_assistant": r"\b(virtual assistant|\bvas?\b|executive assistant|admin(istrative)? support|customer support|"
                          r"customer service|data entry|appointment setter|researcher|lead researcher|bookkeep\w*|"
                          r"inbox management|personal assistant|chat support|live chat|sdr|bdr)\b",
    "devops_cloud": r"\b(devops|aws|azure|gcp|google cloud|kubernetes|docker|terraform|ci/?cd|sre|cloud (engineer|architect)|"
                     r"server (setup|admin)|linux admin|infrastructure)\b",
    "qa_security": r"\b(qa (engineer|tester)|test(ing|er)|penetration test\w*|pentest\w*|security (audit|engineer|review)|"
                    r"bug bount\w*|cyber ?security)\b",
    "blockchain": r"\b(blockchain|smart contracts?|solidity|web3|nft|crypto (developer|project|bot)|dapp|token)\b",
    "game_dev": r"\b(game (dev\w*|design\w*|artist)|unity|unreal|godot|roblox|minecraft (plugin|mod|server))\b",
    "consulting": r"\b(consultant|consulting|advisor|fractional|project manager|product manager|scrum master|"
                   r"business analyst|technical architect|strategy)\b",
}.items()}

# Categories that, on their own, are too generic to prove it's a services lead.
WEAK_CATEGORIES = {"consulting", "blockchain", "qa_security"}

# --------------------------------------------------------------------------- #
# Intent: someone who wants to BUY remote services vs someone offering them
# --------------------------------------------------------------------------- #
_NOUN = (r"(?:a|an|the|some|any|one|another|good|great|reliable|skilled|experienced|senior|junior|talented|"
         r"freelanc\w+|remote|part[- ]?time|full[- ]?time|dedicated|capable|pro|professional)")
_ROLE = (r"(?:developers?|devs?|programmers?|engineers?|designers?|writers?|copywriters?|editors?|freelancers?|"
         r"contractors?|agency|agencies|experts?|specialists?|consultants?|assistants?|va|vas|marketers?|"
         r"co-?founders?|team|help|someone|somebody|anyone|people|person|animators?|translators?|analysts?|"
         r"architects?|testers?|admins?|managers?|coders?|builders?|studio|firm|company|partner)")

_WORK_WORDS = (r"\s+(?:(?:a|an|new|some|any)\s+){0,2}(?:(?:full|part)[- ]?time\s+|freelance\s+|remote\s+|paid\s+|short[- ]?(?:term|time)\s+)?"
               r"(?:work|jobs?|role|roles|position|clients?|customers?|projects?|gigs?|opportunit\w+|employment|internship|"
               r"freelance|contracts?)\b")
_NOT_WORK = f"(?!{_WORK_WORDS})"

_NEED_OBJ = rf"(?:(?:{_NOUN}\s+)(?:[\w\-\.\+/#&]+\s+){{0,4}}?)?{_ROLE}"

BUYER_STRONG = [re.compile(p, I) for p in [
    r"\[\s*hiring\s*\]",
    r"\[\s*task\s*\]",
    rf"(?:^|[\n.!?:;\-–—]\s*)(?:urgently\s+|immediately\s+|asap\s+)?need(?:ed)?\s+{_NEED_OBJ}",
    rf"\b(?:i'?m|i am|we'?re|we are|i'?ve been|currently|still|is)\s+(?:looking|searching|seeking|in need|on the hunt)\b(?!\s+for{_WORK_WORDS})",
    rf"\blooking (?:for|to hire)\b{_NOT_WORK}",
    r"\bsearching for (?:a|an|the|someone|somebody)\b",
    r"\bseeking\s+(?:a|an|the|some|freelanc\w+|developers?|designers?|writers?|contractors?|help)\b",
    rf"\b(?:i|we)\s+(?:really\s+)?need\s+{_NEED_OBJ}",
    r"\b(?:i|we)\s+(?:need|want|require)\s+(?:a|an|to get)\s+\w+(?:\s+\w+){0,3}\s+(?:built|made|developed|created|designed|written|set up|automated|fixed)\b",
    r"\bneed(?:s|ed)?\s+(?:a|an|some)\s+\w+(?:\s+\w+){0,3}\s+(?:built|made|developed|created|designed|written|automated)\b",
    r"\b(?:want|wanna|would like|need|plan(?:ning)?) to (?:hire|outsource|commission|get someone)\b",
    r"\b(?:we are|we'?re|is|are)\s+hiring\b",
    r"\bhiring\s+(?:a|an|for|freelanc\w+|remote|contract\w*|developers?|designers?|writers?)\b",
    r"\b(?:developer|designer|writer|freelancer|engineer|programmer|copywriter|editor|assistant|expert|specialist)s?\s+(?:wanted|needed|required)\b",
    r"\bwanted:?\s+(?:a|an)?\s*\w+\s*(?:developer|designer|writer|freelancer|engineer|programmer|editor)\b",
    r"\b(?:who|anyone|somebody|someone)\s+(?:can|could|knows? how to|is able to|here can)\s+(?:build|make|create|develop|write|design|set up|automate|code|fix|help)\b",
    r"\bcan (?:anyone|someone|somebody) (?:build|make|create|develop|write|design|help|recommend|suggest|automate|fix|code)\b",
    r"\b(?:any|can you|could you|please)\s+(?:recommend\w*|suggest\w*)\s+(?:a|an|any)?\s*(?:good\s+)?(?:developers?|agency|agencies|freelancers?|designers?|writers?|consultants?|companies|firms)\b",
    r"\brecommend\w*\s+(?:for|of)\s+(?:a|an|any)\s+\w+\s*(?:developer|agency|freelancer|designer|writer)\b",
    r"\b(?:willing|ready|happy|able|prepared) to pay\b",
    r"\bpaid (?:project|gig|job|work|task|test|trial)\b",
    r"\b(?:my|our|the)\s+budget\s+(?:is|of|for|:)",
    r"\bbudget\s*(?:is|of|:|around|about|up to)\s*[$€£\d]",
    r"\bhow much (?:would|does|do|will|should) (?:it|this|that)?\s*(?:cost|be|run)\b.*\b(?:build|develop|make|create|design)\b",
    r"\bhow much (?:to|for|would it cost to)\s+(?:build|develop|make|create|hire|get)\b",
    r"\b(?:project|job|gig|task|opportunity)\s+(?:for|available|open)\b.*\b(?:freelanc\w+|developer|designer|writer)\b",
    r"\bseeking freelancer\b",
    r"\bcontract (?:role|position|opportunity|work) available\b",
    r"\b(?:busco|necesito|requiero|se busca|se necesita|buscamos|necesitamos)\s+\w+",
    r"\b(?:procuro|preciso de|precisamos de|procuramos|contrata-se|contratando)\b",
    r"\b(?:suche|suchen|brauche|brauchen|gesucht|gesucht:)\s+(?:einen|eine|ein|nach|jemanden)?",
    r"\b(?:je cherche|nous cherchons|recherche|besoin d'|besoin de|on recherche|cherche)\s+(?:un|une|des)?",
    r"\b(?:cerco|cerchiamo|ho bisogno di|abbiamo bisogno di)\b",
    r"\b(?:zoek|zoeken|ik heb .{0,15} nodig)\b",
    r"\b(?:szukam|potrzebuję|potrzebujemy|poszukujemy)\b",
    r"\b(?:arıyorum|ariyorum|yaptırmak istiyorum|ihtiyacım var|aranıyor)\b",
    r"\b(?:ищу|нужен|нужна|требуется|ищем)\b",
    r"\b(?:шукаю|потрібен|потрібна|шукаємо)\b",
    r"(?:ابحث عن|أبحث عن|مطلوب|محتاج|احتاج|أحتاج|اريد|أريد)",
    r"\b(?:mencari|butuh|dibutuhkan|dicari|membutuhkan)\b",
    r"(?:tìm|cần)\s+(?:lập trình|thiết kế|người|dev)",
    r"(?:探しています|募集|依頼|求む)",
    r"(?:구합니다|구해요|의뢰|찾습니다)",
]]

BUYER_WEAK = [re.compile(p, I) for p in [
    r"\bfreelance\b",
    r"\bcontract\b",
    r"\bremote\b",
    r"\bpay(?:ing)?\b",
    r"\bquote\b",
    r"\bproposal\b",
    r"\bdm me\b",
    r"\bapply\b",
    r"\bsend (?:me )?(?:your )?(?:portfolio|rates|proposal)\b",
    r"\bportfolio\b",
]]

SELLER_TAG = re.compile(r"[\[\(]\s*(?:for hire|forhire|offer|offering|available(?: for hire)?|open to work)\s*[\]\)]|"
                        r"^\s*(?:title:\s*)?for hire\b|\banyone hiring\b|\bhire me\b|\bopen to work\b|"
                        r"\bavailable for (?:hire|work|freelance|projects?)\b|\blooking for (?:a )?new opportunit\w+", I)
BUYER_TAG = re.compile(r"[\[\(]\s*(?:hiring|task|paid|wanted)\s*[\]\)]", I)

SELLER_PATTERNS = [re.compile(p, I) for p in [
    # job-seeker / self-promotion phrasing in other languages
    r"\b(?:busco|buscando|estoy buscando|necesito)\s+(?:un\s+|mi\s+)?(?:trabajo|empleo|proyectos?|clientes|oportunidad|chamba)\b",
    r"\b(?:procuro|estou procurando|busco)\s+(?:um\s+)?(?:emprego|trabalho|projetos?|clientes|oportunidade|freelas?)\b",
    r"\b(?:je cherche|je recherche)\s+(?:un\s+|du\s+|des\s+)?(?:emploi|travail|missions?|clients|poste|stage)\b",
    r"\b(?:suche|ich suche)\s+(?:einen\s+|eine\s+)?(?:arbeit|job|aufträge|auftraege|stelle|kunden)\b",
    r"\b(?:cerco)\s+(?:un\s+)?(?:lavoro|impiego|clienti|occupazione)\b",
    r"\b(?:soy|sou|je suis|ich bin|sono|jestem|ben bir|saya)\s+(?:un\s+|una\s+|um\s+|uma\s+|ein\s+|eine\s+|a\s+)?(?:freelancer|desarrollador\w*|programador\w*|dise[nñ]ador\w*|redactor\w*|desenvolvedor\w*|developer|entwickler\w*|d[eé]veloppeur\w*|sviluppatore\w*|webdesigner|copywriter|content\s+creator|social media)\b",
    r"(?:ابحث عن (?:عمل|وظيفة|شغل)|أبحث عن (?:عمل|وظيفة)|ابحث عن فرصة|أنا (?:مبرمج|مصمم|كاتب))",
    r"\banyone hiring\b|\bwho(?:'s| is) hiring\b|\bany (?:openings|vacancies)\b",
    r"\b(?:hi|hello|hey)\b.{0,20}\b(?:i'?m|i am|am)\s+(?:a|an)\s+\w+",
    r"\b(?:i|we)\s+(?:can|am here to|are here to|will)\s+(?:help|build|do|provide|handle|take care)\b",
    r"\bavailable for\s+\w+\s+projects\b|\bexperienced\s+\w+(?:\s+\w+)?\s+available\b",
    r"\b(?:sono|soy|me llamo|my name is|je m'appelle|ich hei[sß]e|eu sou)\s+[A-Z]\w+",
    r"\bda \d+ anni\b|\bhace \d+ a[nñ]os\b|\bdepuis \d+ ans\b|\bseit \d+ jahren\b|\bh[aá] \d+ anos\b|\bfor (?:the )?(?:last|past) \d+ years\b",
    r"\bneed income\b|\bearn(?:ing)? (?:an )?income\b|\blooking for ways to earn\b",
    r"\blooking for (?:a )?new opportunit\w+\b|\bopen for (?:work|projects|opportunities)\b",
    r"\b(?:i|i'?m|i am)\s+(?:need|want|looking for|seeking|searching for|in need of)\s+(?:a|an|any)\s+(?:[\w\-]+\s+){0,3}(?:job|work|gig|internship|position)\b",
    r"\b(?:any|a)\s+(?:[\w\-]+\s+){0,2}(?:job|gig)s?,?\s+(?:can|pm|dm|message|contact)\b",
    r"\bcan (?:pm|dm) me\b|\bpm me\b|\bmessage me for (?:work|jobs)\b",
    r"\bi(?:'m| am) (?:a )?(?:recent )?(?:graduate|student|fresher|beginner|junior)\b",
    r"\bmy (?:cv|resume|portfolio) (?:is|attached)\b|\bsend (?:me )?(?:a )?job\b",
    r"\[\s*for hire\s*\]",
    r"\[\s*offer\s*\]",
    r"\bfor hire\b",
    r"\bhire me\b",
    r"\b(?:i'?m|i am)\s+(?:currently\s+)?available\s+(?:for|to)\b",
    r"\bavailable for (?:hire|work|freelance|projects?|new)\b",
    r"\bopen to (?:work|new|freelance|opportunit\w+|projects?)\b",
    r"\bmy (?:services|portfolio|rates|prices|pricing|skills|experience|gigs?|fiverr|upwork|resume|cv)\b",
    r"\b(?:i|we)\s+(?:offer|provide|specialize|specialise|deliver|create|build|design|develop|write)s?\b(?!\s+(?:a|an|the)?\s*\w*\s*(?:and|but)?\s*need)",
    r"\bi will (?:build|create|design|write|develop|do|make|edit|fix|provide)\b",
    r"\bi can (?:build|create|design|write|develop|do|make|edit|fix|help you|offer|provide|automate)\b",
    r"\b(?:i'?m|i am)\s+(?:a|an)\s+(?:freelanc\w+|experienced|professional|senior|junior|full|web|software|certified|skilled|passionate|seasoned|self-taught|expert|content|graphic|ui|ux)\b",
    r"\bi have \d+\+? years?\b",
    r"\b\d+\+? years? of experience\b.*\b(?:i|my)\b",
    r"\b(?:looking|seeking|searching) for (?:work|clients?|customers?|projects?|gigs?|opportunit\w+|freelance (?:work|projects?|opportunit\w+|jobs?)|a (?:job|role|position|new role|remote job))\b",
    r"\bseeking work\b",
    r"\bseeking (?:new )?(?:clients?|customers?|projects?|opportunit\w+)\b",
    r"\bhire (?:a |an )?(?:freelance )?\w+ (?:with|who has|from)\s+\d+\b",
    r"\bdm me (?:for|to) (?:my |a )?(?:services|quote|rates|portfolio|work)\b",
    r"\bcheck out my\b",
    r"\bwe are (?:a|an)\s+(?:\w+\s+){0,3}(?:agency|studio|company|team|firm)\b(?!.*\bhiring\b)",
    r"\bour (?:services|agency|team of|company offers)\b",
    r"\bmy name is\b.{0,80}\b(?:developer|designer|writer|freelancer|engineer)\b",
    r"\bfree (?:audit|consultation|quote|trial)\b",
    r"\bget (?:your|a) (?:website|app|logo)\s+(?:built|designed)\s+(?:by|from|for \$)\b",
    r"\bstarting at \$\d",
    r"\bpromo(?:tion)?\b|\bdiscount\b|\bdon'?t miss\b|\blimited (?:time )?offer\b",
    r"\bcurrently accepting\b|\btaking on (?:new )?(?:clients|projects)\b",
    r"\bbook a (?:free )?(?:call|demo)\b",
    r"\bI\s*\(.*\)\s*am\b.*\bfor hire\b",
]]

# Non-lead noise: tutorials, questions about learning, showcases, memes.
NOISE_PATTERNS = [re.compile(p, I) for p in [
    r"\bhow (?:do|can|to|should) (?:i|we|you)\s+(?:learn|start|become|get started|get into)\b",
    r"\bshow ?hn\b|\bshowcase\b|\bshow ?off\b|\bi (?:revived|rebuilt|redesigned|shipped|open[- ]?sourced)\b|\bi (?:built|made|created|launched|just launched)\b",
    r"\bjob (?:search|hunt)\b|\bresume review\b|\bcv review\b|\binterview (?:tips|questions)\b",
    r"\bwhat (?:do|are) you (?:think|using)\b",
    r"\b(?:rate|roast|review) my\b",
    r"\bunemployed\b|\blaid off\b|\blayoffs?\b",
    r"\bhomework\b|\bcollege project\b|\bschool project\b|\bassignment\b",
    r"\btutorial\b|\bcourse\b|\bbootcamp\b|\bcertification\b",
    r"\bwhich (?:framework|language|stack) (?:should|to)\b",
    r"\bsurvey\b|\bacademic research\b|\bquestionnaire\b|\bstudy participants?\b|\bresearch study\b|\bthesis\b",
    r"^\s*(?:built|made|launched|created|introducing|announcing|released|just (?:built|launched|released))\b",
    r"\bhow (?:to|can i|do i|should i|would i)\s+(?!find|hire|get (?:a|an|someone)|choose|pick|outsource|source)\w+",
    r"\bwhat (?:no[- ]?code|tool|app|software|platform|ai|service)s?\b.*\?",
    r"\bsamples?\b.*\b(?:beginner|first)\b|\bfor (?:beginners|a beginner)\b",
    r"\bcommunity\b.*\b(?:build|grow|join)\b",
]]

PAY_SIGNAL = re.compile(r"(?:hiring|hire|budget|willing to pay|paid|pay(?:ing)?\s+\$?\d|per hour|/hr)|[$€£]\s?\d", I)

JOB_ROLE_WORD = re.compile(r"\b(?:developer|engineer|programmer|architect|scientist|analyst|designer|writer|automation)\b", I)
JOB_EXCLUDE = re.compile(
    r"\b(?:physician|licensed|medical|pharmac\w+|photograph\w+|attorney|lawyer|claims|trainer|annotator|evaluator|"
    r"rater|tutor|teacher|instructor|therapist|clinician|radiolog\w+)\b|"
    r"\b(customer success|account (?:executive|manager|development|director)|sales (?:manager|rep|director|executive)|"
    r"recruit\w*|people operations|talent (?:acquisition|partner)|human resources|\bhr\b|payroll|legal counsel|"
    r"accountant|nurse|driver|warehouse|technician|inside sales|business development|support specialist|"
    r"onboarding|solutions? consultant|implementation manager|account coordinator|office manager)\b", I)

EMPLOYMENT_RE = re.compile(r"\b(?:join our (?:growing |amazing )?team|full[- ]?time|salary|benefits|resume|apply (?:now|today|here)|"
                           r"years? of experience|permanent|employee)\b", I)

ONSITE_RE = re.compile(r"\b(?:on-?site|in[- ]person|in[- ]office|must (?:live|reside|be located)|hybrid|relocat\w+|commute)\b", I)
REMOTE_RE = re.compile(r"\b(?:remote(?:ly)?|work from home|wfh|online|virtual|anywhere|freelance|telecommut\w+|distributed)\b", I)

OWNER_HINT = re.compile(
    r"\b(?:my|our)\s+(?:business|startup|company|store|shop|agency|website|site|app|saas|product|brand|"
    r"clients?|customers?|firm|restaurant|clinic|practice|e-?commerce|channel|podcast|course|platform|team)\b", I)

URGENT_HINT = re.compile(r"\b(?:asap|urgent(?:ly)?|immediately|right away|this week|today|tomorrow|as soon as possible|deadline)\b", I)

# --------------------------------------------------------------------------- #
# Budget, contact, country
# --------------------------------------------------------------------------- #
_CUR = r"(?:USD|US\$|\$|EUR|€|GBP|£|INR|₹|CAD|C\$|AUD|A\$|AED|PKR|Rs\.?|BRL|R\$|NGN|₦|ZAR|SGD|JPY|¥|CHF|MXN|PHP|₱)"
_NUM = r"\d{1,3}(?:[,\s]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
BUDGET_RE = re.compile(
    rf"(?P<full>(?P<c1>{_CUR})\s?(?P<n1>{_NUM})\s?(?P<k1>[kKmM])?\b"
    rf"(?:\s?(?:-|–|—|to)\s?(?:{_CUR})?\s?(?P<n2>{_NUM})\s?(?P<k2>[kKmM])?)?"
    rf"(?P<per>\s?(?:/|per|an?|a)\s?(?:hr|hour|h|month|mo|week|wk|day|project|word|article|page)s?\b)?)", re.I)
BUDGET_POST_RE = re.compile(
    rf"(?P<full>(?:(?P<n0>{_NUM})\s?(?:-|–|—|to)\s?)?(?P<n1>{_NUM})\s?(?P<k1>[kK])?\s?(?P<c1>USD|EUR|GBP|INR|CAD|AUD|dollars|euros|pounds|bucks)\b"
    rf"(?P<per>\s?(?:/|per|an?|a)\s?(?:hr|hour|h|month|mo|week|wk|day|project|word|article|page)s?\b)?)", re.I)
BUDGET_WORD_RE = re.compile(r"\b(?:budget|pay(?:ing)?|rate|salary|compensation)\s*(?:is|of|:|around|about|up to|between)?\s*(?:approx\.?|approximately|~)?\s*(\$|€|£)?\s?(\d[\d,\.]*)\s?([kK])?\b", I)

_FX = {"$": 1, "US$": 1, "USD": 1, "dollars": 1, "bucks": 1, "€": 1.08, "EUR": 1.08, "euros": 1.08,
       "£": 1.27, "GBP": 1.27, "pounds": 1.27, "INR": 0.012, "₹": 0.012, "Rs": 0.0036, "Rs.": 0.0036, "PKR": 0.0036,
       "CAD": 0.73, "C$": 0.73, "AUD": 0.65, "A$": 0.65, "AED": 0.27, "BRL": 0.18, "R$": 0.18, "NGN": 0.00065,
       "₦": 0.00065, "ZAR": 0.055, "SGD": 0.74, "JPY": 0.0066, "¥": 0.0066, "CHF": 1.13, "MXN": 0.05, "PHP": 0.017, "₱": 0.017}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_BAD_EMAIL_TAIL = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")
_BAD_EMAIL_HOST = ("example.com", "example.org", "sentry.io", "wixpress.com", "domain.com", "email.com", "yourdomain.com")
_BAD_EMAIL_USER = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster")
TELEGRAM_RE = re.compile(r"(?:t\.me/|telegram(?:\s*(?:id|handle|username))?\s*[:\-]?\s*@?)([A-Za-z0-9_]{4,32})\b", I)
DISCORD_RE = re.compile(r"discord(?:\s*(?:id|handle|username|tag))?\s*[:\-]\s*([A-Za-z0-9_.#]{3,40})", I)
WHATSAPP_RE = re.compile(r"(?:wa\.me/|whats ?app(?:\s*(?:no\.?|number|#))?\s*[:\-]?\s*)(\+?[\d][\d\s\-]{6,18}\d)", I)
SKYPE_RE = re.compile(r"skype\s*[:\-]\s*([A-Za-z0-9_.:\-]{3,40})", I)
LINK_RE = re.compile(r"https?://[^\s<>\")\]]+", I)
CONTACT_LINK_HOSTS = ("calendly.com", "forms.gle", "docs.google.com/forms", "typeform.com", "airtable.com/shr",
                      "tally.so", "jotform.com", "linkedin.com/in/", "linkedin.com/company/", "cal.com",
                      "wa.me", "t.me", "discord.gg", "discord.com/invite", "notion.site", "jobs.lever.co",
                      "boards.greenhouse.io", "workable.com", "ashbyhq.com")

COUNTRIES: dict[str, list[str]] = {
    "United States": [r"u\.?s\.?a\b", r"united states", r"\bus[- ]based\b", r"\bus only\b", r"\bnew york\b", r"\bnyc\b", r"\bsan francisco\b",
                      r"\blos angeles\b", r"\bchicago\b", r"\bsilicon valley\b", r"\bcalifornia\b", r"\btexas\b", r"\bflorida\b",
                      r"\baustin\b", r"\bseattle\b", r"\bboston\b", r"\bmiami\b", r"\bdenver\b", r"\batlanta\b", r"\bamerican\b",
                      r"\best\b", r"\bpst\b", r"\bcst\b"],
    "United Kingdom": [r"\buk\b", r"united kingdom", r"\bbritain\b", r"\bbritish\b", r"\bengland\b", r"\blondon\b", r"\bmanchester\b",
                       r"\bscotland\b", r"\bwales\b", r"\bgmt\b", r"\bbirmingham\b", r"\bedinburgh\b"],
    "Canada": [r"\bcanada\b", r"\bcanadian\b", r"\btoronto\b", r"\bvancouver\b", r"\bmontreal\b", r"\bottawa\b", r"\bcalgary\b"],
    "Australia": [r"\baustralia\w*\b", r"\bsydney\b", r"\bmelbourne\b", r"\bbrisbane\b", r"\bperth\b", r"\baest\b"],
    "New Zealand": [r"\bnew zealand\b", r"\bauckland\b", r"\bwellington\b", r"\bnz\b"],
    "India": [r"\bindia\w*\b", r"\bmumbai\b", r"\bdelhi\b", r"\bbangalore\b", r"\bbengaluru\b", r"\bhyderabad\b", r"\bchennai\b",
              r"\bpune\b", r"\bkolkata\b", r"\bgurgaon\b", r"\bnoida\b", r"\bist\b"],
    "Pakistan": [r"\bpakistan\w*\b", r"\bkarachi\b", r"\blahore\b", r"\bislamabad\b", r"\brawalpindi\b", r"\bfaisalabad\b"],
    "Bangladesh": [r"\bbangladesh\w*\b", r"\bdhaka\b"],
    "Germany": [r"\bgerman\w*\b", r"\bdeutschland\b", r"\bberlin\b", r"\bmunich\b", r"\bmünchen\b", r"\bhamburg\b", r"\bfrankfurt\b"],
    "France": [r"\bfrance\b", r"\bfrench\b", r"\bparis\b", r"\blyon\b", r"\bmarseille\b", r"\btoulouse\b"],
    "Spain": [r"\bspain\b", r"\bespaña\b", r"\bspanish\b", r"\bmadrid\b", r"\bbarcelona\b", r"\bvalencia\b"],
    "Portugal": [r"\bportugal\b", r"\blisbon\b", r"\blisboa\b", r"\bporto\b"],
    "Italy": [r"\bitaly\b", r"\bitalia\b", r"\bitalian\b", r"\bmilan\w*\b", r"\brome\b", r"\broma\b"],
    "Netherlands": [r"\bnetherlands\b", r"\bdutch\b", r"\bamsterdam\b", r"\brotterdam\b", r"\bholland\b"],
    "Belgium": [r"\bbelgium\b", r"\bbrussels\b"],
    "Switzerland": [r"\bswitzerland\b", r"\bswiss\b", r"\bzurich\b", r"\bgeneva\b"],
    "Austria": [r"\baustria\b", r"\bvienna\b", r"\bwien\b"],
    "Sweden": [r"\bsweden\b", r"\bswedish\b", r"\bstockholm\b"],
    "Norway": [r"\bnorway\b", r"\boslo\b"],
    "Denmark": [r"\bdenmark\b", r"\bcopenhagen\b"],
    "Finland": [r"\bfinland\b", r"\bhelsinki\b"],
    "Ireland": [r"\bireland\b", r"\bdublin\b", r"\birish\b"],
    "Poland": [r"\bpoland\b", r"\bpolish\b", r"\bwarsaw\b", r"\bkrak[oó]w\b"],
    "Ukraine": [r"\bukrain\w*\b", r"\bkyiv\b", r"\bkiev\b"],
    "Russia": [r"\brussia\w*\b", r"\bmoscow\b"],
    "Turkey": [r"\bturkey\b", r"\bturkiye\b", r"\btürkiye\b", r"\bistanbul\b", r"\bankara\b"],
    "Greece": [r"\bgreece\b", r"\bathens\b"],
    "Romania": [r"\bromania\b", r"\bbucharest\b"],
    "Czechia": [r"\bczech\w*\b", r"\bprague\b"],
    "Israel": [r"\bisrael\w*\b", r"\btel aviv\b"],
    "UAE": [r"\buae\b", r"\bdubai\b", r"\babu dhabi\b", r"united arab emirates", r"\bsharjah\b"],
    "Saudi Arabia": [r"\bsaudi\b", r"\briyadh\b", r"\bjeddah\b"],
    "Qatar": [r"\bqatar\b", r"\bdoha\b"],
    "Egypt": [r"\begypt\w*\b", r"\bcairo\b"],
    "Nigeria": [r"\bnigeria\w*\b", r"\blagos\b", r"\babuja\b"],
    "Kenya": [r"\bkenya\w*\b", r"\bnairobi\b"],
    "South Africa": [r"\bsouth africa\w*\b", r"\bcape town\b", r"\bjohannesburg\b"],
    "Ghana": [r"\bghana\b", r"\baccra\b"],
    "Morocco": [r"\bmorocco\b", r"\bcasablanca\b"],
    "Brazil": [r"\bbrazil\w*\b", r"\bbrasil\b", r"\bsão paulo\b", r"\bsao paulo\b", r"\brio de janeiro\b"],
    "Mexico": [r"\bmexic\w*\b", r"\bméxico\b", r"\bcdmx\b", r"\bguadalajara\b"],
    "Argentina": [r"\bargentin\w*\b", r"\bbuenos aires\b"],
    "Colombia": [r"\bcolombia\w*\b", r"\bbogot[aá]\b", r"\bmedell[ií]n\b"],
    "Chile": [r"\bchile\b", r"\bsantiago\b"],
    "Peru": [r"\bperu\b", r"\blima\b"],
    "Singapore": [r"\bsingapore\b"],
    "Malaysia": [r"\bmalaysia\w*\b", r"\bkuala lumpur\b"],
    "Indonesia": [r"\bindonesia\w*\b", r"\bjakarta\b", r"\bbali\b"],
    "Philippines": [r"\bphilippines\b", r"\bfilipino\b", r"\bmanila\b", r"\bcebu\b"],
    "Vietnam": [r"\bvietnam\w*\b", r"\bhanoi\b", r"\bho chi minh\b"],
    "Thailand": [r"\bthailand\b", r"\bbangkok\b", r"\bthai\b"],
    "Japan": [r"\bjapan\w*\b", r"\btokyo\b", r"\bosaka\b"],
    "South Korea": [r"\bsouth korea\w*\b", r"\bseoul\b", r"\bkorea\w*\b"],
    "China": [r"\bchina\b", r"\bchinese\b", r"\bbeijing\b", r"\bshanghai\b", r"\bshenzhen\b", r"\bhong kong\b"],
    "Taiwan": [r"\btaiwan\b", r"\btaipei\b"],
    "Sri Lanka": [r"\bsri lanka\b", r"\bcolombo\b"],
    "Nepal": [r"\bnepal\b", r"\bkathmandu\b"],
}
_COUNTRY_RES = {c: re.compile("|".join(p), I) for c, p in COUNTRIES.items()}
_WORLDWIDE_RE = re.compile(r"\b(?:worldwide|anywhere|global(?:ly)?|work from anywhere|any ?where in the world|all countries|international)\b", I)
_CC_TO_COUNTRY = {"US": "United States", "GB": "United Kingdom", "UK": "United Kingdom", "CA": "Canada", "AU": "Australia",
                  "NZ": "New Zealand", "IN": "India", "PK": "Pakistan", "BD": "Bangladesh", "DE": "Germany", "FR": "France",
                  "ES": "Spain", "PT": "Portugal", "IT": "Italy", "NL": "Netherlands", "BE": "Belgium", "CH": "Switzerland",
                  "AT": "Austria", "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "FI": "Finland", "IE": "Ireland",
                  "PL": "Poland", "UA": "Ukraine", "RU": "Russia", "TR": "Turkey", "GR": "Greece", "RO": "Romania",
                  "CZ": "Czechia", "IL": "Israel", "AE": "UAE", "SA": "Saudi Arabia", "QA": "Qatar", "EG": "Egypt",
                  "NG": "Nigeria", "KE": "Kenya", "ZA": "South Africa", "GH": "Ghana", "MA": "Morocco", "BR": "Brazil",
                  "MX": "Mexico", "AR": "Argentina", "CO": "Colombia", "CL": "Chile", "PE": "Peru", "SG": "Singapore",
                  "MY": "Malaysia", "ID": "Indonesia", "PH": "Philippines", "VN": "Vietnam", "TH": "Thailand", "JP": "Japan",
                  "KR": "South Korea", "CN": "China", "TW": "Taiwan", "LK": "Sri Lanka", "NP": "Nepal"}


def country_from_code(code: str) -> str:
    return _CC_TO_COUNTRY.get((code or "").upper(), "")


_CURRENCY_HINTS = [(re.compile(r"₹|\bINR\b|\brupees?\b|\blakhs?\b|\bcrore\b", I), "India"),
                   (re.compile(r"\bPKR\b|\bRs\.? ?\d", I), "Pakistan"),
                   (re.compile(r"£|\bGBP\b", I), "United Kingdom"),
                   (re.compile(r"\bAUD\b|\bA\$|\bAU\$", I), "Australia"),
                   (re.compile(r"\bCAD\b|\bC\$|\bCA\$", I), "Canada"),
                   (re.compile(r"\bAED\b|\bdirhams?\b", I), "UAE"),
                   (re.compile(r"\bNGN\b|₦", I), "Nigeria"),
                   (re.compile(r"\bZAR\b", I), "South Africa"),
                   (re.compile(r"\bPHP\b|₱", I), "Philippines"),
                   (re.compile(r"\bBRL\b|R\$ ?\d", I), "Brazil"),
                   (re.compile(r"\bMXN\b", I), "Mexico"),
                   (re.compile(r"\bPLN\b|\bzł\b", I), "Poland"),
                   (re.compile(r"\bTRY\b|₺", I), "Turkey"),
                   (re.compile(r"\bIDR\b|\bRp ?\d", I), "Indonesia"),
                   (re.compile(r"₩|\bKRW\b", I), "South Korea"),
                   (re.compile(r"\bJPY\b", I), "Japan"),
                   (re.compile(r"\bBDT\b|৳", I), "Bangladesh"),
                   (re.compile(r"\bKES\b|\bKsh\b", I), "Kenya"),
                   (re.compile(r"\bEGP\b", I), "Egypt")]
_TLD_COUNTRY = {"in": "India", "pk": "Pakistan", "uk": "United Kingdom", "au": "Australia", "ca": "Canada", "de": "Germany",
                "fr": "France", "es": "Spain", "it": "Italy", "nl": "Netherlands", "br": "Brazil", "mx": "Mexico",
                "ng": "Nigeria", "za": "South Africa", "ke": "Kenya", "ph": "Philippines", "id": "Indonesia",
                "pl": "Poland", "tr": "Turkey", "jp": "Japan", "kr": "South Korea", "bd": "Bangladesh",
                "ae": "UAE", "sg": "Singapore", "eg": "Egypt", "se": "Sweden", "ie": "Ireland", "nz": "New Zealand"}
_TLD_RE = re.compile(r"[\w.-]+@[\w-]+(?:\.[\w-]+)*?\.([a-z]{2})\b|https?://[\w.-]+?\.([a-z]{2})(?:/|\s|$)", I)
_PHONE_PREFIX = [(re.compile(r"\+91[\s-]?\d"), "India"), (re.compile(r"\+92[\s-]?\d"), "Pakistan"),
                 (re.compile(r"\+44[\s-]?\d"), "United Kingdom"), (re.compile(r"\+61[\s-]?\d"), "Australia"),
                 (re.compile(r"\+971[\s-]?\d"), "UAE"), (re.compile(r"\+234[\s-]?\d"), "Nigeria"),
                 (re.compile(r"\+63[\s-]?\d"), "Philippines"), (re.compile(r"\+880[\s-]?\d"), "Bangladesh"),
                 (re.compile(r"\+27[\s-]?\d"), "South Africa"), (re.compile(r"\+254[\s-]?\d"), "Kenya")]
_TZ_COUNTRY = [(re.compile(r"\b(?:IST|India Standard Time)\b"), "India"), (re.compile(r"\bPKT\b"), "Pakistan"),
               (re.compile(r"\b(?:AEST|AEDT)\b"), "Australia"),
               (re.compile(r"\bSAST\b"), "South Africa"), (re.compile(r"\bPHT\b"), "Philippines")]
_LANG_TO_COUNTRY = {"ja": "Japan", "ko": "South Korea", "tr": "Turkey", "pl": "Poland", "id": "Indonesia",
                    "vi": "Vietnam", "uk": "Ukraine", "th": "Thailand"}


def _country_hints(text: str) -> str:
    """Weaker signals used only when no country/city is named: currency, email/site TLD, phone code, timezone, language."""
    head = (text or "")[:3000]
    for rx, c in _CURRENCY_HINTS:
        if rx.search(head):
            return c
    for rx, c in _PHONE_PREFIX:
        if rx.search(head):
            return c
    for rx, c in _TZ_COUNTRY:
        if rx.search(head):
            return c
    for m in _TLD_RE.finditer(head):
        c = _TLD_COUNTRY.get((m.group(1) or m.group(2) or "").lower())
        if c:
            return c
    return _LANG_TO_COUNTRY.get(detect_language(head), "")


_SCRIPTS = [("ja", re.compile(r"[぀-ヿ]")), ("ko", re.compile(r"[가-힯]")),
            ("zh", re.compile(r"[一-鿿]")), ("ar", re.compile(r"[؀-ۿ]")),
            ("hi", re.compile(r"[ऀ-ॿ]")), ("th", re.compile(r"[฀-๿]")),
            ("ru", re.compile(r"[Ѐ-ӿ]"))]
_STOP = {
    "en": "the and for with you your need looking want have are this that from will can our please",
    "es": "el la los las para con una que busco necesito por del como más está tengo",
    "pt": "para com uma que preciso procuro não você mais está tenho meu seu",
    "fr": "le la les pour avec une que je cherche besoin pas vous dans est sur nous",
    "de": "der die das und für mit ich suche brauche nicht ein eine wir sie auf",
    "it": "il che per con una cerco ho bisogno non sono della sul nel anche",
    "nl": "het een voor met zoek ik niet van naar ons deze die",
    "id": "yang dan untuk dengan saya mencari butuh tidak ini itu dari pada",
    "tr": "bir ve için ile arıyorum yapmak istiyorum bu çok daha ben",
    "pl": "szukam potrzebuję nie jest dla się oraz jak czy mam",
    "vi": "tôi cần tìm một và của cho không là có những",
}
_STOP_SETS = {k: set(v.split()) for k, v in _STOP.items()}


def detect_language(text: str) -> str:
    """Cheap language guess (ISO-639-1) from script + stopword hits. '' when unsure."""
    t = (text or "")[:1500]
    if not t.strip():
        return ""
    for code, rx in _SCRIPTS:
        if len(rx.findall(t)) >= 3:
            if code == "ru" and re.search(r"[іїєґ]", t, I):
                return "uk"
            return code
    words = re.findall(r"[^\W\d_]+", t.lower())
    if not words:
        return ""
    best, best_n = "", 0
    for code, stop in _STOP_SETS.items():
        n = sum(1 for w in words if w in stop)
        if n > best_n:
            best, best_n = code, n
    return best if best_n >= 2 else ("en" if len(words) >= 5 else "")


def detect_country(text: str, hint: str = "") -> str:
    return _detect_country_named(text, hint) or _country_hints(text)


def _detect_country_named(text: str, hint: str = "") -> str:
    for src in (hint, text):
        if not src:
            continue
        best, best_pos = "", 10 ** 9
        for country, rx in _COUNTRY_RES.items():
            m = rx.search(src)
            if m and m.start() < best_pos:
                best, best_pos = country, m.start()
        if best:
            return best
        if src is hint and _WORLDWIDE_RE.search(hint):
            return "Worldwide"
    if _WORLDWIDE_RE.search(text[:1500] if text else ""):
        return "Worldwide"
    return ""


def _num(s: str) -> float:
    s = s.replace(",", "").replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def extract_budget(text: str) -> tuple[str, float | None]:
    """Return (budget_text, approx_usd_total_or_None). Hourly/monthly rates keep text only."""
    if not text:
        return "", None
    m = BUDGET_RE.search(text)
    cur_key = None
    if m:
        cur_key = m.group("c1")
        n1, k1, n2, k2, per = m.group("n1"), m.group("k1"), m.group("n2"), m.group("k2"), m.group("per")
        full = m.group("full")
    else:
        m = BUDGET_POST_RE.search(text)
        if not m:
            m2 = BUDGET_WORD_RE.search(text)
            if m2 and _num(m2.group(2)) >= 5:
                cur = m2.group(1) or "$"
                val = _num(m2.group(2)) * (1000 if m2.group(3) else 1)
                return f"{cur}{m2.group(2)}{m2.group(3) or ''}", val * _FX.get(cur, 1)
            return "", None
        cur_key = m.group("c1")
        n1, k1, n2, k2, per = m.group("n1"), m.group("k1"), None, None, m.group("per")
        n0 = m.group("n0")
        if n0 and k1 and _num(n0) < _num(n1):
            n1 = n0                 # "5-10k USD" -> low end 5k
        full = m.group("full")
    val = _num(n1)
    if val <= 0 or val == 0:
        return "", None
    mult = 1000 if k1 and k1.lower() == "k" else 1_000_000 if k1 and k1.lower() == "m" else 1
    val *= mult
    if n2 and k2 and not k1 and _num(n2) > _num(n1):
        val *= 1000 if k2.lower() == "k" else 1_000_000   # "$5-10k" means $5k-$10k
    fx = _FX.get(cur_key, _FX.get((cur_key or "").upper(), 1))
    usd = val * fx
    full = re.sub(r"\s+", " ", full).strip()
    if usd > 2_000_000:
        return full, None           # implausible for a freelance gig; keep the text only
    if per or usd < 5:
        return full, None if per else (usd if usd >= 5 else None)
    return full, usd


def extract_contacts(text: str, author_url: str = "") -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def add(x: str):
        k = x.lower()
        if k not in seen:
            seen.add(k)
            out.append(x)

    if not text:
        return out
    for e in EMAIL_RE.findall(text):
        el = e.lower().rstrip(".")
        user, _, host = el.partition("@")
        if el.endswith(_BAD_EMAIL_TAIL) or host in _BAD_EMAIL_HOST or user in _BAD_EMAIL_USER:
            continue
        add(el)
    for m in TELEGRAM_RE.finditer(text):
        handle = m.group(1)
        if handle.lower() not in {"me", "com", "org", "here", "channel", "group", "link", "username", "handle", "id", "dm", "chat",
                                 "bot", "bots", "admin", "support", "official", "us", "now", "again", "page"}:
            add(f"telegram:@{handle}")
    for m in DISCORD_RE.finditer(text):
        add(f"discord:{m.group(1)}")
    for m in WHATSAPP_RE.finditer(text):
        add("whatsapp:" + re.sub(r"[\s\-]", "", m.group(1)))
    for m in SKYPE_RE.finditer(text):
        add(f"skype:{m.group(1)}")
    for m in LINK_RE.finditer(text):
        link = m.group(0).rstrip(".,;:!?)`'*_\\")
        if any(h in link.lower() for h in CONTACT_LINK_HOSTS):
            add(link)
    return out[:8]


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
def detect_categories(text: str) -> list[str]:
    cats = [c for c, rx in CATEGORY_PATTERNS.items() if rx.search(text)]
    # "developer"/"software"/"platform" alone is a broad hit; keep but order specific first.
    order = list(CATEGORY_PATTERNS)
    return sorted(cats, key=order.index)


def classify_intent(title: str, text: str, force: str = "") -> tuple[str, int, list[str]]:
    """Return (intent, buy_points, reasons). intent in buyer|seller|unknown."""
    if SELLER_TAG.search(title) and not BUYER_TAG.search(title):
        return "seller", 0, ["seller-tag"]
    if force in ("buyer", "seller"):
        return force, 40 if force == "buyer" else 0, [f"forced:{force}"]
    head = f"{title}\n{text[:1800]}"
    reasons: list[str] = []
    buy = sum(1 for rx in BUYER_STRONG if rx.search(head))
    weak = sum(1 for rx in BUYER_WEAK if rx.search(head))
    sell_title = sum(1 for rx in SELLER_PATTERNS if rx.search(title))
    sell_body = sum(1 for rx in SELLER_PATTERNS if rx.search(text[:1800]))
    sell = sell_title * 2 + sell_body
    noise = any(rx.search(title) for rx in NOISE_PATTERNS) or any(rx.search(text[:400]) for rx in (NOISE_PATTERNS[0], NOISE_PATTERNS[7]))
    if noise and not PAY_SIGNAL.search(head):
        return "unknown", 0, ["noise"]
    if buy:
        reasons.append(f"buy:{buy}")
    if sell:
        reasons.append(f"sell:{sell}")
    if buy and sell_title and sell_title >= buy:
        return "seller", 0, reasons
    if buy == 1 and sell_body >= 1 and title.strip().endswith("?"):
        return "seller", 0, reasons + ["question-title-ad"]   # "LOOKING FOR A DEVELOPER? I can help you build…"
    if buy == 1 and sell_body >= 2:
        return "seller", 0, reasons
    if buy and sell > buy * 2 + 1:
        return "seller", 0, reasons
    if buy:
        pts = min(40, 24 + 6 * buy + 2 * min(weak, 3))
        return "buyer", pts, reasons
    if sell:
        return "seller", 0, reasons
    return "unknown", min(10, weak * 3), reasons


def freshness_points(posted_at: datetime | None, now: datetime) -> tuple[int, float | None]:
    if posted_at is None:
        return 8, None
    age_h = max((now - posted_at).total_seconds() / 3600, 0)
    if age_h < 3:
        p = 20
    elif age_h < 12:
        p = 18
    elif age_h < 24:
        p = 15
    elif age_h < 72:
        p = 11
    elif age_h < 168:
        p = 7
    elif age_h < 336:
        p = 3
    else:
        p = 0
    return p, age_h


def score_item(item: RawItem, now: datetime | None = None, cfg: dict | None = None) -> dict | None:
    """Classify + score. Returns a dict of lead fields or None if not a lead."""
    cfg = cfg or {}
    now = now or datetime.now(timezone.utc)
    text = item.text or ""
    title = item.title or ""
    blob = f"{title}\n{text}"

    max_age = cfg.get("max_age_days", 21)
    if item.posted_at and (now - item.posted_at).days > max_age:
        return None

    cats = detect_categories(blob)
    if item.extra_tags:
        tag_blob = " ".join(item.extra_tags)
        for c in detect_categories(tag_blob):
            if c not in cats:
                cats.append(c)
    strong_cats = [c for c in cats if c not in WEAK_CATEGORIES]
    if not cats:
        return None
    if ONSITE_RE.search(blob) and not REMOTE_RE.search(blob):
        return None            # on-site work is not remote work
    if item.source in ("tinyfish", "mastodon") and EMPLOYMENT_RE.search(blob) and not REMOTE_RE.search(blob):
        return None            # local employee hiring, not a remote services lead
    if item.lead_type == "job" and item.source in ("tinyfish", "mastodon") and not REMOTE_RE.search(blob):
        return None            # scraped job ads must say they are remote
    if item.lead_type != "job":
        # the wanted service must be named near the top, not buried in a long post
        head_cats = [c for c in detect_categories(f"{title}\n{text[:700]}") if c not in WEAK_CATEGORIES]
        if not head_cats:
            return None

    if item.lead_type == "job":
        intent = "buyer"
        # A job is only a useful lead when the ROLE (title / first line) is a remote service we can sell.
        role_blob = f"{title}\n{text[:200]}" if item.source == "hackernews" else title
        if JOB_EXCLUDE.search(title):
            return None
        role_cats = [c for c in detect_categories(role_blob) if c not in WEAK_CATEGORIES]
        if not role_cats:
            return None
        if role_cats == ["ai_ml"] and not JOB_ROLE_WORD.search(title):
            return None        # "AI trainer", "AI risk manager"… not a build/write/design service
        # Contract/freelance openings are better leads than full-time employment.
        low = blob.lower()
        pts = 30 if re.search(r"\b(freelanc\w+|contract(or)?|part[- ]?time|project[- ]based|gig|consult\w+)\b", low) else 16
        reasons = ["job-opening"]
        if not strong_cats:
            return None
    else:
        intent, pts, reasons = classify_intent(title, text, item.force_intent)
        if intent != "buyer":
            return None
        if not strong_cats and item.force_intent != "buyer":
            return None

    fresh, age_h = freshness_points(item.posted_at, now)
    cat_pts = 12 if len(strong_cats) <= 1 else 16 if len(strong_cats) == 2 else 20
    budget_text, budget_usd = extract_budget(blob)
    contacts = extract_contacts(blob + ("\n" + item.contact_hint if item.contact_hint else ""), item.author_url)
    if item.contact_hint and item.contact_hint not in contacts and item.contact_hint.startswith("http"):
        contacts.append(item.contact_hint)
    budget_pts = 0
    if budget_text:
        budget_pts = 10 if (budget_usd or 0) >= 100 or budget_usd is None else 6
    contact_pts = 5 if contacts else 2
    owner_pts = 5 if OWNER_HINT.search(blob) else 0
    urgent_pts = 3 if URGENT_HINT.search(blob) else 0
    score = min(100, pts + cat_pts + fresh + budget_pts + contact_pts + owner_pts + urgent_pts)
    if item.lead_type == "job":
        score = min(score, 88)

    country = detect_country(blob, item.location_hint)
    return {
        "language": detect_language(blob),
        "categories": strong_cats or cats,
        "score": int(score),
        "budget_text": budget_text,
        "budget_usd": budget_usd,
        "contact": " | ".join(contacts),
        "country": country,
        "age_hours": age_h,
        "reasons": reasons + ([f"owner"] if owner_pts else []) + (["urgent"] if urgent_pts else []),
    }
