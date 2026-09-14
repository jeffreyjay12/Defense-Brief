"""
Email newsletter ingestion.

Some of the best sources cannot be fetched from CI at all: Substack, CSBA and
AEI return 403 to GitHub's datacenter IPs, and publications like ExchangeMonitor
have no feed in the first place. Email sidesteps both problems - you are a
subscriber receiving content the ordinary way, and the mailbox does not care
what IP collects it.

Design notes:
  * Extraction is deliberately shallow: sender, subject, date, first substantial
    paragraph, and the outbound links. Newsletter HTML is a mess of nested
    tables and tracking pixels, and any parser that tries to reconstruct article
    structure will break the first time a publisher redesigns. Shallow
    extraction degrades to "slightly worse summary" instead of "nothing".
  * Each message becomes ONE item, titled with the subject line. Newsletters
    that bundle several stories are not split apart - the digest links to the
    issue, which is what a reader wants anyway.
  * Read-only. Messages are never deleted or marked; the mailbox stays a
    reusable archive and a bad run cannot destroy anything.
  * Never raises. No credentials, unreachable server, malformed message - all
    return an empty list and let the brief build without these sources.
"""
import email, imaplib, os, re, datetime as dt
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime


def _decode(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _body(msg):
    """Prefer text/plain; fall back to stripped HTML."""
    html = plain = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            ctype = part.get_content_type()
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                continue
            if not payload:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", "ignore")
            if ctype == "text/plain" and not plain:
                plain = text
            elif ctype == "text/html" and not html:
                html = text
    else:
        try:
            payload = msg.get_payload(decode=True) or b""
            text = payload.decode(msg.get_content_charset() or "utf-8", "ignore")
        except Exception:
            text = ""
        if msg.get_content_type() == "text/html":
            html = text
        else:
            plain = text
    return plain, html


def _first_para(plain, html, maxlen=400):
    for chunk in (plain or "").split("\n\n"):
        t = re.sub(r"\s+", " ", chunk).strip()
        # skip the usual newsletter preamble
        if len(t) > 80 and not re.match(r"(?i)^(view (this|in)|unsubscribe|forwarded|having trouble)", t):
            return t[:maxlen]
    if html:
        body = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", html)
        for para in re.findall(r"(?is)<p[^>]*>(.*?)</p>", body):
            t = re.sub(r"<[^>]+>", " ", para)
            t = re.sub(r"\s+", " ", t).strip()
            if len(t) > 80:
                return t[:maxlen]
        t = re.sub(r"<[^>]+>", " ", body)
        return re.sub(r"\s+", " ", t).strip()[:maxlen]
    return ""


def _best_link(html, plain, skip_hosts):
    """First outbound link that is not tracking, social or unsubscribe."""
    urls = re.findall(r'href=["\'](https?://[^"\']+)', html or "")
    urls += re.findall(r'(https?://[^\s<>"\')]+)', plain or "")
    for u in urls:
        low = u.lower()
        if any(h in low for h in skip_hosts):
            continue
        if re.search(r"(unsubscribe|/track|/open|utm_|list-manage|mailchi\.mp|pixel)", low):
            continue
        return u.rstrip(").,")
    return ""


def fetch(cfg, log=print):
    mailcfg = cfg.get("email", {})
    if not mailcfg.get("enabled", True):
        return []
    user = os.environ.get("MAIL_USER")
    password = os.environ.get("MAIL_PASSWORD")
    if not (user and password):
        log("  email: MAIL_USER/MAIL_PASSWORD not set, skipping")
        return []

    host = os.environ.get("MAIL_HOST", mailcfg.get("host", "imap.gmail.com"))
    folder = mailcfg.get("folder", "INBOX")
    days = int(mailcfg.get("lookback_days", 8))
    senders = mailcfg.get("senders", {})
    skip_hosts = [h.lower() for h in mailcfg.get("skip_link_hosts", [])]

    items = []
    try:
        M = imaplib.IMAP4_SSL(host)
        M.login(user, password)
        M.select(folder, readonly=True)          # read-only: never alters the mailbox
        since = (dt.date.today() - dt.timedelta(days=days)).strftime("%d-%b-%Y")
        typ, data = M.search(None, f'(SINCE {since})')
        ids = data[0].split() if data and data[0] else []
        log(f"  email: {len(ids)} messages since {since}")
        for num in ids[-int(mailcfg.get("max_messages", 120)):]:
            try:
                typ, raw = M.fetch(num, "(RFC822)")
                if not raw or not raw[0]:
                    continue
                msg = email.message_from_bytes(raw[0][1])
            except Exception:
                continue
            frm = _decode(msg.get("From"))
            addr = (re.search(r"[\w\.\+-]+@[\w\.-]+", frm) or [""])[0] if frm else ""
            addr = addr.lower() if isinstance(addr, str) else ""
            match = next((v for k, v in senders.items() if k.lower() in frm.lower()
                          or k.lower() in addr), None)
            if not match:
                continue
            subject = _decode(msg.get("Subject")).strip()
            if not subject:
                continue
            try:
                when = parsedate_to_datetime(msg.get("Date"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=dt.timezone.utc)
            except Exception:
                when = dt.datetime.now(dt.timezone.utc)
            plain, html = _body(msg)
            items.append({
                "id": None,
                "title": subject,
                "link": _best_link(html, plain, skip_hosts),
                "summary": _first_para(plain, html),
                "source": match.get("name", "Email"),
                "kind": match.get("kind", "analysis"),
                "weight": float(match.get("weight", 1.1)),
                "hint": match.get("hint", []),
                "published": when.isoformat(),
            })
        M.close(); M.logout()
    except Exception as ex:  # noqa: BLE001
        log(f"  email: failed ({type(ex).__name__}: {str(ex)[:70]})")
        return []

    log(f"  email: {len(items)} newsletter items matched")
    return items
