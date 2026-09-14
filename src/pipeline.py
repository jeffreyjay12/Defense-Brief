"""
Defense news monitor - fetch, cluster, score, route.

Design notes:
  * Ranking is two-dimensional: a permissive GATE (is this in-domain at all)
    then a MAGNITUDE score (is this big). Topical relevance alone never
    promotes an item; magnitude within the domain does.
  * Cross-source corroboration is the primary "is this big" proxy - five
    outlets carrying one story outranks one outlet carrying it.
  * The DIB override exists because the most valuable industrial-base items
    are structurally single-source and corroboration scoring would bury them.
  * 'analysis' sources (Substacks, think tanks, GAO) are exempt from
    corroboration and decay slowly - a weekly deep-dive is never "confirmed
    by five outlets" but may be the most valuable thing in the week.
"""
import json, re, math, hashlib, datetime as dt
from collections import defaultdict
from html import entities as html_entities, unescape as html_unescape
import urllib.request

try:
    import feedparser
except ImportError:
    feedparser = None

STOP = {
    "the","a","an","and","or","of","to","in","for","on","with","at","by","from","as","is","are",
    "be","was","were","will","would","new","says","say","said","after","over","its","it","that",
    "this","has","have","had","not","but","more","than","into","about","us","u.s.","amid"
}


# ---------------------------------------------------------------- utilities
def norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def slug(s):
    return hashlib.sha1((s or "").encode("utf-8", "ignore")).hexdigest()[:12]


def tokens(title):
    """Title tokens, plus a canonical token for any dollar figure.

    "$50 billion" and "$50B" are the same fact but tokenize differently, so
    three reports of one award failed to cluster. Emitting usd50000000000 for
    both gives them a rare shared token - and dollar figures are the strongest
    same-story signal available, since unrelated coverage of the same company
    rarely cites an identical amount.
    """
    t = re.sub(r"[^a-z0-9 ]", " ", (title or "").lower())
    tk = {w for w in t.split() if len(w) > 2 and w not in STOP}
    usd = parse_dollars(title)
    if usd >= 1e6:
        tk.add(f"usd{int(usd)}")
    return tk


def parse_dollars(text):
    """Return the largest dollar figure mentioned, in dollars."""
    if not text:
        return 0.0
    best = 0.0
    pat = r"\$\s?([\d,]+(?:\.\d+)?)\s*(trillion|billion|million|thousand|[tbmk])?\b"
    mult = {"trillion": 1e12, "t": 1e12, "billion": 1e9, "b": 1e9,
            "million": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}
    for m in re.finditer(pat, text, re.I):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        unit = (m.group(2) or "").lower()
        v *= mult.get(unit, 1.0)
        best = max(best, v)
    return best


UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

XML_PREDEFINED = {"amp", "lt", "gt", "quot", "apos"}


def _read(url, timeout=25, log=None, want_error=False):
    """Fetch bytes ourselves so we can set a browser UA and repair the XML."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
            "Connection": "close",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if log:
            log(f"      _read ok: {len(data)} bytes, ctype={r.headers.get('Content-Type','?')}")
        return (data, None) if want_error else data
    except Exception as ex:
        msg = f"{type(ex).__name__}: {str(ex)[:90]}"
        if log:
            log(f"      _read FAILED: {msg}")
        return (None, msg) if want_error else None


def _scrub(data):
    """Repair feeds that are valid HTML but invalid XML.

    The usual killer is named HTML entities (&nbsp; &mdash; &rsquo;). XML
    predefines only amp/lt/gt/quot/apos; anything else is an undefined entity
    and aborts the parse, discarding every item after that point.
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8", "ignore")
    data = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", data)

    def _ent(m):
        name = m.group(1)
        if name in XML_PREDEFINED:
            return m.group(0)
        cp = html_entities.name2codepoint.get(name)
        return f"&#{cp};" if cp else ""

    data = re.sub(r"&([a-zA-Z][a-zA-Z0-9]{0,31});", _ent, data)
    data = re.sub(r"&(?!(?:#\d+;|#x[0-9a-fA-F]+;|amp;|lt;|gt;|quot;|apos;))", "&amp;", data)
    return data


