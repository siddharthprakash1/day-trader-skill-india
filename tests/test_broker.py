import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
ORDERS_PATH = os.path.join(FIX, "trader-risk", "orders-tcs.json")
PLAN_PATH = os.path.join(FIX, "trader-budget", "plan-tcs.json")
NOW = "2026-11-23 09:46"
TODAY = "2026-11-23"
RUPEE = "₹"
ITC = {"symbol": "ITC", "side": "buy", "qty": "10", "type": "limit", "limit_price": "301.50",
       "time_in_force": "day"}
BATCH = ["ITC", "INFY", "SBIN", "M&M", "BAJAJ-AUTO"]
TCS_CORE = {"symbol": "TCS", "side": "buy", "qty": "15", "type": "market", "time_in_force": "day",
            "order_class": "bracket", "take_profit_limit_price": "2467.65", "stop_loss_stop_price": "2419.35"}
TCS_FIRST = "BUY 15 TCS on NSE, product Intraday (MIS), market order, valid today"
TCS_LOSS = ("max loss about %s362.25 if the stop fills at 2,419.35 (before charges; it can fill worse in a "
            "fast market)" % RUPEE)
TCS_PLAIN = " / ".join([TCS_FIRST, "stop-loss 2,419.35", "target 2,467.65", TCS_LOSS])
TCS_TICKET = [
    TCS_FIRST,
    "stop-loss 2,419.35   -> place a separate SL / SL-M sell order for 15 shares right after the buy fills",
    "target    2,467.65   -> place a limit sell for 15 shares, or set a price alert; cancel the other when "
    "one fills",
    "(two separate exit orders, because many brokers have no bracket order for equity intraday; or use your "
    "broker's bracket/OCO order if it has one)",
    TCS_LOSS,
    "flat by 15:00 IST: your broker auto-squares-off intraday positions around 15:10-15:20 and charges for it",
]


def tcs(**over):
    with open(ORDERS_PATH, encoding="utf-8") as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ticket.main(argv)
    return code, out.getvalue(), err.getvalue()


class ExplicitYes(unittest.TestCase):
    def yes(self, reply, order=ITC, batch=BATCH):
        return ticket.parse_yes(reply, order, batch)[0]

    def test_plain_yes_counts(self):
        for r in ("yes", "Yes.", "YES", "y", "yes place it", "Yes, buy it", "yes ITC", "yes ITC.", "yes 10 ITC",
                  "yes 301.50", "yes, I will place it myself"):
            self.assertTrue(self.yes(r), r)

    def test_anything_else_does_not(self):
        for r in ("", "sure", "ok", "okay", "go", "do it", "yeah", "yep", "haan", "absolutely", "send it",
                  "yes to all", "yes, all of them", "yes to the rest", "yes both",
                  "yes INFY", "yes M&M", "yes BAJAJ-AUTO", "yes 12", "yes but make it 12", "yes?", "no",
                  "not yes", "yes, wait", "yes if it is under 300"):
            self.assertFalse(self.yes(r), r)

    def test_amount_must_match_this_order(self):
        sell = {"symbol": "ITC", "side": "sell", "qty": "3", "type": "market", "time_in_force": "day"}
        self.assertTrue(self.yes("yes sell 3 ITC", sell))
        self.assertFalse(self.yes("yes sell 4", sell))

    def test_nse_symbols_with_ampersand_hyphen_and_digits(self):
        mm = {"symbol": "M&M", "side": "buy", "qty": "2", "type": "market", "time_in_force": "day"}
        self.assertTrue(self.yes("yes M&M", mm))
        self.assertTrue(self.yes("yes 2 M&M.NS", mm))
        self.assertFalse(self.yes("yes ITC", mm))
        bajaj = dict(mm, symbol="BAJAJ-AUTO")
        self.assertTrue(self.yes("yes BAJAJ-AUTO", bajaj))
        self.assertFalse(self.yes("yes M&M", bajaj))
        three_m = dict(mm, symbol="3MINDIA", qty="1")
        self.assertTrue(self.yes("yes 3MINDIA", three_m, ["3MINDIA", "ITC"]))   # the 3 is the name, not an amount
        self.assertFalse(self.yes("yes 3 3MINDIA", three_m, ["3MINDIA", "ITC"]))

    def test_a_bracket_yes(self):
        o = tcs()
        for r in ("yes", "yes 15 TCS", "yes 2419.35", "yes 2,419.35", "yes max loss 362.25", "yes target 2467.65"):
            self.assertTrue(self.yes(r, o, ["TCS"]), r)
        for r in ("yes 16", "yes 20 TCS", "yes but stop at 2400", "yes, stop 2419.35", "yes ITC", "sure"):
            self.assertFalse(self.yes(r, o, ["TCS", "ITC"]), r)


