#!/usr/bin/env python3
"""Market Health Check for trader-market (India, NSE): what kind of day the last
session was, and the market's condition going into the next one.
Standard library only. (python3 on macOS/Linux.)

    python market.py check                       # the full check -> trader/market.json
    python market.py check --no-trend            # skip the slower 20-day part (20 bhavcopies)
    python market.py check --date 2026-11-20     # an earlier session
    python market.py check --set breadth_support_pct=60 --set move_pct=4
    python market.py check --offline DIR --now "2026-11-24 10:00"   # saved files, no network
    python market.py show                        # print the saved trader/market.json again
    python market.py rules                       # the reading's rules and thresholds

check   Reads, for the latest COMPLETED NSE session (or --date):

        breadth      NSE's full bhavcopy sec_bhavdata_full_DDMMYYYY.csv, series
                     EQ only. A stock advanced when CLOSE_PRICE is above
                     PREV_CLOSE (both from the same row). Advances, declines,
                     unchanged, the advance/decline ratio, the share up or down
                     --move-pct (3 %) or more, the median change. The same again
                     for the Nifty 500 (ind_nifty500list.csv), and an industry
                     table from that list's Industry column: median change and
                     share advancing, strongest and weakest first.
        highs/lows   CM_52_wk_High_low_DDMMYYYY.csv holds each stock's 52-week
                     high and low as they stood BEFORE that session. A new high
                     is a day's HIGH_PRICE above that high, a new low a day's
                     LOW_PRICE under that low.
        trend        The share of Nifty 500 stocks that closed above the average
                     of their own last 20 closes. Needs the bhavcopies of the
                     last 20 sessions: each dated file is fetched once, at least
                     0.5 s apart, and kept in trader/cache/, so a second run
                     makes no request for it. Weekends and the NSE holidays are
                     skipped; a missing file is tolerated and named.
                     --no-trend skips all this.
        index        Yahoo daily bars (the v8 chart endpoint, 1 year): Nifty 50
                     (^NSEI) close, change, and distance from its 20-day and
                     50-day average close; India VIX (^INDIAVIX) level, change,
                     and where it sits in its own last-year range.
        reading      One rule-based line: SUPPORTIVE, MIXED or WEAK for long
                     breakouts. Each signal votes +1, 0 or -1 by a printed rule
                     with a printed threshold; the votes are added. A signal
                     whose source is missing is left out and named, and with
                     fewer than min_signals (3) there is no reading at all.

        The reading describes the session that has ended, by stated rules. It
        is not advice and not a prediction. Thresholds: the "market" block of
        trader/config.json, or --set NAME=VALUE (see `rules`).

        Splits and bonuses: the bhavcopy's prices are not adjusted, and its
        PREV_CLOSE is the plain close of the session before, so on the day a
        stock goes ex-split it looks like a crash. A close more than
        split_drop_pct (25 %) under the close before it is therefore treated
        as a likely split or bonus: that stock is left out of the day's
        breadth, and out of the 20-day part when the drop is inside the
        window, and it is counted and named. This is a guess by size, not a
        check against NSE's corporate actions; a smaller adjustment (a 1:4
        bonus is 20 %) is not caught, and a real fall of more than 25 % is
        left out too.

Sources are plain GETs of openly published files: nsearchives.nseindia.com
(never the website's API, no cookies) and Yahoo's unofficial chart endpoint
(free, NSE data on it is delayed). One request a file, no retry on NSE's host;
Yahoo is asked on query2, then query1. Today's undated answers (the Nifty 500
list, the two Yahoo charts) are reused for the rest of the India day.
--refresh ignores the cache. --offline DIR reads the files from DIR instead
(the NSE files under their own names, yahoo-NSEI.json, yahoo-INDIAVIX.json).

Times are India Standard Time (IST, a fixed UTC+5:30, never the computer's own
time zone). --now pins the clock ("2026-11-24 10:00", IST; a trailing " IST"
is fine; ...Z for UTC). The session is NSE's regular one, 09:15 to 15:30 IST;
the pre-open and the Muhurat special session are ignored (Muhurat day counts
as closed). NSE publishes the bhavcopy in the evening: before that, the latest
session's file answers 404 and the tool says so.

Exit code 0 when at least one part could be read, 1 when none could.
"""

import argparse
import csv
import datetime
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)"
YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
NSE_BASE = "https://nsearchives.nseindia.com/"
BHAV_DIR, BHAV_NAME = "products/content/", "sec_bhavdata_full_%s.csv"   # %s = DDMMYYYY
HILO_DIR, HILO_NAME = "content/", "CM_52_wk_High_low_%s.csv"
N500_DIR, N500_NAME = "content/indices/", "ind_nifty500list.csv"
NIFTY, VIX = "^NSEI", "^INDIAVIX"
YAHOO_FILE = {NIFTY: "yahoo-NSEI.json", VIX: "yahoo-INDIAVIX.json"}
INDEX_FILE = "market_index.json"   # in trader/cache/: what was fetched when
NSE_PAUSE = 0.5      # seconds between two requests to the same host, at least
BACKOFF = 5.0        # seconds before the one more round after a Yahoo 429
SLEEP = time.sleep   # tests swap this out
KEEP_SERIES = ("EQ",)
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&-]{0,19}$")
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
DEFAULT_OUT = "trader/market.json"
DEFAULT_CACHE = "trader/cache"
NOTE = ("The reading describes the session that has ended, by the rules printed beside it. It is not advice "
        "and not a prediction: a supportive backdrop does not make any one breakout work.")

# Every threshold of the check. Change one in trader/config.json ("market": {...}) or with --set.
THRESHOLDS = {
    "move_pct": 3.0,              # "big move": a stock up or down this much or more on the day
    "trend_sessions": 20,         # the average close of this many sessions
    "trend_min_sessions": 15,     # with fewer bhavcopies than this the trend part is left out
    "split_drop_pct": 25.0,       # a close this far under the close before it: a likely split or bonus
    "industry_min_stocks": 5,     # smaller industries are not printed (they are in the JSON)
    "breadth_support_pct": 55.0,  # share of Nifty 500 stocks up: supportive at or above
    "breadth_weak_pct": 45.0,     # ... weak at or under
    "trend_support_pct": 60.0,    # share above the 20-day average: supportive at or above
    "trend_weak_pct": 40.0,       # ... weak at or under
    "hl_ratio": 2.0,              # new highs at least this many times new lows (or the reverse)
    "hl_min_count": 20,           # new highs + new lows under this: too few to vote
    "vix_calm_pctile": 50.0,      # VIX at or under this percentile of its last year: calm
    "vix_stress_pctile": 80.0,    # ... at or above: stressed
    "vix_jump_pct": 10.0,         # VIX up this much or more on the day: stressed
    "supportive_score": 2,        # the votes add up to this or more
    "weak_score": -2,             # ... to this or less
    "min_signals": 3,             # fewer signals than this: no reading
}

# NSE trading holidays, capital market segment (NSE circular of 12 Dec 2025).
# Muhurat trading (Diwali, a one-hour special session) is ignored: that day counts as closed.
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
CALENDAR_YEARS = (2026,)   # the years the holiday list above covers
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
IST = datetime.timedelta(hours=5, minutes=30)   # fixed, no daylight saving


