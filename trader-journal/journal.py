#!/usr/bin/env python3
"""Practice Journal and charges calculator for trader-journal (India, NSE). Standard library only.

Writes every practice (or real) trade down, scores it against what the price
really did, and takes the charges off, so you see honest results over weeks
before you risk money. It never places an order and reads and writes only
files under trader/.

    python journal.py add                      # from trader/plan.json + trader/orders.json
    python journal.py add --symbol TCS --date 2026-11-23 --entry 2443.50 --entry-time "9:45 AM" \\
        --stop 2419.35 --target 2467.65 --qty 15 --flat-by 15:00
    python journal.py import                   # every entry trader-broker logged as placed
    python journal.py score                    # every open trade whose session is over
    python journal.py score --symbol TCS --date 2026-11-23 --exit-price 2465.00   # a fill you report
    python journal.py report                   # the scoreboard (--since DATE, --json)
    python journal.py charges --buy 2443.50 --sell 2467.65 --qty 15
    python journal.py charges --plan           # the current plan: target hit, stop hit, break-even
    python journal.py list

How a trade is scored (simulated):
  1. One request for the day's 5-minute bars: Yahoo v8 chart for <SYMBOL>.NS
     (range 5d, 1mo or 60d by how far back the day is; Yahoo keeps 5-minute
     bars for about 60 days). --offline DIR reads saved chart files instead.
  2. Walk the bars AFTER the entry bar, in order. entry_time is the close of
     the entry bar (9:45 AM = the 09:40 bar), so the walk starts at the 09:45
     bar. In each bar the stop is checked before the target:
       low <= stop      out at the stop       (outcome "stop")
       high >= target   out at the target     (outcome "target")
     A bar that touches both counts as the stop: the pack's conservative rule.
  3. Neither by flat_by: out at the CLOSE of the last completed bar before
     flat_by (flat_by 15:00 = the close of the 14:55 bar). Outcome "flat".
  4. A bar missing anywhere before the exit means the trade stays open, with
     the reason: nothing is guessed across a hole in the data.
  5. An entry time that is not on the 5-minute grid (9:47, from the broker
     log) starts in the bar that holds it; in that one bar only the stop
     counts, because part of the bar happened before the entry.
A fill you report (--exit-price, or a sell fill in trader/state/broker.json)
is used instead and the trade is marked "as filled", not "simulated".

R = (exit - entry) / (entry - stop), before charges. Net P&L = gross P&L
minus the charges of one intraday round trip (the CHARGES table below, or
the "charges" block in trader/config.json). Each charge line is rounded half
up to the paisa; a contract note can differ by a few paise because the
exchange and the broker round in their own way (STT, for one, to the rupee).

Simulated means: what a stop and a target at those prices would have done on
Yahoo's 5-minute bars; a real fill can be worse (a gap, a fast market, a
stop that slips). Yahoo's NSE bars can lag and have holes, most often from
15:15 to 15:25, which is why flat_by defaults to 15:00. Times are India
Standard Time (a fixed UTC+5:30, never the computer's zone); --now
"2026-11-23 16:00" pins the clock. Long side only. Most people who day trade
lose money. Education and tooling, not financial advice.
"""

import argparse
import datetime
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

UA = "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)"
PAUSE = 0.5          # seconds between requests: be polite to free endpoints
BACKOFF = 5.0        # seconds to wait once after a 429
HOSTS = ("query2", "query1")
INTERVAL = 300       # 5-minute bars, in seconds
SESSION_BARS = 75    # 09:15 to 15:30 IST in 5-minute bars
OPEN_MIN = 9 * 60 + 15
CLOSE_MIN = 15 * 60 + 30
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
EPOCH = datetime.datetime(1970, 1, 1)
RUPEE = "₹"
CENT = Decimal("0.01")
MAX_DAYS_BACK = 58   # Yahoo keeps 5-minute bars for about 60 days
FEW_TRADES = 20      # under this many scored trades the scoreboard says it proves nothing
DEFAULT_FLAT_BY = "15:00"

# Charges for ONE equity INTRADAY round trip on NSE.
# Source: Groww's pricing page, groww.in/pricing, read 2026-11-24. Percentages are percent of the
# order value (0.025 means 0.025 %). Users of another broker override any line with a "charges"
# block in trader/config.json, e.g. {"charges": {"brokerage_flat": 0, "brokerage_min": 0}}.
CHARGES = {
    "brokerage_flat": "20",         # rupees per executed order ...
    "brokerage_pct": "0.1",         # ... or this percent of the order value, whichever is LOWER
    "brokerage_min": "5",           # minimum brokerage per executed order, rupees
    "stt_sell_pct": "0.025",        # Securities Transaction Tax, on the SELL side only
    "stamp_buy_pct": "0.003",       # stamp duty, on the BUY side only
    "exchange_pct": "0.00307",      # NSE exchange transaction charge, both sides
    "sebi_pct": "0.0001",           # SEBI turnover charge, both sides
    "ipft_pct": "0.0001",           # NSE Investor Protection Fund Trust charge, both sides
    "gst_pct": "18",                # GST on brokerage + exchange + SEBI + IPFT
    "dp_charge": "0",               # DP charges: none for intraday
    "auto_squareoff_fee": "50",     # per position the BROKER closes for you, plus GST; only when it does
}
CHARGES_SOURCE = "Groww's pricing page (groww.in/pricing), read 2026-11-24, equity intraday on NSE"

NSE_HOLIDAYS = {  # NSE circular of 12 Dec 2025; weekdays cross-checked against NIFTY trading days
    "2026-01-15": "Municipal Corporation Election - Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Guru Nanak Jayanti",
    "2026-12-25": "Christmas",
}
CALENDAR_YEARS = (2026,)   # NSE had not published 2027 as of 2026-11-24

# trader-broker's order fingerprint (same fields as trader-broker/ticket.py and trader-risk/check.py)
ORDER_FIELDS = ("symbol", "side", "qty", "notional", "type", "time_in_force", "limit_price",
                "stop_price", "trail_price", "trail_percent", "extended_hours", "client_order_id",
                "order_class", "take_profit_limit_price", "stop_loss_stop_price",
                "stop_loss_limit_price", "advanced_instructions")

SIMULATED_NOTE = ("Simulated = what a stop and a target at those prices would have done on Yahoo's 5-minute "
                  "bars; a real fill can be worse.")
FINE_PRINT = ("Numbers only, no prediction. Most people who day trade lose money. Education and tooling, "
              "not financial advice.")


class JournalError(Exception):
    pass


# ---------------------------------------------------------------- time (India: a fixed UTC+5:30)

def to_ist(utc):
    """Naive UTC -> India Standard Time, a fixed UTC+5:30 with no daylight saving."""
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    return local.replace(tzinfo=None) - IST_OFFSET


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset (or ending in " IST") is India time."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    t = t.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise JournalError("--now wants a time like \"2026-11-23 16:00\" (India time) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def trading_day(d):
    """(is_trading_day, reason). Weekends and the NSE holiday list (2026 only)."""
    if d.weekday() >= 5:
        return False, "weekend"
    name = NSE_HOLIDAYS.get(d.isoformat())
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def utc_of(ts):
    return EPOCH + datetime.timedelta(seconds=int(ts))


