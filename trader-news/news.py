#!/usr/bin/env python3
"""News monitor for trader-news (NSE stocks). Standard library only.

Watches Yahoo Finance headlines for the NSE stocks you watch or hold and
prints only what is new since the last check, newest first, with how far each
stock is from its previous close and a flag on anything that touches your
symbols.

    python news.py                                   # watchlist + plan + portfolio symbols
    python news.py --tickers TCS INFY "M&M"
    python news.py --tickers TCS --all --hours 72    # everything in the last 3 days, seen or not
    python news.py --no-moves                        # skip the price line per symbol

Your symbols, when --tickers is not given: every hit in trader/watchlist.json
(trader-scanner), the symbol in trader/plan.json (trader-exit) and the
positions in trader/portfolio.json. Symbols are NSE symbols (RELIANCE,
HDFCBANK, M&M, BAJAJ-AUTO); the tool adds ".NS" for Yahoo and strips it again.

Sources, one request each per symbol:
  - Yahoo Finance headline RSS:
    https://feeds.finance.yahoo.com/rss/2.0/headline?s=<SYMBOL>.NS&region=IN&lang=en-IN
  - The move: Yahoo Finance's chart endpoint (unofficial, NSE prices on it can
    lag), the last price against the previous close, in rupees:
    https://query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS?range=1d&interval=5m

What this tool does NOT read, and says so in every run:
  - Exchange filings. Results, board meetings, dividends, pledges, orders won
    and every other corporate announcement an Indian company files with NSE
    or BSE do not come through Yahoo's feed. Check the corporate announcements
    page of NSE or BSE for the stock by hand.
  - Indian business news in general. Yahoo's headline feed is thin for NSE
    stocks. Checked on 2026-11-24: RELIANCE.NS and HDFCBANK.NS returned an
    empty feed; TCS.NS and INFY.NS returned 13 items each, mostly global press
    releases, some weeks old; the M&M.NS feed led with stories about Mars,
    the maker of M&M's chocolate, and the SBIN.NS feed mixed State Bank of
    India with unrelated companies called SBI.
    So: an empty result is not "no news", and a headline is not always about
    your company. The tool prints "no headlines from Yahoo for X" rather than
    stay silent, and never fills the gap from anywhere else.

Times print in India Standard Time (IST, a fixed UTC+5:30; never the
computer's own zone). --now "2026-11-23 09:45" is India time. State:
trader/state/news.json remembers what you have seen (30 days), so an item
shows once. Offline: --offline DIR reads yahoo-<SYMBOL>.xml and
chart-<SYMBOL>.json from DIR. The tests use it.

Headlines are leads, not facts: open the story before acting, and treat
nothing here as advice.
"""

import argparse
import datetime as _dt
import email.utils
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
import xml.etree.ElementTree as ET

YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=IN&lang=en-IN"
CHART = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
HOSTS = ("query2", "query1")
UA = "Mozilla/5.0 (compatible; day-trader-skill-india/1.0)"
KEEP_DAYS = 30
MOVE_PCT = 3.0  # a stock this far from its previous close is flagged as moving
IST_OFFSET = _dt.timedelta(hours=5, minutes=30)
SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}(\.BO)?$")
RUPEE = "₹"
FILINGS_NOTE = ("Exchange filings are not read here: check the stock's corporate announcements on the NSE or "
                "BSE website yourself.")

KEYWORDS = [
    ("earnings", r"\bearnings\b|\bresults\b|\bEPS\b|\bguidance\b|\bforecast\b|\bnet profit\b|\bQ[1-4]\b"
                 r"|\bquarterly\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|price target|target price|\boutperform\b|\bunderperform\b"),
    ("legal", r"\blawsuit\b|\bsued\b|\bprobe\b|\binvestigation\b|\bantitrust\b|\bjudge\b|\bcourt\b|\bNCLT\b"
              r"|\btribunal\b|\braids?\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bdemerger\b|\bbuyout\b|\btakeover\b|\bdeals?\b"
             r"|\bstake sale\b|\bopen offer\b"),
    ("capital", r"\bdividend\b|\bbuyback\b|\brepurchase\b|\bstock split\b|\boffering\b|\bbonus issue\b"
                r"|\brights issue\b|\bQIP\b|\bOFS\b|\bblock deal\b|\bbulk deal\b|\bpledg(?:e|ed|ing)\b"),
    ("people", r"\bCEO\b|\bCFO\b|\bmanaging director\b|\bresign|\bsteps down\b|\bappoint"),
    ("risk", r"\brecall\b|\bbankrupt|\binsolvency\b|\bdefault\b|\bdelist|\bhack|\bbreach\b|\boutage\b|\bfraud\b"),
    ("halt", r"\bhalt(?:s|ed)?\b|\btrading (?:pause|suspen)|\b(?:upper|lower) circuit\b"
             r"|\bcircuit (?:limit|filter|breaker)"),
    ("regulatory", r"\bSEBI\b|\bUSFDA\b|\bFDA\b|\bapproval\b|\bapproves\b|\bclinical\b|\bCCI\b|\bIRDAI\b|\bTRAI\b"),
    ("macro", r"\bRBI\b|\brepo rate\b|\bMPC\b|\bCPI\b|\bWPI\b|\binflation\b|\btariffs?\b|\bUnion Budget\b|\bGST\b"
              r"|\bcrude\b|\brupee\b|\bFIIs?\b|\bFPIs?\b|\bNifty\b|\bSensex\b"),
]


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time

