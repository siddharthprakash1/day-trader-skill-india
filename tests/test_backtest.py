"""trader-backtest tests (India, NSE). Offline: neither Yahoo nor NSE is ever called (the
network function is replaced in every test and fails the test if something reaches it).

Fixtures in tests/fixtures/trader-backtest/:

TCS-60d5m.json          REAL Yahoo answer for TCS.NS (range=60d, interval=5m, saved 2026-11-24),
                        CUT to three sessions (19, 20 and 23 Nov 2026) to keep it small, with
                        chartPreviousClose reset to the 18 Nov close it then stands for. As in
                        all of Yahoo's 60-day data, the 9:15 bar has no volume and the 15:20
                        bar is missing.
ind_nifty500list.csv    six lines in the shape of NSE's Nifty 500 file: three EQ stocks, one in
                        series BE and one placeholder row.

Everything else is built in the test from a few hand-made bars, so every expected number
can be worked out by hand.
"""

import argparse
import datetime
import gzip
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, FIX, ROOT

bt = load("trader-backtest", "backtest")
journal = load("trader-journal", "journal")
entry = load("trader-entry", "entry")
exit_tool = load("trader-exit", "exit")

BDIR = os.path.join(FIX, "trader-backtest")
TCS60 = os.path.join(BDIR, "TCS-60d5m.json")
NIFTY = os.path.join(BDIR, "ind_nifty500list.csv")
RS = "₹"
EPOCH = datetime.datetime(1970, 1, 1)
NOW = bt.parse_now("2026-11-24 10:00")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def ts(date, slot):
    d = datetime.date.fromisoformat(date)
    return int((datetime.datetime(d.year, d.month, d.day, 3, 45) - EPOCH).total_seconds()) + 300 * slot


def bar(o, h=None, l=None, c=None, v=1000):
    h = o if h is None else h
    l = o if l is None else l
    c = o if c is None else c
    return {"o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": v}


def sess(price=100.0, edits=None, n=75, v=1000):
    """One session as {slot: bar}: n flat bars at `price`, then the edits ({slot: bar or None})."""
    day = {i: bar(price, v=v) for i in range(n)}
    for k, b in (edits or {}).items():
        if b is None:
            day.pop(k, None)
        else:
            day[k] = b
    return day


def fill(day, first, last, price, v=1000):
    for i in range(first, last + 1):
        day[i] = bar(price, v=v)
    return day


def chart_text(sym, sessions, rmt=None):
    """{date: {slot: bar}} -> the text of a Yahoo chart answer."""
    stamps, q = [], {k: [] for k in ("open", "high", "low", "close", "volume")}
    for date in sorted(sessions):
        for slot in sorted(sessions[date]):
            b = sessions[date][slot]
            stamps.append(ts(date, slot))
            for key, short in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"), ("volume", "v")):
                q[key].append(b[short])
    meta = {"symbol": sym + ".NS", "dataGranularity": "5m", "range": "60d",
            "regularMarketTime": rmt or (max(stamps) + 300 if stamps else 0)}
    return json.dumps({"chart": {"result": [{"meta": meta, "timestamp": stamps, "indicators": {"quote": [q]}}],
                                 "error": None}})


def params(**over):
    p = {"setups": list(bt.SETUPS), "risk_inr": 500.0, "max_order_inr": 40000.0, "target_r": 2.0, "gap_pct": 1.0,
         "min_bars_below": 3, "min_trades": 30, "no_entries_before": "09:30", "no_entries_after": "14:30",
         "flat_by": "15:00", "or_minutes": 15, "last": 68, "k_min": 2, "k_max": 62, "charges_overridden": []}
    p.update(over)
    return p


RATES = bt.load_charges()[0]


def orb_day(after=102.0):
    """Range 99 to 101; the 9:40 bar (slot 5) closes at 102; flat at `after` from then on."""
    day = sess(100.0, {0: bar(100, 101, 99, 100), 1: bar(100, 101, 99, 100), 2: bar(100, 101, 99, 100),
                       5: bar(100, 102, 100, 102)})
    return fill(day, 6, 74, after)


def vwap_day(v0=1000):
    """9:15 flat at 100; 9:20 to 9:30 close at 98.8 (under VWAP), low 98.5; 9:35 closes at 100.9."""
    day = sess(100.0, {0: bar(100, v=v0)})
    for k in (1, 2, 3):
        day[k] = bar(99.5, 100, 98.5, 98.8)
    day[4] = bar(99, 101, 99, 100.9)
    return fill(day, 5, 74, 101.0)


def gap_day():
    """Opens at 101.5; range 101 to 102.5; the 9:40 bar closes at 103."""
    day = sess(102.0, {0: bar(101.5, 102.5, 101, 102), 1: bar(102, 102.5, 101, 102), 2: bar(102, 102.5, 101, 102),
                       5: bar(102, 103, 102, 103)})
    return fill(day, 6, 74, 103.0)


class NoNetwork(unittest.TestCase):
    """Every test here starts with the network cut and the clock faked."""

    def setUp(self):
        self._saved = (bt._get, bt._sleep, bt._clock)
        self.sleeps = []
        self.t = [1000.0]

        def no_net(url, timeout=20):
            raise AssertionError("a test reached the network: %s" % url)

        def fake_sleep(seconds):
            self.sleeps.append(seconds)
            self.t[0] += seconds

        bt._get, bt._sleep, bt._clock = no_net, fake_sleep, (lambda: self.t[0])
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        bt._get, bt._sleep, bt._clock = self._saved
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def serve(self, answers):
        """Replace the network with `answers`: {SYMBOL: text | Exception | [one per call]}. Returns the call log."""
        calls = []

        def fake_get(url, timeout=20):
            self.t[0] += 0.1                     # a request takes a tenth of a second
            calls.append((url, self.t[0]))
            m = re.search(r"/chart/([^?]+)\?", url)
            key = "NIFTY" if "ind_nifty500list" in url else m.group(1).replace("%26", "&").replace(".NS", "")
            a = answers[key]
            if isinstance(a, list):
                a = a.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
        bt._get = fake_get
        return calls


def http(code):
    return urllib.error.HTTPError("https://example.invalid/x", code, "status %d" % code, {}, None)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = bt.main(argv)
    return code, out.getvalue(), err.getvalue()


class IndiaTime(unittest.TestCase):
    def test_ist_and_now(self):
        self.assertEqual(bt.to_ist(datetime.datetime(2026, 11, 23, 3, 45)), datetime.datetime(2026, 11, 23, 9, 15))
        self.assertEqual(bt.parse_now("2026-11-24 10:00"), datetime.datetime(2026, 11, 24, 4, 30))
        self.assertEqual(bt.parse_now("2026-11-24 10:00 IST"), bt.parse_now("2026-11-24T04:30:00Z"))
        with self.assertRaises(bt.BTError):
            bt.parse_now("yesterday")

    def test_calendar(self):
        self.assertFalse(bt.trading_day(datetime.date(2026, 11, 24)))      # Guru Nanak Jayanti
        self.assertTrue(bt.trading_day(datetime.date(2026, 11, 23)))
        self.assertEqual(bt.previous_trading_day(datetime.date(2026, 11, 25)), datetime.date(2026, 11, 23))
        self.assertEqual(bt.previous_trading_day(datetime.date(2026, 11, 23)), datetime.date(2026, 11, 20))

    def test_slots_and_fortnights(self):
        self.assertEqual(bt.slot_close(5), "09:45")
        self.assertEqual(bt.slot_close(68), "15:00")
        self.assertEqual(bt.fortnight("2026-11-15"), ((2026, 11, 0), "1-15 Nov 2026"))
        self.assertEqual(bt.fortnight("2026-11-16"), ((2026, 11, 1), "16-30 Nov 2026"))
        self.assertEqual(bt.fortnight("2026-08-31")[1], "16-31 Aug 2026")

    def test_symbols(self):
        self.assertEqual(bt.yahoo_symbol("m&m"), "M&M.NS")
        self.assertEqual(bt.nse_symbol("TCS.NS"), "TCS")
        self.assertIn("M%26M.NS?range=60d&interval=5m", bt.chart_url("M&M"))
        self.assertTrue(bt.chart_url("TCS").startswith("https://query2.finance.yahoo.com/v8/finance/chart/TCS.NS"))
        with self.assertRaises(bt.BTError):
            bt.clean_symbol("BRK.B")


