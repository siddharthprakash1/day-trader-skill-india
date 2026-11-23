import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

news = load("trader-news", "news")
# Real answers fetched on 2026-11-24 (an NSE holiday):
#   yahoo-*.xml, chart-*.json   Yahoo's headline RSS (region=IN) and chart (range=1d&interval=5m) for TCS.NS,
#                               INFY.NS, RELIANCE.NS and M&M.NS. RELIANCE's feed came back empty.
#   google-*.xml                Google News RSS search, "<company name>" when:2d, for HDFC Bank, Mahindra &
#                               Mahindra, Tata Consultancy Services, Reliance Industries and Infosys (02:15 IST).
#   nse-announcements.xml       NSE's announcements RSS at 02:15 IST: its whole window was one filing.
#   EQUITY_L.csv                the header and 14 real rows cut from NSE's list of listed equities.
# NOT real: nse-announcements-synthetic.xml. Hand-written items in the real feed's shape (each one says
# SYNTHETIC), used for the matching and "!" cases a one-item capture cannot show.
DIR = os.path.join(FIX, "trader-news")
NOW = "2026-11-24 01:15"            # India time, when the fixtures were saved
UTC = datetime.timezone.utc
SAVED = "2026-11-24 02:16"          # India time, when the Google News and NSE fixtures were saved
YAHOO = ["--sources", "yahoo"]      # the tests written before Google News and the NSE feed were added
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
        self.assertEqual((first["source"], first["publisher"], first["kind"], first["ticker"]),
                         ("yahoo", "Yahoo Finance", "headline", "TCS"))

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
                              "--json", "--now", NOW] + YAHOO)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(len(data["items"]), 16)
        self.assertEqual({t for i in data["items"] for t in i["touches"]}, {"M&M"})
        self.assertEqual((data["moves"][0]["ticker"], data["moves"][0]["price"], data["moves"][0]["prev_close"]),
                         ("M&M", 3487.4, 3545.0))

    def test_a_symbol_that_is_not_nse_is_skipped_with_a_note(self):
        code, out, err = run(["--tickers", "TCS", "BRK.B", "--hours", "12", "--offline", DIR, "--dry-run",
                              "--no-moves", "--now", "2026-11-23 09:45"] + YAHOO)
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
        self.base = ["--offline", DIR, "--state", self.state, "--now", NOW, "--sources", "yahoo",
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
                "--now", "2026-11-23 09:45"] + YAHOO
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
        self.assertTrue(lines[6].startswith("Checked 2026-11-23 09:45 IST. Read: Yahoo Finance headlines (thin "
                                            "for NSE stocks, and not always about your company). Not read: "
                                            "filings made only to BSE"))
        self.assertNotIn("Exchange filings are not read here", out)      # the old footer is gone
        self.assertNotIn("Google News", lines[6])                        # only the sources that were read
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
                              "--now", "2026-11-20 21:00"] + YAHOO)
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
        self.assertIn("note: Yahoo headlines for HDFCBANK: offline: no file", out)

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
        code, out, _ = run(["--tickers", "TCS", "--hours", "48", "--dry-run", "--offline", d, "--now", NOW] + YAHOO)
        self.assertNotIn("vs previous close", out)
        self.assertNotIn("note: price", out)

    def test_state_forgets_after_30_days_and_drops_the_sec_cache(self):
        state = {"seen": {"old": "2026-09-01T00:00:00+00:00"}, "ciks": {"AAPL": "0000320193"}}
        now = news.parse_now(NOW)
        news.remember([], state, now)
        self.assertNotIn("old", state["seen"])
        self.assertNotIn("ciks", state)
        self.assertEqual(state["last_check"], now.isoformat())


def offline_dir(tmp, nse="nse-announcements-synthetic.xml"):
    """A copy of the fixtures where the NSE feed is the SYNTHETIC one (hand-written items in the real shape)."""
    d = os.path.join(tmp, "offline")
    shutil.copytree(DIR, d)
    shutil.copyfile(os.path.join(DIR, nse), os.path.join(d, "nse-announcements.xml"))
    return d