class MarketError(Exception):
    pass


# ---------------------------------------------------------------- time

def to_ist(utc):
    return utc.replace(tzinfo=None) + IST


def ist_to_utc(local):
    return local.replace(tzinfo=None) - IST


def ts_to_ist(ts):
    """Unix seconds -> naive India time. Never uses the machine's time zone."""
    return to_ist(datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(ts)))


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as India time (IST)."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    t = t.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise MarketError('--now wants a time like "2026-11-24 10:00" (IST) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def trading_day(d):
    """(is_trading_day, reason). Weekends and the NSE holiday list."""
    if d.weekday() >= 5:
        return False, "weekend"
    name = NSE_HOLIDAYS.get(d.isoformat())
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def calendar_note(d):
    """A line to print when the holiday list does not cover the year of `d`, else ''."""
    if d.year in CALENDAR_YEARS:
        return ""
    return ("Note: the NSE holiday list in this tool covers %s only; for %d it knows weekends "
            "only, so check the NSE holiday list yourself." % (", ".join(str(y) for y in CALENDAR_YEARS), d.year))


def prev_trading_day(d):
    d -= datetime.timedelta(days=1)
    while not trading_day(d)[0]:
        d -= datetime.timedelta(days=1)
    return d


def latest_session(now_utc):
    """The date of the latest COMPLETED NSE session at this clock: today from 15:30 IST
    on a trading day, else the trading day before."""
    now = to_ist(now_utc)
    if trading_day(now.date())[0] and now.time() >= CLOSE:
        return now.date()
    return prev_trading_day(now.date())


def sessions_back(day, n):
    """The n trading days that end with `day`, newest first."""
    out = [day]
    while len(out) < n:
        out.append(prev_trading_day(out[-1]))
    return out


def day_label(d):
    return "%s %s" % (d.strftime("%a"), d.isoformat())


def stamp(d):
    return d.strftime("%d%m%Y")


# ---------------------------------------------------------------- network

def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def polite(net, host, pause):
    """Wait before the second and later requests of this run to the same host."""
    if net.get(host):
        SLEEP(max(pause or 0, NSE_PAUSE))
    net[host] = net.get(host, 0) + 1


def yahoo_symbol(sym):
    """NSE symbol -> Yahoo symbol: RELIANCE -> RELIANCE.NS (an index like ^NSEI stays)."""
    s = sym.strip().upper()
    return s if s.endswith((".NS", ".BO")) or s.startswith("^") else s + ".NS"


def chart_path(sym, rng="1y", interval="1d"):
    return "/v8/finance/chart/%s?range=%s&interval=%s" % (
        urllib.parse.quote(yahoo_symbol(sym), safe=""), rng, interval)   # ^NSEI -> %5ENSEI


def yahoo_get(path, net=None, pause=NSE_PAUSE):
    """GET a Yahoo Finance path. query2 first, query1 on a 429 or 5xx, and one more round
    after BACKOFF seconds if a 429 was seen. Raises MarketError with the reason."""
    net = net if net is not None else {}
    last = "no answer"
    for attempt in (0, 1):
        saw_429 = False
        for host in YAHOO_HOSTS:
            polite(net, host, pause)
            try:
                return http_get(host + path, timeout=20)
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
                code = e.code
                e.close()
                if code == 429:
                    saw_429 = True
                    continue
                if code >= 500:
                    continue
                raise MarketError(last)
            except (urllib.error.URLError, OSError) as e:
                last = str(getattr(e, "reason", e))
                continue
        if attempt == 0 and saw_429:
            SLEEP(BACKOFF)
            continue
        break
    raise MarketError(last)


# ---------------------------------------------------------------- parsing NSE's files

def _num(text):
    """'  8230.00' -> 8230.0; '-' or '' -> None."""
    t = (text or "").strip().replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


def nse_date(text):
    """'23-Nov-2026' or '25-NOV-2026' -> date, or None."""
    m = re.match(r"^\s*(\d{1,2})-([A-Za-z]{3})-(\d{4})\s*$", text or "")
    if not m or m.group(2).upper() not in MONTHS:
        return None
    try:
        return datetime.date(int(m.group(3)), MONTHS.index(m.group(2).upper()) + 1, int(m.group(1)))
    except ValueError:
        return None


def _csv_rows(text):
    return [[c.strip() for c in r] for r in csv.reader(io.StringIO(text.lstrip("﻿").replace("\r", "")))
            if any(c.strip() for c in r)]


def parse_bhavcopy(text):
    """NSE's full bhavcopy -> (session date, {SYM: {"close", "prev_close", "high", "low"}}),
    series EQ rows only. A row without a close above zero is dropped; a '-' is None."""
    rows = _csv_rows(text)
    hdr = [h.upper() for h in rows[0]] if rows else []
    need = ("SYMBOL", "SERIES", "DATE1", "PREV_CLOSE", "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE")
    if not set(need) <= set(hdr):
        raise MarketError("not NSE's full bhavcopy (it starts %r)" % text.strip()[:60])
    ix = {k: hdr.index(k) for k in need}
    day, out = None, {}
    for r in rows[1:]:
        if len(r) < len(hdr) or r[ix["SERIES"]].upper() not in KEEP_SERIES:
            continue
        close = _num(r[ix["CLOSE_PRICE"]])
        if close is None or close <= 0:
            continue
        day = day or nse_date(r[ix["DATE1"]])
        out[r[ix["SYMBOL"]].upper()] = {"close": close, "prev_close": _num(r[ix["PREV_CLOSE"]]),
                                        "high": _num(r[ix["HIGH_PRICE"]]), "low": _num(r[ix["LOW_PRICE"]])}
    if day is None or not out:
        raise MarketError("NSE's bhavcopy has no series EQ rows")
    return day, out


def bhavcopy_for(day):
    """A parser that also checks the bhavcopy is the one of `day`."""
    def parse(text):
        got = parse_bhavcopy(text)
        if got[0] != day:
            raise MarketError("that bhavcopy is for %s, not %s" % (got[0].isoformat(), day.isoformat()))
        return got
    return parse


def parse_hilo(text):
    """CM_52_wk_High_low_DDMMYYYY.csv -> (the 'Effective for' date or None,
    {SYM: {"high", "low"}}), series EQ. The file opens with a disclaimer and an
    'Effective for' line before the header; a '-' is None."""
    rows = _csv_rows(text)
    at = [k for k, r in enumerate(rows) if r and r[0].upper() == "SYMBOL"]
    hdr = [h.upper() for h in rows[at[0]]] if at else []
    if not {"SYMBOL", "SERIES", "ADJUSTED_52_WEEK_HIGH", "ADJUSTED_52_WEEK_LOW"} <= set(hdr):
        raise MarketError("not NSE's 52-week high/low file (it starts %r)" % text.strip()[:60])
    eff = None
    for r in rows[:at[0]]:
        m = re.match(r"(?i)^effective for\s+(\S+)", r[0])
        if m:
            eff = nse_date(m.group(1))
    i_s, i_h, i_l = hdr.index("SERIES"), hdr.index("ADJUSTED_52_WEEK_HIGH"), hdr.index("ADJUSTED_52_WEEK_LOW")
    out = {}
    for r in rows[at[0] + 1:]:
        if len(r) > max(i_s, i_h, i_l) and r[i_s].upper() in KEEP_SERIES:
            hi, lo = _num(r[i_h]), _num(r[i_l])
            if hi or lo:
                out[r[0].upper()] = {"high": hi or None, "low": lo or None}
    if not out:
        raise MarketError("NSE's 52-week high/low file has no series EQ rows")
    return eff, out


