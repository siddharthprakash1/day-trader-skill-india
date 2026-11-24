#!/usr/bin/env python3
"""Backtest for trader-backtest (India, NSE). Standard library only.

Re-runs the pack's three intraday setups on every past session Yahoo still
has, across many stocks, and reports what they would have made AFTER charges.
It never places an order, predicts nothing and recommends nothing.

    python backtest.py run TCS INFY RELIANCE             # the symbols you name
    python backtest.py run --universe watchlist          # trader/watchlist.json
    python backtest.py run --universe nifty500 --limit 40
    python backtest.py run --universe nifty500           # about 500 requests, several minutes
    python backtest.py run --universe my-list.txt --setups orb,gap --risk 500
    python backtest.py run --universe nifty500 --no-fetch   # only what is already in the cache
    python backtest.py show                              # the saved trader/backtest.json again

The setups (long side only, 5-minute bars, the same rules as trader-entry):
  orb    opening-range breakout: the first completed bar after the 15-minute
         opening range (the 9:15, 9:20 and 9:25 bars) that CLOSES above the
         range high. Stop: the opening-range midpoint (trader-exit's default).
  vwap   VWAP reclaim: the first completed bar that closes above the session
         VWAP right after at least --min-bars-below bars (default 3) in a row
         closed at or under it. VWAP = sum(typical price x volume) / sum(volume)
         from 9:15, typical price = (high + low + close) / 3; a bar with no
         volume adds nothing. Stop: the lowest low of that run of bars and the
         trigger bar.
  gap    gap and go: the 9:15 bar opens at least --gap-pct percent (default 1)
         above the previous session's last bar's close, then the same trigger
         as orb. Stop: the opening-range low.

How one trade is simulated (no look-ahead):
  1. A signal on a bar's close enters AT THAT CLOSE, to the paisa. Only bars up
     to and including the entry bar decide the entry. Entries are taken from
     no_entries_before to no_entries_after (09:30 to 14:30), one per setup,
     symbol and session: the first trigger.
  2. The stop is rounded down to the paisa; the target is entry + 2 x risk
     (--target-r), as trader-exit sets them. A stop at or above the entry, or
     closer than 0.05 % of the price, is refused by trader-exit: no trade.
  3. Whole shares from a fixed rupee risk: floor(risk / (entry - stop)), and no
     order above max_order_inr (the pack's limit; --max-order 0 lifts it).
     Under one share: no trade.
  4. The bars AFTER the entry bar are walked in order. low <= stop: out at the
     stop. Else high >= target: out at the target. A bar that touches both
     counts as the stop. Neither by flat_by (15:00): out at the close of the
     last bar before it (the 14:55 bar). Outcome target, stop or time.
  5. Charges: one Groww equity-intraday round trip, the same table and the same
     arithmetic as trader-journal (a "charges" block in trader/config.json
     overrides a line). R = (exit - entry) / (entry - stop); R after charges =
     net P&L / (shares x risk a share).
A session counts for a symbol only when Yahoo has EVERY bar from 9:15 to the
flat-by bar; the others are left out and counted, never patched.

Data: one request per symbol, Yahoo's chart JSON for <SYMBOL>.NS with
range=60d and interval=5m (about 60 sessions; Yahoo keeps no more). At least
0.5 seconds between requests, one retry at most, and every answer is saved
under trader/cache/ for the day, so a second run asks Yahoo nothing and a
stopped run picks up where it left off. --universe nifty500 reads NSE's own
published list (one request, cached the same way).

Times are India Standard Time (a fixed UTC+5:30, never the computer's zone);
--now "2026-11-24 10:00" pins the clock. Prices are rupees.

Exit codes: 0 = a report, 1 = an error, nothing to test or an incomplete run.
"""

import argparse
import calendar
import csv
import datetime
import glob
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
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP

UA = "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)"
PAUSE = 0.5          # seconds between requests, at least: be polite to free endpoints
BACKOFF = 5.0        # seconds to wait before the one retry after a 429
HOSTS = ("query2", "query1")
MAX_RATE_FAILS = 5   # this many rate-limit failures in a row stop the run (it resumes from the cache)
INTERVAL = 300       # 5-minute bars, in seconds
SESSION_BARS = 75    # 09:15 to 15:30 IST in 5-minute bars
OPEN_MIN = 9 * 60 + 15
CLOSE_MIN = 15 * 60 + 30
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
EPOCH = datetime.datetime(1970, 1, 1)
RUPEE = "₹"
CENT = Decimal("0.01")
MIN_RISK_PCT = Decimal("0.05")   # trader-exit refuses a stop closer than 0.05 % of the price
OR_BARS = 3          # the 15-minute opening range
MIN_TRADES = 30      # under this many trades a setup's numbers prove nothing
SETUPS = ("orb", "vwap", "gap")
SETUP_NAMES = {
    "orb": "Opening-range breakout (orb): first close above the 15-minute range high; stop at the range midpoint",
    "vwap": "VWAP reclaim (vwap): first close back above VWAP after %d bars at or under it; stop at the pullback low",
    "gap": "Gap and go (gap): opens %s %% or more above the previous close, then a close above the range high; "
           "stop at the range low",
}
# the pack's example config (trader-budget/config.example.json), used when trader/config.json is absent
DEFAULTS = {"max_loss_per_trade_inr": 500, "max_order_inr": 40000, "no_entries_before": "09:30",
            "no_entries_after": "14:30", "flat_by": "15:00"}
NIFTY500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Charges for ONE equity INTRADAY round trip on NSE: a copy of trader-journal's table
# (Groww's pricing page, groww.in/pricing, read 2026-11-24). tests/test_backtest.py checks
# that this file and trader-journal/journal.py give the same charges.
CHARGES = {
    "brokerage_flat": "20",         # rupees per executed order ...
    "brokerage_pct": "0.1",         # ... or this percent of the order value, whichever is LOWER
    "brokerage_min": "5",           # minimum brokerage per executed order, rupees
    "stt_sell_pct": "0.025",        # Securities Transaction Tax, on the SELL side only
    "stamp_buy_pct": "0.003",       # stamp duty, on the BUY side only
    "exchange_pct": "0.00307",      # NSE exchange transaction charge, both sides
    "sebi_pct": "0.0001",           # SEBI turnover charge, both sides
    "ipft_pct": "0.0001",           # NSE Investor Protection Fund Trust charge, both sides
    "gst_pct": "18",                # GST on brokerage + exchange + SEBI + IPFT
    "dp_charge": "0",               # DP charges: none for intraday
    "auto_squareoff_fee": "50",     # only when the broker closes the position; never charged here
}
CHARGES_SOURCE = "Groww's pricing page (groww.in/pricing), read 2026-11-24, equity intraday on NSE"

