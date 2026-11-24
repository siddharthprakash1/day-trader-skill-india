"""The circuit breaker in trader-risk/check.py: offline, no network, a pinned India clock."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

check = load("trader-risk", "check")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
TCS_PATH = os.path.join(FIX, "trader-risk", "orders-tcs.json")
JOURNAL_PATH = os.path.join(FIX, "trader-risk", "journal-breaker.json")   # made with journal.py, real prices
TODAY = "2026-11-23"            # a Monday; Tuesday the 24th is an NSE holiday
NOW = "2026-11-23 11:00"
RUPEE = "₹"


def at(text):
    return check.parse_now(text)


def cfg(**over):
    with open(CFG_PATH, encoding="utf-8") as fh:
        c = json.load(fh)
    c.update(over)
    return c


def only(rule, **over):
    """The example config with every breaker rule off except `rule`."""
    c = cfg(max_daily_loss_inr=0, loss_streak_limit=0, reentry_cooldown_minutes=0,
            block_reentry_after_stop=False, max_weekly_loss_inr=None)
    c.update({"daily": {"max_daily_loss_inr": 1500}, "streak": {"loss_streak_limit": 3},
              "reentry": {"reentry_cooldown_minutes": 30}, "stop": {"block_reentry_after_stop": True},
              "weekly": {"max_weekly_loss_inr": 1000}}[rule])
    c.update(over)
    return c


def tcs(**over):
    with open(TCS_PATH, encoding="utf-8") as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def jt(sym, net=None, exit_time="10:00", date=TODAY, outcome=None, paper=True, entry_time="09:45", **over):
    """One journal trade the way trader-journal writes it. net=None leaves it open (not scored)."""
    t = {"id": "%s-%s-%s" % (sym, date, entry_time.replace(":", "")), "symbol": sym, "date": date, "side": "buy",
         "entry_time": entry_time, "entry": 1000.0, "stop": 960.0, "target": 1040.0, "qty": 10,
         "flat_by": "15:00", "setup": "orb", "paper": paper, "risk_per_share": 40.0, "source": "flags",
         "status": "open"}
    if net is not None:
        t["status"] = "scored"
        t["result"] = {"basis": "as filled", "outcome": outcome or ("flat" if net >= 0 else "stop"),
                       "exit_price": 960.0, "exit_time": exit_time, "r": -1.0, "net_r": -1.1, "gross": net + 60.0,
                       "charges": {"total": 60.0}, "net": net, "scored_at": date + "T05:00:00Z"}
    t.update(over)
    return t


def placed(sym, max_loss=362.25, day=TODAY, paper=True):
    return {"symbol": sym, "side": "buy", "status": "placed", "kind": "entry", "date": day,
            "at": day + "T09:50:00+05:30", "max_loss": max_loss, "paper": paper}


def state(journal, c=None, now=NOW, log=None):
    return check.breaker_state(c or cfg(), journal, log, at(now))


def rules(b):
    return [t["rule"] for t in b["tripped"]]


def run(order, journal, c=None, now=NOW, **kw):
    return check.check([order], c or cfg(), now=at(now), journal=journal, **kw)


def why(res, i=0):
    return " | ".join(res["results"][i]["reasons"])


def cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = check.main(argv)
    return code, out.getvalue(), err.getvalue()


class Defaults(unittest.TestCase):
    def test_the_thresholds_are_config_keys_with_defaults(self):
        want = {"circuit_breaker": True, "loss_streak_limit": 3, "loss_streak_cooldown_minutes": None,
                "reentry_cooldown_minutes": 30, "block_reentry_after_stop": True, "max_weekly_loss_inr": None}
        for k, v in want.items():
            self.assertEqual(check.DEFAULTS[k], v, k)
        b = state([jt("INFY", -100.0)], {"budget": 50000})        # a config with none of the keys
        self.assertEqual((b["streak_limit"], b["reentry_cooldown_minutes"], b["weekly_limit"], b["daily_limit"]),
                         (3, 30, None, 1500))
        self.assertIs(b["block_reentry_after_stop"], True)

    def test_a_bad_threshold_is_an_error_not_a_guess(self):
        for key, bad in (("loss_streak_limit", "three"), ("loss_streak_limit", 2.5), ("reentry_cooldown_minutes", -5),
                         ("max_weekly_loss_inr", True), ("loss_streak_cooldown_minutes", "soon")):
            with self.assertRaises(check.RiskError) as cm:
                state([], cfg(**{key: bad}))
            self.assertIn(key, str(cm.exception))

    def test_the_whole_breaker_can_be_switched_off(self):
        losses = [jt(s, -600.0, "10:5%d" % i) for i, s in enumerate(("INFY", "ITC", "SBIN"))]
        self.assertIn("breaker:", why(run(tcs(), losses)))
        res = run(tcs(), losses, cfg(circuit_breaker=False))
        self.assertEqual(res["results"][0]["verdict"], "PASS")
        self.assertIsNone(res["breaker"])
        self.assertNotIn("Circuit breaker", check.report(res))


class NoHistory(unittest.TestCase):
    def test_a_missing_or_empty_journal_passes_and_says_so(self):
        for journal in (None, [], {"trades": []}):
            b = state(journal)
            self.assertIs(b["history"], False)
            self.assertEqual((b["tripped"], b["scored"], b["streak"], b["daily_left"]), ([], 0, 0, 1500.0))
            self.assertEqual(check.breaker_line(b), "Circuit breaker (practice trades): no practice trades in the "
                                                    "journal yet, so no history to count. It passes.")
            self.assertIn("no history to count, so it passes", check.breaker_report(b))
            res = run(tcs(), journal)
            self.assertEqual(res["results"][0]["verdict"], "PASS", res["results"][0]["reasons"])

    def test_the_old_call_without_a_journal_still_works(self):
        res = check.check([tcs()], cfg(), now=at("2026-11-23 09:46"))
        self.assertEqual((res["results"][0]["verdict"], res["results"][0]["reasons"]), ("PASS", []))
        self.assertEqual(res["breaker"]["tripped"], [])


class DailyLoss(unittest.TestCase):
    def test_exactly_at_the_limit_trips_and_a_paisa_under_does_not(self):
        at_limit = [jt("INFY", -500.0, "09:50"), jt("ITC", -500.0, "10:05"), jt("SBIN", -500.0, "10:20")]
        b = state(at_limit, only("daily"))
        self.assertEqual(rules(b), ["daily loss"])
        self.assertEqual(b["tripped"][0]["reason"],
                         "daily loss: %s1,500 lost today after charges (3 scored practice trades), at or over your "
                         "%s1,500 daily loss limit. No new entries today" % (RUPEE, RUPEE))
        self.assertEqual((b["today"]["realised_loss"], b["daily_left"]), (1500.0, 0.0))
        self.assertEqual(b["resume"], "2026-11-25 09:15")          # the 24th is an NSE holiday
        res = run(tcs(), at_limit, only("daily"))
        self.assertEqual(res["results"][0]["verdict"], "BLOCK")
        self.assertIn("breaker: daily loss: %s1,500 lost today after charges" % RUPEE, why(res))
        under = at_limit[:2] + [jt("SBIN", -499.99, "10:20")]
        b = state(under, only("daily"))
        self.assertEqual((rules(b), b["today"]["realised_loss"], b["daily_left"]), ([], 1499.99, 0.01))

    def test_wins_offset_losses_and_other_days_do_not_count(self):
        day = [jt("INFY", -900.0, "09:50"), jt("ITC", -900.0, "10:05"), jt("SBIN", 400.0, "10:20"),
               jt("LT", -5000.0, "10:00", date="2026-11-20")]
        b = state(day, only("daily"))
        self.assertEqual((rules(b), b["today"]["net"], b["today"]["realised_loss"]), ([], -1400.0, 1400.0))
        self.assertEqual((b["today"]["scored"], b["today"]["wins"], b["today"]["losses"]), (3, 1, 2))
        self.assertEqual(state([jt("INFY", 800.0)], only("daily"))["today"]["realised_loss"], 0.0)

    def test_the_real_loss_plus_this_order_over_the_limit(self):
        # one planned 362.25 loss really cost 450 after charges; the next 362.25 would make 812.25
        lost = [jt("INFY", -450.0, "09:50", outcome="flat")]
        log = {"orders": [placed("INFY")]}
        c = only("daily", max_daily_loss_inr=800, max_position_pct=100, cash_buffer_pct=0)
        res = run(tcs(), lost, c, state=log)
        self.assertEqual(res["results"][0]["reasons"],
                         ["breaker: daily loss: %s450 lost today after charges plus %s0 still at risk in open trades "
                          "plus %s362.25 for this one = %s812.25, over your %s800 daily loss limit"
                          % (RUPEE, RUPEE, RUPEE, RUPEE, RUPEE)])
        res = run(tcs(), lost, dict(c, max_daily_loss_inr=812.25), state=log)
        self.assertEqual(res["results"][0]["verdict"], "PASS", res["results"][0]["reasons"])   # exactly at: fits

    def test_the_planned_risk_rule_is_not_said_twice(self):
        log = {"orders": [placed("INFY"), placed("ITC")]}
        res = run(tcs(), [jt("INFY", -450.0, "09:50", outcome="flat")], only("daily", max_daily_loss_inr=1000),
                  state=log)
        text = why(res)
        self.assertIn("daily loss: %s724.50 already at risk today plus %s362.25" % (RUPEE, RUPEE), text)
        self.assertNotIn("breaker:", text)


class LosingStreak(unittest.TestCase):
    ACROSS = [jt("INFY", -300.0, "10:00", date="2026-11-19"), jt("ITC", -300.0, "11:00", date="2026-11-20"),
              jt("SBIN", -300.0, "10:10")]

    def test_three_losses_across_days_block_the_rest_of_the_day(self):
        b = state(self.ACROSS, only("streak"))
        self.assertEqual((rules(b), b["streak"]), (["losing streak"], 3))
        self.assertEqual(b["tripped"][0]["reason"],
                         "losing streak: your last 3 scored practice trades all lost (INFY 2026-11-19, ITC "
                         "2026-11-20, SBIN 2026-11-23). No new entries for the rest of today")
        self.assertEqual((b["tripped"][0]["until"], b["resume"]), ("2026-11-23 15:30", "2026-11-25 09:15"))
        for now in ("2026-11-23 10:10", "2026-11-23 14:30"):
            res = run(tcs(), self.ACROSS, only("streak"), now=now)
            self.assertIn("breaker: losing streak:", why(res), now)

    def test_the_next_session_is_open_again_until_one_more_loss(self):
        b = state(self.ACROSS, only("streak"), now="2026-11-25 10:00")
        self.assertEqual((rules(b), b["streak"]), ([], 3))
        self.assertEqual(b["streak_note"], "the cooldown ended at the close on Mon 2026-11-23; one more loss trips "
                                           "it again")
        self.assertEqual(run(tcs(), self.ACROSS, only("streak"), now="2026-11-25 10:00")["results"][0]["verdict"],
                         "PASS")
        four = self.ACROSS + [jt("LT", -300.0, "09:50", date="2026-11-25")]
        self.assertEqual(rules(state(four, only("streak"), now="2026-11-25 10:00")), ["losing streak"])

    def test_a_win_or_a_zero_ends_the_streak(self):
        for mid in (250.0, 0.0):
            rows = [jt("INFY", -300.0, "09:40"), jt("ITC", -300.0, "09:50"), jt("LT", mid, "10:00"),
                    jt("SBIN", -300.0, "10:10")]
            b = state(rows, only("streak"))
            self.assertEqual((rules(b), b["streak"]), ([], 1), mid)
        two = [jt("INFY", -300.0, "09:40"), jt("ITC", -300.0, "09:50")]
        self.assertEqual(state(two, only("streak"))["streak"], 2)
        self.assertEqual(rules(state(two, only("streak", loss_streak_limit=2))), ["losing streak"])
        self.assertEqual(rules(state(self.ACROSS, only("streak", loss_streak_limit=0))), [])

    def test_a_cooldown_in_minutes_ends_exactly_on_the_minute(self):
        c = only("streak", loss_streak_cooldown_minutes=120)           # last losing exit 10:10 -> 12:10
        b = state(self.ACROSS, c, now="2026-11-23 12:09")
        self.assertEqual((rules(b), b["tripped"][0]["until"], b["resume"]),
                         (["losing streak"], "2026-11-23 12:10", "2026-11-23 12:10"))
        self.assertIn("No new entries until 12:10 IST today (120 minutes after the last losing exit at 10:10)",
                      b["tripped"][0]["reason"])
        b = state(self.ACROSS, c, now="2026-11-23 12:10")
        self.assertEqual(rules(b), [])
        self.assertIn("the cooldown ended at 12:10 IST today", b["streak_note"])
        self.assertIn("breaker: losing streak", why(run(tcs(), self.ACROSS, c, now="2026-11-23 12:09")))
        self.assertEqual(run(tcs(), self.ACROSS, c, now="2026-11-23 12:10")["results"][0]["verdict"], "PASS")

    def test_a_long_cooldown_carries_into_the_next_session(self):
        c = only("streak", loss_streak_cooldown_minutes=2880)          # two days after 10:10 on the 23rd
        b = state(self.ACROSS, c, now="2026-11-25 10:09")
        self.assertEqual(rules(b), ["losing streak"])
        self.assertIn("until 10:10 IST today", b["tripped"][0]["reason"])
        self.assertEqual(rules(state(self.ACROSS, c, now="2026-11-25 10:10")), [])


class ReEntry(unittest.TestCase):
    LOSS = [jt("INFY", -354.04, "10:30", outcome="flat")]

    def test_inside_the_cooldown_blocks_and_exactly_at_its_end_passes(self):
        b = state(self.LOSS, only("reentry"), now="2026-11-23 10:59")
        self.assertEqual(rules(b), ["re-entry"])
        self.assertEqual(b["tripped"][0]["reason"],
                         "re-entry: the last losing exit (INFY, -%s354.04 after charges) was at 10:30 IST today, "
                         "29 minutes ago. No new entries until 11:00 IST today (30 minutes after a loss)" % RUPEE)
        self.assertEqual(b["resume"], "2026-11-23 11:00")
        self.assertIn("breaker: re-entry:", why(run(tcs(), self.LOSS, only("reentry"), now="2026-11-23 10:59")))
        b = state(self.LOSS, only("reentry"), now="2026-11-23 11:00")
        self.assertEqual((rules(b), b["last_loss"]["minutes_ago"]), ([], 30))
        self.assertEqual(run(tcs(), self.LOSS, only("reentry"), now="2026-11-23 11:00")["results"][0]["verdict"],
                         "PASS")

    def test_the_minutes_are_configurable_and_zero_is_off(self):
        self.assertEqual(rules(state(self.LOSS, only("reentry", reentry_cooldown_minutes=45), now="2026-11-23 11:14")),
                         ["re-entry"])
        self.assertEqual(rules(state(self.LOSS, only("reentry", reentry_cooldown_minutes=45), now="2026-11-23 11:15")),
                         [])
        self.assertEqual(rules(state(self.LOSS, only("reentry", reentry_cooldown_minutes=0), now="2026-11-23 10:31")),
                         [])

    def test_a_winning_exit_starts_no_cooldown(self):
        self.assertEqual(rules(state([jt("INFY", 301.87, "10:55")], only("reentry"))), [])

    def test_an_exit_with_no_time_uses_when_it_was_scored(self):
        t = jt("INFY", -354.04, None, outcome="flat")
        t["result"]["scored_at"] = "2026-11-23T05:15:00Z"              # 10:45 IST
        b = state([t], only("reentry"))
        self.assertEqual((rules(b), b["last_loss"]["time"], b["last_loss"]["exact_time"]),
                         (["re-entry"], "10:45", False))
        self.assertIn("has no exit time in the journal", " ".join(b["notes"]))

    def test_a_second_entry_in_a_stopped_out_symbol_is_blocked_that_day(self):
        stopped = [jt("TCS", -422.43, "10:05", outcome="stop")]
        c = only("stop")
        b = state(stopped, c)
        self.assertEqual((rules(b), b["stopped_out_today"]), ([], {"TCS": {"time": "10:05", "net": -422.43}}))
        res = run(tcs(), stopped, c)
        self.assertEqual(res["results"][0]["reasons"],
                         ["breaker: re-entry: TCS was stopped out today at 10:05 IST (-%s422.43 after charges). No "
                          "second entry in TCS today" % RUPEE])
        other = dict(tcs(), symbol="INFY")
        self.assertEqual(run(other, stopped, c)["results"][0]["verdict"], "PASS")       # another symbol is fine
        self.assertEqual(run(tcs(), stopped, only("stop", block_reentry_after_stop=False))["results"][0]["verdict"],
                         "PASS")
        self.assertEqual(run(tcs(), stopped, c, now="2026-11-25 11:00")["results"][0]["verdict"], "PASS")  # next day
        flat = [jt("TCS", -120.0, "10:05", outcome="flat")]                             # a loss, but not a stop-out
        self.assertEqual(run(tcs(), flat, c)["results"][0]["verdict"], "PASS")


class WeeklyLoss(unittest.TestCase):
    WEEK = [jt("LT", -5000.0, "10:00", date="2026-11-20", outcome="flat"),            # last week: not counted
            jt("INFY", -600.0, "10:00", date="2026-11-23", outcome="flat"),
            jt("ITC", -400.0, "09:40", date="2026-11-25", outcome="flat")]
    WED = "2026-11-25 11:00"

    def test_it_is_off_unless_configured(self):
        b = state(self.WEEK, cfg(loss_streak_limit=0), now=self.WED)
        self.assertEqual((rules(b), b["weekly_limit"], b["weekly_left"]), ([], None, None))
        self.assertIn("Weekly loss limit: off", check.breaker_report(b))

    def test_exactly_at_the_weekly_limit_trips(self):
        b = state(self.WEEK, only("weekly"), now=self.WED)
        self.assertEqual(rules(b), ["weekly loss"])
        self.assertEqual(b["tripped"][0]["reason"],
                         "weekly loss: %s1,000 lost this week after charges (2 scored practice trades since Mon "
                         "2026-11-23), at or over your %s1,000 weekly loss limit. No new entries until Mon 2026-11-30"
                         % (RUPEE, RUPEE))
        self.assertEqual((b["week"]["from"], b["week"]["net"], b["resume"]), ("2026-11-23", -1000.0, "2026-11-30 09:15"))
        self.assertIn("breaker: weekly loss:", why(run(tcs(), self.WEEK, only("weekly"), now=self.WED)))
        b = state(self.WEEK, only("weekly", max_weekly_loss_inr=1000.01), now=self.WED)
        self.assertEqual((rules(b), b["weekly_left"]), ([], 0.01))
        self.assertEqual(run(tcs(), self.WEEK, only("weekly", max_weekly_loss_inr=1000.01),
                             now=self.WED)["results"][0]["verdict"], "PASS")


class PracticeAndReal(unittest.TestCase):
    REAL = [jt(s, -600.0, "10:4%d" % i, paper=False) for i, s in enumerate(("INFY", "ITC", "SBIN"))]

    def test_each_mode_counts_only_its_own_trades(self):
        b = state(self.REAL)                                           # "paper": true: practice is counted
        self.assertEqual((b["mode"], b["history"], b["other_mode_trades"], b["tripped"]), ("practice", False, 3, []))
        self.assertEqual(run(tcs(), self.REAL)["results"][0]["verdict"], "PASS")
        b = state(self.REAL, cfg(paper=False))
        self.assertEqual((b["mode"], b["scored"], b["streak"]), ("real", 3, 3))
        self.assertEqual(rules(b), ["daily loss", "losing streak", "re-entry"])
        res = run(tcs(), self.REAL, cfg(paper=False))
        self.assertIn("breaker: losing streak: your last 3 scored real trades all lost", why(res))
        practice = [dict(t, paper=True) for t in self.REAL]
        self.assertEqual(run(tcs(), practice, cfg(paper=False))["results"][0]["verdict"], "PASS")

    def test_the_output_names_the_mode(self):
        text = check.breaker_report(state(self.REAL))
        self.assertIn("practice trades: clear", text)
        self.assertIn("3 real trades in the journal are not counted here", text)
        self.assertIn("It counts practice trades only, because \"paper\" is true in the config.", text)
        text = check.breaker_report(state(self.REAL, cfg(paper=False)))
        self.assertIn("real trades: TRIPPED, no new entries", text)
        self.assertIn("It counts real trades only, because \"paper\" is false in the config.", text)
        res = run(tcs(), self.REAL, cfg(paper=False))
        self.assertIn("Circuit breaker (real trades): TRIPPED, new entries are blocked (daily loss, losing streak, "
                      "re-entry). Detail: check.py breaker.", check.report(res))


class Unscored(unittest.TestCase):
    def test_an_open_trade_is_neither_a_win_nor_a_loss(self):
        rows = [jt("INFY", -300.0, "09:40"), jt("ITC", -300.0, "09:50"), jt("SBIN"),               # open, today
                jt("LT", date="2026-11-20")]                                                         # open, older
        b = state(rows, only("streak"))
        self.assertEqual((b["streak"], b["scored"], b["unscored"], b["unscored_today"]), (2, 2, 2, 1))
        self.assertEqual(rules(b), [])
        text = check.breaker_report(b)
        self.assertIn("4 practice trades: 2 scored, 2 not scored yet (1 from today; neither a win nor a loss until "
                      "scored)", text)
        self.assertIn("2 not scored yet", check.breaker_line(b))

    def test_todays_open_trades_count_at_their_planned_max_loss(self):
        rows = [jt("INFY", -500.0, "09:50", outcome="flat"), jt("SBIN")]            # SBIN open: 10 x (1000 - 960)
        b = state(rows, only("daily"))
        self.assertEqual((b["today"]["open_trades"], b["today"]["open_risk"], b["daily_left"]), (1, 400.0, 600.0))
        self.assertIn("%s400 planned max loss in 1 open trade today, before charges" % RUPEE,
                      check.breaker_report(b))

    def test_broker_log_entries_the_journal_does_not_hold(self):
        log = {"orders": [placed("INFY"), placed("ITC", max_loss=200.0), placed("LT", day="2026-11-20"),
                          placed("SBIN", paper=False),
                          {"symbol": "ITC", "side": "sell", "status": "filled", "date": TODAY, "qty": "10",
                           "price": 300.0, "at": TODAY + "T10:20:00+05:30"}]}
        b = state([jt("INFY", -500.0, "09:55", outcome="flat")], only("daily"), log=log)
        # INFY is in the journal (scored): not counted twice. ITC is only in the log: its max loss counts.
        self.assertEqual((b["today"]["open_trades"], b["today"]["open_risk"], b["daily_left"]), (1, 200.0, 800.0))
        self.assertEqual(len(b["notes"]), 1)
        self.assertIn("the broker log has a sell fill in ITC today that the journal has not scored", b["notes"][0])
        b = state(None, only("daily"), log=log)                        # no journal at all: the log still counts
        self.assertEqual((b["history"], b["today"]["open_risk"], b["daily_left"]), (False, 562.25, 937.75))

    def test_a_trade_that_ended_after_now_is_not_counted(self):
        rows = [jt("INFY", -300.0, "09:40"), jt("ITC", -300.0, "09:50"), jt("SBIN", -300.0, "11:30"),
                jt("LT", -300.0, "10:00", date="2026-11-25")]
        b = state(rows, only("streak"))
        self.assertEqual((b["streak"], b["scored"], rules(b)), (2, 2, []))
        self.assertIn("2 scored trades in the journal ended after 2026-11-23 11:00 IST: not counted", b["notes"])
        self.assertEqual(rules(state(rows, only("streak"), now="2026-11-23 11:30")), ["losing streak"])


class ExitsAreNeverBlocked(unittest.TestCase):
    PF = {"cash": 13356.5, "positions": [{"ticker": "TCS", "shares": 15,
                                           "lots": [{"date": TODAY, "shares": 15, "cost": 36643.5}]}]}
    SELL = {"symbol": "TCS", "side": "sell", "qty": "15", "type": "market", "time_in_force": "day"}
    TRIPPED = [jt("INFY", -900.0, "10:40"), jt("ITC", -900.0, "10:50"), jt("TCS", -900.0, "10:58")]

    def test_a_sell_that_closes_passes_with_every_rule_tripped(self):
        c = cfg(max_weekly_loss_inr=1000)
        b = state(self.TRIPPED, c)
        self.assertEqual(rules(b), ["daily loss", "weekly loss", "losing streak", "re-entry"])
        self.assertIn("TCS", b["stopped_out_today"])
        res = check.check([self.SELL, tcs(symbol="SBIN")], c, portfolio=self.PF, now=at(NOW), journal=self.TRIPPED)
        sell, buy = res["results"]
        self.assertEqual((sell["verdict"], sell["kind"], sell["reasons"]), ("PASS", "exit", []))
        self.assertNotIn("breaker", " ".join(sell["warnings"]))
        self.assertEqual((buy["verdict"], buy["kind"]), ("BLOCK", "entry"))
        self.assertEqual(len([r for r in buy["reasons"] if r.startswith("breaker: ")]), 4)
        self.assertIn("Getting out is never blocked.", check.breaker_report(b))


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.join(self.tmp.name, "trader")
        os.makedirs(self.d)
        self.cfg = os.path.join(self.d, "config.json")
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump(cfg(), fh)
        self.journal = os.path.join(self.d, "journal.json")
        self.none = os.path.join(self.d, "none.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, trades, path=None):
        with open(path or self.journal, "w", encoding="utf-8") as fh:
            json.dump({"created_by": "trader-journal", "trades": trades}, fh)

    def breaker(self, *extra, **kw):
        return cli(["breaker", "--config", self.cfg, "--state", self.none, "--now", kw.get("now", NOW)] + list(extra))

    def test_no_journal_file(self):
        code, text, err = self.breaker()
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Circuit breaker at 2026-11-23 11:00 IST (Mon), practice trades: clear\n", text)
        self.assertIn("  Journal            no practice trades in the journal: no history to count, so it passes\n",
                      text)
        self.assertIn("  Daily loss limit   %s1,500: %s1,500 left\n" % (RUPEE, RUPEE), text)
        self.assertIn("  Tripped            none\n", text)
        self.assertIn("not financial advice", text)
        self.assertNotIn("$", text)

    def test_the_status_of_a_tripped_breaker(self):
        self.write([jt("INFY", -300.0, "10:00", date="2026-11-19"), jt("ITC", -300.0, "11:00", date="2026-11-20"),
                    jt("SBIN", -422.43, "10:45"), jt("LT")])
        code, text, _ = self.breaker()                                 # the journal next to the config is found
        self.assertEqual(code, 1)
        for line in ("Circuit breaker at 2026-11-23 11:00 IST (Mon), practice trades: TRIPPED, no new entries",
                     "  Realised today     -%s422.43 after charges (1 scored: 0 won, 1 lost)" % RUPEE,
                     "  Still at risk      %s400 planned max loss in 1 open trade today, before charges" % RUPEE,
                     "  Daily loss limit   %s1,500: %s677.57 left" % (RUPEE, RUPEE),
                     "  This week          -%s422.43 after charges since Mon 2026-11-23 (1 scored). Weekly loss "
                     "limit: off" % RUPEE,
                     "  Losing streak      3 in a row (limit 3)",
                     "  Last losing exit   SBIN at 10:45 IST today (-%s422.43), 15 minutes ago" % RUPEE,
                     "  Stopped out today  SBIN (10:45)",
                     "  Tripped            2 rules",
                     "    - losing streak: your last 3 scored practice trades all lost (INFY 2026-11-19, ITC "
                     "2026-11-20, SBIN 2026-11-23). No new entries for the rest of today.",
                     "    - re-entry: the last losing exit (SBIN, -%s422.43 after charges) was at 10:45 IST today, "
                     "15 minutes ago. No new entries until 11:15 IST today (30 minutes after a loss)." % RUPEE,
                     "  New entries again  from 09:15 IST on Wed 2026-11-25, inside your entry window",
                     "A circuit breaker is discipline: it caps what a bad run can cost. It does not make a trade "
                     "win or improve returns."):
            self.assertIn(line + "\n", text, line)

    def test_symbol_json_and_mode_flags(self):
        self.write([jt("TCS", -422.43, "10:05"), jt("INFY", -100.0, "10:10", paper=False)])
        code, text, _ = self.breaker("--symbol", "tcs.ns")
        self.assertEqual(code, 1)
        self.assertIn("practice trades: clear for the account, but no new entry in TCS\n", text)
        self.assertIn("    - re-entry: TCS was stopped out today at 10:05 IST", text)
        self.assertEqual(self.breaker("--symbol", "INFY")[0], 0)
        code, text, _ = self.breaker("--json", "--symbol", "TCS")
        doc = json.loads(text)
        self.assertEqual((code, doc["mode"], doc["symbol"], len(doc["reasons"]), doc["tripped"]),
                         (1, "practice", "TCS", 1, []))
        self.assertEqual(doc["today"]["net"], -422.43)
        code, text, _ = self.breaker("--real", "--json")
        doc = json.loads(text)
        self.assertEqual((code, doc["mode"], doc["today"]["net"], doc["other_mode_trades"]), (0, "real", -100.0, 1))

    def test_it_is_read_only(self):
        self.write([jt("SBIN", -422.43, "10:45")])
        with open(self.journal, "rb") as fh:
            before = fh.read()
        listing = sorted(os.listdir(self.d))
        self.breaker()
        self.breaker("--json")
        with open(self.journal, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(sorted(os.listdir(self.d)), listing)

    def test_errors_are_one_line(self):
        with open(self.journal, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        code, text, err = self.breaker()
        self.assertEqual((code, text), (1, ""))
        self.assertIn("is not valid JSON", err)
        self.assertNotIn("Traceback", err)
        with open(self.journal, "w", encoding="utf-8") as fh:
            json.dump({"orders": []}, fh)
        code, _, err = self.breaker()
        self.assertEqual(code, 1)
        self.assertIn("is not a trader-journal file", err)
        code, _, err = self.breaker(now="noon")
        self.assertIn("--now wants", err)

    def test_the_check_reads_the_journal_and_blocks(self):
        self.write([jt("INFY", -354.04, "10:45", outcome="flat")])
        out = os.path.join(self.d, "state", "risk.json")
        argv = ["--orders", TCS_PATH, "--config", self.cfg, "--portfolio", self.none, "--prices", self.none,
                "--state", self.none, "--out", out]
        code, text, _ = cli(argv + ["--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("BLOCK #1 buy TCS 15 shares, entry\n", text)
        self.assertIn("        - breaker: re-entry: the last losing exit (INFY, -%s354.04 after charges) was at 10:45 "
                      "IST today, 15 minutes ago. No new entries until 11:15 IST today (30 minutes after a loss)\n"
                      % RUPEE, text)
        self.assertIn("Circuit breaker (practice trades): TRIPPED, new entries are blocked (re-entry). Detail: "
                      "check.py breaker.\n", text)
        with open(out, encoding="utf-8") as fh:
            rec = json.load(fh)
        self.assertEqual((rec["block"], rec["breaker"]["tripped"][0]["rule"], rec["breaker"]["mode"]),
                         (1, "re-entry", "practice"))
        code, text, _ = cli(argv + ["--now", "2026-11-23 11:15"])       # exactly at the cooldown's end
        self.assertEqual(code, 0)
        self.assertIn("Circuit breaker (practice trades): clear. Today -%s354.04 after charges, %s1,145.96 of the "
                      "%s1,500 daily loss limit left, losing streak 1 of 3.\n" % (RUPEE, RUPEE, RUPEE), text)
        other = os.path.join(self.d, "elsewhere.json")                  # --journal points somewhere else
        self.write([], other)
        code, text, _ = cli(argv + ["--now", NOW, "--journal", other])
        self.assertEqual(code, 0)
        self.assertIn("no history to count. It passes.", text)


class SkillExample(unittest.TestCase):
    """The circuit breaker example in SKILL.md is the real output on the fixture journal."""

    def setUp(self):
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            self.skill = fh.read()
        self.flat = " ".join(self.skill.split())

    def test_the_fixture_journal_is_trader_journals_own_output(self):
        with open(JOURNAL_PATH, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["created_by"], "trader-journal")
        got = [(t["symbol"], t["status"], t["result"]["outcome"], t["result"]["exit_time"], t["result"]["net"])
               for t in doc["trades"]]
        self.assertEqual(got, [("TCS", "scored", "target", "10:07", 301.87), ("INFY", "scored", "stop", "10:57", -354.04)])

    def test_the_status_block(self):
        code, text, _ = cli(["breaker", "--config", CFG_PATH, "--journal", JOURNAL_PATH, "--state", "/nonexistent",
                             "--now", "2026-11-23 11:10"])
        self.assertEqual(code, 1)
        lines = text.splitlines()
        self.assertEqual(len(lines), 15)
        for line in lines:
            self.assertIn(line + "\n", self.skill, line)

    def test_the_blocked_order_and_the_lines_quoted_in_the_text(self):
        argv = ["--orders", TCS_PATH, "--config", CFG_PATH, "--journal", JOURNAL_PATH, "--portfolio", "/nonexistent",
                "--prices", "/nonexistent", "--state", "/nonexistent", "--no-write"]
        code, text, _ = cli(argv + ["--now", "2026-11-23 11:10"])
        self.assertEqual(code, 1)
        lines = [l for l in text.splitlines() if l.strip()]
        for line in [l for l in lines if "breaker" in l] + lines[-4:-2]:
            self.assertIn(line + "\n", self.skill, line)
        code, text, _ = cli(argv + ["--now", "2026-11-23 11:27"])
        self.assertEqual(code, 0)
        self.assertIn([l for l in text.splitlines() if l.startswith("Circuit breaker")][0], self.flat)
        code, text, _ = cli(["breaker", "--config", CFG_PATH, "--journal", JOURNAL_PATH, "--state", "/nonexistent",
                             "--symbol", "INFY", "--now", "2026-11-23 11:30"])
        self.assertEqual(code, 1)
        self.assertIn("clear for the account, but no new entry in INFY", self.flat)
        self.assertIn([l for l in text.splitlines() if l.startswith("    - ")][0].strip(" -"), self.flat)

    def test_the_skill_says_what_it_is_and_is_not(self):
        low = self.flat.lower()
        for words in ("circuit breaker", "it is not an edge", "nothing here claims it improves returns",
                      "loss_streak_limit", "loss_streak_cooldown_minutes", "reentry_cooldown_minutes",
                      "block_reentry_after_stop", "max_weekly_loss_inr", "practice and real are separate"):
            self.assertIn(words, low, words)


if __name__ == "__main__":
    unittest.main()
