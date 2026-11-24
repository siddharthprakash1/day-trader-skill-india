import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

size = load("trader-budget", "size")

CFG = os.path.join(ROOT, "trader-budget", "config.example.json")
PLAN = os.path.join(FIX, "trader-budget", "plan-tcs.json")
BARS = os.path.join(FIX, "trader-budget", "tcs-5m-2026-11-23.json")
ORDERS = os.path.join(FIX, "trader-risk", "orders-tcs.json")
NOW = "2026-11-23 09:46"
TODAY = "2026-11-23"
RUPEE = "₹"
# the port spec's default config, key for key
SPEC_CFG = {
    "budget": 50000, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 40000,
    "min_order_inr": 500, "max_loss_per_trade_inr": 500, "max_daily_loss_inr": 1500,
    "max_trades_per_day": 3, "max_orders_per_day": 10, "min_reward_risk": 1.0,
    "require_stop": True, "no_entries_before": "09:30", "no_entries_after": "14:30",
    "flat_by": "15:00", "broker": "groww", "paper": True, "blocked": [],
    "allow_options": False, "allow_margin": False, "allow_short": False,
    "circuit_breaker": True, "loss_streak_limit": 3, "loss_streak_cooldown_minutes": None,
    "reentry_cooldown_minutes": 30, "block_reentry_after_stop": True, "max_weekly_loss_inr": None,
}


def cfg(**over):
    with open(CFG, encoding="utf-8") as fh:
        c = json.load(fh)
    c.update(over)
    return c


def plan(**over):
    with open(PLAN, encoding="utf-8") as fh:
        p = json.load(fh)
    p.update(over)
    return p


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


def prices_doc(price, quote_time="2026-11-23T04:25:00Z"):
    return {"as_of": quote_time, "source": "yahoo", "prices": {"TCS": price},
            "detail": {"TCS": {"prev_close": 2376.2, "time": quote_time, "source": "yahoo"}}}


class ThePlanIsRealData(unittest.TestCase):
    """The hand-made plan fixture really is Monday 2026-11-23's TCS opening-range breakout."""

    def test_plan_matches_the_saved_yahoo_bars(self):
        with open(BARS, encoding="utf-8") as fh:
            res = json.load(fh)["chart"]["result"][0]
        self.assertEqual((res["meta"]["symbol"], res["meta"]["currency"]), ("TCS.NS", "INR"))
        q = res["indicators"]["quote"][0]
        bars = []
        for i, ts in enumerate(res["timestamp"]):
            ist = size.to_ist(datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=ts))
            self.assertEqual(ist.date().isoformat(), TODAY)
            bars.append((ist.strftime("%H:%M"), q["high"][i], q["low"][i], q["close"][i]))
        self.assertEqual(len(bars), 75)                       # a full NSE session of 5-minute bars
        self.assertEqual((bars[0][0], bars[-1][0]), ("09:15", "15:25"))
        rng = [b for b in bars if b[0] in ("09:15", "09:20", "09:25")]
        high = size.round_paisa(max(b[1] for b in rng))
        low = size.round_paisa(min(b[2] for b in rng))
        self.assertEqual((high, low), (2433.7, 2405.0))
        first = next(b for b in bars if b[0] >= "09:30" and size.round_paisa(b[3]) > high)
        p = plan()
        self.assertEqual((first[0], size.round_paisa(first[3])), ("09:40", p["entry"]))
        self.assertEqual(p["stop"], 2419.35)                  # (2433.70 + 2405.00) / 2
        self.assertAlmostEqual(p["stop"], (high + low) / 2, places=6)
        self.assertEqual(p["target"], round(p["entry"] + (p["entry"] - p["stop"]), 2))
        self.assertEqual((p["date"], p["flat_by"], p["symbol"]), (TODAY, "15:00", "TCS"))


