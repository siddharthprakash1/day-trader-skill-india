import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
import urllib.parse
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

scan = load("trader-scanner", "scan")

# Real answers, all fetched 2026-11-23 19:31 UTC (Tue 2026-11-24 01:01 IST, after Monday's session).
SDIR = os.path.join(FIX, "trader-scanner")
WALL = os.path.join(SDIR, "wall5m-2026-11-23.json.gz")     # 39 real v8 5-min charts of Mon Nov 23 2026 + ZZZZQX
DAILY = os.path.join(SDIR, "daily-spark-3mo")              # two real 20-symbol spark answers, 3mo of 1d closes
LIVE = os.path.join(DAILY, "part1.json")                   # the first of them (19 real names + ZZZZQX)
SPARK5 = os.path.join(SDIR, "spark-5m-5d-part1.json.gz")   # the same 20 symbols, spark 5d of 5m closes
EQL = os.path.join(SDIR, "equity_l")                       # NSE's EQUITY_L.csv of that day, cut to 22 real rows
# NSE's own daily files, downloaded 2026-11-24 from nsearchives.nseindia.com and cut to the fixture symbols:
#   sec_bhavdata_full_23112026.csv   Monday's full bhavcopy: the 39 chart symbols + MANAPPURAM (EQ), one BE row
#   CM_52_wk_High_low_23112026.csv   the same symbols, with the file's two heading lines and a '-' row
#   fo_secban.csv                    whole (one stock)
#   ind_nifty500list.csv             the same symbols + every non-EQ row and the placeholder row of the real list
NSE = os.path.join(SDIR, "nse")
BHAV = os.path.join(NSE, "sec_bhavdata_full_23112026.csv")
PART1 = ("RELIANCE HDFCBANK ICICIBANK SBIN INFY TCS ITC LT AXISBANK KOTAKBANK BHARTIARTL HINDUNILVR BAJFINANCE M&M "
         "MARUTI SUNPHARMA TATASTEEL NTPC POWERGRID ZZZZQX").split()
DT = datetime.datetime
D = datetime.date
MON = D(2026, 11, 23)
UNTIL = datetime.time(14, 30)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


def ts(ist_text):
    """India wall-clock text -> unix seconds."""
    utc = scan.ist_to_utc(DT.strptime(ist_text, "%Y-%m-%d %H:%M:%S" if ist_text.count(":") == 2
                                      else "%Y-%m-%d %H:%M"))
    return int((utc - DT(1970, 1, 1)).total_seconds())


