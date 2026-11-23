import datetime
import gzip
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

alerts = load("trader-alerts", "alerts")

# Real Yahoo chart answers (range=1d&interval=1d) for TCS.NS, INFY.NS, RELIANCE.NS, M&M.NS and an unknown
# symbol, fetched on 2026-11-24 (an NSE holiday): each holds Monday 2026-11-23's last price, stamped 15:29 IST.
QDIR = os.path.join(FIX, "trader-alerts")
PLAN = os.path.join(QDIR, "plan-tcs.json")     # built by hand from TCS's real 2026-11-23 bars
DT = datetime.datetime
UA = "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)"
RS = chr(0x20b9)

# NSE trading holidays 2026 (NSE circular of 12 Dec 2025)
NSE_2026 = ["2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03", "2026-04-14",
            "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10",
            "2026-11-24", "2026-12-25"]


def ist(text):
    return alerts.parse_now(text)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = alerts.main(argv)
    return code, out.getvalue(), err.getvalue()


def plan_rules():
    with open(PLAN, encoding="utf-8") as fh:
        return alerts.rules_from_plan(json.load(fh))


class Calendar(unittest.TestCase):
    def test_holidays_match_the_published_list(self):
        self.assertEqual(sorted(alerts.NSE_HOLIDAYS), NSE_2026)
        self.assertEqual(alerts.NSE_HOLIDAYS["2026-11-24"], "Guru Nanak Jayanti")
        self.assertEqual(alerts.CALENDAR_YEARS, (2026,))
        for d in alerts.NSE_HOLIDAYS:
            self.assertLess(datetime.date.fromisoformat(d).weekday(), 5, d)
        for gone in ("NYSE_HOLIDAYS", "NYSE_EARLY_CLOSE", "EARLY", "eastern_offset", "to_eastern", "parse_stooq"):
            self.assertFalse(hasattr(alerts, gone), gone)

    def test_india_time_is_a_fixed_five_and_a_half_hours(self):
        self.assertEqual(alerts.to_ist(DT(2026, 11, 23, 3, 45)), DT(2026, 11, 23, 9, 15))
        self.assertEqual(alerts.ist_to_utc(DT(2026, 11, 23, 15, 30)), DT(2026, 11, 23, 10, 0))
        for month in (1, 3, 7, 11):                      # no daylight saving, any month
            self.assertEqual(alerts.to_ist(DT(2026, month, 8, 20, 0)), DT(2026, month, 9, 1, 30))
            self.assertEqual(alerts.ist_to_utc(alerts.to_ist(DT(2026, month, 8, 6, 59))), DT(2026, month, 8, 6, 59))

    def test_parse_now(self):
        self.assertEqual(ist("2026-11-23T09:15"), DT(2026, 11, 23, 3, 45))
        self.assertEqual(ist("2026-11-23 15:00"), DT(2026, 11, 23, 9, 30))
        self.assertEqual(ist("2026-11-23 15:00 IST"), DT(2026, 11, 23, 9, 30))
        self.assertEqual(ist("2026-06-01T09:15"), DT(2026, 6, 1, 3, 45))
        self.assertEqual(ist("2026-11-23T09:59:34Z"), DT(2026, 11, 23, 9, 59, 34))
        self.assertEqual(ist("2026-11-23T15:00:00+05:30"), DT(2026, 11, 23, 9, 30))
        with self.assertRaises(alerts.AlertError):
            ist("tomorrow")

    def test_the_session_is_0915_to_1530(self):
        self.assertEqual((alerts.OPEN, alerts.CLOSE), (datetime.time(9, 15), datetime.time(15, 30)))
        st = alerts.market_status(ist("2026-11-23T10:00"))
        self.assertTrue(st["open"])
        self.assertEqual((st["opens"], st["closes"], st["weekday"], st["now_ist"]),
                         ("09:15", "15:30", "Monday", "2026-11-23 10:00"))
        self.assertNotIn("early_close", st)
        before = alerts.market_status(ist("2026-11-23T09:14"))     # the pre-open session is not the session
        self.assertFalse(before["open"])
        self.assertEqual((before["reason"], before["next_open"]), ("before the open", "2026-11-23 09:15"))
        self.assertTrue(alerts.market_status(ist("2026-11-23T09:15"))["open"])
        self.assertTrue(alerts.market_status(ist("2026-11-23T15:29"))["open"])
        after = alerts.market_status(ist("2026-11-23T15:30"))
        self.assertFalse(after["open"])
        self.assertEqual((after["reason"], after["next_open"]), ("after the close", "2026-11-25 09:15"))

    def test_holidays_and_weekends(self):
        hol = alerts.market_status(ist("2026-11-24T11:00"))
        self.assertFalse(hol["open"])
        self.assertEqual(hol["reason"], "NSE holiday: Guru Nanak Jayanti")
        self.assertEqual(hol["next_open"], "2026-11-25 09:15")
        self.assertNotIn("opens", hol)
        self.assertEqual(alerts.trading_day(datetime.date(2026, 11, 23)), (True, ""))
        friday = alerts.market_status(ist("2026-04-03T11:00"))
        self.assertIn("Good Friday", friday["reason"])
        self.assertEqual(friday["next_open"], "2026-04-06 09:15")
        sunday = alerts.market_status(ist("2026-11-22T10:00"))
        self.assertEqual((sunday["reason"], sunday["next_open"]), ("weekend", "2026-11-23 09:15"))
        self.assertTrue(alerts.market_status(ist("2026-12-24T14:00"))["open"])      # NSE has no early closes
        xmas = alerts.market_status(ist("2026-12-25T10:00"))
        self.assertFalse(xmas["open"])
        self.assertEqual(xmas["next_open"], "2026-12-28 09:15")

    def test_a_year_without_a_holiday_list(self):
        st = alerts.market_status(ist("2027-01-26T10:00"))     # Republic Day 2027: not known here
        self.assertFalse(st["calendar_known"])
        self.assertTrue(st["open"])
        code, text, _ = run(["clock", "--now", "2027-01-26 10:00"])
        self.assertIn("covers 2026 only", text)
        self.assertIn("nseindia.com", text)
        self.assertEqual(alerts.market_status(ist("2027-01-23T10:00"))["reason"], "weekend")
        last = alerts.market_status(ist("2026-12-31T16:00"))
        self.assertEqual((last["next_open"], last["next_open_known"]), ("2027-01-01 09:15", False))
        self.assertIn("covers 2026 only", run(["clock", "--now", "2026-12-31 16:00"])[1])
        self.assertNotIn("covers 2026 only", run(["clock", "--now", "2026-11-23 10:00"])[1])