def hilo_for(day):
    def parse(text):
        got = parse_hilo(text)
        if got[0] is not None and got[0] != day:
            raise MarketError("that 52-week file is for %s, not %s" % (got[0].isoformat(), day.isoformat()))
        return got
    return parse


def parse_index_list(text):
    """ind_nifty500list.csv -> ({SYM: industry}, the number of rows in the list).
    Series EQ rows with a real ISIN only (the list also carries a few REITs and BE rows)."""
    rows = _csv_rows(text)
    hdr = [h.upper() for h in rows[0]] if rows else []
    if not {"COMPANY NAME", "INDUSTRY", "SYMBOL", "SERIES"} <= set(hdr):
        raise MarketError("not an NSE index constituents list (it starts %r)" % text.strip()[:60])
    out = {}
    for r in rows[1:]:
        row = dict(zip(hdr, r))
        sym = (row.get("SYMBOL") or "").upper()
        if (row.get("SERIES") or "").upper() not in KEEP_SERIES or not SYMBOL.match(sym):
            continue
        if row.get("ISIN CODE") and not row["ISIN CODE"].upper().startswith("IN"):
            continue   # a placeholder row
        out[sym] = " ".join((row.get("INDUSTRY") or "").split())
    if not out:
        raise MarketError("the index list has no series EQ rows")
    return out, len(rows) - 1


# ---------------------------------------------------------------- parsing Yahoo

def parse_daily(text, sday):
    """A v8 chart answer (1-day bars) -> ([(date, close)] oldest first, up to `sday`,
    where the close of `sday` came from: "yahoo", "last_price", or None when `sday` has
    no bar). Null closes are dropped. Yahoo often leaves the close of the latest NSE
    session null for hours: then, when the answer's meta carries a last price stamped at
    or after 15:25 IST on that day, that price stands in and is labelled last_price."""
    try:
        doc = json.loads(text) if isinstance(text, str) else text
        res = doc["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise MarketError("not a Yahoo chart answer")
    if not res:
        err = (doc.get("chart") or {}).get("error") or {}
        raise MarketError("Yahoo: %s" % (err.get("description") or "no data"))
    r = res[0]
    stamps = r.get("timestamp") or []
    try:
        closes = r["indicators"]["quote"][0].get("close") or []
    except (KeyError, IndexError, TypeError):
        closes = []
    out, nulls = {}, set()
    for ts, c in zip(stamps, closes):
        d = ts_to_ist(ts).date()
        if d > sday:
            continue
        if c is None:
            nulls.add(d)
            continue
        out[d] = float(c)
    src = "yahoo" if sday in out else None
    meta = r.get("meta") or {}
    mt, px = meta.get("regularMarketTime"), meta.get("regularMarketPrice")
    if src is None and mt and px is not None:
        at = ts_to_ist(mt)
        if at.date() == sday and at.time() >= datetime.time(15, 25):
            out[sday] = float(px)
            src = "last_price"
    return sorted(out.items()), src


# ---------------------------------------------------------------- the cache

def _slash(p):
    return p.replace("\\", "/")


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


class Store(object):
    """Where the files come from: the --offline folder (nothing else then), else the
    copy kept in the cache folder, else one GET. `today` is the India date of the clock:
    an undated file fetched on another day is fetched again."""

    def __init__(self, cache_dir, today, offline=None, refresh=False, pause=NSE_PAUSE, save=True):
        self.cache_dir, self.today, self.offline = cache_dir, today, offline
        self.refresh, self.pause, self.save = refresh, pause, save
        self.net, self.requests, self.fetched = {}, 0, {}
        idx = read_json(os.path.join(cache_dir, INDEX_FILE), {}) if cache_dir and not offline else {}
        self.index = idx if isinstance(idx, dict) else {}
        for k in ("files", "missing"):
            if not isinstance(self.index.get(k), dict):
                self.index[k] = {}

    def _read(self, path, parse):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return parse(fh.read()), ""
        except (OSError, MarketError) as e:
            return None, str(e)

    def _keep(self, name, text, tag):
        if not (self.cache_dir and self.save):
            return
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(os.path.join(self.cache_dir, name), "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            self.index["files"][name] = {"fetched": self.today.isoformat(), "tag": tag}
            self.index["missing"].pop(name, None)
        except OSError:
            pass

    def flush(self):
        if self.cache_dir and self.save and not self.offline:
            try:
                write_json(os.path.join(self.cache_dir, INDEX_FILE), self.index)
            except OSError:
                pass

    def get(self, name, fetch, parse, dated, tag="", usable=None, remember_miss=False):
        """-> (parsed, where) or (None, why). `fetch()` returns the text or raises.
        dated: the file never changes once published (a dated NSE file), so a saved copy
        is good for ever; otherwise a saved copy is good for the India day it was fetched
        on and the same `tag`. usable(parsed) can refuse a saved copy (a Yahoo answer
        whose latest close was still empty). remember_miss: a failure today is not asked
        again today (an old session's file that is simply not there)."""
        if self.offline:
            p = os.path.join(self.offline, name)
            if not os.path.exists(p):
                return None, "no %s in the --offline folder" % name
            got, why = self._read(p, parse)
            return (got, "the --offline folder") if got is not None else (None, why)
        cached = os.path.join(self.cache_dir, name) if self.cache_dir else None
        if cached and not self.refresh:
            ent = self.index["files"].get(name) or {}
            fresh = dated or (ent.get("fetched") == self.today.isoformat() and ent.get("tag", "") == tag)
            if fresh and os.path.exists(cached):
                got, _ = self._read(cached, parse)
                if got is not None and (usable is None or usable(got)):
                    self.fetched[name] = ent.get("fetched")
                    return got, "the copy saved in %s" % _slash(self.cache_dir)
            miss = self.index["missing"].get(name) or {}
            if remember_miss and miss.get("checked") == self.today.isoformat():
                return None, "%s (asked earlier today, not asked again)" % miss.get("why", "not there")
        self.requests += 1
        try:
            text = fetch()
            got = parse(text)
        except urllib.error.HTTPError as e:
            why = "HTTP %d" % e.code
            e.close()
            got = None
        except (urllib.error.URLError, OSError) as e:
            why, got = str(getattr(e, "reason", e)), None
        except MarketError as e:
            why, got = str(e), None
        if got is None:
            if remember_miss and self.cache_dir and self.save:
                self.index["missing"][name] = {"checked": self.today.isoformat(), "why": why}
            return None, why
        self._keep(name, text, tag)
        self.fetched[name] = self.today.isoformat()
        return got, "downloaded"

    def nse(self, subdir, name, parse, dated=True, remember_miss=False):
        def fetch():
            polite(self.net, NSE_BASE, self.pause)
            return http_get(NSE_BASE + subdir + name)
        return self.get(name, fetch, parse, dated, remember_miss=remember_miss)

    def yahoo(self, sym, sday):
        def fetch():
            return yahoo_get(chart_path(sym), self.net, self.pause)
        return self.get(YAHOO_FILE[sym], fetch, lambda t: parse_daily(t, sday), False,
                        usable=lambda got: got[1] == "yahoo")

    def prune(self, oldest):
        """Drop this tool's own dated NSE files of sessions before `oldest`: the check
        no longer needs them. Newer ones stay, whatever this run read."""
        if not (self.cache_dir and self.save) or self.offline:
            return
        rx = re.compile(r"^(?:sec_bhavdata_full|CM_52_wk_High_low)_(\d{2})(\d{2})(\d{4})\.csv$")

        def old(f):
            m = rx.match(f)
            try:
                return bool(m) and datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1))) < oldest
            except ValueError:
                return False
        try:
            for f in os.listdir(self.cache_dir):
                if old(f):
                    os.remove(os.path.join(self.cache_dir, f))
                    self.index["files"].pop(f, None)
        except OSError:
            pass
        for f in list(self.index["missing"]):
            if old(f):
                self.index["missing"].pop(f, None)


