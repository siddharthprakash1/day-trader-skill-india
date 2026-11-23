---
name: trader-scanner
description: >-
  Chart Scanner for the Indian market (NSE). Scans thousands of NSE charts
  for setups: opening-range breakouts, flags, oversold bounces. Builds the
  list of NSE stocks in series EQ from NSE's own equity list, runs a
  pre-market scan of daily closes (20-day closing-high breakout, bull flag,
  reclaimed the 20-day average, oversold, oversold bounce) and an intraday
  opening-range breakout scan on 5-minute bars (range 09:15 to 09:30 IST),
  then writes trader/watchlist.json for trader-news and trader-entry. Free
  Yahoo Finance data (.NS symbols, rupees, delayed), bar by bar. Use when
  the user says "scan the market", "scan NSE", "find me setups", "what is
  breaking out", "opening range breakouts", "scan for bull flags", "oversold
  stocks", "pre-market scan", "build my watchlist", "what should I look at
  today", or at the start of a trading day before /trader-news.
argument-hint: "[universe | daily | orb] [--at HH:MM]"
---

# trader-scanner

The Chart Scanner is the first step of a day trade on NSE. It reads
thousands of free charts and lists the ones that match a rule. That is all it
does. A scan is a list of charts that match a rule, not a list of stocks to
buy, and a setup on a chart is not a prediction. Most people who day trade
lose money (SEBI's own study found 93% of individual F&O traders lost money
over FY22 to FY24); the scanner does not change that. It only saves you from
flipping through 2,400 charts by hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md
lives). Use `python3` on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/scan.py" universe                          # -> trader/universe.txt
python "${CLAUDE_SKILL_DIR}/scan.py" daily                             # before the open -> trader/watchlist.json
python "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols "RELIANCE,M&M,SBIN"   # or --watchlist FILE, --limit 200
python "${CLAUDE_SKILL_DIR}/scan.py" orb                               # after 09:35 IST -> trader/watchlist.json
python "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:00                    # cut the clock at 10:00 IST
python "${CLAUDE_SKILL_DIR}/scan.py" orb --symbols "TCS,INFY" --now "2026-11-23 10:00"   # replay a day of the last week
python "${CLAUDE_SKILL_DIR}/scan.py" orb --watchlist trader/watchlist.json --json
```

Flags: `--min-price 50` (rupees) and `--min-volume` (daily 500,000 shares,
orb 0), `--or-minutes 15`, `--max-confirm 200` (orb stage 2), `--until HH:MM`
(orb, default `no_entries_after`), `--now "2026-11-23 10:00"` (IST; a
trailing " IST" is fine) to pin the clock, `--offline PATH` to read saved
files, `--no-write`, `--show N`, `--json`. Exit code 0 when the scan found
something, 1 when it found nothing or could not run (the reason is one plain
line). Put a symbol list in quotes: `&` in `M&M` means something to a shell.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line, read by `daily` and `orb` |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. trader-news and trader-entry read `hits[].symbol` |
| `trader/config.json` | read by `orb`: `no_entries_after` (default 14:30) |

Symbols are NSE symbols everywhere: `RELIANCE`, `HDFCBANK`, `M&M`,
`BAJAJ-AUTO`. The tool adds `.NS` for Yahoo (and writes `M%26M.NS` in a URL)
and strips it again, so no file or line of output carries `.NS`. Prices are
rupees (₹).

## Time and the session

All times are India Standard Time, a fixed UTC+5:30 with no daylight saving,
worked out by the tool itself (never the computer's time zone). The session
is NSE's regular one, 09:15 to 15:30 IST: 75 five-minute bars. The pre-open
session (09:00 to 09:15) is ignored. NSE has no early-close days. The Muhurat
special session on Diwali is ignored too: that day counts as closed. The NSE
holiday list for 2026 is built in (16 weekday holidays, NSE circular of
12 Dec 2025). NSE had not published 2027 when this was written: for any other
year the tool knows weekends only and prints a note saying so.

## The universe

`universe` downloads NSE's list of listed equities, `EQUITY_L.csv`, from
`nsearchives.nseindia.com/content/equities/` (`archives.nseindia.com` as the
fallback) and keeps the rows whose SERIES is `EQ`. That is the whole rule:

- `EQ` is the normal rolling-settlement series, the only one you can buy and
  sell on the same day.
- `BE` and `BZ` are trade-for-trade: every trade must be delivered, so they
  cannot be day traded. They are dropped and counted.

If NSE's site refuses the download, save the file from NSE's website
yourself and pass `--offline EQUITY_L.csv`. Real run on the file of
2026-11-24:

```
python scan.py universe
NSE list of listed equities (EQUITY_L.csv): 2,643 rows.
Kept 2,419 NSE stocks in series EQ (the series that can be traded intraday).
Dropped: 184 series BE, 40 series BZ (BE and BZ are trade-for-trade: delivery only, no intraday).
Wrote trader/universe.txt (one SYMBOL|Name a line).
```

The list is every EQ stock, liquid or not; the price and volume floors below
do the rest.

## The daily scan (before the open)

Yahoo's spark endpoint returns closes for 20 symbols a request, so 2,419
symbols take 121 requests. Liquidity comes from the spark meta: last price at
least `--min-price` (₹50) and last session's volume at least `--min-volume`
(500,000 shares). Run it before the open or after the close: during the
session the volume is the day's so far, and today's daily bar is left out
until 15:30.

The setups are computed from daily closes only (spark has no highs or lows):

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

Hits are listed most traded first (price times volume). One Yahoo habit to
know: for hours after the 15:30 close it leaves the latest NSE session's
daily close empty. When that bar is empty and the session is over, the tool
uses the session's last price from the same answer (stamped in the final five
minutes) as that day's close, and says which day the closes run through.

Real run, the whole universe, at 01:07 IST on Tue 2026-11-24 (a holiday, so
the last session is Monday's). It took 62 seconds with the polite 0.3 s pause:

```
python scan.py daily --show 12
Daily scan, Tue 2026-11-24 01:07 IST, closes through 2026-11-23 (Yahoo spark, NSE daily closes only).
Fetched 2,418 of 2,419 charts (1 failed or had no data). 468 passed the liquidity filter (₹50+ and 500,000+ shares), 8 had under 30 days; 460 scanned.
149 charts matched a setup: 48 20-day closing-high breakout, 83 bull flag, 7 reclaimed the 20-day average, 12 oversold (RSI under 30), 7 oversold bounce.
Most traded first (price x volume):
  HDFCBANK         ₹817.80  RSI  65.1  bull flag
  APLAPOLLO      ₹1,670.00  RSI  19.4  oversold (RSI under 30)
  ICICIBANK      ₹1,585.50  RSI  65.3  bull flag
  RELIANCE       ₹1,351.00  RSI  64.8  bull flag
  TCS            ₹2,460.00  RSI  57.2  reclaimed the 20-day average
  SBIN           ₹1,132.65  RSI  70.2  bull flag
  DIVISLAB      ₹11,203.00  RSI  68.8  bull flag
  ITI              ₹276.21  RSI  64.9  20-day closing-high breakout
  BAJFINANCE     ₹1,095.50  RSI  60.7  bull flag
  COFORGE        ₹2,142.30  RSI  68.1  20-day closing-high breakout
  SHRIRAMFIN       ₹995.90  RSI  60.2  bull flag
  BSE            ₹3,757.00  RSI  58.5  bull flag
  ... and 137 more in the watchlist (--show N for more).