class Vwap(unittest.TestCase):
    def test_by_hand_on_a_tiny_series(self):
        bars = [{"h": 10.0, "l": 8.0, "c": 9.0, "v": 100},      # typical 9  -> 900 / 100
                {"h": 12.0, "l": 10.0, "c": 11.0, "v": 300},    # typical 11 -> (900 + 3300) / 400
                {"h": 20.0, "l": 20.0, "c": 20.0, "v": 0},      # no volume: adds nothing
                {"h": 13.0, "l": 11.0, "c": 12.0, "v": 100}]    # typical 12 -> 5400 / 500
        self.assertEqual(bt.session_vwap(bars), [9.0, 10.5, 10.5, 10.8])
        self.assertEqual(bt.session_vwap([{"h": 5.0, "l": 5.0, "c": 5.0, "v": 0}]), [None])

    def test_the_same_function_as_trader_entry(self):
        days = bt.parse_sessions(read(TCS60), "TCS", NOW)
        bars = [days["2026-11-23"][k] for k in sorted(days["2026-11-23"])]
        self.assertEqual(bt.session_vwap(bars), entry.session_vwap(bars))


class EntryRules(unittest.TestCase):
    def test_orb_triggers_on_the_first_close_above_the_range_high(self):
        found, why = bt.find_entry("orb", orb_day(), params())
        self.assertIsNone(why)
        self.assertEqual((found["k"], found["entry"], found["or_high"], found["or_low"]), (5, 102.0, 101.0, 99.0))
        self.assertEqual(found["stop_raw"], Decimal("100"))          # the midpoint of 99 and 101

    def test_orb_a_wick_or_a_close_at_the_high_is_not_a_trigger(self):
        day = sess(100.0, {0: bar(100, 101, 99, 100), 4: bar(100, 105, 100, 100.5), 6: bar(100, 101, 100, 101)})
        self.assertEqual(bt.find_entry("orb", day, params()), (None, "no trigger"))

    def test_orb_only_inside_the_entry_window(self):
        late = sess(100.0, {0: bar(100, 101, 99, 100), 63: bar(100, 102, 100, 102)})     # closes 14:35
        self.assertEqual(bt.find_entry("orb", late, params()), (None, "no trigger"))
        ok = sess(100.0, {0: bar(100, 101, 99, 100), 62: bar(100, 102, 100, 102)})       # closes 14:30
        self.assertEqual(bt.find_entry("orb", ok, params())[0]["k"], 62)
        # a bar inside the opening range never triggers, however high it closes
        inside = sess(100.0, {0: bar(100, 101, 99, 100), 2: bar(100, 110, 100, 110)})
        self.assertEqual(bt.find_entry("orb", inside, params()), (None, "no trigger"))

    def test_vwap_reclaim_triggers_after_three_bars_under(self):
        found, why = bt.find_entry("vwap", vwap_day(), params())
        self.assertIsNone(why)
        # by hand: VWAP after 9:35 = (100 + 3 x 99.1 + 100.3) / 5 = 99.52, each bar 1,000 shares
        self.assertEqual((found["k"], found["entry"], found["vwap"]), (4, 100.9, 99.52))
        self.assertEqual(found["bars_below"], 4)         # the flat 9:15 bar closed AT VWAP: at or under
        self.assertEqual(found["stop_raw"], Decimal("98.5"))         # the pullback low

    def test_vwap_needs_the_bars_under_first(self):
        self.assertEqual(bt.find_entry("vwap", vwap_day(), params(min_bars_below=5)), (None, "no trigger"))
        day = vwap_day(v0=0)
        day[3] = bar(100, 101.5, 100, 101.4)             # back above after only two bars under: resets
        self.assertEqual(bt.find_entry("vwap", day, params()), (None, "no trigger"))

    def test_vwap_quirky_bars(self):
        # Yahoo's 60-day data: no volume on the 9:15 bar, so VWAP starts at 9:20
        found, _ = bt.find_entry("vwap", vwap_day(v0=0), params())
        self.assertEqual((found["k"], found["bars_below"]), (4, 3))
        self.assertEqual(found["vwap"], round((3 * 99.1 + 100.3) / 4, 2))
        # no volume at all: VWAP cannot be computed, and that is the reason given
        dead = {k: dict(b, v=0) for k, b in vwap_day().items()}
        self.assertEqual(bt.find_entry("vwap", dead, params()), (None, "no volume"))
        # a missing bar inside the run breaks it
        holed = vwap_day()
        del holed[2]
        self.assertEqual(bt.find_entry("vwap", holed, params()), (None, "no trigger"))

    def test_gap_and_go(self):
        found, why = bt.find_entry("gap", gap_day(), params(), prev_close=100.0)
        self.assertIsNone(why)
        self.assertEqual((found["k"], found["entry"], found["gap_pct"]), (5, 103.0, 1.5))
        self.assertEqual(found["stop_raw"], Decimal("101"))          # the opening-range low
        self.assertEqual(bt.find_entry("gap", gap_day(), params(), prev_close=101.0), (None, "no gap"))   # +0.5 %
        self.assertEqual(bt.find_entry("gap", gap_day(), params(gap_pct=2.0), prev_close=100.0), (None, "no gap"))
        self.assertEqual(bt.find_entry("gap", gap_day(), params()), (None, "no previous close"))
        quiet = fill(gap_day(), 3, 74, 102.0)
        self.assertEqual(bt.find_entry("gap", quiet, params(), prev_close=100.0), (None, "no trigger"))

    def test_previous_close_is_the_previous_nse_sessions_last_bar(self):
        days = {"2026-11-20": sess(90.0), "2026-11-23": sess(100.0, {73: None, 74: None, 72: bar(100, 100, 100, 99.5)}),
                "2026-11-25": gap_day()}
        self.assertEqual(bt.previous_close(days, "2026-11-25"), 99.5)       # across the 24 Nov holiday
        self.assertEqual(bt.previous_close(days, "2026-11-23"), 90.0)
        self.assertIsNone(bt.previous_close(days, "2026-11-20"))            # 19 Nov is not in the data
        del days["2026-11-23"]
        self.assertIsNone(bt.previous_close(days, "2026-11-25"))            # never an older session's close
        days["2026-11-23"] = sess(100.0, n=60)                              # its data stops at 14:10
        self.assertIsNone(bt.previous_close(days, "2026-11-25"))

    def test_unknown_setup(self):
        with self.assertRaises(bt.BTError):
            bt.find_entry("flag", orb_day(), params())


