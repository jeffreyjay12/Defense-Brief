"""
Closing prices for the ticker strip.

Closes rather than live quotes: the page is static and rebuilt twice a day, so a
"live" quote would be frozen and quietly wrong for hours. A close is a fact about
a completed session, and it is what you actually want from a morning brief.

Sources, in order:
  1. Yahoo /v8/finance/chart - keyless and still working. (The older
     /v7/finance/quote endpoint began returning 401 in January 2026: it now
     requires a session cookie and crumb. The chart endpoint is what Yahoo's own
     site calls, so it is the more durable of the two.)
  2. Stooq CSV - keyless fallback, different infrastructure, so a Yahoo IP
     throttle (429) does not take the strip down with it.
  3. Last good values cached on disk, marked stale.

Never raises. A missing strip is a cosmetic loss; a failed build is not.
"""
import csv, io, json, time, urllib.request, urllib.parse, datetime as dt

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
YAHOO_HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]
YAHOO = "https://{host}/v8/finance/chart/{sym}?interval=1d&range=5d"
STOOQ = "https://stooq.com/q/d/l/?s={sym}&d1={d1}&d2={d2}&i=d"


HEADERS = {
    "User-Agent": UA,
    "Accept": "text/csv,application/json,text/plain,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Connection": "close",
}


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _from_yahoo(sym):
    last = None
    for host in YAHOO_HOSTS:
        try:
            raw = _get(YAHOO.format(host=host, sym=urllib.parse.quote(sym)))
            break
        except Exception as ex:
            last = ex
            raw = None
    if raw is None:
        raise last or RuntimeError("yahoo unreachable")
    d = json.loads(raw)
    res = (d.get("chart") or {}).get("result") or []
    if not res:
        return None
    meta = res[0].get("meta") or {}
    quotes = ((res[0].get("indicators") or {}).get("quote") or [{}])[0]
    closes = [c for c in (quotes.get("close") or []) if c is not None]
    if not closes:
        price = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    else:
        price = closes[-1]
        prev = closes[-2] if len(closes) > 1 else meta.get("chartPreviousClose")
    if price is None:
        return None
    ts = meta.get("regularMarketTime")
    when = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat() if ts else None
    return {"symbol": sym, "price": float(price),
            "prev": float(prev) if prev else None, "date": when, "src": "yahoo",
            # 5-day close series for the sparkline - already in the payload we
            # fetched, so this costs no extra request.
            "series": [float(c) for c in closes[-5:]] if closes else []}


# Stooq uses its own tickers: US equities take a .us suffix, indices and rates
# have bespoke codes.
STOOQ_MAP = {"^GSPC": "^spx", "^IXIC": "^ndq", "^DJI": "^dji",
             "^TNX": "10usy.b", "^TYX": "30usy.b", "^FVX": "5usy.b"}


def _stooq_symbol(sym):
    if sym in STOOQ_MAP:
        return STOOQ_MAP[sym]
    if sym.startswith("^"):
        return sym.lower()
    return sym.lower().replace("-", "-") + ".us"


def _from_stooq(sym):
    today = dt.date.today()
    start = today - dt.timedelta(days=20)
    url = STOOQ.format(sym=_stooq_symbol(sym),
                       d1=start.strftime("%Y%m%d"), d2=today.strftime("%Y%m%d"))
    raw = _get(url).decode("utf-8", "ignore")
    rows = [r for r in csv.DictReader(io.StringIO(raw)) if r.get("Close")]
    closes = []
    for r in rows:
        try:
            closes.append((r.get("Date"), float(r["Close"])))
        except (TypeError, ValueError):
            continue
    if not closes:
        return None
    closes = closes[-5:]
    return {"symbol": sym, "price": closes[-1][1],
            "prev": closes[-2][1] if len(closes) > 1 else None,
            "date": closes[-1][0], "src": "stooq",
            "series": [c for _, c in closes]}


def fetch(cfg, cache_path=None, log=print):
    mk = cfg.get("market", {})
    if not mk.get("enabled", True):
        return []
    symbols = mk.get("symbols", [])
    out, failed = [], []
    diag = []
    for sym in symbols:
        row = None
        for name, fn in (("stooq", _from_stooq), ("stooq2", _from_stooq),
                         ("yahoo", _from_yahoo)):
            if name == "stooq2":
                time.sleep(1.0)   # one retry for a transient blip
            try:
                row = fn(sym)
                if row:
                    break
                diag.append(f"{sym}/{name}: empty")
            except Exception as ex:
                diag.append(f"{sym}/{name}: {type(ex).__name__} {str(ex)[:60]}")
                row = None
        if row:
            out.append(row)
        else:
            failed.append(sym)
        time.sleep(mk.get("delay_seconds", 0.25))

    if failed:
        log(f"  market: {len(out)}/{len(symbols)} fetched, failed: {', '.join(failed[:8])}")
        # Print the first few reasons - "0/23 failed" alone is not diagnosable.
        for line in diag[:6]:
            log(f"      {line}")
    else:
        log(f"  market: {len(out)}/{len(symbols)} fetched")

    # Fall back to the last good snapshot rather than showing gaps.
    if cache_path:
        try:
            if out:
                cache_path.write_text(json.dumps(
                    {"fetched": dt.datetime.now(dt.timezone.utc).isoformat(), "rows": out}, indent=1))
            elif cache_path.exists():
                prev = json.loads(cache_path.read_text())
                log("  market: all fetches failed, using cached snapshot")
                for r in prev.get("rows", []):
                    r["stale"] = True
                return prev.get("rows", [])
        except Exception as ex:  # noqa: BLE001
            log(f"  market: cache error ({type(ex).__name__})")
    return out