NSE_HOLIDAYS = {  # NSE circular of 12 Dec 2025; weekdays cross-checked against NIFTY trading days
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

CAVEATS = (
    "About 60 sessions is one market phase. A setup that did well or badly in it proves little about the next one.",
    "Fills are optimistic: every entry is at the trigger bar's close and every stop and target at its exact "
    "price. A real order slips, most of all a stop in a fast market or after a gap.",
    "Survivorship: the Nifty 500 list is today's list. Stocks that fell out of it during these weeks are not tested.",
    "Yahoo's 5-minute bars for NSE have holes and can be wrong. Sessions with a missing bar were left out, not "
    "repaired, and the closing-auction price is not in them.",
    "Testing several setups and then picking the best one overstates it: some of any difference between them is luck.",
    "The totals add up every simulated trade as if each stood alone. Nobody can take hundreds of trades a day; "
    "this is not the result of one account.",
)
FINE_PRINT = ("Numbers from the past, not a prediction and not advice. Most people who day trade lose money. "
              "Education and tooling, not financial advice.")


class BTError(Exception):
    """kind: 'error' (stop), 'gone' (Yahoo has no such chart: remembered for the day),
    'rate' (rate limited), 'net' (could not connect)."""

    def __init__(self, msg, kind="error"):
        Exception.__init__(self, msg)
        self.kind = kind


# ---------------------------------------------------------------- time (India: a fixed UTC+5:30)

def to_ist(utc):
    """Naive UTC -> India Standard Time, a fixed UTC+5:30 with no daylight saving."""
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    return local.replace(tzinfo=None) - IST_OFFSET


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset (or ending in " IST") is India time."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    t = t.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise BTError("--now wants a time like \"2026-11-24 10:00\" (India time) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def utc_of(ts):
    return EPOCH + datetime.timedelta(seconds=int(ts))


def trading_day(d):
    """Weekends and the NSE holiday list (2026 only; other years: weekends only)."""
    return d.weekday() < 5 and d.isoformat() not in NSE_HOLIDAYS


def previous_trading_day(d):
    p = d - datetime.timedelta(days=1)
    while not trading_day(p):
        p -= datetime.timedelta(days=1)
    return p


def parse_hhmm(text, what):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise BTError("%s wants an India time like 15:00, not %r" % (what, text))
    return int(m.group(1)) * 60 + int(m.group(2))


def clock24(minutes):
    return "%02d:%02d" % divmod(minutes, 60)


def slot_close(slot):
    """The clock time at which bar `slot` closes: slot 5 (the 09:40 bar) -> '09:45'."""
    return clock24(OPEN_MIN + 5 * (slot + 1))


def fortnight(date_text):
    """'2026-11-23' -> ((2026, 11, 1), '16-30 Nov 2026'): the half of the month a session is in."""
    y, m, d = (int(x) for x in date_text.split("-"))
    if d <= 15:
        return (y, m, 0), "1-15 %s %d" % (MONTHS[m - 1], y)
    return (y, m, 1), "16-%d %s %d" % (calendar.monthrange(y, m)[1], MONTHS[m - 1], y)


# ---------------------------------------------------------------- symbols

SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}(\.BO)?$")


def yahoo_symbol(sym):
    """NSE symbol -> Yahoo symbol: RELIANCE -> RELIANCE.NS, M&M -> M&M.NS."""
    s = sym.strip().upper()
    return s if s.endswith((".NS", ".BO")) or s.startswith("^") else s + ".NS"


def nse_symbol(sym):
    """What files and output show: TCS.NS -> TCS."""
    s = str(sym or "").strip().upper()
    return s[:-3] if s.endswith(".NS") else s


def clean_symbol(sym):
    s = nse_symbol(sym)
    if not SYMBOL_RE.match(s):
        raise BTError("%r is not an NSE equity symbol (like RELIANCE, M&M or BAJAJ-AUTO)" % (sym,))
    return s


def chart_url(sym, host="query2"):
    return "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=60d&interval=5m" % (
        host, urllib.parse.quote(yahoo_symbol(sym), safe=""))


# ---------------------------------------------------------------- money (Decimal rupees, to the paisa)

def D(x, what="a number"):
    try:
        if isinstance(x, Decimal):
            return x
        if isinstance(x, bool):
            raise ValueError
        if isinstance(x, float):
            return Decimal(repr(x))
        return Decimal(str(x).strip().lstrip(RUPEE).replace(",", ""))
    except (InvalidOperation, ValueError):
        raise BTError("%s is not a number: %r" % (what, x))


def paise(x):
    """Round half up to the paisa."""
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def floor_paise(x):
    """A long's stop: round down, away from the entry (as trader-exit does)."""
    return D(x).quantize(CENT, rounding=ROUND_FLOOR)


def money(x):
    d = paise(x)
    return "%s%s%s" % ("-" if d < 0 else "", RUPEE, format(abs(d), ",.2f"))


def signed(x):
    d = paise(x)
    return ("+" if d > 0 else "") + money(d)


def _f(d):
    return float(paise(d))


# ---------------------------------------------------------------- charges (the same arithmetic as trader-journal)

def load_charges(cfg=None):
    """The CHARGES table as Decimals, with the config's "charges" block laid over it."""
    rates = {k: Decimal(v) for k, v in CHARGES.items()}
    over = (cfg or {}).get("charges")
    changed = []
    if over is None:
        return rates, changed
    if not isinstance(over, dict):
        raise BTError("\"charges\" in the config must be an object like {\"brokerage_flat\": 0}")
    for k, v in over.items():
        if k not in rates:
            raise BTError("unknown charge %r in the config; the lines are: %s" % (k, ", ".join(CHARGES)))
        d = D(v, "charges.%s" % k)
        if d < 0 or not d.is_finite():
            raise BTError("charges.%s must be 0 or more, got %r" % (k, v))
        if d != rates[k]:
            changed.append(k)
        rates[k] = d
    return rates, changed


def order_brokerage(value, rates):
    """Brokerage for one executed order: the flat fee or the percent, whichever is lower, then the minimum."""
    value = D(value)
    if value <= 0:
        return Decimal("0.00")
    b = min(rates["brokerage_flat"], value * rates["brokerage_pct"] / 100)
    return paise(max(b, rates["brokerage_min"]))


def round_trip(buy, sell, qty, rates=None):
    """Charges for one intraday round trip: buy qty at buy, sell them at sell, one order each
    way. Decimal rupees; each line is rounded half up to the paisa."""
    if rates is None:
        rates = load_charges()[0]
    q = int(qty)
    if q <= 0:
        raise BTError("the quantity must be a whole number of shares above 0")
    buy, sell = paise(buy), paise(sell)
    if buy <= 0 or sell <= 0:
        raise BTError("the buy and sell prices must be above %s0" % RUPEE)
    buy_value, sell_value = buy * q, sell * q
    turnover = buy_value + sell_value
    brokerage = order_brokerage(buy_value, rates) + order_brokerage(sell_value, rates)
    stt = paise(sell_value * rates["stt_sell_pct"] / 100)
    stamp = paise(buy_value * rates["stamp_buy_pct"] / 100)
    exchange = paise(turnover * rates["exchange_pct"] / 100)
    sebi = paise(turnover * rates["sebi_pct"] / 100)
    ipft = paise(turnover * rates["ipft_pct"] / 100)
    gst = paise((brokerage + exchange + sebi + ipft) * rates["gst_pct"] / 100)
    dp = paise(rates["dp_charge"])
    total = brokerage + stt + stamp + exchange + sebi + ipft + gst + dp
    gross = sell_value - buy_value
    return {"qty": q, "buy": buy, "sell": sell, "buy_value": buy_value, "sell_value": sell_value,
            "brokerage": brokerage, "stt": stt, "stamp_duty": stamp, "exchange": exchange, "sebi": sebi,
            "ipft": ipft, "gst": gst, "dp": dp, "total": total, "gross": gross, "net": gross - total}