class Names(unittest.TestCase):
    def test_search_name(self):
        for official, want in (("HDFC Bank Limited", "HDFC Bank"),
                               ("Mahindra & Mahindra Limited", "Mahindra & Mahindra"),
                               ("Tata Consultancy Services Limited", "Tata Consultancy Services"),
                               ("360 ONE WAM LIMITED", "360 ONE WAM"),
                               ("Some Company Ltd.", "Some Company"),
                               ("Some Company (India) Ltd", "Some Company (India)"),
                               ("Some  Company,  Limited. ", "Some Company"),
                               ("State Bank of India", "State Bank of India"),
                               ("Limited Brands Limited", "Limited Brands"),
                               ("", "")):
            self.assertEqual(news.search_name(official), want)

    def test_company_key(self):
        self.assertEqual(news.company_key("Mahindra & Mahindra Limited"),
                         news.company_key("MAHINDRA AND MAHINDRA LTD."))
        self.assertEqual(news.company_key("Ptc Industries Limited"), "ptcindustries")
        self.assertEqual(news.company_key("The Indian Hotels Company Limited"), "indianhotelscompany")
        self.assertNotEqual(news.company_key("Mahindra & Mahindra Limited"),
                            news.company_key("Mahindra & Mahindra Financial Services Limited"))

    def test_universe_and_equity_list(self):
        self.assertEqual(news.parse_universe("TCS|Tata Consultancy Services Limited\nM&M|Mahindra & Mahindra "
                                             "Limited\n\nnot a line\n"),
                         {"TCS": "Tata Consultancy Services Limited", "M&M": "Mahindra & Mahindra Limited"})
        names = news.parse_equity_list(raw("EQUITY_L.csv"))      # real rows cut from NSE's file of 2026-11-24
        self.assertEqual(names["M&M"], "Mahindra & Mahindra Limited")
        self.assertEqual(names["HDFCBANK"], "HDFC Bank Limited")
        self.assertEqual(names["PTCIL"], "PTC Industries Limited")
        self.assertEqual(names["21STCENMGM"], "21st Century Management Services Limited")   # series BE is kept
        with self.assertRaises(news.NewsError):
            news.parse_equity_list(b"<html>Access Denied</html>")

    def test_urls(self):
        self.assertEqual(news.google_url("HDFC Bank"),
                         "https://news.google.com/rss/search?q=%22HDFC+Bank%22+when%3A2d&hl=en-IN&gl=IN&ceid=IN%3Aen")
        self.assertIn("q=%22Mahindra+%26+Mahindra%22+when%3A1d&hl=en-IN", news.google_url("Mahindra & Mahindra", 12))
        self.assertEqual([news.google_days(h) for h in (1, 24, 25, 48, 72.5)], [1, 1, 2, 2, 4])
        # a name that brings its own quotes or OR is used as written
        self.assertEqual(news.google_query('"TCS" OR "Tata Consultancy"', 48), '"TCS" OR "Tata Consultancy" when:2d')
        self.assertEqual(news.NSE_FEED, "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml")
        self.assertEqual(news.NSE_LIST, "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv")
        for url in (news.NSE_FEED, news.NSE_LIST, news.GOOGLE):
            self.assertNotIn("www.nseindia.com", url)
            self.assertNotIn("/api/", url)

    def test_official_names_universe_first_then_the_cached_download(self):
        class Net(object):
            offline = None

            def __init__(self):
                self.calls = []

            def get(self, url, name):
                self.calls.append(url)
                return raw("EQUITY_L.csv")
        now = news.parse_now(NOW)
        with tempfile.TemporaryDirectory() as tmp:
            uni = os.path.join(tmp, "universe.txt")
            with open(uni, "w", encoding="utf-8") as fh:
                fh.write("TCS|Tata Consultancy Services Limited\n")
            cache = os.path.join(tmp, "state", "nse-names.json")
            net = Net()
            # everything is in the scanner's universe: no download, no cache
            self.assertEqual(news.official_names(["TCS"], net, now, uni, cache),
                             {"TCS": "Tata Consultancy Services Limited"})
            self.assertEqual(net.calls, [])
            self.assertFalse(os.path.exists(cache))
            # a symbol the universe does not have: one download, kept
            got = news.official_names(["TCS", "M&M"], net, now, uni, cache)
            self.assertEqual(got["M&M"], "Mahindra & Mahindra Limited")
            self.assertEqual(net.calls, [news.NSE_LIST])
            with open(cache, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual((doc["fetched"], doc["names"]["HDFCBANK"]), (now.isoformat(), "HDFC Bank Limited"))
            # six days later the cache answers; eight days later it is fetched again
            news.official_names(["INFY"], net, now + datetime.timedelta(days=6), uni, cache)
            self.assertEqual(len(net.calls), 1)
            news.official_names(["INFY"], net, now + datetime.timedelta(days=8), uni, cache)
            self.assertEqual(len(net.calls), 2)
            # an unknown symbol is simply absent
            self.assertEqual(news.official_names(["ZZZZ"], net, now + datetime.timedelta(days=8), uni, cache), {})

    def test_offline_names_never_touch_the_cache(self):
        errors = []
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "nse-names.json")
            got = news.official_names(["M&M", "ZZZZ"], news.Fetcher(offline=DIR), news.parse_now(NOW),
                                      os.path.join(tmp, "none.txt"), cache, errors)
            self.assertEqual(got, {"M&M": "Mahindra & Mahindra Limited"})
            self.assertFalse(os.path.exists(cache))
            got = news.official_names(["TCS"], news.Fetcher(offline=tmp), news.parse_now(NOW), None, cache, errors)
            self.assertEqual(got, {})
            self.assertIn("company names: offline: no file", errors[0])


