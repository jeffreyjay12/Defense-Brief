"""Render the digest to a mobile-first static page."""
import datetime as dt, html, json

CSS = """
:root{
  --bg:#0f1216; --card:#171b21; --card2:#1d222a; --line:#262c36;
  --ink:#e8ecf1; --dim:#98a2b3; --accent:#5b8def; --hot:#e8894a;
  --dib:#7bc48a; --new:#e8894a; --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
}
@media (prefers-color-scheme: light){
  :root{ --bg:#f6f7f9; --card:#fff; --card2:#f0f2f5; --line:#e2e6ec;
         --ink:#12161c; --dim:#5b6675; --accent:#2b5fd0; }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased;padding-bottom:40px}
header{position:sticky;top:0;z-index:10;background:var(--bg);
  border-bottom:1px solid var(--line);padding:14px 16px 10px}
h1{margin:0;font-size:17px;letter-spacing:-.01em}
.meta{color:var(--dim);font-size:12px;margin-top:3px;font-family:var(--mono)}
.wrap{max-width:1440px;margin:0 auto;padding:0 16px}
/* On wide screens use the space for a second column of cards rather than
   stretching summary lines, which hurts readability. */
.items{display:grid;grid-template-columns:1fr;gap:9px;align-items:start}
@media (min-width:1080px){ .items{grid-template-columns:1fr 1fr} }
@media (min-width:1500px){ .items{grid-template-columns:1fr 1fr 1fr} }
.panels{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0}
@media (max-width:700px){ .panels{grid-template-columns:1fr} }
.panel{min-width:0}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 15px}
.panel h2{margin:0 0 9px;font-size:11.5px;text-transform:uppercase;letter-spacing:.08em;
  color:var(--dim);display:flex;justify-content:space-between}
.panel h2 span{text-transform:none;letter-spacing:0;font-family:var(--mono);font-size:10.5px}
.panel ol{margin:0;padding-left:17px}
.panel li{margin:6px 0;font-size:14px;line-height:1.38}
.panel a{color:var(--ink);text-decoration:none}
.panel .lbl{color:var(--dim);font-size:11px;font-family:var(--mono)}
.panel .empty{color:var(--dim);font-size:13px;font-style:italic}
.awards{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:11px 14px;margin:0 0 16px}
.awards h2{margin:0 0 7px;font-size:11.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim)}
.awrow{display:flex;gap:10px;align-items:baseline;padding:5px 0;border-top:1px solid var(--line);font-size:13.5px}
.awrow:first-of-type{border-top:none}
.awrow .amt{font-family:var(--mono);font-size:12px;color:var(--hot);min-width:62px;text-align:right;flex-shrink:0}
.awrow a{color:var(--ink);text-decoration:none;flex:1}
.awrow .sec{font-family:var(--mono);font-size:10.5px;color:var(--dim);flex-shrink:0}
.mkt{border:1px solid var(--line);border-radius:10px;background:var(--card);
  overflow:hidden;margin:0 0 14px;position:relative}
.mkt .mhead{display:flex;justify-content:space-between;align-items:baseline;
  padding:7px 12px 3px;font-family:var(--mono);font-size:10px;color:var(--dim);
  text-transform:uppercase;letter-spacing:.07em}
.mtrack{display:flex;gap:22px;padding:4px 12px 9px;white-space:nowrap;
  animation:mscroll 70s linear infinite;width:max-content}
.mkt:hover .mtrack{animation-play-state:paused}
@keyframes mscroll{from{transform:translateX(0)}to{transform:translateX(-50%)}}
@media (prefers-reduced-motion:reduce){.mtrack{animation:none;overflow-x:auto;width:auto}}
.tk{font-family:var(--mono);font-size:12.5px;display:inline-flex;gap:6px;align-items:baseline}
.tk .sym{font-weight:700}
.tk .px{color:var(--dim)}
.tk .up{color:var(--dib)}
.tk .dn{color:#e06c6c}
.tk .flat{color:var(--dim)}
.tk .spk{opacity:.85;vertical-align:middle;margin:0 1px}
.changed{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:14px 16px;margin:16px 0}
.changed h2{margin:0 0 8px;font-size:12px;text-transform:uppercase;
  letter-spacing:.08em;color:var(--dim)}
.changed li{margin:5px 0;font-size:14.5px}
.changed ul{margin:0;padding-left:18px}
section{margin:22px 0}
.sh{display:flex;align-items:baseline;gap:9px;margin:0 0 11px;
  padding-bottom:6px;border-bottom:2px solid var(--accent)}
.sh h2{margin:0;font-size:16.5px;letter-spacing:-.015em;font-weight:700}
.sh .n{color:var(--dim);font-size:11.5px;font-family:var(--mono);margin-left:auto}
/* per-section accent so the page has rhythm when scrolling */
section[data-sec="triad"] .sh{border-color:#7aa7e8}
section[data-sec="dib"]   .sh{border-color:#7bc48a}
section[data-sec="budget"] .sh{border-color:#c9a227}
section[data-sec="primes"] .sh{border-color:#c58bd6}
section[data-sec="deals"] .sh{border-color:#e8894a}
section[data-sec="nuclear_energy"] .sh{border-color:#5ac8c8}
section[data-sec="tech"] .sh{border-color:#8f9bb3}
section[data-sec="global"] .sh{border-color:#d69b7b}
section[data-sec="thinktank"] .sh{border-color:#9a8fd6}
.item{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:12px 14px}
.item a{color:var(--ink);text-decoration:none;font-weight:600;font-size:15px;
  display:block;margin-bottom:5px;letter-spacing:-.005em}
.items .item:first-child a{font-size:17px;line-height:1.3;letter-spacing:-.015em}
.items .item:first-child .gloss{font-size:14px}
.item a:active{opacity:.6}
.gloss{color:var(--dim);font-size:13.5px;margin:0 0 7px}
.tags{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.tag{font-family:var(--mono);font-size:10.5px;padding:2px 7px;border-radius:99px;
  background:var(--card2);color:var(--dim);border:1px solid var(--line)}
.tag.hot{color:var(--hot);border-color:var(--hot)}
.tag.dib{color:var(--dib);border-color:var(--dib)}
.tag.an{color:var(--accent);border-color:var(--accent)}
.tag.new{color:var(--new);border-color:var(--new);font-weight:700}
details.tail{margin-top:8px}
details.tail summary{cursor:pointer;color:var(--dim);font-size:12.5px;
  font-family:var(--mono);padding:7px 2px;list-style:none}
details.tail summary::-webkit-details-marker{display:none}
details.tail summary:before{content:"▸ ";}
details.tail[open] summary:before{content:"▾ ";}
.tailitem{padding:6px 2px;border-top:1px solid var(--line);font-size:13.5px}
.tailitem a{color:var(--ink);text-decoration:none}
.tailitem .src{color:var(--dim);font-size:11px;font-family:var(--mono)}
footer{color:var(--dim);font-size:11.5px;font-family:var(--mono);
  margin:28px 0 0;padding-top:14px;border-top:1px solid var(--line)}
"""