class ExitPlanAndWalk(unittest.TestCase):
    def test_stop_and_target_as_trader_exit_sets_them(self):
        (stop, target, risk), why = bt.exit_plan(2443.5, (Decimal("2433.7") + Decimal("2405")) / 2, 2.0)
        self.assertEqual((stop, target, risk), (Decimal("2419.35"), Decimal("2491.80"), Decimal("24.15")))
        c = exit_tool.compute(2443.5, "or_mid", None, "r", Decimal("2"), 2433.7, 2405.0)
        self.assertEqual((c["stop"], c["target"], c["risk"]), (stop, target, risk))
        # an odd midpoint rounds DOWN, away from the entry, like trader-exit
        (stop, target, _), _ = bt.exit_plan(102.0, (Decimal("101.05") + Decimal("99")) / 2, 1.5)
        c = exit_tool.compute(102.0, "or_mid", None, "r", Decimal("1.5"), 101.05, 99.0)
        self.assertEqual((stop, target), (Decimal("100.02"), c["target"]))
        self.assertEqual(c["stop"], stop)

    def test_what_trader_exit_refuses_is_no_trade(self):
        self.assertEqual(bt.exit_plan(100.0, Decimal("100"), 2.0), (None, "stop at or above the entry"))
        self.assertEqual(bt.exit_plan(1000.0, Decimal("999.60"), 2.0), (None, "stop too tight"))    # 0.04 %
        self.assertIsNotNone(bt.exit_plan(1000.0, Decimal("999.50"), 2.0)[0])                         # 0.05 %
        with self.assertRaises(exit_tool.PlanError):
            exit_tool.compute(1000.0, "price", Decimal("999.60"), "r", Decimal("2"))

    def test_target_first(self):
        day = orb_day()
        day[10] = bar(102, 106.5, 102, 106)
        self.assertEqual(bt.walk(day, 5, 100.0, 106.0, 68), ("target", 106.0, 10, False))

    def test_a_bar_that_touches_both_counts_as_the_stop(self):
        day = orb_day()
        day[10] = bar(102, 107, 99.5, 106)
        self.assertEqual(bt.walk(day, 5, 100.0, 106.0, 68), ("stop", 100.0, 10, False))
        # the stop exactly touched is a stop; the target exactly touched is a target
        day[10] = bar(102, 105, 100, 104)
        self.assertEqual(bt.walk(day, 5, 100.0, 106.0, 68)[0], "stop")
        day[10] = bar(102, 106, 101, 104)
        self.assertEqual(bt.walk(day, 5, 100.0, 106.0, 68)[0], "target")

    def test_a_bar_that_opens_under_the_stop_is_flagged(self):
        day = orb_day()
        day[10] = bar(98, 98.5, 97, 98)
        self.assertEqual(bt.walk(day, 5, 100.0, 106.0, 68), ("stop", 100.0, 10, True))

    def test_flat_by_exit_is_the_close_of_the_last_bar_before_it(self):
        day = orb_day()
        day[68] = bar(102, 103.5, 101.5, 103)            # the 14:55 bar
        day[69] = bar(103, 200, 1, 150)                  # 15:00 and later: never looked at
        self.assertEqual(bt.walk(day, 5, 100.0, 106.0, 68), ("time", 103.0, 68, False))

    def test_the_entry_bar_itself_cannot_end_the_trade(self):
        day = orb_day()
        day[5] = bar(100, 110, 90, 102)                  # its own range covers both the stop and the target
        self.assertEqual(bt.walk(day, 5, 100.0, 106.0, 68)[0], "time")


class OneTrade(unittest.TestCase):
    def test_orb_trade_end_to_end_by_hand(self):
        day = orb_day()
        day[10] = bar(102, 106.5, 102, 106)
        t, why = bt.simulate_session("orb", "DEMO", "2026-11-23", day, params(), RATES)
        self.assertIsNone(why)
        # risk 2.00 a share -> floor(500 / 2) = 250 shares (25,500 rupees, under the 40,000 limit)
        self.assertEqual((t["entry"], t["stop"], t["target"], t["qty"], t["capped"]), (102.0, 100.0, 106.0, 250, False))
        self.assertEqual((t["entry_time"], t["entry_bar"], t["outcome"], t["exit"], t["exit_time"]),
                         ("09:45", 5, "target", 106.0, "10:05"))
        self.assertEqual((t["r"], t["gross"]), (2.0, 1000.0))
        c = journal.round_trip(102.0, 106.0, 250)
        self.assertEqual((t["charges"], t["net"]), (float(c["total"]), float(c["net"])))
        self.assertEqual(t["net_r"], round(float(c["net"]) / 500.0, 4))

    def test_whole_shares_the_order_limit_and_under_one_share(self):
        day = orb_day()
        t, _ = bt.simulate_session("orb", "DEMO", "2026-11-23", day, params(risk_inr=501.99), RATES)
        self.assertEqual(t["qty"], 250)                                   # never a fraction of a share
        t, _ = bt.simulate_session("orb", "DEMO", "2026-11-23", day, params(max_order_inr=10000.0), RATES)
        self.assertEqual((t["qty"], t["capped"]), (98, True))             # floor(10,000 / 102)
        t, _ = bt.simulate_session("orb", "DEMO", "2026-11-23", day, params(max_order_inr=0), RATES)
        self.assertEqual((t["qty"], t["capped"]), (250, False))           # 0 lifts the limit
        self.assertEqual(bt.simulate_session("orb", "DEMO", "2026-11-23", day, params(risk_inr=1.5), RATES),
                         (None, "under one share"))
        self.assertEqual(bt.simulate_session("orb", "DEMO", "2026-11-23", day, params(max_order_inr=50.0), RATES),
                         (None, "under one share"))

    def test_time_exit_and_the_r_after_charges(self):
        day = orb_day()
        day[68] = bar(102, 103, 102, 103)
        t, _ = bt.simulate_session("orb", "DEMO", "2026-11-23", day, params(), RATES)
        self.assertEqual((t["outcome"], t["exit"], t["exit_time"], t["r"]), ("time", 103.0, "15:00", 0.5))
        self.assertLess(t["net_r"], t["r"])
        self.assertEqual(t["net"], round(t["gross"] - t["charges"], 2))

    def test_no_look_ahead(self):
        for setup, day, pc in (("orb", orb_day(), None), ("vwap", vwap_day(), None), ("gap", gap_day(), 100.0)):
            want, _ = bt.simulate_session(setup, "DEMO", "2026-11-23", day, params(), RATES, pc)
            k = want["entry_bar"]
            # the entry, the stop, the target and the size are the same when the later bars are cut off ...
            found_full, _ = bt.find_entry(setup, day, params(), pc)
            found_cut, _ = bt.find_entry(setup, {s: b for s, b in day.items() if s <= k}, params(), pc)
            self.assertEqual(found_cut, found_full, setup)
            # ... and when everything after the entry bar is different
            wild = dict(day)
            for s in range(k + 1, 75):
                wild[s] = bar(500, 900, 400, 700, v=10 ** 9)
            got, _ = bt.simulate_session(setup, "DEMO", "2026-11-23", wild, params(), RATES, pc)
            for key in ("entry_time", "entry_bar", "entry", "stop", "target", "qty"):
                self.assertEqual(got[key], want[key], (setup, key))
            # and no earlier bar could have been the entry
            for s in range(k):
                self.assertIsNone(bt.find_entry(setup, {x: b for x, b in day.items() if x <= s}, params(), pc)[0])

    def test_real_tcs_session_matches_the_journals_scoring(self):
        text = read(TCS60)
        days = bt.parse_sessions(text, "TCS", NOW)
        t, _ = bt.simulate_session("orb", "TCS", "2026-11-23", days["2026-11-23"], params(), RATES)
        self.assertEqual((t["entry_time"], t["entry"], t["stop"], t["target"]), ("09:45", 2443.5, 2419.35, 2491.8))
        self.assertEqual((t["qty"], t["capped"]), (16, True))             # floor(40,000 / 2,443.50), not 500 / 24.15
        self.assertEqual((t["outcome"], t["exit"], t["exit_time"], t["net"]), ("target", 2491.8, "10:10", 711.42))
        # the pack's own scorer, on the same bars, says the same
        jt = journal.make_trade("TCS", "2026-11-23", "09:45", 2443.5, 2419.35, 2491.8, 16)
        res, why = journal.simulate(jt, journal.load_days(text, "TCS", NOW)["2026-11-23"])
        self.assertIsNone(why)
        self.assertEqual((res["outcome"], res["exit_price"], res["exit_time"]), ("target", 2491.8, "10:10"))
        # and trader-entry calls the same bar on the same file
        ch = entry.parse_chart(text, "TCS")
        e = entry.evaluate(ch, entry.parse_now("2026-11-23 15:31"))
        self.assertEqual((e["trigger"]["close_time"], e["trigger"]["price"]), ("09:45", 2443.5))
        for setup, want in (("vwap", (4, 2429.0, 2401.1)), ("gap", (5, 2443.5, 2405.0))):
            t, _ = bt.simulate_session(setup, "TCS", "2026-11-23", days["2026-11-23"], params(), RATES,
                                       bt.previous_close(days, "2026-11-23"))
            e = entry.evaluate(ch, entry.parse_now("2026-11-23 15:31"), setup=setup)
            self.assertEqual((t["entry_bar"], t["entry"], t["stop"]), want)
            self.assertEqual((e["trigger"]["bar_index"], e["trigger"]["price"], e["stop_ref"]["price"]), want)


