import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

market = load("trader-market", "market")

# Real files, all fetched 2026-11-24 21:10 IST (an NSE holiday; the last session was Mon 2026-11-23):
#   sec_bhavdata_full_DDMMYYYY.csv   NSE's full bhavcopy of each of the 20 sessions from 2026-10-26 to 2026-11-23,
#                                    cut to 50 rows: 45 Nifty 500 names, 4 other EQ names, one series BE row
#   CM_52_wk_High_low_23112026.csv   the same symbols, with the file's two heading lines and its '-' row
#   ind_nifty500list.csv             the same 45 names + the 5 rows of the real list that are not series EQ
#   yahoo-NSEI.json, yahoo-INDIAVIX.json   Yahoo's v8 chart answers (range 1y, interval 1d), whole
MDIR = os.path.join(FIX, "trader-market")
DT = datetime.datetime
D = datetime.date
MON = D(2026, 11, 23)
NOW = "2026-11-24 10:00"          # Tuesday, Guru Nanak Jayanti: NSE is closed
BASE = "https://nsearchives.nseindia.com/"


def text_of(name):
    with open(os.path.join(MDIR, name), encoding="utf-8") as fh:
        return fh.read()


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = market.main(argv)
    return code, out.getvalue(), err.getvalue()


def row(close, prev, high=None, low=None):
    return {"close": close, "prev_close": prev, "high": high if high is not None else close,
            "low": low if low is not None else close}


class Clock(unittest.TestCase):
    def test_ist_is_a_fixed_five_and_a_half_hours(self):
        self.assertEqual(market.to_ist(DT(2026, 11, 23, 4, 30)), DT(2026, 11, 23, 10, 0))
        self.assertEqual(market.ist_to_utc(DT(2026, 6, 1, 9, 15)), DT(2026, 6, 1, 3, 45))   # no daylight saving
        self.assertEqual(market.ts_to_ist(1795405500), DT(2026, 11, 23, 9, 15))             # Yahoo's daily bar stamp

    def test_now_is_india_time_unless_it_says_otherwise(self):
        want = DT(2026, 11, 24, 4, 30)
        self.assertEqual(market.parse_now("2026-11-24 10:00"), want)
        self.assertEqual(market.parse_now("2026-11-24 10:00 IST"), want)
        self.assertEqual(market.parse_now("2026-11-24T04:30:00Z"), want)
        with self.assertRaises(market.MarketError):
            market.parse_now("tomorrow")

    def test_the_holiday_list(self):
        self.assertEqual(market.trading_day(D(2026, 11, 24)), (False, "NSE holiday: Guru Nanak Jayanti"))
        self.assertEqual(market.trading_day(MON), (True, ""))
        self.assertEqual(market.trading_day(D(2026, 11, 22)), (False, "weekend"))
        self.assertEqual(market.calendar_note(MON), "")
        self.assertIn("for 2027 it knows weekends only", market.calendar_note(D(2027, 1, 4)))

    def test_the_latest_completed_session(self):
        ls = lambda t: market.latest_session(market.parse_now(t))
        self.assertEqual(ls("2026-11-23 15:29"), D(2026, 11, 20))   # Monday's session is not over
        self.assertEqual(ls("2026-11-23 15:30"), MON)
        self.assertEqual(ls("2026-11-24 10:00"), MON)               # the holiday
        self.assertEqual(ls("2026-11-22 12:00"), D(2026, 11, 20))   # Sunday
        self.assertEqual(ls("2026-11-25 09:00"), MON)               # Wednesday before the close

    def test_the_twenty_sessions_skip_weekends_and_holidays(self):
        days = market.sessions_back(MON, 20)
        self.assertEqual((len(days), days[0], days[-1]), (20, MON, D(2026, 10, 26)))
        self.assertNotIn(D(2026, 11, 10), days)                     # Diwali Balipratipada
        self.assertNotIn(D(2026, 11, 8), days)                      # a Sunday (the Muhurat session is ignored)
        self.assertEqual(market.sessions_back(D(2026, 11, 11), 2), [D(2026, 11, 11), D(2026, 11, 9)])
        self.assertEqual(market.stamp(MON), "23112026")