# ---------------------------------------------------------------- the metrics

def median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def pct(a, b):
    return 100.0 * a / b if b else None


def change_pct(row):
    """The day's change of one bhavcopy row in percent, or None without a previous close."""
    pc = row.get("prev_close")
    if pc is None or pc <= 0:
        return None
    return (row["close"] / pc - 1.0) * 100.0


def breadth(rows, symbols=None, move_pct=3.0, split_drop_pct=25.0):
    """Advances and declines of one session. rows = {SYM: bhavcopy row}; symbols limits
    the count to a list (the Nifty 500). A stock without a previous close is not counted.
    A close more than split_drop_pct under PREV_CLOSE is a likely split or bonus (the
    file's PREV_CLOSE is not adjusted): left out and named. Returns None when nothing
    could be counted."""
    adv = dec = unch = up = down = 0
    chg, absent, split = [], 0, []
    for sym in (sorted(rows) if symbols is None else sorted(symbols)):
        row = rows.get(sym)
        c = change_pct(row) if row else None
        if c is None:
            absent += 1
            continue
        if c < -split_drop_pct:
            split.append(sym)
            continue
        chg.append(c)
        if row["close"] > row["prev_close"]:
            adv += 1
        elif row["close"] < row["prev_close"]:
            dec += 1
        else:
            unch += 1
        if c >= move_pct:
            up += 1
        elif c <= -move_pct:
            down += 1
    n = len(chg)
    if not n:
        return None
    return {"stocks": n, "advances": adv, "declines": dec, "unchanged": unch,
            "ad_ratio": round(adv / float(dec), 2) if dec else None,
            "adv_pct": round(pct(adv, n), 1), "dec_pct": round(pct(dec, n), 1),
            "move_pct": move_pct, "up_big": up, "down_big": down,
            "up_big_pct": round(pct(up, n), 1), "down_big_pct": round(pct(down, n), 1),
            "median_change_pct": round(median(chg), 2), "not_counted": absent, "likely_splits": split}


def industry_table(rows, industry, split_drop_pct=25.0):
    """[{"industry", "stocks", "median_change_pct", "adv_pct"}], the strongest first
    (by median change, then by share advancing). industry = {SYM: industry}."""
    groups = {}
    for sym, ind in industry.items():
        c = change_pct(rows[sym]) if sym in rows else None
        if c is not None and c >= -split_drop_pct and ind:
            groups.setdefault(ind, []).append(c)
    out = [{"industry": ind, "stocks": len(cs), "median_change_pct": round(median(cs), 2),
            "adv_pct": round(pct(sum(1 for c in cs if c > 0), len(cs)), 1)} for ind, cs in groups.items()]
    out.sort(key=lambda g: (-g["median_change_pct"], -g["adv_pct"], g["industry"]))
    return out


def highs_lows(rows, hilo, symbols=None):
    """New 52-week highs and lows of one session: the day's high above the 52-week high
    as it stood before the session, the day's low under the 52-week low. Only stocks that
    are in both files are compared. Returns None when none are."""
    highs, lows, n = [], [], 0
    for sym in (sorted(rows) if symbols is None else sorted(symbols)):
        row, ref = rows.get(sym), hilo.get(sym)
        if not row or not ref:
            continue
        n += 1
        if ref.get("high") and row.get("high") is not None and row["high"] > ref["high"]:
            highs.append(sym)
        if ref.get("low") and row.get("low") is not None and row["low"] < ref["low"]:
            lows.append(sym)
    if not n:
        return None
    return {"compared": n, "new_highs": len(highs), "new_lows": len(lows), "highs": highs, "lows": lows}


def trend(days, symbols, want=20, split_drop_pct=25.0):
    """The share of `symbols` that closed above the average of their own last closes.
    days = [(date, rows)] newest first: the session, then the sessions before it that
    could be read. A stock needs a close in every one of them. The closes are not
    adjusted for splits and bonuses, so a stock with a close more than split_drop_pct
    under the close read before it is left out: its older closes are on another scale."""
    if not days:
        return None
    above = counted = short = 0
    split = []
    for sym in sorted(symbols):
        closes = [rows[sym]["close"] for _, rows in days if sym in rows]
        if len(closes) < len(days):
            short += 1
            continue
        if any((closes[k] / closes[k + 1] - 1.0) * 100.0 < -split_drop_pct for k in range(len(closes) - 1)):
            split.append(sym)
            continue
        counted += 1
        if closes[0] > sum(closes) / len(closes):
            above += 1
    return {"sessions_wanted": want, "sessions_read": len(days), "from": days[-1][0].isoformat(),
            "to": days[0][0].isoformat(), "counted": counted, "above": above,
            "above_pct": round(pct(above, counted), 1) if counted else None,
            "left_out_no_history": short, "likely_splits": split}


def average(xs, n):
    return sum(xs[-n:]) / float(n) if len(xs) >= n else None


def index_stats(bars):
    """Nifty 50 from daily (date, close) bars, oldest first, the last one the session."""
    closes = [c for _, c in bars]
    if len(closes) < 2:
        return None
    last, prev = closes[-1], closes[-2]
    out = {"date": bars[-1][0].isoformat(), "close": round(last, 2), "prev_close": round(prev, 2),
           "prev_date": bars[-2][0].isoformat(), "change_pct": round((last / prev - 1) * 100, 2), "bars": len(closes)}
    for n in (20, 50):
        ma = average(closes, n)
        out["ma%d" % n] = round(ma, 2) if ma else None
        out["vs_ma%d_pct" % n] = round((last / ma - 1) * 100, 2) if ma else None
    return out


