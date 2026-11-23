#!/usr/bin/env python3
"""News monitor for trader-news (NSE stocks). Standard library only.

Watches three free sources for the NSE stocks you watch or hold and prints
only what is new since the last check, newest first, with how far each stock
is from its previous close and a flag on anything that touches your symbols.

    python news.py                                   # watchlist + plan + portfolio symbols
    python news.py --tickers TCS INFY "M&M"
    python news.py --tickers TCS --all --hours 72    # everything in the last 3 days, seen or not
    python news.py --tickers TCS --name "TCS"        # search Google News for another name
    python news.py --sources nse google              # leave Yahoo's headline feed out
    python news.py --no-moves                        # skip the price line per symbol

Your symbols, when --tickers is not given: every hit in trader/watchlist.json
(trader-scanner), the symbol in trader/plan.json (trader-exit) and the
positions in trader/portfolio.json. Symbols are NSE symbols (RELIANCE,
HDFCBANK, M&M, BAJAJ-AUTO); the tool adds ".NS" for Yahoo and strips it again.

Sources:
  1. NSE corporate announcements RSS (source "nse-announcement"), one request
     a run: what companies filed with the exchange.
     https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml
     An item is yours when the PDF file name in its link starts with your
     symbol (PTCINDUSTRIES_2411...pdf) or, failing that, when its title is
     your company's name. Subjects that usually matter (results, board
     meeting, directors / KMP / auditor, acquisition or scheme, fund raising,
     credit rating, insolvency or default, suspension, buyback, dividend...)
     are marked with "!".
     THE FEED IS A SHORT ROLLING WINDOW of the newest filings (one item at
     01:00 on a holiday, more in market hours). A filing that came and went
     between two checks is missed, so run the check on a loop in the session.
  2. Google News RSS search by company name (source "google-news"), one
     request a symbol: the Indian business press.
     https://news.google.com/rss/search?q="HDFC Bank" when:2d&hl=en-IN&gl=IN&ceid=IN:en
     The name comes from --name, else "names" in trader/config.json, else the
     official name with "Limited" taken off ("HDFC Bank Limited" -> "HDFC
     Bank"). Official names are read from trader/universe.txt (written by
     trader-scanner) or, when a symbol is not there, from NSE's EQUITY_L.csv,
     downloaded once and kept in trader/state/nse-names.json for 7 days.
     Bare quote pages are dropped, the same story from several publishers is
     shown once (the earliest, with "+N more") and at most --max-items (8) are
     shown per symbol, the ones that name the company in the title first.
     A search by name finds stories that only mention the company in passing.
  3. Yahoo Finance headline RSS (source "yahoo"), one request a symbol. Thin
     for NSE stocks (empty for RELIANCE.NS and HDFCBANK.NS on 2026-11-24) and
     loose: the M&M.NS feed led with Mars, the maker of M&M's chocolate.
     https://feeds.finance.yahoo.com/rss/2.0/headline?s=<SYMBOL>.NS&region=IN&lang=en-IN
  - The move: Yahoo Finance's chart endpoint (unofficial, NSE prices on it can
    lag), the last price against the previous close, in rupees:
    https://query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS?range=1d&interval=5m

What this tool does NOT read, and says so in every run:
  - Filings made only to BSE, and NSE filings older than the feed's window.
    For the full list open the stock's corporate announcements page on the
    NSE or BSE website.
  - The stories themselves. It reads titles. A headline is a lead.
An empty result is not "no news": the tool prints a note for every source
that had nothing for a symbol, and never fills a gap by guessing.

Times print in India Standard Time (IST, a fixed UTC+5:30; never the
computer's own zone). --now "2026-11-23 09:45" is India time. State:
trader/state/news.json remembers what you have seen (30 days), so an item
shows once. Offline: --offline DIR reads nse-announcements.xml,
google-<SYMBOL>.xml, yahoo-<SYMBOL>.xml, chart-<SYMBOL>.json and (for the
names) EQUITY_L.csv from DIR. The tests use it.

Headlines are leads, not facts: open the story or the filing before acting,
and treat nothing here as advice.
"""

