"""
Weekly compilation - the input to the weekly research synthesis.

The analysis itself is written by a scheduled Claude research session, which
can verify items against primary sources, check whether a silence is real,
search for what the feeds missed, and read the holdco's project files. This
module does the part that has to happen on every build: accumulate the
week, and publish it in one file that session can read.

Published with the site each build (the workflow has read-only repository
access, so state lives on Pages and is read back next build):

  archive.json      every item that has appeared on the page in the last three
                    weeks. The page itself only holds a rolling window.
  watch_state.json  when each watchlist topic was last seen, so absence can be
                    counted in weeks beyond the archive window.
  week.json         the compiled last seven days: items, contract tape,
                    watchlist status, market moves, and the thesis. This is
                    what the Monday research session reads.

A Pages deploy replaces the whole site, so a build that failed to read
archive.json or watch_state.json and carried on would silently erase the
history. If either cannot be read for any reason other than "not published
yet", the build stops; the live site stays as it was and the next build
retries.

Watchlist counts and silences are computed here, not written by a model.
Silence means no matching item in the monitored sources, not that nothing
happened - the research session is told to check.
"""
import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.request

KEEP = ("id", "title", "link", "summary", "lead_source", "sources", "section", "published",
        "usd", "is_award", "bids", "provenance", "relevance", "score_raw", "is_analysis",
        "n_sources")


class HistoryUnavailable(RuntimeError):
    """Published state exists but could not be read; do not overwrite it."""


def _parse(ts):
    try:
        t = dt.datetime.fromisoformat(str(ts))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def site_url():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo and "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}/"
    return ""


def remote_json(name, default, pub, log=print):
    """Read a file this site published on its previous build."""
    base = site_url()
    if not base:
        p = pub / name
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                pass
        return default
    url = f"{base}{name}?t={int(time.time())}"
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "DefenseBrief"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as ex:
            if ex.code == 404:
                log(f"  weekly: {name} not published yet - starting fresh")
                return default
            last = ex
        except Exception as ex:  # noqa: BLE001
            last = ex
        time.sleep(4 * (attempt + 1))
    raise HistoryUnavailable(f"could not read {name} ({type(last).__name__}); "
                             f"stopping so the published copy is not overwritten")


def update_archive(archive, digest, now, keep_days=21):
    for d in digest:
        if not d.get("id") or not d.get("published"):
            continue
        rec = {k: d.get(k) for k in KEEP}
        rec["summary"] = (rec.get("summary") or "")[:300]
        prev = archive.get(d["id"])
        rec["first_seen"] = prev.get("first_seen") if prev else now.isoformat()
        if prev and (prev.get("score_raw") or 0) > (rec.get("score_raw") or 0):
            rec["score_raw"] = prev["score_raw"]
        archive[d["id"]] = rec
    cutoff = now - dt.timedelta(days=keep_days)
    return {k: v for k, v in archive.items() if (_parse(v.get("published")) or now) >= cutoff}


def _matches(item, terms):
    blob = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    return any(t in blob for t in terms)


def update_watch(state, items, watchlist, now):
    state = dict(state or {})
    state.setdefault("tracking_since", now.isoformat())
    topics = state.setdefault("topics", {})
    for t in watchlist.get("topics", []):
        rec = topics.setdefault(t["name"], {"last_seen": None})
        terms = [x.lower() for x in t.get("terms", [])]
        for it in items:
            if _matches(it, terms):
                p = it.get("published")
                if p and (rec["last_seen"] is None or p > rec["last_seen"]):
                    rec["last_seen"] = p
    return state


def _tk(t):
    stop = {"the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with", "at", "by", "from",
            "as", "is", "are", "be", "was", "will", "its", "it", "that", "this", "has", "have", "up"}
    out = set()
    for w in re.sub(r"[^a-z0-9 ]", " ", (t or "").lower()).split():
        if len(w) > 3 and w not in stop:
            out.add(w[:-1] if w.endswith("s") and len(w) > 4 else w)
    return out


def _same(a, b):
    """One story written up twice: the same precise dollar figure and two shared
    headline words, or three shared words covering 60% of the shorter headline.
    Mirrors build._same_story."""
    ta, tb = _tk(a.get("title")), _tk(b.get("title"))
    shared = ta & tb
    ua, ub = a.get("usd"), b.get("usd")
    if ua and ub and len(shared) >= 2 and abs(ua - ub) <= 0.02 * max(ua, ub):
        return True
    if ua and ub and max(ua, ub) > 1.5 * min(ua, ub):
        return False        # different dollar figures: different awards
    return len(shared) >= 3 and len(shared) / max(min(len(ta), len(tb)), 1) >= 0.6