def vix_stats(bars):
    """India VIX from daily (date, close) bars: the level, the day's change, the low and
    the high of the bars read (about a year), how far up that range the level is, and
    its percentile: the share of the other closes that are under it."""
    closes = [c for _, c in bars]
    if len(closes) < 2:
        return None
    last, prev = closes[-1], closes[-2]
    lo, hi = min(closes), max(closes)
    below = sum(1 for c in closes[:-1] if c < last)
    return {"date": bars[-1][0].isoformat(), "close": round(last, 2), "prev_close": round(prev, 2),
            "change_pct": round((last / prev - 1) * 100, 2), "bars": len(closes),
            "range_from": bars[0][0].isoformat(), "low": round(lo, 2), "high": round(hi, 2),
            "range_pos_pct": round(pct(last - lo, hi - lo), 1) if hi > lo else None,
            "pctile": round(pct(below, len(closes) - 1), 1)}


# ---------------------------------------------------------------- the reading

def _n(x):
    """A threshold without a useless .0 (55.0 -> '55')."""
    return ("%g" % x) if isinstance(x, float) else str(x)


def reading(m, thr):
    """The rule-based reading. m = the metrics that could be worked out (a missing one
    is absent or None). Each signal votes +1, 0 or -1; a missing one is left out."""
    sig, left = [], []

    def add(key, vote, value, rule):
        sig.append({"signal": key, "vote": vote, "value": value, "rule": rule})

    b = (m.get("breadth") or {}).get("nifty500")
    scope = "Nifty 500"
    if not b:
        b, scope = (m.get("breadth") or {}).get("all_eq"), "NSE EQ"
    rule = "+1 at %s %% or more, -1 at %s %% or less" % (_n(thr["breadth_support_pct"]), _n(thr["breadth_weak_pct"]))
    if b:
        v = 1 if b["adv_pct"] >= thr["breadth_support_pct"] else -1 if b["adv_pct"] <= thr["breadth_weak_pct"] else 0
        add("breadth", v, "%.1f %% of %s stocks closed up" % (b["adv_pct"], scope), rule)
    else:
        left.append("breadth (no bhavcopy)")

    t = m.get("trend")
    rule = "+1 at %s %% or more, -1 at %s %% or less" % (_n(thr["trend_support_pct"]), _n(thr["trend_weak_pct"]))
    if t and t.get("above_pct") is not None:
        v = 1 if t["above_pct"] >= thr["trend_support_pct"] else -1 if t["above_pct"] <= thr["trend_weak_pct"] else 0
        add("trend", v, "%.1f %% of Nifty 500 stocks closed above their %d-day average"
            % (t["above_pct"], t["sessions_read"]), rule)
    else:
        left.append("trend (%s)" % (m.get("trend_why") or "not worked out"))

    h = (m.get("highs_lows") or {}).get("all_eq")
    rule = ("+1 when new highs are %s times new lows or more, -1 when new lows are %s times new highs or more, "
            "0 when the two together are under %s" % (_n(thr["hl_ratio"]), _n(thr["hl_ratio"]), _n(thr["hl_min_count"])))
    if h:
        hi, lo = h["new_highs"], h["new_lows"]
        v = 0
        if hi + lo >= thr["hl_min_count"]:
            v = 1 if hi >= thr["hl_ratio"] * lo else -1 if lo >= thr["hl_ratio"] * hi else 0
        add("highs_lows", v, "%d new 52-week highs, %d new lows (NSE EQ)" % (hi, lo), rule)
    else:
        left.append("52-week highs and lows (%s)" % (m.get("highs_lows_why") or "not worked out"))

    x = m.get("nifty50")
    rule = "+1 above both its 20-day and 50-day average, -1 under both, 0 between them"
    if x and x.get("ma20") and x.get("ma50"):
        v = 1 if x["close"] > x["ma20"] and x["close"] > x["ma50"] else \
            -1 if x["close"] < x["ma20"] and x["close"] < x["ma50"] else 0
        add("nifty50", v, "Nifty 50 at %s, 20-day average %s, 50-day average %s"
            % (num(x["close"]), num(x["ma20"]), num(x["ma50"])), rule)
    else:
        left.append("Nifty 50 (%s)" % (m.get("nifty50_why") or "not enough bars"))

    x = m.get("india_vix")
    rule = ("+1 at or under the %s percentile of its last year and not up %s %% or more on the day, "
            "-1 at or above the %s percentile or up %s %% or more on the day"
            % (_ord(thr["vix_calm_pctile"]), _n(thr["vix_jump_pct"]), _ord(thr["vix_stress_pctile"]),
               _n(thr["vix_jump_pct"])))
    if x:
        jump = x["change_pct"] >= thr["vix_jump_pct"]
        v = -1 if jump or x["pctile"] >= thr["vix_stress_pctile"] else 1 if x["pctile"] <= thr["vix_calm_pctile"] else 0
        add("india_vix", v, "India VIX at %.2f (%+.2f %% on the day), above %.1f %% of its last year's closes"
            % (x["close"], x["change_pct"], x["pctile"]), rule)
    else:
        left.append("India VIX (%s)" % (m.get("india_vix_why") or "no bars"))

    score = sum(s["vote"] for s in sig)
    if len(sig) < thr["min_signals"]:
        label = "none"
    elif score >= thr["supportive_score"]:
        label = "supportive"
    elif score <= thr["weak_score"]:
        label = "weak"
    else:
        label = "mixed"
    return {"label": label, "score": score, "signals": sig, "left_out": left,
            "rule": "supportive at %+d or more, weak at %+d or less, mixed between them; no reading with fewer "
                    "than %d signals" % (thr["supportive_score"], thr["weak_score"], thr["min_signals"])}


def _ord(x):
    n = int(x) if float(x) == int(x) else None
    if n is None:
        return "%g" % x
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return "%d%s" % (n, suf)


# ---------------------------------------------------------------- thresholds

def thresholds(config_path=None, sets=None):
    """THRESHOLDS, then trader/config.json's "market" block, then --set NAME=VALUE."""
    thr = dict(THRESHOLDS)

    def put(k, v, where):
        if k not in thr:
            raise MarketError("%s: no threshold called %r (see `market.py rules`)" % (where, k))
        try:
            thr[k] = int(v) if isinstance(THRESHOLDS[k], int) else float(v)
        except (TypeError, ValueError):
            raise MarketError("%s: %s wants a number, not %r" % (where, k, v))

    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except ValueError as e:
            raise MarketError("%s is not valid JSON: %s" % (config_path, e))
        block = cfg.get("market") if isinstance(cfg, dict) else None
        for k, v in (block.items() if isinstance(block, dict) else []):
            put(k, v, _slash(config_path))
    for item in sets or []:
        if "=" not in item:
            raise MarketError("--set wants NAME=VALUE, like --set move_pct=4")
        k, v = item.split("=", 1)
        put(k.strip(), v.strip(), "--set")
    if thr["trend_sessions"] < 2 or thr["trend_min_sessions"] < 2 or thr["move_pct"] <= 0:
        raise MarketError("trend_sessions and trend_min_sessions want 2 or more, move_pct more than 0")
    if thr["weak_score"] >= thr["supportive_score"]:
        raise MarketError("weak_score must be under supportive_score")
    return thr


# ---------------------------------------------------------------- the check