import argparse
import datetime as _dt
import email.utils
import gzip
import hashlib
import csv
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=IN&lang=en-IN"
CHART = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
HOSTS = ("query2", "query1")
UA = "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)"
KEEP_DAYS = 30
MOVE_PCT = 3.0  # a stock this far from its previous close is flagged as moving
IST_OFFSET = _dt.timedelta(hours=5, minutes=30)
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}(\.BO)?$")
RUPEE = "₹"
GOOGLE = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN%%3Aen"
NSE_FEED = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"
NSE_LIST = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
SOURCES = ("nse", "google", "yahoo")
MAX_ITEMS = 8        # Google News headlines shown per symbol
NAMES_DAYS = 7       # how long the downloaded symbol -> name map is trusted
SAME_STORY = 0.6     # share of words two titles must have in common to count as one story
NOT_READ = ("Not read: filings made only to BSE, and NSE filings older than the feed's short rolling window "
            "(for the full list open the stock's corporate announcements page on the NSE or BSE website).")
FILINGS_NOTE = NOT_READ   # the old name, kept for anything that imported it

# Google News results that are a quote page, not a story
JUNK = [re.compile(p, re.I) for p in (
    r"stock price, news, quote",
    r"\bquote (?:and|&) (?:history|chart)",
    r"\b(?:share|stock) price\s*[-|:]\s*live\b",
    r"\b(?:share|stock) prices?(?:[\s,&|:-]|today|live|chart|quote|history|nse|bse|and)*$",
    r"\blive (?:nse|bse)(?:/(?:nse|bse))?\s*:",
    r"^news by .{1,60} on tradingview",
    r"\bstock quotes?\b",
)]

# NSE announcement subjects that usually matter to a trade, checked in this order
IMPORTANT = [
    ("results", r"financial results?|outcome of (?:the )?board meeting|board meeting"),
    ("management", r"change in directors?|change in director|key managerial|\bKMP\b|auditors?\b|resign|cessation"
                   r"|change in management"),
    ("deal", r"acquisition|merger|amalgamation|demerger|scheme of arrangement|takeover|open offer"
             r"|sale or disposal|slump sale|disinvestment"),
    ("fund raising", r"fund rais|raising of funds|\bQIP\b|qualified institutions? placement"
                     r"|preferential (?:issue|allotment)|rights issue|issue of securities|\bFPO\b"),
    ("rating", r"credit rating"),
    ("insolvency", r"insolvency|\bdefault|\bCIRP\b|liquidation|winding up|one time settlement|\bfraud"),
    ("halt", r"trading halt|suspension|suspended|delist"),
    ("payout", r"buy-?back|dividend|bonus|stock split|sub-?division|record date"),
]
STOP = frozenset("a an the and or as in of to for on by at is are its it with from after amid says say".split())

KEYWORDS = [
    ("earnings", r"\bearnings\b|\bresults\b|\bEPS\b|\bguidance\b|\bforecast\b|\bnet profit\b|\bQ[1-4]\b"
                 r"|\bquarterly\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|price target|target price|\boutperform\b|\bunderperform\b"),
    ("legal", r"\blawsuit\b|\bsued\b|\bprobe\b|\binvestigation\b|\bantitrust\b|\bjudge\b|\bcourt\b|\bNCLT\b"
              r"|\btribunal\b|\braids?\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bdemerger\b|\bbuyout\b|\btakeover\b|\bdeals?\b"
             r"|\bstake sale\b|\bopen offer\b"),
    ("capital", r"\bdividend\b|\bbuyback\b|\brepurchase\b|\bstock split\b|\boffering\b|\bbonus issue\b"
                r"|\brights issue\b|\bQIP\b|\bOFS\b|\bblock deal\b|\bbulk deal\b|\bpledg(?:e|ed|ing)\b"),
    ("people", r"\bCEO\b|\bCFO\b|\bmanaging director\b|\bresign|\bsteps down\b|\bappoint"),
    ("risk", r"\brecall\b|\bbankrupt|\binsolvency\b|\bdefault\b|\bdelist|\bhack|\bbreach\b|\boutage\b|\bfraud\b"),
    ("halt", r"\bhalt(?:s|ed)?\b|\btrading (?:pause|suspen)|\b(?:upper|lower) circuit\b"
             r"|\bcircuit (?:limit|filter|breaker)"),
    ("regulatory", r"\bSEBI\b|\bUSFDA\b|\bFDA\b|\bapproval\b|\bapproves\b|\bclinical\b|\bCCI\b|\bIRDAI\b|\bTRAI\b"),
    ("macro", r"\bRBI\b|\brepo rate\b|\bMPC\b|\bCPI\b|\bWPI\b|\binflation\b|\btariffs?\b|\bUnion Budget\b|\bGST\b"
              r"|\bcrude\b|\brupee\b|\bFIIs?\b|\bFPIs?\b|\bNifty\b|\bSensex\b"),
]


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time

