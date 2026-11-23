#!/usr/bin/env python3
"""Earnings reader for trader-analyst (NSE stocks). Standard library only.

Lays out a company's reported numbers quarter by quarter and says what
changed: revenue, gross / operating / EBITDA / net margins, net income and
diluted EPS, each against the same quarter a year earlier when that quarter
is there, the change on the quarter before, and the last four quarters added
up. Then it reads three NSE feeds to answer "did it just report, and is a
report or a corporate action close?".

    python facts.py TCS
    python facts.py TCS HDFCBANK "M&M" --quarters 6
    python facts.py                      # the symbols in trader/watchlist.json
    python facts.py TCS --json
    python facts.py TCS RELIANCE --metrics trader/metrics.json

Symbols are NSE symbols (RELIANCE, HDFCBANK, M&M, BAJAJ-AUTO); the tool adds
".NS" for Yahoo and strips it again.

Sources (free, no key, plain GET requests, one per file per run):
  - Yahoo Finance's unofficial fundamentals-timeseries endpoint, one request
    per symbol:
    https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/<SYMBOL>.NS
  - NSE's public archive feeds (https://nsearchives.nseindia.com/content/RSS/):
    Financial_Results.xml, Corporate_action.xml, Board_Meetings.xml
  - Company names, to match the feeds (their titles are company names, not
    symbols): trader/universe.txt (SYMBOL|Name lines written by
    `scan.py universe`) when it exists, else NSE's EQUITY_L.csv, downloaded
    once and kept in trader/state/EQUITY_L.csv.

What Yahoo's quarterly data is like for Indian companies (checked 2026-11-24
on ten symbols): about five quarters at most, and it has holes.
  - TCS, RELIANCE, M&M, ITC and ZEEL came back without the quarter ending
    2025-09-30. SBIN and ICICIBANK had revenue for it but no net income or
    EPS; INFY had revenue but no EPS.
  - HDFCBANK had only two quarters, the newest ending 2025-06-30, five
    quarters behind.
  - TATAMOTORS (the symbol from before the 2025 demerger) returned nothing.
  - INFY's figures come in US dollars, not rupees.
  - Banks have no gross profit, operating income or EBITDA lines.
So the tool treats every quarter as "maybe missing":
  - A quarter Yahoo skipped is printed as "no figure from Yahoo for the
    quarter ending ...", never filled in or estimated.
  - Growth against a year earlier is shown only when that exact quarter is
    there. The change on the quarter before is shown only when the quarter
    before is there. The last four quarters are added up only when all four
    are there, back to back. Nothing is computed across a hole.
  - A line Yahoo does not have (a bank's gross margin) is blank, not zero.
  - Yahoo does not say whether a figure is consolidated or standalone, so
    the tool says "not stated".

Amounts print in rupees crore (1 crore = 10,000,000 rupees), EPS in rupees.
When Yahoo's currency for a company is not INR, amounts print in millions of
that currency and the tool says so; nothing is converted.

The NSE feeds are short rolling windows (3, 7 and 0 items when checked), so
"nothing in the feeds" is not "nothing scheduled". The date of the next
results is usually not in any of these sources: the tool prints the SEBI
deadline (45 days after a quarter ends, 60 days after the financial year
ends) and tells you to look at the company's investor relations page or its
board-meeting intimation on the exchange. It never guesses a date.

Times are India Standard Time (a fixed UTC+5:30; never the computer's own
zone). --now "2026-11-24 09:00" is India time. Offline: --offline DIR reads
timeseries-<SYMBOL>.json, Financial_Results.xml, Corporate_action.xml,
Board_Meetings.xml and universe.txt or EQUITY_L.csv from DIR. The tests use
it.

Exit code 0 when every symbol has figures, 1 when some do, 2 when none do or
the arguments are wrong.

Numbers only: this tool never says whether a stock is cheap or a buy, and it
never places an order.
"""

import argparse
import calendar
import csv
import datetime as _dt
import email.utils
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

TIMESERIES = "https://%s.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/%s"
HOSTS = ("query2", "query1")
NSE_BASE = "https://nsearchives.nseindia.com/content/"
FEEDS = (("results", "Financial_Results.xml", "RSS/Financial_Results.xml", "financial results"),
         ("actions", "Corporate_action.xml", "RSS/Corporate_action.xml", "corporate actions"),
         ("board", "Board_Meetings.xml", "RSS/Board_Meetings.xml", "board meetings"))
NSE_LIST = "EQUITY_L.csv"
UA = "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)"
SOURCE = "Yahoo Finance fundamentals-timeseries (unofficial)"
IST_OFFSET = _dt.timedelta(hours=5, minutes=30)
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")
RUPEE = "₹"
CRORE = 10000000.0
LIST_MAX_AGE_DAYS = 30
UNUSUAL_SHARE = 0.05   # unusual items this big against net income are called out as a one-off

# our name -> Yahoo's type name (after "quarterly" / "annual")
LINES = (("revenue", "TotalRevenue"), ("gross_profit", "GrossProfit"),
         ("operating_income", "OperatingIncome"), ("ebitda", "EBITDA"),
         ("net_income", "NetIncome"), ("eps_diluted", "DilutedEPS"),
         ("unusual_items", "TotalUnusualItems"), ("net_interest_income", "NetInterestIncome"))
ANNUAL = ("revenue", "operating_income", "net_income", "eps_diluted")
CORE = ("revenue", "net_income", "eps_diluted")
WORDS = {"revenue": "revenue", "net_income": "net income", "eps_diluted": "diluted EPS",
         "gross_profit": "gross profit", "operating_income": "operating income", "ebitda": "EBITDA"}