def run_check(now_utc, thr, store, day=None, do_trend=True, say=None):
    """Read every source and work out every part. Returns the market.json document."""
    say = say or (lambda msg: None)
    now = to_ist(now_utc)
    sday = day or latest_session(now_utc)
    m = {"breadth": None, "industries": None, "highs_lows": None, "trend": None, "nifty50": None, "india_vix": None}
    src = []

    def source(what, name, got, how, for_day=None, note=""):
        src.append({"source": what, "file": name, "ok": got is not None, "from": how,
                    "session": for_day.isoformat() if (for_day and got is not None) else None, "note": note})

    # the session's bhavcopy
    b_name = BHAV_NAME % stamp(sday)
    bhav, how = store.nse(BHAV_DIR, b_name, bhavcopy_for(sday))
    note = ""
    if bhav is None and how == "HTTP 404" and sday == now.date():
        note = "NSE publishes it in the evening: run the check again later"
    source("NSE full bhavcopy", b_name, bhav, how, sday, note)
    rows = bhav[1] if bhav else None

    # the Nifty 500 list
    got, how = store.nse(N500_DIR, N500_NAME, parse_index_list, dated=False)
    idx = got[0] if got else None
    source("Nifty 500 list", N500_NAME, idx, how, None,
           ("the list is not dated%s; %d of its %d rows are series EQ" % (
               (", fetched %s" % store.fetched[N500_NAME]) if store.fetched.get(N500_NAME) else "",
               len(idx), got[1])) if got else "")

    if rows:
        m["breadth"] = {"all_eq": breadth(rows, None, thr["move_pct"], thr["split_drop_pct"]),
                        "nifty500": breadth(rows, set(idx), thr["move_pct"], thr["split_drop_pct"]) if idx else None}
        if idx:
            m["industries"] = industry_table(rows, idx, thr["split_drop_pct"])

    # 52-week highs and lows
    h_name = HILO_NAME % stamp(sday)
    if rows:
        hilo, how = store.nse(HILO_DIR, h_name, hilo_for(sday), remember_miss=True)
        source("NSE 52-week high/low", h_name, hilo, how, sday,
               "the highs and lows as they stood before that session" if hilo is not None else "")
        if hilo:
            m["highs_lows"] = {"all_eq": highs_lows(rows, hilo[1]),
                               "nifty500": highs_lows(rows, hilo[1], set(idx)) if idx else None}
            if not m["highs_lows"]["all_eq"]:
                m["highs_lows"], m["highs_lows_why"] = None, "no stock is in both files"
        else:
            m["highs_lows_why"] = "%s: %s" % (h_name, how)
    else:
        m["highs_lows_why"] = "it needs the bhavcopy"

    # the 20-day part
    if not do_trend:
        m["trend_why"] = "skipped with --no-trend"
    elif not rows:
        m["trend_why"] = "it needs the bhavcopy"
    elif not idx:
        m["trend_why"] = "it needs the Nifty 500 list"
    else:
        want = thr["trend_sessions"]
        dates = sessions_back(sday, want)
        days, missing = [(sday, rows)], []
        say("Reading the bhavcopies of the %d sessions before %s (kept in the cache after the first run)..."
            % (want - 1, sday.isoformat()))
        for d in dates[1:]:
            name = BHAV_NAME % stamp(d)
            got, how = store.nse(BHAV_DIR, name, bhavcopy_for(d), remember_miss=True)
            if got is None:
                missing.append({"session": d.isoformat(), "file": name, "why": how})
            else:
                days.append((d, got[1]))
        hist = {"source": "NSE bhavcopies for the 20-day average", "file": BHAV_NAME % "DDMMYYYY",
                "ok": len(days) >= thr["trend_min_sessions"], "from": "%d of %d sessions read" % (len(days), want),
                "session": None, "note": "%s to %s" % (dates[-1].isoformat(), sday.isoformat())}
        src.append(hist)
        if len(days) < thr["trend_min_sessions"]:
            m["trend_why"] = ("only %d of %d bhavcopies could be read, under the minimum of %d"
                              % (len(days), want, thr["trend_min_sessions"]))
        else:
            m["trend"] = trend(days, set(idx), want, thr["split_drop_pct"])
            if not m["trend"]["counted"]:
                m["trend"], m["trend_why"] = None, "no Nifty 500 stock has a close in every session read"
        m["trend_missing"] = missing

    # the index backdrop
    for key, sym, fn, name in (("nifty50", NIFTY, index_stats, "Nifty 50"), ("india_vix", VIX, vix_stats, "India VIX")):
        got, how = store.yahoo(sym, sday)
        bars, where = got if got else ([], None)
        note = ""
        if got is not None and where is None:
            last = bars[-1][0] if bars else None
            m[key + "_why"] = ("Yahoo has no close for %s yet%s" % (sday.isoformat(), (
                "; its last bar is %s" % last.isoformat()) if last else ""))
            note = m[key + "_why"]
        elif got is None:
            m[key + "_why"] = "Yahoo: %s" % how
        else:
            m[key] = fn(bars)
            if m[key] is None:
                m[key + "_why"] = "Yahoo gave fewer than 2 daily bars"
            else:
                m[key]["close_from"] = where
                if where == "last_price":
                    note = "Yahoo's daily close was still empty: its last price of that session stands in"
        src.append({"source": "Yahoo daily bars, %s (%s)" % (name, sym), "file": YAHOO_FILE[sym],
                    "ok": m[key] is not None, "from": how,
                    "session": sday.isoformat() if m[key] is not None else None, "note": note})

    store.prune(sessions_back(sday, thr["trend_sessions"])[-1])
    store.flush()
    ok, why = trading_day(now.date())
    doc = {"tool": "trader-market", "session": sday.isoformat(),
           "checked_ist": now.strftime("%Y-%m-%d %H:%M"),
           "today": {"date": now.date().isoformat(), "trading_day": ok, "why": why},
           "sources": src, "requests": store.requests,
           "breadth": m["breadth"], "industries": m["industries"], "highs_lows": m["highs_lows"],
           "trend": m["trend"], "trend_missing": m.get("trend_missing", []),
           "nifty50": m["nifty50"], "india_vix": m["india_vix"],
           "reading": reading(m, thr), "thresholds": thr, "note": NOTE}
    return doc


# ---------------------------------------------------------------- printing

def num(x):
    return "{:,.2f}".format(x)


def count(x):
    return "{:,}".format(x)


def signed(x, digits=2):
    return ("%+." + str(digits) + "f") % x


def _names(syms, cap=12):
    if not syms:
        return ""
    more = len(syms) - cap
    return "%s%s" % (", ".join(syms[:cap]), (", and %d more" % more) if more > 0 else "")