def to_ist(utc):
    """Aware or naive UTC -> naive India Standard Time (a fixed UTC+5:30, no daylight saving)."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(_dt.timezone.utc)
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    """Naive India time -> aware UTC."""
    return (local.replace(tzinfo=None) - IST_OFFSET).replace(tzinfo=_dt.timezone.utc)


def parse_now(text):
    """--now -> aware UTC. A time without an offset (or ending in " IST") is India time."""
    if not text:
        return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    try:
        d = _dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        raise NewsError("--now wants a time like \"2026-11-23 09:45\" (India time) or ...Z (UTC)")
    if d.tzinfo is None:
        return ist_to_utc(d)
    return d.astimezone(_dt.timezone.utc)


# ---------------------------------------------------------------- inputs

def yahoo_symbol(sym):
    """NSE symbol -> Yahoo symbol: RELIANCE -> RELIANCE.NS. .NS / .BO and ^ index symbols are kept."""
    s = sym.strip().upper()
    return s if s.endswith((".NS", ".BO")) or s.startswith("^") else s + ".NS"


def nse_symbol(sym):
    """What output and files show: TCS.NS -> TCS."""
    s = str(sym or "").strip().upper()
    return s[:-3] if s.endswith(".NS") else s


def inr(x):
    return RUPEE + "{:,.2f}".format(float(x))


def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """{NSE symbol: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = nse_symbol(h.get("symbol") or h.get("ticker"))
        if sym:
            out[sym] = h.get("name")
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.setdefault(nse_symbol(pl["symbol"]), pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = nse_symbol(p.get("ticker") or p.get("symbol"))
        if sym:
            out[sym] = p.get("name") or out.get(sym)
    return out


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.5):
        self.offline = offline
        self.pause = pause
        self._last = 0.0

    def get(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise NewsError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise NewsError("could not reach %s: %s" % (url, exc.reason))


def rss_url(sym):
    return YAHOO % urllib.parse.quote(yahoo_symbol(sym), safe="")


def chart_url(sym, host=HOSTS[0]):
    return CHART % (host, urllib.parse.quote(yahoo_symbol(sym), safe=""))


def google_days(hours):
    """Google's when: filter counts whole days."""
    return max(1, int(math.ceil(float(hours) / 24.0)))


def google_query(name, hours=48):
    """'"HDFC Bank" when:2d'. A name that brings its own quotes or OR is used as written."""
    name = " ".join(str(name).split())
    if '"' not in name and " OR " not in name:
        name = '"%s"' % name
    return "%s when:%dd" % (name, google_days(hours))


def google_url(name, hours=48):
    return GOOGLE % urllib.parse.quote_plus(google_query(name, hours))


# ---------------------------------------------------------------- company names

def search_name(official):
    """The name people write: "HDFC Bank Limited" -> "HDFC Bank", "Mahindra & Mahindra Ltd." -> "Mahindra & Mahindra"."""
    name = " ".join(str(official or "").split())
    while True:
        cut = re.sub(r"[\s,.\-]*\b(?:limited|ltd)\b\.?\s*$", "", name, flags=re.I).rstrip(" ,.-")
        if cut == name:
            return name
        name = cut


def company_key(name):
    """For matching a filing's title to a company: case, punctuation, "Limited" and "&" / "and" do not count."""
    low = search_name(name).lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", re.sub(r"\bthe\b", " ", low))


def parse_universe(text):
    """trader/universe.txt (SYMBOL|Name a line, written by trader-scanner) -> {SYMBOL: official name}."""
    out = {}
    for line in text.splitlines():
        sym, bar, name = line.strip().partition("|")
        if bar and sym.strip() and name.strip():
            out[sym.strip().upper()] = " ".join(name.split())
    return out


def parse_equity_list(data):
    """NSE's EQUITY_L.csv -> {SYMBOL: official name}, every series."""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    rows = [r for r in csv.reader(io.StringIO(text.lstrip("﻿").replace("\r", ""))) if any(c.strip() for c in r)]
    hdr = [h.strip().upper() for h in rows[0]] if rows else []
    if "SYMBOL" not in hdr or "NAME OF COMPANY" not in hdr:
        raise NewsError("not NSE's EQUITY_L.csv (it starts %r)" % text.strip()[:40])
    s, n = hdr.index("SYMBOL"), hdr.index("NAME OF COMPANY")
    out = {}
    for r in rows[1:]:
        if len(r) > max(s, n) and r[s].strip() and r[n].strip():
            out[r[s].strip().upper()] = " ".join(r[n].split())
    return out


def official_names(symbols, fetcher, now, universe="trader/universe.txt", cache="trader/state/nse-names.json",
                   errors=None):
    """{SYMBOL: official name} for the symbols it can find: the scanner's universe.txt first, then the
    cached copy of NSE's equity list (downloaded when missing or older than NAMES_DAYS)."""
    errors = [] if errors is None else errors
    out = {}
    if universe and os.path.isfile(universe):
        with open(universe, encoding="utf-8", errors="replace") as fh:
            known = parse_universe(fh.read())
        out.update({t: known[t] for t in symbols if t in known})
    if all(t in out for t in symbols):
        return out
    names = None
    if not fetcher.offline:
        try:
            doc = load_json(cache) or {}
        except ValueError:
            doc = {}
        when = parse_time(doc.get("fetched"))
        if doc.get("names") and when is not None and now - when < _dt.timedelta(days=NAMES_DAYS) and when <= now:
            names = doc["names"]
    if names is None:
        try:
            names = parse_equity_list(fetcher.get(NSE_LIST, "EQUITY_L.csv"))
        except NewsError as exc:
            errors.append("company names: %s" % exc)
            return out
        if not fetcher.offline and cache:
            folder = os.path.dirname(cache)
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(cache, "w", encoding="utf-8") as fh:
                json.dump({"fetched": now.isoformat(), "source": NSE_LIST, "names": names}, fh, ensure_ascii=False)
                fh.write("\n")
    for t in symbols:
        if t not in out and names.get(t):
            out[t] = names[t]
    return out


# ---------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_time(value):
    """RFC 822 (RSS) or ISO 8601 -> aware UTC datetime, or None. No offset means UTC (feed times)."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def parse_rss(data, ticker):
    """Yahoo headline RSS -> items. An empty feed (Yahoo sends one for many NSE symbols) is []."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("RSS for %s is not valid XML: %s" % (ticker, exc))
    out = []
    for it in root.iter("item"):
        title = _text(it, "title")
        link = _text(it, "link")
        guid = _text(it, "guid") or link
        out.append({"id": "rss:" + guid, "source": "yahoo", "publisher": "Yahoo Finance", "kind": "headline",
                    "ticker": ticker, "title": title, "summary": _text(it, "description"),
                    "link": link, "time": parse_time(_text(it, "pubDate"))})
    return out


def is_junk(title):
    """A quote / price page rather than a story."""
    return any(rx.search(title) for rx in JUNK)


def parse_google(data, ticker):
    """Google News RSS search -> (items, how many quote pages were dropped). Titles lose their " - Publisher"."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("Google News feed for %s is not valid XML: %s" % (ticker, exc))
    out, junk = [], 0
    for it in root.iter("item"):
        title = " ".join(_text(it, "title").split())
        publisher = " ".join(_text(it, "source").split())
        if publisher and title.endswith(" - " + publisher):
            title = title[:-len(publisher) - 3].rstrip()
        elif not publisher and " - " in title:
            title, publisher = [p.strip() for p in title.rsplit(" - ", 1)]
        if not title:
            continue
        if is_junk(title):
            junk += 1
            continue
        link = _text(it, "link")
        out.append({"id": "gn:" + (_text(it, "guid") or link), "source": "google-news",
                    "publisher": publisher or "Google News", "kind": "headline", "ticker": ticker, "title": title,
                    "summary": "", "link": link, "time": parse_time(_text(it, "pubDate")), "more": 0, "also": []})
    return out, junk


def _words(title):
    return frozenset(w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in STOP)


def merge_same_story(items):
    """Near-identical titles from several publishers -> the earliest one, with "more" and "also" filled in."""
    kept = []
    order = sorted(range(len(items)), key=lambda i: (items[i]["time"] is None,
                                                     items[i]["time"] or _dt.datetime.max, i))
    for i in order:
        it = items[i]
        words = _words(it["title"])
        twin = None
        for rep, rep_words in kept:
            both = len(words | rep_words)
            if words == rep_words or (len(words) >= 4 and both and len(words & rep_words) / float(both) >= SAME_STORY):
                twin = rep
                break
        if twin is None:
            kept.append((it, words))
            continue
        twin["more"] = twin.get("more", 0) + 1
        twin.setdefault("also", [])
        if it["publisher"] not in twin["also"] and it["publisher"] != twin["publisher"]:
            twin["also"].append(it["publisher"])
        twin.setdefault("same_story", []).append(it["id"])
    return [rep for rep, _ in kept]


MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}


def parse_nse_time(value):
    """NSE's "24-Nov-2026 00:28:46" (India time) -> aware UTC, or None."""
    m = re.match(r"\s*(\d{1,2})-([A-Za-z]{3})-(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$", value or "")
    if not m or m.group(2).lower() not in MONTHS:
        return parse_time(value)
    try:
        local = _dt.datetime(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)), int(m.group(4)),
                             int(m.group(5)), int(m.group(6) or 0))
    except ValueError:
        return None
    return ist_to_utc(local)


