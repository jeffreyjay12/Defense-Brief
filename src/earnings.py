"""
Quarterly earnings releases from the primes and public sub-tier suppliers -
the customer-demand-to-supplier-P&L read-through.

Source: the earnings release itself, as filed with the SEC the same day (8-K
Item 2.02, Exhibit 99.1). That is the company's own record, so its figures are
PRIMARY. Trade coverage of the earnings calls is left to the weekly synthesis,
which labels it as trade reporting.

Flow, per build:
  1. For each company, read its filing list from data.sec.gov and find 8-Ks
     carrying Item 2.02 inside the lookback window.
  2. New filings only (the state file remembers accession numbers): fetch the
     filing index, pick Exhibit 99.1, convert it to text, and have the model
     pull the supplier-relevant figures exactly as stated - sales, backlog by
     segment, book-to-bill if stated, guidance change, supply-chain and
     capacity commentary. No computed figures; anything not stated is null.
  3. Each release becomes one page item (Defense Industrial Base) and one row
     in earnings.json, the quarter's scorecard, which the weekly synthesis
     reads.

CIKs are configured, not guessed, and verified at runtime against the
`tickers` field of the SEC's own filing list: a wrong CIK is skipped with a
log line rather than silently returning another company's filings.

SEC asks for a descriptive User-Agent with contact details (SEC_USER_AGENT)
and fewer than 10 requests a second.
"""
import datetime as dt
import html
import json
import os
import re
import time
import urllib.request

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/"
API = "https://api.anthropic.com/v1/messages"


def _get(url, ua, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _to_text(raw, max_chars):
    """Release HTML to text, keeping table cells apart so figures stay readable."""
    x = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    x = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", x)
    x = re.sub(r"(?i)</t[dh]>", " | ", x)
    x = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h[1-6]|table)>", "\n", x)
    x = html.unescape(re.sub(r"<[^>]+>", " ", x))
    x = re.sub(r"[ \t ]+", " ", x)
    x = re.sub(r"( \| ){2,}", " | ", x)
    lines = [ln.strip(" |") for ln in x.split("\n")]
    text = "\n".join(ln for ln in lines if ln and ln not in ("$", "%"))
    return text[:max_chars]


def _exhibit_from_index(page):
    """The filing's index page lists each document with its exhibit type, which
    is reliable where file names are not ("ex991q32026.htm",
    "noc-09302026xearningsrelea.htm", "a2026-10x208xkerexhibit99.htm")."""
    found = []
    for row in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", page):
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"(?is)<td[^>]*>(.*?)</td>", row)]
        href = re.search(r'href="([^"]+)"', row)
        if len(cells) >= 4 and href and cells[3].upper().startswith("EX-99"):
            name = href.group(1).rsplit("/", 1)[-1]
            if name.lower().endswith((".htm", ".html")):
                found.append((cells[3].upper() != "EX-99.1", name))
    return sorted(found)[0][1] if found else None


def _pick_exhibit(names):
    """Exhibit 99.1 is the press release. Naming varies by filer agent."""
    htm = [n for n in names if n.lower().endswith((".htm", ".html")) and "index" not in n.lower()
           and not re.match(r"(?i)^r\d+\.htm", n)]
    for pat in (r"ex-?99[._-]?0?1(?!\d)", r"ex991", r"ex-?99", r"99[._-]?1"):
        hit = [n for n in htm if re.search(pat, n, re.I)]
        if hit:
            return hit[0]
    return None


