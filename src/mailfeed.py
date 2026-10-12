"""
Email ingestion, with Claude reading each message.

Some of the best sources cannot be fetched from CI at all: Substack, CSBA and
AEI return 403 to datacenter IPs, publications like ExchangeMonitor have no
feed, and company news often appears only on the press wires (BWXT's $189M
naval fuel award, NANO Nuclear's Radnostix deal). A dedicated inbox
subscribed to newsletters, wire alerts and Google Alerts collects all of it.

Why a model reads the mail instead of a parser:
  * One email is often many stories - a Google Alert or a newsletter digest
    carries five to fifteen. The old shallow parser turned each email into one
    item titled with the subject line, so most of the content was lost.
  * Keyword gates fail on recall: the Pratt/Hermeus second-source story was
    dropped because "engine" was not in the vocabulary. The model judges each
    story against the same thesis the semantic pass uses.

Design:
  * One API call per NEW message. Results are cached by a hash of the
    Message-ID and published with the page (mail_cache.json), then read back
    on the next build, so the twice-daily runs do not pay twice for one email.
  * Every rejection is logged with its title, so the fortnightly audit can see
    what the model dropped and catch anything it should have kept.
  * Read-only IMAP. Messages are never deleted, moved or marked read.
  * Never raises. No credentials, unreachable server, bad JSON, API failure:
    each degrades to fewer items, never a failed build.
"""
import email, hashlib, imaplib, json, os, re, urllib.parse, urllib.request
import datetime as dt
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime, parseaddr

API = "https://api.anthropic.com/v1/messages"


