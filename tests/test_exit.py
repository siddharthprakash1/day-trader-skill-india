"""trader-exit tests (India, NSE). Offline: Yahoo is never called.

Fixtures in tests/fixtures/trader-exit/, saved from Yahoo's chart API on 2026-11-24
(range=1d, the session of Monday 2026-11-23), unedited:

TCS-2026-11-23-5m.json   TCS.NS five-minute bars, for the ATR stop and the end-to-end run
                         (trader-entry triggers at 9:45 AM, 2,443.50, range 2,405.00-2,433.70).
M&M-2026-11-23-5m.json   M&M.NS: a symbol with an ampersand.
"""

import datetime
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
from decimal import Decimal

from _load import load, FIX, ROOT

exitp = load("trader-exit", "exit")
entry = load("trader-entry", "entry")

XDIR = os.path.join(FIX, "trader-exit")
TCS = os.path.join(XDIR, "TCS-2026-11-23-5m.json")
MM = os.path.join(XDIR, "M&M-2026-11-23-5m.json")
DT = datetime.datetime
RS = "₹"

# trader/plan.json for TCS on 2026-11-23, exactly as the contract writes it
TCS_PLAN = {"created_by": "trader-exit", "symbol": "TCS", "side": "buy", "date": "2026-11-23", "setup": "orb",
            "entry": 2443.5, "entry_time": "9:45 AM", "entry_bar": 5, "interval": "5m",
            "opening_range": {"high": 2433.7, "low": 2405.0, "minutes": 15},
            "stop": 2419.35, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
            "target": 2467.65, "target_r": 1.0, "target_label": "1R (one times the risk)",
            "risk_per_share": 24.15, "reward_risk": 1.0, "flat_by": "15:00"}
TCS_ENTRY = {"created_by": "trader-entry", "symbol": "TCS", "setup": "orb", "side": "buy", "date": "2026-11-23",
             "status": "triggered", "time": "9:45 AM", "bar_start": "09:40", "bar_index": 5, "price": 2443.5,
             "level": 2433.7, "interval": "5m", "opening_range": {"high": 2433.7, "low": 2405.0, "minutes": 15},
             "source": "yahoo", "checked_at": "2026-11-23T04:15:00Z"}

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


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def c(entry_px, stop, target="2R", oh=2433.7, ol=2405.0, atr=None, n=None):
    sr, sp = exitp.parse_stop(stop)
    tr, tp = exitp.parse_target(target)
    return exitp.compute(entry_px, sr, sp, tr, tp, oh, ol, atr, n)


