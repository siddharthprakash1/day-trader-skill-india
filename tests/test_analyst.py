import copy
import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

facts = load("trader-analyst", "facts")
# Real answers saved on 2026-11-24 (an NSE holiday), about 02:20 India time:
#   timeseries-<SYMBOL>.json  Yahoo's fundamentals-timeseries answer to the tool's own request, for TCS.NS,
#                             HDFCBANK.NS, M&M.NS, INFY.NS, SBIN.NS, ZEEL.NS and TATAMOTORS.NS (which has nothing)
#   Financial_Results.xml, Corporate_action.xml, Board_Meetings.xml   NSE's feeds (3, 7 and 0 items)
#   universe.txt              a few real SYMBOL|Name lines cut from NSE's EQUITY_L.csv
DIR = os.path.join(FIX, "trader-analyst")
NOW = "2026-11-24 02:30"            # India time
TODAY = datetime.date(2026, 11, 24)
UTC = datetime.timezone.utc
RS = chr(0x20b9)


def raw(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


def doc(sym):
    return json.loads(raw("timeseries-%s.json" % sym).decode("utf-8"))


def analysed(sym, **kw):
    kw.setdefault("today", TODAY)
    return facts.analyse(doc(sym), symbol=sym, **kw)


def without(document, yahoo_type, end):
    """A copy of a Yahoo answer with one value taken out (to make a hole where there was none)."""
    d = copy.deepcopy(document)
    for r in d["timeseries"]["result"]:
        if r["meta"]["type"][0] == yahoo_type:
            r[yahoo_type] = [v for v in r.get(yahoo_type) or [] if v and v["asOfDate"] != end]
    return d


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = facts.main(args)
    return code, out.getvalue(), err.getvalue()


def offline(*args):
    return run(list(args) + ["--offline", DIR, "--now", NOW])


def row(result, end):
    return next(r for r in result["rows"] if r["end"] == end)


class Time(unittest.TestCase):
    def test_india_time_is_a_fixed_offset(self):
        utc = datetime.datetime(2026, 11, 23, 21, 0, tzinfo=UTC)
        self.assertEqual(facts.to_ist(utc), datetime.datetime(2026, 11, 24, 2, 30))
        self.assertEqual(facts.ist_to_utc(datetime.datetime(2026, 11, 24, 2, 30)), utc)
        july = datetime.datetime(2026, 7, 1, 0, 0, tzinfo=UTC)          # no daylight saving in India
        self.assertEqual(facts.to_ist(july), datetime.datetime(2026, 7, 1, 5, 30))

    def test_now_is_india_time_unless_it_says_otherwise(self):
        want = datetime.datetime(2026, 11, 23, 21, 0, tzinfo=UTC)
        self.assertEqual(facts.parse_now("2026-11-24 02:30"), want)
        self.assertEqual(facts.parse_now("2026-11-24 02:30 IST"), want)
        self.assertEqual(facts.parse_now("2026-11-23T21:00:00Z"), want)
        with self.assertRaises(facts.FactsError):
            facts.parse_now("tomorrow")
        self.assertEqual(run(["TCS", "--offline", DIR, "--now", "soon"])[0], 2)

    def test_the_india_date_decides_what_today_is(self):
        # 20:00 UTC on the 24th is already 01:30 on the 25th in India: Zee's ex-date is today
        code, out, _ = run(["ZEEL", "--offline", DIR, "--now", "2026-11-24T20:00:00Z"])
        self.assertEqual(code, 0)
        self.assertIn("fetched 2026-11-25 01:30 IST", out)
        self.assertIn("ex-date 2026-11-25 (TODAY)", out)

    def test_quarter_ends(self):
        d = datetime.date
        self.assertEqual(facts.shift_quarters(d(2026, 9, 30), -1), d(2026, 6, 30))
        self.assertEqual(facts.shift_quarters(d(2026, 3, 31), -1), d(2025, 12, 31))
        self.assertEqual(facts.shift_quarters(d(2026, 9, 30), -4), d(2025, 9, 30))
        self.assertEqual(facts.shift_quarters(d(2026, 12, 31), 1), d(2027, 3, 31))
        self.assertEqual(facts.shift_quarters(d(2023, 11, 30), 1), d(2024, 2, 29))

    def test_fiscal_labels_follow_the_year_end(self):
        d = datetime.date
        self.assertEqual(facts.fiscal_label(d(2026, 6, 30)), "Q1 FY27")
        self.assertEqual(facts.fiscal_label(d(2026, 9, 30)), "Q2 FY27")
        self.assertEqual(facts.fiscal_label(d(2026, 12, 31)), "Q3 FY27")
        self.assertEqual(facts.fiscal_label(d(2026, 3, 31)), "Q4 FY26")
        self.assertEqual(facts.fiscal_label(d(2026, 3, 31), fy_end_month=12), "Q1 FY26")
        self.assertEqual(facts.fiscal_label(d(2026, 12, 31), fy_end_month=12), "Q4 FY26")

    def test_sebi_deadline(self):
        d = datetime.date
        self.assertEqual(facts.results_due(d(2026, 9, 30)), d(2026, 11, 14))      # 45 days after a quarter
        self.assertEqual(facts.results_due(d(2026, 12, 31)), d(2027, 2, 14))
        self.assertEqual(facts.results_due(d(2026, 3, 31)), d(2026, 5, 30))       # 60 days after the year end


class Symbols(unittest.TestCase):
    def test_ns_is_added_and_stripped(self):
        self.assertEqual(facts.yahoo_symbol("tcs"), "TCS.NS")
        self.assertEqual(facts.yahoo_symbol("M&M"), "M&M.NS")
        self.assertEqual(facts.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(facts.yahoo_symbol("TCS.NS"), "TCS.NS")
        self.assertEqual(facts.yahoo_symbol("500325.BO"), "500325.BO")
        self.assertEqual(facts.nse_symbol("m&m.ns"), "M&M")
        self.assertEqual(facts.nse_symbol(" tcs "), "TCS")
        for ok in ("RELIANCE", "M&M", "BAJAJ-AUTO", "3MINDIA", "HDFCBANK"):
            self.assertTrue(facts.SYMBOL.match(ok), ok)
        for bad in ("", "TCS NS", "A/B", "TCS.NS", "X" * 21):
            self.assertFalse(facts.SYMBOL.match(bad), bad)

    def test_the_url_quotes_the_ampersand(self):
        now = facts.parse_now(NOW)
        url = facts.timeseries_url("M&M", now)
        self.assertIn("/timeseries/M%26M.NS?type=", url)
        self.assertTrue(url.startswith("https://query2.finance.yahoo.com/ws/fundamentals-timeseries/"))
        self.assertIn("quarterlyTotalRevenue", url)
        self.assertIn("quarterlyDilutedEPS", url)
        self.assertIn("annualNetIncome", url)
        self.assertTrue(url.endswith("period2=%d" % int(now.timestamp())))
        self.assertTrue(facts.timeseries_url("TCS", now, "query1").startswith("https://query1."))

    def test_mm_runs_end_to_end(self):
        code, out, err = offline("m&m.ns")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Mahindra & Mahindra Limited (M&M). %s crore except EPS" % RS, out)
        self.assertIn("Source: Yahoo Finance (M&M.NS)", out)

    def test_user_agent_is_the_packs_own(self):
        self.assertEqual(facts.UA, "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)")

    def test_watchlist_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "watchlist.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"hits": [{"symbol": "TCS"}, {"symbol": "M&M.NS"}, {"symbol": "TCS"}]}, fh)
            self.assertEqual(facts.watchlist_symbols(p), ["TCS", "M&M"])
            self.assertEqual(facts.watchlist_symbols(os.path.join(tmp, "none.json")), [])
            code, out, _ = offline("--watchlist", p)
            self.assertEqual(code, 0)
            self.assertIn("(TCS).", out)
            self.assertIn("(M&M).", out)


class Money(unittest.TestCase):
    def test_crore(self):
        self.assertEqual(facts.CRORE, 10000000)
        self.assertEqual(facts.crore(731880000000), "73,188")          # TCS revenue, Sep 2026 quarter
        self.assertEqual(facts.crore(2832480000000), "283,248")
        self.assertEqual(facts.crore(478000000), "47.8")               # under 100 crore keeps a decimal
        self.assertEqual(facts.crore(-1020000000), "-102")
        self.assertEqual(facts.crore(0), "0.0")
        self.assertEqual(facts.crore(None), "-")

    def test_sentences(self):
        self.assertEqual(facts.money(731880000000), RS + "73,188 cr")
        self.assertEqual(facts.money(2832480000000), "%s283,248 cr (%s2.83 lakh crore)" % (RS, RS))
        self.assertEqual(facts.money(-31640000000), "-%s3,164 cr" % RS)
        self.assertEqual(facts.money(None), "n/a")
        self.assertEqual(facts.money(5145000000, "USD"), "USD 5,145 million")
        self.assertEqual(facts.per_share(38.37), RS + "38.37")
        self.assertEqual(facts.per_share(0.2, "USD"), "USD 0.20")
        self.assertEqual(facts.amount(5145000000, "USD"), "5,145")
        self.assertEqual(facts.amount(None), "-")

    def test_growth_and_margins(self):
        self.assertEqual(facts.pct(722750000000, 634370000000), 13.9)
        self.assertIsNone(facts.pct(100, None))
        self.assertIsNone(facts.pct(None, 100))
        self.assertIsNone(facts.pct(100, 0))
        self.assertIsNone(facts.pct(100, -50))        # growth from a loss means nothing
        self.assertEqual(facts.margin(138840000000, 731880000000), 19.0)
        self.assertIsNone(facts.margin(None, 100))
        self.assertIsNone(facts.margin(10, 0))
        self.assertIsNone(facts.margin(10, None))


class Parsing(unittest.TestCase):
    def test_tcs_series(self):
        ts = facts.parse_timeseries(doc("TCS"))
        rev = ts["quarterly"]["revenue"]
        self.assertEqual(sorted(rev), ["2025-06-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"])
        self.assertEqual(rev["2026-09-30"], 731880000000.0)
        self.assertEqual(ts["quarterly"]["net_income"]["2026-09-30"], 138840000000.0)
        self.assertEqual(ts["quarterly"]["eps_diluted"]["2026-09-30"], 38.37)
        self.assertEqual(ts["quarterly"]["gross_profit"]["2026-09-30"], 299460000000.0)
        self.assertEqual(ts["quarterly"]["ebitda"]["2026-09-30"], 201520000000.0)
        self.assertEqual(sorted(ts["annual"]["revenue"]), ["2023-03-31", "2024-03-31", "2025-03-31", "2026-03-31"])
        self.assertEqual(ts["currencies"], ["INR"])

    def test_only_three_month_values_are_quarters(self):
        d = {"timeseries": {"result": [
            {"meta": {"type": ["quarterlyTotalRevenue"]}, "quarterlyTotalRevenue": [
                None,
                {"asOfDate": "2026-06-30", "periodType": "3M", "currencyCode": "INR", "reportedValue": {"raw": 5.0}},
                {"asOfDate": "2026-09-30", "periodType": "TTM", "currencyCode": "INR", "reportedValue": {"raw": 9.0}},
                {"asOfDate": "2026-03-31", "periodType": "3M", "currencyCode": "INR", "reportedValue": {}}]},
            {"meta": {"type": ["quarterlyNetIncome"]}},
            {"meta": {"type": ["quarterlySomethingElse"]}, "quarterlySomethingElse": [
                {"asOfDate": "2026-06-30", "periodType": "3M", "reportedValue": {"raw": 1.0}}]}]}}
        ts = facts.parse_timeseries(d)
        self.assertEqual(ts["quarterly"]["revenue"], {"2026-06-30": 5.0})
        self.assertEqual(ts["quarterly"]["net_income"], {})
        with self.assertRaises(facts.FactsError):
            facts.parse_timeseries({"finance": {"error": "x"}})

    def test_a_symbol_yahoo_has_nothing_for(self):
        # the real answer for TATAMOTORS.NS (the symbol from before the 2025 demerger): every type, no values
        self.assertIn(b"TATAMOTORS.NS", raw("timeseries-TATAMOTORS.json"))
        with self.assertRaises(facts.FactsError) as cm:
            analysed("TATAMOTORS")
        self.assertIn("Yahoo has no quarterly or annual figures for TATAMOTORS", str(cm.exception))


class Tcs(unittest.TestCase):
    def setUp(self):
        self.r = analysed("TCS", quarters=6)

    def test_latest_quarter(self):
        q = self.r["rows"][0]
        self.assertEqual((q["end"], q["fiscal"], q["missing"]), ("2026-09-30", "Q2 FY27", False))
        self.assertEqual(q["revenue"], 731880000000.0)
        self.assertEqual(q["net_income"], 138840000000.0)
        self.assertEqual(q["eps_diluted"], 38.37)
        self.assertEqual(q["gross_margin"], 40.9)
        self.assertEqual(q["operating_margin"], 24.0)
        self.assertEqual(q["ebitda_margin"], 27.5)
        self.assertEqual(q["net_margin"], 19.0)
        self.assertEqual(q["revenue_qoq"], 1.3)          # 73,188 against 72,275

    def test_the_hole_is_a_row_not_a_guess(self):
        self.assertEqual([r["end"] for r in self.r["rows"]],
                         ["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"])
        hole = row(self.r, "2025-09-30")
        self.assertTrue(hole["missing"])
        for k in ("revenue", "net_income", "eps_diluted", "gross_margin", "net_margin", "revenue_yoy", "revenue_qoq"):
            self.assertIsNone(hole[k], k)
        self.assertEqual(self.r["holes"], ["2025-09-30"])
        self.assertEqual(facts.gap_notes(self.r), ["no figure from Yahoo for the quarter ending 2025-09-30"])
        self.assertEqual(self.r["behind"], [])

    def test_no_growth_across_the_hole(self):
        latest = self.r["rows"][0]                        # a year earlier is the hole
        self.assertIsNone(latest["revenue_yoy"])
        self.assertIsNone(latest["net_income_yoy"])
        self.assertIsNone(latest["eps_yoy"])
        self.assertFalse(latest["year_ago_present"])
        after = row(self.r, "2025-12-31")                 # the quarter before is the hole
        self.assertIsNone(after["revenue_qoq"])
        self.assertIsNone(after["net_income_qoq"])
        self.assertFalse(after["quarter_before_present"])
        self.assertEqual(row(self.r, "2026-03-31")["revenue_qoq"], 5.4)     # 70,698 against 67,087

    def test_growth_when_the_same_quarter_a_year_earlier_is_there(self):
        q = row(self.r, "2026-06-30")                     # against 2025-06-30: 63,437, 12,760, 35.27
        self.assertEqual(q["revenue_yoy"], 13.9)
        self.assertEqual(q["net_income_yoy"], 4.6)
        self.assertEqual(q["eps_yoy"], 4.6)

    def test_last_four_quarters_when_all_four_are_there(self):
        self.assertEqual(self.r["ttm_period"], ["2025-12-31", "2026-09-30"])
        self.assertEqual(self.r["ttm"]["revenue"], 670870000000.0 + 706980000000.0 + 722750000000.0 + 731880000000.0)
        self.assertEqual(self.r["ttm"]["net_income"], 516080000000.0)
        self.assertEqual(self.r["ttm"]["eps_diluted"], 142.66)            # 29.45 + 37.94 + 36.90 + 38.37
        self.assertEqual(self.r["ttm_missing"], {})

    def test_no_last_four_quarters_across_a_hole(self):
        d = doc("TCS")
        for t in ("quarterlyTotalRevenue", "quarterlyNetIncome", "quarterlyDilutedEPS"):
            d = without(d, t, "2026-09-30")
        r = facts.analyse(d, symbol="TCS", today=datetime.date(2026, 10, 10))
        self.assertEqual(r["rows"][0]["end"], "2026-06-30")
        self.assertEqual(r["ttm"], {})                    # Jun, Mar, Dec are there; Sep 2025 is not
        self.assertEqual(r["ttm_missing"]["revenue"], ["2025-09-30"])
        self.assertEqual(r["ttm_missing"]["eps_diluted"], ["2025-09-30"])
        company = {"result": r}
        self.assertIn("Last 4 quarters of revenue: not added up, Yahoo is missing 2025-09-30.",
                      facts.render(company, "x"))
        self.assertNotIn("Last 4 quarters (", facts.render(company, "x"))

    def test_one_missing_line_blocks_only_that_line(self):
        r = facts.analyse(without(doc("TCS"), "quarterlyDilutedEPS", "2026-03-31"), symbol="TCS", today=TODAY)
        self.assertIn("revenue", r["ttm"])
        self.assertNotIn("eps_diluted", r["ttm"])
        self.assertEqual(r["ttm_missing"], {"eps_diluted": ["2026-03-31"]})
        self.assertEqual(r["partial"], [{"end": "2026-03-31", "missing": ["eps_diluted"]}])
        self.assertIn("no diluted EPS from Yahoo for the quarter ending 2026-03-31", facts.gap_notes(r))

    def test_years(self):
        y = self.r["years"]
        self.assertEqual([x["fiscal"] for x in y], ["FY26", "FY25", "FY24", "FY23"])
        self.assertEqual(y[0]["eps_diluted"], 136.01)
        self.assertEqual(y[0]["revenue_yoy"], 4.6)
        self.assertIsNone(y[-1]["revenue_yoy"])           # nothing before FY23 to compare with
        self.assertEqual(self.r["fiscal_year_end_month"], 3)
        self.assertTrue(self.r["fiscal_year_end_known"])

    def test_consolidated_is_not_stated(self):
        self.assertIsNone(self.r["consolidated"])
        self.assertIn("does not state whether these figures are consolidated or standalone",
                      facts.render({"result": self.r}, "x"))

    def test_what_changed_in_words(self):
        lines = facts.changes(self.r)
        self.assertEqual(lines[0], "Quarter ending 2026-09-30 (Q2 FY27): revenue %s73,188 cr, net income "
                                   "%s13,884 cr, diluted EPS %s38.37." % (RS, RS, RS))
        self.assertEqual(lines[1], "No growth against a year earlier: Yahoo has no figure for the quarter "
                                   "ending 2025-09-30.")
        self.assertIn("Gross margin 40.9% (-3.9 points vs the quarter before).", lines)
        self.assertIn("Net margin 19.0% (+0.5 points vs the quarter before).", lines)
        self.assertTrue(any(l.startswith("Revenue +1.3% vs the quarter before") for l in lines))
        self.assertFalse(any("unusual items" in l for l in lines))        # none in this quarter

    def test_a_one_off_is_flagged_when_it_is_big_enough(self):
        # with the Sep 2026 quarter taken out the newest is June 2026: unusual items of -576 crore against a
        # net income of 13,349 crore, under the 5% line, so no flag
        d = doc("TCS")
        for t in ("quarterlyTotalRevenue", "quarterlyNetIncome", "quarterlyDilutedEPS"):
            d = without(d, t, "2026-09-30")
        r = facts.analyse(d, symbol="TCS", today=TODAY)
        self.assertEqual(r["rows"][0]["unusual_items"], -5760000000.0)
        self.assertEqual(r["rows"][0]["revenue_yoy"], 13.9)
        lines = facts.changes(r)
        self.assertIn("revenue %s72,275 cr (+13.9%% vs a year earlier)" % RS, lines[0])
        self.assertIn("diluted EPS %s36.90 (+4.6%% vs a year earlier)" % RS, lines[0])
        self.assertFalse(any("unusual items" in l for l in lines))
        # back to December 2025: -3,164 crore against a net income of 10,657 crore
        for end in ("2026-06-30", "2026-03-31"):
            for t in ("quarterlyTotalRevenue", "quarterlyNetIncome", "quarterlyDilutedEPS"):
                d = without(d, t, end)
        lines = facts.changes(facts.analyse(d, symbol="TCS", today=TODAY))
        self.assertTrue(lines[0].startswith("Quarter ending 2025-12-31 (Q3 FY26)"))
        self.assertTrue(any(l.startswith("Yahoo lists unusual items of -%s3,164 cr in this quarter (a charge" % RS)
                            for l in lines), lines)
        self.assertIn("No change on the quarter before: Yahoo has no figure for it.", lines)
        self.assertFalse(any("points vs the quarter before" in l for l in lines))

    def test_quarters_flag(self):
        self.assertEqual(len(analysed("TCS", quarters=2)["rows"]), 2)
        self.assertEqual(len(analysed("TCS", quarters=40)["rows"]), 6)    # Yahoo has no more
        self.assertEqual(analysed("TCS", quarters=2)["holes"], ["2025-09-30"])   # still reported


class Banks(unittest.TestCase):
    def test_hdfc_bank_blanks_are_blank_not_zero(self):
        r = analysed("HDFCBANK")
        self.assertEqual([x["end"] for x in r["rows"]], ["2025-06-30", "2025-03-31"])
        q = r["rows"][0]
        for k in ("gross_profit", "operating_income", "ebitda", "gross_margin", "operating_margin", "ebitda_margin"):
            self.assertIsNone(q[k], k)
        self.assertEqual(q["net_income"], 162579100000.0)
        self.assertEqual(q["eps_diluted"], 10.56)
        self.assertEqual(q["net_margin"], 19.0)
        self.assertEqual(q["net_interest_income"], 396633600000.0)
        self.assertTrue(r["lender"])
        self.assertFalse(analysed("TCS")["lender"])
        table = [l for l in facts.render({"result": r}, "x").splitlines() if l.startswith("2025-06-30")][0]
        self.assertEqual(table.split(), ["2025-06-30", "Q1", "FY26", "85,346", "-", "-", "-", "-", "19.0%",
                                         "16,258", "10.56", "-"])

    def test_hdfc_bank_is_five_quarters_behind_and_says_so(self):
        r = analysed("HDFCBANK")
        self.assertEqual(r["behind"], ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"])
        self.assertEqual(r["ttm"], {})                    # two quarters are not four
        self.assertEqual(r["ttm_missing"]["revenue"], ["2024-12-31", "2024-09-30"])
        self.assertIsNone(r["rows"][0]["revenue_yoy"])
        note = facts.gap_notes(r)[-1]
        self.assertIn("Yahoo stops at the quarter ending 2025-06-30: 5 later quarters (2025-09-30 to 2026-09-30)", note)
        self.assertIn("These figures are old", note)
        # a quarter is only "behind" once SEBI's deadline for it has passed
        early = facts.analyse(doc("HDFCBANK"), symbol="HDFCBANK", today=datetime.date(2025, 11, 10))
        self.assertEqual(early["behind"], [])
        self.assertEqual(facts.analyse(doc("HDFCBANK"), symbol="HDFCBANK")["behind"], [])   # no date, no check

    def test_bank_words(self):
        lines = facts.changes(analysed("HDFCBANK"))
        self.assertTrue(any(l.startswith("A bank or lender: Yahoo has no gross profit, operating income or EBITDA")
                            and l.endswith("Net interest income %s39,663 cr." % RS) for l in lines))
        self.assertFalse(any(l.startswith(("Gross margin", "Operating margin", "EBITDA margin")) for l in lines))

    def test_sbi_has_half_filled_quarters(self):
        r = analysed("SBIN")
        self.assertEqual(r["holes"], [])
        self.assertEqual(r["partial"], [{"end": "2025-09-30", "missing": ["net_income", "eps_diluted"]},
                                        {"end": "2025-06-30", "missing": ["revenue"]}])
        latest = r["rows"][0]
        self.assertEqual(latest["revenue_yoy"], 4.6)      # revenue for Sep 2025 is there
        self.assertIsNone(latest["net_income_yoy"])       # net income for Sep 2025 is not
        self.assertIsNone(latest["eps_yoy"])
        self.assertIsNone(row(r, "2025-09-30")["net_margin"])
        self.assertIsNone(row(r, "2025-06-30")["net_margin"])
        self.assertEqual(row(r, "2026-06-30")["eps_yoy"], 9.9)
        self.assertIsNone(row(r, "2026-06-30")["revenue_yoy"])
        self.assertEqual(facts.gap_notes(r), ["no net income or diluted EPS from Yahoo for the quarter ending 2025-09-30",
                                              "no revenue from Yahoo for the quarter ending 2025-06-30"])
        self.assertTrue(any(l.startswith("Yahoo lists unusual items of %s6,879 cr in this quarter (a gain" % RS)
                            for l in facts.changes(r)))


class OtherCurrency(unittest.TestCase):
    def test_infosys_comes_in_dollars_and_is_not_converted(self):
        r = analysed("INFY")
        self.assertEqual(r["currency"], "USD")
        self.assertEqual(r["rows"][0]["revenue"], 5145000000.0)
        code, out, _ = offline("INFY", "--no-events")
        self.assertEqual(code, 0)
        self.assertIn("USD millions except EPS (USD); Yahoo reports this company in USD, not rupees, and nothing "
                      "is converted", out)
        self.assertIn("revenue USD 5,145 million (+1.4% vs a year earlier)", out)
        self.assertNotIn(RS, out)
        self.assertNotIn(" cr", out.replace("crore", ""))


class Feeds(unittest.TestCase):
    def test_nse_dates_are_india_time(self):
        dt = datetime.datetime
        self.assertEqual(facts.parse_nse_date("03-Nov-2026 10:29:23"), dt(2026, 11, 3, 10, 29, 23))
        self.assertEqual(facts.parse_nse_date("23-Nov-2026 18:19"), dt(2026, 11, 23, 18, 19))
        self.assertEqual(facts.parse_nse_date("27-Nov-2026"), dt(2026, 11, 27))
        self.assertEqual(facts.parse_nse_date("Tue, 24 Nov 2026 02:10:07 +0530"), dt(2026, 11, 24, 2, 10, 7))
        self.assertEqual(facts.parse_nse_date("Mon, 23 Nov 2026 20:40:07 GMT"), dt(2026, 11, 24, 2, 10, 7))
        self.assertIsNone(facts.parse_nse_date("-"))
        self.assertIsNone(facts.parse_nse_date("31-Feb-2026"))
        self.assertIsNone(facts.parse_nse_date(""))

    def test_corporate_actions_feed(self):
        items = facts.parse_feed(raw("Corporate_action.xml"))
        self.assertEqual(len(items), 7)
        zee = next(i for i in items if i["company"].startswith("Zee"))
        self.assertEqual(zee["company"], "Zee Entertainment Enterprises Limited")
        self.assertEqual(zee["ex_date"], datetime.date(2026, 11, 25))
        self.assertEqual(zee["fields"]["PURPOSE"], "BUY BACK")
        self.assertEqual(zee["fields"]["RECORD DATE"], "25-Nov-2026")
        self.assertEqual(zee["published"], datetime.datetime(2026, 11, 23, 6, 11, 49))
        self.assertEqual(items[0]["company"], "Fusion CX Limited")
        self.assertEqual(items[0]["fields"]["PURPOSE"], "DIVIDEND - RE 0.75 PER SHARE")

    def test_results_and_board_feeds(self):
        items = facts.parse_feed(raw("Financial_Results.xml"))
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["company"], "Gammon India Limited")     # the title's line break is gone
        self.assertEqual(items[0]["fields"]["RELATING TO"], "First Quarter")
        self.assertEqual(items[0]["fields"]["CONSOLIDATED/NON-CONSOLIDATED"], "Non-Consolidated")
        self.assertEqual(items[0]["fields"]["PERIOD ENDED"], "30-Jun-2024")
        self.assertEqual(items[0]["published"], datetime.datetime(2026, 11, 3, 10, 29, 23))
        self.assertEqual(facts.parse_feed(raw("Board_Meetings.xml")), [])   # a valid feed with no items
        with self.assertRaises(facts.FactsError):
            facts.parse_feed(b"<html>Access Denied")

    def test_names(self):
        self.assertEqual(facts.norm_name("Mahindra & Mahindra Ltd."), "mahindra and mahindra")
        self.assertEqual(facts.norm_name("Zee Entertainment Enterprises Limited \n"), "zee entertainment enterprises")
        self.assertEqual(facts.norm_name("S. P. Apparels Limited"), "s p apparels")
        self.assertNotEqual(facts.norm_name("Fusion CX Limited"), facts.norm_name("Fusion Finance Limited"))
        names = facts.parse_names(raw("universe.txt").decode("utf-8"))
        self.assertEqual(names["M&M"], "Mahindra & Mahindra Limited")
        self.assertEqual(names["ZEEL"], "Zee Entertainment Enterprises Limited")
        csv_text = ("SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, "
                    "FACE VALUE\nM&M,Mahindra & Mahindra Limited,EQ,03-JAN-1996,5,1,INE101A01026,5\n"
                    "TCS,Tata Consultancy Services Limited,EQ,25-AUG-2004,1,1,INE467B01029,1\n")
        self.assertEqual(facts.parse_names(csv_text), {"M&M": "Mahindra & Mahindra Limited",
                                                       "TCS": "Tata Consultancy Services Limited"})

    def feeds(self):
        return facts.read_feeds(facts.Fetcher(offline=DIR))

    def test_matching_by_company_name(self):
        feeds = self.feeds()
        self.assertEqual([feeds[k]["count"] for k in ("results", "actions", "board")], [3, 7, 0])
        self.assertEqual(feeds["actions"]["newest"], datetime.datetime(2026, 11, 23, 6, 11, 49))
        ev = facts.events_for("ZEEL", "Zee Entertainment Enterprises Limited", feeds, TODAY)
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["kind"], ev[0]["date"], ev[0]["today"]), ("ex-date", "2026-11-25", False))
        self.assertEqual(ev[0]["text"], "ex-date 2026-11-25 (in 1 day): BUY BACK, record date 25-Nov-2026")
        # two companies that start alike are not mixed up
        cx = facts.events_for("FUSIONCX", "Fusion CX Limited", feeds, TODAY)
        self.assertEqual([e["date"] for e in cx], ["2026-11-27"])
        self.assertEqual(facts.events_for("FUSION", "Fusion Finance Limited", feeds, TODAY), [])
        self.assertEqual(facts.events_for("TCS", "Tata Consultancy Services Limited", feeds, TODAY), [])
        self.assertEqual(facts.events_for("TCS", None, feeds, TODAY), [])     # no name, no match, no crash

    def test_results_items_match_too(self):
        # the company is in the real feed; the symbol is given by hand here (it is not in the cut-down universe)
        ev = facts.events_for("GAMMONIND", "Gammon India Ltd", self.feeds(), datetime.date(2026, 11, 3))
        self.assertEqual(len(ev), 3)
        self.assertTrue(all(e["kind"] == "results filed" and e["today"] for e in ev))
        self.assertEqual(ev[0]["text"], "results filed with NSE 2026-11-03 10:29 IST (TODAY): First Quarter, "
                                        "Unaudited, Non-Consolidated, period ended 30-Jun-2024")
        self.assertIn("INSIDE TODAY'S TRADE for GAMMONIND: results filed", facts.trade_line("GAMMONIND", ev, True))

    def test_the_trade_line(self):
        feeds = self.feeds()
        name = "Zee Entertainment Enterprises Limited"
        before = facts.events_for("ZEEL", name, feeds, TODAY)
        self.assertEqual(facts.trade_line("ZEEL", before, True),
                         "Nothing for ZEEL lands today in NSE's feeds, but ex-date 2026-11-25 ahead: a trade "
                         "closed today is not exposed, anything held past it is.")
        on = facts.events_for("ZEEL", name, feeds, datetime.date(2026, 11, 25))
        self.assertTrue(on[0]["today"])
        self.assertIn("ex-date 2026-11-25 (TODAY)", on[0]["text"])
        self.assertTrue(facts.trade_line("ZEEL", on, True).startswith("INSIDE TODAY'S TRADE for ZEEL: ex-date."))
        after = facts.events_for("ZEEL", name, feeds, datetime.date(2026, 11, 27))
        self.assertIn("(2 days ago)", after[0]["text"])
        self.assertEqual(facts.trade_line("ZEEL", after, True),
                         "Nothing for ZEEL in NSE's results, corporate-action or board-meeting feeds.")
        self.assertIn("Could not read NSE's feeds", facts.trade_line("ZEEL", [], False))

    def test_a_board_meeting_item(self):
        # NSE's board-meetings feed had no items when the fixtures were saved, so this item is MADE UP in the
        # shape of the other two feeds. It only checks that a company and a date are picked out of such an item.
        xml = ("<rss><channel><item><title>Tata Consultancy Services Limited</title>"
               "<link>https://nsearchives.nseindia.com/corporate/TCS_02122026101500_BM.pdf</link>"
               "<description>To consider and approve the financial results |BOARD MEETING DATE:08-Jan-2027"
               "</description><pubDate>02-Dec-2026 10:15:00</pubDate></item></channel></rss>")
        feeds = {"board": {"items": facts.parse_feed(xml)}}
        day = datetime.date(2027, 1, 6)
        ev = facts.events_for("TCS", "Tata Consultancy Services Limited", feeds, day)
        self.assertEqual((ev[0]["kind"], ev[0]["date"], ev[0]["today"]), ("board meeting", "2027-01-08", False))
        self.assertIn("board meeting 2027-01-08 (in 2 days)", ev[0]["text"])
        self.assertEqual(len(facts.events_for("TCS", None, feeds, day)), 1)     # by the symbol in the file name
        self.assertTrue(facts.events_for("TCS", None, feeds, datetime.date(2027, 1, 8))[0]["today"])

    def test_names_come_from_the_universe_file(self):
        notes = []
        names, where = facts.load_names(facts.Fetcher(offline=DIR), notes=notes)
        self.assertTrue(where.endswith("universe.txt"))
        self.assertEqual(names["HDFCBANK"], "HDFC Bank Limited")
        with tempfile.TemporaryDirectory() as tmp:
            names, where = facts.load_names(facts.Fetcher(offline=tmp), notes=notes)
            self.assertEqual((names, where), ({}, None))
            self.assertIn("feed items cannot be matched to symbols", notes[0])
            feeds = facts.read_feeds(facts.Fetcher(offline=tmp), notes)
            self.assertTrue(all(f["error"] for f in feeds.values()))


