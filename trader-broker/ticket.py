#!/usr/bin/env python3
"""Order tickets and the decision log for trader-broker (NSE, rupees). Standard library only.

There is no broker connection anywhere in this pack. This tool never talks
to a broker, never logs in to one and never places anything. It prints a
ticket in plain words that YOU type into your own broker app (Groww,
Zerodha, Upstox...) as an Intraday (MIS) order, and it keeps the log.

  show      renders each proposed order as a plain-words ticket: what to
            buy, the stop-loss and target to place right after, the max loss
            at the stop (before charges) and the flat-by time. An order is
            READY only if trader-risk passed THIS exact order today (matched
            by fingerprint) and nobody has decided on it yet.
  approve   records your explicit yes for ONE order, with your reply word for
            word. The yes means "I have decided to place this myself". It
            refuses "yes to all", "sure", "ok", a yes that names a different
            stock or amount, and any order that is not READY.
  decline   records a no.
  result    records what you report back: placed (with the fill, and your
            broker's order id if you want it kept), not-placed, or rejected.
  fill      records a fill in trader/portfolio.json (cash and shares).
  flatten   builds a sell ticket that closes a symbol's open position by
            flat_by. It still goes through trader-risk and your yes.
  log       prints today's decisions.

    python ticket.py show                    # every order, READY or not
    python ticket.py show --index 1 --json   # one ticket as JSON
    python ticket.py approve --index 1 --reply "yes"
    python ticket.py result --index 1 --status placed --filled-qty 15 --filled-price 2444.00
    python ticket.py fill --symbol TCS --side sell --qty 15 --price 2467.65
    python ticket.py flatten --symbol TCS --qty 15
    python ticket.py log

Everything is written to trader/state/broker.json (append only). Dates are
India dates; --now "2026-11-23 09:50" pins the clock (IST). With
"paper": true (the default) a ticket is a practice trade: write it down,
place nothing. Rupee figures are before brokerage, STT and other charges.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

# The pack's internal order record: the fields that identify one proposed order (its fingerprint).
ORDER_FIELDS = ("symbol", "side", "qty", "notional", "type", "time_in_force", "limit_price",
                "stop_price", "trail_price", "trail_percent", "extended_hours", "client_order_id",
                "order_class", "take_profit_limit_price", "stop_loss_stop_price",
                "stop_loss_limit_price", "advanced_instructions")
TIF_WORDS = {"day": "valid today", "ioc": "immediate or cancel (IOC)"}
RESULT_STATUSES = ("placed", "not-placed", "rejected")
YES_WORDS = ("yes", "y")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later",
               "hold", "unless", "if"}
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
SQUARE_OFF = "your broker auto-squares-off intraday positions around 15:10-15:20 and charges for it"


class TicketError(Exception):
    pass


# ---------------------------------------------------------------- time (India: a fixed UTC+5:30)

def to_ist(utc):
    """Naive UTC -> India Standard Time. A fixed UTC+5:30: no daylight saving, no tz files."""
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    return local.replace(tzinfo=None) - IST_OFFSET


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as India time (IST)."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    t = t.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise TicketError("--now wants a time like \"2026-11-23 09:50\" (India time) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def ist_iso(now_utc):
    """naive UTC -> "2026-11-23T09:50:00+05:30" (India wall clock with its offset)."""
    return to_ist(now_utc).replace(microsecond=0).isoformat() + "+05:30"


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TicketError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TicketError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def rec_day(rec):
    """The trading day a decision belongs to: its "date" (the day it was made under), else the day of "at"."""
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


# ---------------------------------------------------------------- the order

def nse_symbol(sym):
    """"tcs.ns" -> "TCS", "M&M" -> "M&M": the NSE symbol, without Yahoo's .NS suffix."""
    s = str(sym or "").strip().upper()
    return s[:-3] if s.endswith(".NS") else s