def parse_clock(text, what):
    """'9:45 AM', '9:45am', '15:00' or '3:00' (a bare 1 to 8 o'clock means p.m.) -> minutes after midnight."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*(?:([AaPp])\.?\s*[Mm]\.?)?\s*$", str(text or ""))
    if not m:
        raise JournalError("%s wants an India time like 9:45 AM or 15:00, not %r" % (what, text))
    h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if mi > 59 or h > 23 or (ap and not 1 <= h <= 12):
        raise JournalError("%s %r is not a clock time" % (what, text))
    if ap == "p" and h != 12:
        h += 12
    elif ap == "a" and h == 12:
        h = 0
    elif not ap and 1 <= h <= 8:
        h += 12
    return h * 60 + mi


def clock24(minutes):
    return "%02d:%02d" % divmod(minutes, 60)


def parse_date(text, what="--date"):
    try:
        return datetime.date.fromisoformat(str(text or "").strip())
    except ValueError:
        raise JournalError("%s wants a date like 2026-11-23, not %r" % (what, text))


# ---------------------------------------------------------------- symbols

SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}(\.BO)?$")


def yahoo_symbol(sym):
    """NSE symbol -> Yahoo symbol: RELIANCE -> RELIANCE.NS, M&M -> M&M.NS. .NS / .BO and ^ are kept."""
    s = sym.strip().upper()
    return s if s.endswith((".NS", ".BO")) or s.startswith("^") else s + ".NS"


def nse_symbol(sym):
    """What files and output show: TCS.NS -> TCS."""
    s = str(sym or "").strip().upper()
    return s[:-3] if s.endswith(".NS") else s


def clean_symbol(sym):
    s = nse_symbol(sym)
    if not SYMBOL_RE.match(s):
        raise JournalError("%r is not an NSE equity symbol (like RELIANCE, M&M or BAJAJ-AUTO)" % (sym,))
    return s


def chart_url(sym, rng="5d", host="query2"):
    return "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=5m" % (
        host, urllib.parse.quote(yahoo_symbol(sym), safe=""), rng)


# ---------------------------------------------------------------- money (Decimal rupees, to the paisa)

def D(x, what="a number"):
    try:
        if isinstance(x, Decimal):
            return x
        if isinstance(x, bool):
            raise ValueError
        if isinstance(x, float):
            return Decimal(repr(x))
        return Decimal(str(x).strip().lstrip(RUPEE).replace(",", ""))
    except (InvalidOperation, ValueError):
        raise JournalError("%s is not a number: %r" % (what, x))


def paise(x):
    """Round half up to the paisa."""
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def money(x):
    """362.25 -> '₹362.25', -422.43 -> '-₹422.43'."""
    d = paise(x)
    return "%s%s%s" % ("-" if d < 0 else "", RUPEE, format(abs(d), ",.2f"))


def signed(x):
    """+₹301.87 / -₹422.43 / ₹0.00."""
    d = paise(x)
    return ("+" if d > 0 else "") + money(d)


def pct_text(d):
    """Decimal('0.025') -> '0.025 %'."""
    s = format(D(d).normalize(), "f")
    return s + " %"


def _f(d):
    return float(paise(d))


# ---------------------------------------------------------------- charges

def load_charges(cfg=None):
    """The CHARGES table as Decimals, with the config's "charges" block laid over it.
    Returns (rates, overridden_keys)."""
    rates = {k: Decimal(v) for k, v in CHARGES.items()}
    over = (cfg or {}).get("charges")
    changed = []
    if over is None:
        return rates, changed
    if not isinstance(over, dict):
        raise JournalError("\"charges\" in the config must be an object like {\"brokerage_flat\": 0}")
    for k, v in over.items():
        if k not in rates:
            raise JournalError("unknown charge %r in the config; the lines are: %s" % (k, ", ".join(CHARGES)))
        d = D(v, "charges.%s" % k)
        if d < 0 or not d.is_finite():
            raise JournalError("charges.%s must be 0 or more, got %r" % (k, v))
        if d != rates[k]:
            changed.append(k)
        rates[k] = d
    return rates, changed


def order_brokerage(value, rates):
    """Brokerage for one executed order: the flat fee or the percent, whichever is lower, then the minimum."""
    value = D(value)
    if value <= 0:
        return Decimal("0.00")
    b = min(rates["brokerage_flat"], value * rates["brokerage_pct"] / 100)
    return paise(max(b, rates["brokerage_min"]))


def round_trip(buy, sell, qty, rates=None, auto_squareoff=False, sells=None):
    """Charges for one intraday round trip: buy qty at buy, sell them at sell. All Decimal rupees.

    sells   optional [(price, qty), ...] when the exit took more than one sell order: each one
            pays its own brokerage. Default: one sell order for the whole quantity."""
    if rates is None:
        rates = load_charges()[0]
    q = int(qty)
    if q <= 0:
        raise JournalError("the quantity must be a whole number of shares above 0")
    buy = paise(buy)
    if buy <= 0:
        raise JournalError("the buy price must be above %s0" % RUPEE)
    legs = [(paise(p), int(n)) for p, n in (sells or [(sell, q)])]
    if any(p <= 0 or n <= 0 for p, n in legs) or sum(n for _, n in legs) != q:
        raise JournalError("the sell orders must be at a price above %s0 and add up to %d shares" % (RUPEE, q))
    buy_value = buy * q
    sell_value = sum(p * n for p, n in legs)
    turnover = buy_value + sell_value
    brok_buy = order_brokerage(buy_value, rates)
    brok_sell = sum((order_brokerage(p * n, rates) for p, n in legs), Decimal("0.00"))
    brokerage = brok_buy + brok_sell
    stt = paise(sell_value * rates["stt_sell_pct"] / 100)
    stamp = paise(buy_value * rates["stamp_buy_pct"] / 100)
    exchange = paise(turnover * rates["exchange_pct"] / 100)
    sebi = paise(turnover * rates["sebi_pct"] / 100)
    ipft = paise(turnover * rates["ipft_pct"] / 100)
    squareoff = paise(rates["auto_squareoff_fee"]) if auto_squareoff else Decimal("0.00")
    gst_base = brokerage + exchange + sebi + ipft + squareoff
    gst = paise(gst_base * rates["gst_pct"] / 100)
    dp = paise(rates["dp_charge"])
    total = brokerage + stt + stamp + exchange + sebi + ipft + gst + dp + squareoff
    gross = sell_value - buy_value
    return {"qty": q, "buy": buy, "sell": paise(sell_value / q), "buy_value": buy_value,
            "sell_value": sell_value, "turnover": turnover, "sell_orders": len(legs),
            "brokerage_buy": brok_buy, "brokerage_sell": brok_sell, "brokerage": brokerage,
            "stt": stt, "stamp_duty": stamp, "exchange": exchange, "sebi": sebi, "ipft": ipft,
            "auto_squareoff": squareoff, "gst_base": gst_base, "gst": gst, "dp": dp,
            "total": total, "gross": gross, "net": gross - total}


def break_even(buy, qty, rates=None):
    """The lowest sell price (to the paisa) at which the round trip nets 0 or more after charges."""
    if rates is None:
        rates = load_charges()[0]
    lo = int(paise(buy) * 100)
    hi = lo * 2 + 1000000
    while lo < hi:
        mid = (lo + hi) // 2
        if round_trip(buy, Decimal(mid) / 100, qty, rates)["net"] >= 0:
            hi = mid
        else:
            lo = mid + 1
    return Decimal(lo) / 100


CHARGE_LINES = ("brokerage", "stt", "stamp_duty", "exchange", "sebi", "ipft", "gst", "dp", "auto_squareoff",
                "total")


def charges_record(c):
    """The itemised charges as plain floats, for the journal."""
    return {k: _f(c[k]) for k in CHARGE_LINES}


def charge_share(gross, charges):
    """How the charges compare with the gross result, in plain words."""
    gross, charges = paise(gross), paise(charges)
    if gross > 0:
        share = (charges / gross * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        return "charges took %s %% of your gross profit" % share
    if gross < 0:
        return "charges added %s to a gross loss of %s" % (money(charges), money(-gross))
    return "the gross result was %s0.00, so the charges of %s are the whole loss" % (RUPEE, money(charges))


def rates_note(changed):
    if changed:
        return ("Rates: %s, with these lines overridden by the \"charges\" block in the config: %s."
                % (CHARGES_SOURCE, ", ".join(changed)))
    return "Rates: %s." % CHARGES_SOURCE


def charges_report(c, rates, changed=()):
    q = c["qty"]
    lines = ["Charges for one intraday round trip on NSE: buy %d at %s, sell %d at %s"
             % (q, money(c["buy"]), q, money(c["sell"]))]

    def row(label, amount, how=""):
        lines.append(("  %-18s %12s  %s" % (label, money(amount), how)).rstrip())

    row("Buy value", c["buy_value"])
    row("Sell value", c["sell_value"])
    row("Brokerage", c["brokerage"],
        "buy %s + sell %s (%s or %s of the order value, whichever is lower, minimum %s, per order)"
        % (money(c["brokerage_buy"]), money(c["brokerage_sell"]), money(rates["brokerage_flat"]),
           pct_text(rates["brokerage_pct"]), money(rates["brokerage_min"])))
    row("STT", c["stt"], "%s of the sell value" % pct_text(rates["stt_sell_pct"]))
    row("Stamp duty", c["stamp_duty"], "%s of the buy value" % pct_text(rates["stamp_buy_pct"]))
    row("Exchange (NSE)", c["exchange"], "%s of both sides (%s)" % (pct_text(rates["exchange_pct"]),
                                                                   money(c["turnover"])))
    row("SEBI turnover", c["sebi"], "%s of both sides" % pct_text(rates["sebi_pct"]))
    row("IPFT (NSE)", c["ipft"], "%s of both sides" % pct_text(rates["ipft_pct"]))
    if c["auto_squareoff"]:
        row("Auto square-off", c["auto_squareoff"], "the broker closed the position for you")
    row("GST", c["gst"], "%s of brokerage + exchange + SEBI + IPFT%s (%s)"
        % (pct_text(rates["gst_pct"]), " + auto square-off" if c["auto_squareoff"] else "", money(c["gst_base"])))
    row("DP charges", c["dp"], "none for intraday" if not c["dp"] else "")
    row("Total charges", c["total"])
    row("Gross P&L", c["gross"], "(%s - %s) x %d" % (money(c["sell"]), money(c["buy"]), q))
    row("Net P&L", c["net"], charge_share(c["gross"], c["total"]))
    be = break_even(c["buy"], q, rates)
    lines.append("  Break-even         sell at %s or higher: the price has to rise %s a share (%.2f %%) just to "
                 "cover the charges" % (money(be), money(be - c["buy"]), float((be - c["buy"]) / c["buy"] * 100)))
    lines.append(rates_note(changed))
    lines.append("Each line is rounded to the paisa; your contract note can differ by a few paise.")
    return lines


def plan_charges(entry, stop, target, qty, rates, auto_squareoff=False):
    """The plan after charges: the round trip to the target, the round trip to the stop."""
    win = round_trip(entry, target, qty, rates, auto_squareoff)
    loss = round_trip(entry, stop, qty, rates, auto_squareoff)
    be = break_even(entry, qty, rates)
    gross_rr = (win["gross"] / -loss["gross"]) if loss["gross"] < 0 else None
    net_rr = (win["net"] / -loss["net"]) if loss["net"] < 0 else None
    return {"win": win, "loss": loss, "break_even": be, "break_even_move": be - paise(entry),
            "gross_reward_risk": gross_rr, "net_reward_risk": net_rr}


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise JournalError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise JournalError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def load_config(path):
    cfg = read_json(path, "config", required=False)
    if cfg is None:
        return {}
    if not isinstance(cfg, dict):
        raise JournalError("%s is not a config object" % path)
    return cfg


def load_journal(path):
    doc = read_json(path, "journal", required=False)
    if doc is None:
        return {"created_by": "trader-journal", "trades": []}
    if not isinstance(doc, dict) or not isinstance(doc.get("trades"), list):
        raise JournalError("%s is not a journal (no \"trades\" list)" % path)
    return doc


def save_journal(path, doc, now_utc):
    doc["created_by"] = "trader-journal"
    doc["updated_at"] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    doc["note"] = ("A record of practice and real trades and how they ended. Simulated exits are what a stop "
                   "and a target would have done on Yahoo's 5-minute bars; a real fill can be worse. "
                   "Nothing here places an order.")
    write_json(path, doc)


# ---------------------------------------------------------------- the journal's trades

def trade_key(t):
    return (t["symbol"], t["date"], t["entry_time"])


def find_trade(doc, key):
    for t in doc["trades"]:
        if trade_key(t) == key:
            return t
    return None


def make_trade(symbol, date, entry_time, entry, stop, target, qty, flat_by=None, setup="", paper=True,
               source="flags", now_utc=None):
    """Check the facts of one long intraday trade and return the journal record (status open)."""
    sym = clean_symbol(symbol)
    day = parse_date(date, "the date")
    if day.year in CALENDAR_YEARS or day.weekday() >= 5:
        ok, why = trading_day(day)
        if not ok:
            raise JournalError("NSE was closed on %s (%s): no trade to journal" % (day.isoformat(), why))
    e, s, t = paise(D(entry, "the entry")), paise(D(stop, "the stop")), paise(D(target, "the target"))
    if not Decimal(0) < s < e < t:
        raise JournalError("a long trade needs stop < entry < target (got stop %s, entry %s, target %s); "
                           "this pack journals the long side only" % (s, e, t))
    try:
        qf = float(qty)
    except (TypeError, ValueError):
        raise JournalError("the quantity must be a whole number of shares, got %r" % (qty,))
    if qf <= 0 or abs(qf - round(qf)) > 1e-9:
        raise JournalError("the quantity must be a whole number of shares above 0, got %r (NSE has no "
                           "fractional shares)" % (qty,))
    em = parse_clock(entry_time, "the entry time")
    fm = parse_clock(flat_by or DEFAULT_FLAT_BY, "flat_by")
    if not OPEN_MIN <= em < CLOSE_MIN:
        raise JournalError("the entry time %s is outside the NSE session, 09:15 to 15:30 India time" % clock24(em))
    if fm > CLOSE_MIN:
        raise JournalError("flat_by %s is after the 15:30 NSE close" % clock24(fm))
    if em >= fm:
        raise JournalError("the entry at %s is at or after flat_by %s: there is nothing to hold"
                           % (clock24(em), clock24(fm)))
    rec = {"id": "%s-%s-%s" % (sym, day.isoformat(), clock24(em).replace(":", "")),
           "symbol": sym, "date": day.isoformat(), "side": "buy", "entry_time": clock24(em),
           "entry": float(e), "stop": float(s), "target": float(t), "qty": int(round(qf)),
           "flat_by": clock24(fm), "setup": str(setup or ""), "paper": bool(paper),
           "risk_per_share": float(e - s), "source": source, "status": "open"}
    if now_utc is not None:
        rec["added_at"] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    return rec


def describe(t):
    return ("%s %s %s: bought %d at %s at %s, stop %s, target %s, flat by %s"
            % (t["symbol"], t["date"], "practice" if t.get("paper", True) else "real", t["qty"],
               money(t["entry"]), t["entry_time"], money(t["stop"]), money(t["target"]), t["flat_by"]))


def fingerprint(order):
    """Same function as trader-broker/ticket.py: identifies the exact order."""
    core = {k: order[k] for k in ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def orders_of(doc):
    orders = doc.get("orders", []) if isinstance(doc, dict) else doc
    return [o for o in (orders or []) if isinstance(o, dict)]


def trade_from_plan(plan, orders_doc, cfg, over, now_utc):
    """A journal record from trader/plan.json + trader/orders.json. `over` holds the flags
    that replace a value (entry, stop, target, qty, entry_time, flat_by, date, setup, paper)."""
    if not isinstance(plan, dict) or not plan.get("symbol"):
        raise JournalError("the plan has no symbol: run /trader-exit first, or give --symbol and the other flags")
    if str(plan.get("side") or "buy").lower() not in ("buy", "long"):
        raise JournalError("the plan is a %s: this pack journals the long side only" % plan.get("side"))
    sym = clean_symbol(plan["symbol"])
    order = None
    for o in orders_of(orders_doc):
        if nse_symbol(o.get("symbol")) == sym and str(o.get("side", "")).lower() == "buy":
            order = o
    op = (order or {}).get("plan") or {}
    notes = []
    entry = over.get("entry")
    if entry is None:
        entry = plan.get("entry")
        if op.get("entry") is not None and paise(op["entry"]) != paise(entry):
            notes.append("entry %s is the price trader-budget sized the order at; the plan said %s"
                         % (money(op["entry"]), money(entry)))
            entry = op["entry"]
    qty = over.get("qty")
    if qty is None:
        qty = (order or {}).get("qty")
    if qty is None:
        raise JournalError("no quantity: trader/orders.json has no buy order for %s. Run /trader-budget, or "
                           "give --qty" % sym)
    paper = over.get("paper")
    if paper is None:
        if isinstance(orders_doc, dict) and order is not None and "paper" in orders_doc:
            paper = bool(orders_doc["paper"])
        else:
            paper = bool(cfg.get("paper", True))
    entry_time = over.get("entry_time") or plan.get("entry_time") or op.get("entry_time")
    if not entry_time:
        raise JournalError("the plan has no entry_time: give --entry-time, e.g. \"9:45 AM\"")
    rec = make_trade(sym, over.get("date") or plan.get("date") or to_ist(now_utc).date().isoformat(), entry_time,
                     entry, over.get("stop") if over.get("stop") is not None else plan.get("stop"),
                     over.get("target") if over.get("target") is not None else plan.get("target"), qty,
                     over.get("flat_by") or plan.get("flat_by") or op.get("flat_by") or cfg.get("flat_by"),
                     over.get("setup") or plan.get("setup") or op.get("setup") or "", paper, "plan", now_utc)
    return rec, notes


# ---------------------------------------------------------------- import from trader-broker's log

def rec_day(rec):
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


def at_minutes(rec):
    """The India wall-clock minutes of a log record's "at" ("2026-11-23T09:50:00+05:30"), or None."""
    m = re.match(r"^\d{4}-\d{2}-\d{2}T(\d{2}):(\d{2})", str(rec.get("at", "")))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def broker_entries(state, orders_doc, cfg):
    """Every buy entry trader-broker logged as placed -> (journal facts, None) or (None, why skipped).

    The log line holds symbol, date, qty, stop, max_loss and the plain-words ticket (which names
    the target). The entry price is the buy fill reported right after it when there is one, else
    the plan's entry from trader/orders.json when that is still the same order (same fingerprint),
    else stop + max_loss / qty. The entry time is the plan's entry_time when the order matches,
    else the time the placement was reported."""
    rows = [r for r in (state or {}).get("orders", []) if isinstance(r, dict)]
    by_fp = {fingerprint(o): o for o in orders_of(orders_doc)}
    out = []
    for i, r in enumerate(rows):
        if r.get("status") != "placed" or str(r.get("side", "")).lower() != "buy":
            continue
        if r.get("kind") not in (None, "entry"):
            continue
        sym, day = nse_symbol(r.get("symbol")), rec_day(r)
        label = "%s %s" % (sym or "?", day)
        order = by_fp.get(r.get("fingerprint")) or {}
        op = order.get("plan") or {}
        stop = r.get("stop") if r.get("stop") is not None else order.get("stop_loss_stop_price")
        target = order.get("take_profit_limit_price")
        if target is None:
            m = re.search(r"target\s+([0-9][0-9,]*\.?[0-9]*)", str(r.get("plain", "")))
            target = m.group(1) if m else None
        if stop is None or target is None:
            out.append((None, "%s: the log line has no stop or no target, so there is nothing to score" % label))
            continue
        qty, entry, entry_from = r.get("qty"), None, None
        fill = None
        for nxt in rows[i + 1:]:
            if nxt.get("status") == "placed" and nse_symbol(nxt.get("symbol")) == sym:
                break
            if (nxt.get("status") == "filled" and nxt.get("side") == "buy" and rec_day(nxt) == day
                    and nse_symbol(nxt.get("symbol")) == sym):
                fill = nxt
                break
        if fill is not None:
            entry, qty, entry_from = fill.get("price"), fill.get("qty"), "the buy fill you reported"
        elif op.get("entry") is not None:
            entry, entry_from = op["entry"], "the plan in trader/orders.json"
        elif r.get("max_loss") is not None and qty:
            try:
                entry = paise(D(stop) + D(r["max_loss"]) / D(qty))
                entry_from = "the log's stop + max loss / qty"
            except (JournalError, ArithmeticError):
                entry = None
        if entry is None or not qty:
            out.append((None, "%s: the log line has no entry price or quantity" % label))
            continue
        at = at_minutes(r)
        entry_time = op.get("entry_time") or (clock24(at) if at is not None else None)
        if not entry_time:
            out.append((None, "%s: the log line has no time" % label))
            continue
        out.append(({"symbol": sym, "date": day, "entry_time": entry_time, "entry": entry, "stop": stop,
                     "target": target, "qty": qty, "setup": op.get("setup") or "",
                     "flat_by": op.get("flat_by") or cfg.get("flat_by"),
                     "paper": bool(r.get("paper", cfg.get("paper", True))), "entry_from": entry_from,
                     "broker": {"at": r.get("at"), "fingerprint": r.get("fingerprint"),
                                "broker_order_id": r.get("broker_order_id")}}, None))
    return out