class NextResults(unittest.TestCase):
    def test_the_date_is_never_guessed(self):
        n = facts.next_results(analysed("TCS"), TODAY)
        self.assertEqual((n["quarter_end"], n["deadline"], n["date_known"]), ("2026-12-31", "2027-02-14", False))
        self.assertTrue(n["quarter_still_running"])
        self.assertIn("SEBI's deadline for its results is 2027-02-14, and the company picks the day", n["text"])
        self.assertIn("The date is not in these free sources: look at the company's investor relations page",
                      n["text"])

    def test_a_quarter_that_ended_but_is_not_on_yahoo_yet(self):
        d = doc("TCS")
        for t in ("quarterlyTotalRevenue", "quarterlyNetIncome", "quarterlyDilutedEPS"):
            d = without(d, t, "2026-09-30")
        day = datetime.date(2026, 10, 10)
        n = facts.next_results(facts.analyse(d, symbol="TCS", today=day), day)
        self.assertEqual((n["quarter_end"], n["deadline"], n["quarter_still_running"]),
                         ("2026-09-30", "2026-11-14", False))
        self.assertIn("is over and Yahoo has no figures for it yet", n["text"])

    def test_when_yahoo_is_far_behind(self):
        n = facts.next_results(analysed("HDFCBANK"), TODAY)
        self.assertEqual((n["quarter_end"], n["deadline"]), ("2026-09-30", "2026-11-14"))
        self.assertIn("were due by 2026-11-14 (SEBI's deadline), so they are probably out, but Yahoo does not "
                      "have them", n["text"])


