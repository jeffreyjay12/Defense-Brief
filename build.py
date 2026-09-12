#!/usr/bin/env python3
"""
Build the defense news brief.

  python build.py                 # fetch, score, write public/index.html
  python build.py --explain 20    # print why the top 20 scored what they did
  python build.py --offline       # use data/raw.json instead of fetching
  python build.py --section dib   # explain one section only

Optional: set ANTHROPIC_API_KEY for a written 'what changed' summary.
Everything works without it.
"""
import argparse, json, os, sys, datetime as dt, pathlib

ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

from pipeline import fetch, build_digest          # noqa: E402
from render import render, MANIFEST, ICON         # noqa: E402

DATA = ROOT / "data"
PUB = ROOT / "public"


def load(p):
    with open(p) as f:
        return json.load(f)


def ai_summary(digest, cfg):
    """Optional: 3-4 line written summary via Claude. Silent no-op without a key."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not digest:
        return None
    try:
        import urllib.request
        top = digest[:14]
        listing = "\n".join(
            f"- [{cfg['sections'][d['section']]['label']}] {d['title']} ({', '.join(d['sources'][:3])})"
            for d in top)
        prompt = (
            "You are briefing a defense-industrial-base investor. Below are today's top stories.\n"
            "Write 3-4 single-sentence bullets covering only what genuinely changed - program "
            "decisions, awards, budget moves, supply-chain or capacity news. Skip routine coverage. "
            "No preamble, no bullet characters, one sentence per line.\n\n" + listing)
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps({
                "model": "claude-sonnet-4-6", "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}],
            }).encode(),
            headers={"content-type": "application/json", "x-api-key": key,
                     "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=45) as r:
            body = json.loads(r.read())
        text = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
        lines = [l.strip(" -•\t") for l in text.splitlines() if l.strip()]
        return lines[:4] or None
    except Exception as ex:  # noqa: BLE001
        print(f"  (ai summary skipped: {ex})")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--explain", type=int, nargs="?", const=20, default=0)
    ap.add_argument("--section", default=None)
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    DATA.mkdir(exist_ok=True)
    PUB.mkdir(exist_ok=True)
    sources = load(ROOT / "config" / "sources.json")["sources"]
    cfg = load(ROOT / "config" / "scoring.json")

    if args.offline and (DATA / "raw.json").exists():
        raw = load(DATA / "raw.json")
        errors = []
        print(f"offline: {len(raw)} cached items")
    else:
        print(f"fetching {len(sources)} feeds...")
        raw, errors = fetch(sources)
        (DATA / "raw.json").write_text(json.dumps(raw, indent=1))

    digest, filtered = build_digest(raw, cfg)
    (DATA / "digest.json").write_text(json.dumps(digest, indent=1))
    (DATA / "filtered.json").write_text(json.dumps(filtered, indent=1))

    print(f"\n{len(raw)} fetched -> {len(digest)} scored, {len(filtered)} gated out")
    by = {}
    for d in digest:
        by[d["section"]] = by.get(d["section"], 0) + 1
    for k, v in sorted(by.items(), key=lambda x: -x[1]):
        print(f"  {cfg['sections'][k]['label']:<32} {v}")

    if args.explain:
        rows = [d for d in digest if not args.section or d["section"] == args.section]
        print(f"\n--- top {args.explain} scoring ---")
        for d in rows[:args.explain]:
            print(f"\n[{d['score']:>6.1f}] {d['title'][:88]}")
            print(f"         {cfg['sections'][d['section']]['label']} | {', '.join(d['sources'][:4])}")
            print(f"         {' | '.join(d['why'])}")
        return

    summary = ai_summary(digest, cfg)
    html = render(digest, cfg, errors=errors, ai_summary=summary)
    (PUB / "index.html").write_text(html)
    (PUB / "manifest.json").write_text(MANIFEST)
    (PUB / "icon.svg").write_text(ICON)
    (PUB / "digest.json").write_text(json.dumps(digest, indent=1))
    print(f"\nwrote {PUB/'index.html'} ({len(html)//1024} KB)")
    if errors:
        print(f"feed errors: {len(errors)}")
        for e in errors[:8]:
            print("   ", e["source"], "-", e["error"][:80])


if __name__ == "__main__":
    main()