def link_symbol(link):
    """The NSE symbol a filing's PDF is named after: .../corporate/PTCINDUSTRIES_2411...pdf -> PTCINDUSTRIES."""
    name = urllib.parse.unquote(link.rsplit("/", 1)[-1])
    head = name.split("_", 1)[0].strip().upper()
    return head if "_" in name and SYMBOL.match(head) else ""


def importance(subject, description=""):
    """Why a filing matters ("results", "management", "deal"...), or "". The subject decides; the description
    is read only when there is no subject or it is a catch-all such as "General Updates"."""
    hay = subject if subject and not re.match(r"(?i)\s*(?:general\s+)?updates?\s*$", subject) else description
    for label, rx in IMPORTANT:
        if re.search(rx, hay, re.I):
            return label
    return ""


def parse_nse(data):
    """NSE announcements RSS -> every filing in the feed's window (not yet matched to your symbols)."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("NSE announcements feed is not valid XML: %s" % exc)
    out = []
    for it in root.iter("item"):
        company = " ".join(_text(it, "title").split())
        link = _text(it, "link")
        desc, bar, subject = _text(it, "description").partition("|SUBJECT:")
        desc, subject = " ".join(desc.split()), " ".join(subject.split())
        stamp = _text(it, "pubDate")
        why = importance(subject, desc)
        out.append({"id": "nse:%s|%s" % (link, stamp), "source": "nse-announcement", "publisher": "NSE filing",
                    "kind": "filing", "ticker": link_symbol(link), "company": company,
                    "title": subject or desc or company, "subject": subject, "summary": desc, "link": link,
                    "time": parse_nse_time(stamp), "important": bool(why), "why": why})
    return out


def match_filings(filings, watch, official):
    """The filings that are yours: by the symbol the PDF is named after, else by the company name in the title."""
    by_name = {}
    for t in watch:
        for name in (official.get(t), watch.get(t)):
            if name and company_key(name):
                by_name.setdefault(company_key(name), t)
    out = []
    for f in filings:
        if f["ticker"] in watch:
            f["matched_by"] = "symbol"
        elif company_key(f["company"]) in by_name:
            f["ticker"], f["matched_by"] = by_name[company_key(f["company"])], "name"
        else:
            continue
        out.append(f)
    return out


def parse_chart(data, ticker):
    """Yahoo chart JSON -> {"ticker", "price", "prev_close", "pct", "time", "currency"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable (check the NSE symbol)" % ticker)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        raise NewsError("chart for %s has no price" % ticker)
    when = meta.get("regularMarketTime")
    return {"ticker": ticker, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2), "currency": meta.get("currency") or "INR",
            "time": _dt.datetime.fromtimestamp(when, _dt.timezone.utc) if when else None}


