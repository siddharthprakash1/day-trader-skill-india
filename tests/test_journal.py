import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, ROOT, FIX

j = load("trader-journal", "journal")
ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")

DIR = os.path.join(FIX, "trader-journal")
# Yahoo v8 chart, range=1d interval=5m, fetched 2026-11-24 (an NSE holiday): the session of Monday 2026-11-23.
TCS = os.path.join(DIR, "TCS-2026-11-23-5m.json")
INFY = os.path.join(DIR, "INFY-2026-11-23-5m.json")
CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
ORDERS_PATH = os.path.join(FIX, "trader-risk", "orders-tcs.json")
DAY = "2026-11-23"
AFTER = "2026-11-23 16:00"
NOW_UTC = j.parse_now(AFTER)
RUPEE = chr(0x20b9)

# Real plans, in the shape trader-exit writes. TCS: opening range 2405.00 to 2433.70, the 09:40 bar closes
# at 2443.50 (entry 9:45 AM), midpoint stop, 1R target. INFY: range 1107.70 to 1120.50, the 09:45 bar closes
# at 1123.00 (entry 9:50 AM), midpoint stop, 2R target.
TCS_PLAN = {"created_by": "trader-exit", "symbol": "TCS", "side": "buy", "date": DAY, "setup": "orb",
            "entry": 2443.5, "entry_time": "9:45 AM", "entry_bar": 5, "interval": "5m",
            "opening_range": {"high": 2433.7, "low": 2405.0, "minutes": 15}, "stop": 2419.35,
            "stop_rule": "or_mid", "target": 2467.65, "target_r": 1.0, "risk_per_share": 24.15,
            "reward_risk": 1.0, "flat_by": "15:00"}


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def days_of(path, sym):
    return j.load_days(read(path), sym, NOW_UTC)


def trade(sym="TCS", entry_time="9:45 AM", entry=2443.5, stop=2419.35, target=2467.65, qty=15, **kw):
    return j.make_trade(sym, kw.pop("date", DAY), entry_time, entry, stop, target, qty, **kw)


def tcs():
    return trade()


def infy():
    return trade("INFY", "9:50 AM", 1123.0, 1114.1, 1140.8, 33)


def chart(rows, sym="DEMO.NS", day=datetime.date(2026, 11, 23), first="09:15"):
    """A small synthetic Yahoo chart: rows of (open, high, low, close) or None, 5 minutes apart."""
    h, m = map(int, first.split(":"))
    t0 = int((j.ist_to_utc(datetime.datetime.combine(day, datetime.time(h, m))) - j.EPOCH).total_seconds())
    q = {"open": [], "high": [], "low": [], "close": [], "volume": []}
    for r in rows:
        for k, v in zip(("open", "high", "low", "close"), r or (None,) * 4):
            q[k].append(v)
        q["volume"].append(1000 if r else None)
    return json.dumps({"chart": {"result": [{
        "meta": {"symbol": sym, "dataGranularity": "5m", "currency": "INR"},
        "timestamp": [t0 + 300 * i for i in range(len(rows))], "indicators": {"quote": [q]}}], "error": None}})


def demo(entry_time="9:25", flat_by="10:00", **kw):
    """Entry 100.00, stop 99.00, target 101.00, 10 shares of DEMO."""
    return trade("DEMO", entry_time, 100.0, 99.0, 101.0, 10, flat_by=flat_by, **kw)


QUIET = (100.0, 100.5, 99.5, 100.0)


def sim(t, rows, **kw):
    return j.simulate(t, j.load_days(chart(rows, **kw), t["symbol"], NOW_UTC).get(DAY))


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = j.main(argv)
    return code, out.getvalue(), err.getvalue()


class Clock(unittest.TestCase):
    def test_ist_is_a_fixed_five_and_a_half_hours(self):
        self.assertEqual(j.to_ist(datetime.datetime(2026, 11, 23, 4, 0)), datetime.datetime(2026, 11, 23, 9, 30))
        self.assertEqual(j.to_ist(datetime.datetime(2026, 6, 1, 4, 0)), datetime.datetime(2026, 6, 1, 9, 30))
        self.assertEqual(j.ist_to_utc(datetime.datetime(2026, 11, 23, 15, 30)),
                         datetime.datetime(2026, 11, 23, 10, 0))

    def test_now_is_india_time_unless_it_says_otherwise(self):
        self.assertEqual(j.parse_now("2026-11-23 16:00"), datetime.datetime(2026, 11, 23, 10, 30))
        self.assertEqual(j.parse_now("2026-11-23 16:00 IST"), datetime.datetime(2026, 11, 23, 10, 30))
        self.assertEqual(j.parse_now("2026-11-23T10:30:00Z"), datetime.datetime(2026, 11, 23, 10, 30))
        with self.assertRaises(j.JournalError):
            j.parse_now("tomorrow")

    def test_clock_times(self):
        self.assertEqual(j.parse_clock("9:45 AM", "x"), 585)
        self.assertEqual(j.parse_clock("09:45", "x"), 585)
        self.assertEqual(j.parse_clock("3:00", "x"), 900)
        self.assertEqual(j.parse_clock("15:00", "x"), 900)
        self.assertEqual(j.parse_clock("2:55 pm", "x"), 895)
        self.assertEqual(j.clock24(585), "09:45")
        with self.assertRaises(j.JournalError):
            j.parse_clock("soon", "x")

    def test_holidays(self):
        self.assertEqual(j.trading_day(datetime.date(2026, 11, 23)), (True, ""))
        self.assertEqual(j.trading_day(datetime.date(2026, 11, 24)), (False, "NSE holiday: Guru Nanak Jayanti"))
        self.assertEqual(j.trading_day(datetime.date(2026, 11, 22)), (False, "weekend"))
        self.assertEqual(len(j.NSE_HOLIDAYS), 16)
        self.assertEqual(j.CALENDAR_YEARS, (2026,))

    def test_session_constants(self):
        self.assertEqual((j.OPEN_MIN, j.CLOSE_MIN, j.SESSION_BARS), (555, 930, 75))