def broker_exit(state, trade, journal):
    """Sell fills in trader-broker's log for this trade: [(price, qty), ...], the last fill's
    time, or (None, why). Used only when this is the journal's only trade in that symbol that
    day and the sells add up to exactly the trade's quantity."""
    rows = [r for r in (state or {}).get("orders", []) if isinstance(r, dict)]
    em = parse_clock(trade["entry_time"], "entry_time")
    fills = [r for r in rows if r.get("status") == "filled" and r.get("side") == "sell"
             and nse_symbol(r.get("symbol")) == trade["symbol"] and rec_day(r) == trade["date"]
             and (at_minutes(r) is None or at_minutes(r) >= em)]
    if not fills:
        return None, None, None
    same = [t for t in journal["trades"] if t["symbol"] == trade["symbol"] and t["date"] == trade["date"]]
    if len(same) > 1:
        return None, None, ("the broker log has sell fills for %s that day, but the journal has %d trades in "
                            "it: say which with --entry-time and --exit-price" % (trade["symbol"], len(same)))
    try:
        legs = [(paise(D(r.get("price"))), int(float(r.get("qty")))) for r in fills]
    except (JournalError, TypeError, ValueError):
        return None, None, "a sell fill in the broker log has no price or quantity"
    total = sum(n for _, n in legs)
    if total != trade["qty"]:
        return None, None, ("the broker log has sell fills for %d of the %d shares: not used; report the "
                            "rest, or score it with --exit-price" % (total, trade["qty"]))
    last = at_minutes(fills[-1])
    return legs, (clock24(last) if last is not None else None), None