class Parsing(unittest.TestCase):
    def test_the_bhavcopy_keeps_series_eq(self):
        day, rows = market.parse_bhavcopy(text_of("sec_bhavdata_full_23112026.csv"))
        self.assertEqual((day, len(rows)), (MON, 49))               # 50 rows, the BE one is out
        self.assertNotIn("21STCENMGM", rows)
        # RELIANCE, EQ, 23-Nov-2026, 1377.50, ..., 1390.00, 1346.90, ..., 1351.00
        self.assertEqual(rows["RELIANCE"], {"close": 1351.0, "prev_close": 1377.5, "high": 1390.0, "low": 1346.9})
        self.assertIn("M&M", rows)
        self.assertIn("BAJAJ-AUTO", rows)
        self.assertAlmostEqual(market.change_pct(rows["RELIANCE"]), (1351.0 / 1377.5 - 1) * 100)

    def test_a_file_that_is_not_the_bhavcopy_is_refused(self):
        for bad in ("<html>Not Found</html>", "", "SYMBOL, SERIES\nX, EQ\n"):
            with self.assertRaises(market.MarketError):
                market.parse_bhavcopy(bad)
        with self.assertRaises(market.MarketError) as cm:
            market.bhavcopy_for(D(2026, 11, 20))(text_of("sec_bhavdata_full_23112026.csv"))
        self.assertIn("is for 2026-11-23, not 2026-11-20", str(cm.exception))
        self.assertEqual(market.bhavcopy_for(MON)(text_of("sec_bhavdata_full_23112026.csv"))[0], MON)

    def test_the_52_week_file(self):
        text = text_of("CM_52_wk_High_low_23112026.csv")
        self.assertTrue(text.startswith('"Disclaimer - '))
        eff, hilo = market.parse_hilo(text)
        self.assertEqual(eff, MON)
        self.assertEqual(len(hilo), 49)                             # the '-' row (1STCUS$) and the BE row are out
        self.assertEqual(hilo["EICHERMOT"], {"high": 8230.0, "low": 6442.0})
        self.assertEqual(hilo["TATAPOWER"]["low"], 333.2)
        self.assertNotIn("23-NOV-2026", text)                       # the file stops before its own session
        with self.assertRaises(market.MarketError):
            market.parse_hilo("<html>Not Found</html>")
        with self.assertRaises(market.MarketError):
            market.hilo_for(D(2026, 11, 20))(text)

    def test_the_nifty_500_list(self):
        industry, n = market.parse_index_list(text_of("ind_nifty500list.csv"))
        self.assertEqual((len(industry), n), (45, 50))
        self.assertEqual(industry["TCS"], "Information Technology")
        self.assertEqual(industry["M&M"], "Automobile and Auto Components")
        for sym in ("EMBASSY", "HFCL"):                             # a REIT (series RR) and a BE row
            self.assertNotIn(sym, industry)
        placeholder = "Company Name,Industry,Symbol,Series,ISIN Code\nDummy Ltd.,Services,DUMMYXX,EQ,DUM000000001\n"
        with self.assertRaises(market.MarketError):
            market.parse_index_list(placeholder)
        with self.assertRaises(market.MarketError):
            market.parse_index_list("SYMBOL,NAME\n")

    def test_yahoo_daily_bars(self):
        bars, src = market.parse_daily(text_of("yahoo-NSEI.json"), MON)
        self.assertEqual((len(bars), src), (246, "yahoo"))
        self.assertEqual(bars[0][0], D(2025, 11, 24))
        self.assertEqual((bars[-1][0], round(bars[-1][1], 2)), (MON, 25349.65))
        self.assertEqual((bars[-2][0], round(bars[-2][1], 2)), (D(2026, 11, 20), 25800.6))
        self.assertNotIn(D(2026, 11, 10), [d for d, _ in bars])     # the holiday has no bar
        # an earlier session: the later bars are cut off
        bars, src = market.parse_daily(text_of("yahoo-NSEI.json"), D(2026, 11, 20))
        self.assertEqual((bars[-1][0], src), (D(2026, 11, 20), "yahoo"))
        # a day Yahoo has no bar for
        self.assertIsNone(market.parse_daily(text_of("yahoo-NSEI.json"), D(2026, 11, 24))[1])

    def test_an_empty_yahoo_close_is_never_invented(self):
        doc = json.loads(text_of("yahoo-NSEI.json"))
        r = doc["chart"]["result"][0]
        r["indicators"]["quote"][0]["close"][-1] = None
        # the meta's last price is stamped 15:31 IST on that day: it stands in, and is labelled
        bars, src = market.parse_daily(doc, MON)
        self.assertEqual((bars[-1], src), ((MON, 25349.65), "last_price"))
        # stamped mid-session (13:00 IST): not a close, so the day has no bar
        r["meta"]["regularMarketTime"] = 1795405500 + 225 * 60
        bars, src = market.parse_daily(doc, MON)
        self.assertEqual((bars[-1][0], src), (D(2026, 11, 20), None))
        del r["meta"]["regularMarketTime"]
        self.assertIsNone(market.parse_daily(doc, MON)[1])

    def test_yahoo_answers_that_are_not_charts(self):
        for bad in ("<html>", "{}", '{"chart": {"result": null, "error": {"description": "No data found"}}}'):
            with self.assertRaises(market.MarketError):
                market.parse_daily(bad, MON)

    def test_symbols_and_urls(self):
        self.assertEqual(market.yahoo_symbol("^NSEI"), "^NSEI")
        self.assertEqual(market.yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(market.chart_path("^NSEI"), "/v8/finance/chart/%5ENSEI?range=1y&interval=1d")
        self.assertEqual(market.chart_path("^INDIAVIX"), "/v8/finance/chart/%5EINDIAVIX?range=1y&interval=1d")
        self.assertEqual(market.UA, "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)")
        with open(market.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("www.nseindia.com", src)
        self.assertNotIn("Cookie", src)
        self.assertNotIn("Referer", src)


class Metrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.day, cls.rows = market.parse_bhavcopy(text_of("sec_bhavdata_full_23112026.csv"))
        cls.industry = market.parse_index_list(text_of("ind_nifty500list.csv"))[0]
        cls.hilo = market.parse_hilo(text_of("CM_52_wk_High_low_23112026.csv"))[1]

    def test_breadth_counts(self):
        rows = {"A": row(110, 100), "B": row(102.98, 100), "C": row(100, 100), "D": row(97, 100),
                "E": row(99, 100), "F": row(50, None), "G": row(103, 100)}
        b = market.breadth(rows, None, 3.0)
        self.assertEqual((b["stocks"], b["advances"], b["declines"], b["unchanged"]), (6, 3, 2, 1))
        self.assertEqual((b["ad_ratio"], b["adv_pct"], b["dec_pct"]), (1.5, 50.0, 33.3))
        self.assertEqual((b["up_big"], b["down_big"]), (2, 1))      # A and G (exactly 3 %); D (exactly -3 %)
        self.assertEqual((b["up_big_pct"], b["down_big_pct"]), (33.3, 16.7))
        self.assertEqual(b["median_change_pct"], 1.49)              # the middle of 0 and 2.98
        self.assertEqual(b["not_counted"], 1)                       # F has no previous close
        only = market.breadth(rows, {"A", "D", "ZZZ"}, 3.0)
        self.assertEqual((only["stocks"], only["advances"], only["declines"], only["not_counted"]), (2, 1, 1, 1))
        self.assertIsNone(market.breadth({}, None))
        self.assertIsNone(market.breadth(rows, {"ZZZ"}))
        self.assertIsNone(market.breadth({"A": row(110, 100)}, None)["ad_ratio"])   # no declines: no ratio

    def test_a_likely_split_is_left_out_of_breadth(self):
        rows = {"A": row(110, 100), "B": row(50, 100), "C": row(75, 100)}
        b = market.breadth(rows, None, 3.0, 25.0)
        self.assertEqual((b["stocks"], b["declines"], b["likely_splits"]), (2, 1, ["B"]))   # exactly -25 % stays
        # real: CONCORDBIO on 2026-11-04, PREV_CLOSE 1227.90, close 655.50; the file does not adjust PREV_CLOSE
        day, rows = market.parse_bhavcopy(text_of("sec_bhavdata_full_04112026.csv"))
        self.assertEqual(rows["CONCORDBIO"]["prev_close"], 1227.9)
        self.assertEqual(market.breadth(rows, None)["likely_splits"], ["CONCORDBIO"])
        day, rows = market.parse_bhavcopy(text_of("sec_bhavdata_full_16112026.csv"))
        self.assertEqual(market.breadth(rows, None)["likely_splits"], ["BNAGROCHEM"])        # 162.66 -> 17.08

    def test_breadth_of_the_real_session(self):
        b = market.breadth(self.rows, None, 3.0)
        self.assertEqual((b["stocks"], b["advances"], b["declines"], b["unchanged"]), (49, 13, 36, 0))
        self.assertEqual((b["ad_ratio"], b["adv_pct"], b["up_big"], b["down_big"]), (0.36, 26.5, 3, 8))
        self.assertEqual(b["median_change_pct"], -1.44)
        n = market.breadth(self.rows, set(self.industry), 3.0)
        self.assertEqual((n["stocks"], n["advances"], n["declines"]), (45, 11, 34))
        self.assertEqual((n["ad_ratio"], n["adv_pct"], n["up_big"], n["down_big"], n["median_change_pct"]),
                         (0.32, 24.4, 1, 7, -1.48))
        self.assertEqual(n["likely_splits"], [])                    # APLAPOLLO fell 21.3 %: under the 25 % line
        self.assertEqual(market.breadth(self.rows, None, 1.0)["up_big"], 7)

    def test_the_industry_table(self):
        rows = {"A": row(102, 100), "B": row(104, 100), "C": row(99, 100), "D": row(98, 100), "E": row(40, 100)}
        t = market.industry_table(rows, {"A": "Tech", "B": "Tech", "C": "Banks", "D": "Banks", "E": "Banks",
                                         "Z": "Tech"})
        self.assertEqual(t, [{"industry": "Tech", "stocks": 2, "median_change_pct": 3.0, "adv_pct": 100.0},
                             {"industry": "Banks", "stocks": 2, "median_change_pct": -1.5, "adv_pct": 0.0}])
        real = market.industry_table(self.rows, self.industry)
        self.assertEqual(sum(g["stocks"] for g in real), 45)
        self.assertEqual(real[0], {"industry": "Information Technology", "stocks": 5, "median_change_pct": 1.48,
                                   "adv_pct": 100.0})
        by = {g["industry"]: g for g in real}
        self.assertEqual(by["Financial Services"], {"industry": "Financial Services", "stocks": 6,
                                                    "median_change_pct": -3.05, "adv_pct": 0.0})
        self.assertEqual(by["Healthcare"]["adv_pct"], 20.0)         # 1 of 5
        self.assertEqual([g["median_change_pct"] for g in real], sorted((g["median_change_pct"] for g in real),
                                                                        reverse=True))

    def test_new_highs_and_lows(self):
        rows = {"A": row(100, 99, high=105.0, low=98), "B": row(100, 99, high=105.01, low=98),
                "C": row(100, 99, high=101, low=49.99), "D": row(100, 99, high=200, low=1), "E": row(5, 5, 9, 1)}
        hilo = {"A": {"high": 105.0, "low": 50.0}, "B": {"high": 105.0, "low": 50.0},
                "C": {"high": 105.0, "low": 50.0}, "E": {"high": None, "low": None}}
        h = market.highs_lows(rows, hilo)
        self.assertEqual((h["compared"], h["highs"], h["lows"]), (4, ["B"], ["C"]))   # equal to the old high: not new
        self.assertIsNone(market.highs_lows(rows, {}))
        real = market.highs_lows(self.rows, self.hilo)
        # EICHERMOT's high was 8275.00 against 8230.00; TATAPOWER's low 327.50 against 333.20
        self.assertEqual(real["highs"], ["APOLLOHOSP", "AXISBANK", "DLF", "EICHERMOT", "HMAAGRO", "KOTAKBANK"])
        self.assertEqual(real["lows"], ["APLAPOLLO", "PASHUPATI", "PIIND", "TATAPOWER"])
        self.assertEqual((real["compared"], real["new_highs"], real["new_lows"]), (49, 6, 4))
        n = market.highs_lows(self.rows, self.hilo, set(self.industry))
        self.assertEqual((n["compared"], n["new_highs"], n["new_lows"]), (45, 5, 3))

    def test_trend_on_made_up_closes(self):
        d = [D(2026, 11, 23), D(2026, 11, 20), D(2026, 11, 19)]
        days = [(d[0], {"UP": row(12, 11), "DOWN": row(8, 9), "FLAT": row(10, 10), "NEW": row(5, 5),
                        "SPLIT": row(50, 100)}),
                (d[1], {"UP": row(11, 10), "DOWN": row(9, 10), "FLAT": row(10, 10), "SPLIT": row(100, 100)}),
                (d[2], {"UP": row(10, 10), "DOWN": row(10, 10), "FLAT": row(10, 10), "SPLIT": row(100, 100)})]
        t = market.trend(days, {"UP", "DOWN", "FLAT", "NEW", "SPLIT", "GONE"}, 20)
        self.assertEqual((t["counted"], t["above"], t["above_pct"]), (3, 1, 33.3))    # FLAT equals its average
        self.assertEqual((t["left_out_no_history"], t["likely_splits"]), (2, ["SPLIT"]))
        self.assertEqual((t["sessions_read"], t["sessions_wanted"], t["from"], t["to"]),
                         (3, 20, "2026-11-19", "2026-11-23"))
        self.assertIsNone(market.trend([], {"UP"}))
        self.assertIsNone(market.trend(days[:1], {"GONE"})["above_pct"])

    def test_trend_of_the_real_twenty_sessions(self):
        days = []
        for d in market.sessions_back(MON, 20):
            days.append((d, market.parse_bhavcopy(text_of("sec_bhavdata_full_%s.csv" % market.stamp(d)))[1]))
        t = market.trend(days, set(self.industry), 20)
        self.assertEqual((t["counted"], t["above"], t["above_pct"]), (44, 29, 65.9))
        self.assertEqual((t["likely_splits"], t["left_out_no_history"]), (["CONCORDBIO"], 0))
        self.assertEqual((t["from"], t["to"], t["sessions_read"]), ("2026-10-26", "2026-11-23", 20))
        # RELIANCE by hand: its close against the plain average of its 20 closes
        closes = [rows["RELIANCE"]["close"] for _, rows in days]
        self.assertEqual(closes[0], 1351.0)
        above = sum(1 for s in self.industry if s != "CONCORDBIO"
                    and days[0][1][s]["close"] > sum(r[s]["close"] for _, r in days) / 20.0)
        self.assertEqual(above, 29)

    def test_the_nifty_and_its_averages(self):
        bars, _ = market.parse_daily(text_of("yahoo-NSEI.json"), MON)
        x = market.index_stats(bars)
        closes = [c for _, c in bars]
        self.assertEqual((x["date"], x["close"], x["prev_close"], x["change_pct"]),
                         ("2026-11-23", 25349.65, 25800.6, -1.75))
        self.assertEqual(x["ma20"], round(sum(closes[-20:]) / 20, 2))
        self.assertEqual(x["ma50"], round(sum(closes[-50:]) / 50, 2))
        self.assertEqual((x["ma20"], x["ma50"], x["vs_ma20_pct"], x["vs_ma50_pct"]), (24414.26, 23520.1, 3.83, 7.78))
        short = market.index_stats(bars[-30:])
        self.assertEqual((short["ma20"], short["ma50"], short["vs_ma50_pct"]), (24414.26, None, None))
        self.assertIsNone(market.index_stats(bars[-1:]))

    def test_the_vix_and_its_range(self):
        bars, _ = market.parse_daily(text_of("yahoo-INDIAVIX.json"), MON)
        x = market.vix_stats(bars)
        closes = [c for _, c in bars]
        self.assertEqual((x["close"], x["prev_close"], x["change_pct"]), (17.7, 17.32, 2.19))
        self.assertEqual((x["low"], x["high"], x["range_from"], x["bars"]), (9.15, 27.89, "2025-11-24", 245))
        self.assertEqual(x["range_pos_pct"], 45.6)                  # (17.70 - 9.15) / (27.89 - 9.15)
        under = sum(1 for c in closes[:-1] if c < closes[-1])
        self.assertEqual(x["pctile"], round(100.0 * under / 244, 1))
        self.assertEqual(x["pctile"], 80.7)
        made = [(D(2026, 1, k + 1), c) for k, c in enumerate([10, 20, 30, 40, 25])]
        v = market.vix_stats(made)
        self.assertEqual((v["low"], v["high"], v["range_pos_pct"], v["pctile"], v["change_pct"]),
                         (10, 40, 50.0, 50.0, -37.5))
        self.assertIsNone(market.vix_stats([(MON, 5.0), (MON, 5.0)])["range_pos_pct"])   # no range: no position


def metrics(adv=50.0, above=50.0, highs=10, lows=10, nifty=(100, 100, 100), vix=(60.0, 0.0)):
    m = {}
    if adv is not None:
        m["breadth"] = {"nifty500": {"adv_pct": adv}, "all_eq": {"adv_pct": adv}}
    if above is not None:
        m["trend"] = {"above_pct": above, "sessions_read": 20}
    if highs is not None:
        m["highs_lows"] = {"all_eq": {"new_highs": highs, "new_lows": lows}}
    if nifty is not None:
        m["nifty50"] = {"close": nifty[0], "ma20": nifty[1], "ma50": nifty[2]}
    if vix is not None:
        m["india_vix"] = {"close": 15.0, "pctile": vix[0], "change_pct": vix[1]}
    return m


class Reading(unittest.TestCase):
    THR = dict(market.THRESHOLDS)

    def votes(self, thr=None, **kw):
        r = market.reading(metrics(**kw), thr or self.THR)
        return {s["signal"]: s["vote"] for s in r["signals"]}, r

    def test_each_signal_votes_by_its_threshold(self):
        v = lambda **kw: self.votes(**kw)[0]
        self.assertEqual([v(adv=x)["breadth"] for x in (55.0, 54.9, 45.1, 45.0)], [1, 0, 0, -1])
        self.assertEqual([v(above=x)["trend"] for x in (60.0, 59.9, 40.1, 40.0)], [1, 0, 0, -1])
        self.assertEqual([v(nifty=x)["nifty50"] for x in ((101, 100, 100), (99, 100, 100), (100, 99, 101),
                                                           (100, 100, 100))], [1, -1, 0, 0])
        self.assertEqual([v(vix=x)["india_vix"] for x in ((50.0, 0.0), (50.1, 0.0), (79.9, 9.9), (80.0, 0.0),
                                                           (10.0, 10.0), (10.0, 9.9))], [1, 0, 0, -1, -1, 1])
        self.assertEqual([v(highs=h, lows=lo)["highs_lows"] for h, lo in ((40, 20), (39, 20), (20, 40), (14, 5),
                                                                           (20, 0), (0, 20), (0, 0))],
                         [1, 0, -1, 0, 1, -1, 0])                   # 14 + 5 is under the 20 it takes to vote

    def test_the_label_is_the_sum_of_the_votes(self):
        _, r = self.votes(adv=60.0, above=70.0)
        self.assertEqual((r["label"], r["score"], len(r["signals"]), r["left_out"]), ("supportive", 2, 5, []))
        _, r = self.votes(adv=60.0)
        self.assertEqual((r["label"], r["score"]), ("mixed", 1))
        _, r = self.votes(adv=40.0, above=30.0, vix=(90.0, 0.0))
        self.assertEqual((r["label"], r["score"]), ("weak", -3))
        _, r = self.votes(adv=60.0, above=70.0, nifty=(99, 100, 100), vix=(90.0, 0.0))
        self.assertEqual((r["label"], r["score"]), ("mixed", 0))
        self.assertIn("supportive at +2 or more, weak at -2 or less", r["rule"])
        for s in r["signals"]:                                      # every vote carries its value and its rule
            self.assertTrue(s["value"] and s["rule"], s)

    def test_a_missing_source_is_left_out_not_guessed(self):
        v, r = self.votes(adv=60.0, above=None, highs=None, nifty=(101, 100, 100), vix=(10.0, 0.0))
        self.assertEqual(sorted(v), ["breadth", "india_vix", "nifty50"])
        self.assertEqual((r["label"], r["score"]), ("supportive", 3))
        self.assertEqual(len(r["left_out"]), 2)
        self.assertTrue(r["left_out"][0].startswith("trend ("))
        v, r = self.votes(adv=60.0, above=None, highs=None, nifty=None, vix=(10.0, 0.0))
        self.assertEqual((r["label"], r["score"], len(r["signals"])), ("none", 2, 2))   # under min_signals
        v, r = self.votes(adv=None, above=None, highs=None, nifty=None, vix=None)
        self.assertEqual((r["label"], r["signals"], len(r["left_out"])), ("none", [], 5))
        m = metrics()
        m["nifty50"]["ma50"] = None                                 # under 50 bars: no 50-day average, no vote
        self.assertNotIn("nifty50", [s["signal"] for s in market.reading(m, self.THR)["signals"]])
        m = metrics()
        m["breadth"]["nifty500"] = None                             # no Nifty 500 list: all EQ stocks stand in
        self.assertIn("NSE EQ stocks closed up", market.reading(m, self.THR)["signals"][0]["value"])

    def test_thresholds_can_be_changed(self):
        thr = dict(self.THR, breadth_support_pct=70.0, supportive_score=1, min_signals=1)
        v, r = self.votes(thr, adv=60.0)
        self.assertEqual((v["breadth"], r["label"]), (0, "mixed"))
        self.assertIn("+1 at 70 % or more", r["signals"][0]["rule"])
        v, r = self.votes(thr, adv=70.0)
        self.assertEqual((v["breadth"], r["label"]), (1, "supportive"))

    def test_thresholds_from_the_config_and_from_set(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        cfg = os.path.join(tmp, "config.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"budget": 50000, "market": {"breadth_support_pct": 60, "min_signals": 4}}, fh)
        thr = market.thresholds(cfg, ["move_pct=4", "min_signals = 2"])
        self.assertEqual((thr["breadth_support_pct"], thr["move_pct"], thr["min_signals"]), (60.0, 4.0, 2))
        self.assertEqual(market.thresholds(os.path.join(tmp, "nope.json")), market.THRESHOLDS)
        for bad in (["nope=1"], ["move_pct"], ["move_pct=a lot"], ["weak_score=5"], ["trend_sessions=1"]):
            with self.assertRaises(market.MarketError):
                market.thresholds(None, bad)
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        with self.assertRaises(market.MarketError):
            market.thresholds(cfg)


class Net(unittest.TestCase):
    """Everything that would touch the network, with the network swapped out."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cache = os.path.join(self.tmp, "trader", "cache")
        self.out = os.path.join(self.tmp, "trader", "market.json")
        self.urls, self.sleeps, self.gone, self.yahoo_edit = [], [], {}, None
        self._get, self._sleep = market.http_get, market.SLEEP
        market.http_get, market.SLEEP = self.fake_get, self.sleeps.append
        self.addCleanup(self.restore)

    def restore(self):
        market.http_get, market.SLEEP = self._get, self._sleep

    def fake_get(self, url, timeout=60):
        self.urls.append(url)
        for part, code in self.gone.items():
            if part in url:
                raise urllib.error.HTTPError(url, code, "gone", {}, None)
        if "finance.yahoo.com" in url:
            name = "yahoo-NSEI.json" if "%5ENSEI" in url else "yahoo-INDIAVIX.json"
        else:
            name = url.rsplit("/", 1)[1]
        path = os.path.join(MDIR, name)
        if not os.path.exists(path):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        if self.yahoo_edit and name.startswith("yahoo-"):
            doc = json.loads(text)
            self.yahoo_edit(doc["chart"]["result"][0])
            text = json.dumps(doc)
        return text

    def check(self, *more, **kw):
        now = kw.get("now", NOW)
        return run(["check", "--now", now, "--cache", self.cache, "--out", self.out, "--quiet",
                    "--config", os.path.join(self.tmp, "none.json")] + list(more))

    def doc(self):
        with open(self.out, encoding="utf-8") as fh:
            return json.load(fh)

    def test_the_request_is_a_plain_get_with_the_honest_user_agent(self):
        seen = []

        class Answer(object):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"ok"

        def fake_open(req, timeout=None):
            seen.append(req)
            return Answer()
        real = market.urllib.request.urlopen
        market.urllib.request.urlopen = fake_open
        try:
            self.assertEqual(self._get(BASE + "content/indices/ind_nifty500list.csv"), "ok")
        finally:
            market.urllib.request.urlopen = real
        req = seen[0]
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(dict(req.header_items()), {"User-agent": market.UA, "Accept": "*/*"})

    def test_the_first_run_fetches_each_file_once_and_politely(self):
        code, text, err = self.check()
        self.assertEqual((code, err), (0, ""))
        nse = [u for u in self.urls if u.startswith(BASE)]
        self.assertEqual(len(self.urls), 24)
        self.assertEqual(len(set(self.urls)), 24)                   # nothing asked twice
        self.assertEqual(len(nse), 22)                              # 20 bhavcopies, the list, the 52-week file
        self.assertEqual(self.urls[0], BASE + "products/content/sec_bhavdata_full_23112026.csv")
        self.assertIn(BASE + "content/indices/ind_nifty500list.csv", nse)
        self.assertIn(BASE + "content/CM_52_wk_High_low_23112026.csv", nse)
        self.assertIn(BASE + "products/content/sec_bhavdata_full_26102026.csv", nse)
        self.assertNotIn(BASE + "products/content/sec_bhavdata_full_10112026.csv", nse)   # the holiday is not asked
        self.assertEqual(self.urls[-2:], ["https://query2.finance.yahoo.com/v8/finance/chart/%5ENSEI?range=1y&interval=1d",
                                          "https://query2.finance.yahoo.com/v8/finance/chart/%5EINDIAVIX?range=1y&interval=1d"])
        for u in self.urls:
            self.assertNotIn("nseindia.com/api", u)
        self.assertEqual(len(self.sleeps), 22)                      # 21 between NSE requests, 1 between Yahoo's
        self.assertTrue(all(s >= 0.5 for s in self.sleeps))
        self.assertIn("  24 requests made in this run.", text)
        self.assertEqual(sorted(os.listdir(self.cache)),
                         sorted(os.listdir(MDIR) + ["market_index.json"]))
        self.assertEqual(self.doc()["requests"], 24)

    def test_a_second_run_the_same_day_asks_for_nothing(self):
        self.check()
        first = self.doc()
        del self.urls[:]
        code, text, _ = self.check(now="2026-11-24 18:00")
        self.assertEqual((code, self.urls), (0, []))
        self.assertIn("  0 requests made in this run.", text)
        self.assertIn("session 2026-11-23; the copy saved in %s" % self.cache.replace("\\", "/"), text)
        second = self.doc()
        for k in ("breadth", "industries", "highs_lows", "trend", "nifty50", "india_vix", "reading"):
            self.assertEqual(first[k], second[k], k)

    def test_the_next_day_only_the_undated_answers_are_asked_again(self):
        self.check()
        del self.urls[:]
        # Wednesday morning: Monday is still the latest completed session
        code, text, _ = self.check(now="2026-11-25 09:00")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(u.rsplit("/", 1)[1].split("?")[0] for u in self.urls),
                         ["%5EINDIAVIX", "%5ENSEI", "ind_nifty500list.csv"])
        self.assertIn("the list is not dated, fetched 2026-11-25", text)

    def test_refresh_ignores_the_cache_and_no_write_leaves_no_file(self):
        self.check("--no-write")
        self.assertEqual(len(self.urls), 24)
        self.assertFalse(os.path.exists(self.cache))
        self.assertFalse(os.path.exists(self.out))
        self.check()
        del self.urls[:]
        self.check("--refresh")
        self.assertEqual(len(self.urls), 24)

    def test_a_missing_old_bhavcopy_is_tolerated_and_said(self):
        self.gone["sec_bhavdata_full_05112026.csv"] = 404
        code, text, _ = self.check()
        self.assertEqual(code, 0)
        self.assertIn("Missing: sec_bhavdata_full_05112026.csv (HTTP 404). The average is over the sessions that "
                      "could be read.", text)
        self.assertIn("closed above the average of their last 19 closes (2026-10-26 to 2026-11-23)", text)
        self.assertIn("19 of 20 sessions read", text)
        d = self.doc()
        self.assertEqual(d["trend"]["sessions_read"], 19)
        self.assertEqual(d["trend_missing"], [{"session": "2026-11-05", "file": "sec_bhavdata_full_05112026.csv",
                                               "why": "HTTP 404"}])
        self.assertIn("above their 19-day average", [s for s in d["reading"]["signals"] if s["signal"] == "trend"][0]["value"])
        # the same day again: the file that was not there is not asked for again
        del self.urls[:]
        code, text, _ = self.check()
        self.assertEqual(self.urls, [])
        self.assertIn("sec_bhavdata_full_05112026.csv (HTTP 404 (asked earlier today, not asked again))", text)
        # the next day it is asked for once more
        del self.gone["sec_bhavdata_full_05112026.csv"]
        del self.urls[:]
        self.check(now="2026-11-25 09:00")
        self.assertIn(BASE + "products/content/sec_bhavdata_full_05112026.csv", self.urls)
        self.assertEqual(self.doc()["trend"]["sessions_read"], 20)

    def test_too_few_bhavcopies_leave_the_trend_out(self):
        for d in market.sessions_back(MON, 20)[1:7]:
            self.gone["sec_bhavdata_full_%s.csv" % market.stamp(d)] = 404
        code, text, _ = self.check()
        self.assertEqual(code, 0)
        self.assertIn("Not available: only 14 of 20 bhavcopies could be read, under the minimum of 15.", text)
        d = self.doc()
        self.assertIsNone(d["trend"])
        self.assertEqual(len(d["trend_missing"]), 6)
        self.assertEqual([s["signal"] for s in d["reading"]["signals"]], ["breadth", "highs_lows", "nifty50", "india_vix"])
        self.assertIn("Left out of the reading: trend (only 14 of 20", text)

    def test_the_sessions_bhavcopy_is_not_out_yet(self):
        # 16:00 on Monday: the session is over, NSE has not published the file
        self.gone["sec_bhavdata_full_23112026.csv"] = 404
        code, text, _ = self.check(now="2026-11-23 16:00")
        self.assertEqual(code, 0)                                   # the index part could still be read
        self.assertIn("Not available: sec_bhavdata_full_23112026.csv (HTTP 404). NSE publishes it in the evening: "
                      "run the check again later.", text)
        self.assertIn("NOT READ; HTTP 404", text)
        d = self.doc()
        self.assertEqual((d["breadth"], d["highs_lows"], d["trend"], d["industries"]), (None, None, None, None))
        self.assertEqual(d["reading"]["label"], "none")             # two signals, the rule wants three
        self.assertIn("Reading: NONE. Only 2 signals could be worked out and the rule wants 3.", text)
        self.assertEqual(len(self.urls), 4)                         # no 52-week file, no older bhavcopies
        # later the same evening the file is there: it is asked for again
        del self.gone["sec_bhavdata_full_23112026.csv"]
        del self.urls[:]
        code, text, _ = self.check(now="2026-11-23 19:00")
        self.assertEqual(self.urls[0], BASE + "products/content/sec_bhavdata_full_23112026.csv")
        self.assertEqual(self.doc()["breadth"]["all_eq"]["stocks"], 49)

    def test_a_wrong_file_under_the_right_name_is_refused(self):
        real = self.fake_get

        def swapped(url, timeout=60):
            if url.endswith("sec_bhavdata_full_20112026.csv"):
                url = url.replace("20112026", "19112026")           # Thursday's file served as Friday's
            return real(url, timeout)
        market.http_get = swapped
        code, text, _ = self.check()
        self.assertIn("Missing: sec_bhavdata_full_20112026.csv (that bhavcopy is for 2026-11-19, not 2026-11-20)", text)
        self.assertNotIn("sec_bhavdata_full_20112026.csv", os.listdir(self.cache))

    def test_yahoo_down_leaves_the_index_out(self):
        self.gone["finance.yahoo.com"] = 503
        code, text, _ = self.check("--no-trend")
        self.assertEqual(code, 0)
        self.assertIn("Nifty 50     not available: Yahoo: HTTP 503.", text)
        self.assertIn("India VIX    not available: Yahoo: HTTP 503.", text)
        yahoo = [u for u in self.urls if "yahoo" in u]
        self.assertEqual(len(yahoo), 4)                             # query2 then query1, for each of the two
        self.assertTrue(yahoo[1].startswith("https://query1.finance.yahoo.com/"))
        d = self.doc()
        self.assertEqual((d["nifty50"], d["india_vix"]), (None, None))
        self.assertEqual([s["signal"] for s in d["reading"]["signals"]], ["breadth", "highs_lows"])
        self.assertEqual(d["reading"]["label"], "none")

    def test_yahoo_rate_limit_falls_back_to_the_other_host(self):
        self.gone["query2.finance.yahoo.com"] = 429
        code, text, _ = self.check("--no-trend")
        self.assertEqual(self.doc()["nifty50"]["close"], 25349.65)
        self.assertEqual(len([u for u in self.urls if "query1" in u]), 2)

    def test_a_yahoo_answer_without_the_sessions_close_is_asked_for_again(self):
        def empty_close(r):                                         # Yahoo an hour after the bell: no daily close yet
            r["indicators"]["quote"][0]["close"][-1] = None
        self.yahoo_edit = empty_close
        code, text, _ = self.check("--no-trend", now="2026-11-23 19:00")
        d = self.doc()
        self.assertEqual((d["nifty50"]["close"], d["nifty50"]["close_from"]), (25349.65, "last_price"))
        self.assertEqual(text.count("Yahoo's daily close was still empty: this is its last price of the session."), 2)
        self.assertIn("its last price of that session stands in", text)
        # later the same evening Yahoo has the close: the saved answers are not good enough, so it is asked again
        self.yahoo_edit = None
        del self.urls[:]
        self.check("--no-trend", now="2026-11-23 21:00")
        self.assertEqual(len(self.urls), 2)
        self.assertTrue(all("finance.yahoo.com" in u for u in self.urls))
        self.assertEqual(self.doc()["nifty50"]["close_from"], "yahoo")
        del self.urls[:]
        self.check("--no-trend", now="2026-11-23 22:00")
        self.assertEqual(self.urls, [])

    def test_yahoo_has_no_bar_for_the_session(self):
        def no_bar(r):                                              # the answer stops at Friday
            for k in ("open", "high", "low", "close", "volume"):
                r["indicators"]["quote"][0][k] = r["indicators"]["quote"][0][k][:-1]
            r["timestamp"] = r["timestamp"][:-1]
            r["meta"]["regularMarketTime"] = r["timestamp"][-1]
        self.yahoo_edit = no_bar
        code, text, _ = self.check("--no-trend")
        self.assertIn("Nifty 50     not available: Yahoo has no close for 2026-11-23 yet; its last bar is 2026-11-20.", text)
        self.assertIsNone(self.doc()["nifty50"])                    # Friday's close is not passed off as Monday's
        self.assertIn("Left out of the reading: Nifty 50 (Yahoo has no close for 2026-11-23 yet", text)

    def test_a_mid_session_check_reads_the_session_before(self):
        self.check(now="2026-11-23 12:00")                          # Monday 12:00: Friday is the latest session
        d = self.doc()
        self.assertEqual((d["session"], d["nifty50"]["date"], d["nifty50"]["close"]), ("2026-11-20", "2026-11-20", 25800.6))
        self.assertIsNone(d["highs_lows"])                          # Friday's 52-week file is not in the fixtures
        del self.urls[:]
        self.check(now="2026-11-23 12:30")
        self.assertEqual(self.urls, [])                             # not even the file that was not there

    def test_an_earlier_date_does_not_empty_the_cache(self):
        self.check()
        before = sorted(os.listdir(self.cache))
        code, text, _ = self.check("--date", "2026-11-20", "--no-trend")
        self.assertEqual(code, 0)
        self.assertIn("Market Health Check: NSE session of Fri 2026-11-20", text)
        self.assertIn("CM_52_wk_High_low_20112026.csv", text)       # not in the fixtures: said, not guessed
        self.assertEqual(sorted(os.listdir(self.cache)), before)
        # files of sessions before the 20-session window are dropped on the next check
        old = os.path.join(self.cache, "sec_bhavdata_full_23102026.csv")
        with open(old, "w", encoding="utf-8") as fh:
            fh.write("old")
        keep = os.path.join(self.cache, "notes.txt")
        with open(keep, "w", encoding="utf-8") as fh:
            fh.write("mine")
        self.check()
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(keep))                       # only the tool's own dated files are pruned
        self.assertTrue(os.path.exists(os.path.join(self.cache, "sec_bhavdata_full_26102026.csv")))


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.out = os.path.join(self.tmp, "trader", "market.json")
        self._get = market.http_get
        market.http_get = self.no_network
        self.addCleanup(self.restore)

    def restore(self):
        market.http_get = self._get

    def no_network(self, url, timeout=60):
        raise AssertionError("an offline run asked for %s" % url)

    def check(self, *more, **kw):
        return run(["check", "--offline", kw.get("offline", MDIR), "--now", kw.get("now", NOW), "--out", self.out,
                    "--config", os.path.join(self.tmp, "none.json")] + list(more))

    def test_the_full_check(self):
        code, text, err = self.check()
        self.assertEqual((code, err), (0, ""))
        for line in (
            "Market Health Check: NSE session of Mon 2026-11-23",
            "Checked Tue 2026-11-24 10:00 IST. NSE is closed today (NSE holiday: Guru Nanak Jayanti).",
            "  All EQ stocks  49 stocks: 13 up, 36 down, 0 unchanged; advance/decline 0.36; 26.5 % up, 73.5 % down",
            "                 up 3 % or more: 3 (6.1 %); down 3 % or more: 8 (16.3 %); median change -1.44 %",
            "  Nifty 500      45 stocks: 11 up, 34 down, 0 unchanged; advance/decline 0.32; 24.4 % up, 75.6 % down",
            "    Information Technology    5 stocks  median +1.48 %  100.0 % up",
            "    Financial Services        6 stocks  median -3.05 %    0.0 % up",
            "  All EQ stocks  6 new highs, 4 new lows, of 49 stocks compared",
            "                 highs: APOLLOHOSP, AXISBANK, DLF, EICHERMOT, KOTAKBANK",
            "                 lows: APLAPOLLO, PIIND, TATAPOWER",
            "  29 of 44 (65.9 %) closed above the average of their last 20 closes (2026-10-26 to 2026-11-23)",
            "(a close more than 25 % under the one before it; not verified): CONCORDBIO",
            "  Nifty 50     25,349.65, -1.75 % on the day (from 25,800.60); 3.83 % above its 20-day average "
            "(24,414.26); 7.78 % above its 50-day average (23,520.10)",
            "  India VIX    17.70, +2.19 % on the day (from 17.32); range since 2025-11-24: 9.15 to 27.89, "
            "45.6 % of the way up; above 80.7 % of the closes in it",
            "Reading: MIXED for long breakouts",
            "  Score 0 from 5 signals: supportive at +2 or more, weak at -2 or less, mixed between them; no reading "
            "with fewer than 3 signals.",
            "  -1  24.4 % of Nifty 500 stocks closed up",
            "      rule: +1 at 55 % or more, -1 at 45 % or less",
            "  +1  65.9 % of Nifty 500 stocks closed above their 20-day average",
            "   0  6 new 52-week highs, 4 new lows (NSE EQ)",
            "  +1  Nifty 50 at 25,349.65, 20-day average 24,414.26, 50-day average 23,520.10",
            "  -1  India VIX at 17.70 (+2.19 % on the day), above 80.7 % of its last year's closes",
            "It is not advice and not a prediction",
            "sec_bhavdata_full_23112026.csv  session 2026-11-23; the --offline folder",
            "the list is not dated; 45 of its 50 rows are series EQ",
            "20 of 20 sessions read; 2026-10-26 to 2026-11-23",
            "yahoo-INDIAVIX.json             session 2026-11-23; the --offline folder",
            "  0 requests made in this run.",
            "Wrote %s" % self.out.replace("\\", "/"),
        ):
            self.assertIn(line, text)
        self.assertNotIn(chr(0x2014), text)
        self.assertNotIn("$", text)

    def test_market_json_is_there_for_the_other_skills(self):
        self.check()
        with open(self.out, encoding="utf-8") as fh:
            d = json.load(fh)
        self.assertEqual((d["tool"], d["session"], d["checked_ist"]), ("trader-market", "2026-11-23", "2026-11-24 10:00"))
        self.assertEqual(d["today"], {"date": "2026-11-24", "trading_day": False,
                                      "why": "NSE holiday: Guru Nanak Jayanti"})
        self.assertEqual(d["breadth"]["nifty500"]["advances"], 11)
        self.assertEqual(len(d["industries"]), 17)
        self.assertEqual(d["highs_lows"]["nifty500"]["highs"], ["APOLLOHOSP", "AXISBANK", "DLF", "EICHERMOT", "KOTAKBANK"])
        self.assertEqual((d["trend"]["above"], d["trend"]["counted"]), (29, 44))
        self.assertEqual((d["nifty50"]["close"], d["nifty50"]["close_from"]), (25349.65, "yahoo"))
        self.assertEqual(d["india_vix"]["pctile"], 80.7)
        self.assertEqual((d["reading"]["label"], d["reading"]["score"]), ("mixed", 0))
        self.assertEqual([s["vote"] for s in d["reading"]["signals"]], [-1, 1, 0, 1, -1])
        self.assertEqual(d["thresholds"], market.THRESHOLDS)
        self.assertEqual([(s["file"], s["session"]) for s in d["sources"]],
                         [("sec_bhavdata_full_23112026.csv", "2026-11-23"), ("ind_nifty500list.csv", None),
                          ("CM_52_wk_High_low_23112026.csv", "2026-11-23"), ("sec_bhavdata_full_DDMMYYYY.csv", None),
                          ("yahoo-NSEI.json", "2026-11-23"), ("yahoo-INDIAVIX.json", "2026-11-23")])
        self.assertIn("not advice and not a prediction", d["note"])
        code, text, _ = self.check("--json", "--no-write")
        self.assertEqual(json.loads(text)["reading"], d["reading"])

    def test_no_trend_skips_the_slower_part(self):
        only = os.path.join(self.tmp, "one-day")
        os.makedirs(only)
        for f in ("sec_bhavdata_full_23112026.csv", "CM_52_wk_High_low_23112026.csv", "ind_nifty500list.csv",
                  "yahoo-NSEI.json", "yahoo-INDIAVIX.json"):
            shutil.copy(os.path.join(MDIR, f), only)
        code, text, _ = self.check("--no-trend", offline=only)
        self.assertEqual(code, 0)
        self.assertIn("  Not available: skipped with --no-trend.", text)
        self.assertIn("  Left out of the reading: trend (skipped with --no-trend)", text)
        self.assertIn("  Score -1 from 4 signals", text)
        self.assertNotIn("sec_bhavdata_full_DDMMYYYY.csv", text)
        # without the flag the 19 older files are missed one by one, and the part is left out
        code, text, _ = self.check(offline=only)
        self.assertIn("only 1 of 20 bhavcopies could be read, under the minimum of 15", text)
        self.assertIn("Missing: sec_bhavdata_full_20112026.csv (no sec_bhavdata_full_20112026.csv in the --offline "
                      "folder)", text)

    def test_every_source_missing(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        code, text, _ = self.check(offline=empty)
        self.assertEqual(code, 1)
        self.assertIn("  Not available: sec_bhavdata_full_23112026.csv (no sec_bhavdata_full_23112026.csv in the "
                      "--offline folder).", text)
        self.assertIn("  Not available: it needs the bhavcopy.", text)
        self.assertIn("Reading: NONE. Only 0 signals could be worked out and the rule wants 3.", text)
        self.assertEqual(text.count("NOT READ"), 4)
        self.assertNotRegex(text, r"\d+\.\d+ %")                    # not one number was made up

    def test_parts_go_missing_one_at_a_time(self):
        some = os.path.join(self.tmp, "some")
        shutil.copytree(MDIR, some)
        os.remove(os.path.join(some, "CM_52_wk_High_low_23112026.csv"))
        os.remove(os.path.join(some, "ind_nifty500list.csv"))
        os.remove(os.path.join(some, "yahoo-INDIAVIX.json"))
        code, text, _ = self.check(offline=some)
        self.assertEqual(code, 0)
        self.assertIn("  All EQ stocks  49 stocks: 13 up, 36 down", text)
        self.assertIn("  Nifty 500      not available: no ind_nifty500list.csv in the --offline folder.", text)
        self.assertNotIn("Industries (", text)
        self.assertIn("  Not available: CM_52_wk_High_low_23112026.csv (no CM_52_wk_High_low_23112026.csv in the "
                      "--offline folder).", text)
        self.assertIn("  Not available: it needs the Nifty 500 list.", text)
        self.assertIn("  India VIX    not available: Yahoo: no yahoo-INDIAVIX.json in the --offline folder.", text)
        self.assertIn("Reading: NONE. Only 2 signals could be worked out and the rule wants 3.", text)
        self.assertIn("  -1  26.5 % of NSE EQ stocks closed up", text)      # all EQ stands in for the Nifty 500
        code, text, _ = self.check("--set", "min_signals=2", offline=some)
        self.assertIn("Reading: MIXED for long breakouts", text)
        self.assertIn("  Score 0 from 2 signals", text)

    def test_an_earlier_session_and_a_likely_split(self):
        code, text, _ = self.check("--date", "2026-11-04")
        self.assertEqual(code, 0)
        self.assertIn("Market Health Check: NSE session of Wed 2026-11-04", text)
        self.assertIn("left out as a likely split or bonus (close more than 25 % under PREV_CLOSE, which the file "
                      "does not adjust; not verified): CONCORDBIO", text)
        self.assertIn("  Nifty 500      44 stocks:", text)
        self.assertIn("only 8 of 20 bhavcopies could be read", text)        # the fixtures start on 2026-10-26
        with open(self.out, encoding="utf-8") as fh:
            d = json.load(fh)
        self.assertEqual((d["nifty50"]["date"], d["nifty50"]["close"]), ("2026-11-04", 23960.6))
        for bad, why in (("2026-11-24", "was not a trading day (NSE holiday: Guru Nanak Jayanti)"),
                         ("2026-11-22", "was not a trading day (weekend)"),
                         ("2026-11-25", "is not complete yet"), ("yesterday", "wants a date like")):
            code, text, err = self.check("--date", bad)
            self.assertEqual(code, 1)
            self.assertIn(why, err)

    def test_thresholds_on_the_command_line(self):
        code, text, _ = self.check("--set", "breadth_weak_pct=20", "--set", "vix_stress_pctile=90", "--move-pct", "1")
        self.assertIn("Reading: SUPPORTIVE for long breakouts", text)
        self.assertIn("  Score +2 from 5 signals", text)
        self.assertIn("      rule: +1 at 55 % or more, -1 at 20 % or less", text)
        self.assertIn("up 1 % or more: 7 (14.3 %); down 1 % or more: 31 (63.3 %)", text)
        code, text, _ = self.check("--set", "trend_support_pct=70", "--set", "supportive_score=1",
                                   "--set", "weak_score=0")
        self.assertIn("Reading: WEAK for long breakouts", text)
        self.assertIn("  Score -1 from 5 signals: supportive at +1 or more, weak at +0 or less", text)
        code, text, err = self.check("--set", "nope=1")
        self.assertEqual(code, 1)
        self.assertIn("no threshold called 'nope'", err)
        code, text, err = self.check(offline=os.path.join(self.tmp, "not-there"))
        self.assertEqual(code, 1)
        self.assertIn("--offline wants a folder", err)

    def test_show_prints_the_saved_check_without_fetching(self):
        code, text, err = run(["show", "--out", self.out])
        self.assertEqual(code, 1)
        self.assertIn("run `market.py check` first", err)
        _, first, _ = self.check()
        code, text, _ = run(["show", "--out", self.out])
        self.assertEqual(code, 0)
        self.assertEqual(text.splitlines()[:-1], first.splitlines()[:-1])
        self.assertIn("(checked 2026-11-24 10:00 IST). Nothing was fetched.", text.splitlines()[-1])
        code, text, _ = run(["show", "--out", self.out, "--json"])
        self.assertEqual(json.loads(text)["session"], "2026-11-23")

    def test_rules_prints_every_threshold(self):
        code, text, _ = run(["rules", "--config", os.path.join(self.tmp, "none.json")])
        self.assertEqual(code, 0)
        for k in market.THRESHOLDS:
            self.assertIn("  " + k + " ", text)
        self.assertEqual(sorted(k for k, _ in market.RULE_HELP), sorted(market.THRESHOLDS))
        self.assertIn("not advice and not a prediction", text)
        code, text, _ = run(["rules", "--set", "hl_ratio=3", "--json", "--config", os.path.join(self.tmp, "none.json")])
        self.assertEqual(json.loads(text)["hl_ratio"], 3.0)
        code, text, _ = run([])
        self.assertEqual(code, 1)

    def test_the_holiday_list_does_not_cover_next_year(self):
        code, text, _ = self.check("--no-trend", now="2027-01-04 10:00")
        self.assertIn("for 2027 it knows weekends only", text)
        self.assertIn("Market Health Check: NSE session of Fri 2027-01-01", text)   # no file for it: said
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