Wrote trader/watchlist.json (mode daily, 149 hits).
```

## The opening-range breakout scan (during the session)

The opening range is the first `--or-minutes` (15) of the session: the 09:15,
09:20 and 09:25 five-minute bars. A hit is the first completed 5-minute bar
to CLOSE above the range high, at that bar's close time. The earliest
possible hit is the 09:30 bar, known at 09:35, so `orb` runs from 09:35.

1. Stage 1, every symbol: spark closes of the day's 5-minute bars, 20 a
   request. A chart goes on only if a completed bar after the range closed at
   or above the highest close of the range. That is a necessary condition
   (the range high is at least every range close), so no breakout is lost.
2. Stage 2, each candidate: the v8 chart with real highs and lows, one
   request each. Range high = the highest high of the range bars, range low =
   the lowest low. Stage 2 checks at most `--max-confirm` (200) candidates,
   the most traded first (price times volume), and prints PARTIAL with the
   count it skipped. `--max-confirm 0` checks them all.

A chart is usable only when every 5-minute bar from 09:15 to the cutoff is
there; a missing bar means a halt or a thin stock. Null closes are dropped, a
bar off the 5-minute grid (the live tick) is dropped, and a bar counts only
once it is complete (start + 5 minutes).

Two things Yahoo does with NSE 5-minute bars, both seen in the real data of
Mon 2026-11-23 and handled in the open:

- Its closes-only spark feed leaves out a few bars a day that its full chart
  has (RELIANCE was missing 11:45, 13:05 and 13:25; SUNPHARMA 09:20). A chart
  with such a gap cannot be ruled out on closes, so it goes to stage 2 as a
  candidate and the full chart decides under the strict rule. The count is
  printed ("1 with a bar missing from Yahoo's closes feed, sent to the full
  chart").
- Its full chart had no 15:20 bar on any of the five days looked at. So a
  scan cut after 15:20 finds no usable chart. With the default last entry
  time of 14:30 this never comes up.

Before 09:35, on a weekend or an NSE holiday, after the close, or after
`no_entries_after` (default 14:30: an expired setup) it prints one plain line
and writes nothing.

`--now` on a day of the last week replays that session from live Yahoo data
(Yahoo's free 5-minute history is short; older days need `--offline` with
saved charts). This was run for real on the holiday, replaying Monday:

```
python scan.py orb --symbols "RELIANCE,HDFCBANK,ICICIBANK,SBIN,INFY,TCS,ITC,LT,AXISBANK,KOTAKBANK,BHARTIARTL,HINDUNILVR,BAJFINANCE,M&M,MARUTI,SUNPHARMA,TATASTEEL,NTPC,POWERGRID,ONGC,COALINDIA,WIPRO,HCLTECH,TECHM,ADANIENT,ADANIPORTS,JSWSTEEL,HINDALCO,BAJAJ-AUTO,TITAN" --now "2026-11-23 10:00"
Opening-range breakout scan, Mon 2026-11-23 10:00 AM IST: 15-minute range (9:15 to 9:30 AM), 5-minute bars, Yahoo.
Fetched 30 of 30 charts; 30 usable (1 with a bar missing from Yahoo's closes feed, sent to the full chart); 9 candidates, 9 confirmed with real highs and lows.
5 of 30 charts closed above their opening-range high by 10:00 AM:
   9:40 AM  TECHM          ₹1,788.80  range 1,760.40 to 1,784.80  +0.22% over
   9:45 AM  HINDUNILVR     ₹2,076.90  range 2,057.20 to 2,073.50  +0.16% over
   9:45 AM  TCS            ₹2,443.50  range 2,405.00 to 2,433.70  +0.40% over
   9:45 AM  WIPRO            ₹188.75  range 186.74 to 188.17  +0.31% over
   9:50 AM  INFY           ₹1,123.00  range 1,107.70 to 1,120.50  +0.22% over
Wrote trader/watchlist.json (mode orb, 5 hits).
A scan is a list of charts that match a rule, not a list of stocks to buy. Next: pick one chart, then /trader-news SYMBOL and /trader-entry SYMBOL.
```

TCS's range is the 09:15 to 09:25 bars, high 2,433.70, low 2,405.00; the
09:40 bar closed at 2,443.50, 0.40 % over the high, at 9:45 AM. The same
day's charts are saved in the tests
(`tests/fixtures/trader-scanner/wall5m-2026-11-23.json.gz`, 39 real Yahoo
charts), and `orb --offline` on that file at 10:00 gives the same five.

Not run: an opening-range scan over the whole universe. It would be 121 spark
requests plus up to 200 v8 charts; how long it takes and how many of 2,419
charts are usable on a live morning has not been measured. For a fast answer
scan the daily watchlist or a list of names you trade (`--watchlist`,
`--symbols`).

## Data and limits

- Yahoo Finance's chart and spark endpoints are free and unofficial. NSE
  symbols carry `.NS`, prices are in INR, and NSE data on Yahoo is typically
  delayed. Bars are 5-minute bars, bar by bar, never tick by tick. query2
  first, query1 on a 429 or 5xx, one retry after a 5-second wait on a 429.
- Partial results are said as partial: "Fetched 2,418 of 2,419 charts (1
  failed or had no data)". Never round a partial scan up to "the whole
  market".
- The tools do not model brokerage, STT, exchange charges or GST. Any loss or
  risk figure elsewhere in this pack is before charges.

## Steps

1. Once a week or so: `scan.py universe`.
2. Before the open: `scan.py daily`. Tell the user the counts (fetched,
   liquid, scanned, hits), which day the closes run through, and the top few
   by setup.
3. After 09:35 IST (10:00 is a common choice): `scan.py orb`. Tell the user
   how many usable charts broke their range and when, and that a later hit is
   closer to the price now.
4. The user picks one chart. Hand it on: `/trader-news SYMBOL` (what is
   moving it), then `/trader-entry SYMBOL` (the entry bar by bar). Never pick
   for them.

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before acting on it.
- Long side only: the scanner looks for breakouts upward, never short setups.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data real time, and never promises that a setup works.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Nothing in this pack places an order. Claude never logs in to a broker and never sends an order:
  the user enters every order themselves in their own broker app, after trader-risk passes that
  exact order and they have said an explicit yes to it in the chat.
- Practice mode by default (`"paper": true`): write the trade down first, risk nothing.
- Long side, cash-segment equity only. No F&O, margin (MTF) or short selling unless the config
  allows it and the user confirms.
- Broker passwords, PINs and OTPs never go in the chat.