SECTION_ORDER = ["triad", "dib", "budget", "primes", "deals",
                 "nuclear_energy", "tech", "global", "thinktank"]


def esc(s):
    return html.escape(s or "", quote=True)


def clip(s, n):
    """Truncate on a word boundary with an ellipsis.

    Hard character truncation ended summaries mid-word ("...U.S. la"), which
    reads as a rendering fault rather than a deliberate excerpt.
    """
    s = (s or "").strip()
    if len(s) <= n:
        return s
    cut = s[:n]
    sp = cut.rfind(" ")
    if sp > n * 0.6:
        cut = cut[:sp]
    return cut.rstrip(" ,.;:-\u2014\u2013") + "\u2026"


def summarize(digest, cfg):
    """Deterministic 'what changed' - top scoring items, one line each."""
    top = [d for d in digest if d["score"] >= cfg["display"]["headline_threshold"]][:4]
    if not top:
        top = digest[:3]
    lines = []
    for d in top:
        label = cfg["sections"][d["section"]]["label"]
        lines.append(f"<b>{esc(label)}:</b> {esc(d['title'])}")
    return lines


def _spark(series, w=34, h=12):
    """Inline SVG sparkline. Drawn from the 5-day series the quote already carries."""
    pts = [p for p in (series or []) if p is not None]
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    rng = (hi - lo) or 1.0
    step = w / (len(pts) - 1)
    coords = " ".join(f"{i*step:.1f},{h - (p-lo)/rng*h:.1f}" for i, p in enumerate(pts))
    up = pts[-1] >= pts[0]
    col = "var(--dib)" if up else "#e06c6c"
    return (f'<svg class="spk" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'preserveAspectRatio="none" aria-hidden="true">'
            f'<polyline points="{coords}" fill="none" stroke="{col}" '
            f'stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round"/></svg>')


