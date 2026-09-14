# Monitor backlog

Open items, carried from the 13 September build. Reviewed Friday.

Status at kickoff: 24 active feeds, 0 errors, 377 fetched → 155 scored, market
24/24, semantic pass adjudicating ~90 per run.

---

## 0. Purpose check — add to the Friday agenda

This brief exists to make its reader a subject-matter expert on the defence
industrial base and to surface acquisition targets — small private Tier 2–4
suppliers to be bought and consolidated into a holding company. Not to follow
defence news, and not to track equities.

Review each section against that test, not just whether items route correctly.
A section can be perfectly sorted and still not help find or evaluate a company.
Ask of each: does this tell me something about qualification barriers, capacity,
supplier health, demand durability, or who is buying what?

## 1. Silent empty feeds

STRATCOM, AFGSC and RAND all return **0 bytes** and report no error, so they
never appear in the failure count. They have been delivering nothing since the
first build.

This is the exact failure mode the evaluation doc flags as dangerous: invisible
by construction. STRATCOM and AFGSC are two of the best native triad sources.

- Diagnose with `view-source:` on the parent page and search `rss`, the method
  that found the real NNSA path (`/rss/nnsa/2892342` — energy.gov serves feeds
  from a top-level route, not under the listing path).
- Add a build-log warning when a feed returns 0 items, so this class of failure
  stops being silent.

## 2. Section order does not match priority

Current output: Nuclear Energy 35, Allied & Adversary 31, Budget 28, Tech 26,
Analysis 16, DIB 10, **Triad 6**, Primes 2, Deals 1.

The three most important sections are the three smallest, and the page leads
with the lowest-priority content.

- Pin display order: Triad, DIB, Deals, Contracts-equivalent, Budget, Primes,
  Nuclear Energy, Tech, Allied, Analysis — independent of item count.
- Consider tighter caps on Nuclear Energy and Allied (currently 14 each but
  holding 35 and 31 pre-cap).

## 3. Gate rejection rate

157 of 377 items (42%) are gated out before scoring. Some is correct — World
Nuclear News international coverage, Stimson's broad output — but the rate is
high enough to check.

- Review `data/filtered.json`, specifically the `reason` field.
- Confirm nothing thesis-relevant is being dropped at the gate rather than
  ranked low.

## 3b. Target-identification infrastructure — the real gap

The sections are tuned, but the sources that would most directly serve
target-finding are the ones still missing:

- **KippsDeSanto** (404) — weekly sub-tier M&A with sponsors and targets named.
  Nothing else in the source list covers this.
- **OUSW A&S** (404) — industrial base policy, DPA Title III awards, capacity
  investments. SSL now fixed; only the URL is wrong.
- **USAspending sub-awards, SAM.gov, FPDS** — databases, no feeds. Would need
  the page-watcher module (see watch list).

Both 404s should be settled with `view-source:` rather than more guessing.

## 4. Email ingestion — built, not wired

`src/mailfeed.py` exists and is tested but has no credentials. Blocked sources
waiting on it: Industrial Base Alpha, Defense Tech & Acquisition, CSBA, AEI,
CSIS. Also the route for ExchangeMonitor's free weekly and Inside Defense
Notification Center alerts.

Setup: dedicated Gmail, 2FA on, app password, subscribe from that address, then
`MAIL_USER` / `MAIL_PASSWORD` / `MAIL_HOST` as repo secrets plus the workflow
env block.

## 5. Parked sources

Eight disabled with documented reasons in `config/sources.json`:

- **403 from CI** (datacenter IP block, needs email or a VPS): Industrial Base
  Alpha, Defense Tech & Acquisition, CSBA, AEI, CSIS
- **404, URL unknown**: National Defense (NDIA), NNSA (alt)
- **Malformed XML, unrepairable**: Brookings — scrub, namespace strip and
  salvage all failed. Low-weight site-wide feed; not worth more effort.

## 6. VPS decision — deferred by design

A $5/month VPS with a residential-looking IP would fix every 403 at once and
allow dropping the Twelve Data key (free quote endpoints would work again).
Deliberately deferred until email ingestion shows how much gap remains.

---

## Watch list, not yet actionable

- **Clustering** is lexical and biased toward splitting. Duplicate headlines
  from one story cost a corroboration bonus. Acceptable; revisit only if
  duplicates become visibly annoying.
- **Deals** — the `"to acquire"` term was routing procurement intent into the
  section and has been removed. It will stay thin until KippsDeSanto lands;
  that source is what the section was designed around.
- **Prime Demand Signal** (renamed from Primes) — reframed around what primes
  do that implies demand for the tier beneath: capacity, production rates,
  facilities, supplier investment, throughput constraints. Share-price and
  analyst-rating items now reject. Ten company IR feeds added as the input.
- **EDGAR built then disabled** — it served ownership and control events, which
  is trading intelligence rather than demand research. Module retained, off.
- **Semantic cost** — confirm actual Anthropic spend after a week matches the
  "pennies a day" estimate. Lever is `semantic.floor` / `ceiling`.

---

## Method note

Of the nine original feed failures, **none** was a genuinely broken feed: seven
were wrong URLs returning HTML error pages, two were IP blocks. The repair
machinery built along the way — entity scrubbing, tag closing, salvage — never
fixed anything.

When a feed fails, check the URL in `view-source:` before writing code.