# ---------------------------------------------------------------- bars

def _sleep(seconds):
    time.sleep(seconds)


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_chart(sym, rng="5d"):
    """One request for the 5-minute bars. query2 first, one retry after a 429, then query1 on 429 or 5xx."""
    last = None
    for n, host in enumerate(HOSTS):
        if n:
            _sleep(PAUSE)
        for attempt in (0, 1):
            try:
                return _get(chart_url(sym, rng, host))
            except urllib.error.HTTPError as e:
                last = e.code
                try:
                    e.close()
                except Exception:
                    pass
                if e.code == 429 and attempt == 0:
                    _sleep(BACKOFF)
                    continue
                if e.code == 429 or e.code >= 500:
                    break
                if e.code == 404:
                    raise JournalError("Yahoo has no chart for %s (HTTP 404): check the NSE symbol" % sym.upper())
                raise JournalError("Yahoo answered HTTP %d for %s" % (e.code, sym.upper()))
            except urllib.error.URLError as e:
                raise JournalError("could not reach Yahoo: %s" % e.reason)
            except OSError as e:
                raise JournalError("could not reach Yahoo: %s" % e)
    if last == 429:
        raise JournalError("Yahoo is rate limiting (HTTP 429). Wait a minute and run it again.")
    raise JournalError("Yahoo answered HTTP %s for %s on both hosts. Try again later." % (last, sym.upper()))


def offline_names(sym, date):
    stems = []
    for s in (sym, yahoo_symbol(sym)):
        stems += ["%s-%s-5m" % (s, date), "%s-60d5m" % s, "%s-5m" % s, s]
    return [st + ext for st in stems for ext in (".json", ".json.gz")]


def read_offline(path, sym, date):
    """A saved Yahoo chart file (.json or .json.gz), or a folder holding one for this symbol:
    <SYM>-<DATE>-5m.json, <SYM>-60d5m.json, <SYM>-5m.json or <SYM>.json (.gz too)."""
    if os.path.isdir(path):
        for name in offline_names(sym, date):
            if os.path.exists(os.path.join(path, name)):
                path = os.path.join(path, name)
                break
        else:
            raise JournalError("no saved chart for %s in %s (looked for %s-%s-5m.json, %s-60d5m.json, "
                               "%s.json)" % (sym, path, sym, date, sym, sym))
    if not os.path.exists(path):
        raise JournalError("no offline chart file %s" % path)
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        raise JournalError("could not read %s: %s" % (path, e))