# ---------------------------------------------------------------- network and the day's cache

def _sleep(seconds):
    time.sleep(seconds)


def _clock():
    return time.time()


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/csv, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def polite_get(url, net, pause=PAUSE):
    """One GET, at least `pause` seconds after this run's previous request. `net` counts them."""
    last = net.get("last")
    if last is not None:
        wait = max(pause, PAUSE) - (_clock() - last)
        if wait > 0:
            _sleep(wait)
    net["requests"] = net.get("requests", 0) + 1
    try:
        return _get(url)
    finally:
        net["last"] = _clock()


def fetch_chart(sym, net, pause=PAUSE):
    """60 days of 5-minute bars in one request to query2. On a 429, a 5xx or a failed connection:
    ONE retry, on query1 (after BACKOFF seconds when it was a 429). Never more than two requests."""
    why, kind = "no answer", "net"
    for n, host in enumerate(HOSTS):
        try:
            return polite_get(chart_url(sym, host), net, pause)
        except urllib.error.HTTPError as e:
            code = e.code
            try:
                e.close()
            except Exception:
                pass
            if code == 404:
                raise BTError("Yahoo has no chart for %s (HTTP 404)" % sym, "gone")
            if code != 429 and code < 500:
                raise BTError("Yahoo answered HTTP %d for %s" % (code, sym), "net")
            why, kind = "HTTP %d" % code, ("rate" if code == 429 else "net")
            if n == 0 and code == 429:
                _sleep(BACKOFF)
        except urllib.error.URLError as e:
            why, kind = "could not connect: %s" % (e.reason,), "net"
        except OSError as e:
            why, kind = "network error: %s" % (e,), "net"
    raise BTError("Yahoo did not answer for %s (%s, after one retry)" % (sym, why), kind)


def cache_file(cache_dir, sym, today):
    return os.path.join(cache_dir, "%s-60d5m-%s.json.gz" % (sym, today))


def none_file(cache_dir, sym, today):
    return os.path.join(cache_dir, "%s-60d5m-%s.none" % (sym, today))


