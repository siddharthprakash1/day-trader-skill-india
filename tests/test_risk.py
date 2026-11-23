import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

check = load("trader-risk", "check")
ticket = load("trader-broker", "ticket")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
TCS_PATH = os.path.join(FIX, "trader-risk", "orders-tcs.json")
PRICES_PATH = os.path.join(FIX, "trader-risk", "prices-tcs-0955.json")
NOW = "2026-11-23 09:46"
TODAY = "2026-11-23"
RUPEE = "₹"
QUOTE_TIME = datetime.datetime(2026, 11, 23, 4, 25)    # 09:55 IST: the 09:50 bar closed at 2455.30
# every day-trade check switched off: the basic checks still hold
BASIC = {"budget": 50000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 20000,
         "min_order_inr": 500, "max_orders_per_day": 10, "require_stop": False, "no_entries_before": None,
         "no_entries_after": None, "max_trades_per_day": 0, "max_daily_loss_inr": 0,
         "max_loss_per_trade_inr": 0, "min_reward_risk": 0}
PRICES = {"ITC": 301.0, "INFY": 1103.4, "SBIN": 1132.65, "M&M": 3000.0, "BAJAJ-AUTO": 9000.0}


def at(text):
    return check.parse_now(text)


def cfg(**over):
    with open(CFG_PATH, encoding="utf-8") as fh:
        c = json.load(fh)
    c.update(over)
    return c


def basic(**over):
    c = dict(BASIC)
    c.update(over)
    return c


def tcs(**over):
    with open(TCS_PATH, encoding="utf-8") as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def bracket(sym, qty, entry, stop, target):
    return {"symbol": sym, "side": "buy", "qty": str(qty), "type": "market", "time_in_force": "day",
            "order_class": "bracket", "take_profit_limit_price": "%.2f" % target,
            "stop_loss_stop_price": "%.2f" % stop,
            "plan": {"entry": entry, "stop": stop, "target": target}}


def buy(sym, qty):
    return {"symbol": sym, "side": "buy", "qty": str(qty), "type": "market", "time_in_force": "day"}


def placed(sym, side="buy", day=TODAY, max_loss=362.25, kind=None, status="placed"):
    r = {"symbol": sym, "side": side, "status": status, "date": day, "at": day + "T09:50:00+05:30"}
    if max_loss is not None:
        r["max_loss"] = max_loss
    if kind:
        r["kind"] = kind
    return r


def verdicts(res):
    return [r["verdict"] for r in res["results"]]


def reasons(res, i=0):
    return " | ".join(res["results"][i]["reasons"])


def warnings(res, i=0):
    return " | ".join(res["results"][i]["warnings"])


