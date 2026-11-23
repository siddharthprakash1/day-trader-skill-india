"""Offline tests for trader-insider/deals.py, on files saved from NSE's archive host on
2026-11-24 (bulk.csv and block.csv of the 2026-11-23 session, the insider-trading and SAST
Regulation 29 feeds, nine real insider XBRL files and a cut of EQUITY_L.csv)."""

import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

deals = load("trader-insider", "deals")
DIR = os.path.join(FIX, "trader-insider")
NOW = "2026-11-24 02:30"
UTC = datetime.timezone.utc

RAVINDRA = "IT_1197873_1662973_02052026044642_WEB.xml"    # director, ESOP
MANGALAM = "IT_1197871_1662943_02052026022032_WEB.xml"    # promoter, pledge
BAJAJ = "IT_1197866_1662688_30042026102612_WEB.xml"       # promoter buys, promoter group sells
HCL = "IT_1197864_1662516_30042026072326_WEB.xml"         # type Buy, mode "Market Sale"
ECLERX = "IT_1197851_1661892_30042026020546_WEB.xml"      # welfare trust, off market
RESPONSIVE = "IT_1197858_1662307_30042026055528_WEB.xml"  # promoter group, pledge released
HCL_TRUST = "IT_1197862_1662394_30042026063149_WEB.xml"   # four rows, no CategoryOfPerson
SANGAM = "IT_1197855_1662038_30042026035329_WEB.xml"      # welfare trust sells, mode ESOP