class Math(unittest.TestCase):
    def test_or_mid_1r(self):
        p = c(2443.5, "or-mid", "1R")
        self.assertEqual(p["stop"], Decimal("2419.35"))             # (2433.70 + 2405.00) / 2, exact
        self.assertEqual(p["risk"], Decimal("24.15"))
        self.assertEqual(p["target"], Decimal("2467.65"))
        self.assertEqual(p["reward_risk"], Decimal("1.00"))
        self.assertEqual(p["target_label"], "1R (one times the risk)")
        self.assertEqual(p["stop_detail"], "(%s2,433.70 + %s2,405.00) / 2 = %s2,419.35" % (RS, RS, RS))

    def test_a_midpoint_on_half_a_paisa_rounds_down(self):
        p = c(364.18, "or-mid", "1R", 362.18, 354.47)              # 358.325 -> 358.32, away from the entry
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("358.32"), Decimal("5.86"), Decimal("370.04")))
        self.assertIn("= %s358.325, rounded down" % RS, p["stop_detail"])

    def test_the_default_2r(self):
        p = c(2443.5, "or-mid")
        self.assertEqual(p["target"], Decimal("2491.80"))
        self.assertEqual(p["target_label"], "2R (two times the risk)")

    def test_raw_yahoo_floats_give_the_same_paise(self):
        p = c(2443.5, "or-mid", "1R", 2433.699951171875, 2405.0)
        self.assertEqual((p["entry"], p["stop"], p["target"]),
                         (Decimal("2443.50"), Decimal("2419.35"), Decimal("2467.65")))
        p = c(2480.300048828125, "or-mid", "1R", 2433.699951171875, 2405.0)
        self.assertEqual((p["entry"], p["risk"]), (Decimal("2480.30"), Decimal("60.95")))

    def test_other_stops(self):
        p = c(2443.5, "or-low")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("2405.00"), Decimal("38.50"), Decimal("2520.50")))
        p = c(2443.5, "price:2419.337")
        self.assertEqual(p["stop"], Decimal("2419.33"))             # rounds away from the entry
        self.assertEqual(c(2443.5, "price:%s2,419.35" % RS)["stop"], Decimal("2419.35"))   # a pasted rupee price
        p = c(2443.5, "pct:1")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("2419.06"), Decimal("24.44"), Decimal("2492.38")))
        p = c(2443.5, "atr:1.5", atr=18.2666, n=6)
        self.assertEqual(p["stop"], Decimal("2416.10"))             # 2443.50 - 27.3999 = 2416.1001
        self.assertEqual(p["stop_label"], "1.5 x ATR(6) under the entry")
        self.assertIn("ATR %s18.2666 over the 6 bars" % RS, p["stop_detail"])

    def test_target_rounds_half_up_and_price_targets(self):
        p = c(100, "price:99.01", "1.5R", None, None)
        self.assertEqual(p["target"], Decimal("101.49"))            # 100 + 1.485
        self.assertEqual(p["target_label"], "1.5R (1.5 times the risk)")
        p = c(2443.5, "or-mid", "price:2476")
        self.assertEqual(p["reward_risk"], Decimal("1.35"))         # 32.50 / 24.15
        self.assertEqual(p["target_label"], "your price (1.35R)")
        self.assertEqual(p["target_r"], Decimal("1.35"))

    def test_money_is_rupees(self):
        self.assertEqual(exitp.money(2443.5), RS + "2,443.50")
        self.assertEqual(exitp.money("1369.1"), RS + "1,369.10")
        self.assertEqual(exitp.money(Decimal("123456.789")), RS + "123,456.79")
        self.assertEqual(exitp.D(RS + "1,369.10"), Decimal("1369.10"))

    def test_refusals_say_why(self):
        cases = [("price:2443.5", "2R", "at or above the entry"),
                 ("price:2450", "2R", "at or above the entry"),
                 ("or-mid", "price:2443", "at or below the entry"),
                 ("price:2443.00", "2R", "too tight to survive the spread"),
                 ("pct:0.04", "2R", "too tight to survive the spread")]
        for stop, target, why in cases:
            with self.assertRaises(exitp.PlanError) as cm:
                c(2443.5, stop, target)
            self.assertIn(why, str(cm.exception), stop)
            self.assertNotIn("$", str(cm.exception))
        # 0.05% of 2,443.50 is 1.22175: a 1.23 risk is allowed, 1.22 is not
        self.assertEqual(c(2443.5, "price:2442.27")["risk"], Decimal("1.23"))
        with self.assertRaises(exitp.PlanError) as cm:
            c(2443.5, "price:2442.28")
        self.assertIn("%s1.22 under the entry" % RS, str(cm.exception))
        with self.assertRaises(exitp.PlanError):
            c(2415.00, "or-mid")                                    # the midpoint is above this entry
        for bad in ("mid-ish", "pct:100", "price:0", "atr:-1"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_stop(bad)
        for bad in ("0R", "R", "lots", "price:x"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_target(bad)
        with self.assertRaises(exitp.PlanError):
            c(2443.5, "or-mid", oh=None, ol=None)
        with self.assertRaises(exitp.PlanError):
            c(2443.5, "or-mid", oh=2405.0, ol=2433.7)

    def test_atr_on_the_session_bars(self):
        ch = exitp.load_chart("TCS", "5m", None, TCS)
        day = datetime.date(2026, 11, 23)
        bars = exitp.session_bars(ch, day, "5m", upto_slot=5)
        self.assertEqual(len(bars), 6)                              # 9:15 ... the 9:40 entry bar
        # by hand: the true range of each bar, the first one without the overnight gap
        trs, prev = [], None
        for b in bars:
            r = b["h"] - b["l"]
            if prev is not None:
                r = max(r, abs(b["h"] - prev), abs(b["l"] - prev))
            trs.append(r)
            prev = b["c"]
        self.assertAlmostEqual(trs[0], 2433.7 - 2405.8, places=3)
        atr, n = exitp.atr_of(bars)
        self.assertEqual(n, 6)
        self.assertAlmostEqual(atr, sum(trs) / 6, places=9)
        self.assertAlmostEqual(atr, 18.2666, places=3)
        # the whole day, read as of the file's own time (15:29:34): the 15:25 bar has not closed and
        # the 15:20 bar came as nulls, so 73 of the 75 bars count; the ATR uses the last 14
        all_bars = exitp.session_bars(ch, day, "5m")
        self.assertEqual(len(all_bars), 73)
        self.assertEqual(exitp.atr_of(all_bars)[1], 14)
        # no bars on a closed day
        self.assertEqual(exitp.session_bars(ch, datetime.date(2026, 11, 24), "5m"), [])


class India(unittest.TestCase):
    def test_ist_is_utc_plus_530_all_year(self):
        self.assertEqual(exitp.to_ist(DT(2026, 11, 23, 3, 45)), DT(2026, 11, 23, 9, 15))
        self.assertEqual(exitp.ist_to_utc(DT(2026, 11, 23, 15, 30)), DT(2026, 11, 23, 10, 0))
        for month in range(1, 13):
            self.assertEqual(exitp.ist_to_utc(DT(2026, month, 15, 9, 15)), DT(2026, month, 15, 3, 45))
        self.assertEqual(exitp.parse_now("2026-11-23 09:45"), DT(2026, 11, 23, 4, 15))
        self.assertEqual(exitp.parse_now("2026-11-23 09:45 IST"), DT(2026, 11, 23, 4, 15))
        self.assertEqual(exitp.parse_now("2026-11-23T04:15:00Z"), DT(2026, 11, 23, 4, 15))
        with self.assertRaises(exitp.PlanError):
            exitp.parse_now("tomorrow")
        self.assertFalse(hasattr(exitp, "to_eastern"))
        self.assertFalse(hasattr(exitp, "eastern_offset"))

    def test_session_and_holidays(self):
        self.assertEqual(exitp.session(datetime.date(2026, 11, 23)), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertIsNone(exitp.session(datetime.date(2026, 11, 24)))      # Guru Nanak Jayanti
        self.assertIsNone(exitp.session(datetime.date(2026, 11, 22)))      # Sunday
        self.assertEqual(exitp.NSE_HOLIDAYS["2026-11-24"], "Guru Nanak Jayanti")
        self.assertEqual(exitp.NSE_HOLIDAYS, entry.NSE_HOLIDAYS)           # both copies of the calendar agree
        self.assertEqual(exitp.CALENDAR_YEARS, (2026,))
        self.assertEqual((exitp.OPEN, exitp.CLOSE), (entry.OPEN, entry.CLOSE))
        self.assertFalse(hasattr(exitp, "NYSE_HOLIDAYS"))
        self.assertFalse(hasattr(exitp, "NYSE_EARLY_CLOSE"))
        self.assertEqual(exitp.DEFAULTS, {"flat_by": "15:00", "min_reward_risk": 1.0})

    def test_symbols(self):
        self.assertEqual(exitp.yahoo_symbol("tcs"), "TCS.NS")
        self.assertEqual(exitp.yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(exitp.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(exitp.yahoo_symbol("TCS.NS"), "TCS.NS")
        self.assertEqual(exitp.nse_symbol("M&M.NS"), "M&M")
        with self.assertRaises(exitp.PlanError):
            exitp.clean_symbol("BRK.B")
        ch = exitp.load_chart("m&m", "5m", None, MM)               # the file says M&M.NS
        self.assertEqual(len(exitp.session_bars(ch, datetime.date(2026, 11, 23), "5m", upto_slot=5)), 6)
        with self.assertRaises(exitp.PlanError) as cm:
            exitp.load_chart("INFY", "5m", None, TCS)
        self.assertIn("holds TCS, not INFY", str(cm.exception))

    def test_the_atr_bars_come_from_yahoo_with_ns(self):
        calls = []
        with open(TCS, encoding="utf-8") as fh:
            body = fh.read()
        with open(MM, encoding="utf-8") as fh:
            mm = fh.read()

        def fake(url, timeout=20):
            calls.append(url)
            if len(calls) == 3:
                raise urllib.error.HTTPError(url, 503, "x", {}, io.BytesIO(b""))
            return mm if "M%26M" in url else body
        keep = exitp._open_url
        exitp._open_url = fake
        try:
            day = exitp.to_ist(exitp.utc_now()).date()
            ch = exitp.load_chart("TCS", "5m", day)
            self.assertEqual(len(ch["bars"]), 74)
            exitp.load_chart("M&M", "5m", day)
            exitp.load_chart("TCS", "5m", day)                      # 503 on query2 -> query1
        finally:
            exitp._open_url = keep
        self.assertEqual(calls[0], "https://query2.finance.yahoo.com/v8/finance/chart/TCS.NS?range=1d&interval=5m")
        self.assertEqual(calls[1], "https://query2.finance.yahoo.com/v8/finance/chart/M%26M.NS?range=1d&interval=5m")
        self.assertTrue(calls[3].startswith("https://query1.finance.yahoo.com/v8/finance/chart/TCS.NS?"))
        self.assertEqual(exitp.UA, "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)")


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, path, doc):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def plan(self, *args):
        return run(exitp, ["plan", "--now", "2026-11-23 09:46"] + list(args))

    def read_plan(self):
        with open(os.path.join("trader", "plan.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_entry_to_plan_end_to_end(self):
        code, _, _ = run(entry, ["check", "TCS", "--offline", TCS, "--now", "2026-11-23 09:45", "--write"])
        self.assertEqual(code, 0)
        with open(os.path.join("trader", "entry.json"), encoding="utf-8") as fh:
            e = json.load(fh)
        for k, v in TCS_ENTRY.items():                              # what trader-entry wrote is what exit reads
            self.assertEqual(e[k], v, k)
        code, text, err = self.plan("--stop", "or-mid", "--target", "1R", "--write")
        self.assertEqual(code, 0, err)
        p = self.read_plan()
        for k, v in TCS_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertIn("Exit plan: TCS long, Mon 2026-11-23, opening-range breakout on 5-minute bars", text)
        self.assertIn("Entry         %s2,443.50 at 9:45 AM (bar 5)" % RS, text)
        self.assertIn("Stop          %s2,419.35  opening-range midpoint" % RS, text)
        self.assertIn("Target        %s2,467.65  1R (one times the risk)" % RS, text)
        self.assertIn("Risk a share  %s24.15 (0.99%% of the entry)" % RS, text)
        self.assertIn("Reward:risk   1.0", text)
        self.assertIn("Flat by       15:00 IST", text)
        self.assertIn("not a guarantee", text)
        self.assertIn("/trader-timemachine", text)
        self.assertNotIn("$", text)
        self.assertNotIn("pro" + "fit", text.lower())   # never on screen
        code, text, _ = self.plan("--write")                       # the defaults: or-mid, 2R
        self.assertEqual(self.read_plan()["target"], 2491.8)
        self.assertEqual(self.read_plan()["target_r"], 2.0)
        code, text, _ = run(exitp, ["show", "--now", "2026-11-23 09:50"])
        self.assertEqual(code, 0)
        self.assertIn(RS + "2,491.80", text)
        self.assertNotIn("not today", text)
        code, text, _ = run(exitp, ["show", "--now", "2026-11-24 09:50"])
        self.assertIn("not today", text)

    def test_the_plan_keeps_its_fields(self):
        self.write(os.path.join("trader", "entry.json"), TCS_ENTRY)
        self.assertEqual(self.plan("--write")[0], 0)
        self.assertEqual(sorted(self.read_plan()), sorted(list(TCS_PLAN) + ["stop_detail", "created_at", "note"]))

    def test_manual_flags_and_json(self):
        code, text, _ = self.plan("--symbol", "tcs", "--entry", "2443.50", "--or-high", "2433.70", "--or-low",
                                  "2405.00", "--time", "9:45", "--entry-bar", "5", "--date", "2026-11-23",
                                  "--target", "1R", "--json")
        self.assertEqual(code, 0)
        p = json.loads(text)
        for k, v in TCS_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["warnings"], [])
        code, text, err = self.plan("--symbol", "m&m.ns", "--entry", "3570", "--or-high", "3568", "--or-low",
                                    "3530.8", "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["symbol"], p["stop"], p["target"]), ("M&M", 3549.4, 3611.2))
        code, _, err = self.plan("--symbol", "BRK.B", "--entry", "100", "--stop", "pct:1")
        self.assertEqual(code, 1)
        self.assertIn("not an NSE equity symbol", err)

    def test_atr_stop_reads_bars(self):
        self.write(os.path.join("trader", "entry.json"), TCS_ENTRY)
        code, text, err = self.plan("--stop", "atr:1.5", "--offline", TCS, "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["stop"], p["risk_per_share"], p["target"]), (2416.1, 27.4, 2498.3))
        self.assertEqual((p["stop_rule"], p["atr_bars"]), ("atr", 6))
        self.assertAlmostEqual(p["atr"], 18.2666, places=3)
        os.mkdir("charts")                                          # a folder of <SYMBOL>.json works too
        shutil.copy(TCS, os.path.join("charts", "TCS.json"))
        code, text, err = self.plan("--stop", "atr:1.5", "--offline", "charts", "--json")
        self.assertEqual(json.loads(text)["stop"], 2416.1, err)
        code, _, err = self.plan("--stop", "atr:1.5", "--offline", TCS, "--entry-bar", "1")
        self.assertEqual(code, 1)
        self.assertIn("at least 3 closed", err)

    def test_refusals_from_the_cli(self):
        self.write(os.path.join("trader", "entry.json"), dict(TCS_ENTRY, status="waiting", time=None, price=None,
                                                              bar_index=None, bar_start=None))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("TCS is waiting, not triggered", err)
        code, text, _ = self.plan("--entry", "2433.75", "--json")   # plan ahead at the level
        self.assertEqual(code, 0)
        p = json.loads(text)
        self.assertEqual((p["entry"], p["stop"], p["entry_time"]), (2433.75, 2419.35, None))

        self.write(os.path.join("trader", "entry.json"), dict(TCS_ENTRY, setup="level", level=2430.0))
        code, _, err = self.plan()
        self.assertIn("level setup has no opening-range stop", err)
        self.assertEqual(self.plan("--stop", "pct:1")[0], 0)

        code, _, err = self.plan("--side", "sell")
        self.assertIn("longs only", err)
        self.write(os.path.join("trader", "entry.json"), dict(TCS_ENTRY, side="sell"))
        code, _, err = self.plan()
        self.assertIn("longs only", err)
        code, _, err = self.plan("--stop", "price:2443.10")
        self.assertIn("longs only", err)
        os.remove(os.path.join("trader", "entry.json"))
        code, _, err = self.plan()
        self.assertIn("no entry", err)
        code, _, err = self.plan("--symbol", "TCS")
        self.assertIn("needs --entry", err)
        code, _, err = self.plan("--symbol", "TCS", "--from", "x.json", "--entry", "1")
        self.assertIn("not both", err)
        code, _, err = self.plan("--symbol", "TCS", "--entry", "2443.50", "--or-high", "2433.70")
        self.assertIn("both --or-high and --or-low", err)
        self.assertNotIn("Traceback", err)

    def test_flat_by_config_and_late_entries(self):
        self.write(os.path.join("trader", "entry.json"), TCS_ENTRY)
        self.write(os.path.join("trader", "config.json"), {"flat_by": "14:45", "min_reward_risk": 1.5,
                                                           "max_loss_per_trade_inr": 500, "broker": "groww"})
        code, text, _ = self.plan("--target", "1R", "--json")
        p = json.loads(text)
        self.assertEqual(p["flat_by"], "14:45")
        self.assertIn("under your min_reward_risk 1.5", p["warnings"][0])
        self.write(os.path.join("trader", "config.json"), {"flat_by": "15:45"})
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("flat_by in the config 15:45 is at or after the 3:30 PM close", err)
        os.remove(os.path.join("trader", "config.json"))

        code, text, _ = self.plan("--flat-by", "15:10", "--json")
        self.assertEqual(json.loads(text)["flat_by"], "15:10")
        for late in ("15:30", "16:30"):
            code, _, err = self.plan("--flat-by", late)
            self.assertEqual(code, 1)
            self.assertIn("at or after the 3:30 PM close", err)
        # an entry at or after flat-by is refused
        for late in ("3:00 PM", "3:05 PM", "15:20"):
            self.write(os.path.join("trader", "entry.json"), dict(TCS_ENTRY, time=late))
            code, _, err = self.plan()
            self.assertEqual(code, 1, late)
            self.assertIn("no time left to hold it", err)
        self.write(os.path.join("trader", "entry.json"), dict(TCS_ENTRY, time="2:55 PM"))
        self.assertEqual(self.plan()[0], 0)

    def test_closed_days_and_unknown_years_are_noted(self):
        self.write(os.path.join("trader", "entry.json"), dict(TCS_ENTRY, date="2026-11-24"))
        code, text, _ = run(exitp, ["plan", "--now", "2026-11-24 09:46", "--json"])
        self.assertEqual(code, 0)
        p = json.loads(text)
        self.assertEqual(p["flat_by"], "15:00")                     # no early closes: nothing moves
        self.assertEqual(p["warnings"], ["The NSE is closed on 2026-11-24 (NSE holiday: Guru Nanak Jayanti)."])
        self.write(os.path.join("trader", "entry.json"), dict(TCS_ENTRY, date="2027-03-01"))
        code, text, _ = run(exitp, ["plan", "--now", "2027-03-01 09:46", "--json"])
        self.assertIn("NSE holiday list covers 2026 only", json.loads(text)["warnings"][0])
        self.write(os.path.join("trader", "entry.json"), TCS_ENTRY)
        self.assertEqual(json.loads(self.plan("--json")[1])["warnings"], [])

    def test_show_without_a_plan(self):
        code, text, _ = run(exitp, ["show"])
        self.assertEqual(code, 1)
        self.assertIn("No plan", text)

    def test_the_rupee_sign_survives_a_windows_pipe(self):
        self.write(os.path.join("trader", "entry.json"), TCS_ENTRY)
        env = dict(os.environ)
        env.pop("PYTHONIOENCODING", None)
        env.pop("PYTHONUTF8", None)
        p = subprocess.run([sys.executable, os.path.join(ROOT, "trader-exit", "exit.py"), "plan", "--now",
                            "2026-11-23 09:46", "--write"], capture_output=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Stop          %s2,419.35" % RS, p.stdout.decode("utf-8"))
        self.assertEqual(self.read_plan()["stop"], 2419.35)
        p = subprocess.run([sys.executable, os.path.join(ROOT, "trader-exit", "exit.py"), "plan", "--now",
                            "2026-11-23 09:46", "--stop", "price:2443"], capture_output=True, env=env)
        self.assertEqual(p.returncode, 1)
        self.assertIn(RS + "0.50 under the entry", p.stderr.decode("utf-8"))


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-exit", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-exit\n"))
        head = text.split("---")[1]
        for field in ("name:", "description:", "argument-hint:"):
            self.assertIn(field, head)
        self.assertGreater(len(" ".join(head.split("description:")[1].split("argument-hint:")[0].split())), 200)
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], SAFETY)
        low = " ".join(text.lower().split())
        for must in ("before the entry", "a stop is a plan, not a guarantee", "day trading loses most people money",
                     "/trader-timemachine", "/trader-budget", "long side only", RS + "2,419.35", RS + "2,491.80",
                     RS + "24.15", "2,467.65", "too tight to survive the spread", "not financial advice",
                     "explicit yes", "15:00", "india standard time", "before charges", "python3",
                     'python "${claude_skill_dir}/exit.py"', ".ns"):
            self.assertIn(must, low)
        for never in (chr(0x2014), "tick " + "by tick", "real " + "time", "pro" + "fit", "$3", "new york", "nyse",
                      "alpaca", "tsla", "the reel", " et ", "early close", "15:55"):
            self.assertNotIn(never, low)


if __name__ == "__main__":
    unittest.main()