class TheExample(unittest.TestCase):
    def test_the_tcs_bracket_passes_every_check(self):
        res = check.check([tcs()], cfg(), now=at(NOW))
        r = res["results"][0]
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertEqual(r["reasons"], [])
        self.assertEqual(r["warnings"], [])
        self.assertEqual((r["kind"], r["entry_ref"], r["stop"], r["target"]), ("entry", 2443.5, 2419.35, 2467.65))
        self.assertEqual((r["risk_per_share"], r["max_loss"], r["reward_risk"]), (24.15, 362.25, 1.0))
        self.assertEqual(r["value_inr"], 36652.5)
        self.assertIn("before brokerage, STT and other charges", r["info"])
        self.assertEqual(res["cash_after"], 13347.5)
        self.assertEqual((res["date"], res["now_ist"]), (TODAY, "2026-11-23 09:46"))
        self.assertIs(res["paper"], True)
        self.assertNotIn("day_trades_5d", res["today"])

    def test_sixteen_shares_is_blocked_by_the_position_cap_only(self):
        res = check.check([tcs(qty="16")], cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertEqual(res["results"][0]["reasons"],
                         ["position: TCS would be %s39,096, 78.2%% of the %s50,000 account, over your 75%% cap"
                          % (RUPEE, RUPEE)])

    def test_the_max_loss_blocks(self):
        res = check.check([tcs()], cfg(max_loss_per_trade_inr=300), now=at(NOW))
        self.assertEqual(res["results"][0]["reasons"],
                         ["loss: 15 shares x 24.15 risk a share = %s362.25 at the stop, over your %s300 max loss "
                          "per trade" % (RUPEE, RUPEE)])

    def test_fingerprint_is_shared_with_the_broker(self):
        o = tcs()
        self.assertEqual(check.fingerprint(o), ticket.fingerprint(o))
        self.assertEqual(check.fingerprint(dict(o, why="anything", plan={})), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, qty="16")), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, stop_loss_stop_price="2419.00")), check.fingerprint(o))
        self.assertEqual(check.ORDER_FIELDS, ticket.ORDER_FIELDS)
        for f in ("symbol", "side", "qty", "type", "time_in_force", "order_class", "take_profit_limit_price",
                  "stop_loss_stop_price", "limit_price"):
            self.assertIn(f, check.ORDER_FIELDS)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "state", "risk.json")
            none = os.path.join(d, "none")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", TCS_PATH, "--config", CFG_PATH, "--portfolio", none,
                                   "--prices", none, "--state", none, "--now", NOW, "--out", out])
            text = buf.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("PASS  #1 buy TCS 15 shares, entry", text)
            self.assertIn("entry 2,443.50 (the plan's entry), stop 2,419.35, target 2,467.65: risk 24.15 a share",
                          text)
            self.assertIn("max loss %s362.25 (limit %s500), reward:risk 1.00 (min 1)" % (RUPEE, RUPEE), text)
            self.assertIn("          before brokerage, STT and other charges\n", text)
            self.assertIn("today before this: 0 of 3 trades, %s0 of %s1,500 daily loss" % (RUPEE, RUPEE), text)
            self.assertIn("1 passed, 0 blocked at 2026-11-23 09:46 IST", text)
            self.assertIn("%s13,347.50 (practice mode)" % RUPEE, text)
            self.assertIn("not financial advice", text)
            self.assertNotIn("$", text)
            self.assertNotIn("New York", text)
            with open(out, encoding="utf-8") as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], TODAY)
            self.assertEqual(rec["results"][0]["kind"], "entry")
            self.assertEqual(rec["results"][0]["fingerprint"], check.fingerprint(tcs()))


class IndiaCalendar(unittest.TestCase):
    def test_ist_is_a_fixed_utc_plus_5_30(self):
        self.assertEqual(at("2026-11-23 09:46"), datetime.datetime(2026, 11, 23, 4, 16))
        self.assertEqual(at("2026-11-23 09:46 IST"), datetime.datetime(2026, 11, 23, 4, 16))
        self.assertEqual(at("2026-06-15 09:46"), datetime.datetime(2026, 6, 15, 4, 16))    # no daylight saving
        self.assertEqual(at("2026-11-23T04:16:00Z"), datetime.datetime(2026, 11, 23, 4, 16))
        self.assertEqual(at("2026-11-23T09:46:00+05:30"), datetime.datetime(2026, 11, 23, 4, 16))
        self.assertEqual(check.to_ist(datetime.datetime(2026, 11, 23, 4, 16)), datetime.datetime(2026, 11, 23, 9, 46))
        self.assertEqual(check.ist_to_utc(datetime.datetime(2026, 3, 9, 0, 10)), datetime.datetime(2026, 3, 8, 18, 40))
        for gone in ("to_eastern", "eastern_to_utc", "eastern_offset", "_nth_sunday", "NYSE_HOLIDAYS",
                     "NYSE_EARLY_CLOSE", "EARLY", "day_trades", "business_days"):
            self.assertFalse(hasattr(check, gone), gone)

    def test_the_nse_session(self):
        self.assertEqual((check.OPEN, check.CLOSE), (datetime.time(9, 15), datetime.time(15, 30)))

    def test_nse_holidays_2026(self):
        self.assertEqual(check.CALENDAR_YEARS, (2026,))
        self.assertEqual(len(check.NSE_HOLIDAYS), 16)
        self.assertEqual(check.trading_day(datetime.date(2026, 11, 24)), (False, "NSE holiday: Guru Nanak Jayanti"))
        self.assertEqual(check.trading_day(datetime.date(2026, 11, 23)), (True, ""))
        self.assertEqual(check.trading_day(datetime.date(2026, 1, 26)), (False, "NSE holiday: Republic Day"))
        self.assertEqual(check.trading_day(datetime.date(2026, 11, 22)), (False, "weekend"))
        for day in check.NSE_HOLIDAYS:
            self.assertLess(datetime.date.fromisoformat(day).weekday(), 5, day)     # every one is a weekday
        self.assertEqual(check.trading_day(datetime.date(2026, 11, 26))[0], True)   # US Thanksgiving: open

    def test_a_year_without_a_holiday_list_warns(self):
        res = check.check([tcs()], cfg(), now=at("2027-01-26 10:00"))               # a Tuesday
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("covers 2026 only", " ".join(res["warnings"]))
        res = check.check([tcs()], cfg(), now=at("2027-01-23 10:00"))               # a Saturday
        self.assertIn("window: the market is closed today (weekend", reasons(res))