class TheExample(unittest.TestCase):
    """Rs 50,000, 10% buffer, Rs 500 max loss, TCS risk 24.15 -> 15 shares, Rs 36,652.50, max loss Rs 362.25."""

    def test_config_example_is_the_spec(self):
        with open(CFG, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), SPEC_CFG)
        self.assertEqual({k: v for k, v in size.DEFAULTS.items() if k != "budget"},
                         {k: v for k, v in SPEC_CFG.items() if k != "budget"})
        for gone in ("fractional", "pdt_guard", "sec_user_agent", "max_order_usd"):
            self.assertNotIn(gone, size.DEFAULTS)

    def test_tcs_is_15_shares_bound_by_the_position_cap(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        self.assertEqual(s["qty"], 15)
        self.assertEqual(s["bound_by"], "position cap")
        self.assertEqual(s["risk_per_share"], 24.15)
        self.assertEqual(s["cost"], 36652.5)
        self.assertEqual(s["max_loss"], 362.25)
        self.assertEqual(s["reward_risk"], 1.0)
        self.assertEqual(s["cash_buffer"], 5000)
        self.assertEqual([(l["name"], l["shares"]) for l in s["limits"]],
                         [("max loss", 20), ("position cap", 15), ("max order", 16), ("cash", 18)])
        self.assertEqual([l["name"] for l in s["limits"] if l["binds"]], ["position cap"])
        self.assertIsNone(s["refusal"])

    def test_the_bracket_order_record(self):
        o = size.bracket_order(size.size_trade(plan(), cfg(), today=TODAY))
        why = o.pop("why")
        self.assertEqual(o, {
            "symbol": "TCS", "side": "buy", "qty": "15", "type": "market", "time_in_force": "day",
            "order_class": "bracket", "take_profit_limit_price": "2467.65", "stop_loss_stop_price": "2419.35",
            "plan": {"entry": 2443.5, "stop": 2419.35, "target": 2467.65, "risk_per_share": 24.15,
                     "max_loss": 362.25, "setup": "orb", "entry_time": "9:45 AM", "flat_by": "15:00"}})
        self.assertIn("bound by your position cap", why)
        self.assertIn(RUPEE + "362.25", why)
        self.assertIn("before charges", why)
        self.assertNotIn("notional", o)

    def test_the_fixture_orders_file_is_what_it_writes(self):
        with open(ORDERS, encoding="utf-8") as fh:
            fixture = json.load(fh)["orders"][0]
        self.assertEqual(size.bracket_order(size.size_trade(plan(), cfg(), today=TODAY)), fixture)

    def test_a_limit_order(self):
        o = size.bracket_order(size.size_trade(plan(), cfg(), limit=2443.5, today=TODAY))
        self.assertEqual((o["type"], o["limit_price"], o["qty"]), ("limit", "2443.50", "15"))
        s = size.size_trade(plan(), cfg(), limit=2440.00, today=TODAY)
        self.assertEqual((s["risk_per_share"], s["qty"], s["max_loss"]), (20.65, 15, 309.75))
        self.assertEqual(size.bracket_order(s)["limit_price"], "2440.00")

    def test_cli_writes_one_bracket_order_in_rupees(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "trader", "orders.json")
            code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--write", out])
            self.assertEqual(code, 0)
            self.assertIn("Size: 15 shares of TCS = %s36,652.50 at 2,443.50" % RUPEE, text)
            self.assertIn("15 x 24.15 = %s362.25" % RUPEE, text)
            self.assertIn("75%% of %s50,000 = %s37,500 / 2,443.50" % (RUPEE, RUPEE), text)
            self.assertIn("<- binds", text)
            self.assertIn("Before brokerage, STT and other charges", text)
            self.assertIn("Intraday (MIS)", text)
            self.assertIn("Flat by 15:00 IST", text)
            self.assertIn("not financial advice", text)
            self.assertNotIn("$", text)
            self.assertNotIn("New York", text)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual(doc["status"], "proposed")
            self.assertIs(doc["paper"], True)
            self.assertEqual(doc["created_at"], "2026-11-23T04:16:00Z")    # 09:46 IST
            self.assertEqual(len(doc["orders"]), 1)
            self.assertEqual(doc["orders"][0]["plan"]["max_loss"], 362.25)
            with open(ORDERS, encoding="utf-8") as fh:
                self.assertEqual(doc, json.load(fh))

    def test_cli_json_and_limit(self):
        code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent", "--now", NOW,
                             "--limit", "--json"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(out["sizing"]["qty"], 15)
        self.assertEqual(out["orders"][0]["limit_price"], "2443.50")


class IndiaClockAndSymbols(unittest.TestCase):
    def test_ist_is_a_fixed_utc_plus_5_30(self):
        self.assertEqual(size.parse_now("2026-11-23 09:46"), datetime.datetime(2026, 11, 23, 4, 16))
        self.assertEqual(size.parse_now("2026-11-23 09:46 IST"), datetime.datetime(2026, 11, 23, 4, 16))
        self.assertEqual(size.parse_now("2026-11-23T04:16:00Z"), datetime.datetime(2026, 11, 23, 4, 16))
        for month in (1, 3, 6, 11):        # no daylight saving: the same offset all year
            utc = datetime.datetime(2026, month, 10, 3, 45)
            self.assertEqual(size.to_ist(utc), datetime.datetime(2026, month, 10, 9, 15))
            self.assertEqual(size.ist_to_utc(size.to_ist(utc)), utc)
        self.assertEqual(size.to_ist(datetime.datetime(2026, 11, 23, 19, 0)).date().isoformat(), "2026-11-24")
        for gone in ("to_eastern", "eastern_to_utc", "eastern_offset", "_nth_sunday"):
            self.assertFalse(hasattr(size, gone), gone)

    def test_the_india_date_decides_which_day_the_plan_is_for(self):
        # 20:00 UTC on the 22nd is already 01:30 on the 23rd in India
        code, text, err = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent",
                               "--now", "2026-11-22T20:00:00Z", "--json"])
        self.assertEqual(code, 0, err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent",
                            "--now", "2026-11-24 10:00"])
        self.assertEqual(code, 1)
        self.assertIn("today is 2026-11-24 in India", err)

    def test_nse_symbols(self):
        self.assertEqual(size.nse_symbol("tcs.ns"), "TCS")
        self.assertEqual(size.nse_symbol(" M&M "), "M&M")
        self.assertEqual(size.nse_symbol("BAJAJ-AUTO.NS"), "BAJAJ-AUTO")
        for sym, want in (("M&M", "M&M"), ("BAJAJ-AUTO", "BAJAJ-AUTO"), ("M&M.NS", "M&M"), ("tcs.ns", "TCS")):
            s = size.size_trade(plan(symbol=sym), cfg(), today=TODAY)
            self.assertEqual(s["symbol"], want)
            self.assertEqual(size.bracket_order(s)["symbol"], want)
        with self.assertRaises(size.SizeError):
            size.size_trade(plan(symbol="M&M.NS"), cfg(blocked=["m&m"]), today=TODAY)

    def test_money_is_rupees(self):
        self.assertEqual(size.money(50000), RUPEE + "50,000")
        self.assertEqual(size.money(362.25), RUPEE + "362.25")
        self.assertEqual(size.px(2419.35), "2,419.35")
        self.assertEqual(size._price(2419.35), "2419.35")
        self.assertEqual(size.round_paisa(2443.505), 2443.51)