def collect_moves(fetcher, watch, errors=None):
    errors = [] if errors is None else errors
    out = []
    for t in sorted(watch):
        name = "chart-%s.json" % t
        try:
            try:
                data = fetcher.get(chart_url(t, HOSTS[0]), name)
            except NewsError as exc:
                if fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
                    raise
                data = fetcher.get(chart_url(t, HOSTS[1]), name)
            out.append(parse_chart(data, t))
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (t, exc))
    return out


def mention_patterns(watch):
    pats = {}
    for t, name in watch.items():
        s = re.escape(t)
        alts = [r"\(%s\)" % s, r"\b(?:NSE|BSE)\s*:\s*%s(?![A-Z0-9&-])" % s, r"\b%s\.(?:NS|BO)\b" % s]
        if name and len(name) >= 4:
            alts.append(r"\b%s\b" % re.escape(name).replace(r"\ ", r"[\s-]"))
        pats[t] = re.compile("|".join(alts))
    return pats


def tag(items, watch):
    """Which of your symbols each item touches, and why it matters."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        touches = {it["ticker"]} if it["ticker"] in watch else set()
        for t, rx in pats.items():
            if rx.search(hay):
                touches.add(t)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
    return items


def dedupe(items):
    """One item per story: same id, or same link, or (headlines only) same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"]]
        if it.get("kind") != "filing":      # two filings with the same subject are two filings
            keys.append("%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                                   it["time"].date() if it["time"] else ""))
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the check

def collect(fetcher, watch, errors=None, feeds=None):
    """Every item of every symbol's Yahoo feed. feeds[symbol] = that feed's items (for the coverage notes)."""
    errors = [] if errors is None else errors
    items = []
    for t in sorted(watch):
        try:
            got = parse_rss(fetcher.get(rss_url(t), "yahoo-%s.xml" % t), t)
        except NewsError as exc:
            errors.append("Yahoo headlines for %s: %s" % (t, exc))
            continue
        if feeds is not None:
            feeds[t] = got
        items += got
    return items


def collect_google(fetcher, names, hours, errors=None, feeds=None):
    """Google News by company name. names = {symbol: search name or None}. Returns (stories, every raw item):
    stories has one item per story, raw is what the state file must remember so a re-run stays quiet.
    feeds[symbol] = {"name", "stories", "raw", "junk"} for the coverage notes."""
    errors = [] if errors is None else errors
    stories, raw = [], []
    for t in sorted(names):
        name = names[t]
        if not name:
            errors.append("Google News skipped for %s: no company name for it (pass --name \"...\" or add it to "
                          "\"names\" in trader/config.json)" % t)
            continue
        try:
            got, junk = parse_google(fetcher.get(google_url(name, hours), "google-%s.xml" % t), t)
        except NewsError as exc:
            errors.append("Google News for %s: %s" % (t, exc))
            continue
        merged = merge_same_story(got)
        if feeds is not None:
            feeds[t] = {"name": name, "stories": merged, "raw": len(got), "junk": junk}
        raw += got
        stories += merged
    return stories, raw


def collect_nse(fetcher, watch, official, errors=None, feed=None):
    """NSE announcements for your symbols. feed gets {"items", "oldest", "newest"}: the whole window's size."""
    errors = [] if errors is None else errors
    try:
        filings = parse_nse(fetcher.get(NSE_FEED, "nse-announcements.xml"))
    except NewsError as exc:
        errors.append("NSE announcements: %s" % exc)
        return []
    if feed is not None:
        times = [f["time"] for f in filings if f["time"] is not None]
        feed.update({"items": len(filings), "oldest": min(times) if times else None,
                     "newest": max(times) if times else None})
    return match_filings(filings, watch, official)


def nse_note(feed, mine, now, hours):
    """What the rolling window held when none of it is shown for your symbols, or None."""
    if not feed or any(in_window(f, now, hours) for f in mine):
        return None
    if not feed["items"]:
        return "the NSE announcements feed is empty right now"
    span = ""
    if feed["oldest"] is not None:
        a, b = to_ist(feed["oldest"]), to_ist(feed["newest"])
        span = (", filed %s IST" % a.strftime("%Y-%m-%d %H:%M") if a == b else
                ", filed %s to %s IST" % (a.strftime("%Y-%m-%d %H:%M"), b.strftime("%Y-%m-%d %H:%M")))
    return ("no NSE announcements for your symbols in the feed's window right now (it holds %d filing%s%s)"
            % (feed["items"], "" if feed["items"] == 1 else "s", span))


def google_notes(feeds, now, hours):
    """One line per symbol Google News had nothing for, with the name that was searched."""
    notes = []
    for t in sorted(feeds):
        f = feeds[t]
        if any(in_window(it, now, hours) for it in f["stories"]):
            continue
        extra = " (%d quote page%s dropped)" % (f["junk"], "" if f["junk"] == 1 else "s") if f["junk"] else ""
        notes.append("no headlines from Google News for %s in the last %g hours, searched \"%s\"%s"
                     % (t, hours, f["name"], extra))
    return notes


def cap_google(shown, watch, max_items=MAX_ITEMS):
    """At most max_items Google News stories a symbol: those naming the company in the title first, among them
    the tagged ones (earnings, deal, analyst...) first, then the newest. Returns (items to show, {symbol: how
    many were left out})."""
    if not max_items or max_items < 0:
        return shown, {}
    pats = mention_patterns(watch)
    groups = {}
    for it in shown:
        if it["source"] == "google-news":
            groups.setdefault(it["ticker"], []).append(it)
    drop, hidden = set(), {}
    for t, group in groups.items():
        if len(group) <= max_items:
            continue
        rx = pats.get(t)
        bare = re.compile(r"(?<![A-Za-z0-9&-])%s(?![A-Za-z0-9&-])" % re.escape(t))

        def rank(it, rx=rx, bare=bare):
            named = bool((rx and rx.search(it["title"])) or (len(t) >= 3 and bare.search(it["title"])))
            return (not named, not it.get("tags"), -(it["time"].timestamp() if it["time"] else 0))
        for it in sorted(group, key=rank)[max_items:]:
            drop.add(id(it))
        hidden[t] = len(group) - max_items
    return [it for it in shown if id(it) not in drop], hidden


def in_window(it, now, hours):
    """Inside the last `hours`, and not from after a pinned --now."""
    if it["time"] is None:
        return True
    return now - _dt.timedelta(hours=hours) <= it["time"] <= now + _dt.timedelta(minutes=10)


def coverage_notes(feeds, now, hours):
    """One plain line per symbol Yahoo had nothing for, so silence is never read as 'no news'."""
    notes = []
    for t in sorted(feeds):
        got = feeds[t]
        if not got:
            notes.append("no headlines from Yahoo for %s (its feed is empty)" % t)
            continue
        if any(in_window(it, now, hours) for it in got):
            continue
        times = [it["time"] for it in got if it["time"] is not None and it["time"] <= now]
        newest = (", the newest of its %d is from %s" % (len(got), to_ist(max(times)).strftime("%Y-%m-%d"))
                  if times else "")
        notes.append("no headlines from Yahoo for %s in the last %g hours%s" % (t, hours, newest))
    return notes


def select(items, state, now, hours=48, show_all=False):
    """Items within the window; only unseen ones unless show_all."""
    seen = state.setdefault("seen", {})
    out = []
    for it in items:
        if not in_window(it, now, hours):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp
    state.pop("ciks", None)   # left behind by the US version's SEC lookup


def render_moves(moves, move_pct=MOVE_PCT, now=None):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        when = ""
        if m.get("time"):
            local = to_ist(m["time"])
            when = " at %s IST" % local.strftime("%H:%M")
            if now is not None and local.date() != to_ist(now).date():
                when += " on %s" % local.date().isoformat()   # a stale quote: a holiday, a weekend, last night
        out.append("%-10s %s %+.2f%% vs previous close %s%s  %s" % (
            m["ticker"], inr(m["price"]), m["pct"], inr(m["prev_close"]), when, flag))
    return [line.rstrip() for line in out]


READ = {
    "nse": "NSE corporate announcements (a short rolling window of the newest filings: run this on a loop "
           "during the session, a filing that scrolled out between checks is missed)",
    "google": "Google News by company name (it also finds stories that only mention the company)",
    "yahoo": "Yahoo Finance headlines (thin for NSE stocks, and not always about your company)",
}


def footer(now, sources=SOURCES):
    read = "; ".join(READ[s] for s in SOURCES if s in sources)
    return ("Checked %s. Read: %s. %s Headlines are leads, not facts; open the story or the filing before "
            "acting. Not financial advice." % (to_ist(now).strftime("%Y-%m-%d %H:%M IST"), read, NOT_READ))


def item_title(it):
    title = it["title"]
    if it.get("kind") == "filing":
        if it.get("summary") and it["summary"] != title:
            title = "%s: %s" % (title, it["summary"])
        if len(title) > 220:
            title = title[:217].rstrip() + "..."
        if it.get("important"):
            title = "! " + title
    elif it.get("more"):
        title += " (+%d more)" % it["more"]
    return title


def render(items, errors, now, moves=None, move_pct=MOVE_PCT, sources=SOURCES):
    out = render_moves(moves or [], move_pct, now)
    if out:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M IST") if it["time"] else "time unknown"
        who = ",".join(it["touches"]) or it["ticker"]
        out.append("  %s  %-11s %-13s %s" % (when, who, it.get("publisher") or it["source"], item_title(it)))
        extra = []
        if it.get("why"):
            extra.append("important: " + it["why"])
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        extra.append(it["link"])
        out.append("      " + "  ".join(extra))
    for e in errors:
        out.append("note: %s" % e)
    out.append(footer(now, sources))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d.pop("same_story", None)
        d["time"] = it["time"].isoformat() if it["time"] else None
        d["time_ist"] = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M") if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="NSE symbols; default: watchlist + plan + portfolio")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--sources", nargs="+", choices=SOURCES, default=list(SOURCES), metavar="SOURCE",
                    help="which sources to read: nse google yahoo (default: all three)")
    ap.add_argument("--name", help='company name to search Google News for, with a single ticker ("HDFC Bank")')
    ap.add_argument("--max-items", type=int, default=MAX_ITEMS,
                    help="Google News headlines shown per symbol (default %d, 0 for no limit)" % MAX_ITEMS)
    ap.add_argument("--config", default="trader/config.json", help='may hold "names": {"TCS": "TCS"}')
    ap.add_argument("--universe", default="trader/universe.txt", help="SYMBOL|Name lines from trader-scanner")
    ap.add_argument("--names-cache", default="trader/state/nse-names.json",
                    help="where the downloaded symbol -> company name map is kept")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per symbol")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help='pretend it is this time ("2026-11-23 09:45" India time, or ISO with Z)')
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    errors = []
    if args.tickers:
        watch = {nse_symbol(t): None for t in args.tickers if nse_symbol(t)}
        known = watch_from_files(args.portfolio, args.watchlist, args.plan)
        watch.update({t: known[t] for t in watch if known.get(t)})
    else:
        watch = watch_from_files(args.portfolio, args.watchlist, args.plan)
    for t in sorted(watch):
        if not SYMBOL.match(t):
            errors.append("%s is not an NSE symbol like RELIANCE, M&M or BAJAJ-AUTO: skipped" % t)
            del watch[t]
    if not watch:
        for e in errors:
            print("news: %s" % e, file=sys.stderr)
        print("news: no tickers. Pass --tickers, or run /trader-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    try:
        now = parse_now(args.now)
    except NewsError as exc:
        print("news: %s" % exc, file=sys.stderr)
        return 2
    if args.name and len(watch) != 1:
        print("news: --name goes with exactly one ticker; for several, put \"names\": {\"SYMBOL\": \"Name\"} in "
              "%s" % args.config, file=sys.stderr)
        return 2
    sources = [s for s in SOURCES if s in args.sources]
    state = load_json(args.state) or {}
    fetcher = Fetcher(offline=args.offline)

    # company names: the official one matches filings, the search name goes to Google News
    official, names = {}, {}
    if "nse" in sources or "google" in sources:
        try:
            cfg_names = (load_json(args.config) or {}).get("names") or {}
        except ValueError:
            cfg_names = {}
            errors.append("%s is not valid JSON: its names were not used" % args.config)
        cfg_names = {nse_symbol(k): " ".join(str(v).split()) for k, v in cfg_names.items() if v}
        need = sorted(t for t in watch if "nse" in sources or not (args.name or cfg_names.get(t)))
        if need:
            official = official_names(need, fetcher, now, args.universe, args.names_cache, errors)
        for t in watch:
            names[t] = (args.name or cfg_names.get(t) or search_name(official.get(t) or watch[t] or "") or None)
            if names[t] and '"' not in names[t] and " OR " not in names[t]:
                watch[t] = names[t]              # so a headline that names the company is tagged as touching it

    items, extra_seen, notes = [], [], []
    if "nse" in sources:
        nse_feed = {}
        mine = collect_nse(fetcher, watch, official, errors, nse_feed)
        items += mine
        note = nse_note(nse_feed, mine, now, args.hours)
        if note:
            notes.append(note)
    if "google" in sources:
        gfeeds = {}
        stories, extra_seen = collect_google(fetcher, names, args.hours, errors, gfeeds)
        items += stories
        notes += google_notes(gfeeds, now, args.hours)
    if "yahoo" in sources:
        feeds = {}
        items += collect(fetcher, watch, errors=errors, feeds=feeds)
        notes += coverage_notes(feeds, now, args.hours)
    items = dedupe(tag(items, watch))
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    shown, hidden = cap_google(shown, watch, args.max_items)
    for t in sorted(hidden):
        notes.append("%d more Google News headline%s for %s not shown (--max-items %d)"
                     % (hidden[t], "" if hidden[t] == 1 else "s", t, args.max_items))
    errors += notes
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   time_ist=to_ist(m["time"]).strftime("%Y-%m-%d %H:%M") if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({"checked": now.isoformat(), "checked_ist": to_ist(now).strftime("%Y-%m-%d %H:%M"),
                          "sources": sources, "names": names, "moves": mv, "items": _json_ready(shown),
                          "notes": errors, "not_read": NOT_READ}, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct, sources))
    if not args.dry_run:
        remember(items + extra_seen, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