def week_items(archive, now, days=7):
    """Items published in the window, highest score first. Repeat write-ups of
    one story (which the archive can hold under different ids across builds)
    fold into the highest-scoring copy; the others are kept as also_reported."""
    start = now - dt.timedelta(days=days)
    rows = [dict(v) for v in archive.values() if (_parse(v.get("published")) or start) >= start]
    rows.sort(key=lambda x: x.get("score_raw") or 0, reverse=True)
    out, members = [], []
    for r in rows:
        k = next((i for i, grp in enumerate(members) if any(_same(m, r) for m in grp)), None)
        if k is None:
            out.append(r)
            members.append([r])
            continue
        dup = out[k]
        members[k].append(r)
        dup.setdefault("also_reported", []).append(
            {"title": r.get("title"), "link": r.get("link"), "source": r.get("lead_source")})
        dup["sources"] = list(dict.fromkeys((dup.get("sources") or []) + (r.get("sources") or [])))
        dup["n_sources"] = len(dup["sources"])
    return out


def watch_table(state, week, watchlist, now):
    since = _parse(state.get("tracking_since")) or now
    out = []
    for t in watchlist.get("topics", []):
        terms = [x.lower() for x in t.get("terms", [])]
        hits = [it["id"] for it in week if _matches(it, terms)]
        last = _parse((state.get("topics", {}).get(t["name"]) or {}).get("last_seen"))
        if hits:
            weeks, note = 0, ""
        elif last:
            weeks, note = (now - last).days // 7, ""
        else:
            weeks, note = (now - since).days // 7, f"never matched since tracking began {since.date().isoformat()}"
        out.append({"area": t.get("area"), "name": t["name"], "count": len(hits), "item_ids": hits[:12],
                    "weeks_silent": weeks, "note": note,
                    "last_seen": last.date().isoformat() if last else None})
    return out


def contract_tape(week):
    tape = [w for w in week if w.get("is_award")]
    tape.sort(key=lambda x: x.get("usd") or 0, reverse=True)
    return tape[:30]


def market_moves(rows, cfg):
    """COMPUTED: change from the first to the last of the most recent five daily
    closes the market module returns (four sessions). Yields in basis points."""
    yields = set(cfg.get("market", {}).get("yield_symbols", []))
    out = []
    for r in rows or []:
        s = [x for x in (r.get("series") or []) if x is not None]
        if len(s) < 2 or not s[0]:
            continue
        sym = r["symbol"].lstrip("^")
        if r["symbol"] in yields:
            out.append({"symbol": sym, "close": r.get("price"), "date": r.get("date"),
                        "change": round((s[-1] - s[0]) * 100), "unit": "bp"})
        else:
            out.append({"symbol": sym, "close": r.get("price"), "date": r.get("date"),
                        "change": round((s[-1] / s[0] - 1) * 100, 2), "unit": "%"})
    return out


def run(digest, cfg, market_rows, pub, root, now=None, log=print):
    """Every build: update the archive and watch state, publish week.json."""
    now = now or dt.datetime.now(dt.timezone.utc)
    wk = cfg.get("weekly", {})
    if not wk.get("enabled", True):
        return
    try:
        watchlist = json.loads((root / "config" / "watchlist.json").read_text())
    except Exception as ex:  # noqa: BLE001
        log(f"  weekly: watchlist unreadable ({type(ex).__name__}) - absence tracking off")
        watchlist = {"topics": []}

    archive = remote_json("archive.json", {}, pub, log)
    state = remote_json("watch_state.json", {}, pub, log)
    archive = update_archive(archive, digest, now, int(wk.get("archive_days", 21)))
    state = update_watch(state, list(archive.values()), watchlist, now)

    week = week_items(archive, now)
    labels = {k: v["label"] for k, v in cfg["sections"].items() if isinstance(v, dict) and not k.startswith("_")}
    compiled = {
        "_readme": ("Compiled by the Defense & Nuclear Monitor pipeline for the weekly research synthesis. "
                    "items: everything on the page in the window, highest score first. tape: items flagged as "
                    "awards or transactions. watchlist: computed counts; weeks_silent counts weeks with no "
                    "matching item in the monitored sources (a topic with no match since tracking began says so). also_reported: other write-ups of the same story. markets: COMPUTED change across the latest five "
                    "daily closes. provenance: primary = government or company record, release = press "
                    "release, trade = trade press."),
        "generated": now.isoformat(),
        "window": {"start": (now - dt.timedelta(days=7)).isoformat(), "end": now.isoformat()},
        "tracking_since": state.get("tracking_since"),
        "sections": labels,
        "thesis": cfg.get("semantic", {}).get("thesis", ""),
        "counts": {"items": len(week), "awards": sum(1 for w in week if w.get("is_award"))},
        "items": week,
        "tape": contract_tape(week),
        "watchlist": watch_table(state, week, watchlist, now),
        "markets": market_moves(market_rows, cfg),
    }
    (pub / "archive.json").write_text(json.dumps(archive))
    (pub / "watch_state.json").write_text(json.dumps(state))
    (pub / "week.json").write_text(json.dumps(compiled, indent=1))
    log(f"  weekly: archive {len(archive)} items; week.json {len(week)} items, "
        f"{compiled['counts']['awards']} awards")
