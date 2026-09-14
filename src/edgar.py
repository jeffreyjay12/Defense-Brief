"""
SEC EDGAR filings for the tracked companies.

Company disclosure is different information from trade coverage: it catches
material events the press does not chase, and it is the primary source rather
than a report about one.

Filtering, because EDGAR is otherwise a firehose:
  * Only material form types. Form 4 (insider transactions) alone would bury
    everything else, and routine ownership churn is not signal here.
  * 8-K items are triaged by number where the feed exposes them - 1.01 material
    agreements and 2.01 completed acquisitions matter; 5.02 officer changes and
    7.01 Reg FD disclosures do not.
  * Everything then passes through the normal gate, scoring and semantic
    relevance pass, so routine filings sink even if they slip the form filter.

CIKs are resolved at runtime from the SEC's own ticker map rather than
hard-coded: a wrong CIK silently returns another company's filings, which is
worse than returning nothing.

SEC requires a descriptive User-Agent with contact information on API requests.
Set SEC_USER_AGENT; without it the module no-ops rather than risk a block.
"""
import json, os, re, time, urllib.parse, urllib.request, datetime as dt

TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"
FILINGS = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
           "&type={form}&dateb=&owner=exclude&count=20&output=atom")

# Form types worth surfacing. Everything else - Form 4, 144, SD, ARS - is noise
# for this purpose.
# Default is deliberately narrow - ownership and control events. 8-K and the
# periodic reports were dropped: mostly routine, and the IR feeds already carry
# whatever the company wants noticed.
MATERIAL_FORMS = ["SC 13D", "SC 14D1", "S-4", "25-NSE"]

# 8-K item numbers that indicate something happened, versus routine housekeeping.
MATERIAL_8K_ITEMS = {
    "1.01": "material definitive agreement",
    "1.02": "termination of material agreement",
    "2.01": "completion of acquisition or disposition",
    "2.03": "material direct financial obligation",
    "2.05": "costs associated with exit or disposal",
    "2.06": "material impairment",
    "3.01": "delisting notice",
    "4.01": "change in accountant",
    "4.02": "non-reliance on prior financials",
    "8.01": "other events",
}


def _get(url, ua, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": ua,
        "Accept": "application/atom+xml,application/json,*/*",
        "Accept-Encoding": "identity",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _resolve_ciks(tickers, ua, log=print):
    """Map tickers to CIKs using the SEC's published file - never guess."""
    try:
        data = json.loads(_get(TICKER_MAP, ua))
    except Exception as ex:
        log(f"  edgar: ticker map unavailable ({type(ex).__name__})")
        return {}
    want = {t.replace("-", ".").upper() for t in tickers}
    out = {}
    for row in data.values():
        tk = str(row.get("ticker", "")).upper()
        if tk in want:
            out[tk] = str(row.get("cik_str", "")).zfill(10)
    missing = want - set(out)
    if missing:
        log(f"  edgar: no CIK for {', '.join(sorted(missing))}")
    return out


def _parse_atom(xml, ticker, name):
    """Minimal atom parse - avoids a dependency for a simple, stable format."""
    items = []
    for entry in re.findall(r"(?is)<entry>(.*?)</entry>", xml):
        def tag(t):
            m = re.search(rf"(?is)<{t}[^>]*>(.*?)</{t}>", entry)
            return re.sub(r"<[^>]+>", " ", m.group(1)).strip() if m else ""
        title = tag("title")
        link = ""
        m = re.search(r'(?is)<link[^>]*href="([^"]+)"', entry)
        if m:
            link = m.group(1)
        updated = tag("updated") or tag("filing-date")
        summary = tag("summary")
        form = (re.match(r"^([A-Z0-9\-/ ]+?)\s*-", title) or [None, ""])[1].strip()
        items.append({"title": title, "link": link, "updated": updated,
                      "summary": summary, "form": form, "ticker": ticker, "name": name})
    return items


def fetch(cfg, log=print):
    ec = cfg.get("edgar", {})
    if not ec.get("enabled", False):
        return []
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        log("  edgar: SEC_USER_AGENT not set, skipping (SEC requires a contact UA)")
        return []

    tickers = ec.get("tickers", [])
    ciks = _resolve_ciks(tickers, ua, log=log)
    if not ciks:
        return []

    forms = ec.get("forms", MATERIAL_FORMS)
    lookback = int(ec.get("lookback_days", 10))
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=lookback)
    out = []
    for tk, cik in ciks.items():
        for form in forms:
            try:
                xml = _get(FILINGS.format(cik=cik, form=urllib.parse.quote(form)), ua).decode(
                    "utf-8", "ignore")
            except Exception:
                continue
            for it in _parse_atom(xml, tk, tk):
                try:
                    when = dt.datetime.fromisoformat(it["updated"].replace("Z", "+00:00"))
                except Exception:
                    continue
                if when.tzinfo is None:
                    when = when.replace(tzinfo=dt.timezone.utc)
                if when < cutoff:
                    continue
                # triage 8-K items where the summary exposes them
                blob = f"{it['title']} {it['summary']}"
                if it["form"].startswith("8-K"):
                    nums = set(re.findall(r"\b(\d\.\d\d)\b", blob))
                    if nums and not (nums & set(MATERIAL_8K_ITEMS)):
                        continue
                out.append({
                    "id": None,
                    "title": f"{tk}: {it['title'][:120]}",
                    "link": it["link"],
                    "summary": it["summary"][:300],
                    "source": "SEC EDGAR",
                    "kind": "news",
                    "weight": float(ec.get("weight", 1.0)),
                    "hint": ["primes", "deals"],
                    "published": when.isoformat(),
                })
            time.sleep(float(ec.get("delay_seconds", 0.3)))   # SEC asks for <10 req/s
    log(f"  edgar: {len(out)} material filings across {len(ciks)} companies")
    return out