class GoogleNews(unittest.TestCase):
    """Real answers of news.google.com/rss/search fetched on 2026-11-24 02:15 IST, query "<name>" when:2d."""

    def test_parse(self):
        want = {"HDFCBANK": (55, 2), "M&M": (20, 0), "TCS": (23, 2), "RELIANCE": (21, 2), "INFY": (34, 0)}
        for sym, (count, junk) in want.items():
            items, dropped = news.parse_google(raw("google-%s.xml" % sym), sym)
            self.assertEqual((len(items), dropped), (count, junk), sym)
            for it in items:
                self.assertEqual((it["source"], it["kind"], it["ticker"]), ("google-news", "headline", sym))
                self.assertTrue(it["id"].startswith("gn:"))
                self.assertTrue(it["link"].startswith("https://news.google.com/rss/articles/"))
                self.assertIsNotNone(it["time"])
                self.assertFalse(it["title"].endswith(" - " + it["publisher"]))
        items, _ = news.parse_google(raw("google-TCS.xml"), "TCS")
        first = items[0]
        self.assertEqual((first["title"], first["publisher"]),
                         ("Tata Consultancy Services rallies Monday, outperforms competitors", "MarketWatch"))
        self.assertEqual(first["time"], datetime.datetime(2026, 11, 23, 10, 33, tzinfo=UTC))
        self.assertEqual(news.to_ist(first["time"]).strftime("%H:%M"), "16:03")
        with self.assertRaises(news.NewsError):
            news.parse_google(b"<html>sorry", "TCS")

    def test_quote_pages_are_junk_and_listicles_are_not(self):
        for title in ("HDFC Bank Limited (HDFCBANK.NS) stock price, news, quote and history",        # real
                      "Reliance Industries Share Price - Live NSE: RELIANCE Stock Price & Chart",    # real
                      "News by CNBC TV18 on TradingView, 2026-11-23 %s cnbctv:43d687d89094b:0" % chr(0x2014),  # real
                      "Tata Consultancy Services Share Price",
                      "TCS Share Price Today Live",
                      "Infosys Ltd Stock Price, Quote & Chart",
                      "INFY Stock Quote"):
            self.assertTrue(news.is_junk(title), title)
        for title in ("HDFC Bank Share Price Declines 3.3 Percent; Analysts Remain Bullish But Correction Seems "
                      "Imminent",                                                                     # real
                      "Reliance Industries Share Price Prediction for Wednesday (25 Nov 2026): Stock Falls 1.92% "
                      "and Breaks Friday's Low",                                                      # real
                      "Infosys Share Price Fall: Price Action and Fundamentals",                      # real
                      "HDFC Bank share price today: stock falls 3% as Bank Nifty slides",
                      "TCS share price live updates: stock up 3% on Bosch deal report",
                      "HDFC Bank slips Monday, underperforms market"):
            self.assertFalse(news.is_junk(title), title)

    def test_the_same_story_from_many_publishers_is_one_item(self):
        items, _ = news.parse_google(raw("google-HDFCBANK.xml"), "HDFCBANK")
        stories = news.merge_same_story(items)
        self.assertEqual(len(stories), 40)
        self.assertEqual(sum(s["more"] for s in stories), 15)
        rbi = max(stories, key=lambda s: s["more"])
        # the earliest publisher is kept: Moneycontrol at 17:26 IST, ten more followed
        self.assertEqual((rbi["title"], rbi["publisher"], rbi["more"]),
                         ("RBI retains SBI, HDFC Bank and ICICI Bank as systemically important banks in 2026 list",
                          "Moneycontrol.com", 10))
        self.assertEqual(news.to_ist(rbi["time"]).strftime("%Y-%m-%d %H:%M"), "2026-11-23 17:26")
        self.assertIn("Business Standard", rbi["also"])
        self.assertEqual(len(rbi["same_story"]), 10)
        # different stories about the same company stay apart
        titles = [s["title"] for s in stories]
        self.assertIn("HDFC Bank slips Monday, underperforms market", titles)
        self.assertIn("HDFC Bank shares, Bank Nifty retreat sharply from record highs", titles)
        self.assertEqual(len(news.merge_same_story(news.parse_google(raw("google-RELIANCE.xml"), "RELIANCE")[0])), 21)

    def test_cap_prefers_titles_that_name_the_company(self):
        items, _ = news.parse_google(raw("google-TCS.xml"), "TCS")
        watch = {"TCS": "Tata Consultancy Services"}
        stories = news.tag(news.merge_same_story(items), watch)
        self.assertEqual(len(stories), 20)
        kept, hidden = news.cap_google(list(stories), watch, 8)
        self.assertEqual((len(kept), hidden), (8, {"TCS": 12}))
        named = [s for s in stories if "TCS" in s["title"] or "Tata Consultancy Services" in s["title"]]
        self.assertEqual(len(named), 8)
        for s in named:
            self.assertIn(s, kept)
        self.assertEqual(news.cap_google(list(stories), watch, 0), (stories, {}))
        self.assertEqual(news.cap_google(list(stories), watch, 50), (stories, {}))