class Charges(unittest.TestCase):
    CASES = ((2443.5, 2491.8, 16), (2443.5, 2419.35, 16), (100, 101, 5), (1500, 1490.05, 300), (37.85, 38.4, 1050),
             (9.95, 9.9, 4000), (25000, 25600, 1), (102.0, 106.0, 250), (412.3, 412.3, 97))

    def test_the_table_is_trader_journals(self):
        self.assertEqual(bt.CHARGES, journal.CHARGES)
        self.assertEqual(bt.CHARGES_SOURCE, journal.CHARGES_SOURCE)

    def test_same_charges_as_the_journal_line_by_line(self):
        for buy, sell, qty in self.CASES:
            mine, theirs = bt.round_trip(buy, sell, qty), journal.round_trip(buy, sell, qty)
            for k in ("brokerage", "stt", "stamp_duty", "exchange", "sebi", "ipft", "gst", "dp", "total", "gross", "net"):
                self.assertEqual(mine[k], theirs[k], (buy, sell, qty, k))

    def test_by_hand(self):
        c = bt.round_trip(2443.5, 2491.8, 16)
        # 39,096.00 bought, 39,868.80 sold: brokerage 20 + 20 (0.1 % would be more), STT 9.97 on the sell,
        # stamp duty 1.17 on the buy, exchange 2.42, SEBI 0.08, IPFT 0.08, GST 18 % of 42.58 = 7.66
        self.assertEqual([str(c[k]) for k in ("brokerage", "stt", "stamp_duty", "exchange", "sebi", "ipft", "gst")],
                         ["40.00", "9.97", "1.17", "2.42", "0.08", "0.08", "7.66"])
        self.assertEqual((str(c["total"]), str(c["gross"]), str(c["net"])), ("61.38", "772.80", "711.42"))

    def test_a_charges_block_in_the_config_is_read_the_same_way(self):
        cfg = {"charges": {"brokerage_flat": 0, "brokerage_min": 0, "stt_sell_pct": "0.03"}}
        mine, theirs = bt.load_charges(cfg), journal.load_charges(cfg)
        self.assertEqual(mine, theirs)
        for buy, sell, qty in self.CASES:
            self.assertEqual(bt.round_trip(buy, sell, qty, mine[0])["total"],
                             journal.round_trip(buy, sell, qty, theirs[0])["total"])
        with self.assertRaises(bt.BTError):
            bt.load_charges({"charges": {"brokerage": 1}})
        with self.assertRaises(bt.BTError):
            bt.round_trip(100, 101, 0)


class Sessions(unittest.TestCase):
    def test_real_file(self):
        days = bt.parse_sessions(read(TCS60), "TCS", NOW)
        self.assertEqual(sorted(days), ["2026-11-19", "2026-11-20", "2026-11-23"])
        d = days["2026-11-23"]
        self.assertEqual(len(d), 73)
        self.assertNotIn(73, d)                           # the 15:20 bar came as nulls
        self.assertNotIn(74, d)                           # Yahoo's last trade is 15:29:34: 15:25 never completed
        self.assertEqual(d[0], {"o": 2420.0, "h": 2433.7, "l": 2405.8, "c": 2421.9, "v": 0})
        self.assertEqual(d[5]["c"], 2443.5)               # snapped to the paisa
        self.assertTrue(bt.is_full(d, 68))
        self.assertFalse(bt.is_full(d, 74))

    def test_only_complete_bars_as_of_now(self):
        text = read(TCS60)
        days = bt.parse_sessions(text, "TCS", bt.parse_now("2026-11-23 11:02"))
        self.assertEqual(max(days["2026-11-23"]), 20)     # the 10:55 bar; 11:00 is still forming
        trades, counts = bt.backtest_symbol("TCS", days, params(), ["orb"], RATES)
        self.assertEqual((counts["sessions"], counts["left_out"]), (2, 1))
        self.assertFalse([t for t in trades if t["date"] == "2026-11-23"])

    def test_off_grid_out_of_hours_and_null_bars_are_dropped(self):
        d = json.loads(chart_text("DEMO", {"2026-11-23": sess(100.0, n=3)}))
        r = d["chart"]["result"][0]
        r["timestamp"] += [ts("2026-11-23", 3) + 45, ts("2026-11-23", -3), ts("2026-11-23", 80), ts("2026-11-23", 4)]
        q = r["indicators"]["quote"][0]
        for k in q:
            q[k] += [100.0, 100.0, 100.0, None]
        days = bt.parse_sessions(json.dumps(d), "DEMO", NOW)
        self.assertEqual(sorted(days["2026-11-23"]), [0, 1, 2])

    def test_wrong_symbol_wrong_bars_and_empty_answers(self):
        with self.assertRaises(bt.BTError):
            bt.parse_sessions(read(TCS60), "INFY", NOW)
        d = json.loads(chart_text("DEMO", {"2026-11-23": sess()}))
        d["chart"]["result"][0]["meta"]["dataGranularity"] = "1d"
        with self.assertRaises(bt.BTError):
            bt.parse_sessions(json.dumps(d), "DEMO", NOW)
        for text in ("not json", '{"chart": {"result": null, "error": {"description": "No data found"}}}'):
            with self.assertRaises(bt.BTError):
                bt.parse_sessions(text, "DEMO", NOW)

    def test_a_session_with_a_hole_before_flat_by_is_left_out_not_patched(self):
        days = {"2026-11-20": orb_day(), "2026-11-23": orb_day()}
        del days["2026-11-23"][30]                        # the 11:45 bar is missing
        trades, counts = bt.backtest_symbol("DEMO", days, params(), ["orb"], RATES)
        self.assertEqual((counts["sessions"], counts["left_out"]), (1, 1))
        self.assertEqual([t["date"] for t in trades], ["2026-11-20"])
        # a hole after flat-by (Yahoo's usual 15:15 to 15:25) does not matter
        days["2026-11-23"] = orb_day()
        for k in (72, 73, 74):
            del days["2026-11-23"][k]
        self.assertEqual(bt.backtest_symbol("DEMO", days, params(), ["orb"], RATES)[1]["sessions"], 2)

    def test_reasons_for_no_trade_are_counted(self):
        days = {"2026-11-20": sess(100.0), "2026-11-23": gap_day()}
        trades, counts = bt.backtest_symbol("DEMO", days, params(), list(bt.SETUPS), RATES)
        self.assertEqual(sorted((t["date"], t["setup"]) for t in trades),
                         [("2026-11-23", "gap"), ("2026-11-23", "orb")])
        self.assertEqual(counts["skips"]["gap"], {"no previous close": 1})
        self.assertEqual(counts["skips"]["orb"], {"no trigger": 1})
        self.assertEqual(counts["skips"]["vwap"], {"no trigger": 2})
        self.assertEqual(counts["no_open_volume"], 0)


