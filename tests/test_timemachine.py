import datetime
import gzip
import hashlib
import io
import json
import os
import random
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

tm = load("trader-timemachine", "timemachine")

DIR = os.path.join(FIX, "trader-timemachine")
# Yahoo v8 chart for TCS.NS, range=60d interval=5m, fetched 2026-11-24 (an NSE holiday): 60 sessions,
# 2026-08-26 to 2026-11-23, 4,500 bars of which 111 have no price (almost all in the last 15 minutes).
TCS = os.path.join(DIR, "TCS-60d5m.json.gz")
with gzip.open(TCS, "rt", encoding="utf-8") as _fh:
    TEXT = _fh.read()
NOW = "2026-11-23 16:00"
NOW_UTC = tm.parse_now(NOW)
UNTIL = "15:00"
RUPEE = chr(0x20b9)

# A real plan, built by hand from the fixture's bars of Monday 2026-11-23, in the shape trader-exit writes:
# opening range 2405.00 to 2433.70 (the 09:15, 09:20 and 09:25 bars), the 09:40 bar closes at 2443.50
PLAN = {"created_by": "trader-exit", "symbol": "TCS", "side": "buy", "date": "2026-11-23", "setup": "orb",
        "entry": 2443.50, "entry_time": "9:45 AM", "entry_bar": 5, "interval": "5m",
        "opening_range": {"high": 2433.70, "low": 2405.00, "minutes": 15},
        "stop": 2419.35, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 2467.65, "target_r": 1.0, "target_label": "1R (one times the risk)",
        "risk_per_share": 24.15, "reward_risk": 1.0, "flat_by": "15:00"}

# fingerprints of the first run of the ported tool on this fixture (seed 19): a change in the draws shows here
SIM_BANDS = "661d2ed28d50423b"
SIM_PATHS = "a4309fc4121214dd"
SIM_TRADES = "c1a9ac758fcaabe2"
SIM_HIST = {"lo": 2373.94, "hi": 2497.0,
            "bins": [111, 87, 155, 216, 310, 378, 479, 598, 652, 758, 705, 833, 826, 757, 666, 579, 450, 371,
                     303, 200, 151, 114, 84, 56]}


def sha(x):
    return hashlib.sha256(json.dumps(x).encode("utf-8")).hexdigest()[:16]


def plan(**kw):
    raw = dict(PLAN)
    raw.update(kw)
    return tm.normalize_plan(raw)


def machine(p=None, text=TEXT, now=NOW_UTC, until=UNTIL, **kw):
    return tm.time_machine(text, p or plan(), now, until=until, **kw)


def edited(fn):
    """The fixture's chart JSON, changed by fn(result), back as text."""
    doc = json.loads(TEXT)
    fn(doc["chart"]["result"][0])
    return json.dumps(doc)


def keep_bars(result, keep):
    """Keep only the bars where keep(ts) is true."""
    q = result["indicators"]["quote"][0]
    idx = [i for i, ts in enumerate(result["timestamp"]) if keep(ts)]
    result["timestamp"] = [result["timestamp"][i] for i in idx]
    for k in q:
        q[k] = [q[k][i] for i in idx]


def ist(ts):
    return tm.to_ist(tm.utc_of(ts))


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tm.main(argv)
    return code, out.getvalue(), err.getvalue()


class RealPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = machine()

    def test_the_odds(self):
        m = self.doc["mc"]
        self.assertEqual((m["replays"], m["seed"], m["window"], m["until"]), (10000, 19, 2, "15:00"))
        self.assertEqual(m["pool_sessions"], 58)
        self.assertEqual(m["bars_left"], 63)
        self.assertEqual((m["pool_from"], m["pool_to"]), ("2026-08-26", "2026-11-20"))
        self.assertEqual(m["p_target"], 0.2383)
        self.assertEqual(m["p_stop"], 0.4708)
        self.assertEqual(m["p_neither"], 0.2909)
        self.assertEqual(m["p_green"], 0.3584)
        self.assertEqual(m["median_close"], 2433.34)
        self.assertEqual(m["p5_close"], 2391.89)
        self.assertEqual(m["p95_close"], 2476.6)
        self.assertEqual(m["avg_R"], -0.258)
        self.assertEqual(m["r_at_target"], 1.0)

    def test_the_draws_are_pinned(self):
        m = self.doc["mc"]
        self.assertEqual(sha(m["bands"]), SIM_BANDS)
        self.assertEqual(len(m["sample_paths"]), 160)
        self.assertEqual(sha(m["sample_paths"]), SIM_PATHS)
        self.assertEqual(m["hist"], SIM_HIST)
        self.assertEqual(m["bands"][-1], [2391.03, 2414.6, 2432.53, 2450.04, 2475.7])

    def test_the_history_replay(self):
        h = self.doc["history"]
        self.assertEqual((h["sessions"], h["triggered"]), (58, 28))
        self.assertEqual((h["target"], h["stop"], h["close"], h["green"]), (6, 13, 9, 10))
        self.assertEqual(h["avg_R"], -0.255)
        self.assertEqual(h["rule"], {"setup": "orb", "or_minutes": 15, "stop_rule": "or_mid", "target_r": 1.0})
        slim = [{"date": t["date"], "entry": t["entry"], "outcome": t["outcome"], "R": t["R"]} for t in h["trades"]]
        self.assertEqual(sha(slim), SIM_TRADES)
        first = h["trades"][0]
        self.assertEqual((first["date"], first["time"], first["entry"], first["stop"], first["target"],
                          first["outcome"], first["R"]),
                         ("2026-08-28", "9:35 AM", 2328.5, 2296.0, 2361.0, "close", 0.28))

    def test_the_verdict(self):
        self.assertEqual(self.doc["verdict"], "Strongly toward the stop: target first 23.8 % vs stop first 47.1 %.")
        # 1R breaks even at 50 % of the replays that hit one side; the target got 33.6 %
        gap, share, even = tm.lean(self.doc["mc"])
        self.assertEqual((round(share, 3), even), (0.336, 0.5))
        self.assertEqual(self.doc["verdict_notes"], [])

    def test_bands_start_at_the_entry(self):
        m = self.doc["mc"]
        self.assertEqual(m["bands"][0], [2443.5] * 5)
        self.assertEqual(len(m["bands"]), 64)
        self.assertEqual((m["band_times"][0], m["band_times"][-1], len(m["band_times"])), ("09:45", "15:00", 64))
        self.assertTrue(all(p[0] == 2443.5 and len(p) == 64 for p in m["sample_paths"]))

    def test_the_plan_is_carried_through(self):
        p = self.doc["plan"]
        self.assertEqual((p["symbol"], p["entry"], p["stop"], p["target"], p["entry_bar"], p["entry_time"]),
                         ("TCS", 2443.5, 2419.35, 2467.65, 5, "9:45 AM"))
        self.assertEqual((p["risk_per_share"], p["target_r"], p["flat_by"]), (24.15, 1.0, "15:00"))
        self.assertEqual(self.doc["warnings"], [])
        self.assertEqual(self.doc["data"]["symbol"], "TCS")          # Yahoo says TCS.NS
        self.assertEqual(self.doc["data"]["last_bar"], "2026-11-23 15:15 IST")

    def test_the_text_report(self):
        text = "\n".join(tm.report(self.doc))
        R = RUPEE
        for line in ("TCS buy, Monday 2026-11-23: entry %s2,443.50 at 9:45 AM (bar 5), stop %s2,419.35, "
                     "target %s2,467.65 (1R, risk %s24.15 a share)." % (R, R, R, R),
                     "10,000 replays", "9:45 AM to 3:00 PM (63 five-minute bars)", "seed 19", "58 full sessions",
                     "target first   23.8 %", "stop first     47.1 %", "neither        29.1 %   still open at 3:00 PM",
                     "green          35.8 %",
                     "median %s2,433.34, 90 %% of replays between %s2,391.89 and %s2,476.60" % (R, R, R),
                     "Average R: -0.258", "fired 28 times: 6 target, 13 stop, 9 neither. Average R -0.255",
                     "Strongly toward the stop: target first 23.8 % vs stop first 47.1 %.",
                     "Left out 1 session(s) with a missing bar before 3:00 PM: 2026-10-15.",
                     "not a prediction", "before brokerage", "--seed 19"):
            self.assertIn(line, text)
        for word in ("profit", "P&L", "guarantee", "real time", "tick by tick", "$", " ET", "New York"):
            self.assertNotIn(word, text)


class Seeds(unittest.TestCase):
    def test_same_seed_same_numbers(self):
        a = machine(replays=2000, seed=5)["mc"]
        b = machine(replays=2000, seed=5)["mc"]
        self.assertEqual(a, b)

    def test_another_seed_gives_other_numbers(self):
        m = machine(seed=7)["mc"]
        self.assertEqual((m["p_target"], m["p_stop"], m["median_close"]), (0.2393, 0.4653, 2433.26))
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertNotEqual(sha(m["bands"]), SIM_BANDS)

    def test_the_draws_are_stable_across_pythons(self):
        # random.Random(seed), randint and choice are stable since Python 3.2; this pins the first draws so a
        # failure of the numbers above points at the RNG
        r = random.Random(19)
        self.assertEqual([r.randint(-2, 2) for _ in range(8)], [-2, 2, -2, 2, -1, 1, 0, 2])
        self.assertEqual(r.choice(range(59)), 18)

    def test_window_and_replays(self):
        a = machine(replays=500, window=0)["mc"]
        self.assertEqual((a["replays"], a["window"]), (500, 0))
        self.assertNotEqual(a["bands"], machine(replays=500)["mc"]["bands"])
        with self.assertRaises(tm.TMError):
            machine(replays=0)
        with self.assertRaises(tm.TMError):
            machine(window=11)

    def test_a_replay_never_draws_a_bar_after_until(self):
        # the pool ends at the --until bar, and the +-window draw is kept inside it
        sessions = tm.full_sessions(tm.split_sessions(tm.parse_bars(tm.chart_result(TEXT, "TCS"), NOW_UTC)[1]),
                                    "2026-11-23", 68)[0]
        pool = tm.build_pool(sessions)
        self.assertEqual((len(pool), len(pool[0]), len(pool[1]), len(pool[68])), (69, 0, 58, 58))
        self.assertEqual(tm.simulate(pool, 2443.5, 2419.35, 2467.65, 5, 68, replays=300)["bars_left"], 63)