def chart_result(text, sym):
    try:
        data = json.loads(text)
    except ValueError:
        raise JournalError("Yahoo returned something that is not JSON for %s" % sym.upper())
    chart = data.get("chart") if isinstance(data, dict) else None
    if not isinstance(chart, dict):
        raise JournalError("Yahoo returned something unexpected for %s" % sym.upper())
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise JournalError("Yahoo has no data for %s: %s" % (sym.upper(), err.get("description") or "unknown symbol"))
    return res[0] or {}


def parse_bars(result, now_utc):
    """Yahoo chart result -> {date: {slot: bar}}. Regular-session 5-minute bars only; slot 0 is
    the 09:15 bar. Dropped: bars with a missing price, bars off the 5-minute grid (the live
    tick), bars not complete yet (now < start + 5 min, where now is the earlier of --now and
    the data's own regularMarketTime) and bars outside 09:15 to 15:30 India time. Prices are
    snapped to the paisa (Yahoo sends 2433.70 as 2433.699951171875)."""
    meta = result.get("meta") or {}
    stamps = result.get("timestamp") or []
    q = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    cols = [q.get(k) or [] for k in ("open", "high", "low", "close")]
    as_of = now_utc
    rmt = meta.get("regularMarketTime")
    if isinstance(rmt, (int, float)) and utc_of(rmt) < as_of:
        as_of = utc_of(rmt)
    days = {}
    for i, ts in enumerate(stamps):
        o, h, l, c = [col[i] if i < len(col) else None for col in cols]
        if ts is None or None in (o, h, l, c):
            continue
        ts = int(ts)
        if ts % INTERVAL:
            continue
        start = utc_of(ts)
        local = to_ist(start)
        mins = local.hour * 60 + local.minute - OPEN_MIN
        if mins < 0 or mins >= SESSION_BARS * 5:
            continue
        if start + datetime.timedelta(seconds=INTERVAL) > as_of:
            continue
        days.setdefault(local.date().isoformat(), {})[mins // 5] = {
            "start": local.strftime("%H:%M"), "o": round(o, 2), "h": round(h, 2), "l": round(l, 2),
            "c": round(c, 2)}
    return days


def load_days(text, sym, now_utc):
    result = chart_result(text, sym)
    meta = result.get("meta") or {}
    msym = nse_symbol(meta.get("symbol"))
    if msym and msym != nse_symbol(sym):
        raise JournalError("the chart is for %s, not %s" % (msym, nse_symbol(sym)))
    gran = meta.get("dataGranularity")
    if gran and gran != "5m":
        raise JournalError("the chart has %s bars; the journal scores on 5-minute bars" % gran)
    return parse_bars(result, now_utc)


def range_for(days_back):
    """The Yahoo range that reaches a day this many calendar days back, or None when it is too old."""
    if days_back <= 4:
        return "5d"
    if days_back <= 25:
        return "1mo"
    if days_back <= MAX_DAYS_BACK:
        return "60d"
    return None


# ---------------------------------------------------------------- scoring

def slot_start(slot):
    return clock24(OPEN_MIN + 5 * slot)


def simulate(trade, day_bars):
    """Walk one session's bars after the entry. Returns (result, None) or (None, why it stays open).

    result: {"outcome": target|stop|flat, "exit_price", "exit_time", "notes": [...]}.
    exit_time is the START of the bar that touched the stop or the target ("10:05"), or the
    flat-by boundary for a flat exit (the close of the last completed bar before flat_by)."""
    sym, date = trade["symbol"], trade["date"]
    if not day_bars:
        return None, "Yahoo has no 5-minute bars for %s on %s" % (sym, date)
    entry, stop, target = trade["entry"], trade["stop"], trade["target"]
    em = parse_clock(trade["entry_time"], "entry_time")
    fm = parse_clock(trade.get("flat_by") or DEFAULT_FLAT_BY, "flat_by")
    first = (em - OPEN_MIN) // 5
    partial = (em - OPEN_MIN) % 5 != 0
    last = (fm - OPEN_MIN) // 5 - 1
    notes = []
    if not partial:
        eb = day_bars.get(first - 1)
        if eb and not eb["l"] - 0.01 <= entry <= eb["h"] + 0.01:
            notes.append("the entry %s is outside the %s entry bar's range, %s to %s"
                         % (money(entry), eb["start"], money(eb["l"]), money(eb["h"])))
    else:
        notes.append("the entry time %s is inside the %s bar: only the stop counts in that bar, because part "
                     "of it happened before the entry" % (trade["entry_time"], slot_start(first)))
    for s in range(first, last + 1):
        b = day_bars.get(s)
        if b is None:
            return None, ("Yahoo has no %s bar for %s on %s: a gap before the exit, so it is not scored. "
                          "If you know the real exit, give it with --exit-price" % (slot_start(s), sym, date))
        if b["l"] <= stop:
            if b["h"] >= target:
                notes.append("the %s bar touched both the stop and the target: counted as the stop "
                             "(the pack's conservative rule)" % b["start"])
            if b["o"] < stop:
                notes.append("the %s bar opened at %s, under the stop: a real stop order would have filled "
                             "near there, worse than %s" % (b["start"], money(b["o"]), money(stop)))
            return {"outcome": "stop", "exit_price": stop, "exit_time": b["start"], "notes": notes,
                    "bar": dict(b)}, None
        if b["h"] >= target and not (partial and s == first):
            return {"outcome": "target", "exit_price": target, "exit_time": b["start"], "notes": notes,
                    "bar": dict(b)}, None
    b = day_bars[last]
    return {"outcome": "flat", "exit_price": b["c"], "exit_time": clock24(OPEN_MIN + 5 * (last + 1)),
            "notes": notes, "bar": dict(b)}, None


def settle(trade, exit_price, exit_time, outcome, basis, rates, now_utc, auto_squareoff=False, notes=(),
           sells=None, data=None):
    """Write the result into the trade: R, gross, itemised charges, net."""
    c = round_trip(trade["entry"], exit_price, trade["qty"], rates, auto_squareoff, sells)
    risk = paise(trade["entry"]) - paise(trade["stop"])
    exit_p = c["sell"]
    r = float(((exit_p - paise(trade["entry"])) / risk).quantize(CENT, rounding=ROUND_HALF_UP))
    net_r = float((c["net"] / (risk * trade["qty"])).quantize(CENT, rounding=ROUND_HALF_UP))
    trade["status"] = "scored"
    trade.pop("open_reason", None)
    trade["result"] = {
        "basis": basis, "outcome": outcome, "exit_price": float(exit_p), "exit_time": exit_time,
        "r": r, "net_r": net_r, "gross": _f(c["gross"]), "charges": charges_record(c), "net": _f(c["net"]),
        "auto_squareoff": bool(auto_squareoff), "notes": list(notes), "data": data,
        "scored_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")}
    return trade


def outcome_of_fill(trade, price):
    p = float(paise(price))
    if p >= trade["target"]:
        return "target"
    if p <= trade["stop"]:
        return "stop"
    return "flat"


def result_lines(t):
    """Two or three plain lines for one scored trade."""
    r = t["result"]
    out = [describe(t)]
    head = "%s, %s" % (r["outcome"].upper(), r["basis"])
    if r["basis"] == "simulated":
        if r["outcome"] == "target":
            how = "the %s bar reached the target before any bar touched the stop" % r["exit_time"]
        elif r["outcome"] == "stop":
            how = "the %s bar touched the stop first" % r["exit_time"]
        else:
            how = ("neither the stop nor the target by %s: out at the close of the last bar before it"
                   % r["exit_time"])
    else:
        how = "the exit you reported%s" % ((" at " + r["exit_time"]) if r.get("exit_time") else "")
        if r["outcome"] == "flat":
            how += ", between the stop and the target"
        if r.get("auto_squareoff"):
            how += ", squared off by the broker"
    out.append("  %s: %s." % (head, how))
    out.append("  Out at %s: %+.2fR, gross %s, charges %s, net %s."
               % (money(r["exit_price"]), r["r"], signed(r["gross"]), money(r["charges"]["total"]),
                  signed(r["net"])))
    for n in r.get("notes") or []:
        out.append("  Note: %s." % n)
    return out


# ---------------------------------------------------------------- the scoreboard

