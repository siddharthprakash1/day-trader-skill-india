"""trader-entry tests (India, NSE). Offline: Yahoo is never called.

Fixtures in tests/fixtures/trader-entry/, all saved from Yahoo's chart API on
2026-11-24 (range=1d, the session of Monday 2026-11-23), unedited:

TCS-2026-11-23-5m.json       TCS.NS five-minute bars: 75 bars 09:15-15:25 IST, the
                             15:20 bar came as nulls. Breaks its opening range at 9:45.
TCS-2026-11-23-1m.json       TCS.NS one-minute bars for the same session.
RELIANCE-2026-11-23-5m.json  RELIANCE.NS: never closes above its opening-range high.
M&M-2026-11-23-5m.json       M&M.NS: a symbol with an ampersand.
DEMO-1m-synthetic.json       SYNTHETIC one-minute bars (not market data): a wick above
                             the range high, a close AT the high, a null bar, a live tick.

Mid-session answers (an in-progress bar, a live tick, Yahoo lagging) are cut from the
real files by snapshot() below.
"""

import copy
import datetime
import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

entry = load("trader-entry", "entry")

EDIR = os.path.join(FIX, "trader-entry")
TCS = os.path.join(EDIR, "TCS-2026-11-23-5m.json")
TCS1M = os.path.join(EDIR, "TCS-2026-11-23-1m.json")
REL = os.path.join(EDIR, "RELIANCE-2026-11-23-5m.json")
MM = os.path.join(EDIR, "M&M-2026-11-23-5m.json")
DEMO = os.path.join(EDIR, "DEMO-1m-synthetic.json")
OPEN_TS = 1795405500       # 2026-11-23 09:15 IST = 03:45 UTC
DT = datetime.datetime
RS = "₹"

SAFETY = """## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Nothing in this pack places an order. Claude never logs in to a broker and never sends an order:
  the user enters every order themselves in their own broker app, after trader-risk passes that
  exact order and they have said an explicit yes to it in the chat.
- Practice mode by default (`"paper": true`): write the trade down first, risk nothing.
- Long side, cash-segment equity only. No F&O, margin (MTF) or short selling unless the config
  allows it and the user confirms.
- Broker passwords, PINs and OTPs never go in the chat.
"""


def ist(text):
    return entry.parse_now(text)


def doc(path=TCS):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def chart(d=None, sym="TCS"):
    return entry.parse_chart(json.dumps(d if d is not None else doc()), sym)


def at(text, d=None, sym="TCS", **kw):
    return entry.evaluate(chart(d, sym), ist(text), **kw)


def slot_ts(hhmm):
    h, m = map(int, hhmm.split(":"))
    return OPEN_TS + ((h - 9) * 60 + m - 15) * 60


def snapshot(d, last_trade_ts, tick=True):
    """The same chart the way Yahoo serves it mid-session: bars that started by the last
    trade, a live tick at the last trade when it is off the grid, and meta's time set to it."""
    d = copy.deepcopy(d)
    r = d["chart"]["result"][0]
    keep = [i for i, t in enumerate(r["timestamp"]) if t <= last_trade_ts]
    q = r["indicators"]["quote"][0]
    for k in q:
        q[k] = [q[k][i] for i in keep]
    r["timestamp"] = [r["timestamp"][i] for i in keep]
    if tick and last_trade_ts % 300:
        px = q["close"][-1]
        r["timestamp"].append(last_trade_ts)
        for k in ("open", "high", "low", "close"):
            q[k].append(px)
        q["volume"].append(0)
    r["meta"]["regularMarketTime"] = last_trade_ts
    return d


def shift_to(d, day):
    """The same session moved to another date. 09:15 IST is 03:45 UTC every day of the year."""
    d = copy.deepcopy(d)
    r = d["chart"]["result"][0]
    new_open = int((DT.combine(day, datetime.time(3, 45)) - DT(1970, 1, 1)).total_seconds())
    delta = new_open - OPEN_TS
    r["timestamp"] = [t + delta for t in r["timestamp"]]
    r["meta"]["regularMarketTime"] += delta
    return d


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = entry.main(argv)
    return code, out.getvalue(), err.getvalue()