class Clock(unittest.TestCase):
    def test_india_time_is_a_fixed_five_and_a_half_hours(self):
        DT = datetime.datetime
        self.assertEqual(tm.to_ist(DT(2026, 11, 23, 3, 45)), DT(2026, 11, 23, 9, 15))
        self.assertEqual(tm.ist_to_utc(DT(2026, 11, 23, 15, 30)), DT(2026, 11, 23, 10, 0))
        for month in (1, 3, 6, 11):                     # no daylight saving, any month
            self.assertEqual(tm.to_ist(DT(2026, month, 2, 18, 45)), DT(2026, month, 3, 0, 15))
        self.assertFalse(hasattr(tm, "eastern_offset"))
        self.assertFalse(hasattr(tm, "to_eastern"))

    def test_parse_now(self):
        DT = datetime.datetime
        self.assertEqual(tm.parse_now("2026-11-23 09:45"), DT(2026, 11, 23, 4, 15))
        self.assertEqual(tm.parse_now("2026-11-23 09:45 IST"), DT(2026, 11, 23, 4, 15))
        self.assertEqual(tm.parse_now("2026-11-23T04:15:00Z"), DT(2026, 11, 23, 4, 15))
        self.assertEqual(tm.parse_now("2026-11-23T09:45:00+05:30"), DT(2026, 11, 23, 4, 15))
        with self.assertRaises(tm.TMError) as cm:
            tm.parse_now("tomorrow")
        self.assertIn("India time", str(cm.exception))

    def test_the_session_is_0915_to_1530(self):
        self.assertEqual((tm.OPEN_MIN, tm.CLOSE_MIN, tm.SESSION_BARS), (9 * 60 + 15, 15 * 60 + 30, 75))
        self.assertEqual(tm.OPEN_MIN + 5 * tm.SESSION_BARS, tm.CLOSE_MIN)
        self.assertEqual(tm.clock12(tm.CLOSE_MIN), "3:30 PM")

    def test_nse_holidays(self):
        D = datetime.date
        self.assertEqual(tm.trading_day(D(2026, 11, 24)), (False, "NSE holiday: Guru Nanak Jayanti"))
        self.assertEqual(tm.trading_day(D(2026, 11, 23)), (True, ""))
        self.assertEqual(tm.trading_day(D(2026, 11, 22)), (False, "weekend"))
        self.assertEqual(len(tm.NSE_HOLIDAYS), 16)
        self.assertEqual(tm.CALENDAR_YEARS, (2026,))
        for d in tm.NSE_HOLIDAYS:
            self.assertLess(D.fromisoformat(d).weekday(), 5, d)
        # no trading day in the real 60-day chart is a listed holiday, and the two holidays inside its
        # span (Dussehra, Diwali Balipratipada) have no bars
        days = tm.split_sessions(tm.parse_bars(tm.chart_result(TEXT, "TCS"), NOW_UTC)[1])
        self.assertEqual(len(days), 60)
        self.assertEqual([d for d in days if d in tm.NSE_HOLIDAYS], [])
        self.assertNotIn("2026-10-20", days)
        self.assertNotIn("2026-11-10", days)

    def test_a_plan_on_a_holiday_says_so(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-11-24"), now=tm.parse_now("2026-11-24 16:00"))
        self.assertIn("no bars for that day, NSE holiday: Guru Nanak Jayanti", str(cm.exception))
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-11-22"))
        self.assertIn("no bars for that day, weekend", str(cm.exception))


class Symbols(unittest.TestCase):
    def test_yahoo_symbols(self):
        self.assertEqual(tm.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(tm.yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(tm.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(tm.yahoo_symbol("TCS.NS"), "TCS.NS")
        self.assertEqual(tm.yahoo_symbol("500325.BO"), "500325.BO")
        self.assertEqual(tm.yahoo_symbol("^NSEI"), "^NSEI")
        self.assertEqual(tm.nse_symbol("tcs.ns"), "TCS")
        self.assertEqual(tm.nse_symbol("M&M.NS"), "M&M")

    def test_plan_symbols(self):
        for sym in ("RELIANCE", "HDFCBANK", "M&M", "BAJAJ-AUTO", "3MINDIA", "M&MFIN"):
            self.assertEqual(plan(symbol=sym)["symbol"], sym)
        self.assertEqual(plan(symbol="tcs.ns")["symbol"], "TCS")
        for bad in ("", "TCS NS", "BRK.B", "A" * 21, "TCS;rm"):
            with self.assertRaises(tm.TMError, msg=bad) as cm:
                plan(symbol=bad)
            self.assertIn("NSE symbol", str(cm.exception))

    def test_the_data_must_be_the_plans_symbol(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(symbol="INFY"))
        self.assertIn("data is for TCS but the plan is for INFY", str(cm.exception))
        self.assertEqual(machine(plan(symbol="TCS.NS"), replays=100)["plan"]["symbol"], "TCS")


class Bars(unittest.TestCase):
    def setUp(self):
        self.result = tm.chart_result(TEXT, "TCS")

    def test_empty_bars_and_the_last_bar_are_dropped(self):
        meta, bars, dropped = tm.parse_bars(self.result, NOW_UTC)
        self.assertEqual((meta["symbol"], meta["currency"], meta["dataGranularity"]), ("TCS.NS", "INR", "5m"))
        self.assertEqual(len(self.result["timestamp"]), 4500)          # 60 sessions x 75 bars
        # 111 bars have no price; the 15:25 bar of the last day is not complete at the data's own time (15:29)
        self.assertEqual(dropped, {"no_price": 111, "off_grid": 0, "not_complete": 1, "outside_hours": 0})
        last = bars[-1]
        self.assertEqual((last["date"], last["start"], last["slot"]), ("2026-11-23", "15:15", 72))
        self.assertEqual((bars[0]["date"], bars[0]["start"], bars[0]["slot"]), ("2026-08-26", "09:15", 0))
        entry = [b for b in bars if b["date"] == "2026-11-23" and b["slot"] == 5][0]
        self.assertEqual((entry["start"], entry["c"]), ("09:40", 2443.5))

    def test_yahoo_has_no_complete_session_to_the_close(self):
        # the reason for --until: no session in 60 days has all 75 priced bars, 58 have every bar to 15:15
        days = tm.split_sessions(tm.parse_bars(self.result, NOW_UTC)[1])
        self.assertEqual(len(tm.full_sessions(days, "2026-11-23")[0]), 0)
        self.assertEqual(len(tm.full_sessions(days, "2026-11-23", 71)[0]), 58)      # to the 15:10 bar
        self.assertEqual(len(tm.full_sessions(days, "2026-11-23", 72)[0]), 42)      # to the 15:15 bar
        with self.assertRaises(tm.TMError) as cm:
            machine(until=None)
        msg = str(cm.exception)
        self.assertIn("only 0 full session(s) of TCS before 2026-11-23", msg)
        self.assertIn("every bar from 9:15 AM to 3:30 PM", msg)
        self.assertIn("--until 15:20 or earlier works with this data", msg)

    def test_now_pins_the_clock(self):
        bars = tm.parse_bars(self.result, tm.parse_now("2026-11-23 09:42"))[1]
        today = [b for b in bars if b["date"] == "2026-11-23"]
        self.assertEqual(len(today), 5)
        with self.assertRaises(tm.TMError) as cm:
            machine(now=tm.parse_now("2026-11-23 09:42"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("09:35 bar", str(cm.exception))
        self.assertEqual(machine(now=tm.parse_now("2026-11-23 09:45"), replays=200)["mc"]["pool_sessions"], 58)

    def test_bars_outside_the_session_are_dropped(self):
        stamps = [1795405500,   # 2026-11-23 03:45Z = 09:15 IST, the open
                  1795405200,   # 09:10 IST, the pre-open
                  1795427700,   # 15:25 IST, the last bar
                  1795428000,   # 15:30 IST, after the close
                  1784864700]   # 2026-07-24 03:45Z = 09:15 IST in July: the same offset all year
        res = {"meta": {"symbol": "X.NS"}, "timestamp": stamps,
               "indicators": {"quote": [{"open": [1] * 5, "high": [1] * 5, "low": [1] * 5, "close": [1] * 5,
                                         "volume": [0] * 5}]}}
        meta, bars, dropped = tm.parse_bars(res, tm.parse_now("2026-11-24T00:00Z"))
        self.assertEqual([(b["date"], b["start"], b["slot"]) for b in bars],
                         [("2026-07-24", "09:15", 0), ("2026-11-23", "09:15", 0), ("2026-11-23", "15:25", 74)])
        self.assertEqual(dropped["outside_hours"], 2)

    def test_a_session_with_a_gap_before_until_is_left_out(self):
        doc = machine(replays=300)
        self.assertEqual(doc["data"]["short_sessions_left_out"], [{"date": "2026-10-15", "bars": 66}])

        def cut(res):  # 2026-09-04 stops at 1:00 PM: 45 bars
            keep_bars(res, lambda ts: not (ist(ts).date().isoformat() == "2026-09-04" and ist(ts).hour >= 13))
        doc = machine(text=edited(cut), replays=300)
        self.assertEqual(doc["mc"]["pool_sessions"], 57)
        self.assertEqual(doc["data"]["short_sessions_left_out"],
                         [{"date": "2026-09-04", "bars": 45}, {"date": "2026-10-15", "bars": 66}])
        self.assertEqual(doc["history"]["sessions"], 57)
        self.assertNotIn("2026-09-04", [t["date"] for t in doc["history"]["trades"]])
        self.assertIn("Left out 2 session(s) with a missing bar before 3:00 PM", "\n".join(tm.report(doc)))
        # a gap after --until does not matter: with --until 11:30 the 11:45 gap of 2026-10-15 is fine
        self.assertEqual(machine(until="11:30", replays=100)["mc"]["pool_sessions"], 59)

    def test_today_is_not_in_the_pool(self):
        doc = machine(replays=200)
        self.assertEqual(doc["mc"]["pool_to"], "2026-11-20")
        self.assertNotIn("2026-11-23", [t["date"] for t in doc["history"]["trades"]])

    def test_a_missing_price_is_dropped(self):
        def hole(res):
            res["indicators"]["quote"][0]["close"][5] = None
        doc = machine(text=edited(hole), replays=200)
        self.assertEqual(doc["data"]["dropped"]["no_price"], 112)
        self.assertEqual(doc["mc"]["pool_sessions"], 57)   # 2026-08-26 now has a gap

    def test_empty_and_broken_data(self):
        empty = json.dumps({"chart": {"result": [{"meta": {"symbol": "TCS.NS"}, "timestamp": [],
                                                  "indicators": {"quote": [{}]}}], "error": None}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=empty)
        self.assertIn("no complete 5-minute bars", str(cm.exception))
        gone = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                               "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=gone)
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(tm.TMError):
            machine(text="<html>rate limited</html>")

    def test_fewer_than_20_sessions_is_refused(self):
        def trim(res):
            keep_bars(res, lambda ts: ist(ts).date().isoformat() >= "2026-10-26")
        with self.assertRaises(tm.TMError) as cm:
            machine(text=edited(trim))
        self.assertIn("only 19 full session(s)", str(cm.exception))
        self.assertIn("at least 20", str(cm.exception))
        self.assertNotIn("or earlier works", str(cm.exception))    # no --until would help here

        def twenty(res):
            keep_bars(res, lambda ts: ist(ts).date().isoformat() >= "2026-10-23")
        self.assertEqual(machine(text=edited(twenty), replays=200)["mc"]["pool_sessions"], 20)


class Plans(unittest.TestCase):
    def test_an_entry_not_in_the_data_is_refused(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-11-25"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("no bars for that day", str(cm.exception))
        with self.assertRaises(tm.TMError) as cm:      # the data has no 15:20 bar on the plan's day
            machine(plan(entry_time="3:25 PM", entry_bar=None), until="15:30")
        self.assertIn("not in the data", str(cm.exception))

    def test_plan_checks(self):
        cases = [
            (dict(entry_time="9:47 AM", entry_bar=None), "5-minute grid"),
            (dict(entry_time="3:30 PM", entry_bar=None), "between 9:20 AM and 3:25 PM"),
            (dict(entry_time="9:15 AM", entry_bar=None), "between 9:20 AM and 3:25 PM"),
            (dict(entry_bar=6), "disagrees with itself"),
            (dict(entry_time=None, entry_bar=None), "entry_time"),
            (dict(interval="1m"), "7 days of 1-min bars"),
            (dict(stop=2450.0), "stop < entry < target"),
            (dict(side="short"), "buy or sell"),
            (dict(symbol=""), "NSE symbol"),
            (dict(entry="abc"), "number for entry"),
            (dict(date="Nov 23"), "date like"),
            (dict(opening_range={"minutes": 7}), "multiple of 5"),
            (dict(opening_range={"minutes": 375}), "multiple of 5"),
        ]
        for change, words in cases:
            with self.assertRaises(tm.TMError, msg=change) as cm:
                plan(**change)
            self.assertIn(words, str(cm.exception), change)

    def test_entry_bar_or_entry_time_alone(self):
        self.assertEqual(plan(entry_time=None)["entry_time"], "9:45 AM")
        self.assertEqual(plan(entry_bar=None)["entry_bar"], 5)
        self.assertEqual(plan(entry_time="09:45", entry_bar=None)["entry_bar"], 5)
        self.assertEqual(plan(entry_time="9:45am", entry_bar=None)["entry_bar"], 5)
        self.assertEqual(plan(entry_time="9:20 AM", entry_bar=None)["entry_bar"], 0)
        self.assertEqual(plan(entry_time="3:25 PM", entry_bar=None)["entry_bar"], 73)
        self.assertEqual(plan(entry_time="2:30", entry_bar=None)["entry_bar"], 62)   # a bare 2:30 is p.m.

    def test_until_cuts_the_replay(self):
        doc = machine(until="14:55", replays=500)
        m = doc["mc"]
        self.assertEqual((m["until"], m["bars_left"], m["band_times"][-1]), ("14:55", 62, "14:55"))
        self.assertIn("still open at 2:55 PM", "\n".join(tm.report(doc)))
        self.assertEqual(machine(until="2:55 PM", replays=500)["mc"], m)
        late = machine(until="15:15", replays=500)["mc"]
        self.assertEqual((late["until"], late["bars_left"], late["pool_sessions"]), ("15:15", 66, 58))
        for bad, words in (("9:45 AM", "nothing to replay"), ("15:35", "no later than 3:30 PM, the NSE close"),
                           ("14:57", "5-minute grid"), ("noon", "India time")):
            with self.assertRaises(tm.TMError) as cm:
                machine(until=bad, replays=10)
            self.assertIn(words, str(cm.exception), bad)

    def test_an_entry_outside_its_bar_is_flagged(self):
        doc = machine(plan(entry=2460.0, stop=2440.0, target=2480.0), replays=200)
        self.assertIn("outside that bar's range", doc["warnings"][0])

    def test_stop_rounds_away_from_the_entry(self):
        self.assertEqual(tm.cents_away(2419.355, True), 2419.35)
        self.assertEqual(tm.cents_away(2419.355, False), 2419.36)
        self.assertEqual(tm.cents_away(2419.35, True), 2419.35)
        self.assertEqual(tm.cents_away(354.46999999999997, True), 354.47)

    def test_the_opening_range_is_snapped_to_the_paisa(self):
        # Yahoo sends 2433.70 as 2433.699951171875; unsnapped, the midpoint stop would come out 2419.34
        day = tm.split_sessions(tm.parse_bars(tm.chart_result(TEXT, "TCS"), NOW_UTC)[1])["2026-11-23"]
        self.assertEqual(max(day[i]["h"] for i in range(3)), 2433.699951171875)
        self.assertEqual(tm.opening_range(day, 3), (2433.7, 2405.0))
        rule = {"stop_rule": "or_mid"}
        self.assertEqual(tm.rule_stop(rule, 2433.7, 2405.0, 2443.5, True), 2419.35)
        self.assertEqual(tm.rule_stop({"stop_rule": "or_low"}, 2433.7, 2405.0, 2443.5, True), 2405.0)

    def test_the_stop_rule_is_worked_out_when_not_given(self):
        rule = machine(plan(stop_rule=None), replays=100)["history"]["rule"]
        self.assertEqual(rule["stop_rule"], "or_mid")
        # one paisa off (a tool that did not snap the range) is still the midpoint rule
        rule = machine(plan(stop_rule=None, stop=2419.34, target=2467.66), replays=100)["history"]["rule"]
        self.assertEqual(rule["stop_rule"], "or_mid")
        rule = machine(plan(stop_rule=None, stop=2405.0, target=2482.0), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["target_r"]), ("or_low", 1.0))
        rule = machine(plan(stop_rule="atr", stop=2431.0, target=2468.5), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["stop_pct"], rule["target_r"]), ("pct", 0.5116, 2.0))

    def test_a_setup_other_than_orb_skips_the_history(self):
        doc = machine(plan(setup="flag"), replays=200)
        self.assertIsNone(doc["history"]["rule"])
        self.assertIn("orb", doc["history"]["note"])
        self.assertEqual(doc["mc"]["pool_sessions"], 58)
        self.assertIn("History: skipped", "\n".join(tm.report(doc)))

    def test_a_sell_plan_mirrors_the_rules(self):
        doc = machine(plan(side="sell", stop=2467.65, target=2419.35, stop_rule="or_high"), replays=2000)
        m = doc["mc"]
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertEqual(m["r_at_target"], 1.0)
        self.assertEqual(doc["history"]["rule"]["stop_rule"], "or_high")
        for t in doc["history"]["trades"]:
            self.assertTrue(t["target"] < t["entry"] < t["stop"], t)
        self.assertIn("below the entry", "\n".join(tm.report(doc)))

    def test_the_verdict_thresholds_at_1r(self):
        # 1R: the target's share of the replays that hit one side against 50 %
        self.assertTrue(tm.verdict({"p_target": 0.30, "p_stop": 0.26}).startswith("Close to a coin flip"))
        self.assertTrue(tm.verdict({"p_target": 0.40, "p_stop": 0.30}).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict({"p_target": 0.20, "p_stop": 0.30}).startswith("Leans toward the stop"))
        self.assertTrue(tm.verdict({"p_target": 0.60, "p_stop": 0.30}).startswith("Strongly toward the target"))
        self.assertEqual(tm.verdict({"p_target": 0.10, "p_stop": 0.60, "r_at_target": 1.0}),
                         "Strongly toward the stop: target first 10.0 % vs stop first 60.0 %.")
        self.assertEqual(tm.verdict({"p_target": 0.0, "p_stop": 0.0, "until": "15:00"}),
                         "No replay reached the target or the stop by 3:00 PM: the price at 3:00 PM decides every one.")
        self.assertEqual(tm.verdict({"p_target": 0.0, "p_stop": 0.0}),
                         "No replay reached the target or the stop by 3:30 PM: the price at 3:30 PM decides every one.")

    def test_a_2r_target_is_judged_against_its_break_even(self):
        # made-up odds (not from a run): a 2R plan needs 33 % of the replays that hit a side, this one got 32 %
        mc = {"p_target": 0.241, "p_stop": 0.505, "p_neither": 0.254, "r_at_target": 2.0, "avg_R": 0.095}
        self.assertEqual(tm.verdict(mc), "Close to break-even: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 32 % (target first 24.1 % vs stop "
                                         "first 50.5 %).")
        self.assertEqual(tm.verdict_notes(mc), [])
        lean = dict(mc, p_target=0.15, p_stop=0.55)
        self.assertTrue(tm.verdict(lean).startswith("Leans toward the stop: at 2R the target has to come first in "
                                                    "33 % of the replays that hit one side; it did in 21 %"))
        self.assertEqual(tm.verdict_notes(lean), ["Average R is +0.095 all the same: the replays that hit neither "
                                                  "pull it the other way."])
        self.assertTrue(tm.verdict(dict(mc, p_target=0.30, p_stop=0.40)).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict(dict(mc, p_target=0.40, p_stop=0.40)).startswith("Strongly toward the target"))
        self.assertIn("at 1.5R the target has to come first in 40 %", tm.verdict(dict(mc, r_at_target=1.5)))

    def test_2r_on_the_fixture_a_tight_stop(self):
        # stop 2431.00, 2R target 2468.50: 19.8 % vs 70.4 %, and 2R needs a third of the replays that hit a side
        doc = machine(plan(stop=2431.0, target=2468.5, stop_rule="pct"))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["avg_R"]), (2.0, 0.1978, 0.7041, -0.272))
        self.assertEqual(doc["verdict"], "Leans toward the stop: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 22 % (target first 19.8 % vs stop "
                                         "first 70.4 %).")
        self.assertEqual(doc["verdict_notes"], [])

    def test_2r_on_the_fixture_a_far_target(self):
        # the plan's entry and stop with a 2R target 2491.80: target first only 3.5 % vs stop first 48.4 %
        doc = machine(plan(target=2491.80))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["p_neither"], m["avg_R"]),
                         (2.0, 0.0352, 0.4837, 0.4811, -0.316))
        self.assertEqual(doc["verdict"], "Strongly toward the stop: at 2R the target has to come first in 33 % of "
                                         "the replays that hit one side; it did in 7 % (target first 3.5 % vs stop "
                                         "first 48.4 %).")
        self.assertEqual(doc["verdict_notes"], [
            "48.1 % of the replays hit neither by 3:00 PM; the price at 3:00 PM decides those."])
        text = "\n".join(tm.report(doc))
        self.assertIn(doc["verdict"] + "\n" + doc["verdict_notes"][0], text)


class Network(unittest.TestCase):
    def setUp(self):
        self.calls, self.urls, self.sleeps = [], [], []
        self._get, self._sleep = tm._get, tm._sleep
        tm._sleep = self.sleeps.append

    def tearDown(self):
        tm._get, tm._sleep = self._get, self._sleep

    def answer(self, codes):
        codes = list(codes)

        def fake(url, timeout=20):
            self.urls.append(url)
            self.calls.append(url.split(".finance")[0].split("//")[1])
            code = codes.pop(0)
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return TEXT
        tm._get = fake

    def test_url_and_user_agent(self):
        self.assertEqual(tm.chart_url("tcs"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/TCS.NS?range=60d&interval=5m")
        self.assertIn("/chart/M%26M.NS?", tm.chart_url("M&M", "query1"))
        self.assertIn("/chart/BAJAJ-AUTO.NS?", tm.chart_url("bajaj-auto"))
        self.assertIn("/chart/TCS.NS?", tm.chart_url("TCS.NS"))
        self.assertIn("/chart/%5ENSEI?", tm.chart_url("^NSEI"))
        self.assertEqual(tm.UA, "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)")
        self.assertEqual(tm.HOSTS, ("query2", "query1"))

    def test_429_waits_retries_then_falls_back_to_query1(self):
        self.answer([429, 429, 200])
        self.assertEqual(tm.fetch_chart("TCS"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2", "query1"])
        self.assertEqual(self.sleeps, [tm.BACKOFF, tm.PAUSE])

    def test_one_429_then_ok(self):
        self.answer([429, 200])
        self.assertEqual(tm.fetch_chart("TCS"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2"])

    def test_5xx_falls_back(self):
        self.answer([503, 200])
        self.assertEqual(tm.fetch_chart("TCS"), TEXT)
        self.assertEqual(self.calls, ["query2", "query1"])

    def test_rate_limited_everywhere_is_one_plain_line(self):
        self.answer([429, 429, 429, 429])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("TCS")
        self.assertIn("rate limiting", str(cm.exception))

    def test_404(self):
        self.answer([404])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("ZZZZ")
        self.assertIn("check the NSE symbol", str(cm.exception))

    def test_run_over_the_network_path(self):
        self.answer([200])
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "plan.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(PLAN, fh)
            code, text, _ = run(["run", "--plan", p, "--now", NOW, "--replays", "300", "--until", UNTIL])
            self.assertEqual(code, 0)
            self.assertEqual(self.calls, ["query2"])
            self.assertIn("/chart/TCS.NS?range=60d&interval=5m", self.urls[0])
        finally:
            shutil.rmtree(d)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.plan = os.path.join(self.d, "trader", "plan.json")
        os.makedirs(os.path.dirname(self.plan))
        with open(self.plan, "w", encoding="utf-8") as fh:
            json.dump(PLAN, fh, indent=2)
        self.out = os.path.join(self.d, "trader", "timemachine.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_run_with_the_plan_end_to_end(self):
        code, text, err = run(["run", "--plan", self.plan, "--offline", TCS, "--now", NOW, "--until", UNTIL,
                               "--write", self.out])
        self.assertEqual(code, 0, err)
        self.assertIn("target first   23.8 %", text)
        self.assertIn("entry %s2,443.50 at 9:45 AM" % RUPEE, text)
        self.assertIn("Strongly toward the stop: target first 23.8 % vs stop first 47.1 %.", text)
        self.assertIn("Wrote %s." % self.out, text)
        self.assertIn("/trader-budget", text)
        self.assertIn("Not financial advice", text)
        with open(self.out, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(k for k in ("plan", "mc", "history") if k in doc), ["history", "mc", "plan"])
        self.assertEqual(doc["created_by"], "trader-timemachine")
        self.assertEqual(doc["source"], "offline")
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["avg_R"]), (0.2383, -0.258))
        self.assertEqual(sha(doc["mc"]["sample_paths"]), SIM_PATHS)
        self.assertEqual((doc["history"]["triggered"], doc["history"]["avg_R"]), (28, -0.255))
        self.assertEqual(doc["plan"]["from"], self.plan)

    def test_the_default_until_is_the_plans_flat_by(self):
        code, text, _ = run(["run", "--plan", self.plan, "--offline", TCS, "--now", NOW])
        self.assertEqual(code, 0)
        self.assertIn("9:45 AM to 3:00 PM", text)
        self.assertIn("target first   23.8 %", text)

    def test_a_run_to_the_close_is_refused_because_yahoo_cannot_fill_it(self):
        code, text, err = run(["run", "--plan", self.plan, "--offline", TCS, "--now", NOW, "--until", "15:30"])
        self.assertEqual(code, 1)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertIn("every bar from 9:15 AM to 3:30 PM", err)
        self.assertIn("--until 15:20 or earlier works", err)

    def test_json_output(self):
        code, text, _ = run(["run", "--plan", self.plan, "--offline", TCS, "--now", NOW, "--until", UNTIL, "--json"])
        self.assertEqual(code, 0)
        doc = json.loads(text)
        self.assertEqual(doc["mc"]["p_stop"], 0.4708)
        self.assertNotIn("sample_paths", doc["mc"])
        self.assertEqual(doc["verdict"][:24], "Strongly toward the stop")
        self.assertFalse(os.path.exists(self.out))

    def test_the_plan_as_flags_matches_the_file(self):
        code, text, err = run(["run", "--symbol", "tcs", "--date", "2026-11-23", "--entry", "2443.50",
                               "--stop", "2419.35", "--target", "2467.65", "--entry-time", "9:45 AM",
                               "--offline", TCS, "--now", NOW + " IST", "--until", UNTIL, "--json"])
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["p_green"]), (0.2383, 0.3584))
        self.assertEqual(doc["plan"]["stop_rule"], "or_mid")
        self.assertEqual((doc["history"]["target"], doc["history"]["stop"], doc["history"]["avg_R"]), (6, 13, -0.255))
        code, _, err = run(["run", "--symbol", "TCS", "--entry", "2443.50", "--offline", TCS])
        self.assertEqual(code, 1)
        self.assertIn("--stop", err)

    def test_offline_folder_and_default_paths(self):
        shutil.copy(TCS, self.d)
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            code, text, err = run(["run", "--offline", ".", "--now", NOW, "--until", UNTIL, "--replays", "500",
                                   "--write"])
            self.assertEqual(code, 0, err)
            self.assertTrue(os.path.exists(os.path.join("trader", "timemachine.json")))
            code, text, err = run(["chart", "--from"])
            self.assertEqual(code, 0, err)
            self.assertIn("* median", text)
            with open(os.path.join("trader", "timemachine.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["mc"]["replays"], 500)
        finally:
            os.chdir(cwd)

    def test_errors_are_one_plain_line(self):
        for argv in (["run", "--plan", os.path.join(self.d, "nope.json")],
                     ["run", "--plan", self.plan, "--offline", os.path.join(self.d, "missing.json.gz")],
                     ["run", "--plan", self.plan, "--offline", TCS, "--until", "9:00"],
                     ["chart", "--from", os.path.join(self.d, "nope.json")]):
            code, text, err = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertTrue(err.startswith("timemachine.py: "), err)
            self.assertNotIn("Traceback", err)
        self.assertEqual(run([])[0], 1)

    def test_chart(self):
        run(["run", "--plan", self.plan, "--offline", TCS, "--now", NOW, "--until", UNTIL, "--write", self.out])
        code, text, _ = run(["chart", "--from", self.out])
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertIn("TCS 2026-11-23, 2,000 replay paths from the 9:45 AM entry (seed 19)", lines[0])
        self.assertTrue(any(l.startswith("  2467.65 |") and l.endswith("  target") for l in lines))
        self.assertTrue(any(l.startswith("  2419.35 |") and l.endswith("  stop") for l in lines))
        self.assertTrue(any(l.startswith("  2443.50 |*") and l.endswith("  entry") for l in lines))
        self.assertIn("9:45", text)
        self.assertIn("3:00", text)
        self.assertEqual(lines[-1], "Strongly toward the stop: target first 23.8 % vs stop first 47.1 %.")
        code, again, _ = run(["chart", "--plan", self.plan, "--offline", TCS, "--now", NOW, "--until", UNTIL])
        self.assertEqual(again, text)


if __name__ == "__main__":
    unittest.main()