def fingerprint(order):
    """Same function as trader-risk/check.py: identifies the exact order."""
    core = {k: order[k] for k in ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _str_num(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        f = float(v)
        return str(int(f)) if f == int(f) else repr(f)
    return str(v)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _money(v):
    s = "{:,.2f}".format(float(v))
    return "₹" + (s[:-3] if s.endswith(".00") else s)


def _p(v):
    """2419.35 -> "2,419.35": a price as it is shown."""
    return "{:,.2f}".format(float(v))


def _shares(q):
    q = _str_num(q)
    return "%s share%s" % (q, "" if q == "1" else "s")


def is_bracket(order):
    oc = str(order.get("order_class") or "").lower()
    legs = order.get("take_profit_limit_price") is not None or order.get("stop_loss_stop_price") is not None
    return oc == "bracket" or (not oc and legs)


def max_loss(order):
    """(rupees, stop) lost if the stop fills exactly at the stop, or (None, stop). Before charges."""
    stop = _f(order.get("stop_loss_stop_price")) or _f((order.get("plan") or {}).get("stop"))
    q = _f(order.get("qty"))
    if not stop or not q:
        return None, stop
    plan = order.get("plan") or {}
    if str(order.get("type", "market")) == "limit" and _f(order.get("limit_price")):
        entry = _f(order["limit_price"])
    elif _f(plan.get("max_loss")) is not None:
        return round(_f(plan["max_loss"]), 2), stop
    else:
        entry = _f(plan.get("entry"))
    if not entry:
        return None, stop
    risk = abs(entry - stop)
    return round(q * int(round(risk * 100)) / 100.0, 2), stop


def how_words(order):
    """market order / limit order at 2,440.00 / SL-M order, trigger ... / SL order, trigger ..., limit ..."""
    typ = str(order.get("type", "market"))
    if typ == "limit":
        return "limit order at %s" % _p(order.get("limit_price") or 0)
    if typ == "stop":
        return "SL-M order, trigger %s" % _p(order.get("stop_price") or 0)
    if typ == "stop_limit":
        return "SL order, trigger %s, limit %s" % (_p(order.get("stop_price") or 0),
                                                   _p(order.get("limit_price") or 0))
    return "%s order" % typ.replace("_", " ")


def first_line(order):
    """BUY 15 TCS on NSE, product Intraday (MIS), market order, valid today"""
    tif = TIF_WORDS.get(str(order.get("time_in_force", "day")), order.get("time_in_force"))
    return "%s %s %s on NSE, product Intraday (MIS), %s, %s" % (
        str(order.get("side", "")).upper(), _str_num(order.get("qty")), nse_symbol(order.get("symbol")) or "?",
        how_words(order), tif)


def loss_line(order):
    loss, stop = max_loss(order)
    if loss is None:
        return None
    return ("max loss about %s if the stop fills at %s (before charges; it can fill worse in a fast market)"
            % (_money(loss), _p(stop)))


def plain_parts(order):
    """The order in plain words, one part per item (the log's one-liner)."""
    parts = [first_line(order)]
    if is_bracket(order):
        if order.get("stop_loss_stop_price") is not None:
            leg = "stop-loss %s" % _p(order["stop_loss_stop_price"])
            if order.get("stop_loss_limit_price") is not None:
                leg += " (limit %s)" % _p(order["stop_loss_limit_price"])
            parts.append(leg)
        if order.get("take_profit_limit_price") is not None:
            parts.append("target %s" % _p(order["take_profit_limit_price"]))
        loss = loss_line(order)
        if loss:
            parts.append(loss)
    return parts


def plain(order):
    """BUY 15 TCS on NSE, product Intraday (MIS), market order, valid today / stop-loss 2,419.35 /
    target 2,467.65 / max loss about ₹362.25 if the stop fills at 2,419.35 (before charges; ...)"""
    return " / ".join(plain_parts(order))


def ticket_lines(order, flat_by=None, exiting=False):
    """The ticket body: what the user types into their own broker app, line by line."""
    lines = [first_line(order)]
    q = _shares(order.get("qty"))
    side = str(order.get("side", "")).lower()
    out_side = "sell" if side == "buy" else "buy"
    if is_bracket(order):
        if order.get("stop_loss_stop_price") is not None:
            sl = _p(order["stop_loss_stop_price"])
            if order.get("stop_loss_limit_price") is not None:
                sl += " (limit %s)" % _p(order["stop_loss_limit_price"])
            lines.append("stop-loss %s   -> place a separate SL / SL-M %s order for %s right after the %s fills"
                         % (sl, out_side, q, side))
        if order.get("take_profit_limit_price") is not None:
            lines.append("target    %s   -> place a limit %s for %s, or set a price alert; cancel the other "
                         "when one fills" % (_p(order["take_profit_limit_price"]), out_side, q))
        lines.append("(two separate exit orders, because many brokers have no bracket order for equity "
                     "intraday; or use your broker's bracket/OCO order if it has one)")
        loss = loss_line(order)
        if loss:
            lines.append(loss)
    if exiting:
        lines.append("this closes your intraday position: first cancel the open stop-loss and target orders "
                     "for these shares in your broker app, then exit")
    if flat_by and (is_bracket(order) or exiting):
        lines.append("flat by %s IST: %s" % (flat_by, SQUARE_OFF))
    return lines


def amount_numbers(order):
    nums = []
    for k in ("qty", "limit_price", "stop_price", "stop_loss_stop_price", "take_profit_limit_price",
              "stop_loss_limit_price"):
        if order.get(k) is not None:
            v = _f(order[k])
            if v is not None:
                nums.append(v)
    loss, _ = max_loss(order)
    if loss is not None:
        nums.append(loss)
    return nums


def parse_yes(reply, order, batch_symbols=()):
    """Is this reply an explicit yes for THIS one order? Returns (ok, why)."""
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    words = re.findall(r"[a-z']+", raw.lower())
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" counts (not ok, sure, go, do it, haan)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one order at a time, not a batch"
    if DOUBT_WORDS & set(words[1:]):
        return False, "the reply has a condition or a doubt in it; ask again"
    sym = nse_symbol(order.get("symbol"))
    upper = raw.upper()
    tokens = {nse_symbol(t.strip("-&.")) for t in re.findall(r"[A-Z0-9&\-.]+", upper)}
    others = {nse_symbol(s) for s in batch_symbols} - {sym, ""}
    named = tokens & others
    if named:
        return False, "the reply names %s, but this order is for %s" % (", ".join(sorted(named)), sym)
    # numbers in the reply must be this order's numbers; the symbol's own digits (3MINDIA) do not count
    rest = upper.replace(sym + ".NS", " ").replace(sym, " ") if sym else upper
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", rest)]
    if nums:
        want = amount_numbers(order)
        if not any(abs(n - w) < 0.005 for n in nums for w in want):
            return False, "the reply says %s, but this order is %s" % (
                ", ".join(_str_num(n) for n in nums), plain(order))
    return True, "explicit yes for %s" % plain(order)


# ---------------------------------------------------------------- state

def load_state(path):
    return read_json(path, "broker state", required=False) or {"orders": []}


def latest(state, fp, today):
    """The latest decision recorded today for this fingerprint, or None."""
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today:
            found = rec
    return found


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "trader-risk has not checked these orders (run check.py)"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today; run check.py again" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "this exact order was not in the last risk check (it changed or is new); run check.py again"


def status_of(order, risk, state, today):
    """(ready, label, risk_result)"""
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, fp, today)
    if rv is None:
        return False, "NOT READY: " + why, None
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by trader-risk: " + "; ".join(rv.get("reasons", [])), rv
    rec = latest(state, fp, today)
    if rec:
        st = rec.get("status")
        if st == "approved":
            return False, ("APPROVED by you at %s: now you enter it yourself, then tell Claude what happened "
                           "(ticket.py result)" % rec.get("at")), rv
        return False, "DONE: %s at %s" % (st, rec.get("at")), rv
    return True, "READY: trader-risk PASS, waiting for your yes", rv


def kind_of(order, rv):
    """entry or exit, as trader-risk saw it (older checks: a buy is an entry)."""
    if rv and rv.get("kind"):
        return rv["kind"]
    return "entry" if str(order.get("side", "")).lower() == "buy" else "exit"


# ---------------------------------------------------------------- rendering

def header(idx, total, cfg):
    if cfg.get("paper", True):
        return "ORDER %d of %d, PRACTICE (paper: write it down, place nothing)" % (idx, total)
    return "ORDER %d of %d, LIVE: you enter this yourself in your broker app (%s)" % (
        idx, total, str(cfg.get("broker") or "groww"))


def flat_by_of(order, cfg):
    return (order.get("plan") or {}).get("flat_by") or order.get("flat_by") or cfg.get("flat_by") or "15:00"


def question(cfg, exiting=False):
    if cfg.get("paper", True) and exiting:
        return ("Practice mode: do you want to close this practice trade (written down, nothing placed)? "
                "Reply yes or no.")
    if cfg.get("paper", True):
        return ("Practice mode: do you want to take this as a practice trade (written down, nothing placed)? "
                "Reply yes or no.")
    return "Do you want to place this order yourself? Reply yes or no."


def render(order, idx, total, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    exiting = kind_of(order, rv) == "exit" and not is_bracket(order)
    lines = [header(idx, total, cfg)]
    for part in ticket_lines(order, flat_by_of(order, cfg), exiting):
        lines.append("  " + part)
    if order.get("why"):
        lines.append("  why: " + order["why"])
    lines.append("  " + label)
    for w in (rv or {}).get("warnings", []):
        lines.append("  note: " + w)
    if ready:
        lines.append("  " + question(cfg, exiting))
        lines.append("  Claude never places it, never opens your broker's order form and never handles your "
                     "password, PIN or OTP.")
    return "\n".join(lines), ready


def ticket_json(order, idx, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    loss, stop = max_loss(order)
    exiting = kind_of(order, rv) == "exit" and not is_bracket(order)
    return {"index": idx, "ready": ready, "status": label, "plain": plain(order),
            "lines": ticket_lines(order, flat_by_of(order, cfg), exiting),
            "fingerprint": fingerprint(order), "paper": bool(cfg.get("paper", True)),
            "broker": cfg.get("broker", "groww"), "placed_by": "the user, by hand, in their own broker app",
            "why": order.get("why", ""), "bracket": is_bracket(order), "max_loss": loss, "stop": stop,
            "flat_by": flat_by_of(order, cfg), "question": question(cfg, exiting) if ready else None}


# ---------------------------------------------------------------- fills -> portfolio

def apply_fill(portfolio_path, cfg, symbol, side, qty, price, date):
    """Record a filled order in trader/portfolio.json: cash and shares, with a
    cost-basis lot per buy (oldest lots are relieved first on a sell)."""
    pf = read_json(portfolio_path, "portfolio", required=False)
    if pf is None:
        budget = cfg.get("budget")
        if not budget:
            raise TicketError("no %s yet and no budget in the config to start it from" % portfolio_path)
        pf = {"cash": float(budget), "positions": []}
    symbol = nse_symbol(symbol)
    qty, price = _f(qty), _f(price)
    if qty is None or price is None or qty <= 0 or price <= 0:
        raise TicketError("a fill needs a positive qty and price")
    if abs(qty - round(qty)) > 1e-9:
        raise TicketError("NSE has no fractional shares: the fill qty must be whole shares, got %s"
                          % _str_num(qty))
    cost = round(qty * price, 2)
    pos = None
    for p in pf.setdefault("positions", []):
        if nse_symbol(p.get("ticker") or p.get("symbol")) == symbol:
            pos = p
    if side == "buy":
        if pos is None:
            pos = {"ticker": symbol, "shares": 0.0, "lots": []}
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({"id": "%s-%d" % (symbol, len(lots) + 1), "date": date, "shares": qty, "cost": cost})
        pos["shares"] = round(float(pos.get("shares", 0)) + qty, 6)
        pf["cash"] = round(float(pf.get("cash", 0)) - cost, 2)
    else:
        if pos is None or float(pos.get("shares", 0)) + 1e-9 < qty:
            raise TicketError("cannot record selling %s %s: the portfolio holds %s"
                              % (_str_num(qty), symbol, pos.get("shares", 0) if pos else 0))
        left = qty
        for lot in sorted(pos.get("lots", []), key=lambda l: l.get("date", "")):
            if left <= 1e-9:
                break
            take = min(left, float(lot["shares"]))
            per = float(lot["cost"]) / float(lot["shares"]) if float(lot["shares"]) else 0
            lot["cost"] = round(float(lot["cost"]) - per * take, 2)
            lot["shares"] = round(float(lot["shares"]) - take, 6)
            left -= take
        pos["lots"] = [l for l in pos.get("lots", []) if float(l["shares"]) > 1e-9]
        pos["shares"] = round(float(pos["shares"]) - qty, 6)
        if pos["shares"] <= 1e-9:
            pf["positions"].remove(pos)
        pf["cash"] = round(float(pf.get("cash", 0)) + cost, 2)
    write_json(portfolio_path, pf)
    return pf


def log_fill(state_path, now, today, symbol, side, qty, price):
    """A fill in the log: what the user reported, kept next to the decisions."""
    state = load_state(state_path)
    state.setdefault("orders", []).append({
        "at": ist_iso(now), "date": today, "status": "filled", "symbol": nse_symbol(symbol), "side": side,
        "qty": _str_num(float(qty)), "price": float(price),
        "plain": "%s %s %s at %s" % (side.upper(), _str_num(float(qty)), nse_symbol(symbol), _p(price))})
    write_json(state_path, state)


# ---------------------------------------------------------------- commands

def _clock(a):
    now = parse_now(getattr(a, "now", None))
    today = getattr(a, "today", None) or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", today):
        raise TicketError("--today wants a date like 2026-11-23")
    return now, today


def _load(a):
    doc = read_json(a.orders, "orders file (run trader-budget, or ticket.py flatten, first)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise TicketError("no orders in %s" % a.orders)
    cfg = read_json(a.config, "config", required=False) or {}
    risk = read_json(a.risk, "risk check", required=False)
    state = load_state(a.state)
    now, today = _clock(a)
    return orders, cfg, risk, state, today, now


def _pick(orders, index):
    if index is None:
        raise TicketError("say which order: --index N")
    if not 1 <= index <= len(orders):
        raise TicketError("there is no order %d (there are %d)" % (index, len(orders)))
    return orders[index - 1]


def cmd_show(a):
    orders, cfg, risk, state, today, now = _load(a)
    picks = [(a.index, _pick(orders, a.index))] if a.index else list(enumerate(orders, 1))
    if a.json:
        out = [ticket_json(o, i, cfg, risk, state, today) for i, o in picks]
        print(json.dumps(out[0] if a.index else out, indent=2, ensure_ascii=False))
        return 0 if any(t["ready"] for t in out) else 1
    blocks, any_ready = [], False
    for i, o in picks:
        text, ready = render(o, i, len(orders), cfg, risk, state, today)
        blocks.append(text)
        any_ready = any_ready or ready
    print("\n\n".join(blocks))
    print("\nOne order at a time: a yes covers one ticket, never the batch. Nothing here is sent to a broker. "
          "Education and tooling, not financial advice.")
    return 0 if any_ready else 1


def cmd_approve(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    ready, label, rv = status_of(order, risk, state, today)
    if not ready:
        raise TicketError("order %d is not ready: %s" % (a.index, label))
    ok, why = parse_yes(a.reply, order, [o.get("symbol", "") for o in orders])
    if not ok:
        print("Not recorded: %s. Ask the user again with the ticket for order %d." % (why, a.index))
        return 1
    loss, stop = max_loss(order)
    paper = bool(cfg.get("paper", True))
    broker = str(cfg.get("broker") or "groww")
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": nse_symbol(order.get("symbol")), "side": order.get("side"),
           "qty": order.get("qty"), "kind": kind_of(order, rv), "max_loss": loss, "stop": stop,
           "plain": plain(order), "status": "approved", "reply": a.reply,
           "paper": paper, "broker": broker}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded your yes for order %d: %s." % (a.index, plain(order)))
    if paper:
        print("Practice mode: nothing is placed. Write it down as taken at the price on your screen now.")
    else:
        print("Now YOU enter it in your broker app (%s), exactly as the ticket reads. Claude does not place "
              "it and does not open the order form." % broker)
    print("Then tell Claude what happened, and record it:")
    print("  ticket.py result --index %d --status placed --filled-qty Q --filled-price P [--broker-id ID]"
          % a.index)
    print("  ticket.py result --index %d --status not-placed     (you changed your mind)" % a.index)
    print("  ticket.py result --index %d --status rejected --note \"why\"     (your broker refused it)" % a.index)
    return 0


def cmd_decline(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": nse_symbol(order.get("symbol")), "side": order.get("side"),
           "plain": plain(order), "status": "declined", "reply": a.reply or "no"}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded: you said no to order %d (%s). Nothing was placed." % (a.index, plain(order)))
    return 0


def cmd_result(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    fp = fingerprint(order)
    rec = latest(state, fp, today)
    if not rec or rec.get("status") != "approved":
        raise TicketError("order %d has no yes recorded today; a result can only follow an approval" % a.index)
    if bool(a.filled_qty) != bool(a.filled_price):
        raise TicketError("give both --filled-qty and --filled-price, or neither")
    if a.status != "placed" and a.filled_qty:
        raise TicketError("a fill only goes with --status placed")
    new = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fp, "symbol": rec["symbol"],
           "side": rec["side"], "plain": rec["plain"], "status": a.status,
           "reported_by": "the user", "paper": bool(rec.get("paper", cfg.get("paper", True)))}
    for k in ("qty", "kind", "max_loss", "stop"):
        if rec.get(k) is not None:
            new[k] = rec[k]
    if a.broker_id:
        new["broker_order_id"] = a.broker_id
    if a.note:
        new["note"] = a.note
    if a.status == "placed" and a.filled_qty:
        # the portfolio first: a bad fill (a fraction, a sell of more than is held) records nothing
        apply_fill(a.portfolio, cfg, rec["symbol"], rec["side"], a.filled_qty, a.filled_price, today)
    state["orders"].append(new)
    write_json(a.state, state)
    print("Recorded order %d as %s (as you reported it)." % (a.index, a.status))
    if a.status == "placed" and a.filled_qty:
        log_fill(a.state, now, today, rec["symbol"], rec["side"], a.filled_qty, a.filled_price)
        print("Added the fill to %s." % a.portfolio)
    elif a.status == "placed":
        print("When it fills, record it: ticket.py fill --symbol %s --side %s --qty Q --price P"
              % (rec["symbol"], rec["side"]))
    if a.status == "placed" and rec.get("kind") == "entry" and is_bracket(order):
        if new["paper"]:
            print("Practice: watch the stop-loss %s and the target %s yourself (trader-alerts), and record "
                  "the sell with ticket.py fill when one is hit."
                  % (_p(order.get("stop_loss_stop_price") or 0), _p(order.get("take_profit_limit_price") or 0)))
        else:
            print("Now place the stop-loss and the target sell orders from the ticket in your broker app. "
                  "When one fills, cancel the other and record the sell with ticket.py fill.")
    return 0


def cmd_fill(a):
    cfg = read_json(a.config, "config", required=False) or {}
    now = parse_now(a.now)
    date = a.date or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise TicketError("--date wants a date like 2026-11-23")
    pf = apply_fill(a.portfolio, cfg, a.symbol, a.side, a.qty, a.price, date)
    log_fill(a.state, now, date, a.symbol, a.side, a.qty, a.price)
    print("Recorded %s %s %s at %s on %s in %s. Cash now %s (before charges)."
          % (a.side, a.qty, nse_symbol(a.symbol), _money(a.price), date, a.portfolio, _money(pf["cash"])))
    return 0


def cmd_flatten(a):
    cfg = read_json(a.config, "config", required=False) or {}
    sym = nse_symbol(a.symbol)
    now, today = _clock(a)
    source = "you, from your broker app's positions page"
    if a.qty is not None:
        qty = _f(a.qty)
        if qty is None or qty <= 0 or abs(qty - round(qty)) > 1e-9:
            raise TicketError("--qty wants the whole number of shares you hold, like --qty 15")
    else:
        pf = read_json(a.portfolio, "portfolio", required=False) or {}
        qty = sum(_f(p.get("shares", p.get("qty"))) or 0 for p in pf.get("positions", [])
                  if nse_symbol(p.get("ticker") or p.get("symbol")) == sym)
        source = a.portfolio
        if qty <= 1e-9:
            raise TicketError("nothing to flatten: no %s shares in %s. Look at the positions page of your "
                              "broker app and pass --qty if you do hold some" % (sym, a.portfolio))
    plan = read_json(a.plan, "plan", required=False) or {}
    flat_by = (plan.get("flat_by") if nse_symbol(plan.get("symbol")) == sym else None) \
        or cfg.get("flat_by") or "15:00"
    order = {"symbol": sym, "side": "sell", "qty": _str_num(qty), "type": "market", "time_in_force": "day",
             "flat_by": flat_by,
             "why": "flat by %s IST: closes the %s intraday position before your broker's auto square-off "
                    "(qty from %s)" % (flat_by, sym, source)}
    old = read_json(a.orders, "orders", required=False)
    replaced = len(old.get("orders", [])) if isinstance(old, dict) else 0
    doc = {"created_by": "trader-broker", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "proposed",
           "paper": bool(cfg.get("paper", True)),
           "note": ("Flatten ticket. A proposal only: after trader-risk passes it and you say yes in the chat, "
                    "you enter it yourself in your own broker app."),
           "source": "ticket.py flatten %s on %s" % (sym, today), "orders": [order]}
    write_json(a.orders, doc)
    print("Flatten %s: %s (flat by %s IST)." % (sym, plain(order), flat_by))
    print("Wrote it to %s%s." % (a.orders, (" (it replaced %d earlier proposal%s)"
                                             % (replaced, "" if replaced == 1 else "s")) if replaced else ""))
    if a.qty is None:
        print("The qty comes from %s. Check it against the positions page of your broker app before you "
              "say yes." % source)
    print("Next:")
    print("  1. trader-risk: check.py. A sell that only closes what you hold is never blocked by the daily")
    print("     limits or the entry window.")
    print("  2. ticket.py show, then the user's yes for this one order.")
    print("  3. The user, in their own broker app: cancel the open stop-loss and target sell orders for these")
    print("     shares first (or the exit can be rejected, or leave them short), then exit the position, then")
    print("     tell Claude the fill: ticket.py result --index 1 --status placed --filled-qty Q --filled-price P")
    return 0


def cmd_log(a):
    state = load_state(a.state)
    now, today = _clock(a)
    rows = [r for r in state.get("orders", []) if a.all or rec_day(r) == today]
    if not rows:
        print("No decisions recorded%s." % ("" if a.all else " today"))
        return 1
    for r in rows:
        extra = (" id " + r["broker_order_id"]) if r.get("broker_order_id") else ""
        reply = (' reply "%s"' % r["reply"]) if r.get("reply") and r["status"] in ("approved", "declined") else ""
        loss = (" max loss %s" % _money(r["max_loss"])) if r.get("max_loss") is not None and r["status"] == "placed" else ""
        idx = ("#%s" % r["index"]) if r.get("index") is not None else "  "
        print("%s  %s %-10s %s%s%s%s" % (r.get("at"), idx, r.get("status"), r.get("plain"), reply, loss, extra))
    placed = sum(1 for r in rows if r.get("status") == "placed")
    print("%d placed%s (placed by you, as you reported it; practice trades count too)."
          % (placed, "" if a.all else " today"))
    return 0


def main(argv=None):
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Order tickets and your yes (trader-broker). It never places "
                                             "orders and never talks to a broker: you enter them yourself.")
    sub = ap.add_subparsers(dest="cmd")

    def clock(p):
        p.add_argument("--now", help="pretend it is this India time (IST), e.g. \"2026-11-23 09:50\"")
        p.add_argument("--today", help="YYYY-MM-DD (default: today in India)")

    def common(p):
        p.add_argument("--orders", default="trader/orders.json")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--risk", default="trader/state/risk.json")
        p.add_argument("--state", default="trader/state/broker.json")
        clock(p)

    p = sub.add_parser("show", help="render the tickets")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve", help="record the user's explicit yes for one order")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="the user's reply, word for word")
    p = sub.add_parser("decline", help="record a no")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply")
    p = sub.add_parser("result", help="record what the user reports back after entering the order themselves")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--status", required=True, choices=list(RESULT_STATUSES))
    p.add_argument("--broker-id", help="optional: the order id your broker app shows")
    p.add_argument("--note")
    p.add_argument("--filled-qty")
    p.add_argument("--filled-price")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p = sub.add_parser("fill", help="record a fill in trader/portfolio.json")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    p.add_argument("--qty", required=True)
    p.add_argument("--price", required=True)
    p.add_argument("--date", help="YYYY-MM-DD (default: today in India)")
    p.add_argument("--now")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--state", default="trader/state/broker.json")
    p = sub.add_parser("flatten", help="a sell ticket that closes a symbol's position by flat_by")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", help="shares you hold, as your broker app shows (default: trader/portfolio.json)")
    p.add_argument("--orders", default="trader/orders.json", help="where the ticket is written")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--config", default="trader/config.json")
    clock(p)
    p = sub.add_parser("log", help="today's decisions")
    p.add_argument("--state", default="trader/state/broker.json")
    clock(p)
    p.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"show": cmd_show, "approve": cmd_approve, "decline": cmd_decline, "result": cmd_result,
                "fill": cmd_fill, "flatten": cmd_flatten, "log": cmd_log}[a.cmd](a)
    except TicketError as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