class NseAnnouncements(unittest.TestCase):
    def test_the_real_feed(self):
        # the whole feed at 02:15 IST on a holiday: one filing. That is the rolling window.
        got = news.parse_nse(raw("nse-announcements.xml"))
        self.assertEqual(len(got), 1)
        f = got[0]
        self.assertEqual((f["source"], f["kind"], f["company"]),
                         ("nse-announcement", "filing", "PTC Industries Limited"))
        self.assertEqual(f["subject"], "Analysts/Institutional Investor Meet/Con. Call Updates")
        self.assertEqual(f["summary"], "Ptc Industries Limited has informed the Exchange about Schedule of meet")
        self.assertEqual(f["time"], datetime.datetime(2026, 11, 23, 18, 58, 46, tzinfo=UTC))    # 00:28:46 IST
        self.assertEqual((f["important"], f["why"]), (False, ""))
        self.assertTrue(f["link"].endswith("PTCINDUSTRIES_24112026002830_Investor_Meet_27112026_Templeton_and_SBI_"
                                           "Life.pdf"))
        # the PDF is named PTCINDUSTRIES but the NSE symbol is PTCIL: the company name in the title finds it
        self.assertEqual(f["ticker"], "PTCINDUSTRIES")
        official = {"PTCIL": news.parse_equity_list(raw("EQUITY_L.csv"))["PTCIL"]}
        mine = news.match_filings(news.parse_nse(raw("nse-announcements.xml")), {"PTCIL": None}, official)
        self.assertEqual([(m["ticker"], m["matched_by"]) for m in mine], [("PTCIL", "name")])
        self.assertEqual(news.match_filings(news.parse_nse(raw("nse-announcements.xml")), {"PTC": None},
                                            {"PTC": "PTC India Limited"}), [])
        with self.assertRaises(news.NewsError):
            news.parse_nse(b"<html>Access Denied")

    def test_times_are_india_time(self):
        self.assertEqual(news.parse_nse_time("24-Nov-2026 00:28:46"),
                         datetime.datetime(2026, 11, 23, 18, 58, 46, tzinfo=UTC))
        self.assertEqual(news.parse_nse_time("02-May-2026 16:46"), datetime.datetime(2026, 5, 2, 11, 16, tzinfo=UTC))
        self.assertIsNone(news.parse_nse_time("31-Feb-2026 10:00:00"))
        self.assertIsNone(news.parse_nse_time(""))

    def test_link_symbol(self):
        base = "https://nsearchives.nseindia.com/corporate/"
        self.assertEqual(news.link_symbol(base + "PTCINDUSTRIES_24112026002830_Investor_Meet.pdf"), "PTCINDUSTRIES")
        self.assertEqual(news.link_symbol(base + "M%26M_23112026_x.pdf"), "M&M")
        self.assertEqual(news.link_symbol(base + "M&MFIN_23112026_x.pdf"), "M&MFIN")
        self.assertEqual(news.link_symbol(base + "bajaj-auto_23112026_x.pdf"), "BAJAJ-AUTO")
        self.assertEqual(news.link_symbol(base + "nounderscore.pdf"), "")
        self.assertEqual(news.link_symbol(""), "")

    def test_important_subjects(self):
        for subject, why in (("Outcome of Board Meeting", "results"), ("Financial Result Updates", "results"),
                             ("Board Meeting Intimation", "results"), ("Change in Director(s)", "management"),
                             ("Resignation", "management"), ("Change in Auditors", "management"),
                             ("Acquisition", "deal"), ("Scheme of Arrangement", "deal"),
                             ("Raising of Funds", "fund raising"), ("Preferential Issue", "fund raising"),
                             ("Credit Rating", "rating"), ("Corporate Insolvency Resolution Process", "insolvency"),
                             ("Defaults on Payment of Interest/Principal", "insolvency"),
                             ("Suspension of Trading", "halt"), ("Buyback", "payout"), ("Dividend", "payout"),
                             ("Record Date", "payout")):
            self.assertEqual(news.importance(subject), why, subject)
        for subject in ("Analysts/Institutional Investor Meet/Con. Call Updates", "Copy of Newspaper Publication",
                        "ESOP/ESOS/ESPS", "Trading Window", "Loss of Share Certificates", "Shareholders meeting"):
            self.assertEqual(news.importance(subject, "about Dividend and Financial Results"), "", subject)
        # no subject, or a catch-all one: the description decides
        self.assertEqual(news.importance("", "has informed the Exchange regarding Buyback of equity shares"), "payout")
        self.assertEqual(news.importance("General Updates", "a qualified institutions placement"), "fund raising")
        self.assertEqual(news.importance("Updates", "nothing much"), "")

    def test_matching_on_the_synthetic_feed(self):
        # SYNTHETIC items in the real feed's shape (the file says so in every item), plus the one real filing
        self.assertIn(b"SYNTHETIC TEST DATA - NOT REAL FILINGS", raw("nse-announcements-synthetic.xml"))
        filings = news.parse_nse(raw("nse-announcements-synthetic.xml"))
        self.assertEqual(len(filings), 10)
        names = news.parse_equity_list(raw("EQUITY_L.csv"))
        watch = {t: None for t in ("TCS", "M&M", "HDFCBANK", "RELIANCE", "INFY")}
        mine = news.match_filings(filings, watch, {t: names[t] for t in watch})
        got = [(m["ticker"], m["matched_by"], m["title"], m["why"]) for m in mine]
        self.assertEqual(got, [
            ("TCS", "symbol", "Outcome of Board Meeting", "results"),
            ("TCS", "symbol", "Copy of Newspaper Publication", ""),
            ("M&M", "name", "Acquisition", "deal"),              # the file is MM_...: found by the company name
            ("HDFCBANK", "symbol", "Change in Director(s)", "management"),
            ("HDFCBANK", "symbol", "ESOP/ESOS/ESPS", ""),
            ("RELIANCE", "symbol", "General Updates", "fund raising"),
            ("INFY", "symbol", "SYNTHETIC TEST ITEM: Infosys Limited has informed the Exchange regarding Buyback of "
                               "equity shares", "payout"),
        ])
        # M&MFIN's filing is not M&M's, by symbol or by name; without names only the symbol prefix matches
        self.assertNotIn("Credit Rating", [m["title"] for m in mine])
        bare = news.match_filings(news.parse_nse(raw("nse-announcements-synthetic.xml")), {"M&M": None}, {})
        self.assertEqual(bare, [])
        # two filings with the same subject on the same day stay two items
        one = news.parse_nse(raw("nse-announcements-synthetic.xml"))[0]
        two = dict(one, id="nse:other", link="other.pdf")
        both = news.match_filings([one, two], {"TCS": None}, {})
        self.assertEqual(len(news.dedupe(news.tag(both, {"TCS": None}))), 2)


