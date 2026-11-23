#!/usr/bin/env python3
"""Insider and big-deals tracker for trader-insider (NSE). Standard library only.

Reads four things NSE publishes openly on its archive host, after the trade:

    python deals.py bulk                          # last session's bulk and block deals, per stock
    python deals.py bulk --symbol LENSKART --min-crore 10
    python deals.py insider                       # insider-trading disclosures (SEBI PIT rules)
    python deals.py sast                          # takeover-code disclosures (SAST Regulation 29)
    python deals.py new                           # only what was not seen at the last check
    python deals.py new --holdings                # ...and only in stocks you hold, watch or plan

Where the data comes from (https://nsearchives.nseindia.com/...):
  - content/equities/bulk.csv and block.csv: the last session's bulk and block
    deals (client name, buy or sell, quantity, price).
  - content/RSS/Insider_Trading.xml: disclosures under the SEBI (Prohibition of
    Insider Trading) Regulations. Each item links to an XBRL file with the
    detail: who, what category of person, buy / sell / pledge, how many, the
    value, the dates, the mode, the holding before and after.
  - content/RSS/Sast_Regulation29.xml: disclosures under Regulation 29 of the
    takeover code (SAST): company, acquirer, date and a link to a zip or PDF.
    The linked files are not opened by this tool.

What these are, and are not:
  - PUBLIC filings made AFTER the trade. Reading them fast is not inside
    information. There is none here.
  - A bulk deal row is one side of a trade. The other side is often not in
    the file. A client that bought and sold the same stock the same day was
    trading intraday, not taking a view.
  - Nobody's trades are a signal to copy. This tool makes no return claims.

The feeds can be stale. Each run prints the newest item's date for every
source. When this tool was written (2026-11-24) the insider feed's newest item
was dated 02-May-2026 although NSE rebuilt the file that same night.

Company names: the two RSS feeds carry the company NAME, not the symbol. Names
are matched to NSE symbols with trader/universe.txt (SYMBOL|Name, written by
trader-scanner) if it exists, else with NSE's EQUITY_L.csv, downloaded once
and kept in trader/state/EQUITY_L.csv. The insider XBRL carries the symbol
itself, which wins.

State: `new` remembers what it has shown in trader/state/insider.json, so each
deal and each filing alerts once. On the first run, items older than 7 days
are recorded without an alert. Exit code 1 means nothing new.

Offline: --offline DIR reads bulk.csv, block.csv, Insider_Trading.xml,
Sast_Regulation29.xml, EQUITY_L.csv and the XBRL files (by file name) from a
folder instead of the network. The tests use it.

Times are India Standard Time (a fixed UTC+5:30), never the computer's own
time zone. --now "2026-11-24 02:30" is IST.
"""

import argparse
import csv
import datetime as _dt
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

BASE = "https://nsearchives.nseindia.com/"
URLS = {
    "bulk": BASE + "content/equities/bulk.csv",
    "block": BASE + "content/equities/block.csv",
    "insider": BASE + "content/RSS/Insider_Trading.xml",
    "sast": BASE + "content/RSS/Sast_Regulation29.xml",
    "names": BASE + "content/equities/EQUITY_L.csv",
}
FILES = {"bulk": "bulk.csv", "block": "block.csv", "insider": "Insider_Trading.xml",
         "sast": "Sast_Regulation29.xml", "names": "EQUITY_L.csv"}
SOURCES = ("bulk", "block", "insider", "sast")
TITLES = {"bulk": "BULK DEALS", "block": "BLOCK DEALS",
          "insider": "INSIDER TRADING DISCLOSURES (SEBI PIT Regulations)",
          "sast": "TAKEOVER-CODE DISCLOSURES (SAST Regulation 29)"}