def to_ist(utc):
    """Aware or naive UTC -> naive India Standard Time (a fixed UTC+5:30, no daylight saving)."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(_dt.timezone.utc)
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    """Naive India time -> aware UTC."""
    return (local.replace(tzinfo=None) - IST_OFFSET).replace(tzinfo=_dt.timezone.utc)


def parse_now(text):
    """--now -> aware UTC. A time without an offset (or ending in " IST") is India time."""
    if not text:
        return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    try:
        d = _dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        raise NewsError("--now wants a time like \"2026-11-23 09:45\" (India time) or ...Z (UTC)")
    if d.tzinfo is None:
        return ist_to_utc(d)
    return d.astimezone(_dt.timezone.utc)


# ---------------------------------------------------------------- inputs

def yahoo_symbol(sym):
    """NSE symbol -> Yahoo symbol: RELIANCE -> RELIANCE.NS. .NS / .BO and ^ index symbols are kept."""
    s = sym.strip().upper()
    return s if s.endswith((".NS", ".BO")) or s.startswith("^") else s + ".NS"


def nse_symbol(sym):
    """What output and files show: TCS.NS -> TCS."""
    s = str(sym or "").strip().upper()
    return s[:-3] if s.endswith(".NS") else s


def inr(x):
    return RUPEE + "{:,.2f}".format(float(x))


def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """{NSE symbol: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = nse_symbol(h.get("symbol") or h.get("ticker"))
        if sym:
            out[sym] = h.get("name")
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.setdefault(nse_symbol(pl["symbol"]), pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = nse_symbol(p.get("ticker") or p.get("symbol"))
        if sym:
            out[sym] = p.get("name") or out.get(sym)
    return out


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.5):
        self.offline = offline
        self.pause = pause
        self._last = 0.0

    def get(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise NewsError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise NewsError("could not reach %s: %s" % (url, exc.reason))


def rss_url(sym):
    return YAHOO % urllib.parse.quote(yahoo_symbol(sym), safe="")


def chart_url(sym, host=HOSTS[0]):
    return CHART % (host, urllib.parse.quote(yahoo_symbol(sym), safe=""))


# ---------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_time(value):
    """RFC 822 (RSS) or ISO 8601 -> aware UTC datetime, or None. No offset means UTC (feed times)."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def parse_rss(data, ticker):
    """Yahoo headline RSS -> items. An empty feed (Yahoo sends one for many NSE symbols) is []."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("RSS for %s is not valid XML: %s" % (ticker, exc))
    out = []
    for it in root.iter("item"):
        title = _text(it, "title")
        link = _text(it, "link")
        guid = _text(it, "guid") or link
        out.append({"id": "rss:" + guid, "source": "Yahoo Finance", "kind": "headline",
                    "ticker": ticker, "title": title, "summary": _text(it, "description"),
                    "link": link, "time": parse_time(_text(it, "pubDate"))})
    return out


def parse_chart(data, ticker):
    """Yahoo chart JSON -> {"ticker", "price", "prev_close", "pct", "time", "currency"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable (check the NSE symbol)" % ticker)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        raise NewsError("chart for %s has no price" % ticker)
    when = meta.get("regularMarketTime")
    return {"ticker": ticker, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2), "currency": meta.get("currency") or "INR",
            "time": _dt.datetime.fromtimestamp(when, _dt.timezone.utc) if when else None}


def collect_moves(fetcher, watch, errors=None):
    errors = [] if errors is None else errors
    out = []
    for t in sorted(watch):
        name = "chart-%s.json" % t
        try:
            try:
                data = fetcher.get(chart_url(t, HOSTS[0]), name)
            except NewsError as exc:
                if fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
                    raise
                data = fetcher.get(chart_url(t, HOSTS[1]), name)
            out.append(parse_chart(data, t))
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (t, exc))
    return out


def mention_patterns(watch):
    pats = {}
    for t, name in watch.items():
        s = re.escape(t)
        alts = [r"\(%s\)" % s, r"\b(?:NSE|BSE)\s*:\s*%s(?![A-Z0-9&-])" % s, r"\b%s\.(?:NS|BO)\b" % s]
        if name and len(name) >= 4:
            alts.append(r"\b%s\b" % re.escape(name).replace(r"\ ", r"[\s-]"))
        pats[t] = re.compile("|".join(alts))
    return pats


def tag(items, watch):
    """Which of your symbols each item touches, and why it matters."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        touches = {it["ticker"]} if it["ticker"] in watch else set()
        for t, rx in pats.items():
            if rx.search(hay):
                touches.add(t)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
    return items


def dedupe(items):
    """One item per story: same id, or same link, or same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           it["time"].date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the check

def collect(fetcher, watch, errors=None, feeds=None):
    """Every item of every symbol's feed. feeds[symbol] = that feed's items (for the coverage notes)."""
    errors = [] if errors is None else errors
    items = []
    for t in sorted(watch):
        try:
            got = parse_rss(fetcher.get(rss_url(t), "yahoo-%s.xml" % t), t)
        except NewsError as exc:
            errors.append("headlines for %s: %s" % (t, exc))
            continue
        if feeds is not None:
            feeds[t] = got
        items += got
    return items


def in_window(it, now, hours):
    """Inside the last `hours`, and not from after a pinned --now."""
    if it["time"] is None:
        return True
    return now - _dt.timedelta(hours=hours) <= it["time"] <= now + _dt.timedelta(minutes=10)


def coverage_notes(feeds, now, hours):
    """One plain line per symbol Yahoo had nothing for, so silence is never read as 'no news'."""
    notes = []
    for t in sorted(feeds):
        got = feeds[t]
        if not got:
            notes.append("no headlines from Yahoo for %s (its feed is empty)" % t)
            continue
        if any(in_window(it, now, hours) for it in got):
            continue
        times = [it["time"] for it in got if it["time"] is not None and it["time"] <= now]
        newest = (", the newest of its %d is from %s" % (len(got), to_ist(max(times)).strftime("%Y-%m-%d"))
                  if times else "")
        notes.append("no headlines from Yahoo for %s in the last %g hours%s" % (t, hours, newest))
    return notes


def select(items, state, now, hours=48, show_all=False):
    """Items within the window; only unseen ones unless show_all."""
    seen = state.setdefault("seen", {})
    out = []
    for it in items:
        if not in_window(it, now, hours):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp
    state.pop("ciks", None)   # left behind by the US version's SEC lookup


def render_moves(moves, move_pct=MOVE_PCT, now=None):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        when = ""
        if m.get("time"):
            local = to_ist(m["time"])
            when = " at %s IST" % local.strftime("%H:%M")
            if now is not None and local.date() != to_ist(now).date():
                when += " on %s" % local.date().isoformat()   # a stale quote: a holiday, a weekend, last night
        out.append("%-10s %s %+.2f%% vs previous close %s%s  %s" % (
            m["ticker"], inr(m["price"]), m["pct"], inr(m["prev_close"]), when, flag))
    return [line.rstrip() for line in out]


def render(items, errors, now, moves=None, move_pct=MOVE_PCT):
    out = render_moves(moves or [], move_pct, now)
    if out:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M IST") if it["time"] else "time unknown"
        who = ",".join(it["touches"]) or it["ticker"]
        out.append("  %s  %-11s %-13s %s" % (when, who, it["source"], it["title"]))
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        extra.append(it["link"])
        out.append("      " + "  ".join(extra))
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s. Yahoo's coverage of NSE stocks is thin, and a headline it lists is not always about "
               "your company. %s Headlines are leads, not facts; open the story before acting. "
               "Not financial advice." % (to_ist(now).strftime("%Y-%m-%d %H:%M IST"), FILINGS_NOTE))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        d["time_ist"] = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M") if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="NSE symbols; default: watchlist + plan + portfolio")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per symbol")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help='pretend it is this time ("2026-11-23 09:45" India time, or ISO with Z)')
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    errors = []
    if args.tickers:
        watch = {nse_symbol(t): None for t in args.tickers if nse_symbol(t)}
        known = watch_from_files(args.portfolio, args.watchlist, args.plan)
        watch.update({t: known[t] for t in watch if known.get(t)})
    else:
        watch = watch_from_files(args.portfolio, args.watchlist, args.plan)
    for t in sorted(watch):
        if not SYMBOL.match(t):
            errors.append("%s is not an NSE symbol like RELIANCE, M&M or BAJAJ-AUTO: skipped" % t)
            del watch[t]
    if not watch:
        for e in errors:
            print("news: %s" % e, file=sys.stderr)
        print("news: no tickers. Pass --tickers, or run /trader-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    try:
        now = parse_now(args.now)
    except NewsError as exc:
        print("news: %s" % exc, file=sys.stderr)
        return 2
    state = load_json(args.state) or {}
    fetcher = Fetcher(offline=args.offline)
    feeds = {}
    items = collect(fetcher, watch, errors=errors, feeds=feeds)
    items = dedupe(tag(items, watch))
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    errors += coverage_notes(feeds, now, args.hours)
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   time_ist=to_ist(m["time"]).strftime("%Y-%m-%d %H:%M") if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({"checked": now.isoformat(), "checked_ist": to_ist(now).strftime("%Y-%m-%d %H:%M"),
                          "moves": mv, "items": _json_ready(shown), "notes": errors,
                          "not_read": FILINGS_NOTE}, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