class CommandLine(unittest.TestCase):
    def test_a_full_run(self):
        code, out, err = offline("TCS", "HDFCBANK", "ZEEL")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Tata Consultancy Services Limited (TCS). %s crore except EPS (%s). Source: Yahoo Finance "
                      "(TCS.NS), fetched 2026-11-24 02:30 IST." % (RS, RS), out)
        self.assertIn("2025-09-30  Q2 FY26    no figure from Yahoo for this quarter", out)
        self.assertIn("  - no figure from Yahoo for the quarter ending 2025-09-30.", out)
        self.assertIn("Last 4 quarters (2025-12 to 2026-09): revenue %s283,248 cr (%s2.83 lakh crore), net income "
                      "%s51,608 cr, diluted EPS %s142.66." % (RS, RS, RS, RS), out)
        self.assertIn("  - ex-date 2026-11-25 (in 1 day): BUY BACK, record date 25-Nov-2026", out)
        self.assertIn("  - Nothing for TCS in NSE's results, corporate-action or board-meeting feeds.", out)
        self.assertIn("  - financial results: 3 items, newest 2026-11-03 10:29 IST", out)
        self.assertIn("  - board meetings: 0 items", out)
        self.assertIn("These feeds are short rolling windows", out)
        self.assertIn("Not financial advice.", out)
        for word in ("cheap", "undervalued", "overvalued", "a buy", "a sell", "should buy", "$"):
            self.assertNotIn(word, out.lower(), word)

    def test_exit_codes(self):
        self.assertEqual(offline("TCS")[0], 0)
        code, out, err = offline("TCS", "TATAMOTORS")
        self.assertEqual(code, 1)                                  # some
        self.assertIn("facts: TATAMOTORS: Yahoo has no quarterly or annual figures for TATAMOTORS", err)
        self.assertIn("(TCS).", out)
        self.assertEqual(offline("TATAMOTORS")[0], 2)              # none
        code, _, err = offline("NOFILE")
        self.assertEqual(code, 2)
        self.assertIn("offline: no file", err)
        code, _, err = offline("TCS", "BAD/SYM")
        self.assertEqual(code, 1)
        self.assertIn("BAD/SYM is not an NSE symbol", err)
        self.assertEqual(offline("TCS", "--quarters", "0")[0], 2)
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = offline("--watchlist", os.path.join(tmp, "none.json"))
            self.assertEqual(code, 2)
            self.assertIn("no symbols", err)

    def test_json(self):
        code, out, _ = offline("TCS", "ZEEL", "--json")
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertEqual(d["checked_ist"], "2026-11-24 02:30")
        tcs, zee = d["companies"]
        self.assertEqual(tcs["symbol"], "TCS")
        self.assertEqual(tcs["result"]["holes"], ["2025-09-30"])
        self.assertEqual(tcs["gaps"], ["no figure from Yahoo for the quarter ending 2025-09-30"])
        self.assertIsNone(tcs["result"]["rows"][0]["revenue_yoy"])
        self.assertIsNone(tcs["result"]["consolidated"])
        self.assertEqual(tcs["events"], [])
        self.assertFalse(tcs["next_results"]["date_known"])
        self.assertEqual(zee["events"][0]["date"], "2026-11-25")
        self.assertEqual(d["feeds"]["actions"]["count"], 7)
        self.assertEqual(d["feeds"]["board"]["count"], 0)
        self.assertIn("short rolling windows", d["window_note"])

    def test_no_events_reads_no_feeds(self):
        code, out, _ = offline("TCS", "--no-events")
        self.assertEqual(code, 0)
        self.assertNotIn("NSE feeds", out)
        self.assertNotIn("Results and actions", out)
        self.assertIn("Tata Consultancy Services Limited (TCS)", out)

    def test_metrics_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "trader", "metrics.json")
            code, out, _ = offline("TCS", "HDFCBANK", "--metrics", path)
            self.assertEqual(code, 0)
            self.assertIn("(2 companies)", out)
            code, _, _ = offline("M&M", "TCS", "--metrics", path, "--no-events")
            self.assertEqual(code, 0)
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        self.assertEqual(d["as_of"], "2026-11-24")
        self.assertEqual([r["symbol"] for r in d["companies"]], ["HDFCBANK", "M&M", "TCS"])   # merged, one row each
        hdfc, mm, tcs = d["companies"]
        self.assertEqual(tcs["period_end"], "2026-09-30")
        self.assertEqual(tcs["revenue_ttm"], 2832480000000.0)
        self.assertEqual(tcs["eps_ttm"], 142.66)
        self.assertIsNone(tcs["revenue_growth_yoy"])
        self.assertEqual(tcs["quarters_missing"], ["2025-09-30"])
        self.assertEqual(tcs["net_margin"], 19.0)
        self.assertEqual(tcs["currency"], "INR")
        self.assertIsNone(hdfc["gross_margin"])
        self.assertIsNone(hdfc["operating_margin"])
        self.assertIsNone(hdfc["revenue_ttm"])
        self.assertEqual(len(hdfc["quarters_behind"]), 5)
        self.assertEqual(mm["name"], "Mahindra & Mahindra Limited")
        self.assertEqual(mm["consolidated"], "not stated by Yahoo")


if __name__ == "__main__":
    unittest.main()