CONSOLIDATED_NOTE = ("Yahoo does not state whether these figures are consolidated or standalone: "
                     "check them against the company's results filing.")
WHERE_TO_LOOK = ("the company's investor relations page, or its board-meeting intimation on the "
                 "NSE or BSE corporate filings page")
WINDOW_NOTE = ("These feeds are short rolling windows: nothing in them is not the same as nothing "
               "scheduled.")


class FactsError(Exception):
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
        raise FactsError("--now wants a time like \"2026-11-24 09:00\" (India time) or ...Z (UTC)")
    if d.tzinfo is None:
        return ist_to_utc(d)
    return d.astimezone(_dt.timezone.utc)


def month_end(year, month):
    return _dt.date(year, month, calendar.monthrange(year, month)[1])


def shift_quarters(day, n):
    """The month-end n quarters after (n < 0: before) the month of `day`."""
    k = day.year * 12 + (day.month - 1) + 3 * n
    return month_end(k // 12, k % 12 + 1)


def results_due(end, fy_end_month=3):
    """SEBI's deadline for filing results: 45 days after a quarter ends, 60 after the year ends."""
    return end + _dt.timedelta(days=60 if end.month == fy_end_month else 45)


def fiscal_label(end, fy_end_month=3):
    """2026-09-30 with a March year-end -> 'Q2 FY27'."""
    q = ((end.month - fy_end_month - 1) % 12) // 3 + 1
    fy = end.year if end.month <= fy_end_month else end.year + 1
    return "Q%d FY%02d" % (q, fy % 100)


# ---------------------------------------------------------------- symbols and money

def yahoo_symbol(sym):
    """NSE symbol -> Yahoo symbol: RELIANCE -> RELIANCE.NS. .NS / .BO and ^ index symbols are kept."""
    s = sym.strip().upper()
    return s if s.endswith((".NS", ".BO")) or s.startswith("^") else s + ".NS"


def nse_symbol(sym):
    """What output and files show: TCS.NS -> TCS."""
    s = str(sym or "").strip().upper()
    return s[:-3] if s.endswith(".NS") else s


def timeseries_url(sym, now, host=HOSTS[0]):
    types = ["quarterly" + y for _, y in LINES] + ["annual" + dict(LINES)[k] for k in ANNUAL]
    p2 = int(now.timestamp())
    return (TIMESERIES % (host, urllib.parse.quote(yahoo_symbol(sym), safe=""))
            + "?type=%s&period1=%d&period2=%d" % (",".join(types), p2 - 6 * 366 * 86400, p2))


def crore(v):
    """Rupees -> a crore figure as text: 731880000000 -> '73,188'. None -> '-'."""
    if v is None:
        return "-"
    c = v / CRORE
    return "{:,.0f}".format(c) if abs(c) >= 100 else "{:,.1f}".format(c)


def amount(v, currency="INR"):
    """A table cell: crore for rupees, millions for any other currency."""
    if v is None:
        return "-"
    return crore(v) if currency == "INR" else "{:,.0f}".format(v / 1e6)


def money(v, currency="INR"):
    """A figure in a sentence: '₹73,188 cr', with lakh crore in words from 1 lakh crore up."""
    if v is None:
        return "n/a"
    if currency != "INR":
        return "%s %s million" % (currency, "{:,.0f}".format(v / 1e6))
    s = "%s%s cr" % (RUPEE, crore(abs(v)))
    if abs(v) >= 1e12:
        s += " (%s%.2f lakh crore)" % (RUPEE, abs(v) / 1e12)
    return ("-" if v < 0 else "") + s


def per_share(v, currency="INR"):
    if v is None:
        return "n/a"
    return ("%s%.2f" % (RUPEE, v)) if currency == "INR" else "%s %.2f" % (currency, v)


def pct(a, b):
    """Percent change from b to a. None when either is missing or the base is zero or a loss."""
    if a is None or b is None or b <= 0:
        return None
    return round(100.0 * (a - b) / b, 1)


def margin(part, whole):
    if part is None or not whole or whole < 0:
        return None
    return round(100.0 * part / whole, 1)


def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watchlist_symbols(path="trader/watchlist.json"):
    """The scanner's hits, in order, once each."""
    doc = load_json(path) or {}
    out = []
    for h in doc.get("hits", []) if isinstance(doc, dict) else []:
        s = nse_symbol(h.get("symbol") or h.get("ticker")) if isinstance(h, dict) else nse_symbol(h)
        if s and s not in out:
            out.append(s)
    return out


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    """Plain GETs, at least `pause` seconds apart per host, or files from a folder."""

    def __init__(self, offline=None, pause=0.5):
        self.offline, self.pause, self._last = offline, pause, {}

    def get(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise FactsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        host = urllib.parse.urlsplit(url).netloc.split(".", 1)[-1]
        gap = time.time() - self._last.get(host, 0.0)
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last[host] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise FactsError("HTTP %s for %s" % (exc.code, url))
        except (urllib.error.URLError, OSError) as exc:
            raise FactsError("could not reach %s: %s" % (url, getattr(exc, "reason", exc)))


def fetch_timeseries(fetcher, sym, now):
    name = "timeseries-%s.json" % sym
    try:
        data = fetcher.get(timeseries_url(sym, now, HOSTS[0]), name)
    except FactsError as exc:
        if fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
            raise
        data = fetcher.get(timeseries_url(sym, now, HOSTS[1]), name)   # the one retry
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError:
        raise FactsError("Yahoo's answer for %s is not JSON" % sym)


# ---------------------------------------------------------------- Yahoo's series

def parse_timeseries(doc):
    """Yahoo's answer -> {"quarterly": {line: {end: value}}, "annual": {...}, "currencies": [...]}.

    Only three-month values count as quarters and twelve-month values as years. A missing or
    null entry is simply absent: nothing is ever read as zero."""
    try:
        results = doc["timeseries"]["result"] or []
    except (KeyError, TypeError):
        raise FactsError("Yahoo's answer has no timeseries in it")
    names = {y: k for k, y in LINES}
    out = {"quarterly": {k: {} for k, _ in LINES}, "annual": {k: {} for k in ANNUAL}}
    currencies = []
    for r in results:
        t = ((r.get("meta") or {}).get("type") or [""])[0]
        for prefix, want in (("quarterly", "3M"), ("annual", "12M")):
            key = names.get(t[len(prefix):]) if t.startswith(prefix) else None
            if key is None or key not in out[prefix]:
                continue
            for v in r.get(t) or []:
                if not v or v.get("periodType") != want:
                    continue
                raw = (v.get("reportedValue") or {}).get("raw")
                if raw is None or not v.get("asOfDate"):
                    continue
                out[prefix][key][v["asOfDate"]] = float(raw)
                cur = v.get("currencyCode")
                if cur and cur not in currencies:
                    currencies.append(cur)
    out["currencies"] = currencies
    return out


def _by_month(series):
    """{(year, month): value} so a quarter is found by its month whatever day Yahoo stamps it."""
    out = {}
    for end, v in series.items():
        d = _dt.date.fromisoformat(end)
        out[(d.year, d.month)] = v
    return out


def _at(series_m, day):
    return series_m.get((day.year, day.month))


def analyse(doc, quarters=6, today=None, symbol=None, name=None):
    """Every number in the report, as a dict. `today` (an India date) is used to say how far
    behind Yahoo is; without it that check is skipped."""
    ts = parse_timeseries(doc)
    q = {k: _by_month(v) for k, v in ts["quarterly"].items()}
    months = sorted(set().union(*[set(q[k]) for k in CORE]))
    annual_ends = sorted(set().union(*[set(ts["annual"][k]) for k in ANNUAL]))
    if not months and not annual_ends:
        raise FactsError("Yahoo has no quarterly or annual figures for %s (check the NSE symbol; "
                         "a new listing or a renamed company may have none)" % (symbol or "it"))
    fy_months = [_dt.date.fromisoformat(e).month for e in annual_ends]
    fy_end = max(set(fy_months), key=fy_months.count) if fy_months else 3
    currency = ts["currencies"][0] if ts["currencies"] else "INR"

    rows, holes, partial = [], [], []
    if months:
        newest, oldest = month_end(*months[-1]), month_end(*months[0])
        grid = []
        d = newest
        while d >= oldest:
            grid.append(d)
            d = shift_quarters(d, -1)
        for end in grid[:max(1, quarters)]:
            vals = {k: _at(q[k], end) for k, _ in LINES}
            missing = all(vals[k] is None for k in CORE)
            before, year_ago = shift_quarters(end, -1), shift_quarters(end, -4)
            row = {"end": end.isoformat(), "fiscal": fiscal_label(end, fy_end), "missing": missing}
            row.update(vals)
            if vals["eps_diluted"] is not None:
                row["eps_diluted"] = round(vals["eps_diluted"], 2)
            r = vals["revenue"]
            row.update({
                "gross_margin": margin(vals["gross_profit"], r),
                "operating_margin": margin(vals["operating_income"], r),
                "ebitda_margin": margin(vals["ebitda"], r),
                "net_margin": margin(vals["net_income"], r),
                "revenue_yoy": pct(r, _at(q["revenue"], year_ago)),
                "net_income_yoy": pct(vals["net_income"], _at(q["net_income"], year_ago)),
                "eps_yoy": pct(vals["eps_diluted"], _at(q["eps_diluted"], year_ago)),
                "revenue_qoq": pct(r, _at(q["revenue"], before)),
                "net_income_qoq": pct(vals["net_income"], _at(q["net_income"], before)),
                "year_ago_present": _at(q["revenue"], year_ago) is not None,
                "quarter_before_present": _at(q["revenue"], before) is not None,
            })
            rows.append(row)
            if missing:
                holes.append(end.isoformat())
            else:
                gone = [k for k in CORE if vals[k] is None]
                if gone:
                    partial.append({"end": end.isoformat(), "missing": gone})
        # holes older than the rows shown still matter for growth: list every one in the grid
        all_holes = [d.isoformat() for d in grid if all(_at(q[k], d) is None for k in CORE)]
    else:
        newest, grid, all_holes = None, [], []

    ttm, ttm_missing = {}, {}
    if newest:
        last4 = [shift_quarters(newest, -i) for i in range(4)]
        for k in CORE:
            vals = [_at(q[k], d) for d in last4]
            if all(v is not None for v in vals):
                ttm[k] = round(sum(vals), 2) if k == "eps_diluted" else sum(vals)
            else:
                ttm_missing[k] = [d.isoformat() for d, v in zip(last4, vals) if v is None]
        ttm_period = [last4[-1].isoformat(), last4[0].isoformat()]
    else:
        ttm_period = None

    behind = []
    if newest and today:
        d = shift_quarters(newest, 1)
        while results_due(d, fy_end) < today:
            behind.append(d.isoformat())
            d = shift_quarters(d, 1)

    years = []
    a = ts["annual"]
    for end in annual_ends[-4:]:
        d = _dt.date.fromisoformat(end)
        prev = "%04d-%s" % (d.year - 1, end[5:])
        years.append({
            "end": end, "fiscal": "FY%02d" % (d.year % 100),
            "revenue": a["revenue"].get(end), "operating_income": a["operating_income"].get(end),
            "net_income": a["net_income"].get(end),
            "eps_diluted": None if a["eps_diluted"].get(end) is None else round(a["eps_diluted"][end], 2),
            "operating_margin": margin(a["operating_income"].get(end), a["revenue"].get(end)),
            "net_margin": margin(a["net_income"].get(end), a["revenue"].get(end)),
            "revenue_yoy": pct(a["revenue"].get(end), a["revenue"].get(prev)),
            "net_income_yoy": pct(a["net_income"].get(end), a["net_income"].get(prev)),
        })
    years.reverse()

    lender = bool(months) and not q["operating_income"] and not q["gross_profit"] \
        and any(v > 0 for v in q["net_interest_income"].values())
    return {"symbol": symbol, "name": name, "currency": currency, "currencies": ts["currencies"],
            "consolidated": None, "fiscal_year_end_month": fy_end, "fiscal_year_end_known": bool(fy_months),
            "rows": rows, "holes": all_holes, "partial": partial, "behind": behind,
            "ttm": ttm, "ttm_missing": ttm_missing, "ttm_period": ttm_period,
            "years": years, "lender": lender, "source": SOURCE}


def gap_notes(result):
    """Plain sentences about what Yahoo did not have."""
    out = []
    for end in result["holes"]:
        out.append("no figure from Yahoo for the quarter ending %s" % end)
    for p in result["partial"]:
        out.append("no %s from Yahoo for the quarter ending %s" % (
            " or ".join(WORDS[k] for k in p["missing"]), p["end"]))
    if result["behind"]:
        b = result["behind"]
        newest = result["rows"][0]["end"]
        out.append("Yahoo stops at the quarter ending %s: %d later quarter%s (%s) should have been "
                   "reported by now and %s not here. These figures are old" % (
                       newest, len(b), "" if len(b) == 1 else "s",
                       b[0] if len(b) == 1 else "%s to %s" % (b[0], b[-1]),
                       "is" if len(b) == 1 else "are"))
    return out


def changes(result):
    """Plain sentences: what changed in the newest quarter Yahoo has."""
    rows = [r for r in result["rows"] if not r["missing"]]
    if not rows:
        return []
    cur = rows[0]
    cur_ccy = result["currency"]
    i = result["rows"].index(cur)      # the quarter before, only when it sits right behind and has figures
    prev = result["rows"][i + 1] if len(result["rows"]) > i + 1 and not result["rows"][i + 1]["missing"] else None
    out = []

    def grew(v):
        return " (%+.1f%% vs a year earlier)" % v if v is not None else ""

    line = "Quarter ending %s (%s): " % (cur["end"], cur["fiscal"])
    parts = []
    if cur["revenue"] is not None:
        parts.append("revenue %s%s" % (money(cur["revenue"], cur_ccy), grew(cur["revenue_yoy"])))
    if cur["net_income"] is not None:
        parts.append("net income %s%s" % (money(cur["net_income"], cur_ccy), grew(cur["net_income_yoy"])))
    if cur["eps_diluted"] is not None:
        parts.append("diluted EPS %s%s" % (per_share(cur["eps_diluted"], cur_ccy), grew(cur["eps_yoy"])))
    out.append(line + ", ".join(parts) + ".")
    if not cur["year_ago_present"]:
        out.append("No growth against a year earlier: Yahoo has no figure for the quarter ending %s." % (
            shift_quarters(_dt.date.fromisoformat(cur["end"]), -4).isoformat()))
    for key, label in (("gross_margin", "Gross margin"), ("operating_margin", "Operating margin"),
                       ("ebitda_margin", "EBITDA margin"), ("net_margin", "Net margin")):
        if cur[key] is None:
            continue
        s = "%s %.1f%%" % (label, cur[key])
        if prev and prev[key] is not None:
            s += " (%s vs the quarter before)" % _pts(cur[key] - prev[key])
        out.append(s + ".")
    if cur["revenue_qoq"] is not None:
        out.append("Revenue %s vs the quarter before (many businesses are seasonal: compare with "
                   "a year earlier first)." % _p(cur["revenue_qoq"]))
    elif cur["revenue"] is not None and not cur["quarter_before_present"]:
        out.append("No change on the quarter before: Yahoo has no figure for it.")
    if result["lender"]:
        s = "A bank or lender: Yahoo has no gross profit, operating income or EBITDA for it, so those " \
            "margins are blank, not zero."
        if cur.get("net_interest_income") is not None:
            s += " Net interest income %s." % money(cur["net_interest_income"], cur_ccy)
        out.append(s)
    if abs(cur.get("unusual_items") or 0) >= UNUSUAL_SHARE * abs(cur["net_income"] or 0) > 0:
        out.append("Yahoo lists unusual items of %s in this quarter (a %s to profit before tax): a one-off, "
                   "read the results note before treating the profit as the run rate." % (
                       money(cur["unusual_items"], cur_ccy), "gain" if cur["unusual_items"] > 0 else "charge"))
    if cur["net_income"] is not None and cur["operating_income"] is not None \
            and cur["net_income"] > cur["operating_income"]:
        out.append("Net income is above operating income: it includes gains outside the business "
                   "(other income, investments, tax items). Check the filing before reading it as growth.")
    if cur["eps_yoy"] is not None and cur["net_income_yoy"] is not None \
            and abs(cur["eps_yoy"] - cur["net_income_yoy"]) > 15:
        out.append("EPS and net income moved very differently against a year earlier (%+.1f%% and %+.1f%%): "
                   "the share count changed (a bonus issue, split, buyback or new shares). Compare net "
                   "income, not EPS." % (cur["eps_yoy"], cur["net_income_yoy"]))
    return out


def next_results(result, today):
    """What can honestly be said about the next results: the legal deadline, and where to look."""
    rows = [r for r in result["rows"] if not r["missing"]]
    if not rows:
        return None
    fy_end = result["fiscal_year_end_month"]
    newest = _dt.date.fromisoformat(rows[0]["end"])
    ended = newest
    while shift_quarters(ended, 1) <= today:
        ended = shift_quarters(ended, 1)
    if ended > newest:     # a quarter has ended that Yahoo does not have
        due = results_due(ended, fy_end)
        if due >= today:
            text = ("The quarter ending %s is over and Yahoo has no figures for it yet. SEBI's deadline "
                    "for those results is %s; the company picks the day." % (ended, due))
        else:
            text = ("Results for the quarter ending %s were due by %s (SEBI's deadline), so they are "
                    "probably out, but Yahoo does not have them." % (ended, due))
        quarter, running = ended, False
    else:
        quarter = shift_quarters(newest, 1)
        due = results_due(quarter, fy_end)
        text = ("Yahoo has the quarter ending %s. The next one ends %s; SEBI's deadline for its results "
                "is %s, and the company picks the day." % (newest, quarter, due))
        running = True
    text += " The date is not in these free sources: look at %s." % WHERE_TO_LOOK
    return {"quarter_end": quarter.isoformat(), "deadline": due.isoformat(), "quarter_still_running": running,
            "date_known": False, "text": text}


# ---------------------------------------------------------------- NSE feeds

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
_DMY = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?")


def parse_nse_date(text):
    """'27-Nov-2026' or '03-Nov-2026 10:29:23' (NSE writes India time) -> naive datetime, or None.
    An RFC 822 date with an offset is converted to India time."""
    text = (text or "").strip()
    m = _DMY.search(text)
    if m and m.group(2).lower() in _MONTHS:
        try:
            return _dt.datetime(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)),
                                int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0))
        except ValueError:
            return None
    try:
        d = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    return to_ist(d) if d is not None and d.tzinfo is not None else d