def bars_series(day, closes, highs=None, lows=None, start="09:15", extra=None):
    """A v8-like series of 5-min bars from `start` on `day` (YYYY-MM-DD)."""
    t0 = DT.strptime(day + " " + start, "%Y-%m-%d %H:%M")
    t = [ts((t0 + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M")) for i in range(len(closes))]
    s = {"t": t, "c": list(closes), "h": list(highs or [c + 0.1 if c is not None else None for c in closes]),
         "l": list(lows or [c - 0.1 if c is not None else None for c in closes]), "o": None,
         "v": [1000] * len(closes), "meta": {}}
    for when, c in (extra or []):
        s["t"].append(ts(when))
        for k in ("c", "h", "l"):
            s[k].append(c)
        s["v"].append(10)
    return s


class Universe(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(EQL, "EQUITY_L.csv"), encoding="utf-8") as fh:
            self.text = fh.read()
        self.kept, self.counts = scan.build_universe(self.text)
        self.rows = {r["SYMBOL"]: r for r in scan.parse_equity_list(self.text)}

    def test_the_kept_list_is_series_eq(self):
        self.assertTrue(self.text.startswith("SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING,"))  # NSE's own header
        self.assertEqual([s for s, _ in self.kept],
                         ["20MICRONS", "3MINDIA", "BAJAJ-AUTO", "HDFCBANK", "INFY", "ITC", "J&KBANK", "M&M", "M&MFIN",
                          "NAM-INDIA", "RELIANCE", "SBIN", "TATASTEEL", "TCS", "WIPRO"])
        self.assertEqual(self.counts, {"rows": 22, "keep": 15, "series BE": 4, "series BZ": 3})
        self.assertEqual(dict(self.kept)["M&M"], "Mahindra & Mahindra Limited")
        self.assertEqual(self.rows["RELIANCE"]["ISIN NUMBER"], "INE002A01018")   # header spaces are trimmed

    def test_one_case_per_rule(self):
        cases = {"RELIANCE": "keep", "M&M": "keep", "BAJAJ-AUTO": "keep", "3MINDIA": "keep",
                 "ABAN": "series BE",        # trade-for-trade: no intraday
                 "AGSTRA": "series BZ"}
        for sym, want in cases.items():
            self.assertEqual(scan.classify(self.rows[sym]), want, sym)
        self.assertEqual(scan.classify({"SYMBOL": "ABC$D", "SERIES": "EQ"}), "not a plain symbol")
        self.assertEqual(scan.classify({"SYMBOL": "ABC", "SERIES": ""}), "series blank")
        self.assertEqual(scan.classify({"SYMBOL": "abc", "SERIES": " eq "}), "keep")

    def test_not_the_equity_list(self):
        with self.assertRaises(scan.ScanError):
            scan.parse_equity_list("<html>Access Denied</html>")
        with self.assertRaises(scan.ScanError):
            scan.parse_equity_list("")

    def test_fallback_host(self):
        calls, old_get, old_sleep = [], scan.http_get, scan.SLEEP

        def fake(url, timeout=20):
            calls.append(url)
            if "nsearchives" in url:
                raise urllib.error.HTTPError(url, 403, "x", {}, io.BytesIO(b""))
            return self.text
        scan.http_get, scan.SLEEP = fake, (lambda s: None)
        try:
            self.assertEqual(scan.fetch_equity_list(), self.text)
            self.assertEqual(calls, ["https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
                                     "https://archives.nseindia.com/content/equities/EQUITY_L.csv"])
            scan.http_get = lambda url, timeout=20: (_ for _ in ()).throw(urllib.error.URLError("down"))
            with self.assertRaises(scan.ScanError) as cm:
                scan.fetch_equity_list()
            self.assertIn("--offline", str(cm.exception))
        finally:
            scan.http_get, scan.SLEEP = old_get, old_sleep


class Time(unittest.TestCase):
    def test_ist_is_a_fixed_offset(self):
        self.assertEqual(scan.to_ist(DT(2026, 11, 23, 3, 45)), DT(2026, 11, 23, 9, 15))
        self.assertEqual(scan.to_ist(DT(2026, 6, 30, 20, 0)), DT(2026, 7, 1, 1, 30))       # no daylight saving
        self.assertEqual(scan.ist_to_utc(DT(2026, 11, 23, 9, 15)), DT(2026, 11, 23, 3, 45))
        self.assertEqual(scan.ist_to_utc(DT(2026, 3, 29, 2, 30)), DT(2026, 3, 28, 21, 0))
        self.assertEqual(scan.ts_to_ist(1795405500), DT(2026, 11, 23, 9, 15))             # RELIANCE's first bar
        self.assertFalse(hasattr(scan, "eastern_offset"))

    def test_clock_parsing(self):
        self.assertEqual(scan.parse_now("2026-11-23 10:00"), DT(2026, 11, 23, 4, 30))
        self.assertEqual(scan.parse_now("2026-11-23 10:00 IST"), DT(2026, 11, 23, 4, 30))
        self.assertEqual(scan.parse_now("2026-11-23T04:30:00Z"), DT(2026, 11, 23, 4, 30))
        self.assertEqual(scan.parse_now("2026-11-23T10:00:00+05:30"), DT(2026, 11, 23, 4, 30))
        self.assertEqual(scan.clock_label(DT(2026, 11, 23, 10, 0)), "10:00 AM")
        self.assertEqual(scan.clock_label(datetime.time(15, 30)), "3:30 PM")
        with self.assertRaises(scan.ScanError):
            scan.parse_now("tomorrow")
        with self.assertRaises(scan.ScanError):
            scan.parse_hhmm("25:00")

    def test_nse_calendar_and_session(self):
        self.assertEqual(scan.trading_day(MON), (True, ""))
        self.assertEqual(scan.trading_day(D(2026, 11, 24)), (False, "NSE holiday: Guru Nanak Jayanti"))
        self.assertEqual(scan.trading_day(D(2026, 11, 22)), (False, "weekend"))
        self.assertEqual(scan.trading_day(D(2026, 1, 26))[1], "NSE holiday: Republic Day")
        self.assertEqual(len(scan.NSE_HOLIDAYS), 16)
        self.assertEqual(scan.session(MON), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertIsNone(scan.session(D(2026, 11, 24)))
        self.assertEqual(scan.slot(MON, 74), DT(2026, 11, 23, 15, 25))                    # 75 bars a session
        self.assertFalse(hasattr(scan, "NYSE_EARLY_CLOSE"))

    def test_a_year_without_a_holiday_list(self):
        self.assertEqual(scan.CALENDAR_YEARS, (2026,))
        self.assertEqual(scan.trading_day(D(2027, 1, 26)), (True, ""))                    # weekends only
        self.assertIn("covers 2026 only", scan.calendar_note(D(2027, 1, 26)))
        self.assertEqual(scan.calendar_note(MON), "")


class Symbols(unittest.TestCase):
    def test_ns_suffix_and_quoting(self):
        self.assertEqual(scan.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(scan.yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(scan.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(scan.yahoo_symbol("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(scan.yahoo_symbol("^NSEI"), "^NSEI")
        self.assertEqual(scan.from_yahoo("M&M.NS"), "M&M")
        self.assertEqual(scan.from_yahoo("BAJAJ-AUTO.NS"), "BAJAJ-AUTO")
        self.assertEqual(scan.spark_path(["M&M", "bajaj-auto"], "3mo", "1d"),
                         "/v7/finance/spark?symbols=M%26M.NS,BAJAJ-AUTO.NS&range=3mo&interval=1d")
        self.assertEqual(scan.chart_path("M&M", "1d", "5m"), "/v8/finance/chart/M%26M.NS?range=1d&interval=5m")
        for s in ("RELIANCE", "HDFCBANK", "M&M", "BAJAJ-AUTO", "3MINDIA"):
            self.assertTrue(scan.SYMBOL.match(s), s)


class Spark(unittest.TestCase):
    def test_a_real_spark_answer(self):
        with open(LIVE, encoding="utf-8") as fh:
            text = fh.read()
        data = scan.parse_spark(text, {scan.yahoo_symbol(s): s for s in PART1})
        self.assertEqual(len(data), 19)            # 20 asked; Yahoo silently drops the made-up ZZZZQX
        self.assertNotIn("ZZZZQX", data)
        self.assertIn("M&M", data)                 # M&M.NS comes back as ours
        self.assertEqual(sorted(scan.parse_spark(text)), sorted(data))   # and the .NS is stripped without a map
        t = data["RELIANCE"]
        self.assertEqual(len(t["t"]), 62)
        self.assertIsNone(t["h"])                  # spark is closes only
        self.assertEqual(t["c"][-2], 1377.5)       # Fri Nov 20
        self.assertIsNone(t["c"][-1])              # Mon Nov 23: Yahoo had not filled the daily close yet
        self.assertEqual(t["meta"]["regularMarketVolume"], 15388955)
        self.assertEqual(t["meta"]["currency"], "INR")

    def test_the_in_progress_bar_and_the_unfilled_close(self):
        t = scan.load_offline(DAILY)[0]["RELIANCE"]
        for now in ("2026-11-23 09:00", "2026-11-23 12:00", "2026-11-23 15:29"):
            self.assertEqual(scan.daily_closes(t, scan.parse_now(now))[-1], (D(2026, 11, 20), 1377.5), now)
        # after the close the null bar takes the last price of the session (meta, stamped 15:29:34)
        for now in ("2026-11-23 15:30", "2026-11-24 09:00"):
            self.assertEqual(scan.daily_closes(t, scan.parse_now(now))[-1], (MON, 1351.0), now)
        self.assertEqual(scan.ts_to_ist(t["meta"]["regularMarketTime"]), DT(2026, 11, 23, 15, 29, 34))
        # a price stamped mid-session is not a close: the bar stays out
        mid = dict(t, meta=dict(t["meta"], regularMarketTime=ts("2026-11-23 12:00")))
        self.assertEqual(scan.daily_closes(mid, scan.parse_now("2026-11-24 09:00"))[-1][0], D(2026, 11, 20))
        # and a close Yahoo did fill is never overwritten
        s = {"t": [ts("2026-11-20 09:15"), ts("2026-11-23 09:15")], "c": [10.0, 11.0],
             "meta": {"regularMarketTime": ts("2026-11-23 15:29"), "regularMarketPrice": 99.0}}
        self.assertEqual(scan.daily_closes(s, scan.parse_now("2026-11-24 09:00")), [(D(2026, 11, 20), 10.0),
                                                                                    (MON, 11.0)])

    def test_the_fill_agrees_with_the_last_five_minute_bar(self):
        daily, _ = scan.load_offline(DAILY)
        wall, _ = scan.load_offline(WALL)
        for sym in wall:
            last = [c for c in wall[sym]["c"] if c is not None][-1]
            fill = scan.daily_closes(daily[sym], scan.parse_now("2026-11-24 09:00"))[-1]
            self.assertEqual(fill[0], MON, sym)
            self.assertLess(abs(last / fill[1] - 1), 0.003, sym)

    def test_spark_errors(self):
        too_many = '{"spark":{"result":null,"error":{"code":"Bad Request",' \
                   '"description":"Number of symbols needs to be less than or equal to 20"}}}'
        with self.assertRaises(scan.ScanError) as cm:
            scan.parse_spark(too_many)
        self.assertIn("less than or equal to 20", str(cm.exception))
        self.assertEqual(scan.parse_spark('{"spark":{"result":[],"error":null}}'), {})
        with self.assertRaises(scan.ScanError):
            scan.parse_spark('{"chart":{}}')


class Network(unittest.TestCase):
    """429s and 5xx without the network."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep = scan.http_get, scan.SLEEP
        scan.SLEEP = self.sleeps.append
        with open(LIVE, encoding="utf-8") as fh:
            self.body = fh.read()

    def tearDown(self):
        scan.http_get, scan.SLEEP = self._get, self._sleep

    def answers(self, codes):
        codes = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url.split("/v")[0])
            code = codes.pop(0) if codes else 200
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return self.body
        scan.http_get = fake

    def test_429_falls_back_then_waits_once(self):
        self.answers([429, 429, 200])
        text = scan.yahoo_get("/v7/finance/spark?symbols=RELIANCE.NS&range=3mo&interval=1d")
        self.assertEqual(text, self.body)
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com",
                                      "https://query2.finance.yahoo.com"])
        self.assertEqual(self.sleeps, [scan.BACKOFF])

    def test_5xx_goes_to_query1_without_waiting(self):
        self.answers([503, 200])
        scan.yahoo_get("/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.sleeps, [])

    def test_429_for_good_is_reported_as_partial(self):
        self.answers([200] + [429] * 10)
        syms = PART1 + ["X%02d" % i for i in range(20)]
        data, failed = scan.fetch_spark(syms, "3mo", "1d", 0.3)
        self.assertEqual(len(data), 19)
        self.assertIn("M&M", data)
        self.assertEqual(failed["ZZZZQX"], "no data from Yahoo")
        self.assertEqual(len(failed), 21)
        self.assertIn("HTTP 429", failed["X00"])
        self.assertEqual(self.sleeps, [0.3, scan.BACKOFF])  # a pause between batches, one backoff
        hits, counts, _ = scan.scan_daily(data, syms, scan.parse_now("2026-11-24 09:00"), 50, 500000)
        self.assertEqual(counts["requested"], 40)
        self.assertEqual(counts["fetched"], 19)

    def test_a_404_is_final(self):
        self.answers([404])
        with self.assertRaises(scan.ScanError):
            scan.yahoo_get("/v8/finance/chart/NOPE.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 1)

    def test_the_user_agent_names_nobody(self):
        self.assertEqual(scan.UA, "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)")


class Daily(unittest.TestCase):
    # Before Monday's open: closes through Fri Nov 20 2026. Numbers in the comments are from the closes.
    EXPECT = {
        "RELIANCE": ["breakout_20d", "bull_flag"],   # 1377.5 > 1375.6; up 14.8 % then a 2.4 % range
        "HDFCBANK": ["breakout_20d", "bull_flag"],
        "ICICIBANK": ["breakout_20d", "bull_flag"],
        "BAJFINANCE": ["breakout_20d", "bull_flag"],
        "TRENT": ["breakout_20d", "bull_flag"],
        "SBIN": ["breakout_20d"],                    # 1165.1 > 1156.0 (the last 5 closes span 6.4 %: no flag)
        "M&M": ["breakout_20d"],                     # 3545.0 > 3509.0
        "MARUTI": ["breakout_20d"],
        "AXISBANK": ["breakout_20d"],
        "BHARTIARTL": ["breakout_20d"],
        "KOTAKBANK": ["breakout_20d"],
        "EICHERMOT": ["breakout_20d"],
        "POWERGRID": ["breakout_20d"],
        "ULTRACEMCO": ["breakout_20d"],
        "NESTLEIND": ["breakout_20d"],
        "TATASTEEL": ["bull_flag"],                  # up 10.2 % over the 10 sessions, last 5 in a 2.4 % range
        "ASIANPAINT": ["bull_flag"],                 # up 10.8 %, 2.0 % range
        "HINDALCO": ["reclaim_ma20"],                # 910.0 under 917.36, then 929.5 over 919.55
    }

    def setUp(self):
        self.data, self.errors = scan.load_offline(DAILY)
        self.syms = sorted(self.data)

    def test_setups_before_the_open(self):
        self.assertEqual(len(self.syms), 39)
        hits, counts, last = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-11-23 09:00"), 0, 0)
        self.assertEqual({h["symbol"]: h["setups"] for h in hits}, self.EXPECT)
        self.assertEqual(last, D(2026, 11, 20))
        self.assertEqual(counts, {"requested": 39, "fetched": 39, "illiquid": 0, "short": 0, "scanned": 39,
                                  "hits": 18})
        rel = [h for h in hits if h["symbol"] == "RELIANCE"][0]
        self.assertEqual((rel["price"], rel["rsi"], rel["close_date"]), (1377.5, 75.0, "2026-11-20"))
        self.assertEqual(hits[0]["symbol"], "HDFCBANK")       # most rupees traded first

    def test_the_in_progress_bar_is_dropped(self):
        before = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-11-23 09:00"), 0, 0)
        during = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-11-23 15:29"), 0, 0)
        self.assertEqual(before, during)

    def test_after_the_close_and_on_the_holiday(self):
        # Tue Nov 24 is a holiday: the last session is Monday's, filled from the session's last price
        after = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-11-23 15:30"), 0, 0)
        hol = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-11-24 09:00"), 0, 0)
        self.assertEqual(after, hol)
        hits, counts, last = hol
        self.assertEqual(last, MON)
        got = {h["symbol"]: h["setups"] for h in hits}
        self.assertEqual(got["TCS"], ["reclaim_ma20"])        # 2376.2 under 2457.05, then 2460.0 over 2453.55
        self.assertEqual(got["EICHERMOT"], ["breakout_20d"])  # 8172.5, a new 20-day closing high on a down day
        self.assertNotIn("SBIN", [s for s, t in got.items() if "breakout_20d" in t])   # 1132.65 < 1165.1

    def test_liquidity_filter(self):
        hits, counts, _ = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-11-24 09:00"), 50, 500000)
        self.assertEqual(sorted(h["symbol"] for h in hits), ["ASIANPAINT", "BAJFINANCE", "EICHERMOT", "HDFCBANK",
                                                             "ICICIBANK", "ITC", "NESTLEIND", "RELIANCE", "SBIN",
                                                             "TCS"])
        self.assertEqual(counts["illiquid"], 3)     # BAJAJ-AUTO 382,071, TRENT 436,767, ULTRACEMCO 226,967 shares
        self.assertEqual(counts["scanned"], 36)
        hits, counts, _ = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-11-24 09:00"), 200, 0)
        self.assertEqual(counts["illiquid"], 2)     # TATASTEEL 163.00 and WIPRO 187.29 under a 200-rupee floor

    def test_short_and_empty_charts(self):
        s = {"t": [ts("2026-11-%02d 09:15" % d) for d in range(2, 12)], "c": [100.0] * 10,
             "meta": {"regularMarketPrice": 100, "regularMarketVolume": 5000000}}
        hits, counts, last = scan.scan_daily({"X": s, "E": {"t": [], "c": [], "meta": {}}}, ["X", "E", "GONE"],
                                             scan.parse_now("2026-11-23 09:00"), 50, 500000)
        self.assertEqual(hits, [])
        self.assertEqual((counts["fetched"], counts["short"], counts["illiquid"], counts["scanned"]), (2, 1, 1, 0))
        self.assertIsNone(last)

    def test_rsi(self):
        self.assertIsNone(scan.rsi([1.0] * 10))
        self.assertEqual(scan.rsi([float(x) for x in range(1, 40)]), 100.0)
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61,
                  46.28, 46.28]
        self.assertAlmostEqual(scan.rsi(closes), 70.46, places=1)  # Wilder's textbook example


class Orb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.errors = scan.load_offline(WALL)
        cls.syms = sorted(set(cls.data) | set(cls.errors))

    def scan_at(self, now, min_price=0, syms=None, or_minutes=15):
        day, cutoff = scan.orb_window(scan.parse_now(now), None, or_minutes, UNTIL)
        return scan.scan_orb(self.data, syms or self.syms, day, cutoff, or_minutes, min_price, 0)

    def test_monday_at_ten(self):
        hits, counts, failed = self.scan_at("2026-11-23 10:00")
        self.assertEqual(len(self.syms), 40)
        self.assertEqual(counts["scanned"], 39)           # usable charts
        self.assertEqual(self.errors, {"ZZZZQX": "no data for ZZZZQX: No data found, symbol may be delisted"})
        self.assertEqual(counts["unusable"], 0)
        self.assertEqual([(h["at"], h["symbol"]) for h in hits],       # sorted by time, then symbol
                         [("9:40 AM", "TECHM"), ("9:45 AM", "HINDUNILVR"), ("9:45 AM", "TCS"), ("9:45 AM", "WIPRO"),
                          ("9:50 AM", "INFY")])
        # TCS: range bars 09:15, 09:20, 09:25 -> high 2433.7 (the 09:15 bar), low 2405.0 (the 09:25 bar);
        # the 09:30 and 09:35 bars closed 2408.4 and 2429.0, the 09:40 bar closed 2443.5
        self.assertEqual(hits[2], {"symbol": "TCS", "setups": ["orb"], "price": 2443.5, "at": "9:45 AM",
                                   "bar_start": "09:40", "bar_index": 5, "or_high": 2433.7, "or_low": 2405.0,
                                   "pct_over": 0.4})
        self.assertIn("M&M", self.data)
        self.assertIn("BAJAJ-AUTO", self.data)

    def test_price_floor(self):
        hits, counts, _ = self.scan_at("2026-11-23 10:00", min_price=200)
        self.assertEqual([h["symbol"] for h in hits], ["TECHM", "HINDUNILVR", "TCS", "INFY"])   # WIPRO at 188.75 is out
        self.assertEqual(counts["illiquid"], 2)            # WIPRO and TATASTEEL

    def test_in_progress_bar_and_no_breakout_yet(self):
        hits, _, _ = self.scan_at("2026-11-23 09:44", syms=["TCS"])
        self.assertEqual(hits, [])                         # the 09:40 bar is not done until 09:45
        hits, _, _ = self.scan_at("2026-11-23 09:45", syms=["TCS"])
        self.assertEqual(hits[0]["at"], "9:45 AM")
        hits, _, _ = self.scan_at("2026-11-23 14:30", syms=["TCS"])
        self.assertEqual(hits[0]["price"], 2443.5)         # still the first close above the range

    def test_the_whole_session_is_75_bars(self):
        s = self.data["RELIANCE"]
        self.assertEqual(len(s["t"]), 75)
        self.assertEqual(scan.ts_to_ist(s["t"][0]), DT(2026, 11, 23, 9, 15))
        self.assertEqual(scan.ts_to_ist(s["t"][-1]), DT(2026, 11, 23, 15, 25))
        # Yahoo has no 15:20 bar (a null) on this chart: usable to 15:20, not after
        cut = DT(2026, 11, 23, 15, 20)
        self.assertEqual(len(scan.session_bars(s, MON, cut)), 73)
        self.assertTrue(scan.usable(scan.session_bars(s, MON, cut), MON, cut, 3))
        cut = DT(2026, 11, 23, 15, 30)
        self.assertEqual(len(scan.session_bars(s, MON, cut)), 74)
        self.assertFalse(scan.usable(scan.session_bars(s, MON, cut), MON, cut, 3))
        # a made-up full day: the pre-open 09:10 bar and the 15:30 closing print are not session bars
        full = bars_series("2026-11-23", [100.0] * 77, start="09:10")
        bars = scan.session_bars(full, MON, DT(2026, 11, 23, 16, 0))
        self.assertEqual(len(bars), 75)
        self.assertEqual((bars[0]["start"].strftime("%H:%M"), bars[-1]["start"].strftime("%H:%M")), ("09:15", "15:25"))
        self.assertTrue(scan.usable(bars, MON, DT(2026, 11, 23, 16, 0), 3))

    def test_the_live_tick_is_dropped(self):
        s = bars_series("2026-11-23", [100, 101, 100.5, 100.8, 101.6, 101.7, 101.8, 101.9, 102.0, 102.2],
                        extra=[("2026-11-23 10:03:07", 250.0)])
        cut = DT(2026, 11, 23, 10, 4)
        bars = scan.session_bars(s, MON, cut)
        self.assertEqual(len(bars), 9)                     # 09:15 to 09:55; 10:00 in progress, the tick dropped
        self.assertTrue(scan.usable(bars, MON, cut, 3))
        hits, _, _ = scan.scan_orb({"X": s}, ["X"], MON, cut, 15, 0, 0)
        self.assertEqual((hits[0]["at"], hits[0]["price"], hits[0]["or_high"]), ("9:40 AM", 101.6, 101.1))

    def test_stage_one_is_a_necessary_condition(self):
        cut = DT(2026, 11, 23, 14, 30)
        n = 0
        for sym, s in self.data.items():
            bars = scan.session_bars(s, MON, cut)
            if scan.usable(bars, MON, cut, 3) and scan.orb_hit(bars, 3):
                n += 1
                self.assertTrue(scan.orb_candidate(bars, 3), sym)
        self.assertEqual(n, 7)                             # ... HCLTECH at 10:10 AM and ITC at 1:50 PM as well

    def test_confirm_uses_the_v8_highs(self):
        spark_like = {k: dict(v, h=None, l=None) for k, v in self.data.items()}  # closes only, like spark
        asked = []

        def confirm(sym):
            asked.append(sym)
            return self.data[sym]
        hits, counts, failed = scan.scan_orb(spark_like, self.syms, MON, DT(2026, 11, 23, 10, 0), 15, 0, 0, confirm)
        self.assertEqual(len(hits), 5)
        self.assertEqual((counts["candidates"], len(asked)), (10, 10))   # stage 1 skipped 29 of 39 charts

        def broken(sym):
            raise scan.ScanError("Yahoo answered HTTP 429 for /v8/finance/chart/%s" % sym)
        hits, counts, failed = scan.scan_orb(spark_like, ["TCS", "RELIANCE"], MON, DT(2026, 11, 23, 10, 0), 15, 0, 0,
                                             broken)
        self.assertEqual(hits, [])
        self.assertIn("429", failed["TCS"])

    def test_a_missing_bar_makes_a_chart_unusable(self):
        s = bars_series("2026-11-23", [100, 100.2, 100.1, None, 100.5, 100.6, 100.7, 100.8, 100.9])
        cut = DT(2026, 11, 23, 10, 0)
        self.assertFalse(scan.usable(scan.session_bars(s, MON, cut), MON, cut, 3))
        hits, counts, _ = scan.scan_orb({"X": s}, ["X"], MON, cut, 15, 0, 0)
        self.assertEqual((hits, counts["unusable"], counts["scanned"]), ([], 1, 0))

    def test_a_30_minute_range(self):
        closes = [100, 101, 100.5, 100.8, 101.2, 100.9, 101.5] + [101.0] * 40
        s = bars_series("2026-11-23", closes, highs=[c + 0.2 for c in closes])
        hits, counts, _ = scan.scan_orb({"X": s}, ["X"], MON, DT(2026, 11, 23, 12, 0), 30, 0, 0)
        self.assertEqual(counts["scanned"], 1)
        self.assertEqual(hits[0]["at"], "9:50 AM")          # range 09:15 to 09:45 (high 101.4), the 09:45 bar 101.5
        self.assertEqual(hits[0]["or_high"], 101.4)

    def test_window_messages(self):
        def msg(now, at=None, until=UNTIL):
            with self.assertRaises(scan.ScanError) as cm:
                scan.orb_window(scan.parse_now(now), at, 15, until)
            return str(cm.exception)
        self.assertIn("weekend", msg("2026-11-21 10:00"))
        self.assertIn("NSE holiday: Guru Nanak Jayanti", msg("2026-11-24 10:00"))
        self.assertIn("runs 9:15 to 9:30 AM", msg("2026-11-23 09:20"))
        self.assertIn("Run orb again after 9:35 AM", msg("2026-11-23 09:20"))
        self.assertIn("Run orb again after 9:35 AM", msg("2026-11-23 09:34"))
        self.assertIn("IST", msg("2026-11-23 09:34"))
        self.assertIn("expired", msg("2026-11-23 14:31"))
        self.assertIn("expired", msg("2026-11-23 11:30", until=datetime.time(11, 0)))
        self.assertIn("closed at 3:30 PM IST", msg("2026-11-23 15:30"))
        self.assertIn("later than now", msg("2026-11-23 09:45", at=datetime.time(10, 0)))
        self.assertIn("after the 3:30 PM close", msg("2026-11-23 17:00", at=datetime.time(16, 0)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-11-23 16:30"), datetime.time(10, 0), 15, UNTIL),
                         (MON, DT(2026, 11, 23, 10, 0)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-11-23 09:35"), None, 15, UNTIL)[1],
                         DT(2026, 11, 23, 9, 35))            # the scan is allowed from 09:35
        self.assertEqual(scan.orb_window(scan.parse_now("2026-11-23 14:30"), None, 15, UNTIL)[1],
                         DT(2026, 11, 23, 14, 30))
        with self.assertRaises(scan.ScanError):
            scan.orb_window(scan.parse_now("2026-11-23 10:00"), None, 7, UNTIL)


class SparkGaps(unittest.TestCase):
    """Yahoo's real spark 5-minute answer for NSE leaves out bars that its v8 chart has."""

    @classmethod
    def setUpClass(cls):
        cls.spark = scan.parse_spark(scan._read_maybe_gz(SPARK5))
        cls.wall, _ = scan.load_offline(WALL)
        cls.syms = sorted(cls.spark)

    def starts(self, series, cut):
        return [b["start"].strftime("%H:%M") for b in scan.session_bars(series, MON, cut)]

    def test_the_gaps_are_in_spark_only(self):
        cut = DT(2026, 11, 23, 10, 5)
        self.assertEqual(len(self.spark), 19)
        self.assertIn("M&M", self.spark)
        self.assertIsNone(self.spark["SUNPHARMA"]["h"])
        self.assertNotIn("09:20", self.starts(self.spark["SUNPHARMA"], cut))     # a range bar
        self.assertNotIn("10:00", self.starts(self.spark["INFY"], cut))
        self.assertIn("09:20", self.starts(self.wall["SUNPHARMA"], cut))
        self.assertIn("10:00", self.starts(self.wall["INFY"], cut))
        self.assertEqual(len(self.starts(self.wall["INFY"], cut)), 10)
        # other days ride along in a 5d answer and are not Monday's bars
        days = {scan.ts_to_ist(t).date() for t in self.spark["RELIANCE"]["t"]}
        self.assertEqual(sorted(days), [D(2026, 11, 17), D(2026, 11, 18), D(2026, 11, 19), D(2026, 11, 20), MON])

    def test_a_gappy_chart_goes_to_the_full_chart(self):
        asked = []

        def confirm(sym):
            asked.append(sym)
            return self.wall[sym]
        cut = DT(2026, 11, 23, 10, 0)
        hits, counts, failed = scan.scan_orb(self.spark, self.syms, MON, cut, 15, 0, 0, confirm)
        self.assertEqual(asked, ["HINDUNILVR", "INFY", "SUNPHARMA", "TCS"])   # SUNPHARMA only for its gap
        self.assertEqual((counts["scanned"], counts["gaps"], counts["unusable"], counts["candidates"]),
                         (19, 1, 0, 4))
        self.assertEqual([h["symbol"] for h in hits], ["HINDUNILVR", "TCS", "INFY"])
        self.assertEqual(failed, {})
        # the same answer as scanning the 19 full charts
        want, _, _ = scan.scan_orb(self.wall, self.syms, MON, cut, 15, 0, 0)
        self.assertEqual(hits, want)
        # half an hour on, INFY (10:00) and KOTAKBANK (10:25) have gaps too
        cut = DT(2026, 11, 23, 10, 30)
        hits, counts, _ = scan.scan_orb(self.spark, self.syms, MON, cut, 15, 0, 0, confirm)
        self.assertEqual(counts["gaps"], 3)
        self.assertEqual(hits, scan.scan_orb(self.wall, self.syms, MON, cut, 15, 0, 0)[0])

    def test_without_a_full_chart_a_gap_is_unusable(self):
        hits, counts, _ = scan.scan_orb(self.spark, self.syms, MON, DT(2026, 11, 23, 10, 0), 15, 0, 0)
        self.assertEqual((counts["unusable"], counts["scanned"], counts["gaps"]), (1, 18, 0))
        self.assertEqual(hits, [])                         # closes only: no highs, so nothing can be confirmed

    def test_a_real_gap_stays_unusable(self):
        thin = bars_series("2026-11-23", [100, 100.2, 100.1, None, 100.5, 100.6, 100.7, 100.8, 100.9])
        closes_only = dict(thin, h=None, l=None)
        hits, counts, failed = scan.scan_orb({"THIN": closes_only}, ["THIN"], MON, DT(2026, 11, 23, 10, 0), 15, 0, 0,
                                             lambda sym: thin)
        self.assertEqual((hits, failed), ([], {}))
        self.assertEqual((counts["unusable"], counts["scanned"], counts["gaps"], counts["candidates"]), (1, 0, 0, 0))

    def test_stage_two_cap_takes_the_most_traded(self):
        spark_like = {k: dict(v, h=None, l=None) for k, v in self.wall.items()}
        asked = []

        def confirm(sym):
            asked.append(sym)
            return self.wall[sym]
        cut = DT(2026, 11, 23, 10, 0)
        hits, counts, _ = scan.scan_orb(spark_like, sorted(self.wall), MON, cut, 15, 0, 0, confirm, max_confirm=3)
        self.assertEqual((counts["confirmed"], counts["unconfirmed"]), (3, 7))
        self.assertEqual(len(asked), 3)
        hits_all, _, _ = scan.scan_orb(spark_like, sorted(self.wall), MON, cut, 15, 0, 0, confirm, max_confirm=0)
        self.assertEqual(len(hits_all), 5)
        for h in hits:
            self.assertIn(h, hits_all)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.t = os.path.join(self.d, "trader")
        self.wl = os.path.join(self.t, "watchlist.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_universe_daily_orb_end_to_end(self):
        uni = os.path.join(self.t, "universe.txt")
        code, text, _ = run(["universe", "--offline", EQL, "--out", uni])
        self.assertEqual(code, 0)
        self.assertIn("EQUITY_L.csv): 22 rows", text)
        self.assertIn("Kept 15 NSE stocks in series EQ", text)
        self.assertIn("Dropped: 4 series BE, 3 series BZ", text)
        with open(uni, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 15)
        self.assertIn("M&M|Mahindra & Mahindra Limited", lines)
        self.assertIn("BAJAJ-AUTO|Bajaj Auto Limited", lines)
        code, text, _ = run(["universe", "--offline", os.path.join(EQL, "EQUITY_L.csv"), "--no-write", "--json"])
        self.assertEqual(json.loads(text)["count"], 15)

        # daily over that universe with the fixture charts: 10 of the 15 are in both, so partial
        code, text, _ = run(["daily", "--universe", uni, "--offline", DAILY, "--now", "2026-11-23 09:00",
                             "--out", self.wl])
        self.assertEqual(code, 0)
        self.assertIn("Daily scan, Mon 2026-11-23 09:00 IST, closes through 2026-11-20", text)
        self.assertIn("Fetched 10 of 15 charts (5 failed or had no data)", text)
        self.assertIn("(₹50+ and 500,000+ shares)", text)
        self.assertIn("  RELIANCE       ₹1,377.50  RSI  75.0  20-day closing-high breakout, bull flag", text)
        self.assertIn("  M&M            ₹3,545.00  RSI  73.9  20-day closing-high breakout", text)
        self.assertNotIn("$", text)
        doc = self.read(self.wl)
        self.assertEqual((doc["mode"], doc["universe"], doc["scanned"], doc["failed"]), ("daily", 15, 9, 5))
        self.assertEqual([h["symbol"] for h in doc["hits"]], ["HDFCBANK", "TATASTEEL", "RELIANCE", "SBIN", "M&M"])
        self.assertEqual(doc["hits"][4], {"symbol": "M&M", "setups": ["breakout_20d"], "price": 3545.0,
                                          "close_date": "2026-11-20", "rsi": 73.9, "volume": 2692976})
        self.assertEqual(doc["as_of"], "2026-11-20 close")
        self.assertEqual(doc["filters"], {"min_price": 50.0, "min_volume": 500000})

        # Monday's 10:00 scan
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-11-23 10:00", "--out", self.wl])
        self.assertEqual(code, 0)
        self.assertIn("Opening-range breakout scan, Mon 2026-11-23 10:00 AM IST: 15-minute range (9:15 to 9:30 AM)",
                      text)
        self.assertIn("Fetched 39 of 40 charts (1 failed or had no data); 39 usable; 10 candidates, 10 confirmed",
                      text)
        self.assertIn("5 of 39 charts closed above their opening-range high by 10:00 AM", text)
        self.assertIn("   9:45 AM  TCS            ₹2,443.50  range 2,405.00 to 2,433.70  +0.40% over", text)
        self.assertIn("not a list of stocks to buy", text)
        doc = self.read(self.wl)
        self.assertEqual({k: doc[k] for k in ("created_by", "created_at", "mode", "as_of", "universe", "scanned",
                                              "source")},
                         {"created_by": "trader-scanner", "created_at": "2026-11-23T04:30:00Z", "mode": "orb",
                          "as_of": "2026-11-23 10:00 IST", "universe": 40, "scanned": 39, "source": "yahoo"})
        self.assertEqual(len(doc["hits"]), 5)
        self.assertIn({"symbol": "TCS", "setups": ["orb"], "price": 2443.5, "at": "9:45 AM", "bar_start": "09:40",
                       "bar_index": 5, "or_high": 2433.7, "or_low": 2405.0, "pct_over": 0.4}, doc["hits"])
        self.assertEqual(sorted(doc["hits"][0]), ["at", "bar_index", "bar_start", "or_high", "or_low", "pct_over",
                                                  "price", "setups", "symbol"])     # the shape trader-entry reads

        # the hits read back as a watchlist, with --at instead of --now
        code, text, _ = run(["orb", "--offline", WALL, "--at", "10:00", "--watchlist", self.wl, "--json",
                             "--no-write"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], len(out["hits"])), (5, 5, 5))

    def fake_yahoo(self, machine_now, argv):
        """The networked path with a fake Yahoo that answers from Monday's bundle (spark = closes only)."""
        bundle = json.loads(scan._read_maybe_gz(WALL))
        calls = []

        def get(path):
            calls.append(path)
            if path.startswith("/v7/finance/spark"):
                res = []
                for y in path.split("symbols=")[1].split("&")[0].split(","):
                    y = urllib.parse.unquote(y)
                    self.assertTrue(y.endswith(".NS"), y)
                    r = ((bundle.get(y[:-3]) or {}).get("chart") or {}).get("result")
                    if r:
                        closes = r[0]["indicators"]["quote"][0].get("close", [])
                        res.append({"symbol": y, "response": [dict(r[0], indicators={"quote": [{"close": closes}]})]})
                return json.dumps({"spark": {"result": res, "error": None}})
            return json.dumps(bundle[urllib.parse.unquote(path.split("/chart/")[1].split("?")[0])[:-3]])
        old_get, old_sleep, real_now = scan.yahoo_get, scan.SLEEP, scan.parse_now
        scan.yahoo_get, scan.SLEEP = get, (lambda s: None)
        scan.parse_now = lambda text: real_now(text or machine_now)  # the machine clock, pinned
        uni = os.path.join(self.d, "u.txt")
        with open(uni, "w", encoding="utf-8") as fh:
            fh.write("\n".join(sorted(bundle)) + "\n")
        try:
            code, text, err = run(["orb", "--universe", uni] + argv)
        finally:
            scan.yahoo_get, scan.SLEEP, scan.parse_now = old_get, old_sleep, real_now
        return code, text, err, calls

    def test_partial_stage_two_is_said(self):
        code, text, _, calls = self.fake_yahoo("2026-11-23 10:05", ["--now", "2026-11-23 10:00", "--max-confirm", "3",
                                                                    "--no-write"])
        self.assertEqual(code, 0)
        self.assertIn("39 usable; 10 candidates, 3 confirmed", text)
        self.assertIn("PARTIAL: 7 more candidates were not checked", text)
        self.assertIn("of the 3 checked charts closed above their opening-range high by 10:00 AM", text)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v8/")), 3)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v7/")), 2)    # 40 symbols, 20 a request
        self.assertTrue(all("range=1d&interval=5m" in c for c in calls))
        self.assertTrue(any("M%26M.NS" in c for c in calls))                  # & never goes raw into a URL
        self.assertFalse(any("M&M" in c for c in calls))

    def test_replaying_monday_from_the_holiday(self):
        # run on Tue Nov 24 (a holiday) with --now on Monday: the 5-day range covers it
        code, text, _, calls = self.fake_yahoo("2026-11-24 01:00", ["--now", "2026-11-23 10:00", "--no-write"])
        self.assertEqual(code, 0)
        self.assertIn("5 of 39 charts closed above their opening-range high by 10:00 AM", text)
        self.assertTrue(all("range=5d&interval=5m" in c for c in calls))
        self.assertEqual(sum(1 for c in calls if c.startswith("/v8/")), 10)
        # a day too far back for Yahoo's free 5-minute history
        code, _, err, calls = self.fake_yahoo("2026-11-24 01:00", ["--now", "2026-11-09 10:00", "--no-write"])
        self.assertEqual(code, 1)
        self.assertIn("--offline", err)
        self.assertEqual(calls, [])
        # with no --now on the holiday itself there is nothing to scan
        code, _, err, calls = self.fake_yahoo("2026-11-24 10:00", ["--no-write"])
        self.assertEqual(code, 1)
        self.assertIn("No session on Tue 2026-11-24 (NSE holiday: Guru Nanak Jayanti)", err)
        self.assertEqual(calls, [])

    def test_plain_messages_and_exit_codes(self):
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-11-21 10:00", "--out", self.wl])
        self.assertEqual(code, 1)
        self.assertIn("No session on Sat 2026-11-21 (weekend)", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.wl))
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-11-23 09:29", "--out", self.wl])
        self.assertIn("after 9:35 AM", err)
        # the default last entry time is 14:30
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-11-23 14:45",
                            "--config", os.path.join(self.d, "none.json")])
        self.assertEqual(code, 1)
        self.assertIn("after 14:30", err)
        cfg = os.path.join(self.t, "config.json")
        os.makedirs(self.t)
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"no_entries_after": "09:45"}, fh)
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-11-23 10:00", "--config", cfg])
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        code, _, err = run(["orb", "--offline", WALL])
        self.assertIn("--at HH:MM", err)
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-11-23 10:00", "--symbols", "RELIANCE",
                             "--out", self.wl])
        self.assertEqual(code, 1)                          # no breakout, but the empty list is written
        self.assertIn("No chart closed above its opening-range high by 10:00 AM", text)
        self.assertEqual(self.read(self.wl)["hits"], [])
        code, _, err = run(["daily", "--universe", os.path.join(self.d, "nope.txt")])
        self.assertEqual(code, 1)
        self.assertIn("scan.py universe", err)
        code, _, err = run(["daily", "--offline", os.path.join(self.d, "nothing-here")])
        self.assertIn("no offline file", err)
        code, _, err = run(["universe", "--offline", os.path.join(self.d, "nothing-here")])
        self.assertEqual(code, 1)
        self.assertIn("no EQUITY_L.csv", err)
        empty = os.path.join(self.d, "empty.json")
        with open(empty, "w", encoding="utf-8") as fh:
            fh.write("{}")
        code, _, err = run(["daily", "--offline", empty])
        self.assertEqual(code, 1)
        self.assertIn("no symbols to scan", err)

    def test_offline_folder_of_v8_files(self):
        folder = os.path.join(self.d, "charts")
        os.makedirs(folder)
        bundle = json.loads(scan._read_maybe_gz(WALL))
        for sym in ("TCS", "RELIANCE", "M&M", "BAJAJ-AUTO", "ZZZZQX"):
            with open(os.path.join(folder, sym + ".json"), "w", encoding="utf-8") as fh:
                json.dump(bundle[sym], fh)
        code, text, _ = run(["orb", "--offline", folder, "--now", "2026-11-23 10:00", "--no-write", "--json"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], out["failed"]), (5, 4, 1))
        self.assertEqual([h["symbol"] for h in out["hits"]], ["TCS"])
        self.assertEqual(code, 0)

    def test_symbol_lists(self):
        txt = os.path.join(self.d, "list.txt")
        with open(txt, "w", encoding="utf-8") as fh:
            fh.write("# my list\nM&M|Mahindra & Mahindra Limited\nbajaj-auto\n\nM&M\n")
        self.assertEqual(scan.symbols_from_file(txt), ["M&M", "BAJAJ-AUTO", "M&M"])
        js = os.path.join(self.d, "wl.json")
        with open(js, "w", encoding="utf-8") as fh:
            json.dump({"hits": [{"symbol": "m&m"}, {"symbol": "TCS"}]}, fh)
        self.assertEqual(scan.symbols_from_file(js), ["M&M", "TCS"])
        code, text, _ = run(["daily", "--offline", DAILY, "--watchlist", txt, "--now", "2026-11-23 09:00",
                             "--json", "--no-write", "--min-volume", "0"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"]), (2, 2))
        self.assertEqual([h["symbol"] for h in out["hits"]], ["M&M"])
        code, text, _ = run(["daily", "--offline", DAILY, "--symbols", "m&m, bajaj-auto,RELIANCE", "--now",
                             "2026-11-23 09:00", "--json", "--no-write", "--min-volume", "0"])
        self.assertEqual(json.loads(text)["universe"], 3)
        code, text, _ = run(["daily", "--offline", DAILY, "--limit", "3", "--now", "2026-11-23 09:00", "--json",
                             "--no-write", "--min-volume", "0"])
        self.assertEqual(json.loads(text)["universe"], 3)

    def test_daily_json(self):
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-11-23 09:00", "--json", "--no-write",
                             "--min-volume", "0", "--min-price", "0"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(len(out["hits"]), 18)
        self.assertEqual(out["as_of"], "2026-11-20 close")
        self.assertEqual(out["note"], "A scan is a list of charts that match a rule, not a list of stocks to buy.")

    def test_defaults(self):
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-11-24 09:00", "--json", "--no-write"])
        out = json.loads(text)
        self.assertEqual(out["filters"], {"min_price": 50.0, "min_volume": 500000})
        self.assertEqual((out["as_of"], out["scanned"], len(out["hits"])), ("2026-11-23 close", 36, 10))
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-11-23 10:00", "--json", "--no-write"])
        self.assertEqual(json.loads(text)["filters"], {"min_price": 50.0, "min_volume": 0})


def nse_text(name):
    with open(os.path.join(NSE, name), encoding="utf-8") as fh:
        return fh.read()


class NseFiles(unittest.TestCase):
    """NSE's own published files: the bhavcopy, the ban list, the 52-week file, the Nifty 500 list."""

    @classmethod
    def setUpClass(cls):
        cls.day, cls.rows = scan.parse_bhavcopy(nse_text("sec_bhavdata_full_23112026.csv"))
        cls.hilo = scan.parse_hilo(nse_text("CM_52_wk_High_low_23112026.csv"))
        cls.data, _ = scan.load_offline(DAILY)
        cls.syms = sorted(cls.data)

    def test_latest_completed_session(self):
        for now, want in (("2026-11-23 15:29", D(2026, 11, 20)),      # Monday, still open: Friday
                          ("2026-11-23 15:30", MON),
                          ("2026-11-24 01:00", MON),                  # the holiday
                          ("2026-11-25 09:00", MON),                  # Wednesday before the close: the holiday is skipped
                          ("2026-11-25 16:00", D(2026, 11, 25)),
                          ("2026-11-22 12:00", D(2026, 11, 20))):     # Sunday
            self.assertEqual(scan.latest_session(scan.parse_now(now)), want, now)
        self.assertEqual(scan.prev_trading_day(MON), D(2026, 11, 20))
        self.assertEqual(scan.prev_trading_day(D(2026, 11, 25)), MON)

    def test_the_bhavcopy(self):
        text = nse_text("sec_bhavdata_full_23112026.csv")
        self.assertTrue(text.startswith("SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE,"))   # NSE's own header
        self.assertEqual(self.day, MON)
        self.assertEqual(len(self.rows), 40)                 # 41 rows in the cut, the series BE one is out
        self.assertNotIn("21STCENMGM", self.rows)
        # RELIANCE, EQ, 23-Nov-2026, ..., CLOSE 1351.00, ..., 15392522, 209437.69 (lakhs), ..., 53.91
        self.assertEqual(self.rows["RELIANCE"], {"close": 1351.0, "volume": 15392522, "turnover_cr": 2094.38,
                                                 "delivery_pct": 53.91})
        self.assertEqual(self.rows["M&M"]["close"], 3487.4)
        self.assertEqual(set(self.syms) - set(self.rows), set())
        for bad in ("<html>Not Found</html>", "", "SYMBOL,NAME OF COMPANY, SERIES\nX,Y,EQ\n"):
            with self.assertRaises(scan.ScanError):
                scan.parse_bhavcopy(bad)
        with self.assertRaises(scan.ScanError) as cm:
            scan.bhavcopy_for(D(2026, 11, 20))(text)         # Monday's file is not Friday's
        self.assertIn("is for 2026-11-23, not 2026-11-20", str(cm.exception))
        self.assertEqual(scan.nse_date("23-Nov-2026"), MON)
        self.assertEqual(scan.nse_date("25-NOV-2026"), D(2026, 11, 25))
        self.assertIsNone(scan.nse_date("-"))
        self.assertIsNone(scan._num(" - "))
        self.assertEqual(scan._num("   8230.00"), 8230.0)

    def test_the_official_close_fills_the_empty_bar(self):
        t = self.data["RELIANCE"]
        after = scan.parse_now("2026-11-24 09:00")
        self.assertEqual(scan.daily_bars(t, after), (scan.daily_closes(t, after), "last_price"))
        bars, src = scan.daily_bars(t, after, (MON, 1350.25))          # a made-up close, to tell the two apart
        self.assertEqual((bars[-1], src), ((MON, 1350.25), "bhavcopy"))
        self.assertEqual(scan.daily_closes(t, after, (MON, 1350.25))[-1], (MON, 1350.25))
        # not before the session is over
        bars, src = scan.daily_bars(t, scan.parse_now("2026-11-23 15:29"), (MON, 1350.25))
        self.assertEqual((bars[-1], src), ((D(2026, 11, 20), 1377.5), "yahoo"))
        # a close Yahoo did fill is never overwritten
        s = {"t": [ts("2026-11-20 09:15"), ts("2026-11-23 09:15")], "c": [10.0, 11.0], "meta": {}}
        self.assertEqual(scan.daily_bars(s, after, (MON, 12.0)), ([(D(2026, 11, 20), 10.0), (MON, 11.0)], "yahoo"))
        # a bar that is missing altogether is added only right after the session before it
        s = {"t": [ts("2026-11-19 09:15"), ts("2026-11-20 09:15")], "c": [9.0, 10.0], "meta": {}}
        self.assertEqual(scan.daily_bars(s, after, (MON, 12.0))[0][-1], (MON, 12.0))
        s = {"t": [ts("2026-11-18 09:15"), ts("2026-11-19 09:15")], "c": [9.0, 10.0], "meta": {}}
        self.assertEqual(scan.daily_bars(s, after, (MON, 12.0)), ([(D(2026, 11, 18), 9.0), (D(2026, 11, 19), 10.0)],
                                                                  "yahoo"))
        self.assertEqual(scan.daily_bars({"t": [], "c": [], "meta": {}}, after, (MON, 12.0)), ([], None))

    def test_the_scan_with_the_bhavcopy(self):
        now = scan.parse_now("2026-11-24 09:00")
        tally, plain = {}, {}
        hits, counts, last = scan.scan_daily(self.data, self.syms, now, 50, 500000, (self.day, self.rows), tally)
        old = scan.scan_daily(self.data, self.syms, now, 50, 500000, None, plain)
        self.assertEqual((tally, plain), ({"bhavcopy": 36}, {"last_price": 36}))
        self.assertEqual(last, MON)
        # on this day Yahoo's last price was already the official close for all 39, so the same charts match
        self.assertEqual([s for s in self.syms if self.data[s]["meta"]["regularMarketPrice"] != self.rows[s]["close"]],
                         [])
        self.assertEqual([(h["symbol"], h["setups"], h["price"]) for h in hits],
                         [(h["symbol"], h["setups"], h["price"]) for h in old[0]])
        self.assertEqual(counts, old[1])
        # the volume is the bhavcopy's TTL_TRD_QNTY, not Yahoo's regularMarketVolume (15,388,955)
        rel = [h for h in hits if h["symbol"] == "RELIANCE"][0]
        self.assertEqual((rel["volume"], [h for h in old[0] if h["symbol"] == "RELIANCE"][0]["volume"]),
                         (15392522, 15388955))
        # Monday before the open: Monday's file has nothing to say about Friday's closes
        tally = {}
        hits, _, last = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-11-23 09:00"), 50, 500000,
                                        (self.day, self.rows), tally)
        self.assertEqual((last, tally), (D(2026, 11, 20), {"yahoo": 36}))
        # a symbol that is not in the bhavcopy's EQ rows keeps the last-price fill
        few = {k: v for k, v in self.rows.items() if k != "TCS"}
        tally = {}
        scan.scan_daily(self.data, self.syms, now, 50, 500000, (self.day, few), tally)
        self.assertEqual(tally, {"bhavcopy": 35, "last_price": 1})

    def test_the_ban_list_and_the_52_week_file(self):
        self.assertEqual(scan.parse_ban(nse_text("fo_secban.csv")), ("25-NOV-2026", {"MANAPPURAM"}))
        self.assertEqual(scan.parse_ban("Securities in Ban For Trade Date 25-NOV-2026:\n"), ("25-NOV-2026", set()))
        with self.assertRaises(scan.ScanError):
            scan.parse_ban("<html>Access Denied</html>")
        self.assertTrue(nse_text("CM_52_wk_High_low_23112026.csv").startswith('"Disclaimer - '))
        self.assertEqual(len(self.hilo), 40)                 # the BE row and the '-' row (1STCUS$) are out
        self.assertEqual((self.hilo["EICHERMOT"], self.hilo["M&M"]), (8230.0, 3839.9))
        with self.assertRaises(scan.ScanError):
            scan.parse_hilo("<html>Not Found</html>")

    def test_the_optional_fields_on_a_hit(self):
        now = scan.parse_now("2026-11-24 09:00")
        hits, _, _ = scan.scan_daily(self.data, self.syms, now, 50, 500000, (self.day, self.rows))
        industry = scan.build_index_universe(nse_text("ind_nifty500list.csv"))[1]
        scan.enrich_hits(hits, (self.day, self.rows), {"MANAPPURAM", "SBIN"}, self.hilo, industry)   # SBIN: made up
        by = {h["symbol"]: h for h in hits}
        self.assertEqual(by["RELIANCE"], {"symbol": "RELIANCE", "setups": ["bull_flag"], "price": 1351.0,
                                          "close_date": "2026-11-23", "rsi": 64.8, "volume": 15392522,
                                          "delivery_pct": 53.91, "turnover_cr": 2094.38,
                                          "industry": "Oil Gas & Consumable Fuels"})
        # EICHERMOT closed at 8172.50; its 52-week high before Monday was 8230.00 (0.7 % above)
        self.assertEqual((by["EICHERMOT"]["near_52w_high"], by["EICHERMOT"]["high_52w"]), (True, 8230.0))
        self.assertEqual([s for s, h in by.items() if h.get("near_52w_high")], ["EICHERMOT"])
        self.assertEqual([s for s, h in by.items() if h.get("fno_ban")], ["SBIN"])
        self.assertNotIn("fno_ban", by["RELIANCE"])
        # a hit whose close is another day's gets no figures of this session
        stale = [{"symbol": "RELIANCE", "setups": ["bull_flag"], "price": 1377.5, "close_date": "2026-11-20",
                  "rsi": 75.0, "volume": 1}]
        scan.enrich_hits(stale, (self.day, self.rows), set(), self.hilo, {})
        self.assertEqual(sorted(stale[0]), ["close_date", "price", "rsi", "setups", "symbol", "volume"])
        # and with nothing to add, nothing is added
        bare = [dict(stale[0])]
        self.assertEqual(scan.enrich_hits(bare), stale)

    def test_the_nifty_500_list(self):
        text = nse_text("ind_nifty500list.csv")
        self.assertTrue(text.startswith("Company Name,Industry,Symbol,Series,ISIN Code"))
        kept, industry, counts = scan.build_index_universe(text)
        self.assertEqual(counts, {"rows": 46, "keep": 40, "series RR": 3, "series BE": 2,
                                  "placeholder without a real ISIN": 1})     # REITs, trade-for-trade, DUMMYHEG
        self.assertEqual(dict(kept)["M&M"], "Mahindra & Mahindra Ltd.")
        self.assertEqual(industry["M&M"], "Automobile and Auto Components")
        self.assertEqual(sorted(industry), [s for s, _ in kept])
        self.assertNotIn("DUMMYHEG", industry)
        self.assertNotIn("EMBASSY", industry)
        with self.assertRaises(scan.ScanError):
            scan.build_index_universe(nse_text("sec_bhavdata_full_23112026.csv"))


class NseDownloads(unittest.TestCase):
    """nse_file without the network: one plain GET, a saved copy, a 404."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.state = os.path.join(self.d, "trader", "state")
        self.calls, self.sleeps, self.answers = [], [], {}
        self._get, self._sleep = scan.http_get, scan.SLEEP
        scan.SLEEP = self.sleeps.append

        def fake(url, timeout=20):
            self.calls.append(url)
            name = url.rsplit("/", 1)[1]
            if name not in self.answers:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"<html>"))
            return self.answers[name]
        scan.http_get = fake

    def tearDown(self):
        scan.http_get, scan.SLEEP = self._get, self._sleep
        shutil.rmtree(self.d)

    def test_download_once_then_the_saved_copy(self):
        name = "sec_bhavdata_full_23112026.csv"
        self.answers[name] = nse_text(name)
        os.makedirs(self.state)
        with open(os.path.join(self.state, "sec_bhavdata_full_20112026.csv"), "w") as fh:
            fh.write("an older copy")
        got, where = scan.nse_file(scan.BHAV_DIR, name, scan.bhavcopy_for(MON), cache_dir=self.state)
        self.assertEqual((got[0], len(got[1]), where), (MON, 40, "downloaded from NSE"))
        self.assertEqual(self.calls, ["https://nsearchives.nseindia.com/products/content/" + name])
        with open(os.path.join(self.state, name), encoding="utf-8", newline="") as fh:
            self.assertEqual(fh.read(), self.answers[name])                  # saved as it came
        got2, where = scan.nse_file(scan.BHAV_DIR, name, scan.bhavcopy_for(MON), cache_dir=self.state)
        self.assertEqual((got2, len(self.calls)), (got, 1))                  # no second request
        self.assertTrue(where.startswith("the copy saved in "))
        scan.prune_cache(self.state, scan.BHAV_NAME, name)
        self.assertEqual(os.listdir(self.state), [name])
        # save=False (a --no-write run) leaves nothing behind
        other = os.path.join(self.d, "other")
        scan.nse_file(scan.BHAV_DIR, name, scan.bhavcopy_for(MON), cache_dir=other, save=False)
        self.assertFalse(os.path.exists(other))

    def test_a_404_and_a_page_that_is_not_the_file(self):
        name = "sec_bhavdata_full_25112026.csv"
        net = {}
        self.assertEqual(scan.nse_file(scan.BHAV_DIR, name, scan.bhavcopy_for(D(2026, 11, 25)), cache_dir=self.state,
                                       net=net), (None, "HTTP 404"))
        self.assertEqual(len(self.calls), 1)                                 # no retry, no other host
        self.assertFalse(os.path.exists(self.state))
        self.answers[name] = "<html>Access Denied</html>"
        got, why = scan.nse_file(scan.BHAV_DIR, name, scan.bhavcopy_for(D(2026, 11, 25)), cache_dir=self.state,
                                 net=net)
        self.assertIsNone(got)
        self.assertIn("not NSE's full bhavcopy", why)
        self.assertFalse(os.path.exists(self.state))                         # a bad answer is never saved
        self.assertEqual(self.sleeps, [scan.NSE_PAUSE])                      # half a second between two requests
        self.assertEqual(scan.NSE_PAUSE, 0.5)
        scan.http_get = lambda url, timeout=20: (_ for _ in ()).throw(urllib.error.URLError("down"))
        self.assertEqual(scan.nse_file(scan.BAN_DIR, "fo_secban.csv", scan.parse_ban), (None, "down"))

    def test_a_folder_of_saved_files_and_offline(self):
        got, where = scan.nse_file(scan.BAN_DIR, "fo_secban.csv", scan.parse_ban, nse_dir=NSE)
        self.assertEqual((got, where), (("25-NOV-2026", {"MANAPPURAM"}), "the --nse folder"))
        got, why = scan.nse_file(scan.BHAV_DIR, "sec_bhavdata_full_20112026.csv", scan.bhavcopy_for(D(2026, 11, 20)),
                                 nse_dir=NSE)
        self.assertEqual((got, why), (None, "no sec_bhavdata_full_20112026.csv in the --nse folder"))
        self.assertEqual(scan.nse_file(scan.BAN_DIR, "fo_secban.csv", scan.parse_ban, offline=True),
                         (None, "an offline run without --nse"))
        self.assertEqual(self.calls, [])

    def daily_live(self, argv):
        """`daily` on the networked path: a fake Yahoo answering from the two saved spark answers."""
        docs = [json.loads(scan._read_maybe_gz(os.path.join(DAILY, f))) for f in ("part1.json", "part2.json")]
        both = json.dumps({"spark": {"result": docs[0]["spark"]["result"] + docs[1]["spark"]["result"],
                                     "error": None}})
        syms = sorted(scan.parse_spark(both))
        old = scan.yahoo_get
        scan.yahoo_get = lambda path: both
        try:
            return run(["daily", "--symbols", ",".join(syms), "--out", os.path.join(self.d, "trader", "wl.json")]
                       + argv)
        finally:
            scan.yahoo_get = old

    def test_daily_downloads_each_file_once_and_keeps_them(self):
        for name in ("sec_bhavdata_full_23112026.csv", "CM_52_wk_High_low_23112026.csv", "fo_secban.csv"):
            self.answers[name] = nse_text(name)
        code, text, _ = self.daily_live(["--now", "2026-11-23 18:00"])
        self.assertEqual(code, 0)
        base = "https://nsearchives.nseindia.com/"
        self.assertEqual(self.calls, [base + "products/content/sec_bhavdata_full_23112026.csv",
                                      base + "content/fo/fo_secban.csv",
                                      base + "content/CM_52_wk_High_low_23112026.csv"])
        self.assertEqual(self.sleeps, [0.3, 0.5, 0.5])     # one between the two Yahoo requests, then NSE's two
        self.assertIn("Official closes: NSE's bhavcopy of 2026-11-23 (sec_bhavdata_full_23112026.csv, downloaded "
                      "from NSE) gave the close and the volume of that day for 36 charts that Yahoo had left empty.",
                      text)
        self.assertEqual(sorted(os.listdir(self.state)), ["CM_52_wk_High_low_23112026.csv",
                                                          "sec_bhavdata_full_23112026.csv"])   # the ban list is not kept
        del self.calls[:]
        code, text, _ = self.daily_live(["--now", "2026-11-23 18:00"])
        self.assertEqual(self.calls, [base + "content/fo/fo_secban.csv"])    # the same evening: only the ban list
        self.assertIn("the copy saved in ", text)
        self.assertIn("for 36 charts that Yahoo had left empty", text)

    def test_daily_falls_back_when_the_bhavcopy_is_not_out(self):
        self.answers["fo_secban.csv"] = nse_text("fo_secban.csv")
        code, text, _ = self.daily_live(["--now", "2026-11-23 15:45"])
        self.assertEqual(code, 0)
        self.assertIn("NSE's bhavcopy of 2026-11-23 was not used (HTTP 404: NSE publishes it in the evening after "
                      "the session, so it may not be out yet). 36 charts take the session's last traded price from "
                      "Yahoo as that day's close, not the official close.", text)
        self.assertEqual([c.rsplit("/", 1)[1] for c in self.calls], ["sec_bhavdata_full_23112026.csv",
                                                                     "fo_secban.csv"])   # no 52-week request either
        self.assertNotIn("delivery", text)
        with open(os.path.join(self.d, "trader", "wl.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["nse"], {"session": "2026-11-23", "bhavcopy": None, "bhavcopy_from": "HTTP 404",
                                      "latest_close_from": {"last_price": 36}, "fno_ban_trade_date": "25-NOV-2026",
                                      "fno_ban": ["MANAPPURAM"], "high_52w_file": None})
        self.assertEqual(sorted(doc["hits"][0]), ["close_date", "price", "rsi", "setups", "symbol", "volume"])
        # --no-nse asks NSE for nothing
        del self.calls[:]
        code, text, _ = self.daily_live(["--now", "2026-11-23 15:45", "--no-nse", "--no-write"])
        self.assertEqual(self.calls, [])
        self.assertIn("36 charts take the session's last traded price from Yahoo as that day's close, not the "
                      "official close (--no-nse).", text)


class NseCli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.t = os.path.join(self.d, "trader")
        self.wl = os.path.join(self.t, "watchlist.json")
        self.uni = os.path.join(self.t, "universe.txt")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_nifty_500_universe_then_daily_with_the_nse_files(self):
        code, text, _ = run(["universe", "--index", "nifty500", "--offline", NSE, "--out", self.uni])
        self.assertEqual(code, 0)
        self.assertIn("NSE's Nifty 500 list (ind_nifty500list.csv): 46 rows.", text)
        self.assertIn("Kept 40 stocks in series EQ (the series that can be traded intraday), in 15 industries.", text)
        self.assertIn("Dropped: 3 series RR, 2 series BE, 1 placeholder without a real ISIN.", text)
        with open(self.uni, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 40)
        self.assertIn("M&M|Mahindra & Mahindra Ltd.", lines)
        self.assertTrue(all(ln.count("|") == 1 for ln in lines))            # still SYMBOL|Name, two columns
        with open(os.path.join(self.t, "state", "industry.json"), encoding="utf-8") as fh:
            ind = json.load(fh)
        self.assertEqual((ind["source"], ind["index"], len(ind["industry"])), ("ind_nifty500list.csv", "Nifty 500", 40))
        self.assertEqual(ind["industry"]["TCS"], "Information Technology")
        self.assertEqual(scan.symbols_from_file(self.uni)[:2], ["ADANIENT", "ADANIPORTS"])

        code, text, _ = run(["daily", "--universe", self.uni, "--offline", DAILY, "--nse", NSE, "--now",
                             "2026-11-24 09:00", "--out", self.wl])
        self.assertEqual(code, 0)
        self.assertIn("Fetched 39 of 40 charts (1 failed or had no data). 36 passed the liquidity filter", text)
        self.assertIn("Official closes: NSE's bhavcopy of 2026-11-23 (sec_bhavdata_full_23112026.csv, the --nse "
                      "folder) gave the close and the volume of that day for 36 charts that Yahoo had left empty.",
                      text)
        self.assertIn("F&O ban list for trade date 25-NOV-2026 (fo_secban.csv): 1 stock; none of the hits is on it. "
                      "Information only for a cash-segment trade.", text)
        self.assertIn("1 hit closed within 2 % of the 52-week high, or above it", text)
        self.assertIn("  RELIANCE       ₹1,351.00  RSI  64.8  bull flag  [delivery 53.9 %; ₹2,094.38 cr traded; "
                      "Oil Gas & Consumable Fuels]", text)
        self.assertIn("  EICHERMOT      ₹8,172.50  RSI  71.0  20-day closing-high breakout  [delivery 57.3 %; "
                      "₹622.53 cr traded; Automobile and Auto Components; near its 52-week high (8,230.00)]", text)
        with open(self.wl, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["nse"], {"session": "2026-11-23", "bhavcopy": "sec_bhavdata_full_23112026.csv",
                                      "bhavcopy_from": "the --nse folder", "latest_close_from": {"bhavcopy": 36},
                                      "fno_ban_trade_date": "25-NOV-2026", "fno_ban": ["MANAPPURAM"],
                                      "high_52w_file": "CM_52_wk_High_low_23112026.csv"})
        self.assertEqual(doc["hits"][7], {"symbol": "EICHERMOT", "setups": ["breakout_20d"], "price": 8172.5,
                                          "close_date": "2026-11-23", "rsi": 71.0, "volume": 760976,
                                          "delivery_pct": 57.26, "turnover_cr": 622.53, "near_52w_high": True,
                                          "high_52w": 8230.0, "industry": "Automobile and Auto Components"})
        for h in doc["hits"]:                                               # every old field is still there
            self.assertLessEqual({"symbol", "setups", "price", "close_date", "rsi", "volume"}, set(h))
        self.assertEqual(scan.symbols_from_file(self.wl)[:3], ["HDFCBANK", "ICICIBANK", "RELIANCE"])
        self.assertEqual(sorted(os.listdir(os.path.join(self.t, "state"))), ["industry.json"])   # nothing cached

    def test_a_hit_on_the_ban_list_is_marked(self):
        nse = os.path.join(self.d, "nse")
        shutil.copytree(NSE, nse)
        with open(os.path.join(nse, "fo_secban.csv"), "w", encoding="utf-8") as fh:
            fh.write("Securities in Ban For Trade Date 25-NOV-2026:\n1,MANAPPURAM\n2,SBIN\n")   # SBIN is made up
        code, text, _ = run(["daily", "--offline", DAILY, "--nse", nse, "--now", "2026-11-24 09:00", "--out",
                             self.wl])
        self.assertIn("F&O ban list for trade date 25-NOV-2026 (fo_secban.csv): 2 stocks; among the hits: SBIN.",
                      text)
        self.assertIn("  SBIN           ₹1,132.65  RSI  70.2  bull flag  [delivery 45.7 %; ₹1,712.18 cr traded; "
                      "in the F&O ban period]", text)                        # no industry.json here: no industry
        with open(self.wl, encoding="utf-8") as fh:
            hits = {h["symbol"]: h for h in json.load(fh)["hits"]}
        self.assertIs(hits["SBIN"]["fno_ban"], True)
        self.assertNotIn("fno_ban", hits["RELIANCE"])
        self.assertNotIn("industry", hits["RELIANCE"])

    def test_offline_without_the_nse_files(self):
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-11-24 09:00", "--out", self.wl])
        self.assertEqual(code, 0)
        self.assertIn("NSE's bhavcopy of 2026-11-23 was not used (an offline run without --nse). 36 charts take the "
                      "session's last traded price from Yahoo as that day's close, not the official close.", text)
        self.assertNotIn("ban", text)
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-11-23 09:00", "--no-write"])
        self.assertIn("NSE's bhavcopy of 2026-11-20 was not used (an offline run without --nse). Closes are Yahoo's "
                      "own daily bars, and there are no delivery or turnover figures.", text)
        # a folder without that day's bhavcopy: said plainly, the scan still runs
        code, text, _ = run(["daily", "--offline", DAILY, "--nse", NSE, "--now", "2026-11-23 09:00", "--no-write"])
        self.assertEqual(code, 0)
        self.assertIn("was not used (no sec_bhavdata_full_20112026.csv in the --nse folder)", text)
        self.assertIn("F&O ban list for trade date 25-NOV-2026", text)
        self.assertNotIn("52-week", text)

    def test_index_universe_messages(self):
        code, _, err = run(["universe", "--index", "nifty500", "--offline", os.path.join(self.d, "nope")])
        self.assertEqual(code, 1)
        self.assertIn("no ind_nifty500list.csv", err)
        code, _, err = run(["universe", "--index", "nifty500", "--offline", os.path.join(EQL, "EQUITY_L.csv")])
        self.assertEqual(code, 1)
        self.assertIn("not an NSE index constituents list", err)
        code, text, _ = run(["universe", "--index", "nifty500", "--offline", NSE, "--out", self.uni, "--no-write",
                             "--json"])
        out = json.loads(text)
        self.assertEqual((out["index"], out["count"], out["industries"], out["out"]), ("nifty500", 40, 15, None))
        self.assertFalse(os.path.exists(self.t))


if __name__ == "__main__":
    unittest.main()