UA = "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)"
PAUSE = 0.7                 # seconds between requests (all to the same host)
MAX_BYTES = 5 * 1024 * 1024
IST_OFFSET = _dt.timedelta(hours=5, minutes=30)
RUPEE = "₹"
CRORE = 10000000.0
LAKH = 100000.0
FIRST_DAYS = 7              # first run: older items are recorded, not alerted
STALE_DAYS = 7              # a feed whose newest item is older than this is called stale
KEEP_DAYS = 400             # how long an id stays in the state file
PUBLISHED_AFTER = _dt.time(18, 30)   # the deal files appear in the evening after the session
MONTHS = {m: i + 1 for i, m in enumerate(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
CALENDAR_YEARS = (2026,)
NSE_HOLIDAYS = {
    "2026-01-15": "Municipal Corporation Election - Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Guru Nanak Jayanti",
    "2026-12-25": "Christmas",
}
PUBLIC_NOTE = ("Public filings made after the trade: no inside information here, and nobody's trades are a "
               "signal to copy. Not financial advice.")
BULK_NOTE = ("A bulk or block deal row is one side of a trade; the other side is often not in the file. "
             "\"Bought and sold the same day\" is intraday trading by that client, not an investment view.")


class DealsError(Exception):
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
        raise DealsError("--now wants a time like \"2026-11-24 02:30\" (India time) or ...Z (UTC)")
    if d.tzinfo is None:
        return ist_to_utc(d)
    return d.astimezone(_dt.timezone.utc)


def parse_date(text):
    """'23-NOV-2026', '23-Nov-2026' or '2026-11-23' -> date, else None."""
    t = (text or "").strip()
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$", t)
    try:
        if m and m.group(2).upper() in MONTHS:
            return _dt.date(int(m.group(3)), MONTHS[m.group(2).upper()], int(m.group(1)))
        if re.match(r"^\d{4}-\d{2}-\d{2}$", t):
            return _dt.date.fromisoformat(t)
    except ValueError:
        pass
    return None


def parse_pubdate(text):
    """NSE's RSS pubDate '02-May-2026 16:46' (India time) -> naive IST datetime, else None."""
    m = re.match(r"^\s*(\d{1,2}-[A-Za-z]{3}-\d{4})(?:\s+(\d{1,2}):(\d{2}))?", text or "")
    d = parse_date(m.group(1)) if m else None
    if not d:
        return None
    return _dt.datetime(d.year, d.month, d.day, int(m.group(2) or 0), int(m.group(3) or 0))


def is_trading_day(d):
    return d.weekday() < 5 and d.isoformat() not in NSE_HOLIDAYS


def last_session(now):
    """The latest NSE session whose deal files should be out by `now` (aware UTC)."""
    local = to_ist(now)
    d = local.date()
    if not (is_trading_day(d) and local.time() >= PUBLISHED_AFTER):
        d -= _dt.timedelta(days=1)
    while not is_trading_day(d):
        d -= _dt.timedelta(days=1)
    return d


# ---------------------------------------------------------------- money and symbols

def indian(n):
    """7000000 -> '70,00,000' (Indian digit grouping)."""
    n = int(round(n))
    sign, s = ("-" if n < 0 else ""), str(abs(n))
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return sign + ",".join(parts) + "," + tail


def inr(x):
    return RUPEE + "{:,.2f}".format(float(x))


def to_crore(rupees):
    return rupees / CRORE


def money(rupees):
    """Rupees in crore, or in lakh when under one crore: 166419280 -> '₹16.64 crore'."""
    if rupees is None:
        return "value not given"
    if abs(rupees) >= CRORE:
        return "%s%s crore" % (RUPEE, "{:,.2f}".format(rupees / CRORE))
    return "%s%s lakh" % (RUPEE, "{:,.2f}".format(rupees / LAKH))


def nse_symbol(sym):
    s = str(sym or "").strip().upper()
    return s[:-3] if s.endswith(".NS") else s


_DROP = {"LIMITED", "LTD", "LIMTED"}


def name_key(name):
    """A company name boiled down for matching: 'Mahindra & Mahindra Ltd.' -> 'MAHINDRAANDMAHINDRA'."""
    s = html.unescape(str(name or "")).upper().replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    words = [w for w in s.split() if w not in _DROP]
    if words and words[0] == "THE":
        words = words[1:]
    return "".join(words)


def parse_universe(text):
    """trader/universe.txt (SYMBOL|Name a line) -> [(symbol, name)]."""
    out = []
    for line in text.splitlines():
        if "|" in line:
            sym, name = line.split("|", 1)
            if sym.strip():
                out.append((sym.strip().upper(), name.strip()))
    return out


def parse_equity_list(text):
    """NSE's EQUITY_L.csv -> [(symbol, name)]."""
    rows = [r for r in csv.reader(io.StringIO(text.lstrip("﻿").replace("\r", "")))
            if any(c.strip() for c in r)]
    hdr = [h.strip().upper() for h in rows[0]] if rows else []
    if "SYMBOL" not in hdr or "NAME OF COMPANY" not in hdr:
        raise DealsError("not NSE's EQUITY_L.csv (it starts %r)" % text.strip()[:60])
    si, ni = hdr.index("SYMBOL"), hdr.index("NAME OF COMPANY")
    return [(r[si].strip().upper(), r[ni].strip()) for r in rows[1:] if len(r) > max(si, ni) and r[si].strip()]


class Names(object):
    """Company name -> NSE symbol."""

    def __init__(self, pairs=(), source=None):
        self.source = source
        self.by_key = {}
        self.by_symbol = {}
        for sym, name in pairs:
            self.by_symbol[sym] = name
            self.by_key.setdefault(name_key(name), sym)

    def symbol(self, company):
        return self.by_key.get(name_key(company))

    def __len__(self):
        return len(self.by_symbol)


def load_names(fetcher, universe="trader/universe.txt", cache="trader/state/EQUITY_L.csv", write=True):
    """universe.txt if present, else the cached EQUITY_L.csv, else one download of it."""
    if universe and os.path.isfile(universe):
        with open(universe, encoding="utf-8", errors="replace") as fh:
            return Names(parse_universe(fh.read()), universe)
    if fetcher.offline:
        try:
            return Names(parse_equity_list(fetcher.get("names").decode("utf-8", "replace")), FILES["names"])
        except DealsError:
            return Names((), None)
    if cache and os.path.isfile(cache):
        with open(cache, encoding="utf-8", errors="replace") as fh:
            return Names(parse_equity_list(fh.read()), cache)
    text = fetcher.get("names").decode("utf-8", "replace")
    pairs = parse_equity_list(text)
    if write and cache:
        os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
        with open(cache, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    return Names(pairs, URLS["names"])


def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """{NSE symbol: where it is} from your holdings, the scanner's watchlist and the planned trade."""
    out = {}
    for h in (load_json(watchlist) or {}).get("hits", []):
        sym = nse_symbol(h.get("symbol") or h.get("ticker"))
        if sym:
            out[sym] = "on your watchlist"
    pl = load_json(plan) or {}
    if pl.get("symbol") or pl.get("ticker"):
        out[nse_symbol(pl.get("symbol") or pl.get("ticker"))] = "in your trade plan"
    for p in (load_json(portfolio) or {}).get("positions", []):
        sym = nse_symbol(p.get("ticker") or p.get("symbol"))
        if sym:
            out[sym] = "in your holdings"
    return out


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    """Plain GETs, one at a time, each URL once per run, one retry at most."""

    def __init__(self, offline=None, pause=PAUSE, cache_dir=None, write=True):
        self.offline = offline
        self.pause = pause
        self.cache_dir = cache_dir
        self.write = write
        self._last = 0.0
        self._memo = {}

    def get(self, key):
        return self.get_url(URLS[key], FILES[key])

    def get_url(self, url, name, keep=False):
        """`keep`: a file that never changes (an XBRL filing) is kept in cache_dir and read from there."""
        if name in self._memo:
            return self._memo[name]
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise DealsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                data = fh.read()
        else:
            cached = os.path.join(self.cache_dir, name) if (keep and self.cache_dir) else None
            if cached and os.path.isfile(cached):
                with open(cached, "rb") as fh:
                    data = fh.read()
            else:
                data = self._download(url)
                if cached and self.write:
                    os.makedirs(self.cache_dir, exist_ok=True)
                    with open(cached, "wb") as fh:
                        fh.write(data)
        self._memo[name] = data
        return data

    def _download(self, url):
        if not url.startswith(BASE):
            raise DealsError("not an NSE archive link, not fetched: %s" % url)
        last = "no answer"
        for attempt in (0, 1):
            gap = time.time() - self._last
            wait = self.pause * (3 if attempt else 1)
            if gap < wait:
                time.sleep(wait - gap)
            self._last = time.time()
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=40) as resp:
                    data = resp.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise DealsError("%s is larger than expected, not read" % url)
                return data
            except urllib.error.HTTPError as exc:
                last = "HTTP %s" % exc.code
                if exc.code < 500:
                    break
            except (urllib.error.URLError, OSError) as exc:
                last = "could not reach it (%s)" % getattr(exc, "reason", exc)
        raise DealsError("%s for %s" % (last, url))


# ---------------------------------------------------------------- bulk and block deals

def _num(text):
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_deals(text, kind):
    """bulk.csv / block.csv text -> rows. Lines that are not deals (a 'no records' line) are skipped."""
    rows = [r for r in csv.reader(io.StringIO(text.lstrip("﻿").replace("\r", "")))
            if any(c.strip() for c in r)]
    if not rows:
        return []
    hdr = [re.sub(r"[^a-z]", "", h.lower()) for h in rows[0]]

    def col(*starts):
        return next((i for i, h in enumerate(hdr) if h.startswith(starts)), None)

    idx = {"date": col("date"), "symbol": col("symbol"), "name": col("securityname"),
           "client": col("clientname"), "side": col("buysell"), "qty": col("quantity"),
           "price": col("tradeprice", "price"), "remarks": col("remarks")}
    if None in (idx["date"], idx["symbol"], idx["client"], idx["side"], idx["qty"], idx["price"]):
        raise DealsError("not NSE's %s.csv (it starts %r)" % (kind, text.strip()[:60]))
    out = []
    for r in rows[1:]:
        def cell(k):
            i = idx[k]
            return r[i].strip() if i is not None and i < len(r) else ""
        day, qty, price = parse_date(cell("date")), _num(cell("qty")), _num(cell("price"))
        side = cell("side").upper()
        if not day or qty is None or price is None or side not in ("BUY", "SELL"):
            continue
        value = qty * price
        row = {"kind": kind, "date": day.isoformat(), "symbol": cell("symbol").upper(),
               "name": cell("name"), "client": " ".join(cell("client").split()), "side": side,
               "quantity": int(qty), "price": price, "value": round(value, 2),
               "value_crore": round(to_crore(value), 2),
               "remarks": cell("remarks") if cell("remarks") not in ("", "-") else None}
        row["id"] = "|".join([kind, row["date"], row["symbol"], row["client"], side,
                              str(row["quantity"]), "%.2f" % price])
        out.append(row)
    return out


def group_deals(rows):
    """Rows -> one entry per stock, biggest first, with each client's buys and sells netted."""
    stocks = {}
    for r in rows:
        s = stocks.setdefault((r["kind"], r["symbol"]), {
            "kind": r["kind"], "symbol": r["symbol"], "name": r["name"], "date": r["date"], "clients": {}})
        c = s["clients"].setdefault(r["client"], {
            "client": r["client"], "buy_qty": 0, "buy_value": 0.0, "sell_qty": 0, "sell_value": 0.0})
        key = "buy" if r["side"] == "BUY" else "sell"
        c[key + "_qty"] += r["quantity"]
        c[key + "_value"] += r["value"]
    out = []
    for s in stocks.values():
        clients = []
        for c in s["clients"].values():
            c["buy_price"] = round(c["buy_value"] / c["buy_qty"], 2) if c["buy_qty"] else None
            c["sell_price"] = round(c["sell_value"] / c["sell_qty"], 2) if c["sell_qty"] else None
            c["net_qty"] = c["buy_qty"] - c["sell_qty"]
            c["net_value"] = round(c["buy_value"] - c["sell_value"], 2)
            c["net_value_crore"] = round(to_crore(c["net_value"]), 2)
            c["same_day"] = bool(c["buy_qty"] and c["sell_qty"])
            c["buy_value"], c["sell_value"] = round(c["buy_value"], 2), round(c["sell_value"], 2)
            clients.append(c)
        clients.sort(key=lambda c: -(c["buy_value"] + c["sell_value"]))
        s["clients"] = clients
        s["buy_value"] = round(sum(c["buy_value"] for c in clients), 2)
        s["sell_value"] = round(sum(c["sell_value"] for c in clients), 2)
        s["buy_value_crore"] = round(to_crore(s["buy_value"]), 2)
        s["sell_value_crore"] = round(to_crore(s["sell_value"]), 2)
        s["same_day_clients"] = sum(1 for c in clients if c["same_day"])
        out.append(s)
    out.sort(key=lambda s: (-(s["buy_value"] + s["sell_value"]), s["symbol"]))
    return out


def _qty(n):
    return "{:,}".format(int(n))


def describe_client(c):
    bits = []
    if c["buy_qty"]:
        bits.append("bought %s at %s (%s)" % (_qty(c["buy_qty"]), inr(c["buy_price"]), money(c["buy_value"])))
    if c["sell_qty"]:
        bits.append("sold %s at %s (%s)" % (_qty(c["sell_qty"]), inr(c["sell_price"]), money(c["sell_value"])))
    line = "%s: %s" % (c["client"], ", ".join(bits))
    if c["same_day"]:
        if c["net_qty"] == 0:
            net = "net 0 shares"
        else:
            net = "net %s %s shares" % ("bought" if c["net_qty"] > 0 else "sold", _qty(abs(c["net_qty"])))
        line += ". Bought and sold the same day, %s" % net
    return line


def render_deals(groups, watch=()):
    out = []
    for s in groups:
        flag = "  <- %s is %s" % (s["symbol"], watch[s["symbol"]]) if s["symbol"] in watch else ""
        out.append("  %s  %s%s" % (s["symbol"], s["name"], flag))
        for c in s["clients"]:
            out.append("    - %s" % describe_client(c))
    return out


# ---------------------------------------------------------------- the RSS feeds

def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse_feed(data, kind):
    """An NSE RSS feed -> items, newest first, repeats of the same link folded into one."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise DealsError("the %s feed is not valid XML: %s" % (kind, exc))
    built = None
    for el in root.iter():
        if _local(el.tag) == "lastBuildDate":
            built = (el.text or "").strip() or None
            break
    items, by_id = [], {}
    for el in root.iter():
        if _local(el.tag) != "item":
            continue
        f = {_local(c.tag): " ".join((c.text or "").split()) for c in el}
        link = f.get("link", "")
        when = parse_pubdate(f.get("pubDate", ""))
        desc = html.unescape(f.get("description", ""))
        label, _, value = desc.partition(":")
        ident = link.rsplit("/", 1)[-1] or "%s|%s|%s" % (f.get("title"), f.get("pubDate"), desc)
        if ident in by_id:
            by_id[ident]["repeats"] += 1
            continue
        it = {"kind": kind, "id": ident, "company": html.unescape(f.get("title", "")), "symbol": None,
              "link": link, "description": desc, "published": when.strftime("%Y-%m-%d %H:%M") if when else None,
              "repeats": 1}
        if kind == "sast":
            it["acquirer"] = value.strip() if "ACQUIRER" in label.upper() else desc
        else:
            it["security"] = value.strip() if "SECURITY" in label.upper() else desc
        by_id[ident] = it
        items.append(it)
    items.sort(key=lambda i: i["published"] or "", reverse=True)
    return {"kind": kind, "built": built, "items": items}


# ---------------------------------------------------------------- the insider XBRL

FIELDS = [   # (our name, pattern on the XBRL tag's local name); the first match wins
    ("person", r"^NameOfThePerson"),
    ("category", r"^CategoryOfPerson"),
    ("instrument", r"^TypeOfInstrument"),
    ("before_qty", r"Prior.*NumberOfSecurit"),
    ("before_pct", r"Prior.*Percentage"),
    ("quantity", r"AcquiredOrDisposed.*NumberOfSecurit"),
    ("value", r"AcquiredOrDisposed.*ValueOfSecurit"),
    ("type", r"TransactionType"),
    ("after_qty", r"Post.*NumberOfSecurit"),
    ("after_pct", r"Post.*Percentage"),
    ("from", r"FromDate$"),
    ("to", r"ToDate$"),
    ("intimated", r"DateOfIntimation"),
    ("mode", r"^ModeOf"),
    ("exchange", r"^ExchangeOnWhich"),
    ("value_aggregate", r"^ValueInAggregate"),
    ("form", r"^FormName"),
]
NUMERIC = {"before_qty", "before_pct", "quantity", "value", "after_qty", "after_pct", "value_aggregate"}
HEAD = [("symbol", r"^NSESymbol"), ("company", r"^NameOfTheCompany"), ("isin", r"^ISIN"),
        ("regulation", r"^RegulationOf")]
VERBS = {"buy": "bought", "sell": "sold", "pledge": "pledged", "pledge revoke": "had a pledge released on",
         "pledge invoke": "had a pledge invoked on"}


def plain_kind(row):
    """What happened in plain words, from the type and mode AS FILED."""
    typ, mode = (row.get("type") or "").lower(), (row.get("mode") or "").lower()
    if "esop" in mode or "esos" in mode:
        if "sell" in typ:
            return "shares moved under an employee stock option plan (ESOP), not an ordinary market sale"
        return "an ESOP exercise or allotment, not a market purchase"
    both = typ + " " + mode
    if "revo" in both or "release" in both:
        return "a pledge being released, not a purchase in the market"
    if "invo" in both:
        return "a lender invoking a pledge, not a sale the holder chose"
    if "pledge" in both:
        return "shares pledged as security for a loan, not a sale in the market"
    if "off market" in mode or "off-market" in mode or "inter-se" in mode or "inter se" in mode:
        return "an off-market transfer, not a trade on the exchange"
    if "gift" in mode or "inherit" in mode:
        return "a %s, not a trade on the exchange" % mode
    if ("buy" in typ and "sale" in mode) or ("sell" in typ and "purchase" in mode):
        return ("the filing says type \"%s\" and mode \"%s\", which disagree: read the filing"
                % (row.get("type"), row.get("mode")))
    if "market" in mode:
        return "a trade in the market"
    return None


def parse_xbrl(data, published=None):
    """One insider-trading XBRL file -> {"symbol", "company", "isin", "regulation", "rows": [...]}.

    Tolerant: tags are matched by pattern on their local name, whatever the
    namespace, and grouped by contextRef (one context per transaction row)."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise DealsError("the XBRL file is not valid XML: %s" % exc)
    head = {k: None for k, _ in HEAD}
    contexts, order = {}, []
    for el in root.iter():
        ref = el.get("contextRef")
        if ref is None:
            continue
        name, text = _local(el.tag), " ".join((el.text or "").split())
        hit = next((k for k, rx in HEAD if re.search(rx, name)), None)
        if hit:
            head[hit] = head[hit] or text or None
            continue
        key = next((k for k, rx in FIELDS if re.search(rx, name)), None)
        if not key:
            continue
        if ref not in contexts:
            contexts[ref] = {}
            order.append(ref)
        contexts[ref].setdefault(key, _num(text) if key in NUMERIC else (text or None))
    pub = _dt.date.fromisoformat(published[:10]) if published else None
    rows = []
    for ref in order:
        c = contexts[ref]
        if not (c.get("person") or c.get("type") or c.get("quantity")):
            continue
        row = {k: c.get(k) for k, _ in FIELDS}
        if row["value"] is None:
            row["value"] = row["value_aggregate"]
        for k in ("before_qty", "quantity", "after_qty"):
            if row[k] is not None:
                row[k] = int(row[k])
        row["value_crore"] = round(to_crore(row["value"]), 4) if row["value"] is not None else None
        row["per_share"] = (round(row["value"] / row["quantity"], 2)
                            if row["value"] and row["quantity"] else None)
        row["plain"] = plain_kind(row)
        traded = parse_date(row["to"] or row["from"] or "")
        told = parse_date(row["intimated"] or "")
        row["days_to_tell_company"] = (told - traded).days if traded and told else None
        row["days_to_publish"] = (pub - traded).days if traded and pub else None
        rows.append(row)
    head["symbol"] = (head["symbol"] or "").upper() or None
    if head["symbol"] in ("NA", "NOTLISTED", "NOT LISTED"):
        head["symbol"] = None
    head["rows"] = rows
    return head


def describe_insider(row):
    """One transaction row as two lines of plain words, amounts as filed."""
    verb = VERBS.get((row.get("type") or "").strip().lower(), "%s:" % (row.get("type") or "transaction"))
    what = "%s (%s) %s" % (row.get("person") or "name not given", row.get("category") or "category not given",
                           verb)
    if row.get("quantity") is not None:
        what += " %s" % _qty(row["quantity"])
    what += " %s" % (row.get("instrument") or "securities")
    if row.get("value") is not None:
        what += ", value as filed %s%s (%s)" % (RUPEE, indian(row["value"]), money(row["value"]))
        if row.get("per_share"):
            what += ", which works out to %s each" % inr(row["per_share"])
    what += ". Mode as filed: %s" % (row.get("mode") or "not given")
    if row.get("exchange") and row["exchange"].upper() not in ("NA", "N.A.", "NOT APPLICABLE"):
        what += " on %s" % row["exchange"]
    if row.get("plain"):
        what += " (%s)" % row["plain"]
    what += "."
    lines = [what]
    hold = []
    if row.get("before_qty") is not None and row.get("after_qty") is not None:
        hold.append("Holding %s (%s%%) -> %s (%s%%)." % (
            _qty(row["before_qty"]), _pct(row.get("before_pct")), _qty(row["after_qty"]),
            _pct(row.get("after_pct"))))
    when = row.get("to") or row.get("from")
    if when:
        span = when if row.get("from") in (None, when) else "%s to %s" % (row["from"], when)
        t = "Traded %s" % span
        if row.get("intimated"):
            t += ", told the company %s" % row["intimated"]
        if row.get("days_to_publish") is not None:
            t += ", on the exchange %d day%s after the trade" % (
                row["days_to_publish"], "" if row["days_to_publish"] == 1 else "s")
        hold.append(t + ".")
    if hold:
        lines.append(" ".join(hold))
    return lines


def _pct(x):
    return "?" if x is None else ("%.2f" % x).rstrip("0").rstrip(".") if x else "0"


def add_detail(items, fetcher, errors):
    """Fetch and parse the XBRL behind each insider item (each file once)."""
    for it in items:
        name = it["link"].rsplit("/", 1)[-1]
        if not name.lower().endswith(".xml"):
            it["detail"] = None
            continue
        try:
            d = parse_xbrl(fetcher.get_url(it["link"], name, keep=True), it["published"])
        except DealsError as exc:
            it["detail"] = None
            it["detail_error"] = str(exc)
            errors.append("%s: %s" % (it["company"], exc))
            continue
        it["detail"] = d
        if d.get("symbol"):
            it["symbol"] = d["symbol"]


def render_items(items, watch=()):
    out = []
    for it in items:
        sym = it.get("symbol")
        flag = "  <- %s is %s" % (sym, watch[sym]) if sym in watch else ""
        head = "  %s%s, on the exchange %s IST%s" % (
            it["company"], " (%s)" % sym if sym else " (no NSE symbol matched)",
            it["published"] or "date unknown", flag)
        out.append(head)
        if it["kind"] == "sast":
            out.append("    - Acquirer / seller named in the filing (and persons acting in concert): %s"
                       % (it.get("acquirer") or "not given"))
        else:
            d = it.get("detail")
            if d and d["rows"]:
                for row in d["rows"]:
                    lines = describe_insider(row)
                    out.append("    - %s" % lines[0])
                    out.extend("      %s" % x for x in lines[1:])
            elif d:
                out.append("    - no transaction rows found in the XBRL file: open the link")
            else:
                out.append("    - %s; detail not read%s" % (
                    it.get("description") or "no description",
                    ": %s" % it["detail_error"] if it.get("detail_error") else ""))
        out.append("    %s" % it["link"])
    return out


# ---------------------------------------------------------------- freshness

def freshness(kind, newest, now, count, built=None):
    """How old the newest item of a source is, in plain words."""
    today = to_ist(now).date()
    f = {"source": kind, "newest": newest, "items": count, "age_days": None, "stale": None, "built": built}
    if not newest:
        f["note"] = "%s: no rows in the file." % TITLES[kind].split(" (")[0].capitalize()
        return f
    day = _dt.date.fromisoformat(newest[:10])
    age = (today - day).days
    f["age_days"] = age
    label = TITLES[kind].split(" (")[0].capitalize()
    if kind in ("bulk", "block"):
        want = last_session(now)
        f["expected"] = want.isoformat()
        f["stale"] = day < want
        if day == want:
            note = "dated %s, the last completed NSE session" % newest
        elif day < want:
            note = "dated %s, OLDER than the last completed session (%s): the file has not been updated" % (
                newest, want.isoformat())
        else:
            note = "dated %s" % newest
        if today.year not in CALENDAR_YEARS:
            note += " (NSE holiday list unknown for %d: weekends only)" % today.year
    else:
        f["stale"] = age > STALE_DAYS
        note = "newest item %s IST, %d day%s old" % (newest, age, "" if age == 1 else "s")
        if f["stale"]:
            note += ". STALE: NSE's feed has nothing newer, so recent filings are NOT here"
            if built:
                note += " (the feed file itself was rebuilt %s)" % built
    f["note"] = "%s: %s." % (label, note)
    return f


# ---------------------------------------------------------------- state

def load_state(path):
    st = load_json(path)
    if not isinstance(st, dict):
        st = {}
    st.setdefault("seen", {})
    st.setdefault("last_check", None)
    return st


def save_state(path, state):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def item_date(it):
    return (it.get("published") or it.get("date") or "")[:10]


def new_since(items, state, now, first_days=FIRST_DAYS):
    """(fresh, held_back): items not seen before. On the first run, items older than
    first_days are held back (recorded, never alerted), so a stale feed does not look new."""
    seen = state.get("seen", {})
    fresh = [it for it in items if it["id"] not in seen]
    if state.get("last_check") is not None:
        return fresh, []
    cutoff = (to_ist(now).date() - _dt.timedelta(days=first_days)).isoformat()
    return ([it for it in fresh if item_date(it) >= cutoff],
            [it for it in fresh if item_date(it) < cutoff])


def remember(items, state, now):
    today = to_ist(now).date()
    seen = state.setdefault("seen", {})
    for it in items:
        seen.setdefault(it["id"], today.isoformat())
    cutoff = (today - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    for k in [k for k, v in seen.items() if str(v) < cutoff]:
        del seen[k]
    state["last_check"] = to_ist(now).strftime("%Y-%m-%d %H:%M IST")


# ---------------------------------------------------------------- collecting

def collect(kind, fetcher, names, errors):
    """One source -> (items, feed build date or None). A failed source is an error line, not a crash."""
    try:
        data = fetcher.get(kind)
        if kind in ("bulk", "block"):
            return parse_deals(data.decode("utf-8", "replace"), kind), None
        feed = parse_feed(data, kind)
    except DealsError as exc:
        errors.append("%s: %s" % (TITLES[kind].split(" (")[0].lower(), exc))
        return None, None
    for it in feed["items"]:
        it["symbol"] = names.symbol(it["company"]) if names else None
    return feed["items"], feed["built"]


def pick(items, symbols=None, watch=None, holdings=False, min_crore=None):
    """Filter items. --min-crore keeps BOTH sides of a client's dealing in a stock when either
    side is that large, so a same-day buy and sell is never shown as a one-way deal."""
    big = set()
    if min_crore is not None:
        big = {(it["symbol"], it["client"]) for it in items
               if "client" in it and it["value"] >= min_crore * CRORE}
    out = []
    for it in items:
        sym = it.get("symbol")
        if symbols and sym not in symbols:
            continue
        if holdings and sym not in (watch or {}):
            continue
        if min_crore is not None and "client" in it and (sym, it["client"]) not in big:
            continue
        out.append(it)
    return out


def newest_of(items):
    dates = [it.get("published") or it.get("date") for it in items or [] if it.get("published") or it.get("date")]
    return max(dates) if dates else None


# ---------------------------------------------------------------- CLI

def _parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--symbol", nargs="*", metavar="X", help="only these NSE symbols")
        p.add_argument("--holdings", action="store_true",
                       help="only stocks in your portfolio, watchlist or trade plan")
        p.add_argument("--portfolio", default="trader/portfolio.json")
        p.add_argument("--watchlist", default="trader/watchlist.json")
        p.add_argument("--plan", default="trader/plan.json")
        p.add_argument("--universe", default="trader/universe.txt",
                       help="SYMBOL|Name lines, for matching company names to symbols")
        p.add_argument("--state-dir", default="trader/state",
                       help="where insider.json, the cached EQUITY_L.csv and XBRL files live")
        p.add_argument("--offline", metavar="DIR", help="read saved files from DIR instead of the network")
        p.add_argument("--now", help="pretend it is this India time, e.g. \"2026-11-24 02:30\"")
        p.add_argument("--dry-run", action="store_true", help="write nothing (no state, no cache)")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("bulk", help="last session's bulk and block deals, per stock")
    common(p)
    p.add_argument("--min-crore", type=float, metavar="N", help="only deal rows worth at least N crore rupees")
    p = sub.add_parser("insider", help="insider-trading disclosures, with the detail from each XBRL file")
    common(p)
    p.add_argument("--no-detail", action="store_true", help="list the feed only, do not fetch the XBRL files")
    p = sub.add_parser("sast", help="takeover-code (SAST Regulation 29) disclosures")
    common(p)
    p = sub.add_parser("new", help="everything not seen at the last check, across all sources")
    common(p)
    p.add_argument("--min-crore", type=float, metavar="N", help="only deal rows worth at least N crore rupees")
    p.add_argument("--no-detail", action="store_true", help="do not fetch the XBRL files of new insider items")
    return ap


def main(argv=None):
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = _parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    try:
        return _run(args)
    except (DealsError, OSError) as exc:
        print("deals: %s" % exc, file=sys.stderr)
        return 2


def _run(args):
    now = parse_now(args.now)
    write = not args.dry_run
    fetcher = Fetcher(args.offline, cache_dir=os.path.join(args.state_dir, "insider-xbrl"), write=write)
    watch = watch_from_files(args.portfolio, args.watchlist, args.plan)
    symbols = {nse_symbol(s) for s in (args.symbol or [])} or None
    if args.holdings and not watch:
        raise DealsError("--holdings: no symbols found in %s, %s or %s"
                         % (args.portfolio, args.watchlist, args.plan))
    kinds = {"bulk": ("bulk", "block"), "insider": ("insider",), "sast": ("sast",), "new": SOURCES}[args.cmd]
    errors, names = [], None
    if "insider" in kinds or "sast" in kinds:
        try:
            names = load_names(fetcher, args.universe, os.path.join(args.state_dir, FILES["names"]), write)
        except DealsError as exc:
            errors.append("company names (symbols will be missing): %s" % exc)
    state_path = os.path.join(args.state_dir, "insider.json")
    state = load_state(state_path) if args.cmd == "new" else None
    min_crore = getattr(args, "min_crore", None)

    shown, fresh_notes, everything, held = {}, [], [], 0
    failed = 0
    for kind in kinds:
        items, built = collect(kind, fetcher, names, errors)
        if items is None:
            failed += 1
            continue
        fresh_notes.append(freshness(kind, newest_of(items), now, len(items), built))
        if kind == "insider" and not getattr(args, "no_detail", False) and args.cmd != "new":
            add_detail(items, fetcher, errors)
        everything.extend(items)
        chosen = items
        if state is not None:
            chosen, back = new_since(items, state, now)
            held += len(back)
            if kind == "insider" and not args.no_detail:
                add_detail(chosen, fetcher, errors)
        shown[kind] = pick(chosen, symbols, watch, args.holdings, min_crore)
    if failed == len(kinds):
        for e in errors:
            print("deals: %s" % e, file=sys.stderr)
        return 2

    total = sum(len(v) for v in shown.values())
    last_check = state.get("last_check") if state else None
    if args.json:
        out = {"checked": to_ist(now).strftime("%Y-%m-%d %H:%M IST"), "command": args.cmd,
               "last_check": last_check, "items": shown,
               "deals_by_stock": {k: group_deals(shown[k]) for k in ("bulk", "block") if k in shown},
               "held_back_as_old": held, "freshness": fresh_notes, "errors": errors,
               "watch": watch, "note": PUBLIC_NOTE}
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render(args.cmd, shown, watch, fresh_notes, errors, now, last_check, held))
    if state is not None and write:
        remember(everything, state, now)
        save_state(state_path, state)
    return 0 if total else 1


def render(cmd, shown, watch, fresh_notes, errors, now, last_check=None, held=0):
    out = []
    stamp = to_ist(now).strftime("%Y-%m-%d %H:%M IST")
    total = sum(len(v) for v in shown.values())
    if cmd == "new":
        since = "since the last check (%s)" % last_check if last_check else "(first check)"
        out.append("%d new item%s %s, checked %s." % (total, "" if total == 1 else "s", since, stamp))
        if not total:
            out.append("Nothing new.")
    else:
        out.append("Checked %s." % stamp)
    for kind in SOURCES:
        items = shown.get(kind)
        if not items:
            if kind in shown and cmd != "new":
                out.append("")
                out.append("%s: nothing to show." % TITLES[kind])
            continue
        out.append("")
        if kind in ("bulk", "block"):
            groups = group_deals(items)
            out.append("%s, session %s: %d %srow%s in %d stock%s" % (
                TITLES[kind], items[0]["date"], len(items), "new " if cmd == "new" else "",
                "" if len(items) == 1 else "s", len(groups), "" if len(groups) == 1 else "s"))
            out.extend(render_deals(groups, watch))
        else:
            out.append("%s: %d %sfiling%s" % (TITLES[kind], len(items), "new " if cmd == "new" else "",
                                               "" if len(items) == 1 else "s"))
            out.extend(render_items(items, watch))
            if kind == "sast":
                out.append("  The linked zip / PDF files hold the percentages. This tool does not open them.")
    out.append("")
    out.append("How fresh each source is:")
    for f in fresh_notes:
        out.append("  %s" % f["note"])
    if held:
        out.append("  %d older item%s (more than %d days old) recorded on this first check without an alert."
                   % (held, "" if held == 1 else "s", FIRST_DAYS))
    for e in errors:
        out.append("  COULD NOT READ %s" % e)
    out.append("")
    if any(k in shown for k in ("bulk", "block")):
        out.append(BULK_NOTE)
    out.append(PUBLIC_NOTE)
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.exit(main())