class Symbols(unittest.TestCase):
    def test_yahoo_symbol(self):
        self.assertEqual(j.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(j.yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(j.yahoo_symbol("TCS.NS"), "TCS.NS")
        self.assertEqual(j.yahoo_symbol("^NSEI"), "^NSEI")
        self.assertEqual(j.nse_symbol("m&m.ns"), "M&M")

    def test_the_url_quotes_the_ampersand(self):
        self.assertEqual(j.chart_url("M&M", "5d"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/M%26M.NS?range=5d&interval=5m")
        self.assertIn("/BAJAJ-AUTO.NS?", j.chart_url("BAJAJ-AUTO", "60d", "query1"))

    def test_nse_symbols_are_accepted(self):
        for s in ("RELIANCE", "M&M", "BAJAJ-AUTO", "3MINDIA", "tcs.ns"):
            self.assertEqual(j.clean_symbol(s), j.nse_symbol(s))
        for s in ("", "TCS 15", "A/B", "X" * 30):
            with self.assertRaises(j.JournalError):
                j.clean_symbol(s)

    def test_a_trade_in_m_and_m(self):
        t = trade("m&m.ns", "9:45 AM", 3560.0, 3540.0, 3600.0, 5)
        self.assertEqual((t["symbol"], t["id"]), ("M&M", "M&M-2026-11-23-0945"))

    def test_the_user_agent_is_the_packs(self):
        self.assertEqual(j.UA, "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)")


class Charges(unittest.TestCase):
    def money(self, c, **want):
        for k, v in want.items():
            self.assertEqual(c[k], Decimal(v), k)

    def test_the_table_is_grows_published_rates(self):
        self.assertEqual(j.CHARGES, {
            "brokerage_flat": "20", "brokerage_pct": "0.1", "brokerage_min": "5", "stt_sell_pct": "0.025",
            "stamp_buy_pct": "0.003", "exchange_pct": "0.00307", "sebi_pct": "0.0001", "ipft_pct": "0.0001",
            "gst_pct": "18", "dp_charge": "0", "auto_squareoff_fee": "50"})
        self.assertIn("groww.in/pricing", j.CHARGES_SOURCE)
        self.assertIn("2026-11-24", j.CHARGES_SOURCE)

    def test_tcs_round_trip_line_by_line(self):
        # buy 15 x 2443.50 = 36,652.50; sell 15 x 2467.65 = 37,014.75; both sides 73,667.25
        c = j.round_trip(2443.50, 2467.65, 15)
        self.money(c, buy_value="36652.50", sell_value="37014.75", turnover="73667.25",
                   brokerage_buy="20.00", brokerage_sell="20.00", brokerage="40.00",   # 0.1 % is 36.65: capped
                   stt="9.25",            # 37,014.75 x 0.025 % = 9.2537
                   stamp_duty="1.10",     # 36,652.50 x 0.003 % = 1.0996
                   exchange="2.26",       # 73,667.25 x 0.00307 % = 2.2616
                   sebi="0.07", ipft="0.07",   # 73,667.25 x 0.0001 % = 0.0737
                   gst_base="42.40", gst="7.63",   # 18 % of 40 + 2.26 + 0.07 + 0.07 = 7.632
                   dp="0.00", auto_squareoff="0.00", total="60.38", gross="362.25", net="301.87")

    def test_tcs_at_the_stop(self):
        c = j.round_trip(2443.50, 2419.35, 15)
        self.money(c, sell_value="36290.25", stt="9.07", exchange="2.24", gst="7.63", total="60.18",
                   gross="-362.25", net="-422.43")

    def test_the_five_rupee_minimum(self):
        # 10 shares at 100: an order of 1,000, 0.1 % is 1.00, so the minimum 5.00 applies on each side
        c = j.round_trip(100, 101, 10)
        self.money(c, brokerage_buy="5.00", brokerage_sell="5.00", stt="0.25", stamp_duty="0.03",
                   exchange="0.06", sebi="0.00", ipft="0.00", gst_base="10.06", gst="1.81", total="12.15",
                   gross="10.00", net="-2.15")

    def test_the_percent_branch(self):
        # 100 shares at 100: orders of 10,000 and 10,100, 0.1 % is 10.00 and 10.10, under the 20 cap
        c = j.round_trip(100, 101, 100)
        self.money(c, brokerage_buy="10.00", brokerage_sell="10.10", brokerage="20.10", stt="2.53",
                   stamp_duty="0.30", exchange="0.62", sebi="0.02", ipft="0.02", gst_base="20.76", gst="3.74",
                   total="27.33", gross="100.00", net="72.67")

    def test_the_twenty_rupee_cap_and_its_edges(self):
        r = j.load_charges()[0]
        self.assertEqual(j.order_brokerage(Decimal("4999"), r), Decimal("5.00"))
        self.assertEqual(j.order_brokerage(Decimal("5000"), r), Decimal("5.00"))
        self.assertEqual(j.order_brokerage(Decimal("5010"), r), Decimal("5.01"))
        self.assertEqual(j.order_brokerage(Decimal("19990"), r), Decimal("19.99"))
        self.assertEqual(j.order_brokerage(Decimal("20000"), r), Decimal("20.00"))
        self.assertEqual(j.order_brokerage(Decimal("500000"), r), Decimal("20.00"))
        self.assertEqual(j.order_brokerage(Decimal("0"), r), Decimal("0.00"))

    def test_gst_is_on_brokerage_exchange_sebi_and_ipft_only(self):
        c = j.round_trip(2443.50, 2467.65, 15)
        self.assertEqual(c["gst_base"], c["brokerage"] + c["exchange"] + c["sebi"] + c["ipft"])
        self.assertEqual(c["gst"], (c["gst_base"] * 18 / 100).quantize(Decimal("0.01")))
        self.assertEqual(c["total"], c["brokerage"] + c["stt"] + c["stamp_duty"] + c["exchange"] + c["sebi"]
                         + c["ipft"] + c["gst"])

    def test_auto_square_off_adds_fifty_plus_gst(self):
        plain = j.round_trip(2443.50, 2467.65, 15)
        c = j.round_trip(2443.50, 2467.65, 15, auto_squareoff=True)
        self.money(c, auto_squareoff="50.00", gst_base="92.40", gst="16.63")   # 18 % of 92.40 = 16.632
        self.assertEqual(c["total"] - plain["total"], Decimal("59.00"))

    def test_two_sell_orders_pay_brokerage_twice(self):
        c = j.round_trip(2443.50, None, 15, sells=[(2467.65, 10), (2467.65, 5)])
        self.money(c, brokerage_sell="32.34", sell_value="37014.75", sell="2467.65")   # 20.00 + 12.34
        with self.assertRaises(j.JournalError):
            j.round_trip(2443.50, None, 15, sells=[(2467.65, 10)])

    def test_break_even_is_the_first_price_that_nets_zero_or_more(self):
        be = j.break_even(2443.50, 15)
        self.assertEqual(be, Decimal("2447.52"))
        self.assertGreaterEqual(j.round_trip(2443.50, be, 15)["net"], 0)
        self.assertLess(j.round_trip(2443.50, be - Decimal("0.01"), 15)["net"], 0)
        self.assertEqual(j.break_even(100, 10), Decimal("101.22"))

    def test_the_plan_after_charges(self):
        pc = j.plan_charges(2443.50, 2419.35, 2467.65, 15, j.load_charges()[0])
        self.assertEqual((pc["win"]["net"], pc["loss"]["net"]), (Decimal("301.87"), Decimal("-422.43")))
        self.assertEqual(round(float(pc["gross_reward_risk"]), 2), 1.0)
        self.assertEqual(round(float(pc["net_reward_risk"]), 2), 0.71)
        self.assertEqual(pc["break_even_move"], Decimal("4.02"))

    def test_bad_inputs_are_refused(self):
        for args in ((0, 100, 10), (100, 0, 10), (100, 101, 0), (100, 101, -5)):
            with self.assertRaises(j.JournalError):
                j.round_trip(*args)

    def test_charges_against_the_gross(self):
        self.assertEqual(j.charge_share(Decimal("362.25"), Decimal("60.38")),
                         "charges took 16.7 % of your gross profit")
        self.assertEqual(j.charge_share(Decimal("100"), Decimal("38")), "charges took 38.0 % of your gross profit")
        self.assertEqual(j.charge_share(Decimal("-293.70"), Decimal("60.34")),
                         "charges added %s60.34 to a gross loss of %s293.70" % (RUPEE, RUPEE))

    def test_money_is_exact_to_the_paisa(self):
        self.assertEqual(j.paise(2.525), Decimal("2.53"))       # half up, not the float's 2.52
        self.assertEqual(j.paise("1,234.565"), Decimal("1234.57"))
        self.assertEqual(j.money(-422.43), "-%s422.43" % RUPEE)
        self.assertEqual(j.money(36652.5), "%s36,652.50" % RUPEE)
        self.assertEqual(j.signed(301.87), "+%s301.87" % RUPEE)


class ConfigOverride(unittest.TestCase):
    def test_another_brokers_rates(self):
        rates, changed = j.load_charges({"charges": {"brokerage_flat": 0, "brokerage_min": 0, "gst_pct": 18}})
        self.assertEqual(sorted(changed), ["brokerage_flat", "brokerage_min"])
        c = j.round_trip(2443.50, 2467.65, 15, rates)
        self.assertEqual(c["brokerage"], Decimal("0.00"))
        self.assertEqual(c["gst"], Decimal("0.43"))             # 18 % of 2.26 + 0.07 + 0.07
        self.assertEqual(c["total"], Decimal("13.18"))          # 9.25 + 1.10 + 2.26 + 0.07 + 0.07 + 0.43

    def test_no_block_means_the_table(self):
        rates, changed = j.load_charges({"budget": 50000})
        self.assertEqual(changed, [])
        self.assertEqual(rates["brokerage_flat"], Decimal("20"))

    def test_unknown_or_negative_lines_are_refused(self):
        for bad in ({"brokerage": 20}, {"stt_sell_pct": -1}, {"gst_pct": "lots"}, ["x"]):
            with self.assertRaises(j.JournalError):
                j.load_charges({"charges": bad})

    def test_the_cli_reads_the_block(self):
        d = tempfile.mkdtemp()
        try:
            cfg = os.path.join(d, "config.json")
            with open(cfg, "w", encoding="utf-8") as fh:
                json.dump({"charges": {"brokerage_flat": 0, "brokerage_min": 0}}, fh)
            code, text, _ = run(["charges", "--buy", "2443.50", "--sell", "2467.65", "--qty", "15",
                                 "--config", cfg])
            self.assertEqual(code, 0)
            self.assertIn("  %-18s %12s" % ("Total charges", RUPEE + "13.18") + chr(10), text)
            self.assertIn("overridden by the \"charges\" block in the config: brokerage_flat, brokerage_min", text)
        finally:
            shutil.rmtree(d)


class RealBars(unittest.TestCase):
    def test_the_fixture_is_the_real_session(self):
        days = days_of(TCS, "TCS")
        self.assertEqual(list(days), [DAY])
        day = days[DAY]
        self.assertEqual(day[0], {"start": "09:15", "o": 2420.0, "h": 2433.7, "l": 2405.8, "c": 2421.9})
        self.assertEqual(day[5]["c"], 2443.5)                   # the 09:40 entry bar
        self.assertNotIn(73, day)                               # the 15:20 bar has no prices on Yahoo

    def test_tcs_target_first_in_the_10_05_bar(self):
        day = days_of(TCS, "TCS")[DAY]
        self.assertEqual([day[s]["h"] for s in range(6, 10)], [2453.8, 2460.8, 2460.0, 2466.0])   # all under
        self.assertEqual(day[10]["start"], "10:05")
        self.assertEqual(day[10]["h"], 2482.0)
        self.assertGreater(min(day[s]["l"] for s in range(6, 11)), 2419.35)
        res, why = j.simulate(tcs(), day)
        self.assertIsNone(why)
        self.assertEqual((res["outcome"], res["exit_price"], res["exit_time"], res["notes"]),
                         ("target", 2467.65, "10:05", []))

    def test_infy_stop_first_in_the_10_55_bar(self):
        day = days_of(INFY, "INFY")[DAY]
        self.assertEqual(day[6]["c"], 1123.0)                   # the 09:45 entry bar
        self.assertEqual((day[20]["start"], day[20]["l"]), ("10:55", 1114.1))
        self.assertLess(max(day[s]["h"] for s in range(7, 21)), 1140.8)
        self.assertGreater(min(day[s]["l"] for s in range(7, 20)), 1114.1)
        res, why = j.simulate(infy(), day)
        self.assertEqual((res["outcome"], res["exit_price"], res["exit_time"]), ("stop", 1114.1, "10:55"))

    def test_a_target_out_of_reach_goes_flat_at_the_14_55_close(self):
        day = days_of(TCS, "TCS")[DAY]
        res, _ = j.simulate(trade(target=2600.0), day)
        self.assertEqual((res["outcome"], res["exit_price"], res["exit_time"]), ("flat", 2453.2, "15:00"))
        self.assertEqual(day[68], {"start": "14:55", "o": 2456.0, "h": 2456.5, "l": 2453.1, "c": 2453.2})

    def test_r_and_net_for_the_two_real_trades(self):
        rates = j.load_charges()[0]
        t = j.settle(tcs(), 2467.65, "10:05", "target", "simulated", rates, NOW_UTC)
        r = t["result"]
        self.assertEqual((t["status"], r["r"], r["net_r"], r["gross"], r["net"]),
                         ("scored", 1.0, 0.83, 362.25, 301.87))
        self.assertEqual(r["charges"], {"brokerage": 40.0, "stt": 9.25, "stamp_duty": 1.1, "exchange": 2.26,
                                        "sebi": 0.07, "ipft": 0.07, "gst": 7.63, "dp": 0.0,
                                        "auto_squareoff": 0.0, "total": 60.38})
        t = j.settle(infy(), 1114.1, "10:55", "stop", "simulated", rates, NOW_UTC)
        r = t["result"]
        # buy 33 x 1123.00 = 37,059.00; sell 33 x 1114.10 = 36,765.30
        self.assertEqual((r["r"], r["net_r"], r["gross"], r["charges"]["total"], r["net"]),
                         (-1.0, -1.21, -293.7, 60.34, -354.04))

    def test_a_flat_exit_has_a_fractional_r(self):
        t = j.settle(trade(target=2600.0), 2453.2, "15:00", "flat", "simulated", j.load_charges()[0], NOW_UTC)
        self.assertEqual(t["result"]["r"], 0.4)                 # (2453.20 - 2443.50) / 24.15 = 0.4017
        self.assertEqual(t["result"]["gross"], 145.5)


class SyntheticBars(unittest.TestCase):
    """Entry 100 at 09:25 (the 09:20 bar), stop 99, target 101, flat by 10:00: bars 09:25 to 09:55."""

    def test_target_first(self):
        res, _ = sim(demo(), [QUIET, QUIET, QUIET, (100.2, 101.0, 100.1, 100.9), (100.9, 101, 98, 98)])
        self.assertEqual((res["outcome"], res["exit_price"], res["exit_time"]), ("target", 101.0, "09:30"))

    def test_stop_first(self):
        res, _ = sim(demo(), [QUIET, QUIET, (100, 100.4, 99.0, 99.3), (99.3, 102, 99.2, 101.5)])
        self.assertEqual((res["outcome"], res["exit_price"], res["exit_time"]), ("stop", 99.0, "09:25"))
        self.assertEqual(res["notes"], [])

    def test_both_in_one_bar_counts_as_the_stop(self):
        res, _ = sim(demo(), [QUIET, QUIET, (100, 101.5, 98.9, 101.2)])
        self.assertEqual((res["outcome"], res["exit_price"]), ("stop", 99.0))
        self.assertIn("touched both the stop and the target: counted as the stop", res["notes"][0])

    def test_the_entry_bar_itself_is_not_walked(self):
        # the 09:20 entry bar spikes to 105 and dips to 95: both happened before the 09:25 entry
        rows = [QUIET, (100, 105, 95, 100)] + [QUIET] * 7
        res, _ = sim(demo(), rows)
        self.assertEqual(res["outcome"], "flat")

    def test_flat_by_takes_the_close_of_the_last_bar_before_it(self):
        rows = [QUIET, QUIET] + [QUIET] * 6 + [(100, 100.8, 99.6, 100.7), (100.7, 150, 50, 120)]
        res, _ = sim(demo(), rows)                              # the 09:55 bar closes at 100.70; 10:00 is past
        self.assertEqual((res["outcome"], res["exit_price"], res["exit_time"]), ("flat", 100.7, "10:00"))

    def test_a_missing_bar_before_the_exit_leaves_it_open(self):
        res, why = sim(demo(), [QUIET, QUIET, QUIET, None, (100, 101.5, 100, 101)])
        self.assertIsNone(res)
        self.assertIn("Yahoo has no 09:30 bar for DEMO on 2026-11-23: a gap before the exit", why)

    def test_a_hole_after_the_exit_does_not_matter(self):
        res, why = sim(demo(), [QUIET, QUIET, (100, 101.2, 99.9, 101), None, None])
        self.assertEqual(res["outcome"], "target")

    def test_no_bars_at_all(self):
        res, why = j.simulate(demo(), None)
        self.assertIsNone(res)
        self.assertEqual(why, "Yahoo has no 5-minute bars for DEMO on 2026-11-23")

    def test_a_bar_that_opens_under_the_stop_is_still_the_stop_with_a_note(self):
        res, _ = sim(demo(), [QUIET, QUIET, (98.2, 98.5, 98.0, 98.4)])
        self.assertEqual((res["outcome"], res["exit_price"]), ("stop", 99.0))
        self.assertIn("opened at %s98.20, under the stop" % RUPEE, res["notes"][0])

    def test_an_entry_inside_a_bar_counts_only_the_stop_in_that_bar(self):
        up = [QUIET, QUIET, (100, 101.5, 99.5, 100.2), (100.2, 101.1, 100, 101)]
        res, _ = sim(demo("9:27"), up)                          # the 09:25 high came before or after 09:27: unknown
        self.assertEqual((res["outcome"], res["exit_time"]), ("target", "09:30"))
        self.assertIn("inside the 09:25 bar", res["notes"][0])
        res, _ = sim(demo("9:27"), [QUIET, QUIET, (100, 100.2, 98.9, 99.5)])
        self.assertEqual((res["outcome"], res["exit_time"]), ("stop", "09:25"))

    def test_an_entry_far_from_its_bar_is_noted(self):
        res, _ = sim(demo(), [QUIET, (90, 91, 89, 90)] + [QUIET] * 7)
        self.assertIn("outside the 09:20 entry bar's range", res["notes"][0])

    def test_parse_drops_the_live_tick_the_unfinished_bar_and_bars_outside_the_session(self):
        doc = json.loads(chart([QUIET] * 4, first="09:05"))     # 09:05, 09:10 (pre-open), 09:15, 09:20
        r = doc["chart"]["result"][0]
        r["timestamp"].append(r["timestamp"][-1] + 137)         # a live tick off the grid
        for k in ("open", "high", "low", "close", "volume"):
            r["indicators"]["quote"][0][k].append(100.0)
        days = j.parse_bars(r, j.parse_now("2026-11-23 09:22"))
        self.assertEqual(sorted(days[DAY]), [0])                # 09:20 is not complete at 09:22
        days = j.parse_bars(r, NOW_UTC)
        self.assertEqual(sorted(days[DAY]), [0, 1])

    def test_a_chart_for_another_symbol_or_interval_is_refused(self):
        with self.assertRaises(j.JournalError):
            j.load_days(chart([QUIET], sym="OTHER.NS"), "DEMO", NOW_UTC)
        doc = json.loads(chart([QUIET]))
        doc["chart"]["result"][0]["meta"]["dataGranularity"] = "1m"
        with self.assertRaises(j.JournalError):
            j.load_days(json.dumps(doc), "DEMO", NOW_UTC)
        with self.assertRaises(j.JournalError):
            j.load_days('{"chart": {"result": null, "error": {"description": "No data found"}}}', "DEMO", NOW_UTC)


class MakeTrade(unittest.TestCase):
    def test_the_record(self):
        t = tcs()
        self.assertEqual(t, {"id": "TCS-2026-11-23-0945", "symbol": "TCS", "date": DAY, "side": "buy",
                             "entry_time": "09:45", "entry": 2443.5, "stop": 2419.35, "target": 2467.65,
                             "qty": 15, "flat_by": "15:00", "setup": "", "paper": True, "risk_per_share": 24.15,
                             "source": "flags", "status": "open"})

    def test_long_side_only(self):
        with self.assertRaises(j.JournalError) as cm:
            trade(stop=2467.65, target=2419.35)
        self.assertIn("long side only", str(cm.exception))
        with self.assertRaises(j.JournalError):
            trade(stop=2443.5)

    def test_whole_shares(self):
        self.assertEqual(trade(qty="15")["qty"], 15)
        for q in (0, -1, 1.5, "many", None):
            with self.assertRaises(j.JournalError):
                trade(qty=q)

    def test_a_closed_day_is_refused(self):
        with self.assertRaises(j.JournalError) as cm:
            trade(date="2026-11-24")
        self.assertIn("NSE holiday: Guru Nanak Jayanti", str(cm.exception))
        with self.assertRaises(j.JournalError):
            trade(date="2026-11-22")
        self.assertEqual(trade(date="2027-01-26")["date"], "2027-01-26")   # 2027's list is not published

    def test_times_inside_the_session(self):
        for et, fb in (("9:00 AM", "15:00"), ("15:30", "15:30"), ("9:45 AM", "15:45"), ("3:00 PM", "15:00"),
                       ("14:00", "13:00")):
            with self.assertRaises(j.JournalError):
                trade(entry_time=et, flat_by=fb)
        self.assertEqual(trade(flat_by="15:10")["flat_by"], "15:10")


class Flow(unittest.TestCase):
    """The commands, in a temp folder shaped like trader/."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.journal = os.path.join(self.d, "journal.json")
        self.cfg = os.path.join(self.d, "config.json")
        shutil.copy(CFG_PATH, self.cfg)
        self.plan = os.path.join(self.d, "plan.json")
        with open(self.plan, "w", encoding="utf-8") as fh:
            json.dump(TCS_PLAN, fh)
        self.orders = os.path.join(self.d, "orders.json")
        shutil.copy(ORDERS_PATH, self.orders)
        self.state = os.path.join(self.d, "state", "broker.json")
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.pf = os.path.join(self.d, "portfolio.json")
        self._get = j._get
        j._get = self.no_network
        self._sleep = j._sleep
        j._sleep = lambda s: None

    def tearDown(self):
        j._get = self._get
        j._sleep = self._sleep
        shutil.rmtree(self.d)

    def no_network(self, url, timeout=20):
        raise AssertionError("the network was used: %s" % url)

    def base(self, now=AFTER):
        return ["--journal", self.journal, "--config", self.cfg, "--now", now]

    def add(self, *extra, **kw):
        return run(["add", "--plan", self.plan, "--orders", self.orders] + self.base(kw.get("now", "2026-11-23 09:48"))
                   + list(extra))

    def add_infy(self):
        return self.add("--symbol", "INFY", "--date", DAY, "--entry", "1123.00", "--entry-time", "9:50 AM",
                        "--stop", "1114.10", "--target", "1140.80", "--qty", "33", "--setup", "orb")

    def score(self, *extra, **kw):
        return run(["score", "--state", self.state, "--offline", kw.get("offline", DIR)]
                   + self.base(kw.get("now", AFTER)) + list(extra))

    def doc(self):
        with open(self.journal, encoding="utf-8") as fh:
            return json.load(fh)

    def broker(self, *argv, **kw):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = ticket.main(list(argv) + ["--config", self.cfg, "--state", self.state,
                                             "--now", kw.get("now", "2026-11-23 09:47")])
        return code, out.getvalue()

    def place(self, fill=None):
        """trader-risk passes the TCS order, the user says yes, then reports it placed (really run)."""
        with redirect_stdout(io.StringIO()):
            code = check.main(["--orders", self.orders, "--config", self.cfg, "--portfolio", self.pf,
                               "--prices", os.path.join(self.d, "none"), "--state", self.state,
                               "--now", "2026-11-23 09:46", "--out", self.risk])
        self.assertEqual(code, 0)
        both = ["--orders", self.orders, "--risk", self.risk]
        self.assertEqual(self.broker("approve", "--index", "1", "--reply", "yes", *both)[0], 0)
        extra = ["--filled-qty", "15", "--filled-price", fill] if fill else []
        code, text = self.broker("result", "--index", "1", "--status", "placed", "--portfolio", self.pf,
                                 *(both + extra), now="2026-11-23 09:48")
        self.assertEqual(code, 0, text)

    # ---- add

    def test_add_from_the_plan_and_the_orders(self):
        code, text, _ = self.add()
        self.assertEqual(code, 0)
        self.assertIn("Journaled: TCS 2026-11-23 practice: bought 15 at %s2,443.50 at 09:45, stop %s2,419.35, "
                      "target %s2,467.65, flat by 15:00." % (RUPEE, RUPEE, RUPEE), text)
        self.assertIn("Nothing was placed.", text)
        doc = self.doc()
        self.assertEqual(doc["created_by"], "trader-journal")
        t = doc["trades"][0]
        self.assertEqual((t["symbol"], t["date"], t["entry_time"], t["entry"], t["stop"], t["target"], t["qty"],
                          t["flat_by"], t["setup"], t["paper"], t["status"], t["source"]),
                         ("TCS", DAY, "09:45", 2443.5, 2419.35, 2467.65, 15, "15:00", "orb", True, "open", "plan"))
        self.assertEqual(t["added_at"], "2026-11-23T04:18:00Z")

    def test_add_never_duplicates(self):
        self.add()
        code, text, _ = self.add()
        self.assertEqual(code, 0)
        self.assertIn("Already in the journal", text)
        self.assertEqual(len(self.doc()["trades"]), 1)

    def test_add_from_flags(self):
        code, text, _ = self.add_infy()
        self.assertEqual(code, 0, text)
        t = self.doc()["trades"][0]
        self.assertEqual((t["symbol"], t["entry_time"], t["qty"], t["flat_by"], t["source"], t["paper"]),
                         ("INFY", "09:50", 33, "15:00", "flags", True))
        code, _, err = self.add("--symbol", "INFY", "--entry", "1123")
        self.assertEqual(code, 1)
        self.assertIn("with --symbol also give --entry-time, --stop, --target, --qty", err)

    def test_add_real_and_flag_overrides(self):
        code, text, _ = self.add("--real", "--qty", "10", "--entry", "2444.00")
        self.assertEqual(code, 0)
        t = self.doc()["trades"][0]
        self.assertEqual((t["paper"], t["qty"], t["entry"]), (False, 10, 2444.0))
        self.assertIn("TCS 2026-11-23 real: bought 10", text)

    def test_add_uses_the_price_the_order_was_sized_at(self):
        with open(self.orders, encoding="utf-8") as fh:
            o = json.load(fh)
        o["orders"][0]["plan"]["entry"] = 2446.0
        with open(self.orders, "w", encoding="utf-8") as fh:
            json.dump(o, fh)
        code, text, _ = self.add()
        self.assertEqual(self.doc()["trades"][0]["entry"], 2446.0)
        self.assertIn("the price trader-budget sized the order at; the plan said %s2,443.50" % RUPEE, text)

    def test_add_needs_a_plan_and_a_quantity(self):
        os.remove(self.orders)
        code, _, err = self.add()
        self.assertEqual(code, 1)
        self.assertIn("no quantity", err)
        self.assertEqual(self.add("--qty", "15")[0], 0)
        os.remove(self.plan)
        code, _, err = self.add()
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)

    def test_add_refuses_a_holiday(self):
        code, _, err = self.add("--date", "2026-11-24")
        self.assertEqual(code, 1)
        self.assertIn("NSE was closed on 2026-11-24 (NSE holiday: Guru Nanak Jayanti)", err)
        self.assertFalse(os.path.exists(self.journal))

    # ---- score

    def test_score_the_real_tcs_win(self):
        self.add()
        code, text, _ = self.score()
        self.assertEqual(code, 0)
        self.assertIn("TARGET, simulated: the 10:05 bar reached the target before any bar touched the stop.", text)
        self.assertIn("Out at %s2,467.65: +1.00R, gross +%s362.25, charges %s60.38, net +%s301.87."
                      % (RUPEE, RUPEE, RUPEE, RUPEE), text)
        self.assertIn("1 scored, 0 left open.", text)
        self.assertIn("a real fill can be worse", text)
        self.assertIn("not financial advice", text)
        t = self.doc()["trades"][0]
        r = t["result"]
        self.assertEqual((t["status"], r["basis"], r["outcome"], r["exit_price"], r["exit_time"], r["r"],
                          r["gross"], r["net"], r["data"]),
                         ("scored", "simulated", "target", 2467.65, "10:05", 1.0, 362.25, 301.87, "offline"))
        self.assertEqual(r["charges"]["total"], 60.38)

    def test_score_the_real_infy_loss(self):
        self.add_infy()
        code, text, _ = self.score()
        self.assertIn("STOP, simulated: the 10:55 bar touched the stop first.", text)
        self.assertIn("Out at %s1,114.10: -1.00R, gross -%s293.70, charges %s60.34, net -%s354.04."
                      % (RUPEE, RUPEE, RUPEE, RUPEE), text)

    def test_a_day_that_is_not_over_is_left_open(self):
        self.add()
        code, text, _ = self.score(now="2026-11-23 15:29")
        self.assertEqual(code, 0)
        self.assertIn("LEFT OPEN: the session is not over yet (NSE closes at 15:30 India time on 2026-11-23).",
                      text)
        self.assertIn("0 scored, 1 left open.", text)
        t = self.doc()["trades"][0]
        self.assertEqual(t["status"], "open")
        self.assertIn("not over yet", t["open_reason"])
        self.assertNotIn("result", t)
        self.score(now="2026-11-23 15:30")
        t = self.doc()["trades"][0]
        self.assertEqual(t["status"], "scored")
        self.assertNotIn("open_reason", t)

    def test_a_missing_bar_day_is_left_open(self):
        doc = json.loads(read(TCS))
        r = doc["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        i = next(n for n, ts in enumerate(r["timestamp"]) if j.to_ist(j.utc_of(ts)).strftime("%H:%M") == "09:55")
        for k in q:
            q[k][i] = None                                      # a hole before the 10:05 target bar
        off = os.path.join(self.d, "charts")
        os.makedirs(off)
        with open(os.path.join(off, "TCS-2026-11-23-5m.json"), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        self.add()
        code, text, _ = self.score(offline=off)
        self.assertEqual(code, 0)
        self.assertIn("LEFT OPEN: Yahoo has no 09:55 bar for TCS on 2026-11-23: a gap before the exit", text)
        self.assertEqual(self.doc()["trades"][0]["status"], "open")
        code, text, _ = self.score()                            # the whole chart scores it later
        self.assertIn("1 scored, 0 left open.", text)

    def test_no_saved_chart_is_left_open_with_the_reason(self):
        self.add("--symbol", "SBIN", "--date", DAY, "--entry", "1170", "--entry-time", "9:45 AM", "--stop",
                 "1160", "--target", "1180", "--qty", "5")
        code, text, _ = self.score()
        self.assertIn("LEFT OPEN: no saved chart for SBIN", text)

    def test_scored_trades_are_not_scored_twice(self):
        self.add()
        self.score()
        code, text, _ = self.score()
        self.assertIn("Nothing to score", text)
        code, text, _ = self.score("--again")
        self.assertIn("1 scored, 0 left open.", text)

    def test_one_trade_by_symbol_and_date(self):
        self.add()
        self.add_infy()
        code, text, _ = self.score("--symbol", "INFY", "--date", DAY)
        self.assertIn("1 scored, 0 left open.", text)
        st = {t["symbol"]: t["status"] for t in self.doc()["trades"]}
        self.assertEqual(st, {"TCS": "open", "INFY": "scored"})
        code, _, err = self.score("--symbol", "SBIN")
        self.assertEqual(code, 1)
        self.assertIn("no journal trade matches SBIN", err)

    def test_a_reported_exit_is_used_as_filled(self):
        self.add()
        code, text, _ = self.score("--symbol", "TCS", "--date", DAY, "--exit-price", "2465.00", "--exit-time",
                                   "10:06", now="2026-11-23 10:10")
        self.assertEqual(code, 0)
        self.assertIn("FLAT, as filled: the exit you reported at 10:06, between the stop and the target.", text)
        self.assertNotIn("Simulated =", text)
        r = self.doc()["trades"][0]["result"]
        self.assertEqual((r["basis"], r["outcome"], r["exit_price"], r["exit_time"], r["r"], r["gross"]),
                         ("as filled", "flat", 2465.0, "10:06", 0.89, 322.5))
        self.score("--symbol", "TCS", "--exit-price", "2410.00")   # a stop that slipped: worse than -1R
        r = self.doc()["trades"][0]["result"]
        self.assertEqual((r["outcome"], r["r"], r["gross"]), ("stop", -1.39, -502.5))

    def test_auto_square_off_is_charged_only_when_the_broker_did_it(self):
        self.add()
        self.score("--symbol", "TCS", "--exit-price", "2453.20", "--exit-time", "15:15", "--auto-squareoff")
        r = self.doc()["trades"][0]["result"]
        self.assertEqual((r["auto_squareoff"], r["charges"]["auto_squareoff"]), (True, 50.0))
        plain = j.round_trip(2443.5, 2453.2, 15)["total"]
        self.assertEqual(Decimal(str(r["charges"]["total"])) - plain, Decimal("59.00"))
        code, _, err = self.score("--auto-squareoff")
        self.assertEqual(code, 1)
        self.assertIn("go with --exit-price", err)

    def test_exit_price_wants_exactly_one_trade(self):
        self.add()
        self.add_infy()
        code, _, err = self.score("--exit-price", "2465")
        self.assertEqual(code, 1)
        self.assertIn("--exit-price is for one trade: 2 match", err)

    def test_score_fetches_yahoo_once_a_symbol(self):
        self.add()
        calls = []

        def fake(url, timeout=20):
            calls.append(url)
            return read(TCS)
        j._get = fake
        code, text, _ = run(["score", "--state", self.state] + self.base("2026-11-24 10:00"))
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["https://query2.finance.yahoo.com/v8/finance/chart/TCS.NS?range=5d&interval=5m"])
        self.assertEqual(self.doc()["trades"][0]["result"]["data"], "yahoo")

    def test_a_day_too_far_back_is_left_open(self):
        self.add()
        code, text, _ = run(["score", "--state", self.state] + self.base("2027-02-01 10:00"))
        self.assertIn("more than 58 days back", text)
        self.assertEqual((j.range_for(0), j.range_for(4), j.range_for(5), j.range_for(25), j.range_for(26),
                          j.range_for(58), j.range_for(59)), ("5d", "5d", "1mo", "1mo", "60d", "60d", None))

    def test_a_yahoo_failure_leaves_it_open(self):
        self.add()

        def down(url, timeout=20):
            raise OSError("no route")
        j._get = down
        code, text, _ = run(["score", "--state", self.state] + self.base("2026-11-24 10:00"))
        self.assertEqual(code, 0)
        self.assertIn("LEFT OPEN: could not reach Yahoo", text)

    def test_an_empty_journal(self):
        code, text, _ = self.score()
        self.assertEqual(code, 1)
        self.assertIn("is empty", text)

    # ---- import

    def test_the_real_broker_log_shape(self):
        self.place(fill="2444.00")
        with open(self.state, encoding="utf-8") as fh:
            rows = json.load(fh)["orders"]
        self.assertEqual([r["status"] for r in rows], ["approved", "placed", "filled"])
        placed = rows[1]
        self.assertEqual(sorted(placed), sorted(["at", "date", "index", "fingerprint", "symbol", "side", "plain",
                                                 "status", "reported_by", "paper", "qty", "kind", "max_loss",
                                                 "stop"]))
        self.assertEqual((placed["at"], placed["qty"], placed["kind"], placed["stop"], placed["max_loss"]),
                         ("2026-11-23T09:48:00+05:30", "15", "entry", 2419.35, 362.25))
        self.assertIn("target 2,467.65", placed["plain"])
        with open(self.orders, encoding="utf-8") as fh:
            self.assertEqual(j.fingerprint(json.load(fh)["orders"][0]), placed["fingerprint"])

    def test_import_a_placed_entry_with_its_fill(self):
        self.place(fill="2444.00")
        code, text, _ = run(["import", "--state", self.state, "--orders", self.orders] + self.base())
        self.assertEqual(code, 0)
        self.assertIn("Imported: TCS 2026-11-23 practice: bought 15 at %s2,444.00 at 09:45" % RUPEE, text)
        self.assertIn("(entry from the buy fill you reported)", text)
        self.assertIn("1 imported, 0 already in the journal, 0 skipped.", text)
        t = self.doc()["trades"][0]
        self.assertEqual((t["entry"], t["stop"], t["target"], t["qty"], t["entry_time"], t["flat_by"], t["setup"],
                          t["paper"], t["source"]),
                         (2444.0, 2419.35, 2467.65, 15, "09:45", "15:00", "orb", True, "trader-broker log"))
        self.assertEqual(t["broker"]["at"], "2026-11-23T09:48:00+05:30")

    def test_import_never_duplicates(self):
        self.place()
        run(["import", "--state", self.state, "--orders", self.orders] + self.base())
        code, text, _ = run(["import", "--state", self.state, "--orders", self.orders] + self.base())
        self.assertIn("0 imported, 1 already in the journal, 0 skipped. Nothing written.", text)
        self.assertEqual(len(self.doc()["trades"]), 1)

    def test_import_skips_what_add_already_wrote(self):
        self.add()                                              # same symbol, date and entry time
        self.place()
        code, text, _ = run(["import", "--state", self.state, "--orders", self.orders] + self.base())
        self.assertIn("0 imported, 1 already in the journal", text)
        self.assertEqual(len(self.doc()["trades"]), 1)

    def test_import_without_the_orders_file_reads_the_log_line_alone(self):
        self.place()
        os.remove(self.orders)
        code, text, _ = run(["import", "--state", self.state, "--orders", self.orders] + self.base())
        t = self.doc()["trades"][0]
        # entry = stop + max loss / qty = 2419.35 + 362.25 / 15; the time is when the placement was reported
        self.assertEqual((t["entry"], t["target"], t["qty"], t["entry_time"], t["flat_by"]),
                         (2443.5, 2467.65, 15, "09:48", "15:00"))
        self.assertEqual(t["entry_from"], "the log's stop + max loss / qty")
        code, text, _ = run(["import", "--state", self.state, "--orders", self.orders] + self.base())
        self.assertIn("0 imported, 1 already in the journal", text)

    def test_only_placed_entries_are_imported(self):
        with redirect_stdout(io.StringIO()):
            check.main(["--orders", self.orders, "--config", self.cfg, "--portfolio", self.pf, "--prices",
                        os.path.join(self.d, "none"), "--state", self.state, "--now", "2026-11-23 09:46",
                        "--out", self.risk])
        both = ["--orders", self.orders, "--risk", self.risk]
        self.broker("approve", "--index", "1", "--reply", "yes", *both)
        code, text, _ = run(["import", "--state", self.state, "--orders", self.orders] + self.base())
        self.assertIn("0 imported, 0 already in the journal, 0 skipped.", text)   # a yes is not a placement
        self.broker("result", "--index", "1", "--status", "not-placed", *both, now="2026-11-23 09:48")
        code, text, _ = run(["import", "--state", self.state, "--orders", self.orders] + self.base())
        self.assertIn("0 imported", text)
        self.assertFalse(os.path.exists(self.journal))

    def test_import_needs_the_log(self):
        code, _, err = run(["import", "--state", self.state, "--orders", self.orders] + self.base())
        self.assertEqual(code, 1)
        self.assertIn("no broker log", err)

    def test_a_sell_fill_in_the_broker_log_is_the_exit(self):
        self.place(fill="2444.00")
        run(["import", "--state", self.state, "--orders", self.orders] + self.base())
        code, text = self.broker("fill", "--symbol", "TCS", "--side", "sell", "--qty", "15", "--price", "2466.00",
                                 "--portfolio", self.pf, now="2026-11-23 10:07")
        self.assertEqual(code, 0, text)
        code, text, _ = self.score(now="2026-11-23 10:08")      # before the close: a real fill needs no bars
        self.assertIn("FLAT, as filled: the exit you reported at 10:07", text)
        r = self.doc()["trades"][0]["result"]
        self.assertEqual((r["basis"], r["exit_price"], r["exit_time"], r["gross"], r["data"]),
                         ("as filled", 2466.0, "10:07", 330.0, "broker log"))

    def test_a_partial_sell_fill_is_not_used(self):
        self.place(fill="2444.00")
        run(["import", "--state", self.state, "--orders", self.orders] + self.base())
        self.broker("fill", "--symbol", "TCS", "--side", "sell", "--qty", "5", "--price", "2466.00",
                    "--portfolio", self.pf, now="2026-11-23 10:07")
        code, text, _ = self.score(now="2026-11-23 10:08")
        self.assertIn("LEFT OPEN", text)
        self.assertIn("sell fills for 5 of the 15 shares: not used", text)

    # ---- charges, list, report

    def test_charges_for_one_round_trip(self):
        code, text, _ = run(["charges", "--buy", "2443.50", "--sell", "2467.65", "--qty", "15", "--config",
                             self.cfg])
        self.assertEqual(code, 0)
        for label, amount in (("Brokerage", "40.00"), ("STT", "9.25"), ("Stamp duty", "1.10"),
                              ("Exchange (NSE)", "2.26"), ("SEBI turnover", "0.07"), ("IPFT (NSE)", "0.07"),
                              ("GST", "7.63"), ("DP charges", "0.00"), ("Total charges", "60.38"),
                              ("Gross P&L", "362.25"), ("Net P&L", "301.87")):
            self.assertIn("  %-18s %12s" % (label, RUPEE + amount), text)
        self.assertIn("charges took 16.7 % of your gross profit", text)
        self.assertIn("sell at %s2,447.52 or higher: the price has to rise %s4.02 a share" % (RUPEE, RUPEE), text)
        self.assertIn("groww.in/pricing", text)
        self.assertIn("contract note can differ by a few paise", text)
        code, _, err = run(["charges", "--buy", "2443.50", "--config", self.cfg])
        self.assertEqual(code, 1)

    def test_charges_json(self):
        code, text, _ = run(["charges", "--buy", "100", "--sell", "101", "--qty", "100", "--json", "--config",
                             self.cfg])
        out = json.loads(text)
        self.assertEqual((out["brokerage"], out["total"], out["net"], out["overridden"]), (20.1, 27.33, 72.67, []))

    def test_charges_on_the_plan(self):
        code, text, _ = run(["charges", "--plan", "--plan-file", self.plan, "--orders", self.orders, "--config",
                             self.cfg])
        self.assertEqual(code, 0)
        self.assertIn("If the target hits  gross +%s362.25, charges %s60.38, net +%s301.87"
                      % (RUPEE, RUPEE, RUPEE), text)
        self.assertIn("If the stop hits    gross -%s362.25, charges %s60.18, net -%s422.43"
                      % (RUPEE, RUPEE, RUPEE), text)
        self.assertIn("1.00 before charges, 0.71 after charges", text)
        self.assertIn("Break-even          %s2,447.52: the price has to rise %s4.02 a share" % (RUPEE, RUPEE), text)
        code, text, _ = run(["charges", "--plan", "--plan-file", self.plan, "--orders", self.orders, "--config",
                             self.cfg, "--json", "--auto-squareoff"])
        out = json.loads(text)
        self.assertEqual((out["qty"], out["net_reward_risk"], out["target_hit"]["auto_squareoff"]), (15, 0.5, 50.0))

    def test_list(self):
        code, text, _ = run(["list"] + self.base())
        self.assertEqual(code, 1)
        self.add()
        self.add_infy()
        self.score("--symbol", "TCS")
        code, text, _ = run(["list"] + self.base())
        self.assertEqual(code, 0)
        self.assertIn("target (simulated) out at %s2,467.65, +1.00R, net +%s301.87" % (RUPEE, RUPEE), text)
        self.assertRegex(text, r"2026-11-23 09:50 INFY +33 +1,123.00 +1,114.10 +1,140.80 +practice open")
        self.assertIn("2 trades, 1 open.", text)

    def test_the_scoreboard_of_the_two_real_trades(self):
        self.add()
        self.add_infy()
        self.score()
        code, text, _ = run(["report"] + self.base())
        self.assertEqual(code, 0)
        for line in ("Practice Journal scoreboard: 2 scored trades, 2026-11-23 to 2026-11-23",
                     "2 practice, 0 real. Exits: 2 simulated, 0 as filled. Still open: 0.",
                     "Outcomes            1 target, 1 stop, 0 flat",
                     "After charges       1 win, 1 loss (a win is a net P&L above zero)",
                     "Win rate            50.0 %",
                     "Average R           +0.00R before charges, -0.19R after",
                     "Gross P&L           +%s68.55" % RUPEE,
                     "Total charges       %s120.72" % RUPEE,
                     "Net P&L             -%s52.17" % RUPEE,
                     "Largest loss        -%s354.04 (INFY 2026-11-23, entry 09:50)" % RUPEE,
                     "Longest losing run  1 trade in a row",
                     "Charges vs gross    charges took 176.1 % of your gross profit",
                     "Only 2 scored trades: too few to conclude anything.",
                     "Most people who day trade lose money.", "not financial advice"):
            self.assertIn(line, text)
        self.assertNotIn("profitable", text)
        code, text, _ = run(["report", "--json"] + self.base())
        sb = json.loads(text)
        self.assertEqual((sb["scored"], sb["enough"], sb["net"], sb["by_symbol"]["TCS"]["net"]),
                         (2, False, -52.17, 301.87))
        code, text, _ = run(["report", "--since", "2026-11-24"] + self.base())
        self.assertIn("no scored trades since 2026-11-24", text)

    def test_nothing_is_written_outside_the_journal(self):
        before = sorted(os.listdir(self.d))
        self.add()
        self.score()
        run(["report"] + self.base())
        run(["list"] + self.base())
        self.assertEqual(sorted(os.listdir(self.d)), sorted(before + ["journal.json"]))


def scored(n, date, entry_time, outcome, exit_price, sym="TCS", setup="orb", paper=True, basis="simulated"):
    t = trade(sym, entry_time, 100.0, 98.0, 104.0, 50, date=date, setup=setup, paper=paper)
    return j.settle(t, exit_price, "10:00", outcome, basis, j.load_charges()[0], NOW_UTC)


class Scoreboard(unittest.TestCase):
    def rows(self):
        # 50 shares, entry 100, stop 98, target 104: +2R = +200 gross, -1R = -100 gross
        return [scored(1, "2026-11-16", "9:45", "target", 104.0),
                scored(2, "2026-11-17", "9:45", "stop", 98.0, sym="INFY"),
                scored(3, "2026-11-18", "9:45", "stop", 98.0, sym="INFY", setup="level"),
                scored(4, "2026-11-19", "9:45", "stop", 98.0),
                scored(5, "2026-11-20", "9:45", "flat", 100.1),             # +5 gross: a loss after charges
                scored(6, "2026-11-23", "9:45", "flat", 101.0, paper=False, basis="as filled"),
                trade("SBIN", "9:45", 100.0, 98.0, 104.0, 50)]             # still open

    def test_the_aggregates(self):
        rows = self.rows()
        sb = j.scoreboard(rows)
        costs = [Decimal(str(t["result"]["charges"]["total"])) for t in rows[:6]]
        self.assertEqual((sb["scored"], sb["open"], sb["enough"]), (6, 1, False))
        self.assertEqual((sb["target"], sb["stop"], sb["flat"]), (1, 3, 2))
        self.assertEqual((sb["wins"], sb["losses"], sb["scratch"]), (2, 4, 0))
        self.assertEqual(sb["win_rate"], 33.3)
        self.assertEqual(sb["total_r"], -0.45)                  # 2 - 1 - 1 - 1 + 0.05 + 0.5
        self.assertEqual(sb["avg_r"], -0.08)                    # -0.45 / 6 = -0.075, half up away from zero
        self.assertEqual(sb["gross"], -45.0)                    # 200 - 300 + 5 + 50
        self.assertEqual(sb["charges"], float(sum(costs)))
        self.assertEqual(sb["net"], float(Decimal("-45.00") - sum(costs)))
        self.assertEqual(sb["longest_losing_run"], 4)           # three stops, then the flat that lost to charges
        self.assertEqual((sb["practice"], sb["real"], sb["simulated"], sb["as_filled"]), (5, 1, 5, 1))
        self.assertEqual((sb["from"], sb["to"]), ("2026-11-16", "2026-11-23"))
        worst = sb["largest_loss"]
        self.assertEqual(worst["net"], min(t["result"]["net"] for t in rows[:6]))
        self.assertEqual(worst["symbol"], "INFY")
        self.assertIn("charges added", sb["charges_vs_gross"])

    def test_a_small_gross_win_can_be_a_net_loss(self):
        t = self.rows()[4]
        self.assertEqual(t["result"]["gross"], 5.0)
        self.assertLess(t["result"]["net"], 0)

    def test_by_setup_and_by_symbol(self):
        sb = j.scoreboard(self.rows())
        self.assertEqual({k: v["trades"] for k, v in sb["by_setup"].items()}, {"level": 1, "orb": 5})
        self.assertEqual({k: v["trades"] for k, v in sb["by_symbol"].items()}, {"INFY": 2, "TCS": 4})
        self.assertEqual((sb["by_symbol"]["INFY"]["wins"], sb["by_symbol"]["INFY"]["losses"],
                          sb["by_symbol"]["INFY"]["total_r"], sb["by_symbol"]["INFY"]["gross"]), (0, 2, -2.0, -200.0))
        self.assertEqual(round(sum(v["net"] for v in sb["by_symbol"].values()), 2), sb["net"])

    def test_since_and_only(self):
        rows = self.rows()
        sb = j.scoreboard(rows, since="2026-11-20")
        self.assertEqual((sb["scored"], sb["flat"], sb["from"]), (2, 2, "2026-11-20"))
        self.assertEqual(j.scoreboard(rows, only="real")["scored"], 1)
        self.assertEqual(j.scoreboard(rows, only="practice")["scored"], 5)

    def test_charges_share_of_a_gross_profit(self):
        sb = j.scoreboard(self.rows()[:1])
        self.assertRegex(sb["charges_vs_gross"], r"^charges took \d+\.\d % of your gross profit$")
        self.assertIsNone(sb["largest_loss"])
        self.assertEqual(sb["longest_losing_run"], 0)

    def test_under_twenty_trades_says_so(self):
        text = "\n".join(j.scoreboard_lines(j.scoreboard(self.rows())))
        self.assertIn("Only 6 scored trades: too few to conclude anything.", text)
        self.assertIn("By setup:", text)
        self.assertIn("Largest loss", text)
        nineteen = [scored(i, "2026-10-%02d" % d, "9:45", "target", 104.0)
                    for i, d in enumerate([1, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 19, 21, 22, 23, 26, 27, 28, 29])]
        self.assertIn("Only 19 scored trades", "\n".join(j.scoreboard_lines(j.scoreboard(nineteen))))
        twenty = nineteen + [scored(20, "2026-10-30", "9:45", "stop", 98.0)]
        sb = j.scoreboard(twenty)
        text = "\n".join(j.scoreboard_lines(sb))
        self.assertTrue(sb["enough"])
        self.assertNotIn("too few", text)
        self.assertEqual((sb["wins"], sb["losses"], sb["win_rate"]), (19, 1, 95.0))
        self.assertNotIn("profitable", text)
        self.assertIn("No prediction".lower(), text.lower())

    def test_an_empty_scoreboard(self):
        sb = j.scoreboard([trade()])
        self.assertEqual((sb["scored"], sb["open"]), (0, 1))
        text = "\n".join(j.scoreboard_lines(sb))
        self.assertIn("no scored trades", text)
        self.assertIn("1 trade still open", text)
        self.assertIn("Only 0 scored trades: too few to conclude anything.", text)


class Skill(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-journal", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        fm = text.split("---\n")[1]
        self.assertIn("name: trader-journal\n", fm)
        self.assertIn("argument-hint:", fm)
        self.assertGreater(len(fm), 200)
        self.assertGreater(len(text.splitlines()), 60)
        low = " ".join(text.lower().split())
        for phrase in ("how am i doing", "score my trades", "journal this trade", "practice results",
                       "what did that trade make", "how much are the charges", "what would the charges be",
                       "is this worth it after charges", "show my scoreboard", "not financial advice",
                       "explicit yes", "groww.in/pricing", "2026-11-24", "a real fill can be worse",
                       "what it never does", "contract note"):
            self.assertIn(phrase, low, phrase)
        self.assertNotIn(chr(0x2014), text)
        self.assertNotRegex(text, r"\$\d")

    def test_the_example_numbers_in_skill_md_are_the_tools(self):
        with open(os.path.join(ROOT, "trader-journal", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        rates = j.load_charges()[0]
        t = j.settle(dict(tcs(), setup="orb"), 2467.65, "10:05", "target", "simulated", rates, NOW_UTC)
        i = j.settle(dict(infy(), setup="orb"), 1114.1, "10:55", "stop", "simulated", rates, NOW_UTC)
        for t_ in (t, i):
            for line in j.result_lines(t_):
                self.assertIn(line, text)
        for line in j.charges_report(j.round_trip(2443.50, 2467.65, 15), rates):
            self.assertIn(line, text)
        for line in j.scoreboard_lines(j.scoreboard([t, i])):
            self.assertIn(line, text)


if __name__ == "__main__":
    unittest.main()