def render(doc, show=5):
    """The market.json document -> the lines of the report."""
    thr = doc["thresholds"]
    sday = datetime.date.fromisoformat(doc["session"])
    today = doc["today"]
    out = []
    out.append("Market Health Check: NSE session of %s" % day_label(sday))
    tday = datetime.date.fromisoformat(today["date"])
    out.append("Checked %s %s IST. %s" % (tday.strftime("%a"), doc["checked_ist"], (
        "NSE is closed today (%s)." % today["why"]) if not today["trading_day"] else (
        "That is the latest completed session." if sday != tday else "Today's session is complete.")))
    note = calendar_note(sday) or calendar_note(datetime.date.fromisoformat(today["date"]))
    if note:
        out.append(note)
    missing = {s["source"]: s for s in doc["sources"] if not s["ok"]}

    out.append("")
    out.append("Breadth (NSE full bhavcopy, series EQ; up = closed above the previous close)")
    b = doc.get("breadth")
    if not b:
        s = missing.get("NSE full bhavcopy", {})
        out.append("  Not available: %s (%s)%s." % (s.get("file", "the bhavcopy"), s.get("from", "not read"),
                                                   (". " + s["note"]) if s.get("note") else ""))
    else:
        for label, x in (("All EQ stocks", b.get("all_eq")), ("Nifty 500", b.get("nifty500"))):
            if not x:
                out.append("  %-14s not available: %s." % (label, (missing.get("Nifty 500 list") or {}).get(
                    "from", "no stock could be counted")))
                continue
            ratio = ("advance/decline %.2f" % x["ad_ratio"]) if x["ad_ratio"] is not None else "no declines"
            out.append("  %-14s %s stocks: %s up, %s down, %s unchanged; %s; %.1f %% up, %.1f %% down"
                       % (label, count(x["stocks"]), count(x["advances"]), count(x["declines"]),
                          count(x["unchanged"]), ratio, x["adv_pct"], x["dec_pct"]))
            out.append("  %-14s up %s %% or more: %s (%.1f %%); down %s %% or more: %s (%.1f %%); median change %s %%"
                       % ("", _n(x["move_pct"]), count(x["up_big"]), x["up_big_pct"], _n(x["move_pct"]),
                          count(x["down_big"]), x["down_big_pct"], signed(x["median_change_pct"])))
            if x.get("not_counted") and label == "Nifty 500":
                out.append("  %-14s %d of the list's stocks had no EQ row with a previous close that day"
                           % ("", x["not_counted"]))
            if x.get("likely_splits"):
                out.append("  %-14s left out as a likely split or bonus (close more than %s %% under PREV_CLOSE, "
                           "which the file does not adjust; not verified): %s"
                           % ("", _n(thr["split_drop_pct"]), _names(x["likely_splits"])))

    ind = doc.get("industries")
    if ind:
        big = [g for g in ind if g["stocks"] >= thr["industry_min_stocks"]]
        out.append("")
        out.append("Industries (Nifty 500 list, by median change; %d industries with %s stocks or more)"
                   % (len(big), _n(thr["industry_min_stocks"])))
        width = max([len(g["industry"]) for g in big] or [10])

        def line(g):
            return "    %-*s  %3d stocks  median %s %%  %5.1f %% up" % (
                width, g["industry"], g["stocks"], signed(g["median_change_pct"]), g["adv_pct"])
        if len(big) <= 2 * show:
            out.extend(line(g) for g in big)
        else:
            out.append("  Rose most")
            out.extend(line(g) for g in big[:show])
            out.append("  Rose least")
            out.extend(line(g) for g in big[-show:])

    out.append("")
    out.append("52-week highs and lows (the day's high and low against the 52-week high and low before the session)")
    h = doc.get("highs_lows")
    if not h:
        s = missing.get("NSE 52-week high/low")
        out.append("  Not available: %s." % (("%s (%s)" % (s["file"], s["from"])) if s else "it needs the bhavcopy"))
    else:
        for label, x in (("All EQ stocks", h.get("all_eq")), ("Nifty 500", h.get("nifty500"))):
            if not x:
                continue
            out.append("  %-14s %s new highs, %s new lows, of %s stocks compared"
                       % (label, count(x["new_highs"]), count(x["new_lows"]), count(x["compared"])))
            if label == "Nifty 500":
                for word in ("highs", "lows"):
                    if x[word]:
                        out.append("  %-14s %s: %s" % ("", word, _names(x[word])))

    out.append("")
    out.append("Trend (Nifty 500 stocks above their own %s-day average close)" % _n(thr["trend_sessions"]))
    t = doc.get("trend")
    why = [x for x in doc["reading"]["left_out"] if x.startswith("trend")]
    if not t:
        out.append("  Not available: %s." % (why[0][7:-1] if why else "not worked out"))
    else:
        out.append("  %s of %s (%.1f %%) closed above the average of their last %d closes (%s to %s)"
                   % (count(t["above"]), count(t["counted"]), t["above_pct"], t["sessions_read"], t["from"], t["to"]))
        if t["left_out_no_history"]:
            out.append("  Left out: %d without a close in every session read" % t["left_out_no_history"])
        if t["likely_splits"]:
            out.append("  Left out as a likely split or bonus in the window (a close more than %s %% under the one "
                       "before it; not verified): %s" % (_n(thr["split_drop_pct"]), _names(t["likely_splits"])))
    for x in doc.get("trend_missing") or []:
        out.append("  Missing: %s (%s). The average is over the sessions that could be read." % (x["file"], x["why"]))

    out.append("")
    out.append("Index backdrop (Yahoo daily bars; NSE data on Yahoo is delayed)")
    x = doc.get("nifty50")
    if not x:
        out.append("  Nifty 50     not available: %s." % _why(doc, "Nifty 50"))
    else:
        parts = []
        for n in (20, 50):
            ma, d = x.get("ma%d" % n), x.get("vs_ma%d_pct" % n)
            parts.append(("%.2f %% %s its %d-day average (%s)" % (abs(d), "above" if d >= 0 else "under", n, num(ma)))
                         if ma else "no %d-day average (only %d bars)" % (n, x["bars"]))
        out.append("  Nifty 50     %s, %s %% on the day (from %s); %s" % (num(x["close"]), signed(x["change_pct"]),
                                                                        num(x["prev_close"]), "; ".join(parts)))
        if x.get("close_from") == "last_price":
            out.append("               Yahoo's daily close was still empty: this is its last price of the session.")
    x = doc.get("india_vix")
    if not x:
        out.append("  India VIX    not available: %s." % _why(doc, "India VIX"))
    else:
        pos = (", %.1f %% of the way up" % x["range_pos_pct"]) if x["range_pos_pct"] is not None else ""
        out.append("  India VIX    %.2f, %s %% on the day (from %.2f); range since %s: %.2f to %.2f%s; "
                   "above %.1f %% of the closes in it"
                   % (x["close"], signed(x["change_pct"]), x["prev_close"], x["range_from"], x["low"], x["high"],
                      pos, x["pctile"]))
        if x.get("close_from") == "last_price":
            out.append("               Yahoo's daily close was still empty: this is its last price of the session.")

    r = doc["reading"]
    out.append("")
    if r["label"] == "none":
        out.append("Reading: NONE. Only %d signal%s could be worked out and the rule wants %d."
                   % (len(r["signals"]), "" if len(r["signals"]) == 1 else "s", thr["min_signals"]))
    else:
        words = {"supportive": "SUPPORTIVE for long breakouts", "mixed": "MIXED for long breakouts",
                 "weak": "WEAK for long breakouts"}[r["label"]]
        out.append("Reading: %s" % words)
        out.append("  Score %s from %d signals: %s." % ("%+d" % r["score"] if r["score"] else "0", len(r["signals"]),
                                                      r["rule"]))
    for s in r["signals"]:
        out.append("  %s  %s" % ("%+d" % s["vote"] if s["vote"] else " 0", s["value"]))
        out.append("      rule: %s" % s["rule"])
    for x in r["left_out"]:
        out.append("  Left out of the reading: %s" % x)
    out.append(doc["note"])

    out.append("")
    out.append("Sources and how fresh each one is")
    w1 = max(len(s["source"]) for s in doc["sources"])
    w2 = max(len(s["file"]) for s in doc["sources"])
    for s in doc["sources"]:
        state = ("session %s" % s["session"]) if s["session"] else ("read" if s["ok"] else "NOT READ")
        out.append("  %-*s  %-*s  %s; %s%s" % (w1, s["source"], w2, s["file"], state, s["from"],
                                              ("; " + s["note"]) if s["note"] else ""))
    out.append("  %d request%s made in this run." % (doc["requests"], "" if doc["requests"] == 1 else "s"))
    return out