def read_gz(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return fh.read()


def write_cache(cache_dir, sym, today, text):
    """Save today's answer and drop the same symbol's older ones, so the cache does not grow."""
    os.makedirs(cache_dir, exist_ok=True)
    path = cache_file(cache_dir, sym, today)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    for old in glob.glob(os.path.join(glob.escape(cache_dir), glob.escape(sym) + "-60d5m-*")):
        if os.path.basename(old) not in (os.path.basename(path), os.path.basename(none_file(cache_dir, sym, today))):
            try:
                os.remove(old)
            except OSError:
                pass


def chart_result(text, sym):
    try:
        data = json.loads(text)
    except ValueError:
        raise BTError("Yahoo returned something that is not JSON for %s" % sym, "net")
    chart = data.get("chart") if isinstance(data, dict) else None
    if not isinstance(chart, dict):
        raise BTError("Yahoo returned something unexpected for %s" % sym, "net")
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise BTError("Yahoo has no data for %s: %s" % (sym, err.get("description") or "unknown symbol"), "gone")
    return res[0] or {}


def get_text(sym, today, cache_dir, net, offline=None, fetch=True, pause=PAUSE):
    """(chart JSON text, where from) for one symbol: an --offline folder, else today's cache,
    else Yahoo (and then the cache). Raises BTError with a kind."""
    if offline:
        for name in ("%s-60d5m.json" % sym, "%s-60d5m.json.gz" % sym, "%s.json" % sym, "%s.json.gz" % sym):
            p = os.path.join(offline, name)
            if os.path.exists(p):
                try:
                    return read_gz(p), "offline"
                except (OSError, ValueError) as e:
                    raise BTError("could not read %s: %s" % (p, e), "gone")
        raise BTError("no %s-60d5m.json(.gz) in %s" % (sym, offline), "gone")
    path = cache_file(cache_dir, sym, today)
    if os.path.exists(path):
        try:
            return read_gz(path), "cache"
        except (OSError, ValueError, EOFError):
            pass                                    # a damaged cache file: ask again
    nf = none_file(cache_dir, sym, today)
    if os.path.exists(nf):
        with open(nf, encoding="utf-8") as fh:
            raise BTError(fh.read().strip() or "Yahoo had no data for %s" % sym, "gone-cached")
    if not fetch:
        raise BTError("%s is not in today's cache (--no-fetch)" % sym, "skipped")
    try:
        text = fetch_chart(sym, net, pause)
        chart_result(text, sym)                     # an answer without bars is not worth keeping
    except BTError as e:
        if e.kind == "gone":
            os.makedirs(cache_dir, exist_ok=True)
            with open(nf, "w", encoding="utf-8") as fh:
                fh.write(str(e) + "\n")
        raise
    write_cache(cache_dir, sym, today, text)
    return text, "yahoo"


# ---------------------------------------------------------------- bars

def parse_sessions(text, sym, now_utc):
    """Yahoo chart JSON -> {date: {slot: bar}}: regular-session 5-minute bars, slot 0 = the 09:15
    bar. Dropped: bars with a missing price, bars off the 5-minute grid (the live tick), bars not
    complete yet (now is the earlier of --now and the data's own regularMarketTime) and bars
    outside 09:15 to 15:30 India time. Prices are snapped to the paisa (Yahoo sends 2433.70 as
    2433.699951171875); a missing volume is 0."""
    result = chart_result(text, sym)
    meta = result.get("meta") or {}
    msym = nse_symbol(meta.get("symbol"))
    if msym and msym != nse_symbol(sym):
        raise BTError("the chart is for %s, not %s" % (msym, sym), "gone")
    gran = meta.get("dataGranularity")
    if gran and gran != "5m":
        raise BTError("the chart for %s has %s bars; the backtest needs 5-minute bars" % (sym, gran), "gone")
    stamps = result.get("timestamp") or []
    q = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    cols = [q.get(k) or [] for k in ("open", "high", "low", "close", "volume")]
    as_of = now_utc
    rmt = meta.get("regularMarketTime")
    if isinstance(rmt, (int, float)) and utc_of(rmt) < as_of:
        as_of = utc_of(rmt)
    days = {}
    for i, ts in enumerate(stamps):
        o, h, l, c, v = [col[i] if i < len(col) else None for col in cols]
        if ts is None or None in (o, h, l, c):
            continue
        ts = int(ts)
        if ts % INTERVAL:
            continue
        start = utc_of(ts)
        local = to_ist(start)
        mins = local.hour * 60 + local.minute - OPEN_MIN
        if mins < 0 or mins >= SESSION_BARS * 5:
            continue
        if start + datetime.timedelta(seconds=INTERVAL) > as_of:
            continue
        days.setdefault(local.date().isoformat(), {})[mins // 5] = {
            "o": round(o, 2), "h": round(h, 2), "l": round(l, 2), "c": round(c, 2), "v": int(v or 0)}
    return days


def is_full(day, last):
    """Every bar from 09:15 to the flat-by bar is there."""
    return all(i in day for i in range(last + 1))


def previous_close(days, date_text):
    """The close of the previous NSE session's last bar, when that session is in the data and
    its last bar starts at 15:00 or later. Else None: the gap cannot be measured."""
    prev = previous_trading_day(datetime.date.fromisoformat(date_text)).isoformat()
    day = days.get(prev)
    if not day:
        return None
    last = max(day)
    if OPEN_MIN + 5 * last < 15 * 60:
        return None
    return day[last]["c"]


# ---------------------------------------------------------------- the entry rules (the same as trader-entry)

def session_vwap(bars):
    """Running session VWAP after each bar: sum(typical price x volume) / sum(volume), typical
    price = (high + low + close) / 3. A bar without volume adds nothing. None while no bar so
    far has had any volume."""
    out, cum_v, cum_pv = [], 0.0, 0.0
    for b in bars:
        v = b.get("v") or 0
        if v > 0:
            cum_v += v
            cum_pv += (b["h"] + b["l"] + b["c"]) / 3.0 * v
        out.append(cum_pv / cum_v if cum_v > 0 else None)
    return out


def opening_range(day, n=OR_BARS):
    return max(day[i]["h"] for i in range(n)), min(day[i]["l"] for i in range(n))


def find_entry(setup, day, p, prev_close=None):
    """The entry bar of one setup in one session, from the bars up to and including it:
    ({"k": slot, "entry": close, "stop_raw": price, ...}, None) or (None, why not).
    `day` is {slot: bar}; p holds k_min, k_max (the entry window), min_bars_below, gap_pct."""
    k_min, k_max = p["k_min"], p["k_max"]
    if setup in ("orb", "gap"):
        if not all(i in day for i in range(OR_BARS)):
            return None, "no opening range"
        oh, ol = opening_range(day)
        info = {"or_high": oh, "or_low": ol}
        if setup == "gap":
            if prev_close is None:
                return None, "no previous close"
            pct = 100.0 * (day[0]["o"] / prev_close - 1)
            if pct < p["gap_pct"]:
                return None, "no gap"
            info.update(gap_pct=round(pct, 2), prev_close=prev_close)
        for k in range(max(OR_BARS, k_min), k_max + 1):
            b = day.get(k)
            if b is not None and b["c"] > oh:
                raw = (D(oh) + D(ol)) / 2 if setup == "orb" else D(ol)
                return dict(info, k=k, entry=b["c"], stop_raw=raw), None
        return None, "no trigger"
    if setup == "vwap":
        slots = [k for k in sorted(day) if k <= k_max]
        vw = session_vwap([day[k] for k in slots])
        run, run_low, prev = 0, None, None
        for k, v in zip(slots, vw):
            b = day[k]
            if prev is not None and k != prev + 1:
                run, run_low = 0, None            # a missing bar: its close is unknown, start again
            prev = k
            if v is None:
                run, run_low = 0, None
                continue
            if b["c"] > v:
                if run >= p["min_bars_below"] and k >= k_min:
                    return {"k": k, "entry": b["c"], "stop_raw": D(min(run_low, b["l"])), "vwap": round(v, 2),
                            "bars_below": run}, None
                run, run_low = 0, None
            else:
                run += 1
                run_low = b["l"] if run_low is None else min(run_low, b["l"])
        return None, ("no volume" if vw and vw[-1] is None else "no trigger")
    raise BTError("unknown setup %r (the setups are %s)" % (setup, ", ".join(SETUPS)))


def exit_plan(entry, stop_raw, target_r):
    """(stop, target, risk) as trader-exit sets them, or (None, why it refuses)."""
    e = paise(entry)
    stop = floor_paise(stop_raw)
    if stop >= e or stop <= 0:
        return None, "stop at or above the entry"
    risk = e - stop
    if risk < e * MIN_RISK_PCT / 100:
        return None, "stop too tight"
    return (stop, paise(e + D(target_r) * risk), risk), None


def walk(day, k, stop, target, last):
    """The bars after the entry bar, in order. The stop is checked first, so a bar that touches
    both counts as the stop. (outcome, exit price, exit slot, opened under the stop)."""
    for s in range(k + 1, last + 1):
        b = day[s]
        if b["l"] <= stop:
            return "stop", stop, s, b["o"] < stop
        if b["h"] >= target:
            return "target", target, s, False
    return "time", day[last]["c"], last, False


def simulate_session(setup, sym, date_text, day, p, rates, prev_close=None):
    """One setup on one full session -> (trade, None) or (None, why there is no trade)."""
    found, why = find_entry(setup, day, p, prev_close)
    if found is None:
        return None, why
    k = found["k"]
    if k >= p["last"]:
        return None, "no trigger"
    plan, why = exit_plan(found["entry"], found["stop_raw"], p["target_r"])
    if plan is None:
        return None, why
    stop, target, risk = plan
    entry = paise(found["entry"])
    qty = int(D(p["risk_inr"]) // risk)
    capped = False
    if p.get("max_order_inr"):
        cap = int(D(p["max_order_inr"]) // entry)
        if cap < qty:
            qty, capped = cap, True
    if qty < 1:
        return None, "under one share"
    outcome, exit_price, exit_slot, gapped = walk(day, k, float(stop), float(target), p["last"])
    c = round_trip(entry, exit_price, qty, rates)
    r = (c["sell"] - entry) / risk
    net_r = c["net"] / (risk * qty)
    trade = {"setup": setup, "symbol": sym, "date": date_text, "entry_time": slot_close(k), "entry_bar": k,
             "entry": float(entry), "stop": float(stop), "target": float(target), "qty": qty, "capped": capped,
             "outcome": outcome, "exit": float(c["sell"]),
             "exit_time": slot_close(exit_slot) if outcome == "time" else clock24(OPEN_MIN + 5 * exit_slot),
             "r": round(float(r), 4), "net_r": round(float(net_r), 4), "gross": _f(c["gross"]),
             "charges": _f(c["total"]), "net": _f(c["net"]), "opened_under_stop": gapped}
    return trade, None


def backtest_symbol(sym, days, p, setups, rates):
    """Every setup on every full session of one symbol. Returns (trades, counts)."""
    trades = []
    counts = {"sessions": 0, "left_out": 0, "no_open_volume": 0, "skips": {s: {} for s in setups}}
    for date_text in sorted(days):
        day = days[date_text]
        if not is_full(day, p["last"]):
            counts["left_out"] += 1
            continue
        counts["sessions"] += 1
        if not day[0]["v"]:
            counts["no_open_volume"] += 1
        pc = previous_close(days, date_text) if "gap" in setups else None
        for setup in setups:
            t, why = simulate_session(setup, sym, date_text, day, p, rates, pc)
            if t:
                trades.append(t)
            else:
                counts["skips"][setup][why] = counts["skips"][setup].get(why, 0) + 1
    return trades, counts


# ---------------------------------------------------------------- the numbers

def summarize(trades, min_trades=MIN_TRADES, top=5):
    """One setup's trades -> its scoreboard. A win is a net P&L (after charges) above zero. The
    losing run is counted in order of date, entry time, symbol."""
    rows = sorted(trades, key=lambda t: (t["date"], t["entry_time"], t["symbol"]))
    n = len(rows)
    out = {"trades": n, "enough": n >= min_trades, "min_trades": min_trades}
    if not n:
        return out
    gross = sum((D(t["gross"]) for t in rows), Decimal(0))
    charges = sum((D(t["charges"]) for t in rows), Decimal(0))
    net = sum((D(t["net"]) for t in rows), Decimal(0))
    wins = sum(1 for t in rows if t["net"] > 0)
    losses = sum(1 for t in rows if t["net"] < 0)
    run = longest = 0
    for t in rows:
        run = run + 1 if t["net"] < 0 else 0
        longest = max(longest, run)
    worst = min(rows, key=lambda t: t["net"])
    ends = {o: sum(1 for t in rows if t["outcome"] == o) for o in ("target", "stop", "time")}
    periods = {}
    for t in rows:
        key, label = fortnight(t["date"])
        g = periods.setdefault(key, {"label": label, "trades": 0, "wins": 0, "r": 0.0, "net_r": 0.0, "net": Decimal(0)})
        g["trades"] += 1
        g["wins"] += 1 if t["net"] > 0 else 0
        g["r"] += t["r"]
        g["net_r"] += t["net_r"]
        g["net"] += D(t["net"])
    by_sym = {}
    for t in rows:
        g = by_sym.setdefault(t["symbol"], {"symbol": t["symbol"], "trades": 0, "wins": 0, "net_r": 0.0,
                                            "net": Decimal(0)})
        g["trades"] += 1
        g["wins"] += 1 if t["net"] > 0 else 0
        g["net_r"] += t["net_r"]
        g["net"] += D(t["net"])
    syms = [{"symbol": g["symbol"], "trades": g["trades"], "wins": g["wins"],
             "avg_net_r": round(g["net_r"] / g["trades"], 2), "net": _f(g["net"])} for g in by_sym.values()]
    best = sorted(syms, key=lambda g: (-g["net"], g["symbol"]))[:top]
    worst_syms = sorted(syms, key=lambda g: (g["net"], g["symbol"]))[:top]
    out.update({
        "symbols": len(by_sym), "from": rows[0]["date"], "to": rows[-1]["date"],
        "wins": wins, "losses": losses, "win_rate": round(100.0 * wins / n, 1),
        "avg_r": round(sum(t["r"] for t in rows) / n, 3) + 0.0,
        "avg_net_r": round(sum(t["net_r"] for t in rows) / n, 3) + 0.0,
        "gross": _f(gross), "charges": _f(charges), "net": _f(net),
        "avg_charges": _f(charges / n),
        "largest_loss": ({"net": worst["net"], "symbol": worst["symbol"], "date": worst["date"],
                          "entry_time": worst["entry_time"]} if worst["net"] < 0 else None),
        "longest_losing_run": longest,
        "ended_by": {o: {"trades": ends[o], "pct": round(100.0 * ends[o] / n, 1)} for o in ends},
        "capped_by_max_order": sum(1 for t in rows if t["capped"]),
        "opened_under_stop": sum(1 for t in rows if t["opened_under_stop"]),
        "by_fortnight": [{"label": g["label"], "trades": g["trades"],
                          "win_rate": round(100.0 * g["wins"] / g["trades"], 1),
                          "avg_r": round(g["r"] / g["trades"], 3) + 0.0,
                          "avg_net_r": round(g["net_r"] / g["trades"], 3) + 0.0, "net": _f(g["net"])}
                         for _, g in sorted(periods.items())],
        "best_symbols": best, "worst_symbols": worst_syms,
    })
    return out


def rmult(x):
    """+0.18R, -0.21R; never -0.00R."""
    return "%+.2fR" % (round(float(x), 2) + 0.0)


def few_line(n, min_trades):
    return ("Only %d trade%s: too few trades to conclude anything. Under %d, a good or a bad result is mostly luck."
            % (n, "" if n == 1 else "s", min_trades))


def setup_title(setup, params):
    name = SETUP_NAMES[setup]
    if setup == "vwap":
        return name % params["min_bars_below"]
    if setup == "gap":
        return name % ("%g" % params["gap_pct"])
    return name


def setup_lines(setup, s, params):
    lines = [setup_title(setup, params)]
    n = s["trades"]
    if not n:
        lines.append("  No trades. " + few_line(0, s["min_trades"]))
        return lines
    if not s["enough"]:
        lines.append("  " + few_line(n, s["min_trades"]))
    lines.append("  Trades               %s in %d symbol%s, %s to %s" % (
        format(n, ","), s["symbols"], "" if s["symbols"] == 1 else "s", s["from"], s["to"]))
    lines.append("  Win rate             %.1f %% after charges (a win is a net P&L above zero)" % s["win_rate"])
    lines.append("  Average R            %s before charges, %s after" % (rmult(s["avg_r"]), rmult(s["avg_net_r"])))
    lines.append("  Net P&L              %s (gross %s, charges %s, about %s a trade)" % (
        signed(s["net"]), signed(s["gross"]), money(s["charges"]), money(s["avg_charges"])))
    ll = s["largest_loss"]
    lines.append("  Largest loss         %s" % ("%s (%s, %s at %s)" % (money(ll["net"]), ll["symbol"], ll["date"],
                                                                      ll["entry_time"]) if ll else "none"))
    lines.append("  Longest losing run   %d trade%s in a row (in order of date, time, symbol)" % (
        s["longest_losing_run"], "" if s["longest_losing_run"] == 1 else "s"))
    e = s["ended_by"]
    lines.append("  Ended by             target %.1f %%, stop %.1f %%, time (flat-by) %.1f %%" % (
        e["target"]["pct"], e["stop"]["pct"], e["time"]["pct"]))
    lines.append("  By fortnight:")
    for g in s["by_fortnight"]:
        lines.append("    %-15s %6s trade%s  win %5.1f %%  %s after charges  net %s%s" % (
            g["label"], format(g["trades"], ","), " " if g["trades"] == 1 else "s", g["win_rate"], rmult(g["avg_net_r"]),
            signed(g["net"]), "" if g["trades"] >= s["min_trades"] else "  (too few to read)"))
    for title, key in (("Best symbols", "best_symbols"), ("Worst symbols", "worst_symbols")):
        if s["symbols"] < 2:
            break
        lines.append("  %s by net P&L (a handful of trades each: mostly luck, not a pattern):" % title)
        for g in s[key]:
            lines.append("    %-12s %3d trade%s  net %s" % (g["symbol"], g["trades"], " " if g["trades"] == 1 else "s",
                                                           signed(g["net"])))
    if s["opened_under_stop"]:
        lines.append("  %s stopped trade%s met a bar that OPENED under the stop: counted at the stop price here, "
                     "worse in real life." % (format(s["opened_under_stop"], ","),
                                              "" if s["opened_under_stop"] == 1 else "s"))
    return lines


def report_lines(doc):
    p, d, u = doc["params"], doc["data"], doc["universe"]
    lines = ["Backtest on Yahoo's 5-minute bars (free, unofficial, NSE symbols), as of %s IST" % doc["as_of"]]
    lim = (", the first %d" % u["limit"]) if u.get("limit") else ""
    lines.append("Universe: %s%s: %d symbol%s asked, %d with usable bars, %d failed%s." % (
        u["source"], lim, u["asked"], "" if u["asked"] == 1 else "s", d["symbols_ok"], len(d["failed"]),
        (", %d not fetched" % d["not_fetched"]) if d["not_fetched"] else ""))
    if d["failed"]:
        shown = d["failed"][:8]
        lines.append("  Failed: %s%s." % ("; ".join("%s (%s)" % (f["symbol"], f["why"]) for f in shown),
                                          "; and %d more" % (len(d["failed"]) - len(shown))
                                          if len(d["failed"]) > len(shown) else ""))
    if not doc["complete"]:
        lines.append("INCOMPLETE: %s Run the same command again: it picks up from the cache." % doc["stopped"])
    if d["sessions"]:
        lines.append("Sessions: %s symbol-days from %s to %s; %s more left out (a bar missing between 9:15 and %s)." % (
            format(d["sessions"], ","), d["first_session"], d["last_session"], format(d["left_out"], ","),
            p["flat_by"]))
        if d["no_open_volume"]:
            lines.append("  In %s of them the 9:15 bar came with no volume (a quirk of Yahoo's 60-day data): the "
                         "VWAP there starts at the 9:20 bar, so it is not quite the VWAP trader-entry works out "
                         "live from today's bars." % format(d["no_open_volume"], ","))
    lines.append("Requests: %d this run, %d symbol%s read from %s (%s)." % (
        d["requests"], d["from_cache"], "" if d["from_cache"] == 1 else "s",
        "saved files" if d.get("offline") else "today's cache", d["cache"]))
    lines.append("Rules: in at the trigger bar's close (%s to %s), stop and target as trader-exit sets them "
                 "(%gR target), out at the close of the last bar before %s. A bar that touches both the stop and "
                 "the target counts as the stop." % (p["no_entries_before"], p["no_entries_after"], p["target_r"],
                                                    p["flat_by"]))
    cap = ("no order above %s" % money(p["max_order_inr"])) if p["max_order_inr"] else "no cap on the order size"
    lines.append("Sizing: %s at risk a trade, whole shares, %s. Charges: %s%s." % (
        money(p["risk_inr"]), cap, CHARGES_SOURCE,
        (", with these lines from your config: %s" % ", ".join(p["charges_overridden"]))
        if p["charges_overridden"] else ""))
    for setup in p["setups"]:
        s = doc["setups"][setup]
        lines.append("")
        lines += setup_lines(setup, s, p)
        if s["trades"] and s.get("capped_by_max_order") and p["max_order_inr"]:
            lines.append("  %s of these trades %s cut by the %s order limit, so %s less than %s." % (
                format(s["capped_by_max_order"], ","), "was" if s["capped_by_max_order"] == 1 else "were",
                money(p["max_order_inr"]), "it risked" if s["capped_by_max_order"] == 1 else "they risked",
                money(p["risk_inr"])))
        sk = d["skips"].get(setup) or {}
        if sk:
            lines.append("  No trade: %s." % ", ".join("%s %s" % (format(v, ","), k) for k, v in sorted(sk.items())))
    lines.append("")
    lines.append("Read this before the numbers above:")
    for c in doc["caveats"]:
        lines.append("  - " + c)
    lines.append(FINE_PRINT)
    return lines


# ---------------------------------------------------------------- universe

def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise BTError("%s is not valid JSON: %s" % (path, e))


def parse_symbol_list(text):
    """NSE's index CSV (Company Name,Industry,Symbol,Series,ISIN Code), or plain lines of
    SYMBOL / SYMBOL|Name / SYMBOL,anything. Order kept, repeats dropped. From the CSV only
    series EQ with a real ISIN is kept: a stock in series BE cannot be traded intraday, and
    NSE's placeholder rows are not stocks."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    out = []
    if lines and "symbol" in [h.strip().lower() for h in lines[0].split(",")]:
        rows = list(csv.reader(io.StringIO(text)))
        hdr = [h.strip().lower() for h in rows[0]]
        i_s = hdr.index("symbol")
        i_ser = hdr.index("series") if "series" in hdr else None
        i_isin = hdr.index("isin code") if "isin code" in hdr else None
        for r in rows[1:]:
            if len(r) <= i_s or (i_ser is not None and len(r) > i_ser and r[i_ser].strip().upper() != "EQ"):
                continue
            if i_isin is not None and len(r) > i_isin and not r[i_isin].strip().upper().startswith("IN"):
                continue
            out.append(r[i_s])
    else:
        for ln in lines:
            if ln.lstrip().startswith("#"):
                continue
            out.append(re.split(r"[|,\s]", ln.strip())[0])
    seen, syms = set(), []
    for s in out:
        s = nse_symbol(s)
        if SYMBOL_RE.match(s) and s not in seen:
            seen.add(s)
            syms.append(s)
    return syms


def nifty500(cache_dir, today, net, fetch=True, pause=PAUSE):
    """NSE's published Nifty 500 list: today's cached copy, else one plain GET (no retry)."""
    path = os.path.join(cache_dir, "ind_nifty500list-%s.csv" % today)
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            syms = parse_symbol_list(fh.read())
        if syms:
            return syms
    if not fetch:
        raise BTError("the Nifty 500 list is not in today's cache (--no-fetch): pass --universe FILE")
    try:
        text = polite_get(NIFTY500_URL, net, pause)
    except urllib.error.HTTPError as e:
        code = e.code
        e.close()
        raise BTError("could not get the Nifty 500 list from NSE (HTTP %d). Save ind_nifty500list.csv yourself "
                      "and pass --universe FILE." % code)
    except (urllib.error.URLError, OSError) as e:
        raise BTError("could not get the Nifty 500 list from NSE (%s). Save ind_nifty500list.csv yourself and "
                      "pass --universe FILE." % (getattr(e, "reason", e),))
    syms = parse_symbol_list(text)
    if len(syms) < 100:
        raise BTError("NSE's answer does not look like the Nifty 500 list (it starts %r)" % text.strip()[:60])
    os.makedirs(cache_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return syms


def resolve_universe(a, today, net):
    """(symbols, where they came from)."""
    if a.symbols and a.universe:
        raise BTError("name the symbols or give --universe, not both")
    if a.symbols:
        syms, src = [clean_symbol(s) for s in a.symbols], "the symbols you named"
    elif not a.universe:
        raise BTError("which stocks? Name them (backtest.py run TCS INFY), or --universe watchlist, "
                      "--universe nifty500 or --universe FILE")
    elif a.universe.lower() == "nifty500":
        syms = nifty500(a.cache, today, net, not a.no_fetch and not a.offline, a.pause)
        src = "the Nifty 500 as NSE lists it today"
    elif a.universe.lower() == "watchlist":
        if not os.path.exists(a.watchlist):
            raise BTError("no watchlist at %s: run /trader-scanner first, or name the symbols" % a.watchlist)
        doc = read_json(a.watchlist, "watchlist")
        hits = doc.get("hits") if isinstance(doc, dict) else None
        syms = [nse_symbol(h["symbol"]) for h in (hits or []) if isinstance(h, dict) and h.get("symbol")]
        syms = [s for s in syms if SYMBOL_RE.match(s)]
        src = "your watchlist (%s)" % a.watchlist.replace("\\", "/")
    else:
        if not os.path.exists(a.universe):
            raise BTError("--universe is nifty500, watchlist or a file of symbols; there is no file %s" % a.universe)
        with open(a.universe, encoding="utf-8", errors="replace") as fh:
            syms = parse_symbol_list(fh.read())
        src = "your list (%s)" % a.universe.replace("\\", "/")
    seen, out = set(), []
    for s in syms:
        if s not in seen:
            seen.add(s)
            out.append(s)
    if not out:
        raise BTError("no symbols to test in %s" % src)
    return out, src


# ---------------------------------------------------------------- the run

def load_config(path):
    cfg = {}
    if path and os.path.exists(path):
        cfg = read_json(path, "config")
        if not isinstance(cfg, dict):
            raise BTError("%s is not a config object" % path)
    return cfg


def build_params(a, cfg):
    def pick(flag, key):
        v = getattr(a, flag)
        return v if v is not None else (cfg.get(key) if cfg.get(key) is not None else DEFAULTS[key])
    setups = [s.strip().lower() for s in str(a.setups).split(",") if s.strip()]
    for s in setups:
        if s not in SETUPS:
            raise BTError("--setups takes %s (got %r)" % (", ".join(SETUPS), s))
    if not setups:
        raise BTError("--setups takes %s" % ", ".join(SETUPS))
    before = parse_hhmm(cfg.get("no_entries_before") or DEFAULTS["no_entries_before"], "no_entries_before")
    after = parse_hhmm(cfg.get("no_entries_after") or DEFAULTS["no_entries_after"], "no_entries_after")
    flat = parse_hhmm(pick("flat_by", "flat_by"), "flat_by")
    if (flat - OPEN_MIN) % 5 or not OPEN_MIN + 30 <= flat <= CLOSE_MIN:
        raise BTError("flat_by must be on the 5-minute grid, between 09:45 and 15:30")
    risk = float(pick("risk", "max_loss_per_trade_inr"))
    max_order = float(pick("max_order", "max_order_inr"))
    if not risk > 0:
        raise BTError("--risk must be above 0 rupees")
    if max_order < 0 or a.target_r <= 0 or a.gap_pct <= 0 or a.min_bars_below < 1 or a.min_trades < 1:
        raise BTError("--max-order is 0 or more; --target-r, --gap-pct, --min-bars-below and --min-trades are above 0")
    last = (flat - OPEN_MIN) // 5 - 1
    k_min = max(0, -(-(before - OPEN_MIN) // 5) - 1)
    k_max = min((after - OPEN_MIN) // 5 - 1, last - 1)
    rates, changed = load_charges(cfg)
    params = {"setups": setups, "risk_inr": risk, "max_order_inr": max_order, "target_r": float(a.target_r),
              "gap_pct": float(a.gap_pct), "min_bars_below": int(a.min_bars_below), "min_trades": int(a.min_trades),
              "no_entries_before": clock24(before), "no_entries_after": clock24(after), "flat_by": clock24(flat),
              "or_minutes": 5 * OR_BARS, "last": last, "k_min": k_min, "k_max": k_max,
              "charges_overridden": changed}
    return params, rates


def run_backtest(symbols, params, rates, now_utc, cache_dir, offline=None, fetch=True, pause=PAUSE,
                 progress=None, net=None):
    """Fetch (or read) each symbol, simulate, and add it all up. Returns (data summary, trades)."""
    today = to_ist(now_utc).date().isoformat()
    net = net if net is not None else {}
    data = {"symbols_ok": 0, "failed": [], "not_fetched": 0, "from_cache": 0, "from_yahoo": 0,
            "sessions": 0, "left_out": 0, "no_open_volume": 0, "first_session": None, "last_session": None,
            "skips": {s: {} for s in params["setups"]}, "cache": (offline or cache_dir).replace("\\", "/"),
            "offline": bool(offline)}
    trades, rate_fails, stopped = [], 0, None
    total = len(symbols)
    for i, sym in enumerate(symbols):
        note = ""
        try:
            text, src = get_text(sym, today, cache_dir, net, offline, fetch, pause)
            rate_fails = 0
            days = parse_sessions(text, sym, now_utc)
            got, counts = backtest_symbol(sym, days, params, params["setups"], rates)
            if not counts["sessions"]:
                raise BTError("no full session in Yahoo's data for %s" % sym, "gone-cached")
            data["symbols_ok"] += 1
            data["from_cache" if src != "yahoo" else "from_yahoo"] += 1
            for k in ("sessions", "left_out", "no_open_volume"):
                data[k] += counts[k]
            for s in params["setups"]:
                for why, n in counts["skips"][s].items():
                    data["skips"][s][why] = data["skips"][s].get(why, 0) + n
            full = sorted(d for d in days if is_full(days[d], params["last"]))
            data["first_session"] = min(filter(None, [data["first_session"], full[0]]))
            data["last_session"] = max(filter(None, [data["last_session"], full[-1]]))
            trades += got
            note = "%s  %d sessions, %d trades" % (src, counts["sessions"], len(got))
        except BTError as e:
            if e.kind == "error":
                raise
            if e.kind == "skipped":
                data["not_fetched"] += 1
            else:
                data["failed"].append({"symbol": sym, "why": str(e)})
            note = "FAILED  %s" % e
            if e.kind == "rate":
                rate_fails += 1
                if rate_fails >= MAX_RATE_FAILS:
                    stopped = ("Yahoo rate limited %d symbols in a row, so the run stopped at %s (%d of %d)."
                               % (rate_fails, sym, i + 1, total))
            else:
                rate_fails = 0
        if progress:
            progress(i + 1, total, sym, note)
        if stopped:
            data["not_fetched"] += total - i - 1
            break
    data["requests"] = net.get("requests", 0)
    data["stopped"] = stopped
    return data, trades


def build_doc(symbols, source, limit, params, data, trades, now_utc, keep_trades=False):
    doc = {
        "created_by": "trader-backtest",
        "created_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": to_ist(now_utc).strftime("%Y-%m-%d %H:%M"),
        "complete": not data["stopped"] and not data["not_fetched"],
        "stopped": data["stopped"] or ("%d symbols were not in the cache and were not fetched." % data["not_fetched"]
                                       if data["not_fetched"] else None),
        "universe": {"source": source, "asked": len(symbols), "limit": limit},
        "params": {k: v for k, v in params.items() if k not in ("last", "k_min", "k_max")},
        "data": {k: v for k, v in data.items() if k != "stopped"},
        "charges_source": CHARGES_SOURCE,
        "setups": {s: summarize([t for t in trades if t["setup"] == s], params["min_trades"])
                   for s in params["setups"]},
        "assumptions": [
            "A signal on a bar's close enters at that close; nothing after the entry bar decides the entry.",
            "The stop and the target fill at their exact prices; a bar that touches both counts as the stop.",
            "No stop and no target by flat_by: out at the close of the last bar before it.",
            "A session with a bar missing between 09:15 and flat_by is left out for that symbol.",
            "The gap is measured from the previous session's last 5-minute bar, not NSE's official close.",
            "Each trade is sized alone from the fixed rupee risk and the order limit; trades are not netted "
            "against one account, a daily loss limit or a maximum number of trades a day.",
        ],
        "caveats": list(CAVEATS),
        "note": FINE_PRINT,
    }
    if keep_trades:
        doc["trades"] = sorted(trades, key=lambda t: (t["setup"], t["date"], t["entry_time"], t["symbol"]))
    return doc


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def cmd_run(a):
    now = parse_now(a.now)
    today = to_ist(now).date().isoformat()
    cfg = load_config(a.config)
    params, rates = build_params(a, cfg)
    if a.pause < PAUSE:
        raise BTError("--pause cannot be under %.1f seconds: be polite to a free endpoint" % PAUSE)
    if a.offline and not os.path.isdir(a.offline):
        raise BTError("--offline wants a folder of <SYMBOL>-60d5m.json(.gz) files")
    net = {}
    symbols, source = resolve_universe(a, today, net)
    asked = len(symbols)
    limit = None
    if a.limit is not None:
        if a.limit < 1:
            raise BTError("--limit must be 1 or more")
        if a.limit < asked:
            symbols, limit = symbols[:a.limit], a.limit

    def progress(i, total, sym, note):
        if a.quiet:
            return
        if total <= 60 or i % 25 == 0 or i == total or note.startswith("FAILED"):
            print("[%*d/%d] %-12s %s" % (len(str(total)), i, total, sym, note), file=sys.stderr, flush=True)

    if not a.quiet and not a.offline:
        print("%d symbol%s; anything not in %s for %s is one request to Yahoo, %.1f s apart."
              % (len(symbols), "" if len(symbols) == 1 else "s", a.cache.replace("\\", "/"), today, a.pause),
              file=sys.stderr, flush=True)
    data, trades = run_backtest(symbols, params, rates, now, a.cache, a.offline, not a.no_fetch, a.pause,
                                progress, net)
    doc = build_doc(symbols, source, limit, params, data, trades, now, a.trades)
    if limit:
        doc["universe"]["listed"] = asked
    if not data["symbols_ok"]:
        for f in data["failed"][:5]:
            print("backtest.py: %s" % f["why"], file=sys.stderr)
        raise BTError("no symbol returned usable bars: nothing to test")
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(doc, indent=1, ensure_ascii=False))
    else:
        print("\n".join(report_lines(doc)))
        if not a.no_write:
            print("Wrote %s." % a.out.replace("\\", "/"))
    return 0 if doc["complete"] else 1


def cmd_show(a):
    if not os.path.exists(a.out):
        raise BTError("no %s yet: run backtest.py run first" % a.out)
    doc = read_json(a.out, "backtest")
    if not isinstance(doc, dict) or doc.get("created_by") != "trader-backtest":
        raise BTError("%s is not a backtest result" % a.out)
    if a.json:
        print(json.dumps(doc, indent=1, ensure_ascii=False))
    else:
        print("\n".join(report_lines(doc)))
    return 0


def main(argv=None):
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Backtest (trader-backtest, NSE): the pack's setups on past "
                                             "sessions, after charges.")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("run", help="test the setups on every past session in Yahoo's 60 days")
    p.add_argument("symbols", nargs="*", help="NSE symbols, e.g. TCS INFY M&M")
    p.add_argument("--universe", help="nifty500, watchlist, or a file of symbols (one a line, or NSE's index CSV)")
    p.add_argument("--limit", type=int, help="only the first N symbols of the list")
    p.add_argument("--setups", default=",".join(SETUPS), help="orb,vwap,gap (default: all three)")
    p.add_argument("--risk", type=float, help="rupees at risk a trade (default: max_loss_per_trade_inr, 500)")
    p.add_argument("--max-order", type=float, help="largest order in rupees (default: max_order_inr, 40000; 0 = no cap)")
    p.add_argument("--target-r", type=float, default=2.0, help="target as a multiple of the risk (default 2)")
    p.add_argument("--gap-pct", type=float, default=1.0, help="gap: percent above the previous close (default 1)")
    p.add_argument("--min-bars-below", type=int, default=3, help="vwap: bars at or under VWAP first (default 3)")
    p.add_argument("--flat-by", help="HH:MM India time (default: flat_by in the config, 15:00)")
    p.add_argument("--min-trades", type=int, default=MIN_TRADES,
                   help="under this many trades a setup says it proves nothing (default %d)" % MIN_TRADES)
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--watchlist", default="trader/watchlist.json")
    p.add_argument("--cache", default=os.path.join("trader", "cache"), help="where the day's bars are kept")
    p.add_argument("--offline", metavar="DIR", help="read <SYMBOL>-60d5m.json(.gz) from this folder, no network")
    p.add_argument("--no-fetch", action="store_true", help="use only what is already in today's cache")
    p.add_argument("--pause", type=float, default=PAUSE, help="seconds between requests (0.5 or more)")
    p.add_argument("--out", default=os.path.join("trader", "backtest.json"))
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--trades", action="store_true", help="also save every simulated trade in the JSON")
    p.add_argument("--json", action="store_true")
    p.add_argument("--quiet", action="store_true", help="no progress lines")
    p.add_argument("--now", help='pretend it is this India time (IST), e.g. "2026-11-24 10:00"')
    p = sub.add_parser("show", help="print the saved result again")
    p.add_argument("--out", default=os.path.join("trader", "backtest.json"))
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"run": cmd_run, "show": cmd_show}[a.cmd](a)
    except BTError as e:
        print("backtest.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("backtest.py: stopped. Run it again: it picks up from the cache.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