def _group(rows, key):
    out = {}
    for t in rows:
        g = out.setdefault(key(t), {"trades": 0, "wins": 0, "losses": 0, "total_r": Decimal(0),
                                    "gross": Decimal(0), "charges": Decimal(0), "net": Decimal(0)})
        r = t["result"]
        g["trades"] += 1
        g["wins"] += 1 if r["net"] > 0 else 0
        g["losses"] += 1 if r["net"] < 0 else 0
        g["total_r"] += D(r["r"])
        g["gross"] += D(r["gross"])
        g["charges"] += D(r["charges"]["total"])
        g["net"] += D(r["net"])
    return {k: {"trades": g["trades"], "wins": g["wins"], "losses": g["losses"],
                "total_r": float(g["total_r"].quantize(CENT)), "gross": _f(g["gross"]),
                "charges": _f(g["charges"]), "net": _f(g["net"])} for k, g in sorted(out.items())}


def scoreboard(trades, since=None, only=None):
    """The aggregates over the scored trades (oldest first). Pure numbers; nothing is predicted.
    A win is a trade whose NET P&L (after charges) is above zero; a loss, below zero."""
    def keep(t):
        if since and t["date"] < since:
            return False
        if only == "practice" and not t.get("paper", True):
            return False
        if only == "real" and t.get("paper", True):
            return False
        return True
    chosen = [t for t in trades if keep(t)]
    rows = sorted((t for t in chosen if t.get("status") == "scored" and t.get("result")),
                  key=lambda t: (t["date"], t["entry_time"], t["symbol"]))
    n = len(rows)
    sb = {"scored": n, "open": sum(1 for t in chosen if t.get("status") != "scored"), "since": since,
          "only": only, "enough": n >= FEW_TRADES, "few_trades_threshold": FEW_TRADES}
    if not n:
        return sb
    res = [t["result"] for t in rows]
    gross = sum((D(r["gross"]) for r in res), Decimal(0))
    charges = sum((D(r["charges"]["total"]) for r in res), Decimal(0))
    net = sum((D(r["net"]) for r in res), Decimal(0))
    total_r = sum((D(r["r"]) for r in res), Decimal(0))
    total_net_r = sum((D(r.get("net_r", 0)) for r in res), Decimal(0))
    wins = sum(1 for r in res if r["net"] > 0)
    losses = sum(1 for r in res if r["net"] < 0)
    run = best = 0
    for r in res:
        run = run + 1 if r["net"] < 0 else 0
        best = max(best, run)
    worst = min(rows, key=lambda t: t["result"]["net"])
    sb.update({
        "from": rows[0]["date"], "to": rows[-1]["date"],
        "practice": sum(1 for t in rows if t.get("paper", True)),
        "real": sum(1 for t in rows if not t.get("paper", True)),
        "simulated": sum(1 for r in res if r["basis"] == "simulated"),
        "as_filled": sum(1 for r in res if r["basis"] != "simulated"),
        "target": sum(1 for r in res if r["outcome"] == "target"),
        "stop": sum(1 for r in res if r["outcome"] == "stop"),
        "flat": sum(1 for r in res if r["outcome"] == "flat"),
        "wins": wins, "losses": losses, "scratch": n - wins - losses,
        "win_rate": round(100.0 * wins / n, 1),
        "avg_r": float((total_r / n).quantize(CENT, rounding=ROUND_HALF_UP)),
        "total_r": float(total_r.quantize(CENT)),
        "avg_net_r": float((total_net_r / n).quantize(CENT, rounding=ROUND_HALF_UP)),
        "total_net_r": float(total_net_r.quantize(CENT)),
        "gross": _f(gross), "charges": _f(charges), "net": _f(net),
        "largest_loss": ({"net": worst["result"]["net"], "symbol": worst["symbol"], "date": worst["date"],
                          "entry_time": worst["entry_time"]} if worst["result"]["net"] < 0 else None),
        "longest_losing_run": best,
        "charges_vs_gross": charge_share(gross, charges),
        "by_setup": _group(rows, lambda t: t.get("setup") or "(no setup)"),
        "by_symbol": _group(rows, lambda t: t["symbol"]),
    })
    return sb


def few_line(n):
    return ("Only %d scored trade%s: too few to conclude anything. Under %d trades a good or a bad run is "
            "mostly luck." % (n, "" if n == 1 else "s", FEW_TRADES))


def scoreboard_lines(sb):
    n = sb["scored"]
    scope = {"practice": " (practice trades only)", "real": " (real trades only)"}.get(sb.get("only"), "")
    if not n:
        lines = ["Practice Journal scoreboard%s: no scored trades%s." % (scope, (" since " + sb["since"])
                                                                         if sb.get("since") else "")]
        if sb["open"]:
            lines.append("%d trade%s still open: run journal.py score once the session is over."
                         % (sb["open"], "" if sb["open"] == 1 else "s"))
        lines.append(few_line(0))
        lines.append(FINE_PRINT)
        return lines
    lines = ["Practice Journal scoreboard%s: %d scored trade%s, %s to %s%s"
             % (scope, n, "" if n == 1 else "s", sb["from"], sb["to"],
                (" (since %s)" % sb["since"]) if sb.get("since") else ""),
             "  %d practice, %d real. Exits: %d simulated, %d as filled. Still open: %d."
             % (sb["practice"], sb["real"], sb["simulated"], sb["as_filled"], sb["open"]),
             "",
             "  Outcomes            %d target, %d stop, %d flat" % (sb["target"], sb["stop"], sb["flat"]),
             "  After charges       %d win%s, %d loss%s%s (a win is a net P&L above zero)"
             % (sb["wins"], "" if sb["wins"] == 1 else "s", sb["losses"], "" if sb["losses"] == 1 else "es",
                (", %d at zero" % sb["scratch"]) if sb["scratch"] else ""),
             "  Win rate            %.1f %%" % sb["win_rate"],
             "  Average R           %+.2fR before charges, %+.2fR after" % (sb["avg_r"], sb["avg_net_r"]),
             "  Total R             %+.2fR before charges, %+.2fR after" % (sb["total_r"], sb["total_net_r"]),
             "  Gross P&L           %s" % signed(sb["gross"]),
             "  Total charges       %s" % money(sb["charges"]),
             "  Net P&L             %s" % signed(sb["net"])]
    ll = sb["largest_loss"]
    lines.append("  Largest loss        %s" % ("%s (%s %s, entry %s)" % (money(ll["net"]), ll["symbol"], ll["date"],
                                                                         ll["entry_time"]) if ll else "none"))
    lines.append("  Longest losing run  %d trade%s in a row" % (sb["longest_losing_run"],
                                                                "" if sb["longest_losing_run"] == 1 else "s"))
    lines.append("  Charges vs gross    %s" % sb["charges_vs_gross"])
    for title, key in (("By setup", "by_setup"), ("By symbol", "by_symbol")):
        lines.append("")
        lines.append("%s:" % title)
        for name, g in sb[key].items():
            lines.append("  %-14s %3d trade%s  %d won, %d lost  %+.2fR  gross %s  charges %s  net %s"
                         % (name, g["trades"], " " if g["trades"] == 1 else "s", g["wins"], g["losses"],
                            g["total_r"], signed(g["gross"]), money(g["charges"]), signed(g["net"])))
    lines.append("")
    if not sb["enough"]:
        lines.append(few_line(n))
    if sb["simulated"]:
        lines.append(SIMULATED_NOTE)
    lines.append(FINE_PRINT)
    return lines


# ---------------------------------------------------------------- commands

def cmd_add(a):
    now = parse_now(a.now)
    cfg = load_config(a.config)
    doc = load_journal(a.journal)
    paper = None
    if a.real:
        paper = False
    elif a.practice:
        paper = True
    notes = []
    if a.symbol:
        missing = [f for f in ("entry", "entry_time", "stop", "target", "qty") if getattr(a, f) is None]
        if missing:
            raise JournalError("with --symbol also give %s" % ", ".join("--" + m.replace("_", "-") for m in missing))
        rec = make_trade(a.symbol, a.date or to_ist(now).date().isoformat(), a.entry_time, a.entry, a.stop,
                         a.target, a.qty, a.flat_by or cfg.get("flat_by"), a.setup or "",
                         bool(cfg.get("paper", True)) if paper is None else paper, "flags", now)
    else:
        plan = read_json(a.plan, "plan (run /trader-exit first, or give --symbol and the other flags)")
        orders = read_json(a.orders, "orders", required=False)
        over = {"entry": a.entry, "stop": a.stop, "target": a.target, "qty": a.qty, "entry_time": a.entry_time,
                "flat_by": a.flat_by, "date": a.date, "setup": a.setup, "paper": paper}
        rec, notes = trade_from_plan(plan, orders, cfg, over, now)
    old = find_trade(doc, trade_key(rec))
    if old is not None:
        print("Already in the journal: %s. Nothing added." % describe(old))
        return 0
    doc["trades"].append(rec)
    save_journal(a.journal, doc, now)
    print("Journaled: %s." % describe(rec))
    for n in notes:
        print("Note: %s." % n)
    print("Wrote %s (%d trade%s). Nothing was placed." % (a.journal, len(doc["trades"]),
                                                         "" if len(doc["trades"]) == 1 else "s"))
    print("Next: journal.py score once the session is over (after 15:30 India time).")
    return 0