class Symbols(unittest.TestCase):
    def test_yahoo_symbols(self):
        self.assertEqual(alerts.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(alerts.yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(alerts.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(alerts.yahoo_symbol("TCS.NS"), "TCS.NS")
        self.assertEqual(alerts.yahoo_symbol("500325.BO"), "500325.BO")
        self.assertEqual(alerts.yahoo_symbol("^NSEI"), "^NSEI")
        self.assertEqual(alerts.nse_symbol(" tcs.ns "), "TCS")
        self.assertEqual(alerts.nse_symbol("M&M.NS"), "M&M")

    def test_urls_and_user_agent(self):
        self.assertEqual(alerts.UA, UA)
        self.assertEqual(alerts.yahoo_url("tcs"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/TCS.NS?range=1d&interval=1d")
        self.assertIn("/chart/M%26M.NS?", alerts.yahoo_url("M&M"))
        self.assertIn("/chart/BAJAJ-AUTO.NS?", alerts.yahoo_url("bajaj-auto"))
        self.assertIn("//query1.finance.yahoo.com/", alerts.yahoo_url("TCS", alerts.YAHOO_HOSTS[1]))

    def test_rule_symbols(self):
        for sym in ("RELIANCE", "HDFCBANK", "M&M", "BAJAJ-AUTO", "tcs.ns", "3MINDIA"):
            alerts.validate({"id": "x", "symbol": sym, "type": "above", "price": 1})
        for bad in ("BRK.B", "TCS NS", "A" * 21, "TCS;x"):
            with self.assertRaises(alerts.AlertError, msg=bad) as cm:
                alerts.validate({"id": "x", "symbol": bad, "type": "above", "price": 1})
            self.assertIn("not an NSE symbol", str(cm.exception))


class Quotes(unittest.TestCase):
    def test_yahoo_fixtures(self):
        with open(os.path.join(QDIR, "yahoo-TCS.json"), encoding="utf-8") as fh:
            q = alerts.parse_yahoo(fh.read(), "TCS")
        self.assertEqual((q["symbol"], q["price"], q["prev_close"], q["open"]), ("TCS", 2460.0, 2376.2, 2420.0))
        self.assertEqual((q["high"], q["low"], q["currency"], q["source"]), (2501.9, 2401.0, "INR", "yahoo"))
        self.assertEqual(q["time"], "2026-11-23T09:59:34Z")                  # 15:29:34 IST
        self.assertEqual(alerts._ist_stamp(q["time"]), "2026-11-23 15:29 IST")
        with open(os.path.join(QDIR, "yahoo-M&M.json"), encoding="utf-8") as fh:
            q = alerts.parse_yahoo(fh.read(), "M&M.NS")
        self.assertEqual((q["symbol"], q["price"], q["prev_close"]), ("M&M", 3487.4, 3545.0))

    def test_offline_quotes_and_errors(self):
        q, errors = alerts.get_quotes(["TCS", "infy.ns", "ZZZZQQ", "NOPE"], QDIR)
        self.assertEqual(sorted(q), ["INFY", "TCS"])                         # keys are NSE symbols, no .NS
        self.assertEqual(q["INFY"]["price"], 1103.4)
        self.assertEqual(len(errors), 2)
        self.assertIn("No data found", errors[0])
        self.assertIn("check the NSE symbol", errors[0])
        self.assertIn("no offline quote file", errors[1])

    def test_offline_gz(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(QDIR, "yahoo-TCS.json"), "rb") as src:
                with gzip.open(os.path.join(d, "yahoo-TCS.json.gz"), "wb") as dst:
                    dst.write(src.read())
            q, errors = alerts.get_quotes(["TCS"], d)
            self.assertEqual((q["TCS"]["price"], errors), (2460.0, []))
        finally:
            shutil.rmtree(d)

    def test_rupees(self):
        self.assertEqual(alerts.inr(2460), RS + "2,460.00")
        self.assertEqual(alerts.inr(301.95), RS + "301.95")
        self.assertEqual(alerts.inr(123456.5), RS + "123,456.50")


class FakeNet(object):
    """Stands in for urllib: answers each request with the next status code."""

    def __init__(self, codes, body=None):
        self.codes, self.calls, self.urls, self.sleeps = list(codes), [], [], []
        with open(os.path.join(QDIR, "yahoo-TCS.json"), encoding="utf-8") as fh:
            self.body = body or fh.read()

    def urlopen(self, req, timeout=None):
        self.calls.append((req.full_url.split("/")[2], req.get_header("User-agent")))
        self.urls.append(req.full_url)
        code = self.codes.pop(0)
        if code != 200:
            fp = io.BytesIO(b'{"chart":{"result":null,"error":{"code":"Not Found",'
                            b'"description":"No data found, symbol may be delisted"}}}' if code == 404 else b"")
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, fp)
        return io.BytesIO(self.body.encode("utf-8"))


class Network(unittest.TestCase):
    def setUp(self):
        self.saved = (alerts._urlopen, alerts._sleep)

    def tearDown(self):
        alerts._urlopen, alerts._sleep = self.saved

    def net(self, codes):
        f = FakeNet(codes)
        alerts._urlopen = f.urlopen
        alerts._sleep = f.sleeps.append
        return f

    def hosts(self, f):
        return [h.split(".")[0] for h, _ in f.calls]

    def test_query2_first_with_the_user_agent_and_dot_ns(self):
        f = self.net([200])
        q, errors = alerts.get_quotes(["TCS"])
        self.assertEqual((q["TCS"]["price"], errors), (2460.0, []))
        self.assertEqual(f.calls, [("query2.finance.yahoo.com", UA)])
        self.assertEqual(f.urls, ["https://query2.finance.yahoo.com/v8/finance/chart/TCS.NS?range=1d&interval=1d"])

    def test_m_and_m_is_quoted_in_the_url(self):
        f = self.net([200])
        alerts.get_quotes(["M&M"])
        self.assertEqual(f.urls, ["https://query2.finance.yahoo.com/v8/finance/chart/M%26M.NS?range=1d&interval=1d"])

    def test_one_retry_with_a_pause_on_429(self):
        f = self.net([429, 200])
        self.assertEqual(alerts.get_quotes(["TCS"])[0]["TCS"]["price"], 2460.0)
        self.assertEqual(self.hosts(f), ["query2", "query2"])
        self.assertEqual(f.sleeps, [alerts.BACKOFF])

    def test_falls_back_to_query1_on_429_and_5xx(self):
        f = self.net([429, 429, 200])
        self.assertIn("TCS", alerts.get_quotes(["TCS"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query2", "query1"])
        f = self.net([503, 200])
        self.assertIn("TCS", alerts.get_quotes(["TCS"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query1"])
        f = self.net([500, 502])
        q, errors = alerts.get_quotes(["TCS"])
        self.assertEqual(q, {})
        self.assertIn("query1.finance.yahoo.com/v8/finance/chart/TCS.NS answered HTTP 502", errors[0])

    def test_a_404_is_yahoos_answer_and_a_403_is_not_retried(self):
        f = self.net([404])
        q, errors = alerts.get_quotes(["NOPE"])
        self.assertIn("No data found", errors[0])
        self.assertEqual(self.hosts(f), ["query2"])
        f = self.net([403])
        q, errors = alerts.get_quotes(["TCS"])
        self.assertIn("HTTP 403", errors[0])
        self.assertEqual(self.hosts(f), ["query2"])

    def test_a_pause_between_symbols(self):
        f = self.net([200, 200])
        alerts.get_quotes(["TCS", "INFY"])
        self.assertEqual(f.sleeps, [alerts.PAUSE])

    def test_yahoo_down_says_use_your_broker_app(self):
        self.net([500, 502])
        code, text, _ = run(["quote", "TCS"])
        self.assertEqual(code, 1)
        self.assertIn("No second source: if Yahoo is down, read the price in your broker app", text)
        for word in ("Stooq", "stooq", "Alpaca", "IBKR", "get_stock_snapshot"):
            self.assertNotIn(word, text)


class Rules(unittest.TestCase):
    def setUp(self):
        self.q, _ = alerts.get_quotes(["TCS", "INFY", "RELIANCE", "M&M"], QDIR)
        self.now = ist("2026-11-23T10:00")
        self.open = alerts.market_status(self.now)

    def at(self, text, rules, state=None, quotes=None):
        now = ist(text)
        return alerts.evaluate(rules, self.q if quotes is None else quotes, alerts.market_status(now),
                               state or {}, now)

    def test_a_target_rings_once(self):
        rules = [{"id": "tcs-2450", "symbol": "TCS", "type": "above", "price": 2450}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertEqual(len(rings), 1)
        self.assertEqual(rings[0]["message"],
                         "TCS is %s2,460.00: at or above your alert level %s2,450.00." % (RS, RS))
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])
        rings, _ = self.at("2026-11-23 16:00", rules)                     # after the close: "last", not "is"
        self.assertIn("TCS last %s2,460.00" % RS, rings[0]["message"])

    def test_a_rule_written_with_dot_ns_or_lower_case_still_matches(self):
        rules = [{"id": "a", "symbol": "tcs.ns", "type": "above", "price": 2450},
                 {"id": "b", "symbol": "m&m", "type": "below", "price": 3500}]
        rings, _ = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertEqual([r["symbol"] for r in rings], ["TCS", "M&M"])
        self.assertIn("M&M is %s3,487.40: at or below your alert level %s3,500.00" % (RS, RS), rings[1]["message"])

    def test_stop_and_rearm(self):
        rules = [{"id": "ril-stop", "symbol": "RELIANCE", "type": "stop", "price": 1360, "rearm": True}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertIn("your stop %s1,360.00" % RS, rings[0]["message"])
        self.q["RELIANCE"]["price"] = 1365.0
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])
        self.assertNotIn("ril-stop", st["rung"])
        self.q["RELIANCE"]["price"] = 1355.5
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(len(rings), 1)

    def test_pct_move_from_previous_close(self):
        big = [{"id": "ril-3", "symbol": "RELIANCE", "type": "pct_move", "pct": 3}]
        self.assertEqual(alerts.evaluate(big, self.q, self.open, {}, self.now)[0], [])
        small = [{"id": "ril-1", "symbol": "RELIANCE", "type": "pct_move", "pct": 1, "direction": "down"}]
        rings, st = alerts.evaluate(small, self.q, self.open, {}, self.now)
        self.assertIn("-1.92%", rings[0]["message"])
        self.assertEqual(alerts.evaluate(small, self.q, self.open, st, self.now)[0], [])
        up = [{"id": "ril-up", "symbol": "RELIANCE", "type": "pct_move", "pct": 1, "direction": "up"}]
        self.assertEqual(alerts.evaluate(up, self.q, self.open, {}, self.now)[0], [])
        tcs = [{"id": "tcs-3", "symbol": "TCS", "type": "pct_move", "pct": 3}]
        rings, _ = self.at("2026-11-23 15:29", tcs)
        self.assertEqual(rings[0]["message"], "TCS moved +3.53%% to %s2,460.00 (from %s2,376.20)." % (RS, RS))

    def test_opening_bell_at_0915_once_per_trading_day(self):
        rules = [{"id": "bell", "type": "opening_bell"}]
        self.assertEqual(self.at("2026-11-23 09:14", rules, quotes={})[0], [])
        rings, st = self.at("2026-11-23 09:15", rules, quotes={})
        self.assertEqual(rings[0]["message"],
                         "Opening bell: the NSE is open (2026-11-23 09:15 IST), session 09:15 to 15:30.")
        self.assertEqual(st["rung"]["bell"], "2026-11-23T03:45:00Z")
        self.assertEqual(self.at("2026-11-23 11:00", rules, st, {})[0], [])
        self.assertEqual(self.at("2026-11-24 09:30", rules, st, {})[0], [])        # Guru Nanak Jayanti
        self.assertEqual(len(self.at("2026-11-25 09:16", rules, st, {})[0]), 1)

    def test_the_plan_gives_a_stop_a_target_and_a_flat_by(self):
        rules = plan_rules()
        self.assertEqual([(r["id"], r["type"]) for r in rules],
                         [("tcs-plan-stop", "stop"), ("tcs-plan-target", "target"), ("tcs-flat-by", "time")])
        self.assertEqual((rules[0]["price"], rules[1]["price"]), (2419.35, 2467.65))
        self.assertEqual((rules[2]["at"], rules[2]["label"]), ("15:00", "flat by 15:00"))
        self.assertNotIn("before_close", rules[2])
        self.assertTrue(all(r["date"] == "2026-11-23" for r in rules))
        rings, st = self.at("2026-11-23 14:00", rules)          # 2460.00 sits between the stop and the target
        self.assertEqual(rings, [])
        q = json.loads(json.dumps(self.q))
        q["TCS"]["price"] = 2419.0
        rings, st = self.at("2026-11-23 14:10", rules, st, q)
        self.assertEqual([r["id"] for r in rings], ["tcs-plan-stop"])
        self.assertEqual(rings[0]["message"], "TCS is %s2,419.00: at or below your stop %s2,419.35 "
                                              "(plan stop: opening-range midpoint)." % (RS, RS))
        q["TCS"]["price"] = 2467.65
        self.assertEqual([r["id"] for r in self.at("2026-11-23 14:20", rules, st, q)[0]], ["tcs-plan-target"])

    def test_the_flat_by_default_is_1500(self):
        rules = alerts.rules_from_plan({"symbol": "infy.ns", "stop": 1114.1, "target": 1131.9})
        self.assertEqual((rules[2]["id"], rules[2]["symbol"], rules[2]["at"]), ("infy-flat-by", "INFY", "15:00"))
        rules = alerts.rules_from_plan({"symbol": "INFY", "stop": 1114.1, "target": 1131.9}, "14:45")
        self.assertEqual((rules[2]["at"], rules[2]["label"]), ("14:45", "flat by 14:45"))
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan({"symbol": "INFY", "stop": 1114.1, "target": 1131.9, "flat_by": "3pm"})

    def test_flat_by_rings_once_per_trading_day(self):
        rules = plan_rules()[2:]
        self.assertEqual(self.at("2026-11-23 14:59", rules)[0], [])
        rings, st = self.at("2026-11-23 15:00", rules)
        self.assertEqual(len(rings), 1)
        msg = rings[0]["message"]
        self.assertIn("Flat by 15:00: it is 15:00 IST (set for 15:00), the market closes at 15:30.", msg)
        self.assertIn("close it yourself in your broker app", msg)
        self.assertIn("trader-broker flatten --symbol TCS", msg)
        self.assertIn("square off intraday positions on their own from about 15:10 IST", msg)
        self.assertEqual(self.at("2026-11-23 15:10", rules, st)[0], [])
        rings, _ = self.at("2026-11-23 15:40", rules)
        self.assertIn("the market has closed", rings[0]["message"])
        self.assertEqual(self.at("2026-11-25 15:01", rules, st)[0], [])     # plan rules: their day only

    def test_before_close_from_the_us_version_is_ignored(self):
        # NSE has no early closes: a stored rule that still carries "before_close" rings at its own time
        rule = [{"id": "flat", "type": "time", "at": "15:00", "label": "flat by 15:00", "before_close": True}]
        self.assertEqual(self.at("2026-12-24 12:00", rule)[0], [])
        self.assertEqual(self.at("2026-12-24 14:59", rule)[0], [])
        rings, st = self.at("2026-12-24 15:00", rule)
        self.assertIn("set for 15:00), the market closes at 15:30", rings[0]["message"])
        self.assertEqual(st["rung"]["flat"], "2026-12-24T09:30:00Z")
        self.assertEqual(self.at("2026-11-21 15:01", rule)[0], [])         # Saturday: no session
        self.assertEqual(self.at("2026-11-24 15:01", rule)[0], [])         # NSE holiday
        self.assertEqual(len(self.at("2026-06-01 15:00", rule)[0]), 1)     # the same clock in June

    def test_the_example_rules_are_valid(self):
        with open(os.path.join(ROOT, "trader-alerts", "alerts.example.json"), encoding="utf-8") as fh:
            rules = json.load(fh)["rules"]
        for r in rules:
            alerts.validate(r)
        self.assertEqual([r for r in rules if r["id"].startswith("tcs-plan") or r["id"] == "tcs-flat-by"],
                         plan_rules())
        self.assertIn("M&M", [r.get("symbol") for r in rules])

    def test_bad_rules(self):
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "moon"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "above", "symbol": "TCS"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "time", "at": "3pm"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan({"symbol": "TCS", "stop": 2419.35})


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rules = os.path.join(self.d, "alerts.json")
        self.state = os.path.join(self.d, "state", "alerts.json")
        self.prices = os.path.join(self.d, "prices.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def check(self, now):
        return run(["check", "--rules", self.rules, "--state", self.state, "--prices-out", self.prices,
                    "--offline", QDIR, "--now", now])

    def test_the_tcs_trade_from_the_plan(self):
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state,
                             "--config", os.path.join(self.d, "none")])
        self.assertEqual(code, 0)
        self.assertIn("Added tcs-flat-by", text)
        self.assertIn("an alert sells nothing", text)
        self.assertEqual(run(["add", "--rules", self.rules, "--opening-bell"])[0], 0)

        code, text, _ = self.check("2026-11-23 09:45")
        self.assertEqual(code, 0)                                      # the opening bell rings first
        self.assertIn("NSE OPEN (2026-11-23 09:45 IST, regular session).", text)
        code, text, _ = self.check("2026-11-23 09:50")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit. 4 rule(s) watched today, TCS %s2,460.00." % RS, text)
        with open(self.prices, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"TCS": 2460.0})               # NSE symbol, no .NS
        self.assertEqual(doc["source"], "yahoo")
        self.assertEqual(doc["detail"]["TCS"]["time"], "2026-11-23T09:59:34Z")
        self.assertEqual(doc["detail"]["TCS"]["fetched"], "2026-11-23T04:20:00Z")
        self.assertEqual(doc["detail"]["TCS"]["prev_close"], 2376.2)

        code, text, _ = self.check("2026-11-23 15:00")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Flat by 15:00", text)
        self.assertIn("not an order", text)
        self.assertIn("Nothing here buys or sells", text)
        self.assertEqual(self.check("2026-11-23 15:05")[0], 1)
        code, text, _ = self.check("2026-11-24 10:00")                 # the holiday: nothing rings
        self.assertEqual(code, 1)
        self.assertIn("NSE closed (2026-11-24 10:00 IST, NSE holiday: Guru Nanak Jayanti).", text)
        code, text, _ = self.check("2026-11-25 10:00")
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-11-25 10:05")
        self.assertIn("Nothing hit. 1 rule(s) watched today.", text)

        # a new plan replaces the plan rules and re-arms them
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state])
        with open(self.rules, encoding="utf-8") as fh:
            ids = [r["id"] for r in json.load(fh)["rules"]]
        self.assertEqual(sorted(ids), ["opening-bell", "tcs-flat-by", "tcs-plan-stop", "tcs-plan-target"])
        with open(self.state, encoding="utf-8") as fh:
            self.assertNotIn("tcs-flat-by", json.load(fh)["rung"])

    def test_the_flat_by_comes_from_the_config_when_the_plan_has_none(self):
        with open(PLAN, encoding="utf-8") as fh:
            plan = json.load(fh)
        del plan["flat_by"]
        p, c = os.path.join(self.d, "plan.json"), os.path.join(self.d, "config.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(plan, fh)
        with open(c, "w", encoding="utf-8") as fh:
            json.dump({"flat_by": "14:50"}, fh)
        run(["add", "--from-plan", "--plan", p, "--rules", self.rules, "--state", self.state, "--config", c])
        with open(self.rules, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["rules"][2]["at"], "14:50")
        run(["add", "--from-plan", "--plan", p, "--rules", self.rules, "--state", self.state,
             "--config", os.path.join(self.d, "none")])
        with open(self.rules, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["rules"][2]["at"], "15:00")

    def test_levels_the_evening_and_the_next_morning(self):
        for args in (["--opening-bell"], ["--symbol", "TCS", "--above", "2500"],
                     ["--symbol", "reliance.ns", "--stop", "1300"]):
            code, _, _ = run(["add", "--rules", self.rules] + args)
            self.assertEqual(code, 0)
        code, _, err = run(["add", "--rules", self.rules, "--symbol", "TCS", "--above", "2500"])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        code, _, err = run(["add", "--rules", self.rules, "--symbol", "BRK.B", "--above", "1"])
        self.assertEqual(code, 1)
        self.assertIn("not an NSE symbol", err)

        code, text, _ = self.check("2026-11-23T15:30")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit", text)
        with open(self.prices, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"TCS": 2460.0, "RELIANCE": 1351.0})

        code, text, _ = self.check("2026-11-25T09:16")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-11-25T09:45")
        self.assertEqual(code, 1)

        code, text, _ = run(["list", "--rules", self.rules, "--state", self.state])
        self.assertIn("rang", text)
        self.assertIn("reliance-stop-1300", text)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 0)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 1)

    def test_add_time_and_target(self):
        # --before-close is still accepted (old scripts), and no longer written
        code, text, _ = run(["add", "--rules", self.rules, "--at", "15:00", "--label", "flat by 15:00",
                             "--before-close", "--symbol", "tcs"])
        self.assertEqual(code, 0)
        code, text, _ = run(["add", "--rules", self.rules, "--symbol", "M&M", "--target", "3550.5",
                             "--date", "2026-11-23"])
        self.assertEqual(code, 0)
        with open(self.rules, encoding="utf-8") as fh:
            rules = json.load(fh)["rules"]
        self.assertEqual(rules[0], {"id": "time-1500", "type": "time", "at": "15:00", "label": "flat by 15:00",
                                    "symbol": "TCS"})
        self.assertEqual(rules[1], {"id": "m&m-target-3550.50", "symbol": "M&M", "type": "target",
                                    "price": 3550.5, "date": "2026-11-23"})
        self.assertEqual(run(["add", "--rules", self.rules, "--at", "3pm"])[0], 1)
        code, _, err = run(["add", "--rules", self.rules, "--from-plan", "--plan", os.path.join(self.d, "none")])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)

    def test_quote_write_merges(self):
        code, text, _ = run(["quote", "TCS", "INFY.NS", "--offline", QDIR, "--write", self.prices,
                             "--now", "2026-11-23T10:00:01Z"])
        self.assertEqual(code, 0)
        self.assertIn("TCS        %s2,460.00 (+3.53%% vs prev close %s2,376.20)  2026-11-23 15:29 IST  [yahoo]"
                      % (RS, RS), text)
        self.assertIn("can lag", text)
        self.assertNotIn("$", text)
        run(["quote", "M&M", "--offline", QDIR, "--write", self.prices])
        with open(self.prices, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(doc["prices"]), ["INFY", "M&M", "TCS"])
        self.assertEqual(sorted(doc), ["as_of", "detail", "note", "prices", "source"])
        self.assertEqual(doc["detail"]["INFY"]["prev_close"], 1098.0)
        self.assertEqual(doc["detail"]["INFY"]["fetched"], "2026-11-23T10:00:01Z")
        self.assertEqual(sorted(doc["detail"]["INFY"]), ["fetched", "prev_close", "source", "time"])

    def test_quote_holdings_from_portfolio_and_plan(self):
        pf = os.path.join(self.d, "portfolio.json")
        with open(pf, "w", encoding="utf-8") as fh:
            json.dump({"cash": 0, "positions": [{"ticker": "INFY", "shares": 1}]}, fh)
        code, text, _ = run(["quote", "--holdings", "--portfolio", pf, "--plan", PLAN, "--offline", QDIR, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(json.loads(text)["quotes"]), ["INFY", "TCS"])

    def test_clock_exit_codes(self):
        code, text, _ = run(["clock", "--now", "2026-11-23 09:35"])
        self.assertEqual(code, 0)
        self.assertEqual(text.splitlines(), ["India 2026-11-23 09:35 IST (Monday): NSE OPEN, regular session.",
                                             "Today's session: 09:15 to 15:30 IST."])
        code, text, _ = run(["clock", "--now", "2026-11-24T10:00"])
        self.assertEqual(code, 1)
        self.assertEqual(text.splitlines(), ["India 2026-11-24 10:00 IST (Tuesday): NSE closed, NSE holiday: "
                                             "Guru Nanak Jayanti.", "Next open: 2026-11-25 09:15 IST."])
        code, text, _ = run(["clock", "--now", "2026-12-25T10:00", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)["reason"], "NSE holiday: Christmas")
        for word in ("New York", "NYSE", " ET"):
            self.assertNotIn(word, run(["clock", "--now", "2026-11-23 09:35"])[1])

    def test_check_without_rules(self):
        code, _, err = run(["check", "--rules", self.rules, "--offline", QDIR])
        self.assertEqual(code, 1)
        self.assertIn("no rules", err)


if __name__ == "__main__":
    unittest.main()
