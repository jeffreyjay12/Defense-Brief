# Defense Brief — private news monitor

A static, phone-friendly news brief covering DoD/DoW, the strategic commands, procurement and the
defense industrial base, NNSA/DOE, defense tech, nuclear energy, deals, and think-tank output.
Builds twice daily via GitHub Actions and publishes to GitHub Pages. No server, no cost.

## How the ranking works

Two dimensions, in order:

1. **Gate** — is this in-domain at all? Permissive by design. Everything rejected is written to
   `data/filtered.json` so you can review what was dropped and loosen the gate if needed.
2. **Magnitude** — is this *big*? Topical relevance alone never promotes an item. Magnitude comes from:
   - **Cross-source corroboration** — five outlets carrying one story beats one outlet carrying it.
     This is the main "is this a headline" proxy.
   - **Dollar figures**, parsed and log-scaled, so a $50B award automatically outranks a $12M SBIR.
   - **Milestone language** — "Milestone B", "Nunn-McCurdy", "sole source", "contract award".
   - **Entity tiers** — programs and commands (Sentinel, Columbia, NNSA) score highest; primes next;
     sub-tier suppliers and qualification terms lowest but non-zero.

**The DIB override.** The most valuable industrial-base items are structurally single-source — a
Title III award or a sole-source J&A never gets five outlets — so corroboration scoring would bury
exactly what matters most. Items hitting industrial-base terms get a floor score regardless of how
many outlets carried them. Tune in `config/scoring.json → dib_override`.

**Analysis vs news.** Substacks, think tanks and GAO are marked `"kind": "analysis"`: exempt from
corroboration, given a base score, and decayed slowly (3 weeks vs 1 week). A weekly deep-dive is
never "confirmed by five sources" but may be the most valuable thing you read.

## Setup (about 15 minutes)

1. **Create a repo** (public is fine — the code holds no secrets) and push this folder.

2. **Enable Pages**: repo → Settings → Pages → Source: **GitHub Actions**.

3. **Run it**: Actions tab → "Build brief" → *Run workflow*. First run takes ~2 minutes.
   Your brief is at `https://<user>.github.io/<repo>/`.

4. **Add to your phone**: open that URL in Safari/Chrome → Share → *Add to Home Screen*.
   It installs as a PWA and opens full-screen like an app.

5. **Optional — written summary**: add repo secret `ANTHROPIC_API_KEY`
   (Settings → Secrets and variables → Actions). This produces a 3–4 line "what changed" at the top.
   Everything works without it; you just get a deterministic summary instead.

Schedule is 10:00 and 20:00 UTC (06:00 / 16:00 ET). Change the crons in
`.github/workflows/build.yml`.

## Tuning

Everything lives in `config/` — no code changes needed.

- `sources.json` — add/remove feeds. `kind` is `news` or `analysis`; `weight` is source credibility
  (0.5–1.5).
- `scoring.json` — gate terms, entity tiers, milestone language, section routing, decay rates,
  clustering thresholds, display limits.

Diagnose any ranking with:

```bash
pip install -r requirements.txt
python build.py --explain 20            # why did the top 20 score what they did
python build.py --explain --section dib # one section only
python build.py --offline               # re-score cached data without refetching
```

Each item prints its score breakdown (`source +2.4 | 3 sources +6 | $50,000,000,000 +10 | ...`),
so when something ranks wrong you can see which weight caused it and fix that line in the config.

**Expect two or three tuning passes.** The first week will feel too inclusive — that's deliberate.
It is far easier to see noise and dial it down than to discover stories that were silently dropped.
Check `data/filtered.json` after early runs.

## Sources and paywalls

Feeds carry headlines, links and summaries — that's what the brief indexes. Paywalled publishers
(WSJ, Inside Defense) publish headline-level RSS, so their stories appear in the brief and you tap
through to read them on the publisher's site with your subscription. The brief is a complete index;
it is not a reader for paywalled full text.

**Inside Defense** (`insidedefense.com/rss.xml`) — verified September 2026 as a **full-content
feed**. It carries complete article bodies, `daily-news`, INSIDER digests, the weekly newsletters,
and document postings with direct PDF links (modernized SARs, DOD manuals, reprogramming requests).
No email ingestion is required. Two consequences the pipeline handles:

- **Display**: only the lede paragraph is extracted and shown (~190 characters), with a link out.
  The brief is an index, not a reader — do not widen this to reproduce full articles.
- **Scoring**: scores are computed from title + lede only, never the full body. Full-text feeds
  carry years of background: the IFPC laser story's largest figure is a $4.8B cut from *2024*
  while the actual news is a $2.9M reprogramming. Scoring the whole body would rank routine stories
  as multi-billion-dollar events and would inflate keyword hits for long articles over short ones.
  *Residual limitation*: when a historical figure sits in the lede itself, it still counts. The lede
  is the best available proxy, not a perfect one.

**ExchangeMonitor** is not included: no usable free feed. Their free Wednesday roundup and LinkedIn
posts cover the headline layer until/unless you subscribe.

## Layout

```
build.py                     orchestrator + optional AI summary
config/sources.json          feed list
config/scoring.json          all tuning knobs
src/pipeline.py              fetch, gate, cluster, score, route
src/render.py                HTML/PWA renderer
public/                      generated output (published to Pages)
data/                        raw.json, digest.json, filtered.json (gitignored)
.github/workflows/build.yml  twice-daily build + deploy
```

## Known limitations

- **"Acquisition" is ambiguous** and caused real misrouting: in DoD it means procurement, in
  finance it means M&A. The deals section now matches only corporate senses ("acquires", "merger",
  "acquisition of"), and DoD senses ("acquisition baseline", "acquisition profile") route to budget.
  Watch for similar collisions as you add terms.
- **Feed URLs drift.** Publishers move or retire feeds. The build logs per-source errors and the
  page footer shows a count — check it occasionally. Verify each URL on first run.
- **Clustering is lexical**, not semantic. Very differently-worded headlines about the same event
  may not merge (costing the corroboration bonus). Thresholds are in `config/scoring.json → clustering`.
- **LinkedIn has no feed**, so the KCNSC supplier-award harvesting stays a manual route.
- **No read/unread state** — it's a static page. That needs a backend (Vercel + a small database),
  which is the natural upgrade if you miss it.