def raw(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


def text(name):
    return raw(name).decode("utf-8")


def run(args, tmp=None):
    """Run the CLI offline with every path pointed away from the real trader/ folder."""
    tmp = tmp or tempfile.mkdtemp()
    base = ["--offline", DIR, "--now", NOW, "--state-dir", os.path.join(tmp, "state"),
            "--universe", os.path.join(tmp, "universe.txt")]
    for flag in ("portfolio", "watchlist", "plan"):
        if "--" + flag not in args:
            base += ["--" + flag, os.path.join(tmp, flag + ".json")]
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = deals.main(args + base)
    return code, out.getvalue(), err.getvalue()


def write_json(tmp, name, obj):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return path


class Time(unittest.TestCase):
    def test_ist_is_a_fixed_five_and_a_half_hours(self):
        utc = datetime.datetime(2026, 11, 23, 21, 0, tzinfo=UTC)
        self.assertEqual(deals.to_ist(utc), datetime.datetime(2026, 11, 24, 2, 30))
        self.assertEqual(deals.ist_to_utc(datetime.datetime(2026, 11, 24, 2, 30)), utc)
        summer = datetime.datetime(2026, 6, 1, 0, 0, tzinfo=UTC)      # no daylight saving
        self.assertEqual(deals.to_ist(summer), datetime.datetime(2026, 6, 1, 5, 30))

    def test_now_is_india_time(self):
        want = datetime.datetime(2026, 11, 23, 21, 0, tzinfo=UTC)
        self.assertEqual(deals.parse_now("2026-11-24 02:30"), want)
        self.assertEqual(deals.parse_now("2026-11-24 02:30 IST"), want)
        self.assertEqual(deals.parse_now("2026-11-23T21:00:00Z"), want)
        with self.assertRaises(deals.DealsError):
            deals.parse_now("yesterday")

    def test_dates_as_nse_writes_them(self):
        self.assertEqual(deals.parse_date("23-NOV-2026"), datetime.date(2026, 11, 23))
        self.assertEqual(deals.parse_date("02-May-2026"), datetime.date(2026, 5, 2))
        self.assertEqual(deals.parse_date("2026-04-29"), datetime.date(2026, 4, 29))
        self.assertIsNone(deals.parse_date("NO RECORDS"))
        self.assertEqual(deals.parse_pubdate("02-May-2026 16:46"), datetime.datetime(2026, 5, 2, 16, 46))
        self.assertIsNone(deals.parse_pubdate("soon"))

    def test_last_session_knows_the_holiday(self):
        self.assertFalse(deals.is_trading_day(datetime.date(2026, 11, 24)))   # Guru Nanak Jayanti
        self.assertTrue(deals.is_trading_day(datetime.date(2026, 11, 23)))
        self.assertFalse(deals.is_trading_day(datetime.date(2026, 11, 22)))   # Sunday
        monday = datetime.date(2026, 11, 23)
        self.assertEqual(deals.last_session(deals.parse_now("2026-11-24 02:30")), monday)
        self.assertEqual(deals.last_session(deals.parse_now("2026-11-24 20:00")), monday)
        self.assertEqual(deals.last_session(deals.parse_now("2026-11-23 19:00")), monday)
        # during Monday's session the newest file is still Friday's
        self.assertEqual(deals.last_session(deals.parse_now("2026-11-23 10:00")), datetime.date(2026, 11, 20))


class Money(unittest.TestCase):
    def test_crore_and_lakh(self):
        self.assertEqual(deals.to_crore(10000000), 1.0)
        self.assertEqual(deals.money(166418280.98), "₹16.64 crore")
        self.assertEqual(deals.money(7000000), "₹70.00 lakh")
        self.assertEqual(deals.money(8352160950), "₹835.22 crore")
        self.assertEqual(deals.money(None), "value not given")
        self.assertEqual(deals.inr(1765.13), "₹1,765.13")

    def test_indian_grouping(self):
        self.assertEqual(deals.indian(7000000), "70,00,000")
        self.assertEqual(deals.indian(3700224520), "3,70,02,24,520")
        self.assertEqual(deals.indian(647213), "6,47,213")
        self.assertEqual(deals.indian(999), "999")
        self.assertEqual(deals.indian(1000), "1,000")


class BulkAndBlock(unittest.TestCase):
    def setUp(self):
        self.bulk = deals.parse_deals(text("bulk.csv"), "bulk")
        self.block = deals.parse_deals(text("block.csv"), "block")

    def test_parses_every_row(self):
        self.assertEqual(len(self.bulk), 89)
        self.assertEqual(len(self.block), 5)
        r = self.bulk[0]
        self.assertEqual((r["date"], r["symbol"], r["side"], r["quantity"], r["price"]),
                         ("2026-11-23", "AJMERA", "BUY", 1015117, 163.94))
        self.assertEqual(r["client"], "MICROCURVES TRADING PRIVATE LIMITED")
        self.assertEqual(r["name"], "Ajmera Realty & Inf I Ltd")
        self.assertIsNone(r["remarks"])                       # "-" in the file
        self.assertTrue(all(b["kind"] == "block" and b["symbol"] == "LENSKART" for b in self.block))

    def test_value_in_crore(self):
        r = self.bulk[0]
        self.assertAlmostEqual(r["value"], 1015117 * 163.94, places=2)
        self.assertEqual(r["value_crore"], 16.64)
        steadview = next(b for b in self.block if b["side"] == "SELL" and b["quantity"] == 11520222)
        self.assertEqual(steadview["value"], 11520222 * 725.0)
        self.assertEqual(steadview["value_crore"], 835.22)

    def test_same_day_buy_and_sell(self):
        groups = {g["symbol"]: g for g in deals.group_deals(self.bulk)}
        ajmera = {c["client"]: c for c in groups["AJMERA"]["clients"]}
        micro = ajmera["MICROCURVES TRADING PRIVATE LIMITED"]
        self.assertTrue(micro["same_day"])
        self.assertEqual((micro["buy_qty"], micro["sell_qty"], micro["net_qty"]), (1015117, 1015117, 0))
        self.assertEqual(groups["AJMERA"]["same_day_clients"], 2)
        line = deals.describe_client(micro)
        self.assertIn("bought 1,015,117 at ₹163.94 (₹16.64 crore)", line)
        self.assertIn("Bought and sold the same day, net 0 shares", line)
        graviton = groups["APLAPOLLO"]["clients"][0]
        self.assertTrue(graviton["same_day"])
        self.assertEqual(graviton["net_qty"], 1993475 - 2014740)
        self.assertIn("net sold 21,265 shares", deals.describe_client(graviton))

    def test_one_way_deal_is_not_called_churn(self):
        lens = deals.group_deals(self.block)[0]
        self.assertEqual(lens["symbol"], "LENSKART")
        self.assertEqual(lens["same_day_clients"], 0)
        seller = lens["clients"][0]
        self.assertEqual(seller["client"], "STEADVIEW CAPITAL MAURITIUS LIMITED-FDI")
        self.assertEqual((seller["buy_qty"], seller["net_qty"]), (0, -11520222))
        self.assertNotIn("same day", deals.describe_client(seller))
        self.assertEqual(lens["sell_value_crore"], round((11520222 + 1177480) * 725 / 1e7, 2))

    def test_biggest_stock_first(self):
        groups = deals.group_deals(self.bulk)
        self.assertEqual(len(groups), 25)
        self.assertEqual([g["symbol"] for g in groups[:2]], ["LENSKART", "APLAPOLLO"])

    def test_min_crore_keeps_both_sides_of_a_churn(self):
        big = deals.pick(self.bulk, min_crore=100)
        self.assertEqual(sorted({r["symbol"] for r in big}), ["APLAPOLLO", "LENSKART"])
        apl = [r for r in big if r["symbol"] == "APLAPOLLO"]
        self.assertEqual(sorted(r["side"] for r in apl), ["BUY", "SELL"])
        self.assertEqual(deals.pick(self.bulk, min_crore=100000), [])

    def test_tolerates_an_empty_file_and_rejects_another_file(self):
        head = text("block.csv").splitlines()[0]
        self.assertEqual(deals.parse_deals(head + "\nNO RECORDS\n", "block"), [])
        self.assertEqual(deals.parse_deals("", "block"), [])
        with self.assertRaises(deals.DealsError):
            deals.parse_deals("<html>blocked</html>", "bulk")

    def test_bulk_command(self):
        code, out, _ = run(["bulk", "--symbol", "LENSKART"])
        self.assertEqual(code, 0)
        self.assertIn("BULK DEALS, session 2026-11-23: 1 row in 1 stock", out)
        self.assertIn("BLOCK DEALS, session 2026-11-23: 5 rows in 1 stock", out)
        self.assertIn("SOCIETE GENERALE - ODI: bought 6,176,351 at ₹725.00 (₹447.79 crore)", out)
        self.assertIn("one side of a trade", out)
        self.assertIn("the last completed NSE session", out)
        self.assertNotIn("AJMERA", out)
        code, out, _ = run(["bulk", "--symbol", "RELIANCE"])
        self.assertEqual(code, 1)


class NameToSymbol(unittest.TestCase):
    def setUp(self):
        self.names = deals.Names(deals.parse_equity_list(text("EQUITY_L.csv")))

    def test_ampersand_names(self):
        for name in ("Mahindra & Mahindra Limited", "MAHINDRA AND MAHINDRA LTD.",
                     "Mahindra &amp; Mahindra Ltd", "  mahindra  &  mahindra limited "):
            self.assertEqual(self.names.symbol(name), "M&M", name)
        self.assertEqual(self.names.symbol("Mahindra & Mahindra Financial Services Limited"), "M&MFIN")
        self.assertEqual(self.names.symbol("Bajaj Auto Limited"), "BAJAJ-AUTO")

    def test_names_as_the_feeds_write_them(self):
        self.assertEqual(self.names.symbol("Indusind Bank Limited"), "INDUSINDBK")      # list: IndusInd
        self.assertEqual(self.names.symbol("Dr. Lal Path Labs Ltd."), "LALPATHLAB")
        self.assertEqual(self.names.symbol("Dr Lal PathLabs Limited"), "LALPATHLAB")
        self.assertEqual(self.names.symbol("JINDAL STEEL LIMITED"), "JINDALSTEL")
        self.assertEqual(self.names.symbol("Sangam (India) Limited"), "SANGAMIND")
        self.assertIsNone(self.names.symbol("Siddhika Coatings Limited"))                # not in the list
        self.assertIsNone(self.names.symbol(""))

    def test_universe_file_wins_over_the_equity_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            uni = os.path.join(tmp, "universe.txt")
            with open(uni, "w", encoding="utf-8") as fh:
                fh.write("M&M|Mahindra & Mahindra Limited\nKFINTECH|Kfin Technologies Limited\n")
            names = deals.load_names(deals.Fetcher(DIR), uni, os.path.join(tmp, "EQUITY_L.csv"))
            self.assertEqual(names.source, uni)
            self.assertEqual(len(names), 2)
            self.assertEqual(names.symbol("Mahindra and Mahindra Ltd"), "M&M")
            self.assertIsNone(names.symbol("Samhi Hotels Limited"))
            fallback = deals.load_names(deals.Fetcher(DIR), os.path.join(tmp, "none.txt"),
                                        os.path.join(tmp, "EQUITY_L.csv"))
            self.assertEqual(fallback.symbol("Samhi Hotels Limited"), "SAMHI")
            self.assertFalse(os.path.exists(os.path.join(tmp, "EQUITY_L.csv")))   # offline writes no cache

    def test_cached_equity_list_is_read_without_the_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "EQUITY_L.csv")
            with open(cache, "w", encoding="utf-8") as fh:
                fh.write(text("EQUITY_L.csv"))
            fetcher = deals.Fetcher()                       # online, but must not be used

            def boom(url):
                raise AssertionError("network used: %s" % url)
            fetcher._download = boom
            names = deals.load_names(fetcher, os.path.join(tmp, "none.txt"), cache)
            self.assertEqual(names.symbol("HCL Technologies Limited"), "HCLTECH")

    def test_not_the_equity_list(self):
        with self.assertRaises(deals.DealsError):
            deals.parse_equity_list("<html>nope</html>")