class TheBreakout(unittest.TestCase):
    """TCS, Monday 2026-11-23: range 2,405.00 to 2,433.70, the 9:40 bar closes at 2,443.50."""

    def test_triggers_at_0945_on_the_0940_bar(self):
        r = at("2026-11-23 09:45")
        self.assertEqual(r["status"], "triggered")
        self.assertEqual(r["symbol"], "TCS")                       # Yahoo's TCS.NS, without the suffix
        self.assertEqual(r["opening_range"]["high"], 2433.7)
        self.assertEqual(r["opening_range"]["low"], 2405.0)
        self.assertEqual(r["opening_range"]["bars"], 3)
        t = r["trigger"]
        self.assertEqual(t["time"], "9:45 AM")
        self.assertEqual(t["bar_start"], "09:40")
        self.assertEqual(t["bar_index"], 5)
        self.assertEqual(t["price"], 2443.5)
        self.assertEqual(t["close"], 2443.5)
        self.assertEqual(t["pct_over"], 0.4)
        self.assertEqual(r["level"], 2433.7)
        self.assertEqual(r["prev_close"], 2376.2)

    def test_at_0942_the_0940_bar_is_still_forming(self):
        r = at("2026-11-23 09:42")
        self.assertEqual(r["status"], "waiting")
        self.assertIsNone(r["trigger"])
        self.assertEqual(r["last"]["bar_start"], "09:35")
        self.assertEqual(r["last"]["close"], 2429.0)
        self.assertEqual(r["forming"]["bar_start"], "09:40")
        self.assertIn("%s4.70 under the range high %s2,433.70" % (RS, RS), r["reason"])
        self.assertEqual(at("2026-11-23 09:44:59")["status"], "waiting")

    def test_before_the_open_and_while_the_range_forms(self):
        r = at("2026-11-23 09:10")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "pre-open"))
        self.assertIn("the NSE opens at 9:15", r["reason"])
        self.assertIn("NOT OPEN YET", "\n".join(entry.describe(r)))
        r = at("2026-11-23 09:14:59")
        self.assertEqual(r["phase"], "pre-open")
        r = at("2026-11-23 09:15")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "forming"))
        r = at("2026-11-23 09:29")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "forming"))
        self.assertEqual(r["opening_range"]["bars"], 2)
        self.assertFalse(r["opening_range"]["complete"])
        self.assertIn("RANGE FORMING", "\n".join(entry.describe(r)))
        r = at("2026-11-23 09:31")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "open"))
        self.assertEqual(r["opening_range"]["bars"], 3)
        self.assertTrue(r["opening_range"]["complete"])

    def test_the_live_tick_and_the_in_progress_bar_are_dropped(self):
        tick = slot_ts("10:45") + 4 * 60 + 45                      # Yahoo's last trade: 10:49:45
        d = snapshot(doc(), tick)
        c = chart(d)
        self.assertEqual(c["bars"][-1]["t"], tick)                 # the tick is in the answer
        r = entry.evaluate(c, entry.utc_of(c["data_time"]))        # read as of the answer's own time
        self.assertEqual(r["now"], "2026-11-23 10:49:45")
        self.assertEqual(r["_bars"][-1]["slot"], 17)               # the 10:40 bar
        self.assertEqual(r["forming"]["bar_start"], "10:45")
        self.assertEqual(r["bars_closed"], 18)
        # 10:50: closed by the clock, but Yahoo's last trade (10:49:45) is before its close
        r = at("2026-11-23 10:50", d)
        self.assertEqual(r["_bars"][-1]["slot"], 17)
        self.assertIn("Yahoo's last trade is 10:49:45", r["lag_note"])
        # a minute later with no newer trade it counts; the 10:49:45 tick never becomes a bar
        r = at("2026-11-23 10:51", d)
        self.assertEqual(r["_bars"][-1]["slot"], 18)
        self.assertEqual(r["bars_closed"], 19)
        self.assertTrue(all(b["t"] % 300 == 0 for b in r["_bars"]))

    def test_after_the_close_it_stays_triggered_and_says_it_is_late(self):
        r = at("2026-11-23 15:35")
        self.assertEqual((r["status"], r["phase"]), ("triggered", "after-close"))
        self.assertEqual(r["trigger"]["time"], "9:45 AM")
        text = "\n".join(entry.describe(r))
        self.assertIn("minutes ago", text)
        self.assertNotIn("This is the moment", text)

    def test_words_are_in_rupees_and_india_time(self):
        text = "\n".join(entry.describe(at("2026-11-23 09:45")))
        self.assertIn("TCS  Mon 2026-11-23 09:45 IST", text)
        self.assertIn("Opening range 9:15-9:30: high %s2,433.70, low %s2,405.00 (3 of 3 bars)." % (RS, RS), text)
        self.assertIn("TRIGGERED 9:45 AM at %s2,443.50: the 9:40 bar (bar 5) closed above the range high "
                      "%s2,433.70 (+0.40%%)." % (RS, RS), text)
        self.assertIn("Nothing was placed", text)
        self.assertNotIn("$", text)
        self.assertNotIn(" ET", text)