def cmd_import(a):
    now = parse_now(a.now)
    cfg = load_config(a.config)
    state = read_json(a.state, "broker log (trader-broker writes it when you say yes to a ticket)")
    orders = read_json(a.orders, "orders", required=False)
    doc = load_journal(a.journal)
    seen = {(t.get("broker") or {}).get("at") for t in doc["trades"] if t.get("broker")}
    added, skipped, dup = [], [], 0
    for facts, why in broker_entries(state, orders, cfg):
        if facts is None:
            skipped.append(why)
            continue
        try:
            rec = make_trade(facts["symbol"], facts["date"], facts["entry_time"], facts["entry"], facts["stop"],
                             facts["target"], facts["qty"], facts["flat_by"], facts["setup"], facts["paper"],
                             "trader-broker log", now)
        except JournalError as e:
            skipped.append("%s %s: %s" % (facts["symbol"], facts["date"], e))
            continue
        if facts["broker"]["at"] in seen or find_trade(doc, trade_key(rec)) is not None:
            dup += 1
            continue
        rec["broker"] = facts["broker"]
        rec["entry_from"] = facts["entry_from"]
        doc["trades"].append(rec)
        seen.add(facts["broker"]["at"])
        added.append(rec)
    if added:
        save_journal(a.journal, doc, now)
    for rec in added:
        print("Imported: %s (entry from %s)." % (describe(rec), rec["entry_from"]))
    for why in skipped:
        print("Skipped: %s." % why)
    print("%d imported, %d already in the journal, %d skipped. %s"
          % (len(added), dup, len(skipped), ("Wrote %s." % a.journal) if added else "Nothing written."))
    return 0


def _pick(doc, a):
    """The trades a score run looks at."""
    sym = clean_symbol(a.symbol) if a.symbol else None
    date = parse_date(a.date).isoformat() if a.date else None
    et = clock24(parse_clock(a.entry_time, "--entry-time")) if a.entry_time else None
    rows = [t for t in doc["trades"] if (not sym or t["symbol"] == sym) and (not date or t["date"] == date)
            and (not et or t["entry_time"] == et)]
    if (sym or date or et) and not rows:
        raise JournalError("no journal trade matches %s" % " ".join(x for x in (sym, date, et) if x))
    return rows


def cmd_score(a):
    now = parse_now(a.now)
    cfg = load_config(a.config)
    rates, changed = load_charges(cfg)
    doc = load_journal(a.journal)
    if not doc["trades"]:
        print("The journal at %s is empty: journal.py add or journal.py import first." % a.journal)
        return 1
    rows = _pick(doc, a)
    reported = a.exit_price is not None
    if reported:
        if len(rows) != 1:
            raise JournalError("--exit-price is for one trade: %d match; narrow it with --symbol, --date and "
                               "--entry-time" % len(rows))
        if paise(D(a.exit_price, "--exit-price")) <= 0:
            raise JournalError("--exit-price must be above %s0" % RUPEE)
    elif a.auto_squareoff or a.exit_time:
        raise JournalError("--auto-squareoff and --exit-time go with --exit-price (the fill your broker gave you)")
    todo = [t for t in rows if reported or a.again or t.get("status") != "scored"]
    if not todo:
        print("Nothing to score: every matching trade is already scored (--again scores them afresh).")
        return 0
    state = read_json(a.state, "broker log", required=False)
    now_ist = to_ist(now)
    today = now_ist.date()
    charts, scored, left = {}, [], []
    fetched = 0
    for t in todo:
        if reported:
            xt = clock24(parse_clock(a.exit_time, "--exit-time")) if a.exit_time else None
            settle(t, a.exit_price, xt, outcome_of_fill(t, a.exit_price), "as filled", rates, now,
                   a.auto_squareoff, data="reported")
            scored.append(t)
            continue
        legs, xt, why_not = broker_exit(state, t, doc) if state else (None, None, None)
        if legs:
            avg = sum(p * n for p, n in legs) / sum(n for _, n in legs)
            settle(t, avg, xt, outcome_of_fill(t, avg), "as filled", rates, now, False,
                   ["the exit is the sell fill in trader-broker's log"], sells=legs, data="broker log")
            scored.append(t)
            continue
        day = datetime.date.fromisoformat(t["date"])
        close = datetime.datetime.combine(day, datetime.time(15, 30))
        reason = None
        if now_ist < close:
            reason = "the session is not over yet (NSE closes at 15:30 India time on %s)" % t["date"]
        elif not a.offline and range_for((today - day).days) is None:
            reason = ("%s is more than %d days back: Yahoo no longer has its 5-minute bars. If you know the "
                      "exit, give it with --exit-price" % (t["date"], MAX_DAYS_BACK))
        if reason is None:
            ck = (t["symbol"], t["date"]) if a.offline else t["symbol"]
            if ck not in charts:
                try:
                    if a.offline:
                        text = read_offline(a.offline, t["symbol"], t["date"])
                    else:
                        back = max((today - datetime.date.fromisoformat(x["date"])).days for x in todo
                                   if x["symbol"] == t["symbol"]
                                   and range_for((today - datetime.date.fromisoformat(x["date"])).days))
                        if fetched:
                            _sleep(PAUSE)
                        fetched += 1
                        text = fetch_chart(t["symbol"], range_for(back))
                    charts[ck] = load_days(text, t["symbol"], now)
                except JournalError as e:
                    charts[ck] = str(e)
            got = charts[ck]
            if isinstance(got, str):
                reason = got
            else:
                res, reason = simulate(t, got.get(t["date"]))
                if res:
                    settle(t, res["exit_price"], res["exit_time"], res["outcome"], "simulated", rates, now, False,
                           res["notes"], data="offline" if a.offline else "yahoo")
                    scored.append(t)
                    continue
        if why_not:
            reason = "%s. Also: %s" % (reason, why_not)
        t["status"] = "open"
        t["open_reason"] = reason
        t.pop("result", None)
        left.append(t)
    save_journal(a.journal, doc, now)
    if a.json:
        print(json.dumps({"scored": scored, "left_open": left}, indent=2, ensure_ascii=False))
        return 0
    for t in scored:
        print("\n".join(result_lines(t)))
    for t in left:
        print(describe(t))
        print("  LEFT OPEN: %s." % t["open_reason"])
    print("%d scored, %d left open. Wrote %s." % (len(scored), len(left), a.journal))
    if any(t["result"]["basis"] == "simulated" for t in scored):
        print(SIMULATED_NOTE)
    if scored:
        print(rates_note(changed))
        print("Next: journal.py report for the scoreboard. Education and tooling, not financial advice.")
    return 0


def cmd_report(a):
    doc = load_journal(a.journal)
    since = parse_date(a.since, "--since").isoformat() if a.since else None
    sb = scoreboard(doc["trades"], since, a.only)
    if a.json:
        print(json.dumps(sb, indent=2, ensure_ascii=False))
    else:
        print("\n".join(scoreboard_lines(sb)))
    return 0


