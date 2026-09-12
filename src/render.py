"""Render the digest to a mobile-first static page."""
import datetime as dt, html, json

CSS = """
:root{
  --bg:#0f1216; --card:#171b21; --card2:#1d222a; --line:#262c36;
  --ink:#e8ecf1; --dim:#98a2b3; --accent:#5b8def; --hot:#e8894a;
  --dib:#7bc48a; --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
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
.wrap{max-width:820px;margin:0 auto;padding:0 16px}
.changed{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:14px 16px;margin:16px 0}
.changed h2{margin:0 0 8px;font-size:12px;text-transform:uppercase;
  letter-spacing:.08em;color:var(--dim)}
.changed li{margin:5px 0;font-size:14.5px}
.changed ul{margin:0;padding-left:18px}
section{margin:22px 0}
.sh{display:flex;align-items:baseline;gap:8px;margin:0 0 10px}
.sh h2{margin:0;font-size:15px;letter-spacing:-.01em}
.sh .n{color:var(--dim);font-size:12px;font-family:var(--mono)}
.item{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:12px 14px;margin-bottom:9px}
.item a{color:var(--ink);text-decoration:none;font-weight:600;font-size:15px;
  display:block;margin-bottom:5px}
.item a:active{opacity:.6}
.gloss{color:var(--dim);font-size:13.5px;margin:0 0 7px}
.tags{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.tag{font-family:var(--mono);font-size:10.5px;padding:2px 7px;border-radius:99px;
  background:var(--card2);color:var(--dim);border:1px solid var(--line)}
.tag.hot{color:var(--hot);border-color:var(--hot)}
.tag.dib{color:var(--dib);border-color:var(--dib)}
.tag.an{color:var(--accent);border-color:var(--accent)}
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

SECTION_ORDER = ["triad", "dib", "budget", "primes", "tech", "nuclear_energy", "deals", "thinktank"]


def esc(s):
    return html.escape(s or "", quote=True)


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


def render(digest, cfg, errors=None, generated=None, ai_summary=None):
    generated = generated or dt.datetime.now(dt.timezone.utc)
    disp = cfg["display"]
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

    lines = ai_summary or summarize(digest, cfg)
    if lines:
        parts.append('<div class="changed"><h2>What changed</h2><ul>')
        parts += [f"<li>{l}</li>" for l in lines]
        parts.append("</ul></div>")

    for key in SECTION_ORDER:
        rows = by_sec.get(key) or []
        if not rows:
            continue
        label = cfg["sections"][key]["label"]
        heads = [r for r in rows if r["score"] >= disp["headline_threshold"]][:disp["max_headlines_per_section"]]
        if not heads:
            heads = rows[:2]
        tail = [r for r in rows if r not in heads][:disp["max_tail_per_section"]]

        parts.append(f'<section><div class="sh"><h2>{esc(label)}</h2><span class="n">{len(rows)}</span></div>')
        for r in heads:
            tags = [f'<span class="tag">{esc(r["lead_source"])}</span>']
            if r["n_sources"] > 1:
                tags.append(f'<span class="tag hot">{r["n_sources"]} sources</span>')
            if r["dib"]:
                tags.append('<span class="tag dib">industrial base</span>')
            if r["is_analysis"]:
                tags.append('<span class="tag an">analysis</span>')
            if r["usd"] >= 1e8:
                tags.append(f'<span class="tag hot">${r["usd"]/1e9:.1f}B</span>' if r["usd"] >= 1e9
                            else f'<span class="tag hot">${r["usd"]/1e6:.0f}M</span>')
            gloss = f'<p class="gloss">{esc(r["summary"][:190])}</p>' if r["summary"] else ""
            parts.append(
                f'<div class="item"><a href="{esc(r["link"])}" target="_blank" rel="noopener">{esc(r["title"])}</a>'
                f'{gloss}<div class="tags">{"".join(tags)}</div></div>')
        if tail:
            parts.append(f'<details class="tail"><summary>{len(tail)} more</summary>')
            for r in tail:
                parts.append(
                    f'<div class="tailitem"><a href="{esc(r["link"])}" target="_blank" rel="noopener">{esc(r["title"])}</a> '
                    f'<span class="src">{esc(r["lead_source"])}</span></div>')
            parts.append("</details>")
        parts.append("</section>")

    err = f" &middot; {len(errors)} feed errors" if errors else ""
    parts.append(f'<footer>Generated {generated.strftime("%Y-%m-%d %H:%M UTC")}{err}<br>'
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