def T(date, sym, net, r, net_r, gross, charges, outcome, time="09:45"):
    return {"setup": "orb", "symbol": sym, "date": date, "entry_time": time, "net": net, "r": r, "net_r": net_r,
            "gross": gross, "charges": charges, "outcome": outcome, "capped": False, "opened_under_stop": False}


FIVE = [T("2026-11-18", "CCC", 30.0, 0.8, 0.6, 40.0, 10.0, "time"),
        T("2026-11-02", "AAA", 100.0, 2.0, 1.8, 120.0, 20.0, "target"),
        T("2026-11-04", "BBB", -55.0, -1.0, -1.1, -45.0, 10.0, "stop"),
        T("2026-11-03", "AAA", -60.0, -1.0, -1.2, -50.0, 10.0, "stop"),
        T("2026-11-17", "BBB", -5.0, 0.1, -0.1, 5.0, 10.0, "time")]


class Arithmetic(unittest.TestCase):
    def test_the_scoreboard_by_hand(self):
        s = bt.summarize(FIVE)
        self.assertEqual((s["trades"], s["symbols"], s["from"], s["to"]), (5, 3, "2026-11-02", "2026-11-18"))
        self.assertEqual((s["wins"], s["losses"], s["win_rate"]), (2, 3, 40.0))
        self.assertEqual(s["avg_r"], 0.18)                # (2 - 1 - 1 + 0.1 + 0.8) / 5
        self.assertEqual(s["avg_net_r"], 0.0)             # (1.8 - 1.2 - 1.1 - 0.1 + 0.6) / 5
        self.assertEqual((s["gross"], s["charges"], s["net"], s["avg_charges"]), (70.0, 60.0, 10.0, 12.0))
        self.assertEqual(s["largest_loss"], {"net": -60.0, "symbol": "AAA", "date": "2026-11-03", "entry_time": "09:45"})
        self.assertEqual(s["longest_losing_run"], 3)      # 3, 4 and 17 Nov, in date order
        self.assertEqual(s["ended_by"], {"target": {"trades": 1, "pct": 20.0}, "stop": {"trades": 2, "pct": 40.0},
                                         "time": {"trades": 2, "pct": 40.0}})
        self.assertEqual(s["by_fortnight"], [
            {"label": "1-15 Nov 2026", "trades": 3, "win_rate": 33.3, "avg_r": 0.0, "avg_net_r": -0.167, "net": -15.0},
            {"label": "16-30 Nov 2026", "trades": 2, "win_rate": 50.0, "avg_r": 0.45, "avg_net_r": 0.25, "net": 25.0}])
        self.assertEqual([(g["symbol"], g["trades"], g["net"]) for g in s["best_symbols"]],
                         [("AAA", 2, 40.0), ("CCC", 1, 30.0), ("BBB", 2, -60.0)])
        self.assertEqual([g["symbol"] for g in s["worst_symbols"]], ["BBB", "CCC", "AAA"])

    def test_a_charge_only_loser_is_a_loss_and_no_loss_is_none(self):
        s = bt.summarize([T("2026-11-17", "BBB", -5.0, 0.1, -0.1, 5.0, 10.0, "time")])
        self.assertEqual((s["wins"], s["losses"], s["win_rate"]), (0, 1, 0.0))
        s = bt.summarize([T("2026-11-17", "BBB", 5.0, 0.3, 0.1, 15.0, 10.0, "time")])
        self.assertIsNone(s["largest_loss"])
        self.assertEqual(s["longest_losing_run"], 0)
        self.assertEqual(bt.summarize([]), {"trades": 0, "enough": False, "min_trades": 30})

    def test_too_few_trades_says_so(self):
        s = bt.summarize(FIVE)
        self.assertFalse(s["enough"])
        text = "\n".join(bt.setup_lines("orb", s, params()))
        self.assertIn("Only 5 trades: too few trades to conclude anything. Under 30, a good or a bad result is "
                      "mostly luck.", text)
        self.assertIn("(too few to read)", text)
        enough = bt.summarize(FIVE, min_trades=5)
        self.assertTrue(enough["enough"])
        self.assertNotIn("too few trades to conclude anything", "\n".join(bt.setup_lines("orb", enough, params())))
        none = "\n".join(bt.setup_lines("gap", bt.summarize([]), params()))
        self.assertIn("No trades. Only 0 trades: too few trades to conclude anything.", none)
        self.assertIn("Only 1 trade:", bt.few_line(1, 30))

    def test_the_lines_carry_the_numbers(self):
        text = "\n".join(bt.setup_lines("orb", bt.summarize(FIVE), params()))
        for want in ("Trades               5 in 3 symbols, 2026-11-02 to 2026-11-18",
                     "Win rate             40.0 % after charges",
                     "Average R            +0.18R before charges, +0.00R after",
                     "Net P&L              +%s10.00 (gross +%s70.00, charges %s60.00, about %s12.00 a trade)" % ((RS,) * 4),
                     "Largest loss         -%s60.00 (AAA, 2026-11-03 at 09:45)" % RS,
                     "Longest losing run   3 trades in a row",
                     "Ended by             target 20.0 %, stop 40.0 %, time (flat-by) 40.0 %",
                     "1-15 Nov 2026",
                     "Best symbols by net P&L",
                     "BBB            2 trades  net -%s60.00" % RS):
            self.assertIn(want, text)
        self.assertIn("after 3 bars at or under it", bt.setup_title("vwap", params()))
        self.assertIn("opens 1.5 % or more", bt.setup_title("gap", params(gap_pct=1.5)))