def first_para(html_text, maxlen=400):
    """Extract the lede paragraph from an HTML body.

    Full-text feeds (Inside Defense) wrap articles in nested Drupal divs. The
    lede carries the actual news; the rest is background. Taking the first real
    <p> keeps display clean AND keeps scoring honest - see the note in
    score_cluster about historical dollar figures.
    """
    body = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", html_text or "")
    for para in re.findall(r"(?is)<p[^>]*>(.*?)</p>", body):
        txt = re.sub(r"<[^>]+>", " ", para)
        # Decode entities AFTER stripping tags: feeds carry &#8220; &amp; &nbsp;
        # which otherwise survive as literal text and get re-escaped on render.
        txt = html_unescape(txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        if len(txt) > 60:
            return txt[:maxlen]
    txt = html_unescape(re.sub(r"<[^>]+>", " ", body))
    return re.sub(r"\s+", " ", txt).strip()[:maxlen]


def hits(text, terms):
    low = (text or "").lower()
    return [t for t in terms if t in low]


# ---------------------------------------------------------------- fetching
def fetch(sources, limit_per_feed=40, log=print):
    """Fetch all feeds. Returns (items, errors)."""
    if feedparser is None:
        raise RuntimeError("feedparser not installed")
    items, errors = [], []
    for s in sources:
        try:
            raw_bytes, http_err = _read(s["url"], log=log, want_error=True)
            if raw_bytes is None:
                # Do NOT hand the URL to feedparser here: it refetches, gets the
                # HTML error page and reports a bogus XML parse error.
                errors.append({"source": s["name"], "error": http_err or "fetch failed"})
                log(f"  ! {s['name']}: {http_err or 'fetch failed'}")
                continue
            d = feedparser.parse(raw_bytes)
            if getattr(d, "bozo", 0) and not d.entries and raw_bytes:
                log("      strict parse failed, trying scrub...")
                d = feedparser.parse(_scrub(raw_bytes))
                log(f"      after scrub: {len(d.entries)} entries")
            if getattr(d, "bozo", 0) and not d.entries:
                errors.append({"source": s["name"], "error": str(getattr(d, "bozo_exception", "parse error"))})
                log(f"  ! {s['name']}: no entries ({getattr(d,'bozo_exception','')})")
                continue
            n = 0
            for e in d.entries[:limit_per_feed]:
                title = norm(getattr(e, "title", ""))
                if not title:
                    continue
                # Newsletter tables-of-contents ("Inside the Navy - Sept 14",
                # "INSIDER daily digest") carry no content of their own, just links
                # to that week's articles. Left in, they absorb the real stories
                # during clustering and score on a dozen headlines at once.
                if re.search(r"(?i)\b(inside the (navy|army|air force|pentagon)|"
                             r"daily digest|weekly digest|news briefs?)\b", title):
                    continue
                link = getattr(e, "link", "") or ""
                summary = first_para(getattr(e, "summary", "") or getattr(e, "description", "") or "")
                published = None
                for attr in ("published_parsed", "updated_parsed"):
                    tp = getattr(e, attr, None)
                    if tp:
                        published = dt.datetime(*tp[:6], tzinfo=dt.timezone.utc)
                        break
                if published is None:
                    published = dt.datetime.now(dt.timezone.utc)
                items.append({
                    "id": slug(link or title),
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "source": s["name"],
                    "kind": s.get("kind", "news"),
                    "weight": float(s.get("weight", 1.0)),
                    "hint": s.get("hint", []),
                    "published": published.isoformat(),
                })
                n += 1
            log(f"  + {s['name']}: {n}")
        except Exception as ex:  # noqa: BLE001
            errors.append({"source": s["name"], "error": str(ex)})
            log(f"  ! {s['name']}: {ex}")
    return items, errors


# ---------------------------------------------------------------- gate
def gate(items, cfg):
    g = cfg["gate"]
    ent = cfg["entities"]
    rej = cfg.get("reject", {})
    rej_url = [p.lower() for p in rej.get("url_patterns", [])]
    rej_title = [t.lower() for t in rej.get("title_terms", [])]
    # An entity name is itself proof of being in-domain. Without this, items
    # like "Sentinel ICBM clears Milestone B" fail the generic keyword gate.
    terms = list(g["terms"]) + ent["tier1"] + ent["tier2"] + ent["tier3"]
    kept, filtered = [], []
    for it in items:
        # Hard rejects first. Human-interest features legitimately contain
        # "Army", "Airman", "National Guard", so no keyword gate excludes them -
        # but they live under predictable URL paths, which does.
        link_l = (it.get("link") or "").lower()
        title_l = it["title"].lower()
        summ_l = (it.get("summary") or "").lower()
        rej_summ = [t.lower() for t in rej.get("summary_terms", [])]
        hit = next((p for p in rej_url if p in link_l), None) or \
              next((t for t in rej_title if t in title_l), None) or \
              next((t for t in rej_summ if t in summ_l), None)
        if hit:
            filtered.append({"title": it["title"], "source": it["source"],
                             "link": it["link"], "reason": f"reject: {hit}"})
            continue
        blob = f"{it['title']} {it['summary']}"
        h = hits(blob, terms)
        if len(h) >= g.get("min_terms", 1):
            it["_gate_hits"] = h[:6]
            kept.append(it)
        else:
            filtered.append({"title": it["title"], "source": it["source"],
                             "link": it["link"], "reason": "no domain terms"})
    return kept, filtered


# ---------------------------------------------------------------- clustering
def cluster(items, min_shared=3, overlap=0.40, tight_overlap=0.50):
    """Group near-duplicate stories across sources.

    Similarity is inverse-frequency weighted: a token shared by many headlines
    in the batch ("lockheed", "missile", "defense") is weak evidence, while a
    rare one ("pac-3", "sentinel", "palisades") is strong. Plain token counting
    falsely merged "Army awards Lockheed $50B PAC-3 contract" into "Lockheed
    raises full-year guidance" - they share lockheed/martin/missile/year - which
    silently DELETED the award story. False merges lose news; false splits only
    cost a corroboration bonus, so this errs toward splitting.
    """
    import math
    docs = [(it, tokens(it["title"])) for it in
            sorted(items, key=lambda x: x["published"], reverse=True)]
    n = max(len(docs), 1)
    df = defaultdict(int)
    for _, tk in docs:
        for t in tk:
            df[t] += 1
    idf = {t: math.log(1 + n / c) for t, c in df.items()}

    USD_WEIGHT = 3.0   # supporting evidence only - see the guard below

    def weight(ts):
        return sum(USD_WEIGHT if t.startswith("usd") else idf.get(t, 1.0) for t in ts)

    clusters = []
    for it, tk in docs:
        if not tk:
            clusters.append({"items": [it], "tokens": tk, "seed": tk})
            continue
        placed = False
        for c in clusters:
            seed = c["seed"] or c["tokens"]
            inter = tk & seed
            # A shared dollar figure boosts similarity but must never create a
            # match on its own: Boeing's $50B tanker and Lockheed's $50B PAC-3
            # award share an amount and nothing else. Count only real words
            # toward the threshold; let the amount raise the score.
            inter_words = {t for t in inter if not t.startswith("usd")}
            if len(inter_words) < 2:
                continue
            denom = min(weight(tk), weight(seed)) or 1.0
            ov = weight(inter) / denom
            if len(inter_words) >= min_shared and ov >= overlap:
                c["items"].append(it)
                c["tokens"] |= tk
                placed = True
                break
        if not placed:
            clusters.append({"items": [it], "tokens": set(tk), "seed": set(tk)})
    return clusters


# ---------------------------------------------------------------- scoring
def score_cluster(c, cfg, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    items = c["items"]
    lead = max(items, key=lambda x: x["weight"])
    # Score on title + lede only. Full-text feeds carry years of background in
    # the body: the IFPC laser story's largest figure is a $4.8B cut from 2024,
    # while the actual news is a $2.9M reprogramming. Scoring the whole body
    # would rank it as a multi-billion-dollar story and would also inflate
    # keyword hits for long articles relative to short ones.
    blob = " ".join(f"{i['title']} {i['summary']}" for i in items)
    why = []
    total = 0.0

    # source weight (lead)
    sw = lead["weight"] * 2.0
    total += sw
    why.append(f"source {lead['source']} +{sw:.1f}")

    # analysis base: deep-dives are never corroborated, so without a floor
    # they decay to nothing and never surface.
    is_analysis_early = all(i["kind"] == "analysis" for i in items)
    if is_analysis_early:
        ab = cfg["recency"].get("analysis_base_points", 4)
        total += ab
        why.append(f"analysis base +{ab}")

    # corroboration - news only
    is_analysis = all(i["kind"] == "analysis" for i in items)
    distinct = len({i["source"] for i in items})
    if not is_analysis and distinct > 1:
        cc = cfg["corroboration"]
        pts = min((distinct - 1) * cc["points_per_extra_source"], cc["max_points"])
        total += pts
        why.append(f"{distinct} sources +{pts}")

    # dollar magnitude
    usd = parse_dollars(blob)
    if usd > 0:
        dp = cfg["magnitude"]["dollar_points"]
        pts = 0
        for k in sorted(dp, key=lambda x: float(x)):
            if usd >= float(k):
                pts = dp[k]
        if pts:
            total += pts
            why.append(f"${usd:,.0f} +{pts}")

    # milestone language
    for pts_s, terms in cfg["magnitude"]["milestone_terms"].items():
        h = hits(blob, terms)
        if h:
            total += float(pts_s)
            why.append(f"'{h[0]}' +{pts_s}")
            break

    # entities
    ent = cfg["entities"]
    epts, ehits = 0, []
    for tier, pts_key in (("tier1", "tier1_points"), ("tier2", "tier2_points"), ("tier3", "tier3_points")):
        h = hits(blob, ent[tier])
        if h:
            ehits += h
            epts = max(epts, ent[pts_key])
    if ehits:
        epts += min(len(set(ehits)) - 1, 4) * ent["extra_entity_bonus"]
        epts = min(epts, ent["max_entity_points"])
        total += epts
        why.append(f"entities {', '.join(sorted(set(ehits))[:3])} +{epts}")

    # DIB override
    dib = cfg["dib_override"]
    dib_hits = hits(blob, dib["terms"]) if dib.get("enabled") else []
    if dib_hits:
        total += dib["bonus"]
        why.append(f"DIB '{dib_hits[0]}' +{dib['bonus']}")
        if total < dib["floor_score"]:
            why.append(f"DIB floor -> {dib['floor_score']}")
            total = dib["floor_score"]

    # recency decay
    rc = cfg["recency"]
    try:
        pub = dt.datetime.fromisoformat(lead["published"])
    except Exception:  # noqa: BLE001
        pub = now
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=dt.timezone.utc)
    age_days = max((now - pub).total_seconds() / 86400.0, 0)
    decay = rc["analysis_decay_per_day"] if is_analysis else rc["news_decay_per_day"]
    pen = age_days * decay
    total -= pen
    if pen > 0.5:
        why.append(f"age {age_days:.1f}d -{pen:.1f}")

    max_age = rc["analysis_max_age_days"] if is_analysis else rc["news_max_age_days"]
    stale = age_days > max_age

    return {
        "score": round(total, 2),
        "score_raw": round(total + pen, 2),   # pre-decay: what the weekly panel ranks on
        "why": why,
        "n_sources": distinct,
        "is_analysis": is_analysis,
        "dib": bool(dib_hits),
        "usd": usd,
        "age_days": round(age_days, 2),
        "stale": stale,
    }


def route(c, cfg):
    """Assign exactly one section: highest term-match score, ties to lower priority.

    Program and site names outrank event language. Specificity weighting alone
    scored "milestone b" (2 words) above "sentinel" (1 word), so Sentinel,
    Columbia and Trident stories drained out of the triad section into budget
    whenever they mentioned a procurement milestone. The tier-1 entity list IS
    the triad and weapons complex, so a tier-1 hit claims the story outright.
    """
    blob = " ".join(f"{i['title']} {i['summary']}" for i in c["items"])
    hint = set()
    for i in c["items"]:
        hint |= set(i.get("hint") or [])

    ent = cfg["entities"]
    tier1_hits = hits(blob, ent["tier1"])
    program_bonus = cfg.get("routing", {}).get("tier1_triad_bonus", 8)

    best, best_key = None, None
    for key, sconf in cfg["sections"].items():
        if key.startswith("_") or not isinstance(sconf, dict):
            continue
        h = hits(blob, sconf["terms"])
        sc = sum(len(t.split()) ** 2 for t in h) + (1 if key in hint else 0)
        if key == "triad" and tier1_hits:
            sc += program_bonus + (len(tier1_hits) - 1)
        if sc == 0:
            continue
        cand = (sc, -sconf["priority"])
        if best is None or cand > best:
            best, best_key = cand, key
    if best_key is None:
        # Never default to budget: that turned it into a dump. Unmatched analysis
        # is research. Unmatched NEWS carrying award language is industrial by
        # default - an award with no other subject match is a supplier story,
        # not a think-tank essay.
        if all(i["kind"] == "analysis" for i in c["items"]):
            best_key = "thinktank"
        else:
            aw = cfg.get("awards_strip", {}).get("terms", [])
            best_key = "dib" if hits(blob, aw) else "budget"
    return best_key


def build_digest(items, cfg, now=None):
    kept, filtered = gate(items, cfg)
    cc = cfg.get('clustering', {})
    clusters = cluster(kept,
                       min_shared=cc.get('min_shared_tokens', 3),
                       overlap=cc.get('overlap', 0.40),
                       tight_overlap=cc.get('tight_overlap', 0.50))
    out = []
    for c in clusters:
        sc = score_cluster(c, cfg, now=now)
        if sc["stale"]:
            continue
        lead = max(c["items"], key=lambda x: x["weight"])
        blob_l = " ".join(f"{i['title']} {i['summary']}" for i in c["items"])
        locked = bool(hits(blob_l, cfg["entities"]["tier1"])) and \
            cfg.get("routing", {}).get("lock_tier1_to_triad", True)
        aw = cfg.get("awards_strip", {})
        is_award = bool(aw.get("enabled")) and bool(hits(blob_l, aw.get("terms", []))) \
            and sc["usd"] >= aw.get("min_usd", 1e6)
        out.append({
            "is_award": is_award,
            "_locked": locked,
            "id": slug(lead["link"] or lead["title"]),
            "title": lead["title"],
            "link": lead["link"],
            "summary": lead["summary"][:300],
            "lead_source": lead["source"],
            "sources": sorted({i["source"] for i in c["items"]}),
            "published": lead["published"],
            "section": "triad" if locked else route(c, cfg),
            **sc,
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out, filtered
