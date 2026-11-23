import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

news = load("trader-news", "news")
# Real Yahoo answers fetched on 2026-11-24 (an NSE holiday): the headline RSS (region=IN) and the chart
# (range=1d&interval=5m) for TCS.NS, INFY.NS, RELIANCE.NS and M&M.NS. RELIANCE's feed came back empty.
DIR = os.path.join(FIX, "trader-news")
NOW = "2026-11-24 01:15"            # India time, when the fixtures were saved
UTC = datetime.timezone.utc
RS = chr(0x20b9)


def raw(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = news.main(args)
    return code, out.getvalue(), err.getvalue()


class Feeds(unittest.TestCase):
    def test_yahoo_rss(self):
        items = news.parse_rss(raw("yahoo-TCS.xml"), "TCS")
        self.assertEqual(len(items), 13)
        first = items[0]
        self.assertEqual(first["title"],
                         "CORPWATCH: India's IT giants turn captive tech centres into billion-dollar deals")
        self.assertEqual(first["time"], datetime.datetime(2026, 11, 23, 0, 51, 18, tzinfo=UTC))
        self.assertTrue(first["link"].startswith("http://www.intellinews.com/"))
        self.assertEqual(first["id"], "rss:c894cdde-fd85-3319-bb2e-323d9c336b82")
        self.assertEqual((first["source"], first["kind"], first["ticker"]), ("Yahoo Finance", "headline", "TCS"))

    def test_an_empty_feed_is_no_items_not_an_error(self):
        # what Yahoo really sends for RELIANCE.NS: a valid channel with no <item>
        self.assertIn(b"<channel>", raw("yahoo-RELIANCE.xml"))
        self.assertEqual(news.parse_rss(raw("yahoo-RELIANCE.xml"), "RELIANCE"), [])
        with self.assertRaises(news.NewsError):
            news.parse_rss(b"<html>not a feed", "TCS")

    def test_times(self):
        self.assertEqual(news.parse_time("Mon, 23 Nov 2026 00:51:18 +0000").hour, 0)
        self.assertEqual(news.parse_time("2026-11-23T06:21:18+05:30").hour, 0)
        self.assertIsNone(news.parse_time("soon"))

    def test_the_sec_code_is_gone(self):
        for name in ("parse_sec_atom", "cik_map", "sec_user_agent", "SEC_FEED", "TICKERS_URL", "ITEMS_8K",
                     "IMPORTANT_ITEMS", "eastern_offset", "to_eastern"):
            self.assertFalse(hasattr(news, name), name)
        for flag in (["--no-sec"], ["--forms", "8-K"], ["--user-agent", "x"]):
            with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                news.main(["--tickers", "TCS", "--offline", DIR, "--dry-run"] + flag)
        with open(os.path.join(os.path.dirname(DIR), "..", "..", "trader-news", "news.py"), encoding="utf-8") as fh:
            src = fh.read()
        for word in ("sec.gov", "EDGAR", "8-K", "CIK"):
            self.assertNotIn(word, src)


class Symbols(unittest.TestCase):
    def test_yahoo_symbols(self):
        self.assertEqual(news.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(news.yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(news.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(news.yahoo_symbol("TCS.NS"), "TCS.NS")
        self.assertEqual(news.yahoo_symbol("500325.BO"), "500325.BO")
        self.assertEqual(news.nse_symbol("m&m.ns"), "M&M")

    def test_urls(self):
        self.assertEqual(news.rss_url("tcs"),
                         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=TCS.NS&region=IN&lang=en-IN")
        self.assertIn("?s=M%26M.NS&region=IN", news.rss_url("M&M"))
        self.assertEqual(news.chart_url("M&M"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/M%26M.NS?range=1d&interval=5m")
        self.assertIn("//query1.finance.yahoo.com/", news.chart_url("TCS", "query1"))
        self.assertEqual(news.UA, "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)")

    def test_m_and_m_end_to_end(self):
        code, out, err = run(["--tickers", "m&m.ns", "--hours", "9000", "--offline", DIR, "--dry-run",
                              "--json", "--now", NOW])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(len(data["items"]), 16)
        self.assertEqual({t for i in data["items"] for t in i["touches"]}, {"M&M"})
        self.assertEqual((data["moves"][0]["ticker"], data["moves"][0]["price"], data["moves"][0]["prev_close"]),
                         ("M&M", 3487.4, 3545.0))

    def test_a_symbol_that_is_not_nse_is_skipped_with_a_note(self):
        code, out, err = run(["--tickers", "TCS", "BRK.B", "--hours", "12", "--offline", DIR, "--dry-run",
                              "--no-moves", "--now", "2026-11-23 09:45"])
        self.assertEqual(code, 0, err)
        self.assertIn("note: BRK.B is not an NSE symbol like RELIANCE, M&M or BAJAJ-AUTO: skipped", out)
        code, _, err = run(["--tickers", "BRK.B", "--offline", DIR, "--dry-run"])
        self.assertEqual(code, 2)
        self.assertIn("no tickers", err)


class Clock(unittest.TestCase):
    def test_india_time_is_a_fixed_five_and_a_half_hours(self):
        self.assertEqual(news.to_ist(datetime.datetime(2026, 11, 23, 0, 51, tzinfo=UTC)).strftime("%d %H:%M"),
                         "23 06:21")
        for month in (1, 3, 7, 11):          # no daylight saving, any month
            utc = datetime.datetime(2026, month, 15, 3, 45, tzinfo=UTC)
            self.assertEqual((news.to_ist(utc).hour, news.to_ist(utc).minute), (9, 15))
        self.assertEqual(news.to_ist(datetime.datetime(2026, 11, 23, 20, 0)).day, 24)     # naive is UTC
        self.assertEqual(news.ist_to_utc(datetime.datetime(2026, 11, 23, 9, 15)),
                         datetime.datetime(2026, 11, 23, 3, 45, tzinfo=UTC))

    def test_now_is_india_time(self):
        want = datetime.datetime(2026, 11, 23, 4, 15, tzinfo=UTC)
        self.assertEqual(news.parse_now("2026-11-23 09:45"), want)
        self.assertEqual(news.parse_now("2026-11-23 09:45 IST"), want)
        self.assertEqual(news.parse_now("2026-11-23T04:15:00Z"), want)
        self.assertEqual(news.parse_now("2026-11-23T09:45:00+05:30"), want)
        with self.assertRaises(news.NewsError):
            news.parse_now("this morning")
        code, _, err = run(["--tickers", "TCS", "--offline", DIR, "--dry-run", "--now", "this morning"])
        self.assertEqual(code, 2)
        self.assertIn("India time", err)


class Tagging(unittest.TestCase):
    def test_touches(self):
        items = news.parse_rss(raw("yahoo-TCS.xml"), "TCS")
        news.tag(items, {"TCS": None, "INFY": "Infosys"})
        by = {i["title"][:22]: i for i in items}
        self.assertEqual(by["Why Infosys Can Shrug "]["touches"], ["INFY", "TCS"])      # the company's name
        self.assertEqual(by["CORPWATCH: India's IT "]["touches"], ["TCS"])
        self.assertIn("deal", by["CORPWATCH: India's IT "]["tags"])

    def test_mentions_by_exchange_and_symbol(self):
        def item(title):
            return {"id": title, "source": "Yahoo Finance", "kind": "headline", "ticker": "TCS", "title": title,
                    "summary": "", "link": "", "time": None}
        watch = {"TCS": None, "M&M": None, "INFY": None, "ITC": None}
        got = news.tag([item("Mahindra (NSE: M&M) and Infosys (INFY) rise; INFY.NS at a high"),
                        item("M&MFIN and (NSE:ITCHOTELS) are other companies")], watch)
        self.assertEqual(got[0]["touches"], ["INFY", "M&M", "TCS"])
        self.assertEqual(got[1]["touches"], ["TCS"])

    def test_dedupe_merges_the_same_story(self):
        a = news.parse_rss(raw("yahoo-TCS.xml"), "TCS")
        b = news.parse_rss(raw("yahoo-TCS.xml"), "INFY")
        items = news.dedupe(news.tag(a + b, {"TCS": None, "INFY": None}))
        self.assertEqual(len(items), 13)
        self.assertEqual(items[0]["touches"], ["INFY", "TCS"])

    def test_indian_market_tags(self):
        def tags(title):
            return news.tag([{"id": "x", "source": "Yahoo Finance", "kind": "headline", "ticker": "TCS",
                              "title": title, "summary": "", "link": "", "time": None}], {"TCS": None})[0]["tags"]
        got = tags("SEBI probe after stock hits upper circuit on Q2 results; RBI holds repo rate")
        for t in ("earnings", "legal", "halt", "regulatory", "macro"):
            self.assertIn(t, got)
        self.assertEqual(tags("Board approves bonus issue and a QIP; promoter pledge falls"),
                         ["capital", "regulatory"])
        self.assertIn("regulatory", tags("USFDA issues observations for the Halol plant"))
        self.assertIn("deal", tags("NCLT clears the demerger"))
        # the US-only triggers are gone
        self.assertEqual(tags("Fed FOMC minutes, payrolls and the jobs report; FTC and DOJ; SEC charges; futures"),
                         [])


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state", "news.json")
        self.base = ["--offline", DIR, "--state", self.state, "--now", NOW,
                     "--portfolio", os.path.join(self.tmp.name, "none.json"),
                     "--watchlist", os.path.join(self.tmp.name, "none.json"),
                     "--plan", os.path.join(self.tmp.name, "none.json")]

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_items_once_newest_first(self):
        code, out, err = run(["--tickers", "TCS", "INFY", "--hours", "2400", "--json"] + self.base)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        times = [i["time"] for i in data["items"]]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual(len(data["items"]), 26)                    # 13 TCS + 13 Infosys headlines
        self.assertEqual((data["items"][0]["time"], data["items"][0]["time_ist"]),
                         ("2026-11-23T00:51:18+00:00", "2026-11-23 06:21"))
        self.assertEqual((data["checked"], data["checked_ist"]), ("2026-11-23T19:45:00+00:00", "2026-11-24 01:15"))
        self.assertIn("NSE or BSE", data["not_read"])
        self.assertEqual(data["notes"], [])
        code, out, _ = run(["--tickers", "TCS", "INFY", "--hours", "2400"] + self.base)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)
        with open(self.state, encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual(len(state["seen"]), 26)
        self.assertEqual(sorted(state), ["last_check", "seen"])

    def test_the_tcs_morning_of_2026_11_23(self):
        args = ["--tickers", "TCS", "RELIANCE", "--hours", "12", "--offline", DIR, "--state", self.state,
                "--now", "2026-11-23 09:45"]
        code, out, err = run(args)
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        # the chart was saved after the close, so the price line carries the 15:29 quote
        self.assertEqual(lines[0], "RELIANCE   %s1,351.00 -1.92%% vs previous close %s1,377.50 at 15:29 IST"
                         % (RS, RS))
        self.assertEqual(lines[1], "TCS        %s2,460.00 +3.53%% vs previous close %s2,376.20 at 15:29 IST  MOVING"
                         % (RS, RS))
        self.assertEqual(lines[3], "  2026-11-23 06:21 IST  TCS         Yahoo Finance CORPWATCH: India's IT giants "
                                   "turn captive tech centres into billion-dollar deals")
        self.assertTrue(lines[4].startswith("      tags: deal  http://www.intellinews.com/"))
        self.assertEqual(lines[5], "note: no headlines from Yahoo for RELIANCE (its feed is empty)")
        self.assertTrue(lines[6].startswith("Checked 2026-11-23 09:45 IST. Yahoo's coverage of NSE stocks is thin"))
        self.assertIn("Exchange filings are not read here", lines[6])
        self.assertIn("NSE or BSE website", lines[6])
        self.assertIn("Not financial advice", lines[6])
        self.assertEqual(len(lines), 7)
        for word in (chr(0x2014), " ET", "New York", "SEC", "8-K", "$"):
            self.assertNotIn(word, out)
        code, out, _ = run(args)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)
        self.assertIn("no headlines from Yahoo for RELIANCE", out)       # said every time, not only once

    def test_a_pinned_clock_hides_what_came_later(self):
        code, out, err = run(["--tickers", "TCS", "--hours", "24", "--no-moves", "--dry-run", "--offline", DIR,
                              "--now", "2026-11-20 21:00"])
        self.assertEqual(code, 0, err)
        self.assertIn("  2026-11-20 20:21 IST  TCS", out)
        self.assertNotIn("CORPWATCH", out)                               # published on the 23rd
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("  2026-")]), 1)
        self.assertIn("Checked 2026-11-20 21:00 IST", out)

    def test_silence_is_explained(self):
        code, out, _ = run(["--tickers", "INFY", "M&M", "RELIANCE", "--hours", "48", "--no-moves", "--dry-run"]
                           + self.base)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)
        self.assertIn("note: no headlines from Yahoo for INFY in the last 48 hours, the newest of its 13 is "
                      "from 2026-10-29", out)
        self.assertIn("note: no headlines from Yahoo for M&M in the last 48 hours, the newest of its 16 is "
                      "from 2026-11-18", out)
        self.assertIn("note: no headlines from Yahoo for RELIANCE (its feed is empty)", out)
        # a feed file that is missing offline is an error note, never a quiet pass
        code, out, _ = run(["--tickers", "HDFCBANK", "--no-moves", "--dry-run"] + self.base)
        self.assertEqual(code, 1)
        self.assertIn("note: headlines for HDFCBANK: offline: no file", out)

    def test_coverage_notes(self):
        now = news.parse_now(NOW)
        feeds = {"RELIANCE": [], "TCS": news.parse_rss(raw("yahoo-TCS.xml"), "TCS")}
        self.assertEqual(news.coverage_notes(feeds, now, 48),
                         ["no headlines from Yahoo for RELIANCE (its feed is empty)"])
        self.assertEqual(news.coverage_notes(feeds, now, 12)[1],
                         "no headlines from Yahoo for TCS in the last 12 hours, the newest of its 13 is from "
                         "2026-11-23")

    def test_tickers_from_watchlist_and_plan(self):
        wl = os.path.join(self.tmp.name, "watchlist.json")
        with open(wl, "w", encoding="utf-8") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [{"symbol": "INFY", "setups": ["orb"]}]}, fh)
        plan = os.path.join(self.tmp.name, "plan.json")
        with open(plan, "w", encoding="utf-8") as fh:
            json.dump({"created_by": "trader-exit", "symbol": "TCS.NS"}, fh)
        self.assertEqual(sorted(news.watch_from_files("/nonexistent.json", wl, plan)), ["INFY", "TCS"])
        args = list(self.base)
        args[args.index("--watchlist") + 1] = wl
        code, out, err = run(["--json", "--dry-run", "--hours", "2400"] + args)
        self.assertEqual(code, 0, err)
        tickers = {t for i in json.loads(out)["items"] for t in i["touches"]}
        self.assertEqual(tickers, {"INFY"})
        self.assertFalse(os.path.exists(self.state))

    def test_no_tickers_is_an_error(self):
        code, _, err = run(self.base)
        self.assertEqual(code, 2)
        self.assertIn("no tickers", err)

    def test_moves(self):
        m = news.parse_chart(raw("chart-TCS.json"), "TCS")
        self.assertEqual((m["price"], m["prev_close"], m["pct"], m["currency"]), (2460.0, 2376.2, 3.53, "INR"))
        self.assertEqual(news.to_ist(m["time"]).strftime("%Y-%m-%d %H:%M"), "2026-11-23 15:29")
        quiet = dict(m, pct=1.2)
        self.assertNotIn("MOVING", news.render_moves([quiet])[0])
        self.assertIn("MOVING", news.render_moves([quiet], move_pct=1.0)[0])
        # a quote from another day says its date: on the holiday the price is Monday's
        line = news.render_moves([m], now=news.parse_now(NOW))[0]
        self.assertEqual(line, "TCS        %s2,460.00 +3.53%% vs previous close %s2,376.20 at 15:29 IST on "
                               "2026-11-23  MOVING" % (RS, RS))
        self.assertNotIn(" on 2026", news.render_moves([m], now=news.parse_now("2026-11-23 15:31"))[0])
        with self.assertRaises(news.NewsError):
            news.parse_chart(b'{"chart": {"result": null}}', "ZZZZ")
        # no chart file offline: no move line, no error note about the price
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "yahoo-TCS.xml"), "wb") as fh:
            fh.write(raw("yahoo-TCS.xml"))
        code, out, _ = run(["--tickers", "TCS", "--hours", "48", "--dry-run", "--offline", d, "--now", NOW])
        self.assertNotIn("vs previous close", out)
        self.assertNotIn("note: price", out)

    def test_state_forgets_after_30_days_and_drops_the_sec_cache(self):
        state = {"seen": {"old": "2026-09-01T00:00:00+00:00"}, "ciks": {"AAPL": "0000320193"}}
        now = news.parse_now(NOW)
        news.remember([], state, now)
        self.assertNotIn("old", state["seen"])
        self.assertNotIn("ciks", state)
        self.assertEqual(state["last_check"], now.isoformat())


if __name__ == "__main__":
    unittest.main()