class Fetching(NoNetwork):
    def charts(self):
        return {"AAA": chart_text("AAA", {"2026-11-20": orb_day(), "2026-11-23": orb_day(103.0)}),
                "BBB": chart_text("BBB", {"2026-11-20": sess(50.0), "2026-11-23": gap_day()})}

    def go(self, symbols, **kw):
        net = {}
        data, trades = bt.run_backtest(symbols, params(), RATES, NOW, os.path.join("trader", "cache"), net=net, **kw)
        return data, trades

    def test_one_request_per_symbol_then_none(self):
        calls = self.serve(self.charts())
        data, trades = self.go(["AAA", "BBB"])
        self.assertEqual([re.search(r"chart/(\w+)", u).group(1) for u, _ in calls], ["AAA", "BBB"])
        self.assertTrue(all("query2.finance.yahoo.com" in u and "range=60d&interval=5m" in u for u, _ in calls))
        self.assertEqual((data["requests"], data["from_yahoo"], data["from_cache"], data["symbols_ok"]), (2, 2, 0, 2))
        self.assertEqual(sorted(os.listdir(os.path.join("trader", "cache"))),
                         ["AAA-60d5m-2026-11-24.json.gz", "BBB-60d5m-2026-11-24.json.gz"])
        with gzip.open(os.path.join("trader", "cache", "AAA-60d5m-2026-11-24.json.gz"), "rt", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), self.charts()["AAA"])
        # the second run asks Yahoo nothing and gives the same trades
        calls2 = self.serve({})
        data2, trades2 = self.go(["AAA", "BBB"])
        self.assertEqual(calls2, [])
        self.assertEqual((data2["requests"], data2["from_yahoo"], data2["from_cache"]), (0, 0, 2))
        self.assertEqual(trades2, trades)
        self.assertTrue(trades)

    def test_at_least_half_a_second_between_requests(self):
        calls = self.serve(self.charts())
        self.go(["AAA", "BBB"])
        self.assertGreaterEqual(round(calls[1][1] - calls[0][1], 6), 0.5)
        self.assertEqual([round(s, 6) for s in self.sleeps], [0.5])
        # a longer pause is honoured, a shorter one is not
        shutil.rmtree("trader")
        self.sleeps[:] = []
        self.serve(self.charts())
        self.go(["AAA", "BBB"], pause=0.1)
        self.assertEqual([round(s, 6) for s in self.sleeps], [0.5])
        shutil.rmtree("trader")
        self.sleeps[:] = []
        self.serve(self.charts())
        self.go(["AAA", "BBB"], pause=2.0)
        self.assertEqual([round(s, 6) for s in self.sleeps], [2.0])

    def test_a_429_gets_one_retry_and_never_a_third_request(self):
        ok = self.charts()["AAA"]
        calls = self.serve({"AAA": [http(429), ok]})
        data, _ = self.go(["AAA"])
        self.assertEqual(len(calls), 2)
        self.assertIn("query2.", calls[0][0])
        self.assertIn("query1.", calls[1][0])
        self.assertIn(bt.BACKOFF, self.sleeps)
        self.assertEqual((data["symbols_ok"], data["failed"]), (1, []))
        shutil.rmtree("trader")
        calls = self.serve({"AAA": [http(429), http(429), ok]})
        data, _ = self.go(["AAA"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(data["symbols_ok"], 0)
        self.assertIn("after one retry", data["failed"][0]["why"])
        self.assertFalse(os.path.exists(os.path.join("trader", "cache")) and os.listdir(os.path.join("trader", "cache")))

    def test_a_failed_symbol_is_fetched_again_next_time_and_only_that_one(self):
        charts = self.charts()
        calls = self.serve({"AAA": charts["AAA"], "BBB": [http(503), urllib.error.URLError("down")]})
        data, _ = self.go(["AAA", "BBB"])
        self.assertEqual((data["symbols_ok"], [f["symbol"] for f in data["failed"]]), (1, ["BBB"]))
        self.assertEqual(len(calls), 3)
        calls = self.serve({"BBB": charts["BBB"]})          # resuming: AAA is in the cache
        data, _ = self.go(["AAA", "BBB"])
        self.assertEqual([re.search(r"chart/(\w+)", u).group(1) for u, _ in calls], ["BBB"])
        self.assertEqual((data["symbols_ok"], data["from_cache"], data["from_yahoo"], data["failed"]), (2, 1, 1, []))

    def test_a_symbol_yahoo_does_not_have_is_remembered_for_the_day(self):
        nothing = '{"chart": {"result": null, "error": {"code": "Not Found", "description": "No data found"}}}'
        calls = self.serve({"AAA": self.charts()["AAA"], "GONE": http(404), "EMPTY": nothing})
        data, _ = self.go(["GONE", "EMPTY", "AAA"])
        self.assertEqual(len(calls), 3)
        self.assertEqual([f["symbol"] for f in data["failed"]], ["GONE", "EMPTY"])
        self.assertIn("No data found", data["failed"][1]["why"])
        calls = self.serve({})
        data, _ = self.go(["GONE", "EMPTY", "AAA"])
        self.assertEqual(calls, [])
        self.assertEqual(len(data["failed"]), 2)

    def test_rate_limited_five_times_in_a_row_stops_and_resumes(self):
        charts = self.charts()
        syms = ["S%d" % i for i in range(1, 8)]
        answers = {s: [http(429), http(429)] for s in syms}
        calls = self.serve(answers)
        data, trades = self.go(syms)
        self.assertEqual(len(calls), 10)                    # five symbols, two requests each, then it stops
        self.assertIn("rate limited 5 symbols in a row", data["stopped"])
        self.assertEqual((len(data["failed"]), data["not_fetched"]), (5, 2))
        doc = bt.build_doc(syms, "the symbols you named", None, params(), data, trades, NOW)
        self.assertFalse(doc["complete"])
        self.assertIn("INCOMPLETE:", "\n".join(bt.report_lines(doc)))

    def test_no_fetch_uses_only_the_cache(self):
        self.serve(self.charts())
        self.go(["AAA"])
        calls = self.serve({})
        data, _ = self.go(["AAA", "BBB"], fetch=False)
        self.assertEqual(calls, [])
        self.assertEqual((data["symbols_ok"], data["not_fetched"], data["failed"]), (1, 1, []))

    def test_yesterdays_cache_is_not_used_and_is_cleaned_up(self):
        cache = os.path.join("trader", "cache")
        os.makedirs(cache)
        old = os.path.join(cache, "AAA-60d5m-2026-11-23.json.gz")
        with gzip.open(old, "wt", encoding="utf-8") as fh:
            fh.write(self.charts()["AAA"])
        other = os.path.join(cache, "AAAB-60d5m-2026-11-23.json.gz")
        shutil.copy(old, other)
        calls = self.serve(self.charts())
        self.go(["AAA"])
        self.assertEqual(len(calls), 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(other))               # another symbol's file is left alone
        self.assertTrue(os.path.exists(os.path.join(cache, "AAA-60d5m-2026-11-24.json.gz")))

    def test_the_user_agent_is_the_packs_own(self):
        seen = []

        class Answer(object):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"

        def fake_urlopen(req, timeout=None):
            seen.append((req.full_url, req.get_header("User-agent"), req.get_method(), req.header_items()))
            return Answer()

        real, bt.urllib.request.urlopen = bt.urllib.request.urlopen, fake_urlopen
        try:
            self._saved[0]("https://query2.finance.yahoo.com/v8/finance/chart/TCS.NS?range=60d&interval=5m")
        finally:
            bt.urllib.request.urlopen = real
        url, ua, method, headers = seen[0]
        self.assertEqual(ua, "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)")
        self.assertEqual(method, "GET")
        names = sorted(k.lower() for k, _ in headers)
        self.assertEqual(names, ["accept", "user-agent"])    # no cookie, no referer, no origin

    def test_symbols_with_an_ampersand(self):
        calls = self.serve({"M&M": chart_text("M&M", {"2026-11-23": orb_day()})})
        data, trades = self.go(["M&M"])
        self.assertIn("M%26M.NS", calls[0][0])
        self.assertEqual((data["symbols_ok"], trades[0]["symbol"]), (1, "M&M"))
        self.assertTrue(os.path.exists(os.path.join("trader", "cache", "M&M-60d5m-2026-11-24.json.gz")))


class Universe(NoNetwork):
    def test_the_nifty_500_file(self):
        with open(NIFTY, encoding="utf-8") as fh:
            text = fh.read()
        # series EQ with a real ISIN only: no BE stock (no intraday there) and no placeholder row
        self.assertEqual(bt.parse_symbol_list(text), ["INFY", "M&M", "TCS"])
        self.assertEqual(bt.parse_symbol_list("TCS|Tata Consultancy\n# a note\ninfy\nM&M, anything\nTCS\n??\n"),
                         ["TCS", "INFY", "M&M"])

    def test_nifty500_is_one_request_cached_for_the_day(self):
        rows = ["Company Name,Industry,Symbol,Series,ISIN Code"] + [
            "Company %d Ltd.,Services,SYM%d,EQ,INE%06dA01" % (i, i, i) for i in range(120)]
        calls = self.serve({"NIFTY": "\n".join(rows) + "\n"})
        net = {}
        cache = os.path.join("trader", "cache")
        syms = bt.nifty500(cache, "2026-11-24", net)
        self.assertEqual((len(syms), syms[0], len(calls)), (120, "SYM0", 1))
        self.assertTrue(calls[0][0].startswith("https://nsearchives.nseindia.com/content/indices/"))
        self.assertEqual(bt.nifty500(cache, "2026-11-24", net), syms)
        self.assertEqual(len(calls), 1)
        with self.assertRaises(bt.BTError):
            bt.nifty500(cache, "2026-11-25", net, fetch=False)

    def test_nifty500_failures_say_what_to_do(self):
        self.serve({"NIFTY": http(403)})
        with self.assertRaises(bt.BTError) as cm:
            bt.nifty500("c", "2026-11-24", {})
        self.assertIn("pass --universe FILE", str(cm.exception))
        self.serve({"NIFTY": "<html>Access denied</html>"})
        with self.assertRaises(bt.BTError):
            bt.nifty500("c", "2026-11-24", {})


class Cli(NoNetwork):
    def setUp(self):
        NoNetwork.setUp(self)
        os.mkdir("charts")
        shutil.copy(TCS60, os.path.join("charts", "TCS-60d5m.json"))

    def test_run_offline_writes_the_report_and_the_json(self):
        code, text, err = run(["run", "TCS", "--offline", "charts", "--now", "2026-11-24 10:00", "--trades"])
        self.assertEqual(code, 0, err)
        self.assertIn("[1/1] TCS", err)                     # progress goes to stderr
        self.assertIn("as of 2026-11-24 10:00 IST", text)
        self.assertIn("1 symbol asked, 1 with usable bars, 0 failed", text)
        self.assertIn("Sessions: 3 symbol-days from 2026-11-19 to 2026-11-23; 0 more left out", text)
        self.assertIn("In 3 of them the 9:15 bar came with no volume", text)
        self.assertIn("Sizing: %s500.00 at risk a trade, whole shares, no order above %s40,000.00" % (RS, RS), text)
        self.assertIn("Groww's pricing page", text)
        for name in ("Opening-range breakout (orb)", "VWAP reclaim (vwap)", "Gap and go (gap)"):
            self.assertIn(name, text)
        self.assertIn("too few trades to conclude anything", text)
        self.assertIn("Wrote trader/backtest.json.", text)
        with open(os.path.join("trader", "backtest.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual((doc["created_by"], doc["complete"], doc["as_of"]), ("trader-backtest", True, "2026-11-24 10:00"))
        self.assertEqual(doc["params"]["risk_inr"], 500.0)
        self.assertEqual(doc["data"]["requests"], 0)
        monday = {t["setup"]: t for t in doc["trades"] if t["date"] == "2026-11-23"}
        self.assertEqual((monday["orb"]["outcome"], monday["orb"]["net"]), ("target", 711.42))
        self.assertEqual((monday["vwap"]["entry"], monday["vwap"]["stop"], monday["vwap"]["outcome"]),
                         (2429.0, 2401.1, "target"))
        self.assertEqual((monday["gap"]["stop"], monday["gap"]["qty"], monday["gap"]["outcome"], monday["gap"]["exit"]),
                         (2405.0, 12, "time", 2453.2))
        for setup in bt.SETUPS:
            s = doc["setups"][setup]
            mine = [t for t in doc["trades"] if t["setup"] == setup]
            self.assertEqual(s["trades"], len(mine))
            self.assertEqual(s["net"], round(sum(t["net"] for t in mine), 2))
            self.assertFalse(s["enough"])
        self.assertEqual(len(doc["caveats"]), 6)

    def test_the_caveats_are_printed_every_time(self):
        code, text, _ = run(["run", "TCS", "--offline", "charts", "--now", "2026-11-24 10:00", "--quiet", "--no-write"])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists("trader"))
        low = " ".join(text.lower().split())
        for must in ("about 60 sessions is one market phase", "fills are optimistic", "survivorship",
                     "holes", "picking the best one overstates it", "not the result of one account",
                     "not a prediction and not advice", "not financial advice", "most people who day trade lose money"):
            self.assertIn(must, low, must)
        for never in ("you should", "recommend", "will make", "expected return", chr(0x2014)):
            self.assertNotIn(never, low, never)

    def test_show_json_and_flags(self):
        code, text, _ = run(["run", "TCS", "--offline", "charts", "--now", "2026-11-24 10:00", "--quiet",
                             "--setups", "orb", "--risk", "250", "--max-order", "0", "--min-trades", "1"])
        self.assertEqual(code, 0)
        self.assertNotIn("VWAP reclaim (vwap)", text)
        self.assertNotIn("too few trades to conclude anything", text)
        self.assertIn("no cap on the order size", text)
        code, again, _ = run(["show"])
        self.assertEqual(code, 0)
        self.assertEqual(again.strip(), text.replace("Wrote trader/backtest.json.", "").strip())
        code, text, _ = run(["show", "--json"])
        doc = json.loads(text)
        self.assertEqual((doc["params"]["setups"], doc["params"]["risk_inr"], doc["params"]["max_order_inr"]),
                         (["orb"], 250.0, 0.0))
        self.assertNotIn("trades", doc)
        code, text, _ = run(["run", "TCS", "--offline", "charts", "--now", "2026-11-24 10:00", "--quiet", "--json",
                             "--no-write"])
        self.assertEqual(json.loads(text)["created_by"], "trader-backtest")

    def test_config_is_read(self):
        os.mkdir("trader")
        with open(os.path.join("trader", "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"max_loss_per_trade_inr": 300, "max_order_inr": 20000, "flat_by": "14:00",
                       "no_entries_after": "11:00", "charges": {"brokerage_flat": 0, "brokerage_min": 0}}, fh)
        code, text, err = run(["run", "TCS", "--offline", "charts", "--now", "2026-11-24 10:00", "--quiet", "--json"])
        self.assertEqual(code, 0, err)
        p = json.loads(text)["params"]
        self.assertEqual((p["risk_inr"], p["max_order_inr"], p["flat_by"], p["no_entries_after"]),
                         (300.0, 20000.0, "14:00", "11:00"))
        self.assertEqual(sorted(p["charges_overridden"]), ["brokerage_flat", "brokerage_min"])
        code, text, _ = run(["run", "TCS", "--offline", "charts", "--now", "2026-11-24 10:00", "--quiet", "--risk", "700"])
        self.assertIn("%s700.00 at risk a trade" % RS, text)
        self.assertIn("with these lines from your config: brokerage_flat, brokerage_min", text)

    def test_defaults_are_the_example_config(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json"), encoding="utf-8") as fh:
            example = json.load(fh)
        for k, v in bt.DEFAULTS.items():
            self.assertEqual(example[k], v, k)
        a = argparse.Namespace(setups="orb,vwap,gap", risk=None, max_order=None, flat_by=None, target_r=2.0,
                               gap_pct=1.0, min_bars_below=3, min_trades=30)
        p, _ = bt.build_params(a, {})
        self.assertEqual((p["last"], p["k_min"], p["k_max"]), (68, 2, 62))    # 14:55, 9:25 and 14:25 bars
        self.assertEqual((p["risk_inr"], p["max_order_inr"]), (500.0, 40000.0))

    def test_universe_file_watchlist_and_limit(self):
        with open("list.txt", "w", encoding="utf-8") as fh:
            fh.write("TCS\nZZZZ\n")
        code, text, err = run(["run", "--universe", "list.txt", "--offline", "charts", "--now", "2026-11-24 10:00"])
        self.assertEqual(code, 0, err)
        self.assertIn("your list (list.txt): 2 symbols asked, 1 with usable bars, 1 failed", text)
        self.assertIn("Failed: ZZZZ (no ZZZZ-60d5m.json(.gz) in charts)", text)
        self.assertIn("FAILED", err)
        code, text, _ = run(["run", "--universe", "list.txt", "--limit", "1", "--offline", "charts", "--quiet",
                             "--now", "2026-11-24 10:00"])
        self.assertIn("the first 1: 1 symbol asked, 1 with usable bars, 0 failed", text)
        os.mkdir("trader2")
        with open(os.path.join("trader2", "watchlist.json"), "w", encoding="utf-8") as fh:
            json.dump({"hits": [{"symbol": "TCS", "rule": "orb"}, {"symbol": "TCS.NS"}]}, fh)
        code, text, _ = run(["run", "--universe", "watchlist", "--watchlist", os.path.join("trader2", "watchlist.json"),
                             "--offline", "charts", "--quiet", "--now", "2026-11-24 10:00"])
        self.assertEqual(code, 0)
        self.assertIn("your watchlist (trader2/watchlist.json): 1 symbol asked", text)

    def test_nifty500_from_todays_cache_makes_no_request(self):
        cache = os.path.join("trader", "cache")
        os.makedirs(cache)
        shutil.copy(NIFTY, os.path.join(cache, "ind_nifty500list-2026-11-24.csv"))
        code, text, err = run(["run", "--universe", "nifty500", "--no-fetch", "--now", "2026-11-24 10:00"])
        self.assertEqual(code, 1)                           # nothing in the cache yet, and nothing fetched
        self.assertIn("nothing to test", err)
        with open(TCS60, "rb") as src, gzip.open(os.path.join(cache, "TCS-60d5m-2026-11-24.json.gz"), "wb") as dst:
            dst.write(src.read())
        code, text, err = run(["run", "--universe", "nifty500", "--no-fetch", "--now", "2026-11-24 10:00"])
        self.assertEqual(code, 1)                           # a report, but an incomplete one
        self.assertIn("the Nifty 500 as NSE lists it today: 3 symbols asked, 1 with usable bars, 0 failed, 2 not fetched",
                      text)
        self.assertIn("INCOMPLETE: 2 symbols were not in the cache", text)

    def test_errors(self):
        for argv, msg in ((["run"], "which stocks?"),
                          (["run", "TCS", "--universe", "nifty500"], "not both"),
                          (["run", "TCS", "--offline", "charts", "--pause", "0.1"], "--pause cannot be under 0.5"),
                          (["run", "TCS", "--offline", "charts", "--setups", "orb,flag"], "--setups takes orb, vwap, gap"),
                          (["run", "TCS", "--offline", "charts", "--risk", "0"], "--risk must be above 0"),
                          (["run", "TCS", "--offline", "charts", "--flat-by", "15:02"], "5-minute grid"),
                          (["run", "TCS", "--offline", "nowhere"], "--offline wants a folder"),
                          (["run", "BRK.B", "--offline", "charts"], "not an NSE equity symbol"),
                          (["run", "ZZZZ", "--offline", "charts"], "nothing to test"),
                          (["run", "--universe", "missing.txt"], "there is no file missing.txt"),
                          (["run", "--universe", "watchlist"], "run /trader-scanner first"),
                          (["run", "TCS", "--offline", "charts", "--limit", "0"], "--limit must be 1 or more"),
                          (["show"], "run backtest.py run first")):
            code, _, err = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertIn(msg, err, argv)
        self.assertEqual(run([])[0], 1)

    def test_the_rupee_sign_survives_a_windows_pipe(self):
        env = dict(os.environ)
        env.pop("PYTHONIOENCODING", None)
        env.pop("PYTHONUTF8", None)
        p = subprocess.run([sys.executable, os.path.join(ROOT, "trader-backtest", "backtest.py"), "run", "TCS",
                            "--offline", "charts", "--now", "2026-11-24 10:00", "--no-write"],
                           capture_output=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("%s500.00 at risk a trade" % RS, p.stdout.decode("utf-8"))


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-backtest", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-backtest\n"))
        head = text.split("---")[1]
        for field in ("name:", "description:", "argument-hint:"):
            self.assertIn(field, head)
        self.assertGreater(len(" ".join(head.split("description:")[1].split("argument-hint:")[0].split())), 200)
        self.assertGreater(len(text.splitlines()), 60)
        low = " ".join(text.lower().split())
        for must in ('python "${claude_skill_dir}/backtest.py"', "python3", "--universe nifty500", "--limit",
                     "after charges", "trader/cache", "trader/backtest.json", "no look-ahead", "counts as the stop",
                     "one market phase", "optimistic", "survivorship", "holes", "picking the best one",
                     "too few trades to conclude anything", "not a prediction", "not financial advice",
                     "explicit yes", "india standard time", ".ns", "most people who day trade lose money",
                     "groww", "0.5 seconds", "one retry"):
            self.assertIn(must, low, must)
        self.assertNotRegex(text, r"\$\d")
        for never in (chr(0x2014), "new york", "nyse", "alpaca", "guarantee", "you should buy"):
            self.assertNotIn(never, low, never)


if __name__ == "__main__":
    unittest.main()