def _call(prompt, model, max_tokens=1800, timeout=120):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    req = urllib.request.Request(
        API,
        data=json.dumps({"model": model, "max_tokens": max_tokens,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read())
    return "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")


def _parse_obj(text):
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    for cand in (t, (re.search(r"\{.*\}", t, re.S) or [None])[0]):
        if not cand:
            continue
        try:
            obj = json.loads(cand)
            return obj if isinstance(obj, dict) else None
        except Exception:
            continue
    return None


PROMPT = """Below is a company's quarterly earnings press release (SEC Form 8-K,
Exhibit 99.1). The reader invests in small private suppliers deep in the US
defense and nuclear industrial base, and reads prime and supplier earnings for
what they say about demand flowing down to the sub-tier and pressure on
supplier economics.

Extract the following. Rules:
- Figures EXACTLY as stated in the release, with their units and periods
  ("$20.7 billion", "up 11%"). Never compute, convert or estimate. If a figure
  is not stated, use null. Book-to-bill only if the release states it.
- Use only this release. No outside knowledge.
- supply_chain: short factual points about suppliers, production rates,
  capacity, facilities, inventories, delays, cost growth or charges tied to
  supply problems, as stated. Empty list if none.
- headline: under 120 characters, factual, the most important takeaway for a
  supplier-base reader (often backlog, demand or guidance, not EPS).

Return ONLY a JSON object:
{{"company":"...","period":"Q3 2026","headline":"...",
 "sales":"...","sales_change":"...","backlog":"...",
 "backlog_segments":[{{"segment":"...","backlog":"..."}}],
 "book_to_bill":null,"orders":null,
 "guidance":{{"change":"raised|lowered|reaffirmed|initiated|mixed|none stated","detail":"..."}},
 "free_cash_flow":"...","capex":null,
 "supply_chain":["..."],"programs":["..."],
 "summary":"two sentences, facts from the release only"}}

Company: {name} ({ticker})
Filed: {filed}

Release:
{text}"""


def _headline_row(ticker, name, meta, link):
    """Fallback when the exhibit cannot be read or the model call fails: the
    filing itself is still news, and the weekly synthesis can read it."""
    return {"company": name, "ticker": ticker, "period": None, "filed": meta["filed"],
            "accession": meta["acc"], "link": link, "extracted": False,
            "headline": "earnings release filed (8-K Item 2.02)", "summary": ""}


def fetch(cfg, state=None, log=print):
    """Returns (items, state). `state` is the previous earnings.json; it is
    returned unchanged when the module is off, so the published scorecard
    survives a build that skipped it."""
    state = dict(state or {})
    rows = dict(state.get("rows") or {})
    ec = cfg.get("earnings", {})
    if not ec.get("enabled", False):
        return [], state
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        log("  earnings: SEC_USER_AGENT not set, skipping (SEC requires a contact UA)")
        return [], state

    now = dt.datetime.now(dt.timezone.utc)
    cutoff = (now - dt.timedelta(days=int(ec.get("lookback_days", 10)))).date().isoformat()
    model = ec.get("model", "claude-sonnet-4-6")
    max_chars = int(ec.get("max_chars", 45000))
    max_new = int(ec.get("max_new_per_build", 12))
    delay = float(ec.get("delay_seconds", 0.25))
    weight = float(ec.get("weight", 1.3))
    new_calls, found, failed = 0, 0, 0
    items = []

    for ticker, conf in ec.get("companies", {}).items():
        cik = str(conf["cik"]).zfill(10)
        name = conf.get("name", ticker)
        try:
            sub = json.loads(_get(SUBMISSIONS.format(cik=cik), ua))
        except Exception as ex:  # noqa: BLE001
            log(f"  earnings: {ticker} filing list unavailable ({type(ex).__name__})")
            continue
        finally:
            time.sleep(delay)
        if ticker.upper() not in [t.upper() for t in sub.get("tickers", [])]:
            log(f"  earnings: CIK {cik} does not list {ticker} ({sub.get('tickers')}) - skipped")
            continue
        r = sub.get("filings", {}).get("recent", {})
        for i, form in enumerate(r.get("form", [])):
            if form != "8-K" or "2.02" not in str(r["items"][i]).split(","):
                continue
            filed = r["filingDate"][i]
            if filed < cutoff:
                break                      # newest first; nothing older is in the window
            found += 1
            acc = r["accessionNumber"][i]
            meta = {"acc": acc, "filed": filed,
                    "accepted": r.get("acceptanceDateTime", [None] * (i + 1))[i]}
            base = ARCHIVE.format(cik_int=int(cik), acc=acc.replace("-", ""))
            row = rows.get(acc)
            if row is None or not row.get("extracted"):
                if new_calls >= max_new:
                    continue
                link, row = base, None
                try:
                    page = _get(base + f"{acc}-index.html", ua).decode("utf-8", "ignore")
                    time.sleep(delay)
                    ex99 = _exhibit_from_index(page) or _pick_exhibit(
                        re.findall(r'href="[^"]*/([^/"]+\.html?)"', page))
                    if ex99:
                        link = base + ex99
                        text = _to_text(_get(link, ua), max_chars)
                        time.sleep(delay)
                        new_calls += 1
                        obj = _parse_obj(_call(PROMPT.format(name=name, ticker=ticker, filed=filed,
                                                             text=text), model))
                        if obj:
                            row = {**obj, "ticker": ticker, "company": name, "filed": filed,
                                   "accession": acc, "link": link, "extracted": True}
                except Exception as ex:  # noqa: BLE001
                    log(f"  earnings: {ticker} {acc} not read ({type(ex).__name__})")
                if row is None:
                    failed += 1
                    row = rows.get(acc) or _headline_row(ticker, name, meta, link)
                rows[acc] = row
            items.append(_item(row, meta, weight))

    keep = (now - dt.timedelta(days=int(ec.get("keep_days", 120)))).date().isoformat()
    rows = {k: v for k, v in rows.items() if (v.get("filed") or "") >= keep}
    state = {"_readme": ("Quarterly earnings releases (SEC 8-K Item 2.02, Exhibit 99.1) for the "
                         "tracked primes and public suppliers. Figures are as stated in each "
                         "release (PRIMARY); null means not stated. extracted=false means the "
                         "filing was seen but its release could not be read - follow the link."),
             "updated": now.isoformat(),
             "rows": dict(sorted(rows.items(), key=lambda kv: kv[1].get("filed", ""), reverse=True))}
    log(f"  earnings: {found} releases in window, {new_calls} read now, {failed} unread")
    return items, state


def _item(row, meta, weight):
    bits = [row.get("sales") and f"sales {row['sales']}" + (f" ({row['sales_change']})" if row.get("sales_change") else ""),
            row.get("backlog") and f"backlog {row['backlog']}",
            row.get("book_to_bill") and f"book-to-bill {row['book_to_bill']}"]
    g = row.get("guidance") or {}
    if g.get("change") and g["change"] != "none stated":
        bits.append(f"guidance {g['change']}")
    facts = "; ".join(b for b in bits if b)
    sc = row.get("supply_chain") or []
    summary = "Earnings release. " + (row.get("summary") or "")
    if sc:
        summary += " Supply chain: " + "; ".join(sc[:3])
    period = row.get("period") or ""
    title = f"{row['company']} {period} results: {row.get('headline') or facts}".replace("  ", " ")
    published = meta.get("accepted") or f"{meta['filed']}T21:00:00+00:00"
    return {
        "id": None,
        "title": title[:220],
        "link": row["link"],
        "summary": (summary + (f" [{facts}]" if facts else ""))[:600],
        "source": "SEC EDGAR",
        "kind": "news",
        "weight": weight,
        "hint": ["dib"],
        "force_section": "dib",
        "cluster_key": row.get("ticker"),
        "published": published.replace("Z", "+00:00"),
        "pre_judged": True,
        "relevance": 2,
    }