class TicketShape(unittest.TestCase):
    def test_no_broker_api_anywhere(self):
        for gone in ("tool_input", "ALPACA_FIELDS", "STRING_FIELDS", "to_eastern", "eastern_offset", "ny_iso"):
            self.assertFalse(hasattr(ticket, gone), gone)
        with open(os.path.join(ROOT, "trader-broker", "ticket.py"), encoding="utf-8") as fh:
            src = fh.read().lower()
        for word in ("alpaca", "place_stock_order", "ibkr", "interactive brokers", "mcp", "allow once",
                     "get_open_position", "new york", "api key"):
            self.assertNotIn(word, src, word)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "trader-broker", "claude_desktop_config.example.json")))

    def test_fingerprint_ignores_why_and_plan(self):
        self.assertEqual(ticket.fingerprint(tcs()), ticket.fingerprint(TCS_CORE))
        self.assertEqual(ticket.fingerprint(tcs()), check.fingerprint(tcs()))

    def test_plain_words(self):
        self.assertEqual(ticket.plain(ITC),
                         "BUY 10 ITC on NSE, product Intraday (MIS), limit order at 301.50, valid today")
        self.assertEqual(ticket.plain({"symbol": "M&M.NS", "side": "sell", "qty": "1", "type": "stop",
                                       "stop_price": "2990", "time_in_force": "day"}),
                         "SELL 1 M&M on NSE, product Intraday (MIS), SL-M order, trigger 2,990.00, valid today")
        self.assertEqual(ticket.plain(tcs()), TCS_PLAIN)
        self.assertEqual(ticket.ticket_lines(tcs(), "15:00"), TCS_TICKET)
        lim = tcs(type="limit", limit_price="2440.00")
        self.assertEqual(ticket.plain(lim).split(" / ")[0],
                         "BUY 15 TCS on NSE, product Intraday (MIS), limit order at 2,440.00, valid today")
        self.assertEqual(ticket.max_loss(lim), (309.75, 2419.35))
        self.assertEqual(ticket.max_loss(dict(TCS_CORE)), (None, 2419.35))

    def test_ist_stamp(self):
        self.assertEqual(ticket.parse_now("2026-11-23 09:46 IST"), datetime.datetime(2026, 11, 23, 4, 16))
        self.assertEqual(ticket.ist_iso(datetime.datetime(2026, 11, 23, 4, 16)), "2026-11-23T09:46:00+05:30")
        self.assertEqual(ticket.ist_iso(datetime.datetime(2026, 6, 30, 20, 0)), "2026-07-01T01:30:00+05:30")
        self.assertEqual(ticket.to_ist(ticket.ist_to_utc(datetime.datetime(2026, 3, 8, 9, 15))),
                         datetime.datetime(2026, 3, 8, 9, 15))


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        shutil.copy(ORDERS_PATH, self.orders)
        self.cfg = os.path.join(self.d, "config.json")
        shutil.copy(CFG_PATH, self.cfg)
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "broker.json")
        self.pf = os.path.join(self.d, "portfolio.json")
        self.none = os.path.join(self.d, "none")

    def tearDown(self):
        shutil.rmtree(self.d)

    def args(self, *extra, **kw):
        return list(extra) + ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                              "--state", self.state, "--now", kw.get("now", NOW)]

    def run_risk(self, now=NOW):
        with redirect_stdout(io.StringIO()):
            return check.main(["--orders", self.orders, "--config", self.cfg, "--portfolio", self.pf,
                               "--prices", self.none, "--state", self.state, "--now", now, "--out", self.risk])

    def set_cfg(self, **over):
        with open(self.cfg, encoding="utf-8") as fh:
            c = json.load(fh)
        c.update(over)
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump(c, fh)

    def state_doc(self):
        with open(self.state, encoding="utf-8") as fh:
            return json.load(fh)

    def test_nothing_is_ready_before_the_risk_check(self):
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", text)
        self.assertNotIn("Reply yes or no", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("not ready", err)

    def test_the_full_flow_in_practice_mode(self):
        self.assertEqual(self.run_risk(), 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertIn("ORDER 1 of 1, PRACTICE (paper: write it down, place nothing)\n", text)
        for line in TCS_TICKET:
            self.assertIn("  " + line + "\n", text)
        self.assertIn("  why: opening-range breakout on TCS: 15 shares, bound by your position cap", text)
        self.assertIn("READY: trader-risk PASS, waiting for your yes", text)
        self.assertIn("Reply yes or no.", text)
        self.assertIn("never handles your password, PIN or OTP", text)
        self.assertIn("not financial advice", text)
        for word in ("{", "place_stock_order", "Alpaca", "$", "New York"):
            self.assertNotIn(word, text)

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "sure"))
        self.assertEqual(code, 1)
        self.assertIn("Not recorded", text)
        self.assertFalse(os.path.exists(self.state))

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 0)
        self.assertIn("Recorded your yes for order 1: " + TCS_FIRST, text)
        self.assertIn("Practice mode: nothing is placed", text)
        self.assertIn("ticket.py result --index 1 --status placed", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("APPROVED", err)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("APPROVED by you at 2026-11-23T09:46:00+05:30: now you enter it yourself", text)

        # the user reports the fill: 2442.90 is the real open of the 09:45 bar
        code, text, _ = run(self.args("result", "--index", "1", "--status", "placed", "--filled-qty", "15",
                                      "--filled-price", "2442.90", "--portfolio", self.pf,
                                      now="2026-11-23 09:47"))
        self.assertEqual(code, 0)
        self.assertIn("Recorded order 1 as placed (as you reported it)", text)
        st = self.state_doc()
        self.assertEqual([r["status"] for r in st["orders"]], ["approved", "placed", "filled"])
        appr, plc = st["orders"][0], st["orders"][1]
        self.assertEqual((appr["reply"], appr["kind"], appr["max_loss"], appr["date"]),
                         ("yes", "entry", 362.25, TODAY))
        self.assertEqual((appr["at"], appr["broker"], appr["paper"]), ("2026-11-23T09:46:00+05:30", "groww", True))
        self.assertEqual((plc["max_loss"], plc["kind"], plc["reported_by"]), (362.25, "entry", "the user"))
        self.assertNotIn("broker_order_id", plc)
        with open(self.pf, encoding="utf-8") as fh:
            pf = json.load(fh)
        self.assertEqual(pf["cash"], 13356.5)
        self.assertEqual((pf["positions"][0]["ticker"], pf["positions"][0]["shares"]), ("TCS", 15))

        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: placed", text)
        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertIn("1 placed today", text)
        self.assertIn("max loss %s362.25" % RUPEE, text)

        # trader-risk now counts it: a second TCS buy today is a duplicate, and Rs 362.25 is used
        res = check.check([tcs()], {"budget": 50000}, state=st, now=check.parse_now("2026-11-23 10:00"))
        self.assertIn("already taken today", " ".join(res["results"][0]["reasons"]))
        self.assertEqual((res["today"]["entries_placed"], res["today"]["loss_at_risk"]), (1, 362.25))

        # the target (2467.65) was first reached in the real 10:05 bar: the user records that sell
        code, text, _ = run(["fill", "--symbol", "TCS", "--side", "sell", "--qty", "15", "--price", "2467.65",
                             "--portfolio", self.pf, "--config", self.cfg, "--state", self.state,
                             "--now", "2026-11-23 10:06"])
        self.assertEqual(code, 0)
        self.assertIn("Cash now %s50,371.25 (before charges)" % RUPEE, text)
        with open(self.pf, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"cash": 50371.25, "positions": []})

    def test_live_ticket_says_the_user_enters_it(self):
        self.set_cfg(paper=False, broker="zerodha")
        self.run_risk()
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertIn("ORDER 1 of 1, LIVE: you enter this yourself in your broker app (zerodha)\n", text)
        self.assertIn("Do you want to place this order yourself? Reply yes or no.", text)
        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes 15 TCS"))
        self.assertEqual(code, 0)
        self.assertIn("Now YOU enter it in your broker app (zerodha)", text)
        self.assertIn("Claude does not place it", text)
        code, text, _ = run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "251123000123",
                                      "--portfolio", self.pf))
        self.assertEqual(code, 0)
        self.assertIn("When it fills, record it: ticket.py fill --symbol TCS --side buy", text)
        self.assertIn("place the stop-loss and the target sell orders", text)
        plc = self.state_doc()["orders"][-1]
        self.assertEqual((plc["status"], plc["broker_order_id"], plc["paper"]), ("placed", "251123000123", False))
        self.assertFalse(os.path.exists(self.pf))
        self.assertIn("id 251123000123", run(["log", "--state", self.state, "--now", NOW])[1])

    def test_not_placed_and_rejected_do_not_count_as_trades(self):
        for status in ("not-placed", "rejected"):
            if os.path.exists(self.state):
                os.remove(self.state)
            self.run_risk()
            self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes"))[0], 0)
            code, _, err = run(self.args("result", "--index", "1", "--status", status, "--filled-qty", "15",
                                         "--filled-price", "2442.90", "--portfolio", self.pf))
            self.assertEqual(code, 1)
            self.assertIn("only goes with --status placed", err)
            code, text, _ = run(self.args("result", "--index", "1", "--status", status, "--note", "changed my mind",
                                          "--portfolio", self.pf))
            self.assertEqual(code, 0, status)
            self.assertFalse(os.path.exists(self.pf))
            res = check.check([tcs()], {"budget": 50000}, state=self.state_doc(), now=check.parse_now(NOW))
            self.assertEqual(res["today"]["entries_placed"], 0)
            self.assertEqual(res["results"][0]["verdict"], "PASS")
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            ticket.main(self.args("result", "--index", "1", "--status", "denied"))

    def test_a_bad_fill_records_nothing(self):
        self.run_risk()
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        for extra in (["--filled-qty", "14.5", "--filled-price", "2442.90"], ["--filled-qty", "15"]):
            code, _, err = run(self.args("result", "--index", "1", "--status", "placed", "--portfolio", self.pf,
                                         *extra))
            self.assertEqual(code, 1, extra)
            self.assertNotIn("Traceback", err)
        self.assertEqual([r["status"] for r in self.state_doc()["orders"]], ["approved"])
        self.assertFalse(os.path.exists(self.pf))

    def test_flatten_before_the_auto_square_off(self):
        self.run_risk()
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        run(self.args("result", "--index", "1", "--status", "placed", "--filled-qty", "15",
                      "--filled-price", "2442.90", "--portfolio", self.pf))
        late = "2026-11-23 14:58"
        code, text, _ = run(["flatten", "--symbol", "tcs.ns", "--orders", self.orders, "--portfolio", self.pf,
                             "--plan", PLAN_PATH, "--config", self.cfg, "--now", late])
        self.assertEqual(code, 0)
        self.assertIn("Flatten TCS: SELL 15 TCS on NSE, product Intraday (MIS), market order, valid today "
                      "(flat by 15:00 IST)", text)
        self.assertIn("replaced 1 earlier proposal", text)
        self.assertIn("cancel the open stop-loss and target sell orders", text)
        self.assertIn("positions page of your broker app", text)
        with open(self.orders, encoding="utf-8") as fh:
            doc = json.load(fh)
        o = doc["orders"][0]
        self.assertEqual({k: o[k] for k in ("symbol", "side", "qty", "type", "time_in_force")},
                         {"symbol": "TCS", "side": "sell", "qty": "15", "type": "market", "time_in_force": "day"})
        self.assertEqual(self.run_risk(now=late), 0)       # after the 14:30 entry cutoff: an exit still passes
        with open(self.risk, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["results"][0]["kind"], "exit")
        code, text, _ = run(self.args("show", now=late))
        self.assertEqual(code, 0)
        self.assertIn("READY", text)
        self.assertIn("this closes your intraday position: first cancel the open stop-loss and target orders", text)
        self.assertIn("flat by 15:00 IST: your broker auto-squares-off", text)
        self.assertIn("close this practice trade", text)
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes sell 15 TCS", now=late))[0], 0)
        # 2460.00 is the real close of the 15:25 bar; any price the user reports is recorded as given
        code, _, _ = run(self.args("result", "--index", "1", "--status", "placed", "--filled-qty", "15",
                                   "--filled-price", "2460.00", "--portfolio", self.pf, now="2026-11-23 14:59"))
        self.assertEqual(code, 0)
        with open(self.pf, encoding="utf-8") as fh:
            pf = json.load(fh)
        self.assertEqual((pf["cash"], pf["positions"]), (50256.5, []))
        code, _, err = run(["flatten", "--symbol", "TCS", "--orders", self.orders, "--portfolio", self.pf,
                            "--now", late])
        self.assertEqual(code, 1)
        self.assertIn("nothing to flatten", err)

    def test_flatten_with_the_qty_from_the_broker_app(self):
        code, text, _ = run(["flatten", "--symbol", "m&m", "--qty", "4", "--orders", self.orders,
                             "--portfolio", self.none, "--plan", self.none, "--config", self.cfg, "--now", NOW])
        self.assertEqual(code, 0)
        self.assertIn("Flatten M&M: SELL 4 M&M on NSE", text)
        self.assertNotIn("The qty comes from", text)
        for bad in ("0", "2.5", "lots"):
            self.assertEqual(run(["flatten", "--symbol", "TCS", "--qty", bad, "--orders", self.orders])[0], 1)

    def test_an_edited_order_is_not_ready(self):
        self.run_risk()
        with open(self.orders, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["orders"][0]["qty"] = "14"
        with open(self.orders, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("was not in the last risk check", text)

    def test_last_sessions_check_does_not_count(self):
        self.run_risk(now="2026-11-20 09:46")
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("not today", text)

    def test_blocked_orders_are_never_ready(self):
        for now, why in (("2026-11-23 09:20", "window: no new entries before 09:30 IST"),
                         ("2026-11-24 10:00", "window: the market is closed today (NSE holiday: Guru Nanak Jayanti")):
            self.assertEqual(self.run_risk(now=now), 1)
            code, text, _ = run(self.args("show", "--index", "1", now=now))
            self.assertEqual(code, 1)
            self.assertIn("BLOCKED by trader-risk: " + why, text)
            code, _, _ = run(self.args("approve", "--index", "1", "--reply", "yes", now=now))
            self.assertEqual(code, 1)

    def test_result_needs_an_approval_and_decline_is_logged(self):
        self.run_risk()
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed"))
        self.assertEqual(code, 1)
        self.assertIn("no yes recorded", err)
        code, text, _ = run(self.args("decline", "--index", "1", "--reply", "no thanks"))
        self.assertEqual(code, 0)
        self.assertIn("Nothing was placed", text)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: declined", text)

    def test_show_json(self):
        self.run_risk()
        code, text, _ = run(self.args("show", "--index", "1", "--json"))
        self.assertEqual(code, 0)
        t = json.loads(text)
        self.assertEqual((t["ready"], t["max_loss"], t["stop"], t["flat_by"], t["broker"]),
                         (True, 362.25, 2419.35, "15:00", "groww"))
        self.assertEqual(t["lines"], TCS_TICKET)
        self.assertEqual(t["placed_by"], "the user, by hand, in their own broker app")
        self.assertNotIn("tool_input", t)
        self.assertNotIn("tool", t)

    def test_fill_sell_relieves_the_oldest_lot(self):
        pf = {"cash": 0, "positions": [{"ticker": "ITC", "shares": 30, "lots": [
            {"id": "ITC-1", "date": "2025-01-02", "shares": 20, "cost": 6000.0},
            {"id": "ITC-2", "date": "2026-01-02", "shares": 10, "cost": 3100.0}]}]}
        with open(self.pf, "w", encoding="utf-8") as fh:
            json.dump(pf, fh)
        code, text, _ = run(["fill", "--symbol", "ITC.NS", "--side", "sell", "--qty", "25", "--price", "301",
                             "--date", TODAY, "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 0)
        with open(self.pf, encoding="utf-8") as fh:
            pf = json.load(fh)
        self.assertEqual(pf["cash"], 7525.0)
        lots = pf["positions"][0]["lots"]
        self.assertEqual([l["id"] for l in lots], ["ITC-2"])
        self.assertEqual(lots[0]["shares"], 5)
        self.assertEqual(lots[0]["cost"], 1550.0)
        last = self.state_doc()["orders"][-1]
        self.assertEqual((last["status"], last["symbol"]), ("filled", "ITC"))
        for qty in ("50", "2.5"):
            code, _, err = run(["fill", "--symbol", "ITC", "--side", "sell", "--qty", qty, "--price", "301",
                                "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
            self.assertEqual(code, 1, qty)
            self.assertNotIn("Traceback", err)

    def test_the_skill_md_example_is_the_real_output(self):
        # every example line in SKILL.md is real output of the tool on the fixture order
        with open(os.path.join(ROOT, "trader-broker", "SKILL.md"), encoding="utf-8") as fh:
            skill = fh.read()
        flat = " ".join(skill.split())
        self.run_risk()
        code, text, _ = run(self.args("show"))
        for line in text.splitlines()[:11]:
            self.assertIn(line + "\n", skill, line)
        _, text, _ = run(self.args("approve", "--index", "1", "--reply", "sure"))
        self.assertIn(text.strip() + "\n", skill)
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        _, text, _ = run(["result", "--index", "1", "--status", "placed", "--filled-qty", "15", "--filled-price",
                          "2442.90", "--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                          "--state", self.state, "--portfolio", os.path.join(self.d, "trader", "portfolio.json"),
                          "--now", "2026-11-23 09:47"])
        for line in text.splitlines():
            self.assertIn(line.replace(self.d + os.sep + "trader" + os.sep, "trader/") + "\n", skill, line)
        _, text, _ = run(["fill", "--symbol", "TCS", "--side", "sell", "--qty", "15", "--price", "2467.65",
                          "--portfolio", os.path.join(self.d, "trader", "portfolio.json"), "--config", self.cfg,
                          "--state", self.state, "--now", "2026-11-23 10:06"])
        self.assertIn(text.strip().replace(self.d + os.sep + "trader" + os.sep, "trader/") + "\n", skill)
        self.set_cfg(paper=False)
        os.remove(self.state)
        self.run_risk()
        _, text, _ = run(self.args("show"))
        self.assertIn(text.splitlines()[0], flat)
        self.assertIn("Do you want to place this order yourself? Reply yes or no.", flat)


if __name__ == "__main__":
    unittest.main()