def market_strip(rows, cfg):
    """Scrolling close-price ticker. Duplicated once so the loop is seamless."""
    if not rows:
        return ""
    mk = cfg.get("market", {})
    order = {s: i for i, s in enumerate(mk.get("symbols", []))}
    rows = sorted(rows, key=lambda r: order.get(r["symbol"], 99))
    stale = any(r.get("stale") for r in rows)
    asof = next((r.get("date") for r in rows if r.get("date")), "")
    cells = []
    yields = set(mk.get("yield_symbols", []))
    for r in rows:
        px, prev = r.get("price"), r.get("prev")
        if px is None:
            continue
        is_yield = r["symbol"] in yields
        if prev:
            if is_yield:
                # Rates quote as a level and move in basis points. Showing a
                # percent change of a percent ("+0.72%" for 4.18 -> 4.21) makes
                # the reader recover 3bp by arithmetic.
                bp = (px - prev) * 100
                cls = "up" if bp > 0.5 else ("dn" if bp < -0.5 else "flat")
                chg = f'<span class="{cls}">{bp:+.0f}bp</span>'
            else:
                pct = (px / prev - 1) * 100
                cls = "up" if pct > 0.05 else ("dn" if pct < -0.05 else "flat")
                chg = f'<span class="{cls}">{pct:+.2f}%</span>'
        else:
            chg = '<span class="flat">&mdash;</span>'
        sym = r["symbol"].lstrip("^")
        if is_yield:
            fmt = f"{px:.2f}%"
        else:
            fmt = f"{px:,.2f}" if px < 1000 else f"{px:,.0f}"
        cells.append(f'<span class="tk"><span class="sym">{esc(sym)}</span>'
                     f'<span class="px">{fmt}</span>{_spark(r.get("series"))}{chg}</span>')
    if not cells:
        return ""
    track = "".join(cells)
    note = "last close" + (f" &middot; {esc(asof)}" if asof else "")
    if stale:
        note += " &middot; cached"
    return (f'<div class="mkt"><div class="mhead"><span>Markets</span><span>{note}</span></div>'
            f'<div class="mtrack">{track}{track}</div></div>')