class PlanRules(unittest.TestCase):
    def test_zero_shares_is_a_plain_refusal(self):
        s = size.size_trade(plan(stop=1900.0), cfg(), today=TODAY)
        self.assertEqual(s["qty"], 0)
        self.assertIn("more than your %s500 max loss per trade" % RUPEE, s["refusal"])
        self.assertIsNone(size.bracket_order(s))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "plan.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(plan(stop=1900.0), fh)
            out = os.path.join(d, "orders.json")
            code, text, _ = run(["--plan", p, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--write", out])
            self.assertEqual(code, 1)
            self.assertIn("No order:", text)
            self.assertFalse(os.path.exists(out))

    def test_each_limit_can_bind(self):
        s = size.size_trade(plan(), cfg(max_order_inr=10000), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (4, "max order"))
        s = size.size_trade(plan(), cfg(max_loss_per_trade_inr=100), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["max_loss"]), (4, "max loss", 96.6))
        s = size.size_trade(plan(), cfg(), cash=12000, today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["cash_available"]), (2, "cash", 7000))
        s = size.size_trade(plan(), cfg(max_position_pct=20), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (4, "position cap"))

    def test_whole_shares_only(self):
        for over in ({}, {"max_order_inr": 10000}, {"max_loss_per_trade_inr": 77}, {"budget": 33333}):
            s = size.size_trade(plan(), cfg(**over), today=TODAY)
            self.assertIsInstance(s["qty"], int)
            o = size.bracket_order(s)
            if o:
                self.assertRegex(o["qty"], r"^\d+$")
        one = size.size_trade(plan(), cfg(max_order_inr=2443.49), today=TODAY)
        self.assertEqual(one["qty"], 0)                        # a paisa short of one share: no fraction
        self.assertIn("No order", one["refusal"])

    def test_a_fresh_higher_price_means_a_bigger_risk(self):
        # 2455.30 is the real close of the 09:50 bar
        s = size.size_trade(plan(), cfg(min_reward_risk=0), price=2455.3, today=TODAY)
        self.assertEqual((s["entry"], s["entry_from"], s["risk_per_share"]), (2455.3, "price", 35.95))
        self.assertEqual((s["qty"], s["bound_by"], s["max_loss"], s["reward_risk"]), (13, "max loss", 467.35, 0.34))
        self.assertIsNone(s["refusal"])
        s = size.size_trade(plan(), cfg(), price=2455.3, today=TODAY)
        self.assertIn("reward:risk 0.34 (target 2,467.65, entry 2,455.30, stop 2,419.35) is under your minimum 1",
                      s["refusal"])
        self.assertIsNone(size.bracket_order(s))
        lower = size.size_trade(plan(), cfg(), price=2430.00, today=TODAY)
        self.assertEqual((lower["entry"], lower["qty"]), (2443.5, 15))

    def test_prices_file_is_used_only_when_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            pp = os.path.join(d, "prices.json")
            with open(pp, "w", encoding="utf-8") as fh:
                json.dump(prices_doc(2455.3), fh)
            base = ["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"), "--prices", pp, "--json"]
            code, text, _ = run(base + ["--now", "2026-11-23 09:56"])
            out = json.loads(text)
            self.assertEqual((code, out["sizing"]["qty"], out["orders"]), (1, 13, []))
            self.assertIn("above the plan's entry", out["notes"][0])
            self.assertIn("as of 09:55 IST", out["notes"][0])
            out = json.loads(run(base + ["--now", "2026-11-23 10:30"])[1])
            self.assertEqual(out["sizing"]["qty"], 15)
            self.assertIn("not used", out["notes"][0])

    def test_the_portfolio_cash_caps_the_size(self):
        with tempfile.TemporaryDirectory() as d:
            pf = os.path.join(d, "portfolio.json")
            with open(pf, "w", encoding="utf-8") as fh:
                json.dump({"cash": 12000, "positions": []}, fh)
            out = json.loads(run(["--plan", PLAN, "--config", CFG, "--portfolio", pf, "--now", NOW, "--json"])[1])
            self.assertEqual((out["sizing"]["qty"], out["sizing"]["bound_by"]), (2, "cash"))

    def test_bad_plans(self):
        for p, word in ((plan(stop=2450.0), "not below the entry"),
                        (plan(target=2440.0), "already happened"),
                        (plan(target=None), "positive target"),
                        (plan(side="sell"), "long (buy) intraday trades only"),
                        (plan(symbol=""), "no symbol")):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(p, cfg(), today=TODAY)
            self.assertIn(word, str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), price=2480.30, today=TODAY)   # the real 10:05 close: past the target
        self.assertIn("already happened", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(blocked=["tcs"]), today=TODAY)
        self.assertIn("blocked list", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), today="2026-11-25")
        self.assertIn("make a new plan", str(e.exception))

    def test_under_the_minimum_order(self):
        cheap = plan(symbol="IDEA", entry=10.0, stop=9.0, target=11.0)
        s = size.size_trade(cheap, cfg(max_loss_per_trade_inr=5), today=TODAY)
        self.assertEqual(s["qty"], 5)
        self.assertIn("under your %s500 minimum order" % RUPEE, s["refusal"])
        self.assertIsNone(size.bracket_order(s))

    def test_missing_plan_bad_clock_and_removed_weights_mode(self):
        code, _, err = run(["--plan", "/nonexistent/plan.json", "--config", CFG, "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", "tomorrow"])
        self.assertEqual(code, 1)
        self.assertIn("India time", err)
        self.assertNotIn("Traceback", err)
        for gone in ("size", "orders_from", "parse_weights", "whole_shares", "allocate"):
            self.assertFalse(hasattr(size, gone), gone)
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            size.main(["--weights", "TCS=50,INFY=50"])
        self.assertFalse(os.path.exists(os.path.join(ROOT, "trader-budget", "targets.example.json")))


class SkillExample(unittest.TestCase):
    """Every line of the SKILL.md example is real output of the tool on the fixture plan."""

    def test_the_skill_md_example_is_the_real_output(self):
        with open(os.path.join(ROOT, "trader-budget", "SKILL.md"), encoding="utf-8") as fh:
            skill = fh.read()
        code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent", "--now", NOW])
        self.assertEqual(code, 0)
        shown = [l for l in text.splitlines() if l.strip()][:11]
        self.assertEqual(len(shown), 11)
        for line in shown:
            self.assertIn(line + "\n", skill, line)
        with tempfile.TemporaryDirectory() as d:
            pp = os.path.join(d, "prices.json")
            with open(pp, "w", encoding="utf-8") as fh:
                json.dump(prices_doc(2455.3), fh)
            code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent", "--prices", pp,
                                 "--now", "2026-11-23 09:56"])
        self.assertEqual(code, 1)
        refusal = [l for l in text.splitlines() if l.startswith("No order:")][0]
        self.assertIn(refusal + "\n", skill)
        self.assertIn("13 shares at 35.95", " ".join(skill.split()))


if __name__ == "__main__":
    unittest.main()