class DayTradeChecks(unittest.TestCase):
    def one(self, order, now=NOW, **over):
        return check.check([order], cfg(**over), now=at(now))

    def test_a_stop_is_required(self):
        o = tcs()
        for k in ("order_class", "take_profit_limit_price", "stop_loss_stop_price", "plan"):
            o.pop(k)
        o["plan"] = {"entry": 2443.5}
        res = self.one(o)
        self.assertIn("stop: no stop", reasons(res))
        res = self.one(o, require_stop=False, min_reward_risk=0)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("no stop", warnings(res))

    def test_a_plan_stop_counts_but_warns(self):
        o = tcs()
        for k in ("order_class", "take_profit_limit_price", "stop_loss_stop_price"):
            o.pop(k)
        res = self.one(o)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("in the plan only", warnings(res))
        self.assertEqual(res["results"][0]["max_loss"], 362.25)

    def test_stop_and_target_on_the_right_side(self):
        self.assertIn("must be below the entry 2,443.50", reasons(self.one(tcs(stop_loss_stop_price="2445.00"))))
        res = self.one(tcs(take_profit_limit_price="2443.00"))
        self.assertIn("target: the target 2,443.00 must be above the entry 2,443.50", reasons(res))

    def test_reward_risk(self):
        res = self.one(tcs(take_profit_limit_price="2460.00"))
        self.assertIn("reward:risk: 0.68", reasons(res))
        self.assertEqual(verdicts(self.one(tcs(take_profit_limit_price="2460.00"), min_reward_risk=0.5)), ["PASS"])

    def test_a_fresh_higher_price_is_the_entry(self):
        later = at("2026-11-23 09:56")
        res = check.check([tcs()], cfg(), prices={"TCS": 2455.3}, now=later, price_times={"TCS": QUOTE_TIME})
        r = res["results"][0]
        self.assertEqual((r["entry_ref"], r["entry_ref_from"], r["risk_per_share"]), (2455.3, "price", 35.95))
        why = reasons(res)
        self.assertIn("loss: 15 shares x 35.95 risk a share = %s539.25" % RUPEE, why)
        self.assertIn("reward:risk: 0.34", why)

    def test_a_stale_or_future_price_is_not_used(self):
        for now in ("2026-11-23 10:30", "2026-11-23 09:46"):
            res = check.check([tcs()], cfg(), prices={"TCS": 2455.3}, now=at(now),
                              price_times={"TCS": QUOTE_TIME})
            self.assertEqual(verdicts(res), ["PASS"], now)
            self.assertEqual(res["results"][0]["entry_ref"], 2443.5)
            self.assertIn("older than 15 minutes", warnings(res))

    def test_a_lower_price_does_not_shrink_the_risk(self):
        res = check.check([tcs()], cfg(), prices={"TCS": 2430.00}, now=at(NOW))
        self.assertEqual(res["results"][0]["entry_ref"], 2443.5)

    def test_a_limit_order_uses_its_limit_price(self):
        o = tcs(type="limit", limit_price="2440.00")
        res = check.check([o], cfg(), prices={"TCS": 2455.3}, now=at(NOW))
        r = res["results"][0]
        self.assertEqual((r["entry_ref"], r["max_loss"], r["verdict"]), (2440.0, 309.75, "PASS"))

    def test_daily_loss_and_trades_per_day(self):
        two = {"orders": [placed("INFY"), placed("ITC")]}
        res = check.check([tcs()], cfg(max_position_pct=100, cash_buffer_pct=0), state=two, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("today before this: 2 of 3 trades, %s724.50 of %s1,500 daily loss" % (RUPEE, RUPEE),
                      res["results"][0]["info"][-1])
        three = {"orders": [placed("INFY"), placed("ITC"), placed("SBIN")]}
        res = check.check([tcs()], cfg(max_daily_loss_inr=1400), state=three, now=at(NOW))
        why = reasons(res)
        self.assertIn("trades: 3 entries taken today", why)
        self.assertIn("daily loss: %s1,086.75 already at risk today plus %s362.25 for this one = %s1,449, over "
                      "your %s1,400" % (RUPEE, RUPEE, RUPEE, RUPEE), why)
        last_friday = {"orders": [placed(s, day="2026-11-20") for s in ("INFY", "ITC", "SBIN")]}
        self.assertEqual(verdicts(check.check([tcs()], cfg(), state=last_friday, now=at(NOW))), ["PASS"])

    def test_exits_and_declines_do_not_count_as_trades(self):
        st = {"orders": [placed("INFY"), placed("INFY", side="sell", kind="exit", max_loss=None),
                         placed("ITC", status="declined"), placed("SBIN", status="not-placed"),
                         placed("LT", status="rejected")]}
        res = check.check([tcs()], cfg(), state=st, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertEqual(res["today"]["entries_placed"], 1)

    def test_the_batch_counts_toward_the_daily_limits(self):
        orders = [bracket(s, 10, 1000.0, 960.0, 1040.0) for s in ("AAA", "BBB", "CCC", "DDD")]
        res = check.check(orders, cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "PASS", "BLOCK"])
        why = reasons(res, 3)
        self.assertIn("trades: 0 entries taken today plus 3 passed earlier", why)
        self.assertIn("daily loss: %s1,200 already at risk today plus %s400 for this one = %s1,600" %
                      (RUPEE, RUPEE, RUPEE), why)

    def test_entry_window_india_time(self):
        cases = [("2026-11-23 09:29", "no new entries before 09:30 IST (it is 09:29)"),
                 ("2026-11-23 14:31", "no new entries after 14:30 IST (it is 14:31)"),
                 ("2026-11-23 09:14", "the market is closed (Mon 2026-11-23 09:14 IST; the session is 09:15 to 15:30)"),
                 ("2026-11-23 09:05", "the market is closed"),           # the pre-open session is ignored
                 ("2026-11-23 15:30", "the market is closed"),
                 ("2026-11-23 16:05", "the market is closed"),
                 ("2026-11-21 10:15", "the market is closed today (weekend"),
                 ("2026-11-24 10:15", "the market is closed today (NSE holiday: Guru Nanak Jayanti"),
                 ("2026-12-25 10:15", "the market is closed today (NSE holiday: Christmas"),
                 ("2026-11-23T03:50:00Z", "no new entries before 09:30 IST (it is 09:20)")]
        for now, want in cases:
            res = self.one(tcs(), now=now)
            self.assertIn("window: " + want, reasons(res), now)
        for now in ("2026-11-23 09:30", "2026-11-23 14:30", "2026-11-23T04:16:00Z", "2026-11-25 10:00",
                    "2026-11-27 13:30"):     # the 27th: no early closes on NSE
            self.assertEqual(verdicts(self.one(tcs(), now=now)), ["PASS"], now)
        self.assertEqual(self.one(tcs(), now="2026-11-27 13:30")["warnings"], [])
        res = self.one(tcs(), now="2026-11-21 10:15", no_entries_before=None, no_entries_after=None)
        self.assertEqual(verdicts(res), ["PASS"])
        res = self.one(tcs(), now="2026-11-23 09:20", no_entries_before="09:15")
        self.assertEqual(verdicts(res), ["PASS"])
        with self.assertRaises(check.RiskError):
            self.one(tcs(), no_entries_before="9.30am")

    def test_pdt_guard_is_gone(self):
        def trade(day):
            return [placed("X" + day[-2:], day=day), placed("X" + day[-2:], side="sell", day=day, kind="exit",
                                                            status="filled", max_loss=None)]
        st = {"orders": trade("2026-11-18") + trade("2026-11-19") + trade("2026-11-20")}
        res = check.check([tcs()], cfg(pdt_guard=True), state=st, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertNotIn("pdt_guard", check.DEFAULTS)
        with open(os.path.join(ROOT, "trader-risk", "check.py"), encoding="utf-8") as fh:
            self.assertNotIn("pdt", fh.read().lower())

    def test_a_short_entry_when_allowed(self):
        short = {"symbol": "TCS", "side": "sell", "qty": "15", "type": "market", "time_in_force": "day",
                 "order_class": "bracket", "take_profit_limit_price": "2419.35", "stop_loss_stop_price": "2467.65",
                 "plan": {"entry": 2443.5}}
        res = self.one(short)
        self.assertIn("short: selling more TCS than you hold", reasons(res))
        res = self.one(short, allow_short=True)
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"], r["max_loss"], r["reward_risk"]), ("PASS", "entry", 362.25, 1.0))
        res = self.one(dict(short, stop_loss_stop_price="2430.00"), allow_short=True)
        self.assertIn("must be above the entry 2,443.50 for a sell", reasons(res))

    def test_format(self):
        self.assertIn("whole shares only; give a qty, not a rupee amount",
                      reasons(self.one(dict(tcs(qty=None), notional="36000"))))
        self.assertIn("no fractional shares", reasons(self.one(tcs(qty="14.5"))))
        self.assertIn("no qty", reasons(self.one(tcs(qty=None))))
        self.assertIn("qty must be a positive number", reasons(self.one(tcs(qty="0"))))
        self.assertIn("must be valid for the day", reasons(self.one(tcs(time_in_force="ioc"))))
        self.assertIn("unknown validity", reasons(self.one(tcs(time_in_force="gtc"))))
        o = tcs()
        o.pop("take_profit_limit_price")
        self.assertIn("needs both take_profit_limit_price and stop_loss_stop_price", reasons(self.one(o)))
        self.assertIn("needs a stop_loss_stop_price", reasons(self.one(dict(tcs(), stop_loss_stop_price=None,
                                                                            stop_loss_limit_price="2418.00"))))
        self.assertIn("no extended-hours", reasons(self.one(tcs(extended_hours=True))))
        self.assertEqual(verdicts(self.one(tcs(stop_loss_stop_price=2419.35))), ["PASS"])   # a number is fine


class Exits(unittest.TestCase):
    PF = {"cash": 13356.5, "positions": [{"ticker": "TCS", "shares": 15,
                                           "lots": [{"date": TODAY, "shares": 15, "cost": 36643.5}]}]}
    SELL = {"symbol": "TCS", "side": "sell", "qty": "15", "type": "market", "time_in_force": "day"}

    def test_getting_out_is_never_blocked_by_the_day_limits(self):
        full = {"orders": [placed("TCS"), placed("INFY"), placed("ITC")] +
                          [placed("T%d" % i, max_loss=1) for i in range(6)] +
                          [placed("TCS", side="sell", kind="exit", max_loss=None)]}
        c = cfg(blocked=["TCS"], max_order_inr=1000, max_daily_loss_inr=50)
        res = check.check([self.SELL], c, portfolio=self.PF, state=full, now=at("2026-11-23 14:58"))
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"]), ("PASS", "exit"), r["reasons"])
        why = warnings(res)
        for word in ("blocked:", "duplicate:", "daily limit:"):
            self.assertIn("exit, not blocked: " + word, why)
        self.assertNotIn("price", why)
        for now in ("2026-11-21 12:00", "2026-11-24 12:00", "2026-11-23 15:25"):   # weekend, holiday, late
            res = check.check([self.SELL], cfg(), portfolio=self.PF, now=at(now))
            self.assertEqual(verdicts(res), ["PASS"], now)

    def test_a_partial_exit_and_the_rest(self):
        five = dict(self.SELL, qty="5")
        res = check.check([five, five, five], cfg(), portfolio=self.PF, prices={"TCS": 2460.0}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "PASS"])
        self.assertEqual([r["kind"] for r in res["results"]], ["exit", "exit", "exit"])
        self.assertIn("exit, not blocked: duplicate", warnings(res, 1))
        res = check.check([five] * 4, cfg(), portfolio=self.PF, prices={"TCS": 2460.0}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "PASS", "BLOCK"])
        self.assertIn("short:", reasons(res, 3))

    def test_format_asset_and_short_still_apply_to_a_sell(self):
        res = check.check([dict(self.SELL, type="limit")], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("needs a limit_price", reasons(res))
        res = check.check([dict(self.SELL, qty="16")], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("short: selling more TCS than you hold (15 shares)", reasons(res))
        res = check.check([self.SELL], cfg(), now=at(NOW))
        self.assertIn("record the fill first", reasons(res))

    def test_buy_then_sell_in_one_batch(self):
        res = check.check([tcs(), self.SELL], cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual(res["results"][1]["kind"], "exit")


class BasicChecks(unittest.TestCase):
    """The basic checks, with the day-trade checks switched off."""

    def one(self, order, **over):
        return check.check([order], basic(**over), prices=PRICES, today=TODAY)["results"][0]

    def test_nse_symbols_pass_the_asset_check(self):
        for sym in ("TCS", "RELIANCE", "HDFCBANK", "M&M", "BAJAJ-AUTO", "3MINDIA", "L&TFH", "MCDOWELL-N", "NIFTYBEES"):
            o = {"symbol": sym, "side": "buy", "qty": "1", "type": "limit", "limit_price": "1000",
                 "time_in_force": "day"}
            r = self.one(o)
            self.assertEqual(r["verdict"], "PASS", (sym, r["reasons"]))
            self.assertEqual(r["symbol"], sym)

    def test_the_ns_suffix_is_stripped(self):
        self.assertEqual(check.nse_symbol("m&m.ns"), "M&M")
        r = self.one(buy("M&M.NS", 2))
        self.assertEqual((r["verdict"], r["symbol"], r["value_inr"]), ("PASS", "M&M", 6000.0))
        r = self.one(buy("ITC.NS", 10), blocked=["itc"])
        self.assertIn("blocked list", " ".join(r["reasons"]))
        prices, times = check.load_prices({"as_of": "2026-11-23T04:25:10Z", "prices": {"TCS.NS": 2455.3},
                                           "detail": {"TCS.NS": {"time": "2026-11-23T04:25:00Z"}}})
        self.assertEqual((prices, times), ({"TCS": 2455.3}, {"TCS": QUOTE_TIME}))

    def test_fno_contracts_are_off_unless_allowed(self):
        for sym in ("NIFTY26NOV24500CE", "BANKNIFTY26DEC52000PE", "TCS26NOVFUT", "RELIANCE26NOV1400CE",
                    "M&M26NOV3000PE", "NIFTY26N2424500CE"):
            opt = {"symbol": sym, "side": "buy", "qty": "75", "type": "limit", "limit_price": "100",
                   "time_in_force": "day"}
            r = self.one(opt)
            self.assertEqual(r["verdict"], "BLOCK", sym)
            self.assertIn("F&O", " ".join(r["reasons"]), sym)
            r = self.one(opt, allow_options=True)
            self.assertEqual(r["verdict"], "PASS", (sym, r["reasons"]))
            self.assertIn("F&O order", " ".join(r["warnings"]))

    def test_anything_else_is_not_an_nse_equity(self):
        for sym in ("BTC/USD", "BE270617C00100000", "BRK.B", "TCS.BO", "^NSEI", "TCS EQ", "A" * 21, "USDINR=X"):
            o = {"symbol": sym, "side": "buy", "qty": "1", "type": "limit", "limit_price": "100",
                 "time_in_force": "day"}
            for allow in (False, True):
                r = self.one(o, allow_options=allow)
                self.assertEqual(r["verdict"], "BLOCK", sym)
                self.assertIn("not a plain NSE equity symbol", " ".join(r["reasons"]), sym)

    def test_max_and_min_order(self):
        self.assertIn("over your %s20,000 max order" % RUPEE,
                      " ".join(self.one(buy("INFY", 20), max_position_pct=100)["reasons"]))
        self.assertIn("under your %s500 minimum" % RUPEE, " ".join(self.one(buy("ITC", 1))["reasons"]))

    def test_position_cap_and_cash_buffer(self):
        self.assertIn("position: INFY would be", " ".join(self.one(buy("INFY", 12))["reasons"]))
        res = check.check([buy("ITC", 40), buy("INFY", 11), buy("SBIN", 11), buy("M&M", 4)], basic(),
                          prices=PRICES, today=TODAY)
        self.assertEqual(verdicts(res), ["PASS", "PASS", "PASS", "BLOCK"])
        self.assertIn("cash: leaves", reasons(res, 3))

    def test_blocked_list(self):
        r = self.one(buy("ITC", 10), blocked=["ITC"])
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("blocked list", r["reasons"][0])

    def test_format_rules(self):
        nolimit = {"symbol": "ITC", "side": "buy", "qty": "10", "type": "limit", "time_in_force": "day"}
        self.assertIn("needs a limit_price", " ".join(self.one(nolimit)["reasons"]))
        bad = {"symbol": "ITC", "side": "hold", "qty": "10"}
        self.assertIn("side must be buy or sell", " ".join(self.one(bad)["reasons"]))
        self.assertIn("unknown order type", " ".join(self.one(dict(buy("ITC", 10), type="trailing_stop"))["reasons"]))
        slm = {"symbol": "ITC", "side": "buy", "qty": "10", "type": "stop", "stop_price": "302.00",
               "time_in_force": "day"}
        r = self.one(slm)
        self.assertEqual((r["verdict"], r["entry_ref"], r["entry_ref_from"]), ("PASS", 302.0, "stop price"))

    def test_share_orders_need_a_price(self):
        self.assertIn("no price for ZZZZ", " ".join(self.one(buy("ZZZZ", 2))["reasons"]))
        r = self.one(buy("ITC", 10))
        self.assertEqual(r["verdict"], "PASS")
        self.assertAlmostEqual(r["value_inr"], 3010.0)
        self.assertEqual(r["amount"], "10 shares")

    def test_margin(self):
        r = check.check([buy("ITC", 50)], basic(cash_buffer_pct=0, max_position_pct=100),
                        portfolio={"cash": 10000, "positions": []}, prices=PRICES, today=TODAY)["results"][0]
        self.assertIn("margin", " ".join(r["reasons"]))

    PF = {"cash": 5000.0, "positions": [{"ticker": "ITC", "shares": 20,
                                          "lots": [{"date": "2026-11-20", "shares": 20, "cost": 6000.0}]}]}

    def test_short_sales_are_blocked(self):
        sell = {"symbol": "ITC", "side": "sell", "qty": "25", "type": "market", "time_in_force": "day"}
        r = check.check([sell], basic(), portfolio=self.PF, prices=PRICES, today=TODAY)["results"][0]
        self.assertIn("short:", " ".join(r["reasons"]))
        sell["qty"] = "8"
        r = check.check([sell], basic(), portfolio=self.PF, prices=PRICES, today=TODAY)["results"][0]
        self.assertEqual((r["verdict"], r["kind"]), ("PASS", "exit"))

    def test_a_sell_frees_cash_for_a_later_buy(self):
        orders = [{"symbol": "ITC", "side": "sell", "qty": "15", "type": "market", "time_in_force": "day"},
                  buy("INFY", 5)]
        res = check.check(orders, basic(max_position_pct=100), portfolio=self.PF, prices=PRICES, today=TODAY)
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        res = check.check([buy("INFY", 5)], basic(max_position_pct=100), portfolio=self.PF, prices=PRICES,
                          today=TODAY)
        self.assertEqual(verdicts(res), ["BLOCK"])

    def test_daily_limit_and_already_taken_today(self):
        st = {"orders": [{"symbol": "T%d" % i, "side": "buy", "status": "placed", "at": TODAY + "T10:00:00+05:30"}
                         for i in range(10)]}
        res = check.check([buy("ITC", 10)], basic(), prices=PRICES, state=st, today=TODAY)
        self.assertIn("daily limit", reasons(res))
        res = check.check([buy("ITC", 10)], basic(), prices=PRICES, state=st, today="2026-11-25")
        self.assertEqual(verdicts(res), ["PASS"])
        itc = {"orders": [{"symbol": "ITC", "side": "buy", "status": "placed", "at": TODAY + "T10:00:00+05:30"}]}
        res = check.check([buy("ITC", 10)], basic(), prices=PRICES, state=itc, today=TODAY)
        self.assertIn("already taken today", reasons(res))
        declined = {"orders": [{"symbol": "ITC", "side": "buy", "status": "declined", "at": TODAY + "T10:00:00"}]}
        self.assertEqual(verdicts(check.check([buy("ITC", 10)], basic(), prices=PRICES, state=declined,
                                              today=TODAY)), ["PASS"])

    def test_live_warns(self):
        res = check.check([buy("ITC", 10)], basic(paper=False), prices=PRICES, today=TODAY)
        self.assertIn("LIVE", res["warnings"][0])
        self.assertIn("LIVE", check.report(res))


class Cli(unittest.TestCase):
    def test_prices_file_freshness(self):
        with tempfile.TemporaryDirectory() as d:
            none = os.path.join(d, "none")
            base = ["--orders", TCS_PATH, "--config", CFG_PATH, "--portfolio", none, "--prices", PRICES_PATH,
                    "--state", none, "--no-write"]
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(check.main(base + ["--now", "2026-11-23 09:56"]), 1)
            self.assertIn("15 shares x 35.95 risk a share = %s539.25" % RUPEE, buf.getvalue())
            self.assertIn("TCS last 2,455.30 is above the plan's entry 2,443.50", buf.getvalue())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(check.main(base + ["--now", "2026-11-23 10:30"]), 0)

    def test_the_holiday_blocks_from_the_command_line(self):
        with tempfile.TemporaryDirectory() as d:
            none = os.path.join(d, "none")
            out = os.path.join(d, "state", "risk.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", TCS_PATH, "--config", CFG_PATH, "--portfolio", none, "--prices", none,
                                   "--state", none, "--now", "2026-11-24 10:00", "--out", out])
            self.assertEqual(code, 1)
            self.assertIn("NSE holiday: Guru Nanak Jayanti, Tue 2026-11-24 10:00 IST", buf.getvalue())
            with open(out, encoding="utf-8") as fh:
                rec = json.load(fh)
            self.assertEqual((rec["date"], rec["block"]), ("2026-11-24", 1))
            self.assertTrue(all(len(r["fingerprint"]) == 16 for r in rec["results"]))

    def test_errors_are_one_line(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", "/nonexistent.json", "--no-write"]), 1)
        self.assertIn("no orders file", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", TCS_PATH, "--now", "noon", "--no-write"]), 1)
        self.assertIn("--now wants", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", TCS_PATH, "--today", "monday", "--no-write"]), 1)
        self.assertIn("--today wants", err.getvalue())


class SkillExample(unittest.TestCase):
    """The SKILL.md example blocks are real output of the tool on the fixture order."""

    def lines(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            check.main(argv + ["--config", CFG_PATH, "--portfolio", "/nonexistent", "--state", "/nonexistent",
                               "--no-write"])
        return [l for l in buf.getvalue().splitlines() if l.strip()]

    def test_the_skill_md_examples_are_the_real_output(self):
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            skill = fh.read()
        flat = " ".join(skill.split())
        for line in self.lines(["--orders", TCS_PATH, "--prices", "/nonexistent", "--now", NOW]):
            self.assertIn(line + "\n", skill, line)
        for line in self.lines(["--orders", TCS_PATH, "--prices", PRICES_PATH, "--now", "2026-11-23 09:56"])[:8]:
            self.assertIn(line + "\n", skill, line)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "o16.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"orders": [tcs(qty="16")]}, fh)
            for line in self.lines(["--orders", p, "--prices", "/nonexistent", "--now", NOW])[:6]:
                self.assertIn(line + "\n", skill, line)
        for now in ("2026-11-23 09:20", "2026-11-24 10:00"):
            why = [l for l in self.lines(["--orders", TCS_PATH, "--prices", "/nonexistent", "--now", now])
                   if "window:" in l][0]
            self.assertIn(why.strip(" -"), flat, now)


if __name__ == "__main__":
    unittest.main()