def render(digest, cfg, errors=None, generated=None, ai_summary=None, new_ids=None,
           market_rows=None):
    generated = generated or dt.datetime.now(dt.timezone.utc)
    disp = cfg["display"]
    new_ids = set(new_ids or [])
    by_sec = {k: [] for k in SECTION_ORDER}
    for d in digest:
        by_sec.setdefault(d["section"], []).append(d)

    parts = [f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0f1216">
<link rel="manifest" href="manifest.json">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>Defense Brief</title><style>{CSS}</style></head><body>
<header><div class="wrap"><h1>Defense Brief</h1>
<div class="meta">{generated.strftime('%a %d %b %Y &middot; %H:%M UTC')} &middot; {len(digest)} stories</div>
</div></header><div class="wrap">"""]

    parts.append(market_strip(market_rows or [], cfg))

    def _panel_items(rows):
        out = []
        for d in rows:
            label = cfg["sections"][d["section"]]["label"]
            age = d.get("age_days", 0)
            when = "today" if age < 1 else f"{int(age)}d ago"
            out.append(f'<li><a href="{esc(d["link"])}" target="_blank" rel="noopener">'
                       f'{esc(d["title"])}</a><br><span class="lbl">{esc(label)} &middot; {when}</span></li>')
        return "<ol>" + "".join(out) + "</ol>"

    parts.append('<div class="panels">')

    # LEFT - delta since the previous build
    parts.append('<div class="panel"><h2>What changed <span>since last update</span></h2>')
    if ai_summary:
        parts.append("<ol>" + "".join(f"<li>{l}</li>" for l in ai_summary) + "</ol>")
    else:
        fresh = [d for d in digest if d["id"] in new_ids][:5]
        if fresh:
            parts.append(_panel_items(fresh))
        elif new_ids:
            parts.append('<p class="empty">Nothing new since the last build.</p>')
        else:
            parts.append(_panel_items(digest[:4]))
    parts.append("</div>")

    # RIGHT - biggest of the week on pre-decay magnitude, so Monday's big story
    # still reads as big on Friday instead of being buried by freshness.
    week = sorted(digest, key=lambda x: x.get("score_raw", x["score"]), reverse=True)[:5]
    parts.append('<div class="panel"><h2>Top stories <span>past 7 days</span></h2>')
    parts.append(_panel_items(week) if week else '<p class="empty">No stories in window.</p>')
    parts.append("</div></div>")

    # Awards strip: transactions from every section, largest first. Scannable
    # row list rather than cards - a different reading mode from the stories.
    aw = cfg.get("awards_strip", {})
    if aw.get("enabled"):
        deals = sorted([d for d in digest if d.get("is_award")],
                       key=lambda x: x.get("usd", 0), reverse=True)[:aw.get("max_items", 12)]
        if deals:
            parts.append('<div class="awards"><h2>Awards &amp; transactions</h2>')
            for d in deals:
                usd = d.get("usd", 0)
                amt = f"${usd/1e9:.1f}B" if usd >= 1e9 else f"${usd/1e6:.0f}M"
                sec = cfg["sections"][d["section"]]["label"]
                parts.append(
                    f'<div class="awrow"><span class="amt">{amt}</span>'
                    f'<a href="{esc(d["link"])}" target="_blank" rel="noopener">{esc(d["title"])}</a>'
                    f'<span class="sec">{esc(sec)}</span></div>')
            parts.append("</div>")

    for key in SECTION_ORDER:
        rows = by_sec.get(key) or []
        if not rows:
            continue
        label = cfg["sections"][key]["label"]
        # Cap noisy sections so one source's editorial calendar cannot dominate
        # the page. Items beyond the cap are dropped from display, not scored out.
        caps = disp.get("max_section_items", {})
        cap = caps.get(key, caps.get("_default", 40))
        rows = rows[:cap]
        heads = [r for r in rows if r["score"] >= disp["headline_threshold"]][:disp["max_headlines_per_section"]]
        if not heads:
            heads = rows[:3]
        # The card grid is up to 3 wide, so a count that isn't a multiple of 3
        # leaves visible gaps. Round down to a full row when we have the items,
        # and top up from the tail when we're one or two short.
        if len(rows) >= 3 and len(heads) % 3:
            want = len(heads) + (3 - len(heads) % 3)
            extra = [r for r in rows if r not in heads][:want - len(heads)]
            heads = heads + extra
        tail = [r for r in rows if r not in heads][:disp["max_tail_per_section"]]

        parts.append(f'<section data-sec="{esc(key)}"><div class="sh"><h2>{esc(label)}</h2>'
                     f'<span class="n">{len(rows)}</span></div>')
        parts.append('<div class="items">')
        for r in heads:
            tags = []
            if r["id"] in new_ids:
                tags.append('<span class="tag new">NEW</span>')
            tags.append(f'<span class="tag">{esc(r["lead_source"])}</span>')
            if r["n_sources"] > 1:
                tags.append(f'<span class="tag hot">{r["n_sources"]} sources</span>')
            if r.get("is_award"):
                tags.append('<span class="tag hot">award</span>')
            if r["dib"]:
                tags.append('<span class="tag dib">industrial base</span>')
            if r["is_analysis"]:
                tags.append('<span class="tag an">analysis</span>')
            if r["usd"] >= 1e8:
                tags.append(f'<span class="tag hot">${r["usd"]/1e9:.1f}B</span>' if r["usd"] >= 1e9
                            else f'<span class="tag hot">${r["usd"]/1e6:.0f}M</span>')
            gloss = f'<p class="gloss">{esc(clip(r["summary"], 210))}</p>' if r["summary"] else ""
            parts.append(
                f'<div class="item"><a href="{esc(r["link"])}" target="_blank" rel="noopener">{esc(r["title"])}</a>'
                f'{gloss}<div class="tags">{"".join(tags)}</div></div>')
        parts.append('</div>')
        if tail:
            parts.append(f'<details class="tail"><summary>{len(tail)} more</summary>')
            for r in tail:
                mark = ' <span class="tag new">NEW</span>' if r["id"] in new_ids else ""
                parts.append(
                    f'<div class="tailitem"><a href="{esc(r["link"])}" target="_blank" rel="noopener">{esc(r["title"])}</a> '
                    f'<span class="src">{esc(r["lead_source"])}</span>{mark}</div>')
            parts.append("</details>")
        parts.append("</section>")

    err = f" &middot; {len(errors)} feed errors" if errors else ""
    parts.append(f'<footer>Generated {generated.strftime("%Y-%m-%d %H:%M UTC")}{err}'
                 f' &middot; {len(new_ids)} new since last build<br>'
                 f'Headlines and links only. Paywalled sources open on the publisher site.</footer>')
    parts.append("</div></body></html>")
    return "".join(parts)


MANIFEST = json.dumps({
    "name": "Defense Brief", "short_name": "Brief", "start_url": ".",
    "display": "standalone", "background_color": "#0f1216", "theme_color": "#0f1216",
    "icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml"}]
}, indent=2)

ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">
<rect width="192" height="192" rx="38" fill="#0f1216"/>
<path d="M96 34 L150 60 V104 C150 134 126 152 96 160 C66 152 42 134 42 104 V60 Z"
 fill="none" stroke="#5b8def" stroke-width="9" stroke-linejoin="round"/>
<path d="M70 96 h52 M70 78 h52 M70 114 h34" stroke="#e8ecf1" stroke-width="8" stroke-linecap="round"/>
</svg>"""