class Feeds(unittest.TestCase):
    def test_insider_feed(self):
        feed = deals.parse_feed(raw("Insider_Trading.xml"), "insider")
        items = feed["items"]
        self.assertEqual(len(items), 12)                     # 20 <item>s, repeats folded
        self.assertEqual(sum(i["repeats"] for i in items), 20)
        first = items[0]
        self.assertEqual(first["company"], "Ravindra Energy Limited")
        self.assertEqual(first["published"], "2026-05-02 16:46")
        self.assertEqual(first["id"], RAVINDRA)
        self.assertEqual(first["security"], "Equity Shares")
        self.assertTrue(first["link"].startswith("https://nsearchives.nseindia.com/corporate/xbrl/"))
        self.assertEqual(feed["built"], "Tue, 24 Nov 2026 01:50:07 +0530")
        self.assertEqual(items, sorted(items, key=lambda i: i["published"], reverse=True))

    def test_sast_feed(self):
        items = deals.parse_feed(raw("Sast_Regulation29.xml"), "sast")["items"]
        self.assertEqual(len(items), 19)
        self.assertEqual(items[0]["company"], "Samhi Hotels Limited")
        self.assertEqual(items[0]["acquirer"], "FIL Technology (Dalian) Limited")
        self.assertEqual(items[0]["published"], "2026-11-23 18:19")
        self.assertTrue(items[0]["link"].endswith(".zip"))

    def test_bad_feed(self):
        with self.assertRaises(deals.DealsError):
            deals.parse_feed(b"<html>not a feed", "sast")
        self.assertEqual(deals.parse_feed(b"<rss><channel></channel></rss>", "sast")["items"], [])