class Rules(unittest.TestCase):
    def test_volume_confirmation_moves_the_trigger(self):
        r = at("2026-11-23 10:40", min_volume_x=1.5)
        self.assertEqual(r["opening_range"]["avg_volume"], 138535)
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_index"], t["price"]), ("10:10 AM", 10, 2480.3))
        self.assertEqual(t["volume_x"], 2.05)
        r = at("2026-11-23 09:46", min_volume_x=1.5)
        self.assertEqual(r["status"], "waiting")
        self.assertIn("1.33x the range's volume, under your 1.50x", r["reason"])

    def test_level_setup(self):
        r = at("2026-11-23 10:30", setup="level", level=2460)
        self.assertEqual(r["trigger"]["time"], "10:05 AM")
        self.assertEqual(r["trigger"]["bar_index"], 9)
        self.assertEqual(r["trigger"]["price"], 2464.8)
        # a level under the open: the first eligible close is the 9:25 bar's, at 9:30 (no_entries_before)
        r = at("2026-11-23 10:30", setup="level", level=2400)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:30 AM", 2))
        with self.assertRaises(entry.EntryError):
            at("2026-11-23 10:30", setup="level")

    def test_no_breakout_waits_then_expires_at_the_cutoff(self):
        d = doc(REL)                                               # RELIANCE never closed above 1,389.90
        r = at("2026-11-23 11:00", d, "RELIANCE")
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["level"], 1389.9)
        self.assertEqual(at("2026-11-23 14:29", d, "RELIANCE")["status"], "waiting")
        r = at("2026-11-23 14:30", d, "RELIANCE")
        self.assertEqual((r["status"], r["phase"]), ("expired", "after-cutoff"))
        self.assertIn("by 14:30", r["reason"])
        self.assertEqual(r["no_entries_after"], "14:30")
        self.assertEqual(at("2026-11-23 15:45", d, "RELIANCE")["phase"], "after-close")

    def test_the_bar_closing_at_the_cutoff_still_counts_when_yahoo_lags(self):
        d = snapshot(doc(REL), slot_ts("10:40") + 4 * 60 + 40, tick=False)     # last trade 10:44:40
        cfg = {"no_entries_before": "09:30", "no_entries_after": "10:45"}
        r = at("2026-11-23 10:45:10", d, "RELIANCE", cfg=cfg)      # the 10:40 bar closed by the clock only
        self.assertEqual(r["status"], "waiting")
        self.assertIn("Yahoo's last trade is 10:44:40", r["lag_note"])
        self.assertEqual(at("2026-11-23 10:46", d, "RELIANCE", cfg=cfg)["status"], "expired")

    def test_config_window(self):
        self.assertEqual(entry.DEFAULTS, {"no_entries_before": "09:30", "no_entries_after": "14:30"})
        cfg = {"no_entries_before": "09:30", "no_entries_after": "09:40"}
        r = at("2026-11-23 09:45", cfg=cfg)
        self.assertEqual(r["status"], "expired")
        cfg = {"no_entries_before": "09:50", "no_entries_after": "14:30"}
        r = at("2026-11-23 10:00", cfg=cfg)
        self.assertEqual(r["trigger"]["time"], "9:50 AM")          # the 9:45 bar, first to close at or after 9:50
        # a cutoff after the close cannot outlive the session
        cfg = {"no_entries_before": "09:30", "no_entries_after": "16:00"}
        self.assertEqual(at("2026-11-23 11:00", doc(REL), "RELIANCE", cfg=cfg)["no_entries_after"], "15:30")

    def test_bad_inputs(self):
        with self.assertRaises(entry.EntryError):
            at("2026-11-23 09:45", or_minutes=7)
        with self.assertRaises(entry.EntryError):
            at("2026-11-23 09:45", interval="15m")
        with self.assertRaises(entry.EntryError):
            entry.parse_now("tomorrow")
        with self.assertRaises(entry.EntryError):
            entry.parse_hhmm("25:00", "x")

    def test_or_minutes_30(self):
        r = at("2026-11-23 10:30", or_minutes=30)
        self.assertEqual(r["opening_range"]["bars"], 6)
        self.assertEqual(r["opening_range"]["high"], 2443.6)
        self.assertEqual(r["trigger"]["time"], "9:50 AM")


class IndiaTime(unittest.TestCase):
    def test_ist_is_utc_plus_530_all_year(self):
        self.assertEqual(entry.to_ist(DT(2026, 11, 23, 3, 45)), DT(2026, 11, 23, 9, 15))
        self.assertEqual(entry.ist_to_utc(DT(2026, 11, 23, 15, 30)), DT(2026, 11, 23, 10, 0))
        self.assertEqual(entry.to_ist(DT(2026, 6, 30, 20, 0)), DT(2026, 7, 1, 1, 30))        # crosses midnight
        for month in range(1, 13):                                 # no daylight saving, any month
            local = DT(2026, month, 15, 9, 15)
            self.assertEqual(entry.ist_to_utc(local), DT(2026, month, 15, 3, 45))
            self.assertEqual(entry.to_ist(entry.ist_to_utc(local)), local)
        self.assertEqual(entry.to_ist(entry.utc_of(OPEN_TS)), DT(2026, 11, 23, 9, 15))
        self.assertFalse(hasattr(entry, "to_eastern"))
        self.assertFalse(hasattr(entry, "eastern_offset"))

    def test_parse_now(self):
        self.assertEqual(entry.parse_now("2026-11-23 09:45"), DT(2026, 11, 23, 4, 15))
        self.assertEqual(entry.parse_now("2026-11-23 09:45 IST"), DT(2026, 11, 23, 4, 15))
        self.assertEqual(entry.parse_now("2026-11-23T09:45"), DT(2026, 11, 23, 4, 15))
        self.assertEqual(entry.parse_now("2026-11-23T04:15:00Z"), DT(2026, 11, 23, 4, 15))
        self.assertEqual(entry.parse_now("2026-11-23T09:45:00+05:30"), DT(2026, 11, 23, 4, 15))

    def test_the_same_session_triggers_at_the_same_clock_time_in_any_month(self):
        for day in (datetime.date(2026, 3, 9), datetime.date(2026, 6, 15), datetime.date(2026, 11, 2),
                    datetime.date(2026, 12, 7)):
            d = shift_to(doc(), day)
            r = entry.evaluate(chart(d), ist("%s 09:45" % day))
            self.assertEqual(r["status"], "triggered", day)
            self.assertEqual(r["trigger"]["time"], "9:45 AM", day)
            self.assertEqual(r["trigger"]["bar_index"], 5, day)
            self.assertEqual(entry.evaluate(chart(d), ist("%s 09:42" % day))["status"], "waiting", day)