def cmd_charges(a):
    cfg = load_config(a.config)
    rates, changed = load_charges(cfg)
    if a.plan_mode:
        plan = read_json(a.plan, "plan (run /trader-exit first, or use --buy --sell --qty)")
        sym = clean_symbol(plan.get("symbol") or "")
        qty = a.qty
        if qty is None:
            for o in orders_of(read_json(a.orders, "orders", required=False) or {}):
                if nse_symbol(o.get("symbol")) == sym and str(o.get("side", "")).lower() == "buy":
                    qty = o.get("qty")
        if qty is None:
            raise JournalError("no quantity: trader/orders.json has no buy order for %s. Run /trader-budget, "
                               "or give --qty" % sym)
        rec = make_trade(sym, plan.get("date") or "2026-11-23", plan.get("entry_time") or "09:45",
                         a.buy if a.buy is not None else plan.get("entry"), plan.get("stop"), plan.get("target"),
                         qty, plan.get("flat_by"))
        pc = plan_charges(rec["entry"], rec["stop"], rec["target"], rec["qty"], rates, a.auto_squareoff)
        if a.json:
            out = {"symbol": sym, "qty": rec["qty"], "entry": rec["entry"], "stop": rec["stop"],
                   "target": rec["target"],
                   "target_hit": dict(charges_record(pc["win"]), gross=_f(pc["win"]["gross"]),
                                      net=_f(pc["win"]["net"])),
                   "stop_hit": dict(charges_record(pc["loss"]), gross=_f(pc["loss"]["gross"]),
                                    net=_f(pc["loss"]["net"])),
                   "gross_reward_risk": round(float(pc["gross_reward_risk"]), 2),
                   "net_reward_risk": round(float(pc["net_reward_risk"]), 2),
                   "break_even": _f(pc["break_even"]), "break_even_move": _f(pc["break_even_move"]),
                   "overridden": changed}
            print(json.dumps(out, indent=2, ensure_ascii=False))
            return 0
        w, l = pc["win"], pc["loss"]
        print("Charges on the plan: %s, buy %d at %s, stop %s, target %s"
              % (sym, rec["qty"], money(rec["entry"]), money(rec["stop"]), money(rec["target"])))
        print("  If the target hits  gross %s, charges %s, net %s" % (signed(w["gross"]), money(w["total"]),
                                                                     signed(w["net"])))
        print("  If the stop hits    gross %s, charges %s, net %s" % (signed(l["gross"]), money(l["total"]),
                                                                     signed(l["net"])))
        print("  Reward : risk       %.2f before charges, %.2f after charges (net %s : net %s)"
              % (float(pc["gross_reward_risk"]), float(pc["net_reward_risk"]), money(w["net"]), money(-l["net"])))
        print("  Break-even          %s: the price has to rise %s a share (%.2f %%) before the trade makes "
              "anything" % (money(pc["break_even"]), money(pc["break_even_move"]),
                            float(pc["break_even_move"] / paise(rec["entry"]) * 100)))
        if a.auto_squareoff:
            print("  Both include the broker's auto square-off fee of %s + GST."
                  % money(rates["auto_squareoff_fee"]))
        print(rates_note(changed))
        print("A stop can fill worse than its price, which makes the loss bigger. Each line is rounded to "
              "the paisa; your contract note can differ by a few paise. Not financial advice.")
        return 0
    if a.buy is None or a.sell is None or a.qty is None:
        raise JournalError("give --buy PRICE --sell PRICE --qty N, or --plan for the current plan")
    c = round_trip(D(a.buy, "--buy"), D(a.sell, "--sell"), a.qty, rates, a.auto_squareoff)
    if a.json:
        out = {k: (_f(v) if isinstance(v, Decimal) else v) for k, v in c.items()}
        out["break_even"] = _f(break_even(c["buy"], c["qty"], rates))
        out["overridden"] = changed
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    print("\n".join(charges_report(c, rates, changed)))
    return 0


def cmd_list(a):
    doc = load_journal(a.journal)
    if a.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0
    if not doc["trades"]:
        print("The journal at %s is empty. Add a trade: journal.py add" % a.journal)
        return 1
    rows = sorted(doc["trades"], key=lambda t: (t["date"], t["entry_time"], t["symbol"]))
    print("%-10s %-5s %-11s %5s %10s %10s %10s  %-8s %s"
          % ("date", "time", "symbol", "qty", "entry", "stop", "target", "kind", "result"))
    for t in rows:
        r = t.get("result")
        if t.get("status") == "scored" and r:
            res = "%s (%s) out at %s, %+.2fR, net %s" % (r["outcome"], r["basis"], money(r["exit_price"]),
                                                        r["r"], signed(r["net"]))
        else:
            res = "open" + ((": " + t["open_reason"]) if t.get("open_reason") else "")
        print("%-10s %-5s %-11s %5d %10s %10s %10s  %-8s %s"
              % (t["date"], t["entry_time"], t["symbol"], t["qty"], format(t["entry"], ",.2f"),
                 format(t["stop"], ",.2f"), format(t["target"], ",.2f"),
                 "practice" if t.get("paper", True) else "real", res))
    n_open = sum(1 for t in rows if t.get("status") != "scored")
    print("%d trade%s, %d open. Prices in rupees, times India time." % (len(rows), "" if len(rows) == 1 else "s",
                                                                       n_open))
    return 0


def main(argv=None):
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Practice Journal and charges calculator (trader-journal, NSE). "
                                             "It never places an order.")
    sub = ap.add_subparsers(dest="cmd")

    def files(p, plan=False, state=False):
        p.add_argument("--journal", default="trader/journal.json")
        p.add_argument("--config", default="trader/config.json")
        if plan:
            p.add_argument("--plan", default="trader/plan.json", help="the plan from trader-exit")
            p.add_argument("--orders", default="trader/orders.json", help="the sized order from trader-budget")
        if state:
            p.add_argument("--state", default="trader/state/broker.json", help="trader-broker's log")
        p.add_argument("--now", help='pretend it is this India time (IST), e.g. "2026-11-23 16:00"')

    p = sub.add_parser("add", help="write a trade into the journal (from the plan, or from flags)")
    files(p, plan=True)
    p.add_argument("--symbol", help="NSE symbol, e.g. TCS, RELIANCE, M&M (without it: the current plan)")
    p.add_argument("--date", help="the session, YYYY-MM-DD (default: the plan's, or today in India)")
    p.add_argument("--entry", type=float, help="entry price")
    p.add_argument("--entry-time", help='the entry bar\'s close, India time, e.g. "9:45 AM"')
    p.add_argument("--stop", type=float)
    p.add_argument("--target", type=float)
    p.add_argument("--qty", help="whole shares")
    p.add_argument("--flat-by", help="HH:MM India time (default: the plan's, the config's, else 15:00)")
    p.add_argument("--setup", help="a label for the scoreboard, e.g. orb")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--practice", action="store_true", help="a practice trade (the default follows the config)")
    g.add_argument("--real", action="store_true", help="a trade you really placed")

    p = sub.add_parser("import", help="pull every entry trader-broker logged as placed")
    files(p, state=True)
    p.add_argument("--orders", default="trader/orders.json")

    p = sub.add_parser("score", help="score the open trades whose session is over")
    files(p, state=True)
    p.add_argument("--symbol")
    p.add_argument("--date")
    p.add_argument("--entry-time", help="tells two trades in one symbol on one day apart")
    p.add_argument("--exit-price", type=float, help="the real exit you got: used instead of the simulation")
    p.add_argument("--exit-time", help="when that exit filled, India time")
    p.add_argument("--auto-squareoff", action="store_true",
                   help="the broker closed the position for you: adds its fee (with --exit-price)")
    p.add_argument("--again", action="store_true", help="score already-scored trades afresh")
    p.add_argument("--offline", metavar="DIR", help="saved Yahoo chart files instead of the network")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("report", help="the scoreboard")
    files(p)
    p.add_argument("--since", metavar="DATE", help="only trades on or after YYYY-MM-DD")
    p.add_argument("--only", choices=["practice", "real"], help="only practice trades, or only real ones")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("charges", help="the charges of one intraday round trip, or of the current plan")
    p.add_argument("--buy", type=float, help="buy price")
    p.add_argument("--sell", type=float, help="sell price")
    p.add_argument("--qty", help="whole shares")
    p.add_argument("--plan", dest="plan_mode", action="store_true",
                   help="the current plan: the cost if the target hits and if the stop hits")
    p.add_argument("--plan-file", dest="plan", default="trader/plan.json")
    p.add_argument("--orders", default="trader/orders.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--auto-squareoff", action="store_true", help="add the broker's auto square-off fee")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("list", help="show the journal")
    files(p)
    p.add_argument("--json", action="store_true")

    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"add": cmd_add, "import": cmd_import, "score": cmd_score, "report": cmd_report,
                "charges": cmd_charges, "list": cmd_list}[a.cmd](a)
    except JournalError as e:
        print("journal.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
