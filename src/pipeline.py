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
from html import entities as html_entities

try:
    import feedparser
except ImportError:
    feedparser = None

import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

def _read(url, timeout=25, log=None):
    """Fetch bytes ourselves so we can set a UA and repair malformed XML."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if log:
            log(f"      _read ok: {len(data)} bytes, ctype={r.headers.get('Content-Type','?')}")
        return data
    except Exception as ex:
        if log:
            log(f"      _read FAILED: {type(ex).__name__}: {str(ex)[:90]}")
        return None

XML_PREDEFINED = {"amp", "lt", "gt", "quot", "apos"}

def _scrub(data):
    """Repair feeds that are valid HTML but invalid XML.

    The common killer is named HTML entities (&nbsp; &mdash; &rsquo;). XML
    predefines only amp/lt/gt/quot/apos; everything else is an undefined
    entity and aborts the parse, discarding every item after that point.
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
    t = re.sub(r"[^a-z0-9 ]", " ", (title or "").lower())
    return {w for w in t.split() if len(w) > 2 and w not in STOP}


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
        txt = re.sub(r"\s+", " ", txt).strip()
        if len(txt) > 60:
            return txt[:maxlen]
    txt = re.sub(r"<[^>]+>", " ", body)
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
            raw_bytes = _read(s["url"], log=log)
            d = feedparser.parse(raw_bytes if raw_bytes else s["url"])
            if getattr(d, "bozo", 0) and not d.entries and raw_bytes:
                log("      strict parse failed, trying scrub...")
                d = feedparser.parse(_scrub(raw_bytes))
                log(f"      after scrub: {len(d.entries)} entries")
            if getattr(d, "bozo", 0) and not d.entries:
                errors.append({"source": s["name"], "error": str(getattr(d, "bozo_exception", "parse error"))})
                log(f"  ! {s['name']}: no entries ({getattr(d, 'bozo_exception', '')})")
                continue
            n = 0
            for e in d.entries[:limit_per_feed]:
                title = norm(getattr(e, "title", ""))
                if not title:
                    continue
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
        except Exception as ex:
            errors.append({"source": s["name"], "error": str(ex)})
            log(f"  ! {s['name']}: {ex}")
    return items, errors


# ---------------------------------------------------------------- gate
def gate(items, cfg):
    g = cfg["gate"]
    ent = cfg["entities"]
    # An entity name is itself proof of being in-domain. Without this, items
    # like "Sentinel ICBM clears Milestone B" fail the generic keyword gate.
    terms = list(g["terms"]) + ent["tier1"] + ent["tier2"] + ent["tier3"]
    kept, filtered = [], []
    for it in items:
        blob = f"{it['title']} {it['summary']}"
        h = hits(blob, terms)
        if len(h) >= g.get("min_terms", 1):
            it["_gate_hits"] = h[:6]
            kept.append(it)
        else:
            filtered.append({"title": it["title"], "source": it["source"], "link": it["link"]})
    return kept, filtered


# ---------------------------------------------------------------- clustering
def cluster(items, min_shared=3, overlap=0.40, tight_overlap=0.50):
    """Group near-duplicate stories across sources.

    Uses the overlap coefficient (intersection / smaller set) rather than
    Jaccard: headlines for the same story vary a lot in length and phrasing
    ("Army awards Lockheed $50 billion PAC-3 contract" vs "Lockheed lands
    $50B Army deal"), and Jaccard punishes that variation hard enough that
    real duplicates never merge - which silently kills the corroboration
    signal the whole ranking depends on.
    """
    clusters = []
    for it in sorted(items, key=lambda x: x["published"], reverse=True):
        tk = tokens(it["title"])
        if not tk:
            clusters.append({"items": [it], "tokens": tk, "seed": tk})
            continue
        placed = False
        for c in clusters:
            seed = c["seed"] or c["tokens"]
            inter = tk & seed
            if not inter:
                continue
            ov = len(inter) / max(min(len(tk), len(seed)), 1)
            if (len(inter) >= min_shared and ov >= overlap) or \
               (len(inter) >= 2 and ov >= tight_overlap):
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
        "score_raw": round(total + pen, 2),
        "why": why,
        "n_sources": distinct,
        "is_analysis": is_analysis,
        "dib": bool(dib_hits),
        "usd": usd,
        "age_days": round(age_days, 2),
        "stale": stale,
    }


def route(c, cfg):
    """Assign exactly one section: highest term-match score, ties to lower priority number."""
    blob = " ".join(f"{i['title']} {i['summary']}" for i in c["items"])
    hint = set()
    for i in c["items"]:
        hint |= set(i.get("hint") or [])
    best, best_key = None, None
    for key, sconf in cfg["sections"].items():
        if key.startswith("_") or not isinstance(sconf, dict):
            continue
        h = hits(blob, sconf["terms"])
        sc = len(h) * 2 + (1 if key in hint else 0)
        if sc == 0:
            continue
        cand = (sc, -sconf["priority"])
        if best is None or cand > best:
            best, best_key = cand, key
    if best_key is None:
        # analysis with no term match still belongs somewhere
        best_key = "thinktank" if all(i["kind"] == "analysis" for i in c["items"]) else "budget"
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
        out.append({
            "id": slug(lead["link"] or lead["title"]),
            "title": lead["title"],
            "link": lead["link"],
            "summary": lead["summary"][:300],
            "lead_source": lead["source"],
            "sources": sorted({i["source"] for i in c["items"]}),
            "published": lead["published"],
            "section": route(c, cfg),
            **sc,
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out, filtered