class Calendar(unittest.TestCase):
    def test_nse_holidays_2026(self):
        self.assertEqual(entry.CALENDAR_YEARS, (2026,))
        self.assertEqual(len(entry.NSE_HOLIDAYS), 16)
        self.assertEqual(entry.trading_day(datetime.date(2026, 11, 24)), (False, "NSE holiday: Guru Nanak Jayanti"))
        self.assertEqual(entry.trading_day(datetime.date(2026, 11, 23)), (True, ""))
        self.assertEqual(entry.trading_day(datetime.date(2026, 11, 22)), (False, "weekend"))
        self.assertEqual(entry.trading_day(datetime.date(2026, 1, 26))[1], "NSE holiday: Republic Day")
        self.assertTrue(entry.trading_day(datetime.date(2026, 11, 26))[0])     # a US holiday, an NSE trading day
        for iso in entry.NSE_HOLIDAYS:                             # the list holds weekdays of 2026 only
            d = datetime.date.fromisoformat(iso)
            self.assertEqual(d.year, 2026)
            self.assertLess(d.weekday(), 5, iso)
        self.assertFalse(hasattr(entry, "NYSE_HOLIDAYS"))
        self.assertFalse(hasattr(entry, "NYSE_EARLY_CLOSE"))

    def test_session_is_0915_to_1530(self):
        self.assertEqual(entry.session(datetime.date(2026, 11, 23)), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertIsNone(entry.session(datetime.date(2026, 11, 24)))
        self.assertIsNone(entry.session(datetime.date(2026, 11, 21)))
        r = at("2026-11-23 15:45")
        self.assertEqual((r["opens"], r["closes"]), ("09:15", "15:30"))
        self.assertNotIn("early_close", r)
        self.assertEqual(r["bars_closed"], 74)                     # 75 slots, the 15:20 bar came as nulls
        self.assertEqual(r["_bars"][0]["slot"], 0)
        self.assertEqual(r["_bars"][-1]["slot"], 74)               # the 15:25 bar is the 75th and last
        self.assertEqual(entry.hm(r["_bars"][-1]["et"]), "15:25")
        # bars outside the session (pre-open 09:10, a 15:30 print) never count
        d = doc()
        res = d["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        for ts in (OPEN_TS - 300, OPEN_TS + 75 * 300):
            res["timestamp"].append(ts)
            for k in ("open", "high", "low", "close"):
                q[k].append(9999.0)
            q["volume"].append(1)
        r = at("2026-11-23 15:45", d)
        self.assertEqual(r["bars_closed"], 74)
        self.assertEqual(r["opening_range"]["high"], 2433.7)

    def test_weekend_holiday_and_empty_data(self):
        r = at("2026-11-21 10:30")
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertEqual(r["next_open"], "2026-11-23 09:15")
        r = at("2026-11-24 10:30")
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertIn("the NSE is closed today (NSE holiday: Guru Nanak Jayanti)", r["reason"])
        self.assertEqual(r["next_open"], "2026-11-25 09:15")
        text = "\n".join(entry.describe(r))
        self.assertIn("MARKET CLOSED", text)
        self.assertIn("Next open: 2026-11-25 09:15 India time.", text)
        self.assertEqual(entry.next_open(datetime.date(2026, 11, 23)), "2026-11-25 09:15")
        empty = copy.deepcopy(doc())
        res = empty["chart"]["result"][0]
        del res["timestamp"]
        res["indicators"] = {"quote": [{}]}
        r = at("2026-11-23 09:45", empty)
        self.assertEqual((r["status"], r["phase"]), ("no-data", "open"))
        r = at("2026-11-25 09:45")                                 # Yahoo sent the last session's bars only
        self.assertEqual(r["status"], "no-data")

    def test_a_year_without_a_holiday_list_says_so(self):
        r = at("2027-03-01 10:30")
        self.assertFalse(r["calendar_known"])
        self.assertIn("NSE holiday list covers 2026 only", "\n".join(entry.describe(r)))
        self.assertTrue(at("2026-11-23 09:45")["calendar_known"])

    def test_yahoo_errors(self):
        bad = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                              "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(entry.EntryError) as cm:
            entry.parse_chart(bad, "ZZZZ")
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(entry.EntryError):
            entry.parse_chart("<html>", "TCS")


class Symbols(unittest.TestCase):
    def test_yahoo_symbol_adds_ns(self):
        self.assertEqual(entry.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(entry.yahoo_symbol("HDFCBANK"), "HDFCBANK.NS")
        self.assertEqual(entry.yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(entry.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(entry.yahoo_symbol("TCS.NS"), "TCS.NS")
        self.assertEqual(entry.yahoo_symbol("TCS.BO"), "TCS.BO")
        self.assertEqual(entry.yahoo_symbol("^NSEI"), "^NSEI")

    def test_the_suffix_is_stripped_for_people_and_files(self):
        self.assertEqual(entry.nse_symbol("RELIANCE.NS"), "RELIANCE")
        self.assertEqual(entry.nse_symbol("m&m.ns"), "M&M")
        self.assertEqual(entry.nse_symbol("TCS"), "TCS")
        self.assertEqual(chart(doc(MM), "M&M")["symbol"], "M&M")
        for good in ("RELIANCE", "HDFCBANK", "M&M", "BAJAJ-AUTO", "m&m.ns", "360ONE", "TCS.BO"):
            entry.clean_symbol(good)
        for bad in ("BRK.B", "TCS NS", "", "TCS;rm", "A" * 25):
            with self.assertRaises(entry.EntryError):
                entry.clean_symbol(bad)

    def test_the_url_quotes_the_ampersand(self):
        self.assertEqual(entry.chart_path("TCS", "1d", "5m"), "/v8/finance/chart/TCS.NS?range=1d&interval=5m")
        self.assertEqual(entry.chart_path("m&m", "5d", "1m"), "/v8/finance/chart/M%26M.NS?range=5d&interval=1m")
        self.assertIn("/chart/BAJAJ-AUTO.NS?", entry.chart_path("BAJAJ-AUTO", "1d", "5m"))
        self.assertIn("/chart/%5ENSEI?", entry.chart_path("^NSEI", "1d", "5m"))

    def test_m_and_m_runs_end_to_end(self):
        code, text, err = run(["check", "M&M", "--offline", MM, "--now", "2026-11-23 11:02"])
        self.assertEqual(code, 1, err)
        self.assertIn("M&M  Mon 2026-11-23 11:02 IST", text)
        self.assertIn("WAITING  last close %s3,516.20 (10:55 bar), %s51.80 under the range high %s3,568.00"
                      % (RS, RS, RS), text)
        code, text, err = run(["check", "m&m.ns", "--offline", MM, "--now", "2026-11-23 11:02", "--json"])
        self.assertEqual(json.loads(text)["symbol"], "M&M")


class OneMinute(unittest.TestCase):
    def c(self):
        return entry.parse_chart(json.dumps(doc(DEMO)), "DEMO")

    def test_real_one_minute_bars(self):
        c = entry.parse_chart(json.dumps(doc(TCS1M)), "TCS")
        r = entry.evaluate(c, ist("2026-11-23 10:00"), interval="1m")
        self.assertEqual(r["opening_range"]["bars"], 15)           # fifteen 1-minute bars, 9:15 to 9:29
        self.assertEqual((r["opening_range"]["high"], r["opening_range"]["low"]), (2433.7, 2405.0))
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("9:43 AM", "09:42", 27, 2436.5))
        self.assertEqual(entry.evaluate(c, ist("2026-11-23 09:42:59"), interval="1m")["status"], "waiting")
        self.assertEqual(entry.evaluate(c, ist("2026-11-23 09:20"), interval="1m")["phase"], "forming")

    def test_wick_and_close_at_the_high_do_not_trigger(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m")
        self.assertEqual(r["now"], "2026-11-23 09:51:30")
        self.assertEqual(r["opening_range"], {"high": 100.5, "low": 99.0, "minutes": 15, "bars": 15, "of": 15,
                                              "complete": True, "avg_volume": 10400})
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("9:44 AM", "09:43", 28, 100.62))
        r = entry.evaluate(c, ist("2026-11-23 09:43:59"), interval="1m")
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["forming"]["bar_start"], "09:43")
        self.assertEqual(r["last"]["close"], 100.5)             # the 9:42 bar closed AT the high

    def test_null_bar_tick_and_volume(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m", min_volume_x=2)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:47 AM", 31))
        slots = [b["slot"] for b in r["_bars"]]
        self.assertNotIn(19, slots)                               # 9:34 came as nulls
        self.assertEqual(slots[-1], 35)                           # 9:51 still forming, the 9:51:30 tick dropped
        self.assertEqual(r["forming"]["bar_start"], "09:51")

    def test_the_file_and_the_interval_must_agree(self):
        code, _, err = run(["check", "DEMO", "--offline", DEMO])
        self.assertEqual(code, 1)
        self.assertIn("1m bars, not 5m", err)


class Network(unittest.TestCase):
    """Yahoo is never called: _open_url and _sleep are swapped for fakes."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._open, self._sleep = entry._open_url, entry._sleep
        entry._sleep = lambda s: self.sleeps.append(s)
        with open(TCS, encoding="utf-8") as fh:
            self.body = fh.read()

    def tearDown(self):
        entry._open_url, entry._sleep = self._open, self._sleep

    def answers(self, *codes):
        seq = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url)
            code = seq.pop(0)
            if code == 200:
                return self.body
            raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b'{"chart":{"result":null,'
                                                                           b'"error":{"description":"No data found"}}}'))
        entry._open_url = fake

    def test_url_and_429_retry_once_after_a_pause(self):
        self.answers(429, 200)
        c = entry.get_chart("TCS", "5m", datetime.date(2026, 11, 23), today=datetime.date(2026, 11, 23))
        self.assertEqual(len(c["bars"]), 74)
        self.assertEqual(c["symbol"], "TCS")
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com/v8/finance/chart/TCS.NS?range=1d&interval=5m"] * 2)
        self.assertEqual(self.sleeps, [entry.BACKOFF])

    def test_429_twice_falls_back_to_query1(self):
        self.answers(429, 429, 200)
        entry.get_chart("TCS", "5m", datetime.date(2026, 11, 23), today=datetime.date(2026, 11, 23))
        self.assertEqual([u.split("/")[2] for u in self.calls],
                         ["query2.finance.yahoo.com", "query2.finance.yahoo.com", "query1.finance.yahoo.com"])

    def test_5xx_falls_back_without_waiting(self):
        self.answers(503, 200)
        entry.get_chart("TCS", "5m", datetime.date(2026, 11, 23), today=datetime.date(2026, 11, 23))
        self.assertEqual(self.sleeps, [])
        self.assertIn("query1", self.calls[1])

    def test_still_busy_is_one_plain_error(self):
        self.answers(429, 429, 429, 429)
        code, out, err = run(["check", "TCS", "--now", "2026-11-23 09:45"])
        self.assertEqual(code, 1)
        self.assertIn("HTTP 429", err)
        self.assertNotIn("Traceback", err)

    def test_unknown_symbol_404(self):
        self.answers(404)
        with self.assertRaises(entry.EntryError) as cm:
            entry.get_chart("ZZZZ", "5m", datetime.date(2026, 11, 23), today=datetime.date(2026, 11, 23))
        self.assertIn("No data found", str(cm.exception))

    def test_a_bad_symbol_never_reaches_yahoo(self):
        self.answers(200)
        with self.assertRaises(entry.EntryError):
            entry.get_chart("BRK.B", "5m", datetime.date(2026, 11, 23), today=datetime.date(2026, 11, 23))
        self.assertEqual(self.calls, [])

    def test_ranges_for_past_sessions_and_the_user_agent(self):
        today = datetime.date(2026, 11, 24)
        self.assertEqual(entry.chart_range(today, today, "5m"), "1d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 11, 23), today, "5m"), "5d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 11, 2), today, "5m"), "1mo")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 5), today, "5m"), "60d")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(datetime.date(2026, 11, 2), today, "1m")
        self.assertEqual(entry.UA, "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)")
        self.assertEqual(entry.HOSTS, ("query2", "query1"))


def live_provider(d, sym="TCS", spike=2440.00, lag_until=None, calls=None):
    """Bars the way Yahoo serves them at `now`: closed bars final, the in-progress bar
    showing a spike above the range high (then it closes where it really did), plus a live tick.
    lag_until: before this UTC time Yahoo's last trade lags 30 seconds behind."""
    base = chart(d, sym)

    def provider(now):
        if calls is not None:
            calls.append(now)
        nts = entry.ts_of(now)
        lagging = lag_until is not None and now < lag_until
        data_ts = nts - 30 if lagging else nts
        bars = []
        for b in base["bars"]:
            if b["t"] % 300 or b["t"] > data_ts:
                continue
            b = dict(b)
            if b["t"] + 300 > data_ts:
                b["c"], b["h"] = spike, max(b["h"], spike)
            bars.append(b)
        if data_ts % 300:
            bars.append({"t": data_ts, "o": spike, "h": spike, "l": spike, "c": spike, "v": 0})
        return {"symbol": sym, "meta": {}, "bars": bars, "prev_close": base["prev_close"], "data_time": data_ts}
    return provider


class Watch(unittest.TestCase):
    OPTS = {"setup": "orb", "or_minutes": 15, "interval": "5m", "level": None, "min_volume_x": None, "cfg": None}

    def loop(self, provider, start, opts=None, **kw):
        clock = entry.SimClock(ist(start))
        lines, fired = [], []
        code, res = entry.watch_loop(provider, clock.now, clock.sleep, lines.append, dict(opts or self.OPTS),
                                     on_trigger=lambda r, now: fired.append((r, now)), **kw)
        return code, res, lines, fired, clock

    def test_fires_on_the_first_closed_bar_not_the_spike_in_progress(self):
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(doc(), calls=calls), "2026-11-23 09:37", every=60)
        self.assertEqual(code, 0)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0][0]["trigger"]["time"], "9:45 AM")
        self.assertEqual(fired[0][0]["trigger"]["price"], 2443.5)
        # it looked at 9:37 (9:35 bar spiking to 2,440 in progress) and at 9:40:02 (that bar closed 2,429.00)
        self.assertEqual([entry.to_ist(c).strftime("%H:%M:%S") for c in calls], ["09:37:00", "09:40:02", "09:45:02"])
        text = "\n".join(lines)
        self.assertIn(" 9:35 bar  closed %s2,429.00  %s4.70 under %s2,433.70" % (RS, RS, RS), text)
        self.assertIn(" 9:40 bar  closed %s2,443.50  TRIGGERED" % RS, text)
        self.assertEqual(text.count("TRIGGERED"), 1)

    def test_waits_for_yahoo_to_catch_up(self):
        lag = ist("2026-11-23 09:45:30")
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(doc(), lag_until=lag, calls=calls),
                                                   "2026-11-23 09:42", every=30)
        self.assertEqual(code, 0)
        times = [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times, ["09:42:00", "09:45:02", "09:45:32"])   # 9:45:02: Yahoo still at 9:44:32
        self.assertEqual(fired[0][0]["trigger"]["time"], "9:45 AM")

    def test_from_before_the_open_prints_the_range_bar_by_bar(self):
        code, res, lines, fired, clock = self.loop(live_provider(doc()), "2026-11-23 09:00", every=30)
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        self.assertIn("09:00 IST  not open yet: watching from the first bar's close at 9:20.", lines[0])
        self.assertIn(" 9:25 bar  closed %s2,405.90  opening range set: high %s2,433.70, low %s2,405.00"
                      % (RS, RS, RS), text)
        self.assertEqual(sum(1 for x in lines if " bar  closed " in x), 6)    # 9:15 ... 9:40, one line each

    def test_a_computer_that_dozes_off_catches_up_when_it_wakes(self):
        class Dozing(entry.SimClock):
            naps = 0

            def sleep(self, seconds):
                entry.SimClock.sleep(self, seconds)
                self.naps += 1
                if self.naps == 3:                      # the laptop sleeps for 17 minutes mid-wait
                    self.t += datetime.timedelta(minutes=17)
        clock = Dozing(ist("2026-11-23 09:31"))
        calls, lines = [], []
        opts = dict(self.OPTS, min_volume_x=1.5)        # with the volume check the trigger is the 10:05 bar
        code, res = entry.watch_loop(live_provider(doc(), calls=calls), clock.now, clock.sleep, lines.append,
                                     opts, every=30)
        self.assertEqual(code, 0)
        times = [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times[:2], ["09:31:00", "09:49:30"])   # straight after waking, not a long sleep later
        text = "\n".join(lines)
        for start in (" 9:30", " 9:35", " 9:40", " 9:45"):
            self.assertIn(start + " bar  closed", text)          # the bars it slept through, one line each
        self.assertEqual(res["trigger"]["time"], "10:10 AM")

    def test_expires_and_until(self):
        rel = live_provider(doc(REL), "RELIANCE", spike=1395.00)
        code, res, lines, fired, clock = self.loop(rel, "2026-11-23 14:00", every=300)
        self.assertEqual(code, 1)
        self.assertEqual(res["status"], "expired")
        self.assertEqual(fired, [])
        self.assertEqual(entry.to_ist(clock.now()).strftime("%H:%M"), "14:30")
        until = DT(2026, 11, 23, 11, 0)
        code, res, lines, fired, clock = self.loop(rel, "2026-11-23 10:30", every=300, until=until)
        self.assertEqual(code, 1)
        self.assertIn("--until 11:00", lines[-1])

    def test_closed_day_and_repeated_errors(self):
        code, _, lines, _, _ = self.loop(live_provider(doc()), "2026-11-21 10:00")
        self.assertEqual(code, 1)
        self.assertIn("weekend", lines[0])
        code, _, lines, _, _ = self.loop(live_provider(doc()), "2026-11-24 10:00")
        self.assertEqual(code, 1)
        self.assertIn("NSE holiday: Guru Nanak Jayanti", lines[0])

        def broken(now):
            raise entry.EntryError("Yahoo did not answer")
        code, _, lines, _, _ = self.loop(broken, "2026-11-23 10:00", every=30)
        self.assertEqual(code, 1)
        self.assertIn("Stopping after 5 failed reads", lines[-1])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_check_write_is_the_contract_shape(self):
        code, text, _ = run(["check", "TCS", "--offline", TCS, "--now", "2026-11-23 09:45", "--write"])
        self.assertEqual(code, 0)
        self.assertIn("TRIGGERED 9:45 AM at %s2,443.50" % RS, text)
        self.assertIn("/trader-exit", text)
        with open(os.path.join("trader", "entry.json"), encoding="utf-8") as fh:
            e = json.load(fh)
        want = {"created_by": "trader-entry", "symbol": "TCS", "setup": "orb", "side": "buy", "date": "2026-11-23",
                "status": "triggered", "time": "9:45 AM", "bar_start": "09:40", "bar_index": 5, "price": 2443.5,
                "level": 2433.7, "interval": "5m", "opening_range": {"high": 2433.7, "low": 2405.0, "minutes": 15},
                "source": "yahoo", "checked_at": "2026-11-23T04:15:00Z"}
        for k, v in want.items():
            self.assertEqual(e[k], v, k)
        self.assertEqual(sorted(e), sorted(list(want) + ["reason"]))       # the fields trader-exit reads, no more

    def test_check_waiting_json_and_exit_codes(self):
        code, text, _ = run(["check", "TCS", "--offline", TCS, "--now", "2026-11-23 09:42", "--json"])
        self.assertEqual(code, 1)
        r = json.loads(text)
        self.assertEqual(r["status"], "waiting")
        self.assertNotIn("_bars", r)
        code, text, _ = run(["check", "TCS", "--offline", TCS, "--now", "2026-11-23 09:42", "--write", "e.json"])
        with open("e.json", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["status"], "waiting")
        self.assertIn("still forming", text)

    def test_offline_gz_folder_and_mismatch(self):
        os.mkdir("charts")
        with open(TCS, "rb") as src, gzip.open(os.path.join("charts", "TCS.json.gz"), "wb") as dst:
            dst.write(src.read())
        shutil.copy(MM, os.path.join("charts", "M&M.NS.json"))     # Yahoo's own name works too
        code, text, _ = run(["check", "tcs", "--offline", "charts", "--now", "2026-11-23 09:45"])
        self.assertEqual(code, 0)
        code, text, err = run(["check", "M&M", "--offline", "charts", "--now", "2026-11-23 10:00"])
        self.assertIn("M&M  Mon 2026-11-23 10:00 IST", text, err)
        code, _, err = run(["check", "INFY", "--offline", TCS])
        self.assertEqual(code, 1)
        self.assertIn("holds TCS, not INFY", err)

    def test_longs_only_and_flag_errors(self):
        code, _, err = run(["check", "TCS", "--offline", TCS, "--side", "short"])
        self.assertEqual(code, 1)
        self.assertIn("longs only", err)
        code, _, err = run(["check", "TCS", "--offline", TCS, "--level", "300"])
        self.assertIn("--level is for --setup level", err)
        code, _, err = run(["check", "TCS", "--offline", TCS, "--setup", "level"])
        self.assertIn("needs --level", err)
        code, _, err = run(["check", "BRK.B", "--offline", TCS])
        self.assertEqual(code, 1)
        self.assertIn("not an NSE equity symbol", err)

    def test_config_is_read(self):
        os.mkdir("trader")
        with open(os.path.join("trader", "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"no_entries_after": "09:40", "max_order_inr": 40000, "broker": "groww"}, fh)
        code, text, _ = run(["check", "TCS", "--offline", TCS, "--now", "2026-11-23 09:45"])
        self.assertEqual(code, 1)
        self.assertIn("EXPIRED", text)
        with open(os.path.join("trader", "config.json"), "w", encoding="utf-8") as fh:
            fh.write("{nope")
        code, _, err = run(["check", "TCS", "--offline", TCS])
        self.assertIn("not valid JSON", err)

    def test_watch_replays_the_file_and_writes_the_entry(self):
        code, text, _ = run(["watch", "TCS", "--offline", TCS, "--now", "2026-11-23 09:20", "--every", "30"])
        self.assertEqual(code, 0)
        self.assertIn(" 9:40 bar  closed %s2,443.50  TRIGGERED" % RS, text)
        with open(os.path.join("trader", "entry.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["bar_index"], 5)
        code, text, _ = run(["watch", "TCS", "--offline", TCS, "--now", "2026-11-23 09:20", "--until", "09:40",
                             "--out", "x.json"])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists("x.json"))

    def test_watchlist(self):
        os.mkdir("charts")
        shutil.copy(TCS, os.path.join("charts", "TCS.json"))
        shutil.copy(REL, os.path.join("charts", "RELIANCE.json"))
        shutil.copy(MM, os.path.join("charts", "M&M.json"))
        shutil.copy(DEMO, os.path.join("charts", "DEMO.json"))
        os.mkdir("trader")
        with open(os.path.join("trader", "watchlist.json"), "w", encoding="utf-8") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [
                {"symbol": "TCS", "setups": ["orb"], "or_high": 2433.7, "or_low": 2405.0},
                {"symbol": "ZZZZ", "setups": ["orb"]}, {"symbol": "DEMO", "setups": ["orb"]},
                {"symbol": "RELIANCE.NS", "setups": ["orb"]}, {"symbol": "M&M", "setups": ["orb"]}]}, fh)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", "2026-11-23 09:45"])
        self.assertEqual(code, 0)
        rows = text.splitlines()
        self.assertEqual(rows[1].split(), ["TCS", "triggered", "9:45", "AM", RS + "2,443.50", RS + "2,433.70",
                                           RS + "2,443.50", "+0.40%"])
        self.assertIn("error: no offline file", rows[2])
        self.assertTrue(rows[2].startswith("ZZZZ "))
        self.assertIn("error: DEMO has 1m bars", rows[3])
        self.assertEqual(rows[4].split()[:2], ["RELIANCE", "waiting"])
        self.assertEqual(rows[5].split()[:2], ["M&M", "waiting"])
        self.assertEqual(len({r.index("LEVEL") if "LEVEL" in r else r.index(RS + "2,433.70") for r in rows[:2]}), 1)
        self.assertIn("1 of 5 triggered", text)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", "2026-11-23 09:42", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)[0]["result"]["status"], "waiting")
        code, _, err = run(["watchlist", "--watchlist", "missing.json"])
        self.assertIn("run /trader-scanner first", err)

    def test_the_rupee_sign_survives_a_windows_pipe(self):
        env = dict(os.environ)
        env.pop("PYTHONIOENCODING", None)
        env.pop("PYTHONUTF8", None)
        p = subprocess.run([sys.executable, os.path.join(ROOT, "trader-entry", "entry.py"), "check", "TCS",
                            "--offline", TCS, "--now", "2026-11-23 09:45"], capture_output=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("TRIGGERED 9:45 AM at %s2,443.50" % RS, p.stdout.decode("utf-8"))


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-entry", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-entry\n"))
        head = text.split("---")[1]
        for field in ("name:", "description:", "argument-hint:"):
            self.assertIn(field, head)
        self.assertGreater(len(" ".join(head.split("description:")[1].split("argument-hint:")[0].split())), 200)
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], SAFETY)
        low = " ".join(text.lower().split())
        for must in ("bar by bar", "can lag", "day trading loses most people money", "/trader-exit", "long side only",
                     "in the background", "/loop", "nothing was placed", "9:45 am", RS + "2,443.50", "bar 5",
                     "not financial advice", "explicit yes", "india standard time", ".ns", "09:15", "15:30",
                     "muhurat", "python3", 'python "${claude_skill_dir}/entry.py"', "before charges"):
            self.assertIn(must, low)
        for never in (chr(0x2014), "tick " + "by tick", "real " + "time", "pro" + "fit", "$3", "new york", "nyse",
                      "alpaca", "tsla", "the reel", " et ", "early close"):
            self.assertNotIn(never, low)


if __name__ == "__main__":
    unittest.main()