def _why(doc, name):
    for x in doc["reading"]["left_out"]:
        if x.startswith(name + " ("):
            return x[len(name) + 2:-1]
    return "not read"


# ---------------------------------------------------------------- commands

def cmd_check(a):
    now = parse_now(a.now)
    thr = thresholds(a.config, a.set)
    if a.move_pct is not None:
        thr["move_pct"] = a.move_pct
    day = None
    if a.date:
        try:
            day = datetime.date.fromisoformat(a.date)
        except ValueError:
            raise MarketError("--date wants a date like 2026-11-20")
        ok, why = trading_day(day)
        if not ok:
            raise MarketError("%s was not a trading day (%s)" % (day.isoformat(), why))
        if day > latest_session(now):
            raise MarketError("the session of %s is not complete yet" % day.isoformat())
    if a.offline and not os.path.isdir(a.offline):
        raise MarketError("--offline wants a folder: %s" % a.offline)
    store = Store(a.cache, to_ist(now).date(), offline=a.offline, refresh=a.refresh, pause=a.pause,
                  save=not a.no_write)

    def say(msg):
        if not (a.quiet or a.json or a.offline):
            print(msg, file=sys.stderr)
    doc = run_check(now, thr, store, day, not a.no_trend, say)
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    else:
        print("\n".join(render(doc, a.show)))
        if not a.no_write:
            print("Wrote %s" % _slash(a.out))
    got = any(doc.get(k) for k in ("breadth", "highs_lows", "trend", "nifty50", "india_vix"))
    return 0 if got else 1


def cmd_show(a):
    doc = read_json(a.out)
    if not isinstance(doc, dict) or doc.get("tool") != "trader-market":
        raise MarketError("no saved check in %s: run `market.py check` first" % _slash(a.out))
    if a.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    else:
        print("\n".join(render(doc, a.show)))
        print("Read from %s (checked %s IST). Nothing was fetched." % (_slash(a.out), doc.get("checked_ist")))
    return 0


RULE_HELP = [
    ("move_pct", "a big move: a stock up or down this many percent or more on the day"),
    ("trend_sessions", "the average close of this many sessions (one bhavcopy each)"),
    ("trend_min_sessions", "with fewer bhavcopies than this the trend part is left out"),
    ("split_drop_pct", "a close this many percent under the close before it: a likely split or bonus, left out"),
    ("industry_min_stocks", "industries with fewer stocks are not printed"),
    ("breadth_support_pct", "breadth votes +1 when this share of Nifty 500 stocks closed up, or more"),
    ("breadth_weak_pct", "breadth votes -1 at this share or less"),
    ("trend_support_pct", "trend votes +1 when this share is above its 20-day average, or more"),
    ("trend_weak_pct", "trend votes -1 at this share or less"),
    ("hl_ratio", "highs and lows vote when one is this many times the other, or more"),
    ("hl_min_count", "highs and lows vote 0 when the two together are under this"),
    ("vix_calm_pctile", "India VIX votes +1 at or under this percentile of its last year"),
    ("vix_stress_pctile", "India VIX votes -1 at or above this percentile"),
    ("vix_jump_pct", "India VIX votes -1 when it rose this many percent or more on the day"),
    ("supportive_score", "SUPPORTIVE when the votes add up to this or more"),
    ("weak_score", "WEAK when they add up to this or less; MIXED between the two"),
    ("min_signals", "no reading when fewer signals than this could be worked out"),
]


def cmd_rules(a):
    thr = thresholds(a.config, a.set)
    if a.json:
        print(json.dumps(thr, indent=2))
        return 0
    print("The reading: five signals, each votes +1, 0 or -1; the votes are added.")
    print("  breadth      share of Nifty 500 stocks that closed above their previous close")
    print("  trend        share of Nifty 500 stocks that closed above their own 20-day average close")
    print("  highs/lows   new 52-week highs against new 52-week lows, all EQ stocks")
    print("  nifty50      the Nifty 50's close against its 20-day and 50-day average close: +1 above both, "
          "-1 under both")
    print("  india_vix    the India VIX's percentile in its own last year, and its change on the day")
    print("A signal whose source is missing is left out and named. Thresholds now (default in brackets):")
    for k, text in RULE_HELP:
        print("  %-22s %-6s [%s]  %s" % (k, _n(thr[k]), _n(THRESHOLDS[k]), text))
    print('Change one with --set NAME=VALUE, or in trader/config.json: {"market": {"breadth_support_pct": 60}}')
    print(NOTE)
    return 0


def main(argv=None):
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Market Health Check (trader-market): breadth, 52-week highs and lows, "
                                             "trend, the Nifty 50 and the India VIX, and one rule-based reading.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--out", default=DEFAULT_OUT, help="the saved check (%s)" % DEFAULT_OUT)
        p.add_argument("--config", default="trader/config.json", help='thresholds in its "market" block')
        p.add_argument("--set", action="append", metavar="NAME=VALUE", help="one threshold for this run (repeatable)")
        p.add_argument("--show", type=int, default=5, help="industries to print at each end (5)")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("check", help="read the sources, print the check, write trader/market.json")
    common(p)
    p.add_argument("--no-trend", action="store_true", help="skip the 20-day part (the slower one: 20 bhavcopies)")
    p.add_argument("--date", help="check this earlier session (YYYY-MM-DD) instead of the latest completed one")
    p.add_argument("--move-pct", type=float, help="a big move, in percent (3)")
    p.add_argument("--cache", default=DEFAULT_CACHE, metavar="DIR", help="where fetched files are kept (%s)" % DEFAULT_CACHE)
    p.add_argument("--refresh", action="store_true", help="ignore the cache and fetch everything again")
    p.add_argument("--offline", metavar="DIR", help="read saved files from this folder, no network")
    p.add_argument("--now", help='pretend it is this time ("2026-11-24 10:00" IST, or ...Z)')
    p.add_argument("--pause", type=float, default=NSE_PAUSE, help="seconds between requests to one host (0.5 at least)")
    p.add_argument("--no-write", action="store_true", help="write neither market.json nor the cache")
    p.add_argument("--quiet", action="store_true")
    p = sub.add_parser("show", help="print the saved check again, no network")
    common(p)
    p = sub.add_parser("rules", help="the reading's rules and thresholds")
    common(p)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"check": cmd_check, "show": cmd_show, "rules": cmd_rules}[a.cmd](a)
    except MarketError as e:
        print("market.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("market.py: stopped", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
