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
import csv, io, json, os, time, urllib.request, urllib.parse, datetime as dt

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
TWELVE = ("https://api.twelvedata.com/time_series?symbol={syms}&interval=1day"
          "&outputsize=5&apikey={key}")

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
    if not rows:
        # surface the body so a block/limit notice is distinguishable from a
        # genuinely unknown ticker
        raise ValueError("no rows; body=" + " ".join(raw.split())[:110])
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


TWELVE_SYMBOL = {"MOG-A": "MOG.A", "BRK-B": "BRK.B"}


def _from_twelve(symbols, key, log=print):
    """Batch fetch. Returns {symbol: row}. Free keyless endpoints (Yahoo, Stooq)
    both refuse GitHub's datacenter IPs - 429 and a bot page respectively - so a
    keyed service is the only reliable path from CI."""
    out = {}
    batch = int(os.environ.get("TWELVEDATA_BATCH", "8"))
    pause = float(os.environ.get("TWELVEDATA_PAUSE", "62"))
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        wire = [TWELVE_SYMBOL.get(s, s) for s in chunk]
        back = {TWELVE_SYMBOL.get(s, s): s for s in chunk}
        url = TWELVE.format(syms=",".join(urllib.parse.quote(w) for w in wire), key=key)
        d = None
        for attempt in range(3):
            try:
                d = json.loads(_get(url, timeout=30))
                break
            except Exception as ex:
                code = getattr(ex, "code", None)
                if code == 429 and attempt < 2:
                    log(f"      rate limited, waiting {pause:.0f}s (attempt {attempt+1})")
                    time.sleep(pause)
                    continue
                log(f"      twelvedata batch failed: {type(ex).__name__} {str(ex)[:60]}")
                break
        if d is None:
            continue
        # single-symbol calls return the object directly; batches key by symbol
        payloads = {wire[0]: d} if "values" in d else d
        for sym, p in payloads.items():
            if not isinstance(p, dict):
                continue
            if p.get("status") == "error":
                log(f"      {sym}: {str(p.get('message'))[:70]}")
                continue
            vals = p.get("values") or []
            closes = []
            for v in reversed(vals):
                try:
                    closes.append((v.get("datetime"), float(v.get("close"))))
                except (TypeError, ValueError):
                    continue
            if not closes:
                continue
            sym = back.get(sym, sym)
            out[sym] = {"symbol": sym, "price": closes[-1][1],
                        "prev": closes[-2][1] if len(closes) > 1 else None,
                        "date": closes[-1][0], "src": "twelvedata",
                        "series": [c for _, c in closes[-5:]]}
        # The minute allowance is consumed per symbol, so a full batch needs a
        # full minute before the next one.
        if i + batch < len(symbols):
            time.sleep(pause)
    return out


FRED = ("https://api.stlouisfed.org/fred/series/observations?series_id={sid}"
        "&api_key={key}&file_type=json&sort_order=desc&limit=7")


def _from_fred(cfg, log=print):
    """Treasury yields. Returns {symbol: row}."""
    key = os.environ.get("FRED_API_KEY")
    series = (cfg.get("market", {}) or {}).get("fred_series", {})
    if not key or not series:
        return {}
    out = {}
    for sym, sid in series.items():
        try:
            d = json.loads(_get(FRED.format(sid=sid, key=key), timeout=20))
            obs = [o for o in d.get("observations", []) if o.get("value") not in (None, ".")]
            if not obs:
                continue
            vals = [(o["date"], float(o["value"])) for o in obs][:5][::-1]
            out[sym] = {"symbol": sym, "price": vals[-1][1],
                        "prev": vals[-2][1] if len(vals) > 1 else None,
                        "date": vals[-1][0], "src": "fred",
                        "series": [v for _, v in vals]}
        except Exception as ex:
            log(f"      fred {sym}: {type(ex).__name__} {str(ex)[:50]}")
    return out


def fetch(cfg, cache_path=None, log=print):
    mk = cfg.get("market", {})
    if not mk.get("enabled", True):
        return []
    symbols = mk.get("symbols", [])
    out, failed = [], []

    # Preferred path: keyed service. Falls through to the free endpoints if no
    # key is configured, which works locally even though it fails in CI.
    fred_rows = _from_fred(cfg, log=log)
    equities = [s for s in symbols if s not in fred_rows]

    key = os.environ.get("TWELVEDATA_API_KEY")
    if key:
        got = _from_twelve(equities, key, log=log)
        got.update(fred_rows)
        if got:
            rows = [got[s] for s in symbols if s in got]
            missing = [s for s in symbols if s not in got]
            if missing:
                log(f"  market: {len(rows)}/{len(symbols)} fetched, missing: {', '.join(missing[:8])}")
            else:
                log(f"  market: {len(rows)}/{len(symbols)} fetched")
            if cache_path and rows:
                try:
                    cache_path.write_text(json.dumps(
                        {"fetched": dt.datetime.now(dt.timezone.utc).isoformat(),
                         "rows": rows}, indent=1))
                except Exception:
                    pass
            return rows
        log("  market: twelvedata returned nothing, trying free sources")
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