class InsiderXbrl(unittest.TestCase):
    def test_director_esop(self):
        d = deals.parse_xbrl(raw(RAVINDRA), "2026-05-02 16:46")
        self.assertEqual((d["symbol"], d["company"], d["regulation"]), ("RELTD", "Ravindra Energy Limited", "7(2)"))
        self.assertEqual(d["isin"], "INE206N01018")
        self.assertEqual(len(d["rows"]), 1)
        r = d["rows"][0]
        self.assertEqual((r["person"], r["category"], r["type"], r["mode"]),
                         ("Shantanu Lath", "Director", "Buy", "ESOP"))
        self.assertEqual((r["quantity"], r["value"], r["per_share"]), (70000, 7000000.0, 100.0))
        self.assertEqual((r["before_qty"], r["before_pct"], r["after_qty"], r["after_pct"]),
                         (50000, 0.03, 120000, 0.07))
        self.assertEqual((r["from"], r["to"], r["intimated"]), ("2026-04-29", "2026-04-29", "2026-05-02"))
        self.assertEqual((r["days_to_tell_company"], r["days_to_publish"]), (3, 3))
        self.assertIn("not a market purchase", r["plain"])
        lines = deals.describe_insider(r)
        self.assertIn("Shantanu Lath (Director) bought 70,000 Equity Shares", lines[0])
        self.assertIn("value as filed ₹70,00,000 (₹70.00 lakh)", lines[0])
        self.assertIn("Holding 50,000 (0.03%) -> 120,000 (0.07%)", lines[1])
        self.assertIn("on the exchange 3 days after the trade", lines[1])

    def test_promoter_pledge(self):
        r = deals.parse_xbrl(raw(MANGALAM), "2026-05-02 14:20")["rows"][0]
        self.assertEqual((r["category"], r["type"], r["mode"]), ("Promoters", "Pledge", "Pledge Creation"))
        self.assertEqual(r["quantity"], 2212300)
        self.assertEqual((r["from"], r["to"]), ("2026-04-28", "2026-04-29"))
        self.assertEqual(r["days_to_publish"], 3)             # counted from the last trade date
        self.assertEqual(r["plain"], "shares pledged as security for a loan, not a sale in the market")
        line = deals.describe_insider(r)
        self.assertIn("pledged 2,212,300", line[0])
        self.assertIn("Traded 2026-04-28 to 2026-04-29", line[1])

    def test_pledge_release_and_a_missing_category(self):
        r = deals.parse_xbrl(raw(RESPONSIVE), "2026-04-30 17:55")["rows"][0]
        self.assertEqual((r["type"], r["mode"]), ("Pledge Revoke", "Revokation of Pledge"))   # as filed
        self.assertEqual(r["plain"], "a pledge being released, not a purchase in the market")
        self.assertEqual((r["before_qty"], r["after_qty"]), (24650000, 24650000))
        self.assertIn("Fairpoint Tradecom LLP (Promoter Group) had a pledge released on 300,000 Equity Shares",
                      deals.describe_insider(r)[0])
        rows = deals.parse_xbrl(raw(HCL_TRUST), "2026-04-30 18:31")["rows"]
        self.assertEqual(len(rows), 4)
        self.assertIsNone(rows[0]["category"])                # the file leaves CategoryOfPerson out
        self.assertIn("HCL Technologies Stock Options Trust (category not given) sold 70,395",
                      deals.describe_insider(rows[0])[0])

    def test_two_rows_promoter_and_promoter_group(self):
        d = deals.parse_xbrl(raw(BAJAJ), "2026-04-30 22:26")
        self.assertEqual(d["symbol"], "BAJAJFINSV")
        buy, sell = d["rows"]
        self.assertEqual(buy["person"], "Bajaj Holdings & Investment Limited")      # &amp; decoded
        self.assertEqual((buy["category"], buy["type"], buy["mode"], buy["exchange"]),
                         ("Promoters", "Buy", "Market Purchase", "BSE"))
        self.assertEqual((sell["category"], sell["type"], sell["after_qty"]), ("Promoter Group", "Sell", 0))
        self.assertEqual(buy["value"], 3700224520.0)
        self.assertEqual(buy["value_crore"], 370.0225)
        self.assertEqual(buy["plain"], "a trade in the market")
        self.assertIn("(₹370.02 crore)", deals.describe_insider(buy)[0])
        self.assertIn("-> 0 (0%)", deals.describe_insider(sell)[1])

    def test_type_and_mode_that_disagree_are_quoted_not_fixed(self):
        rows = deals.parse_xbrl(raw(HCL), "2026-04-30 19:23")["rows"]
        self.assertEqual([(r["type"], r["mode"]) for r in rows], [("Buy", "Market Sale")] * 2)
        self.assertIn("disagree", rows[0]["plain"])
        self.assertEqual([r["days_to_publish"] for r in rows], [6, 1])
        self.assertIsNone(rows[0]["value_aggregate"])         # this file leaves that tag out
        self.assertEqual(rows[0]["value"], 122655032.0)

    def test_off_market_and_esop_sale(self):
        rows = deals.parse_xbrl(raw(ECLERX), "2026-04-30 14:05")["rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["type"], rows[0]["mode"]), ("Sell", "Off Market"))
        self.assertIn("off-market", rows[0]["plain"])
        sangam = deals.parse_xbrl(raw(SANGAM), "2026-04-30 15:53")["rows"][0]
        self.assertEqual((sangam["type"], sangam["mode"]), ("Sell", "ESOP"))
        self.assertIn("not an ordinary market sale", sangam["plain"])

    def test_tolerant_of_another_prefix_and_missing_tags(self):
        other = text(RAVINDRA).replace("in-capmkt:", "x:").replace("xmlns:in-capmkt=", "xmlns:x=")
        d = deals.parse_xbrl(other.encode("utf-8"), "2026-05-02 16:46")
        self.assertEqual(d, deals.parse_xbrl(raw(RAVINDRA), "2026-05-02 16:46"))
        thin = (b'<x><NameOfThePerson contextRef="a">Someone</NameOfThePerson>'
                b'<SecuritiesAcquiredOrDisposedTransactionType contextRef="a">Sell</SecuritiesAcquiredOrDisposedTransactionType></x>')
        row = deals.parse_xbrl(thin)["rows"][0]
        self.assertEqual((row["person"], row["type"], row["quantity"], row["value"]), ("Someone", "Sell", None, None))
        self.assertIsNone(row["days_to_publish"])
        self.assertIn("Someone (category not given) sold", deals.describe_insider(row)[0])
        with self.assertRaises(deals.DealsError):
            deals.parse_xbrl(b"%PDF-1.4 not xml")

    def test_insider_command(self):
        code, out, _ = run(["insider"])
        self.assertEqual(code, 0)
        self.assertIn("12 filings", out)
        self.assertIn("Ravindra Energy Limited (RELTD), on the exchange 2026-05-02 16:46 IST", out)
        self.assertIn("206 days old. STALE", out)
        self.assertIn("detail not read", out)                 # three XBRL files are not in the fixtures
        self.assertIn("Lloyds Luxuries Limited (no NSE symbol matched)", out)
        self.assertIn("no inside information", out)
        self.assertNotIn(chr(0x2014), out)
        code, out, _ = run(["insider", "--symbol", "BAJAJFINSV", "--json"])
        got = json.loads(out)
        self.assertEqual([i["symbol"] for i in got["items"]["insider"]], ["BAJAJFINSV"])
        self.assertEqual(len(got["items"]["insider"][0]["detail"]["rows"]), 2)
        self.assertTrue(got["freshness"][0]["stale"])
        self.assertEqual(got["freshness"][0]["age_days"], 206)
        self.assertEqual(got["checked"], "2026-11-24 02:30 IST")

    def test_sast_command(self):
        code, out, _ = run(["sast"])
        self.assertEqual(code, 0)
        self.assertIn("Kfin Technologies Limited (KFINTECH), on the exchange 2026-11-23 18:15 IST", out)
        self.assertIn("Wasatch Advisors LP", out)
        self.assertIn("newest item 2026-11-23 18:19 IST, 1 day old.", out)
        self.assertNotIn("STALE", out)
        self.assertIn("does not open them", out)


class Freshness(unittest.TestCase):
    def test_stale_and_fresh(self):
        now = deals.parse_now(NOW)
        f = deals.freshness("insider", "2026-05-02 16:46", now, 12, "Tue, 24 Nov 2026 01:50:07 +0530")
        self.assertEqual((f["age_days"], f["stale"]), (206, True))
        self.assertIn("rebuilt Tue, 24 Nov 2026", f["note"])
        self.assertFalse(deals.freshness("sast", "2026-11-23 18:19", now, 19)["stale"])
        self.assertFalse(deals.freshness("bulk", "2026-11-23", now, 89)["stale"])
        old = deals.freshness("bulk", "2026-11-20", now, 10)
        self.assertTrue(old["stale"])
        self.assertIn("OLDER than the last completed session (2026-11-23)", old["note"])
        self.assertIn("no rows", deals.freshness("block", None, now, 0)["note"])


class NewSinceLastCheck(unittest.TestCase):
    def test_alerts_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run(["new"], tmp)
            self.assertEqual(code, 0)
            # 89 bulk rows + 5 block rows + 19 takeover filings; the 12 insider items are from May
            self.assertIn("113 new items (first check), checked 2026-11-24 02:30 IST.", out)
            self.assertIn("BULK DEALS, session 2026-11-23: 89 new rows in 25 stocks", out)
            self.assertIn("19 new filings", out)
            self.assertNotIn("Ravindra", out)
            self.assertIn("12 older items (more than 7 days old) recorded on this first check without an alert", out)
            self.assertIn("STALE", out)
            with open(os.path.join(tmp, "state", "insider.json"), encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertEqual(saved["last_check"], "2026-11-24 02:30 IST")
            self.assertIn(RAVINDRA, saved["seen"])
            self.assertEqual(len(saved["seen"]), 89 + 5 + 12 + 19)
            code, out, _ = run(["new"], tmp)
            self.assertEqual(code, 1)
            self.assertIn("Nothing new.", out)
            self.assertIn("since the last check (2026-11-24 02:30 IST)", out)

    def test_a_new_id_is_new_and_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            run(["new"], tmp)
            path = os.path.join(tmp, "state", "insider.json")
            with open(path, encoding="utf-8") as fh:
                state = json.load(fh)
            del state["seen"][BAJAJ]
            del state["seen"]["Dis_11232026181546_54163.zip"]
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            code, out, _ = run(["new"], tmp)
            self.assertEqual(code, 0)
            self.assertIn("2 new items since the last check", out)
            # after the first check an unseen filing alerts however old it is, with its detail
            self.assertIn("Bajaj Holdings & Investment Limited (Promoters) bought 2,090,050", out)
            self.assertIn("Kfin Technologies Limited (KFINTECH)", out)
            self.assertNotIn("Samhi", out)
            self.assertEqual(run(["new"], tmp)[0], 1)

    def test_new_since_function(self):
        now = deals.parse_now(NOW)
        items = [{"id": "a", "published": "2026-11-23 18:19"}, {"id": "b", "published": "2026-05-02 16:46"},
                 {"id": "c", "date": "2026-11-23"}]
        fresh, held = deals.new_since(items, {"seen": {}, "last_check": None}, now)
        self.assertEqual(([i["id"] for i in fresh], [i["id"] for i in held]), (["a", "c"], ["b"]))
        fresh, held = deals.new_since(items, {"seen": {"a": "2026-11-24"}, "last_check": "x"}, now)
        self.assertEqual(([i["id"] for i in fresh], held), (["b", "c"], []))

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, _ = run(["new", "--dry-run"], tmp)
            self.assertEqual(code, 0)
            self.assertFalse(os.path.exists(os.path.join(tmp, "state")))
            self.assertEqual(run(["new", "--dry-run"], tmp)[0], 0)        # still new: nothing recorded

    def test_old_ids_are_forgotten(self):
        state = {"seen": {"old": "2025-01-01", "kept": "2026-11-01"}, "last_check": "x"}
        deals.remember([{"id": "fresh"}], state, deals.parse_now(NOW))
        self.assertEqual(sorted(state["seen"]), ["fresh", "kept"])
        self.assertEqual(state["last_check"], "2026-11-24 02:30 IST")

    def test_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run(["new", "--json", "--min-crore", "100"], tmp)
            got = json.loads(out)
            self.assertEqual(code, 0)
            self.assertEqual(len(got["items"]["bulk"]), 3)
            self.assertEqual(got["items"]["insider"], [])
            self.assertEqual(got["held_back_as_old"], 12)
            self.assertEqual(got["deals_by_stock"]["block"][0]["symbol"], "LENSKART")
            self.assertEqual([f["source"] for f in got["freshness"]], ["bulk", "block", "insider", "sast"])
            self.assertIn("no inside information", got["note"])

    def test_a_missing_source_is_reported_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as off:
            for name in ("bulk.csv", "Sast_Regulation29.xml"):
                with open(os.path.join(off, name), "wb") as fh:
                    fh.write(raw(name))
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = deals.main(["new", "--offline", off, "--now", NOW, "--state-dir", tmp,
                                   "--universe", os.path.join(tmp, "u.txt"),
                                   "--portfolio", os.path.join(tmp, "p.json"),
                                   "--watchlist", os.path.join(tmp, "w.json"),
                                   "--plan", os.path.join(tmp, "pl.json")])
            self.assertEqual(code, 0)
            self.assertIn("COULD NOT READ block deals", out.getvalue())
            self.assertIn("COULD NOT READ insider trading disclosures", out.getvalue())
            self.assertIn("Samhi Hotels Limited (no NSE symbol matched)", out.getvalue())   # no name list either
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as off:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = deals.main(["bulk", "--offline", off, "--state-dir", tmp])
            self.assertEqual(code, 2)
            self.assertIn("offline: no file", err.getvalue())


class Holdings(unittest.TestCase):
    def files(self, tmp):
        return ["--portfolio", write_json(tmp, "portfolio.json", {"positions": [{"ticker": "LENSKART", "qty": 10}]}),
                "--watchlist", write_json(tmp, "watchlist.json", {"hits": [{"symbol": "KFINTECH.NS"}]}),
                "--plan", write_json(tmp, "plan.json", {"symbol": "HCLTECH", "entry": 1200})]

    def test_watch_from_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = self.files(tmp)
            watch = deals.watch_from_files(f[1], f[3], f[5])
            self.assertEqual(watch, {"LENSKART": "in your holdings", "KFINTECH": "on your watchlist",
                                     "HCLTECH": "in your trade plan"})
            self.assertEqual(deals.watch_from_files(os.path.join(tmp, "x"), os.path.join(tmp, "y"),
                                                    os.path.join(tmp, "z")), {})

    def test_items_in_your_stocks_are_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = self.files(tmp)
            code, out, _ = run(["new"] + f, tmp)
            self.assertEqual(code, 0)
            self.assertIn("LENSKART  Lenskart Solutions Ltd  <- LENSKART is in your holdings", out)
            self.assertIn("<- KFINTECH is on your watchlist", out)
            self.assertNotIn("<- SAMHI", out)
            code, out, _ = run(["insider"] + f, tmp)
            self.assertIn("HCL Technologies Limited (HCLTECH), on the exchange 2026-04-30 19:23 IST  "
                          "<- HCLTECH is in your trade plan", out)

    def test_holdings_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = self.files(tmp)
            code, out, _ = run(["new", "--holdings", "--json"] + f, tmp)
            got = json.loads(out)
            self.assertEqual(code, 0)
            self.assertEqual({r["symbol"] for r in got["items"]["bulk"] + got["items"]["block"]}, {"LENSKART"})
            self.assertEqual([i["symbol"] for i in got["items"]["sast"]], ["KFINTECH"])
            # everything fetched is recorded, shown or not, so the second check is quiet
            self.assertEqual(run(["new", "--holdings"] + f, tmp)[0], 1)

    def test_holdings_with_no_files_is_an_error(self):
        code, _, err = run(["bulk", "--holdings"])
        self.assertEqual(code, 2)
        self.assertIn("--holdings: no symbols found", err)


class Network(unittest.TestCase):
    def test_only_the_nse_archive_host_is_ever_fetched(self):
        f = deals.Fetcher()
        with self.assertRaises(deals.DealsError):
            f._download("https://example.com/corporate/xbrl/x.xml")
        with self.assertRaises(deals.DealsError):
            f._download("https://www.nseindia.com/api/corporates-pit")
        self.assertTrue(all(u.startswith("https://nsearchives.nseindia.com/") for u in deals.URLS.values()))
        self.assertEqual(deals.UA, "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)")

    def test_each_file_is_read_once_per_run(self):
        f = deals.Fetcher(DIR)
        first = f.get("bulk")
        f.offline = os.path.join(DIR, "nowhere")
        self.assertIs(f.get("bulk"), first)


if __name__ == "__main__":
    unittest.main()