# ---------------------------------------------------------------- message text
def _decode(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _body(msg):
    """(plain, html) bodies, attachments skipped."""
    html = plain = ""
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            continue
        if not payload:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", "ignore")
        ctype = part.get_content_type()
        if ctype == "text/plain" and not plain:
            plain = text
        elif ctype == "text/html" and not html:
            html = text
    return plain, html


JUNK_LINK = re.compile(
    r"(unsubscribe|/alerts/(remove|edit|feeds)|manage.?preferences|email.?preferences|/optout|opt-out|list-manage\.com/"
    r"(?!track)|privacy|terms-of|mailto:|/account|/login|/signin|view.?in.?browser|"
    r"facebook\.com|twitter\.com|x\.com/|linkedin\.com/(?!pulse)|instagram\.com|youtube\.com|"
    r"apps\.apple\.com|play\.google\.com|\.(png|jpg|gif)(\?|$))", re.I)


def _unwrap(url):
    """Strip redirect wrappers that carry the real URL as a parameter
    (Google Alerts, several newsletter platforms)."""
    try:
        p = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(p.query)
        for k in ("url", "q", "u", "redirect", "target"):
            if k in q and q[k][0].startswith("http"):
                return q[k][0]
    except Exception:
        pass
    return url


def _to_text(plain, html, max_chars, max_links):
    """Readable text with numbered links: 'Headline [L3]' plus a link table.
    The model picks link numbers, so it can only cite URLs that are really in
    the email."""
    links = []

    def add(url):
        url = _unwrap(url.strip().rstrip(").,"))
        if JUNK_LINK.search(url) or len(links) >= max_links:
            return None
        if url in links:
            return links.index(url) + 1
        links.append(url)
        return len(links)

    if html:
        t = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", html)

        def anchor(m):
            n = add(m.group(1))
            label = re.sub(r"<[^>]+>", " ", m.group(2))
            label = re.sub(r"\s+", " ", label).strip()
            return f" {label} [L{n}] " if n and label else f" {label} "
        t = re.sub(r'(?is)<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', anchor, t)
        t = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h[1-6]|table)>", "\n", t)
        t = re.sub(r"<[^>]+>", " ", t)
        import html as _h
        t = _h.unescape(t)
    else:
        def repl(m):
            n = add(m.group(0))
            return f"[L{n}]" if n else ""
        t = re.sub(r"https?://[^\s<>\"')\]]+", repl, plain or "")
    t = re.sub(r"[ \t ‌​]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t).strip()
    return t[:max_chars], links


# ---------------------------------------------------------------- model call
def _call(prompt, model, max_tokens=2500, timeout=90):
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


def _parse_array(text):
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"\[.*\]", t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def _extract(sender, subject, date, text, links, cfg):
    """Ask the model for the distinct stories in one email, each judged
    against the thesis. Returns a list of rows, or None on failure."""
    sem = cfg.get("semantic", {})
    sections = {k: v["label"] for k, v in cfg["sections"].items()
                if isinstance(v, dict) and not k.startswith("_")}
    guide = "\n".join(f"  {k} ({sections[k]}): {sem.get('section_guide', {}).get(k, '')}".rstrip(": ")
                      for k in sections)
    link_table = "\n".join(f"L{n+1}: {u[:200]}" for n, u in enumerate(links))
    prompt = f"""{sem.get('thesis', '')}

Below is one email from the brief's dedicated inbox: a newsletter, a press-wire
alert, a Google Alert, or an administrative message. List the distinct news or
analysis items it contains and judge each against the thesis above.

Rules:
- One entry per distinct story. A digest of ten stories is ten entries; a single
  essay is one entry. Skip ads, sponsor blocks, event promotions, job posts,
  "read more" boilerplate, and the newsletter's own housekeeping.
- Administrative mail (subscription confirmations, welcome notes, password or
  security notices, receipts) has no stories: return [].
- title: the story's own headline if it has one, otherwise a short factual
  headline in your words. Never invent facts not in the email.
- link: the L-number of the link that goes to that story, or null if none does.
  Use only L-numbers from the table.
- summary: one or two sentences in your own words, facts from the email only.
  Include dollar amounts exactly as stated.
- kind: "news" for events, awards, deals, filings and announcements; "analysis"
  for commentary, essays and research.
- relevance: 3 directly on-thesis, 2 useful context, 1 tangential, 0 irrelevant.
- section: the best section key from the list.

Sections:
{guide}

Return ONLY a JSON array:
[{{"title":"...","link":"L3","summary":"...","kind":"news","relevance":2,"section":"dib"}}]

From: {sender}
Subject: {subject}
Date: {date}

Links:
{link_table or '(none)'}

Body:
{text}"""
    out = _parse_array(_call(prompt, cfg.get("email", {}).get("model", "claude-sonnet-4-6")))
    return out if isinstance(out, list) else None


# ---------------------------------------------------------------- main entry
def fetch(cfg, cache=None, log=print):
    """Returns (items, cache). `cache` maps a message hash to its extracted
    rows; pass the previous build's cache to avoid paying twice."""
    mailcfg = cfg.get("email", {})
    cache = dict(cache or {})
    if not mailcfg.get("enabled", True):
        return [], cache
    user, password = os.environ.get("MAIL_USER"), os.environ.get("MAIL_PASSWORD")
    if not (user and password):
        log("  email: MAIL_USER/MAIL_PASSWORD not set, skipping")
        return [], cache
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("  email: no ANTHROPIC_API_KEY - cannot read mail without the model, skipping")
        return [], cache

    folder = mailcfg.get("folder", "Monitor")
    # A personal account must never have its whole inbox sent to the model.
    # Mail reaches the monitor through a Gmail label; reading INBOX is refused
    # unless the config says, explicitly, that the account is dedicated.
    if folder.upper() == "INBOX" and not mailcfg.get("dedicated_account", False):
        log("  email: folder is INBOX on a non-dedicated account - refusing; set email.folder to the label")
        return [], cache
    host = os.environ.get("MAIL_HOST") or mailcfg.get("host", "imap.gmail.com")
    days = int(mailcfg.get("lookback_days", 4))
    min_rel = int(mailcfg.get("min_relevance", 1))
    max_new = int(mailcfg.get("max_new_messages", 60))
    senders = mailcfg.get("senders", {})
    # Your own address is NOT ignored: everything in the label is there on
    # purpose, and forwarding a newsletter or release to the +monitor address
    # is the manual way to feed the brief.
    ignore = [s.lower() for s in mailcfg.get("ignore_senders", [])]
    sec_keys = {k for k, v in cfg["sections"].items() if isinstance(v, dict) and not k.startswith("_")}

    items, kept_cache = [], {}
    stats = {"messages": 0, "cached": 0, "extracted": 0, "failed": 0, "skipped": 0,
             "stories": 0, "kept": 0, "rejected": 0}
    try:
        M = imaplib.IMAP4_SSL(host)
        M.login(user, password)
        typ, _ = M.select(f'"{folder}"', readonly=True)          # never alters the mailbox
        if typ != "OK":
            log(f"  email: label '{folder}' not found - create it and the Gmail filter that fills it")
            M.logout()
            return [], cache
        since = (dt.date.today() - dt.timedelta(days=days)).strftime("%d-%b-%Y")
        typ, data = M.search(None, f"(SINCE {since})")
        ids = data[0].split() if data and data[0] else []
        new_calls = 0
        for num in reversed(ids[-int(mailcfg.get("max_messages", 200)):]):   # newest first
            try:
                typ, raw = M.fetch(num, "(BODY.PEEK[])")
                if not raw or not raw[0] or not isinstance(raw[0], tuple):
                    continue
                msg = email.message_from_bytes(raw[0][1])
            except Exception:
                continue
            stats["messages"] += 1
            frm = _decode(msg.get("From"))
            name, addr = parseaddr(frm)
            addr = (addr or "").lower()
            if any(s in addr or s in frm.lower() for s in ignore):
                stats["skipped"] += 1
                continue
            subject = _decode(msg.get("Subject")).strip()
            try:
                when = parsedate_to_datetime(msg.get("Date"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=dt.timezone.utc)
            except Exception:
                when = dt.datetime.now(dt.timezone.utc)
            key = hashlib.sha1((msg.get("Message-ID") or f"{frm}|{subject}|{when}")
                               .encode("utf-8", "ignore")).hexdigest()[:16]

            if key in cache:
                rows = cache[key].get("rows", [])
                stats["cached"] += 1
            else:
                if new_calls >= max_new:
                    continue
                new_calls += 1
                plain, html = _body(msg)
                text, links = _to_text(plain, html, int(mailcfg.get("max_chars", 14000)),
                                       int(mailcfg.get("max_links", 80)))
                try:
                    rows = _extract(frm, subject, when.isoformat(), text, links, cfg)
                except Exception as ex:  # noqa: BLE001
                    log(f"  email: extraction failed for '{subject[:60]}' ({type(ex).__name__})")
                    rows = None
                if rows is None:
                    stats["failed"] += 1
                    continue                     # not cached, so it is retried next build
                stats["extracted"] += 1
                for r in rows:                   # resolve L-numbers now, cache final URLs
                    ln = str(r.get("link") or "")
                    m = re.fullmatch(r"L?(\d+)", ln.strip())
                    r["link"] = links[int(m.group(1)) - 1] if m and 0 < int(m.group(1)) <= len(links) else ""
            kept_cache[key] = {"rows": rows, "date": when.isoformat()}

            match = next((v for k, v in senders.items() if k.lower() in addr or k.lower() in frm.lower()), {})
            src = match.get("name") or name or addr or "Email"
            for r in rows:
                # Aggregator mail (Google Alerts) names the publisher in the
                # link, not the sender: credit the outlet, not "Google Alert".
                story_src = src
                if (match.get("source_from_link") or addr == user.lower()) and r.get("link"):
                    host = urllib.parse.urlparse(r["link"]).netloc.lower()
                    story_src = re.sub(r"^www\.", "", host) or src
                if not isinstance(r, dict) or not str(r.get("title", "")).strip():
                    continue
                stats["stories"] += 1
                rel = r.get("relevance")
                rel = int(rel) if isinstance(rel, (int, float)) else 1
                if rel < min_rel:
                    stats["rejected"] += 1
                    log(f"  email: rejected [{rel}] {r['title'][:90]}  ({story_src})")
                    continue
                sec = r.get("section") if r.get("section") in sec_keys else None
                stats["kept"] += 1
                items.append({
                    "id": None,
                    "title": str(r["title"]).strip()[:220],
                    "link": r.get("link") or "",
                    "summary": str(r.get("summary") or "").strip()[:400],
                    "source": story_src,
                    "kind": r.get("kind") if r.get("kind") in ("news", "analysis") else match.get("kind", "news"),
                    "weight": float(match.get("weight", mailcfg.get("default_weight", 1.1))),
                    "hint": [sec] if sec else list(match.get("hint", [])),
                    "published": when.isoformat(),
                    "pre_judged": True,
                    "relevance": rel,
                })
        M.close()
        M.logout()
    except Exception as ex:  # noqa: BLE001
        log(f"  email: failed ({type(ex).__name__}: {str(ex)[:80]})")
        return items, {**cache, **kept_cache}

    log("  email: {messages} messages ({cached} cached, {extracted} read now, {failed} failed, "
        "{skipped} ignored) -> {stories} stories, {kept} kept, {rejected} rejected".format(**stats))
    return items, kept_cache