class AllSources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = offline_dir(self.tmp.name)
        self.state = os.path.join(self.tmp.name, "state", "news.json")
        none = os.path.join(self.tmp.name, "none")
        self.base = ["--offline", self.dir, "--state", self.state, "--now", SAVED, "--no-moves",
                     "--portfolio", none, "--watchlist", none, "--plan", none, "--config", none,
                     "--universe", none, "--names-cache", os.path.join(self.tmp.name, "names.json")]

    def tearDown(self):
        self.tmp.cleanup()

    def test_three_sources_then_nothing_new(self):
        code, out, err = run(["--tickers", "TCS", "m&m.ns", "HDFCBANK", "--json"] + self.base)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["sources"], ["nse", "google", "yahoo"])
        self.assertEqual(data["names"], {"TCS": "Tata Consultancy Services", "M&M": "Mahindra & Mahindra",
                                         "HDFCBANK": "HDFC Bank"})
        by = {}
        for it in data["items"]:
            by.setdefault(it["source"], []).append(it)
        self.assertEqual(sorted(by), ["google-news", "nse-announcement", "yahoo"])
        self.assertEqual([(f["ticker"], f["title"], f["important"], f["time_ist"]) for f in by["nse-announcement"]],
                         [("TCS", "Outcome of Board Meeting", True, "2026-11-23 16:30"),
                          ("TCS", "Copy of Newspaper Publication", False, "2026-11-23 16:15"),
                          ("M&M", "Acquisition", True, "2026-11-23 15:45"),
                          ("HDFCBANK", "Change in Director(s)", True, "2026-11-23 15:05"),
                          ("HDFCBANK", "ESOP/ESOS/ESPS", False, "2026-11-23 14:30")])
        for sym in ("TCS", "M&M", "HDFCBANK"):
            self.assertEqual(len([i for i in by["google-news"] if i["ticker"] == sym]), 8, sym)   # the cap
        self.assertEqual([i["title"][:9] for i in by["yahoo"]], ["CORPWATCH"])
        times = [i["time"] for i in data["items"]]
        self.assertEqual(times, sorted(times, reverse=True))
        for it in data["items"]:
            self.assertIn("publisher", it)
            self.assertNotIn("same_story", it)
        notes = "\n".join(data["notes"])
        self.assertIn("32 more Google News headlines for HDFCBANK not shown (--max-items 8)", notes)
        self.assertIn("Yahoo headlines for HDFCBANK: offline: no file", notes)
        self.assertIn("no headlines from Yahoo for M&M in the last 48 hours", notes)
        self.assertNotIn("no NSE announcements", notes)
        self.assertIn("BSE", data["not_read"])
        self.assertIn("rolling window", data["not_read"])
        # the second check is quiet: the capped and the merged headlines are remembered too
        code, out, _ = run(["--tickers", "TCS", "m&m.ns", "HDFCBANK"] + self.base)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)
        self.assertNotIn("not shown", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "names.json")))     # offline: no cache

    def test_the_printed_check(self):
        code, out, err = run(["--tickers", "M&M", "--sources", "nse", "google", "--max-items", "3"] + self.base)
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertEqual(lines[0], "  2026-11-23 22:00 IST  M&M         Trade Brains  Mahindra & Mahindra, Maruti "
                                   "Suzuki and 8 Other Auto Stocks Dominating the Nifty Auto Index; Check the Full "
                                   "List")
        self.assertTrue(lines[1].startswith("      tags: macro  https://news.google.com/rss/articles/"))
        filing = [i for i, l in enumerate(lines) if "  NSE filing  " in l]
        self.assertEqual(len(filing), 1)
        self.assertEqual(lines[filing[0]], "  2026-11-23 15:45 IST  M&M         NSE filing    ! Acquisition: SYNTHETIC "
                                           "TEST ITEM: Mahindra & Mahindra Limited has informed the Exchange "
                                           "regarding Acquisition")
        self.assertEqual(lines[filing[0] + 1], "      important: deal  tags: deal  https://nsearchives.nseindia.com/"
                                               "corporate/MM_23112026154500_SYNTHETIC_TEST_acquisition.pdf")
        self.assertEqual(len([l for l in lines if l.startswith("  2026-")]), 4)          # 3 headlines + 1 filing
        self.assertIn("note: 16 more Google News headlines for M&M not shown (--max-items 3)", lines)
        foot = lines[-1]
        self.assertTrue(foot.startswith("Checked 2026-11-24 02:16 IST. Read: NSE corporate announcements (a short "
                                        "rolling window of the newest filings: run this on a loop"))
        self.assertIn("Google News by company name", foot)
        self.assertNotIn("Yahoo", foot)
        self.assertIn("Not read: filings made only to BSE", foot)
        self.assertIn("Not financial advice", foot)
        self.assertNotIn("Exchange filings are not read", out)
        self.assertNotIn(chr(0x2014), "\n".join(l for l in lines if not l.startswith("  ")))   # ours, not theirs

    def test_merged_stories_say_how_many_more(self):
        code, out, err = run(["--tickers", "HDFCBANK", "--sources", "google", "--max-items", "0"] + self.base)
        self.assertEqual(code, 0, err)
        self.assertIn("  2026-11-23 17:26 IST  HDFCBANK    Moneycontrol.com RBI retains SBI, HDFC Bank and ICICI Bank "
                      "as systemically important banks in 2026 list (+10 more)", out)
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("  2026-")]), 40)
        self.assertNotIn("stock price, news, quote", out)
        self.assertNotIn("not shown", out)

    def test_name_override_and_config_names(self):
        # --name: the search uses it, and no name lookup is needed for Google alone
        code, out, err = run(["--tickers", "TCS", "--name", "TCS", "--sources", "google", "--json"] + self.base)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["names"], {"TCS": "TCS"})
        code, _, err = run(["--tickers", "TCS", "INFY", "--name", "TCS"] + self.base)
        self.assertEqual(code, 2)
        self.assertIn("--name goes with exactly one ticker", err)
        cfg = os.path.join(self.tmp.name, "config.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"names": {"infy": "Infosys Ltd", "TCS.NS": "TCS"}}, fh)
        args = list(self.base)
        args[args.index("--config") + 1] = cfg
        code, out, err = run(["--tickers", "TCS", "INFY", "M&M", "--sources", "google", "--json", "--dry-run"] + args)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["names"], {"TCS": "TCS", "INFY": "Infosys Ltd", "M&M": "Mahindra & Mahindra"})

    def test_names_from_the_scanners_universe(self):
        uni = os.path.join(self.tmp.name, "universe.txt")
        with open(uni, "w", encoding="utf-8") as fh:
            fh.write("RELIANCE|Reliance Industries Limited\n")
        os.remove(os.path.join(self.dir, "EQUITY_L.csv"))               # must not be needed
        args = list(self.base)
        args[args.index("--universe") + 1] = uni
        code, out, err = run(["--tickers", "RELIANCE", "--sources", "nse", "google", "--json", "--dry-run"] + args)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["names"], {"RELIANCE": "Reliance Industries"})
        self.assertEqual([(i["title"], i["why"]) for i in data["items"] if i["source"] == "nse-announcement"],
                         [("General Updates", "fund raising")])
        self.assertEqual([n for n in data["notes"] if "company names" in n], [])

    def test_honest_notes_when_a_source_has_nothing(self):
        # the real feed of the holiday night: one filing, none of it ours
        real = offline_dir(os.path.join(self.tmp.name, "real"), "nse-announcements.xml")
        args = list(self.base)
        args[args.index("--offline") + 1] = real
        code, out, err = run(["--tickers", "RELIANCE", "--sources", "nse", "--dry-run"] + args)
        self.assertEqual(code, 1, err)
        self.assertIn("Nothing new.", out)
        self.assertIn("note: no NSE announcements for your symbols in the feed's window right now (it holds 1 "
                      "filing, filed 2026-11-24 00:28 IST)", out)
        # the symbol whose PDF is named differently is still found (PTCIL / PTCINDUSTRIES_...pdf)
        code, out, err = run(["--tickers", "PTCIL", "--sources", "nse", "--dry-run"] + args)
        self.assertEqual(code, 0, err)
        self.assertIn("  2026-11-24 00:28 IST  PTCIL       NSE filing    Analysts/Institutional Investor Meet/Con. "
                      "Call Updates: Ptc Industries Limited has informed the Exchange about Schedule of meet", out)
        self.assertNotIn("! Analysts", out)
        # no company name: Google News is skipped and says so; a missing feed file is a note, not a quiet pass
        os.remove(os.path.join(real, "EQUITY_L.csv"))
        os.remove(os.path.join(real, "nse-announcements.xml"))
        code, out, _ = run(["--tickers", "TCS", "--sources", "nse", "google", "--dry-run"] + args)
        self.assertEqual(code, 1)
        self.assertIn("note: company names: offline: no file", out)
        self.assertIn("note: NSE announcements: offline: no file", out)
        self.assertIn('note: Google News skipped for TCS: no company name for it (pass --name "..." or add it to '
                      '"names" in trader/config.json)', out)
        # a pinned clock two days later: the saved headlines are outside 12 hours, and the note names the search
        code, out, _ = run(["--tickers", "M&M", "--sources", "google", "--hours", "12", "--dry-run"] + self.base
                           + ["--now", "2026-11-26 09:00"])
        self.assertEqual(code, 1)
        self.assertIn('note: no headlines from Google News for M&M in the last 12 hours, searched "Mahindra & '
                      'Mahindra"', out)


if __name__ == "__main__":
    unittest.main()
