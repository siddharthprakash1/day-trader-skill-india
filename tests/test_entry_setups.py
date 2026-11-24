"""trader-entry tests for the two later setups: VWAP reclaim (vwap) and gap and go (gap).
Offline: Yahoo is never called. The opening-range and level setups are in test_entry.py.

Fixtures, all real Yahoo answers saved on 2026-11-24:

trader-entry/TCS-2026-11-23-5m.json       range=1d. TCS closes under VWAP at 9:20, 9:25 and 9:30 and back
                                          above it at 9:35; it opens 1.84 % above the previous close.
trader-entry/RELIANCE-2026-11-23-5m.json  never back above VWAP after 9:15; opens only 0.25 % up.
trader-journal/INFY-2026-11-23-5m.json    opens 1.55 % up, closes above its range high at 9:50.
trader-backtest/TCS-60d5m.json            range=60d CUT to three sessions (19, 20 and 23 Nov) with
                                          chartPreviousClose reset to the 18 Nov close it then stands for.
                                          In Yahoo's 60-day data the 9:15 bar has no volume.
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

entry = load("trader-entry", "entry")
exit_tool = load("trader-exit", "exit")

TCS = os.path.join(FIX, "trader-entry", "TCS-2026-11-23-5m.json")
REL = os.path.join(FIX, "trader-entry", "RELIANCE-2026-11-23-5m.json")
INFY = os.path.join(FIX, "trader-journal", "INFY-2026-11-23-5m.json")
TCS60 = os.path.join(FIX, "trader-backtest", "TCS-60d5m.json")
OPEN_TS = 1795405500       # 2026-11-23 09:15 IST = 03:45 UTC
RS = "₹"


def doc(path=TCS):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def at(text, d=None, sym="TCS", **kw):
    ch = entry.parse_chart(json.dumps(d if d is not None else doc()), sym)
    return entry.evaluate(ch, entry.parse_now(text), **kw)


def quote(d):
    return d["chart"]["result"][0]["indicators"]["quote"][0]


def synthetic(rows, sym="DEMO", prev_close=100.0):
    """A Yahoo chart answer for 2026-11-23 from (open, high, low, close, volume) rows; None = a null bar."""
    q = {k: [] for k in ("open", "high", "low", "close", "volume")}
    for r in rows:
        for k, v in zip(("open", "high", "low", "close", "volume"), r or (None,) * 5):
            q[k].append(v)
    meta = {"symbol": sym + ".NS", "dataGranularity": "5m", "regularMarketTime": OPEN_TS + 300 * 75}
    if prev_close is not None:
        meta["chartPreviousClose"] = prev_close
    return {"chart": {"result": [{"meta": meta, "timestamp": [OPEN_TS + 300 * i for i in range(len(rows))],
                                  "indicators": {"quote": [q]}}], "error": None}}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = entry.main(argv)
    return code, out.getvalue(), err.getvalue()


class VwapArithmetic(unittest.TestCase):
    def test_by_hand_on_a_tiny_series(self):
        bars = [{"h": 10.0, "l": 8.0, "c": 9.0, "v": 100},      # typical 9  -> 900 / 100
                {"h": 12.0, "l": 10.0, "c": 11.0, "v": 300},    # typical 11 -> (900 + 3300) / 400
                {"h": 20.0, "l": 20.0, "c": 20.0, "v": 0},      # no volume: adds nothing
                {"h": 13.0, "l": 11.0, "c": 12.0, "v": 100}]    # typical 12 -> 5400 / 500
        self.assertEqual(entry.session_vwap(bars), [9.0, 10.5, 10.5, 10.8])

    def test_no_volume_yet_is_none_not_zero(self):
        bars = [{"h": 10.0, "l": 8.0, "c": 9.0, "v": 0}, {"h": 12.0, "l": 10.0, "c": 11.0, "v": 50}]
        self.assertEqual(entry.session_vwap(bars), [None, 11.0])
        self.assertEqual(entry.session_vwap([]), [])

    def test_the_real_first_bar(self):
        r = at("2026-11-23 09:20", setup="vwap")
        # the 9:15 bar alone: (2433.70 + 2405.80 + 2421.90) / 3
        self.assertEqual(r["vwap"]["value"], round((2433.70 + 2405.80 + 2421.90) / 3, 2))


class VwapReclaim(unittest.TestCase):
    def test_triggers_on_the_first_close_back_above_after_three_bars_under(self):
        r = at("2026-11-23 09:40", setup="vwap")
        self.assertEqual(r["status"], "triggered")
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("9:40 AM", "09:35", 4, 2429.0))
        self.assertEqual((t["vwap"], t["bars_below"], r["level"]), (2416.16, 3, 2416.16))
        # the natural stop: the lowest low of the 9:20, 9:25, 9:30 bars and the trigger bar
        self.assertEqual(r["stop_ref"]["price"], 2401.1)
        self.assertEqual(r["stop_ref"]["exit_flag"], "--stop price:2401.10")
        text = "\n".join(entry.describe(r))
        self.assertIn("TRIGGERED 9:40 AM at %s2,429.00: the 9:35 bar (bar 4) closed above VWAP %s2,416.16 (+0.53%%) "
                      "after 3 bars at or under it." % (RS, RS), text)
        self.assertIn("exit.py plan --from trader/entry.json --stop price:2401.10", text)
        self.assertIn("Nothing was placed", text)
        self.assertNotIn("Opening range", text)

    def test_waits_while_the_bar_is_still_forming(self):
        r = at("2026-11-23 09:36", setup="vwap")
        self.assertEqual((r["status"], r["vwap"]["bars_below"]), ("waiting", 3))
        self.assertIn("3 bars in a row at or under it, the next close above VWAP is the trigger", r["reason"])
        r = at("2026-11-23 09:26", setup="vwap")
        self.assertIn("1 bar in a row at or under it, a reclaim needs 3", r["reason"])

    def test_not_enough_bars_under_is_no_trigger(self):
        # TCS spent exactly three bars under VWAP in the morning: asking for four finds nothing all day
        r = at("2026-11-23 14:31", setup="vwap", min_bars_below=4)
        self.assertEqual(r["status"], "expired")
        self.assertIn("after 4 bars at or under it by 14:30", r["reason"])
        # above VWAP with no run under it first is not a reclaim
        r = at("2026-11-23 10:30", setup="vwap", min_bars_below=4)
        self.assertEqual(r["status"], "waiting")
        self.assertIn("a reclaim needs 4 bars in a row at or under VWAP first", r["reason"])

    def test_never_back_above_expires(self):
        r = at("2026-11-23 11:01", doc(REL), "RELIANCE", setup="vwap")
        self.assertEqual((r["status"], r["vwap"]["bars_below"]), ("waiting", 20))
        r = at("2026-11-23 14:31", doc(REL), "RELIANCE", setup="vwap")
        self.assertEqual(r["status"], "expired")
        self.assertIsNone(r["trigger"])

    def test_volume_check_is_against_the_earlier_bars_of_the_session(self):
        r = at("2026-11-23 09:40", setup="vwap", min_volume_x=1.5)
        self.assertEqual(r["status"], "waiting")
        avg = (156841 + 89391 + 169374 + 77780) / 4.0
        self.assertIn("%.2fx the session's average volume, under your 1.50x" % (106322 / avg), r["reason"])
        r = at("2026-11-23 09:40", setup="vwap", min_volume_x=0.5)
        self.assertEqual(r["trigger"]["volume_x"], round(106322 / avg, 2))

    def test_the_entry_window(self):
        rows = [(100, 100.5, 99.5, 100, 1000)] + [(99, 99.5, 98.5, 99, 1000)] * 3 + [(99, 101, 99, 100.8, 1000)]
        d = synthetic(rows)
        r = at("2026-11-23 09:40", d, "DEMO", setup="vwap")
        self.assertEqual(r["trigger"]["bar_index"], 4)
        # the same reclaim before no_entries_before is not an entry, and does not come back later
        cfg = {"no_entries_before": "10:00", "no_entries_after": "14:30"}
        r = at("2026-11-23 09:40", d, "DEMO", setup="vwap", cfg=cfg)
        self.assertEqual(r["status"], "waiting")


class QuirkyBars(unittest.TestCase):
    def test_a_first_bar_without_volume_is_left_out_and_said(self):
        # Yahoo's 60-day data: the 9:15 bar has prices but no volume
        r = at("2026-11-23 09:40", doc(TCS60), setup="vwap")
        self.assertEqual(r["vwap"]["bars_without_volume"], 1)
        self.assertEqual(r["status"], "triggered")
        self.assertEqual((r["trigger"]["bar_index"], r["trigger"]["price"], r["stop_ref"]["price"]), (4, 2429.0, 2401.1))
        # VWAP without the 9:15 bar's volume is not the same number as with it
        self.assertNotEqual(r["trigger"]["vwap"], 2416.16)
        self.assertIn("1 bar with no volume left out", "\n".join(entry.describe(r)))
        r = at("2026-11-23 09:20", doc(TCS60), setup="vwap")
        self.assertEqual((r["status"], r["vwap"]["value"]), ("no-data", None))

    def test_no_volume_at_all_says_vwap_could_not_be_computed(self):
        d = doc()
        quote(d)["volume"] = [0] * len(quote(d)["volume"])
        r = at("2026-11-23 11:00", d, setup="vwap")
        self.assertEqual(r["status"], "no-data")
        self.assertIn("VWAP could not be computed: Yahoo sent no volume for TCS", r["reason"])
        d = doc()
        quote(d)["volume"] = [None] * len(quote(d)["volume"])
        self.assertEqual(at("2026-11-23 11:00", d, setup="vwap")["status"], "no-data")

    def test_a_missing_bar_breaks_the_run(self):
        d = doc()
        for k in ("open", "high", "low", "close", "volume"):
            quote(d)[k][2] = None                       # the 9:25 bar came as nulls
        r = at("2026-11-23 09:40", d, setup="vwap")
        self.assertEqual(r["status"], "waiting")        # 9:20 under, hole, 9:30 under: only one in a row
        self.assertEqual(r["vwap"]["missing_bars"], 1)
        self.assertIn("1 bar missing from Yahoo's data", "\n".join(entry.describe(r)))

    def test_the_late_holes_do_not_matter(self):
        r = at("2026-11-23 15:31", setup="vwap")        # the 15:20 bar is null in the real file
        self.assertEqual((r["status"], r["trigger"]["time"]), ("triggered", "9:40 AM"))
        self.assertEqual(r["vwap"]["missing_bars"], 1)

    def test_a_close_exactly_at_vwap_is_not_above_it(self):
        rows = [(100, 100, 100, 100, 1000)] * 5        # every close equals VWAP
        r = at("2026-11-23 09:45", synthetic(rows), "DEMO", setup="vwap")
        self.assertEqual((r["status"], r["vwap"]["bars_below"]), ("waiting", 5))


class NoLookAhead(unittest.TestCase):
    def test_bars_after_the_trigger_bar_change_nothing(self):
        want = at("2026-11-23 15:31", setup="vwap")
        for setup in ("vwap", "gap"):
            want = at("2026-11-23 15:31", setup=setup)
            k = want["trigger"]["bar_index"]
            cut = doc()
            res = cut["chart"]["result"][0]
            res["timestamp"] = res["timestamp"][:k + 1]
            for key in quote(cut):
                quote(cut)[key] = quote(cut)[key][:k + 1]
            wild = doc()
            for key, v in (("high", 9999.0), ("low", 1.0), ("close", 5000.0), ("volume", 10 ** 9)):
                quote(wild)[key][k + 1:] = [v] * (len(quote(wild)[key]) - k - 1)
            for other in (cut, wild):
                got = at("2026-11-23 15:31", other, setup=setup)
                self.assertEqual(got["trigger"], want["trigger"], setup)
                self.assertEqual(got["stop_ref"], want["stop_ref"], setup)

    def test_the_trigger_is_known_at_its_own_close(self):
        early = at("2026-11-23 09:40", setup="vwap")
        late = at("2026-11-23 15:31", setup="vwap")
        self.assertEqual(early["trigger"], late["trigger"])
        self.assertEqual(at("2026-11-23 09:39", setup="vwap")["status"], "waiting")


class GapAndGo(unittest.TestCase):
    def test_gap_day_triggers_on_the_close_above_the_range_high(self):
        r = at("2026-11-23 09:45", setup="gap")
        self.assertEqual(r["status"], "triggered")
        self.assertEqual(r["gap"]["open"], 2420.0)
        self.assertEqual(r["gap"]["prev_close"], 2376.2)
        self.assertEqual(r["gap"]["pct"], round(100 * (2420.0 / 2376.2 - 1), 2))
        self.assertEqual((r["trigger"]["time"], r["trigger"]["price"], r["level"]), ("9:45 AM", 2443.5, 2433.7))
        self.assertEqual(r["stop_ref"], {"price": 2405.0, "rule": "or-low", "what": "the opening-range low",
                                         "exit_flag": "--stop or-low"})
        text = "\n".join(entry.describe(r))
        self.assertIn("Gap: opened at %s2,420.00, +1.84%% from the previous close %s2,376.20" % (RS, RS), text)
        self.assertIn("closed above the range high %s2,433.70" % RS, text)
        self.assertIn("exit.py plan --from trader/entry.json --stop or-low", text)
        # the same bar the opening-range setup calls
        self.assertEqual(at("2026-11-23 09:45")["trigger"], r["trigger"])

    def test_infy(self):
        r = at("2026-11-23 09:50", doc(INFY), "INFY", setup="gap")
        self.assertEqual((r["gap"]["pct"], r["trigger"]["time"], r["trigger"]["price"]), (1.55, "9:50 AM", 1123.0))
        self.assertEqual(at("2026-11-23 09:46", doc(INFY), "INFY", setup="gap")["status"], "waiting")

    def test_a_small_gap_expires_at_once(self):
        r = at("2026-11-23 09:22", doc(REL), "RELIANCE", setup="gap")
        self.assertEqual((r["status"], r["phase"], r["gap"]["pct"]), ("expired", "no-gap", 0.25))
        self.assertIn("+0.25% from the previous close", r["reason"])
        self.assertIn("gap and go needs +1.00% or more", r["reason"])
        self.assertIn("No entry on this setup today", "\n".join(entry.describe(r)))
        # the threshold is yours to set
        self.assertEqual(at("2026-11-23 09:45", setup="gap", gap_pct=2.0)["status"], "expired")
        self.assertEqual(at("2026-11-23 15:31", doc(REL), "RELIANCE", setup="gap", gap_pct=0.2)["status"], "expired")
        self.assertEqual(at("2026-11-23 15:31", doc(REL), "RELIANCE", setup="gap", gap_pct=0.2)["phase"], "after-close")

    def test_gap_day_without_a_breakout_waits_then_expires(self):
        rows = [(102, 103, 101, 102, 1000)] * 4
        d = synthetic(rows, prev_close=100.0)
        r = at("2026-11-23 09:35", d, "DEMO", setup="gap")
        self.assertEqual((r["status"], r["gap"]["pct"]), ("waiting", 2.0))
        self.assertIn("under the range high", r["reason"])
        r = at("2026-11-23 09:17", d, "DEMO", setup="gap")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "forming"))

    def test_previous_close_on_a_replayed_day_is_the_last_bar_before_it(self):
        r = at("2026-11-23 09:45", doc(TCS60), setup="gap")
        self.assertEqual(r["gap"]["prev_close"], 2376.2)          # the last 20 Nov bar in the file
        self.assertIn("2026-11-20", r["gap"]["prev_close_from"])
        self.assertEqual(r["status"], "triggered")
        # the chart's first session has nothing before it: Yahoo's chartPreviousClose
        r = at("2026-11-19 09:45", doc(TCS60), setup="gap")
        self.assertEqual((r["gap"]["prev_close"], r["gap"]["prev_close_from"]), (2379.5, "Yahoo's previous close"))
        self.assertEqual(r["gap"]["open"], 2385.6)
        self.assertEqual(r["status"], "expired")                  # +0.26 %

    def test_what_cannot_be_measured_is_no_data(self):
        rows = [(102, 103, 101, 102, 1000)] * 4
        r = at("2026-11-23 09:35", synthetic(rows, prev_close=None), "DEMO", setup="gap")
        self.assertEqual(r["status"], "no-data")
        self.assertIn("no previous close", r["reason"])
        r = at("2026-11-23 09:35", synthetic([None] + rows[1:]), "DEMO", setup="gap")
        self.assertEqual(r["status"], "no-data")
        self.assertIn("no 9:15 bar", r["reason"])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def plan(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = exit_tool.main(["plan"] + argv)
        return code, out.getvalue(), err.getvalue()

    def test_vwap_entry_json_keeps_the_contract_and_adds_the_stop(self):
        code, text, _ = run(["check", "TCS", "--setup", "vwap", "--offline", TCS, "--now", "2026-11-23 09:40", "--write"])
        self.assertEqual(code, 0)
        self.assertIn("Wrote trader/entry.json (status triggered).", text)
        with open(os.path.join("trader", "entry.json"), encoding="utf-8") as fh:
            e = json.load(fh)
        want = {"created_by": "trader-entry", "symbol": "TCS", "setup": "vwap", "side": "buy", "date": "2026-11-23",
                "status": "triggered", "time": "9:40 AM", "bar_start": "09:35", "bar_index": 4, "price": 2429.0,
                "level": 2416.16, "interval": "5m", "source": "yahoo"}
        for k, v in want.items():
            self.assertEqual(e[k], v, k)
        self.assertEqual(e["stop_ref"]["price"], 2401.1)
        # trader-exit has no default stop for it and says so; with the flag the tool printed, it plans
        code, _, err = self.plan(["--now", "2026-11-23 09:41"])
        self.assertEqual(code, 1)
        self.assertIn("a vwap setup has no opening-range stop to default to", err)
        code, text, err = self.plan(e["stop_ref"]["exit_flag"].split() + ["--now", "2026-11-23 09:41", "--json"])
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["setup"], p["entry"], p["stop"], p["target"], p["entry_time"], p["flat_by"]),
                         ("vwap", 2429.0, 2401.1, 2484.8, "9:40 AM", "15:00"))

    def test_gap_entry_json_plans_with_the_or_low_stop(self):
        code, _, _ = run(["check", "TCS", "--setup", "gap", "--offline", TCS, "--now", "2026-11-23 09:45", "--write"])
        self.assertEqual(code, 0)
        with open(os.path.join("trader", "entry.json"), encoding="utf-8") as fh:
            e = json.load(fh)
        self.assertEqual((e["setup"], e["price"], e["level"]), ("gap", 2443.5, 2433.7))
        self.assertEqual(e["opening_range"], {"high": 2433.7, "low": 2405.0, "minutes": 15})
        self.assertEqual(e["gap"]["pct"], 1.84)
        code, text, err = self.plan(e["stop_ref"]["exit_flag"].split() + ["--now", "2026-11-23 09:46", "--json"])
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["stop"], p["stop_rule"], p["target"]), (2405.0, "or_low", 2520.5))

    def test_the_opening_range_entry_json_is_unchanged(self):
        run(["check", "TCS", "--offline", TCS, "--now", "2026-11-23 09:45", "--write"])
        with open(os.path.join("trader", "entry.json"), encoding="utf-8") as fh:
            e = json.load(fh)
        self.assertNotIn("stop_ref", e)
        self.assertNotIn("gap", e)
        self.assertNotIn("vwap", e)

    def test_a_setup_that_did_not_trigger_writes_no_stop(self):
        run(["check", "RELIANCE", "--setup", "gap", "--offline", REL, "--now", "2026-11-23 09:45", "--write"])
        with open(os.path.join("trader", "entry.json"), encoding="utf-8") as fh:
            e = json.load(fh)
        self.assertEqual((e["status"], e["stop_ref"], e["price"]), ("expired", None, None))

    def test_watch_replays_a_vwap_day_bar_by_bar(self):
        code, text, _ = run(["watch", "TCS", "--setup", "vwap", "--offline", TCS, "--now", "2026-11-23 09:10"])
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertIn(" 9:25 bar  closed %s2,405.90  %s10.44 under VWAP %s2,416.34 (2 in a row)" % (RS, RS, RS), lines)
        self.assertTrue(any(ln.startswith(" 9:35 bar") and "TRIGGERED: closed above VWAP" in ln for ln in lines))
        self.assertFalse(any(ln.startswith(" 9:40 bar") for ln in lines))
        self.assertTrue(os.path.exists(os.path.join("trader", "entry.json")))

    def test_watch_stops_at_once_on_a_day_without_a_gap(self):
        code, text, _ = run(["watch", "RELIANCE", "--setup", "gap", "--offline", REL, "--now", "2026-11-23 09:10"])
        self.assertEqual(code, 1)
        self.assertIn("EXPIRED  it opened at %s1,381.00, +0.25%%" % RS, text)
        self.assertNotIn("9:30 bar", text)

    def test_flag_errors(self):
        for argv, msg in ((["--setup", "vwap", "--level", "2400"], "--level is for --setup level"),
                          (["--setup", "vwap", "--min-bars-below", "0"], "--min-bars-below must be 1 or more"),
                          (["--setup", "gap", "--gap-pct", "0"], "--gap-pct must be above 0"),
                          (["--setup", "gap", "--side", "short"], "longs only")):
            code, _, err = run(["check", "TCS", "--offline", TCS] + argv)
            self.assertEqual(code, 1)
            self.assertIn(msg, err)
        with self.assertRaises(entry.EntryError):
            at("2026-11-23 09:45", setup="flag")

    def test_json_output_has_no_internal_fields(self):
        code, text, _ = run(["check", "TCS", "--setup", "vwap", "--offline", TCS, "--now", "2026-11-23 09:40", "--json"])
        r = json.loads(text)
        self.assertNotIn("_bars", r)
        self.assertEqual(r["stop_ref"]["price"], 2401.1)


class SkillDoc(unittest.TestCase):
    def test_skill_md_covers_the_new_setups(self):
        with open(os.path.join(ROOT, "trader-entry", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        low = " ".join(text.lower().split())
        for must in ("--setup vwap", "--setup gap", "vwap reclaim", "gap and go", "typical price", "--min-bars-below",
                     "--gap-pct", "--stop price:2401.10", "--stop or-low", "pullback low", "opening-range low",
                     "could not be computed", RS + "2,429.00", RS + "2,416.16", "+1.84%", "stop_ref"):
            self.assertIn(must, low, must)


if __name__ == "__main__":
    unittest.main()