def parse_feed(data):
    """An NSE RSS feed -> items. Titles are company names; descriptions are 'KEY:VALUE |KEY:VALUE'."""
    try:
        root = ET.fromstring(data.strip() if isinstance(data, bytes) else data.strip().encode("utf-8"))
    except ET.ParseError as exc:
        raise FactsError("not valid XML: %s" % exc)
    out = []
    for it in root.iter("item"):
        def text(tag):
            el = it.find(tag)
            return " ".join((el.text or "").split()) if el is not None else ""
        title, desc = text("title"), text("description")
        company, ex_date = title, None
        m = re.match(r"^(.*?)\s+-\s+Ex-Date:\s*(\S+)\s*$", title, re.I)
        if m:
            company = m.group(1).strip()
            ex = parse_nse_date(m.group(2))
            ex_date = ex.date() if ex else None
        fields = {}
        for part in desc.split("|"):
            if ":" in part:
                k, v = part.split(":", 1)
                fields[k.strip().upper()] = v.strip()
        out.append({"company": company, "title": title, "description": desc, "fields": fields,
                    "link": text("link"), "published": parse_nse_date(text("pubDate")), "ex_date": ex_date})
    return out


def norm_name(name):
    """'Mahindra & Mahindra Ltd.' -> 'mahindra and mahindra': how company names are compared."""
    s = (name or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(r"\s+(limited|ltd)$", "", s)
    return s


def parse_names(text):
    """universe.txt (SYMBOL|Name lines) or NSE's EQUITY_L.csv -> {SYMBOL: name}."""
    text = text.lstrip("﻿").replace("\r", "")
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    out = {}
    if first.upper().startswith("SYMBOL,"):
        rows = list(csv.reader(io.StringIO(text)))
        hdr = [h.strip().upper() for h in rows[0]]
        if "NAME OF COMPANY" not in hdr:
            raise FactsError("not NSE's EQUITY_L.csv (no NAME OF COMPANY column)")
        si, ni = hdr.index("SYMBOL"), hdr.index("NAME OF COMPANY")
        for r in rows[1:]:
            if len(r) > max(si, ni) and r[si].strip():
                out[r[si].strip().upper()] = " ".join(r[ni].split())
        return out
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "|" in ln:
            sym, name = ln.split("|", 1)
            out[sym.strip().upper()] = " ".join(name.split())
    return out


def load_names(fetcher, universe="trader/universe.txt", cache="trader/state/EQUITY_L.csv", notes=None):
    """{SYMBOL: company name}. universe.txt first; else EQUITY_L.csv, downloaded once and kept."""
    notes = [] if notes is None else notes
    candidates = []
    if fetcher.offline:
        candidates += [os.path.join(fetcher.offline, "universe.txt"), os.path.join(fetcher.offline, NSE_LIST)]
    else:
        candidates.append(universe)
        if os.path.isfile(cache) and time.time() - os.path.getmtime(cache) < LIST_MAX_AGE_DAYS * 86400:
            candidates.append(cache)
    for path in candidates:
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                return parse_names(fh.read()), path
    if fetcher.offline:
        notes.append("no universe.txt or %s in %s: feed items cannot be matched to symbols" % (
            NSE_LIST, fetcher.offline))
        return {}, None
    try:
        data = fetcher.get(NSE_BASE + "equities/" + NSE_LIST, NSE_LIST)
        names = parse_names(data.decode("utf-8", "replace"))
    except FactsError as exc:
        notes.append("could not get NSE's list of company names (%s): feed items cannot be matched "
                     "to symbols" % exc)
        return {}, None
    try:
        os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
        with open(cache, "wb") as fh:
            fh.write(data)
    except OSError:
        pass
    return names, cache


def read_feeds(fetcher, notes=None):
    """{"results": {...}, "actions": {...}, "board": {...}}, each with its items or its error."""
    notes = [] if notes is None else notes
    out = {}
    for key, fname, path, label in FEEDS:
        feed = {"label": label, "url": NSE_BASE + path, "items": [], "count": None, "newest": None, "error": None}
        try:
            feed["items"] = parse_feed(fetcher.get(NSE_BASE + path, fname))
            feed["count"] = len(feed["items"])
            stamps = [i["published"] for i in feed["items"] if i["published"]]
            feed["newest"] = max(stamps) if stamps else None
        except FactsError as exc:
            feed["error"] = str(exc)
            notes.append("NSE %s feed not read: %s" % (label, exc))
        out[key] = feed
    return out


def _link_symbol(link):
    m = re.search(r"/corporate/([A-Z0-9&\-]+)_\d", link or "")
    return m.group(1) if m else None


def _days(day, today):
    n = (day - today).days
    if n == 0:
        return "TODAY"
    if n > 0:
        return "in %d day%s" % (n, "" if n == 1 else "s")
    return "%d day%s ago" % (-n, "" if n == -1 else "s")


def events_for(symbol, name, feeds, today):
    """Feed items about one company, each with a plain line and whether it lands on `today`."""
    want = norm_name(name) if name else None
    out = []
    for key, _, _, label in FEEDS:
        for it in feeds.get(key, {}).get("items", []):
            if not ((want and norm_name(it["company"]) == want) or _link_symbol(it["link"]) == symbol):
                continue
            f = it["fields"]
            pub = it["published"]
            ev = {"feed": label, "published_ist": pub.strftime("%Y-%m-%d %H:%M") if pub else None,
                  "date": None, "today": False, "description": it["description"]}
            if key == "actions":
                ex = it["ex_date"]
                ev["kind"] = "ex-date"
                ev["date"] = ex.isoformat() if ex else None
                ev["today"] = ex == today
                ev["text"] = "ex-date %s (%s): %s%s" % (
                    ex.isoformat() if ex else "not given", _days(ex, today) if ex else "date unknown",
                    f.get("PURPOSE", it["description"]),
                    ", record date %s" % f["RECORD DATE"] if f.get("RECORD DATE", "-") != "-" else "")
            elif key == "results":
                ev["kind"] = "results filed"
                ev["date"] = pub.date().isoformat() if pub else None
                ev["today"] = bool(pub) and pub.date() == today
                bits = [f.get("RELATING TO"), f.get("AUDITED/UNAUDITED"), f.get("CONSOLIDATED/NON-CONSOLIDATED")]
                ev["text"] = "results filed with NSE %s (%s): %s, period ended %s" % (
                    pub.strftime("%Y-%m-%d %H:%M IST") if pub else "(time not given)",
                    _days(pub.date(), today) if pub else "date unknown",
                    ", ".join(b for b in bits if b) or it["description"], f.get("PERIOD ENDED", "not given"))
            else:
                when = None
                for k, v in f.items():
                    if "MEETING" in k and parse_nse_date(v):
                        when = parse_nse_date(v).date()
                if when is None:
                    m = _DMY.search(it["description"])
                    when = parse_nse_date(m.group(0)).date() if m and parse_nse_date(m.group(0)) else None
                ev["kind"] = "board meeting"
                ev["date"] = when.isoformat() if when else None
                ev["today"] = when == today
                ev["text"] = "board meeting %s: %s" % (
                    "%s (%s)" % (when.isoformat(), _days(when, today)) if when else "(date not found in the item)",
                    it["description"] or it["title"])
            out.append(ev)
    return out


def trade_line(symbol, events, feeds_ok):
    """The line a day trader needs: does a report or an ex-date land on today's session?"""
    if not feeds_ok:
        return "Could not read NSE's feeds, so nothing is known about results or ex-dates near today for %s." % symbol
    hot = [e for e in events if e["today"]]
    if hot:
        return "INSIDE TODAY'S TRADE for %s: %s. A stop does not protect against a gap or a halt." % (
            symbol, "; ".join(e["kind"] for e in hot))
    ahead = [e for e in events if e["date"] and not e["today"] and "ago" not in e["text"]]
    if ahead:
        return ("Nothing for %s lands today in NSE's feeds, but %s ahead: a trade closed today is not "
                "exposed, anything held past it is." % (symbol, " and ".join(
                    sorted(set("%s %s" % (e["kind"], e["date"]) for e in ahead)))))
    return "Nothing for %s in NSE's results, corporate-action or board-meeting feeds." % symbol


# ---------------------------------------------------------------- output

def _p(v):
    return "-" if v is None else "%+.1f%%" % v if abs(v) >= 0.05 else "0.0%"


def _pts(v):
    return "%+.1f points" % v if abs(v) >= 0.05 else "unchanged"


def _pm(v):
    return "-" if v is None else "%.1f%%" % v


def render(company, checked):
    r = company["result"]
    cur = r["currency"]
    sym = r["symbol"]
    unit = ("%s crore except EPS (%s)" % (RUPEE, RUPEE)) if cur == "INR" else \
        "%s millions except EPS (%s); Yahoo reports this company in %s, not rupees, and nothing is converted" % (
            cur, cur, cur)
    out = ["%s (%s). %s. Source: Yahoo Finance (%s), fetched %s." % (
        r["name"] or sym, sym, unit[0].upper() + unit[1:], yahoo_symbol(sym), checked), CONSOLIDATED_NOTE, ""]
    if r["rows"]:
        out.append("%-11s %-8s %9s %8s %7s %7s %7s %7s %9s %8s %8s" % (
            "quarter end", "fiscal", "revenue", "rev yoy", "gross", "oper", "EBITDA", "net",
            "net inc", "EPS dil", "EPS yoy"))
        for row in r["rows"]:
            if row["missing"]:
                out.append("%-11s %-8s   no figure from Yahoo for this quarter" % (row["end"], row["fiscal"]))
                continue
            out.append(("%-11s %-8s %9s %8s %7s %7s %7s %7s %9s %8s %8s" % (
                row["end"], row["fiscal"], amount(row["revenue"], cur), _p(row["revenue_yoy"]),
                _pm(row["gross_margin"]), _pm(row["operating_margin"]), _pm(row["ebitda_margin"]),
                _pm(row["net_margin"]), amount(row["net_income"], cur),
                "-" if row["eps_diluted"] is None else "%.2f" % row["eps_diluted"], _p(row["eps_yoy"]))).rstrip())
        out.append("A dash is a figure Yahoo does not have, or growth that cannot be worked out without "
                   "one. It is never zero.")
        if not r["fiscal_year_end_known"]:
            out.append("Fiscal labels assume a March year-end (Yahoo had no annual figures to tell).")
    else:
        out.append("Yahoo has no quarterly figures for %s, only the yearly ones below." % sym)
    gaps = gap_notes(r)
    if gaps:
        out.append("")
        out.append("Gaps (nothing is estimated or computed across them):")
        out += ["  - %s." % g for g in gaps]
    if r["rows"]:
        out.append("")
        t, per = r["ttm"], r["ttm_period"]
        if t:
            bits = []
            for k in CORE:
                if k in t:
                    bits.append("%s %s" % (WORDS[k], per_share(t[k], cur) if k == "eps_diluted" else money(t[k], cur)))
            out.append("Last 4 quarters (%s to %s): %s." % (per[0][:7], per[1][:7], ", ".join(bits)))
        for k in CORE:
            if k in r["ttm_missing"]:
                out.append("Last 4 quarters of %s: not added up, Yahoo is missing %s." % (
                    WORDS[k], ", ".join(r["ttm_missing"][k])))
    if r["years"]:
        out.append("")
        out.append("%-11s %-8s %9s %8s %7s %7s %9s %8s" % (
            "year end", "fiscal", "revenue", "rev yoy", "oper", "net", "net inc", "EPS dil"))
        for y in r["years"]:
            out.append(("%-11s %-8s %9s %8s %7s %7s %9s %8s" % (
                y["end"], y["fiscal"], amount(y["revenue"], cur), _p(y["revenue_yoy"]),
                _pm(y["operating_margin"]), _pm(y["net_margin"]), amount(y["net_income"], cur),
                "-" if y["eps_diluted"] is None else "%.2f" % y["eps_diluted"])).rstrip())
    ch = changes(r)
    if ch:
        out.append("")
        out.append("What changed:")
        out += ["  - " + line for line in ch]
    if company.get("events") is not None:
        out.append("")
        out.append("Results and actions (NSE feeds):")
        for e in company["events"]:
            out.append("  - %s" % e["text"])
        out.append("  - " + company["trade_line"])
        if company.get("next_results"):
            out.append("  - Next results: " + company["next_results"]["text"])
    return "\n".join(out) + "\n"


def render_feeds(feeds, checked, names_from):
    out = ["NSE feeds read %s:" % checked]
    for key, _, _, label in FEEDS:
        f = feeds[key]
        if f["error"]:
            out.append("  - %s: not read (%s)" % (label, f["error"]))
        else:
            out.append("  - %s: %d item%s%s" % (
                label, f["count"], "" if f["count"] == 1 else "s",
                ", newest %s IST" % f["newest"].strftime("%Y-%m-%d %H:%M") if f["newest"] else ""))
    out.append(WINDOW_NOTE + " Feed titles are company names, matched to symbols with %s." % (
        names_from or "nothing (no list of names)"))
    return "\n".join(out) + "\n"


def metrics_row(result):
    rows = [r for r in result["rows"] if not r["missing"]]
    cur = rows[0] if rows else {}
    t = result["ttm"]
    return {"symbol": result["symbol"], "name": result["name"], "currency": result["currency"],
            "period_end": cur.get("end"), "fiscal": cur.get("fiscal"),
            "revenue": cur.get("revenue"), "net_income": cur.get("net_income"),
            "eps_diluted": cur.get("eps_diluted"),
            "revenue_ttm": t.get("revenue"), "net_income_ttm": t.get("net_income"),
            "eps_ttm": t.get("eps_diluted"),
            "revenue_growth_yoy": cur.get("revenue_yoy"), "net_income_growth_yoy": cur.get("net_income_yoy"),
            "eps_growth_yoy": cur.get("eps_yoy"),
            "gross_margin": cur.get("gross_margin"), "operating_margin": cur.get("operating_margin"),
            "ebitda_margin": cur.get("ebitda_margin"), "net_margin": cur.get("net_margin"),
            "quarters_missing": result["holes"], "quarters_behind": result["behind"],
            "consolidated": "not stated by Yahoo", "source": SOURCE}


def _json_feeds(feeds):
    out = {}
    for key, f in feeds.items():
        out[key] = {"url": f["url"], "count": f["count"], "error": f["error"],
                    "newest_ist": f["newest"].strftime("%Y-%m-%d %H:%M") if f["newest"] else None}
    return out


def main(argv=None):
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("symbols", nargs="*", help="NSE symbols, e.g. TCS HDFCBANK \"M&M\"; default: the watchlist")
    ap.add_argument("--quarters", type=int, default=6, help="quarters to show (Yahoo has about five)")
    ap.add_argument("--no-events", action="store_true", help="skip NSE's results / actions / board-meeting feeds")
    ap.add_argument("--metrics", metavar="FILE", help="merge a row per company into this JSON")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--universe", default="trader/universe.txt", help="SYMBOL|Name lines (from scan.py universe)")
    ap.add_argument("--names-cache", default="trader/state/EQUITY_L.csv",
                    help="where NSE's EQUITY_L.csv is kept when there is no universe file")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help='pretend it is this time ("2026-11-24 09:00" India time, or ISO with Z)')
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        now = parse_now(args.now)
    except FactsError as exc:
        print("facts: %s" % exc, file=sys.stderr)
        return 2
    if args.quarters < 1:
        print("facts: --quarters wants 1 or more", file=sys.stderr)
        return 2
    wanted = []
    for s in (args.symbols or watchlist_symbols(args.watchlist)):
        s = nse_symbol(s)
        if s and s not in wanted:
            wanted.append(s)
    if not wanted:
        print("facts: no symbols. Pass NSE symbols (facts.py TCS \"M&M\"), or run /trader-scanner first "
              "(it writes trader/watchlist.json).", file=sys.stderr)
        return 2
    today = to_ist(now).date()
    checked = to_ist(now).strftime("%Y-%m-%d %H:%M IST")
    fetcher = Fetcher(offline=args.offline)
    notes = []
    names, names_from, feeds = {}, None, None
    if not args.no_events:
        names, names_from = load_names(fetcher, args.universe, args.names_cache, notes)
        feeds = read_feeds(fetcher, notes)
    else:
        for path in ([os.path.join(args.offline, "universe.txt")] if args.offline else [args.universe]):
            if os.path.isfile(path):
                with open(path, encoding="utf-8", errors="replace") as fh:
                    names = parse_names(fh.read())
    feeds_ok = bool(feeds) and any(f["error"] is None for f in feeds.values())

    companies, failed = [], 0
    for sym in wanted:
        if not SYMBOL.match(sym):
            print("facts: %s is not an NSE symbol like RELIANCE, M&M or BAJAJ-AUTO: skipped" % sym, file=sys.stderr)
            failed += 1
            continue
        try:
            result = analyse(fetch_timeseries(fetcher, sym, now), quarters=args.quarters, today=today,
                             symbol=sym, name=names.get(sym))
        except FactsError as exc:
            print("facts: %s: %s" % (sym, exc), file=sys.stderr)
            failed += 1
            continue
        company = {"symbol": sym, "result": result, "gaps": gap_notes(result), "changes": changes(result)}
        if feeds is not None:
            if names and sym not in names:
                notes.append("%s is not in the list of NSE company names, so feed items cannot be matched "
                             "to it by name" % sym)
            company["events"] = events_for(sym, names.get(sym), feeds, today)
            company["trade_line"] = trade_line(sym, company["events"], feeds_ok)
            company["next_results"] = next_results(result, today)
        companies.append(company)
        if not args.json:
            sys.stdout.write(render(company, checked))
            sys.stdout.write("\n")

    if args.json:
        print(json.dumps({"checked_ist": checked[:16], "companies": companies,
                          "feeds": _json_feeds(feeds) if feeds is not None else None,
                          "names_from": names_from, "notes": notes,
                          "consolidated_note": CONSOLIDATED_NOTE, "window_note": WINDOW_NOTE},
                         indent=2, ensure_ascii=False))
    else:
        if feeds is not None:
            sys.stdout.write(render_feeds(feeds, checked, names_from))
        for n in notes:
            print("note: %s." % n)
        print("Figures are what Yahoo carries, not the filing itself: confirm anything you act on against "
              "the company's results. Numbers only, no view on the stock. Not financial advice.")
    if args.metrics and companies:
        existing = {}
        old = load_json(args.metrics)
        if isinstance(old, dict):
            existing = {r.get("symbol"): r for r in old.get("companies", []) if r.get("symbol")}
        for c in companies:
            existing[c["symbol"]] = metrics_row(c["result"])
        folder = os.path.dirname(args.metrics)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.metrics, "w", encoding="utf-8") as fh:
            json.dump({"as_of": today.isoformat(),
                       "units": "amounts in whole units of the row's currency (1 crore = 10,000,000); "
                                "EPS per share; growth and margins in percent; null = Yahoo had no figure",
                       "companies": sorted(existing.values(), key=lambda r: r["symbol"])},
                      fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        if not args.json:
            print("wrote %s (%d companies)" % (args.metrics, len(existing)))
    return 0 if companies and not failed else (1 if companies else 2)


if __name__ == "__main__":
    sys.exit(main())
