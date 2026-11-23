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
  Yahoo Finance data (.NS symbols, rupees, delayed), bar by bar, plus NSE's
  own published daily files: official closes, delivery percentage and
  turnover from the bhavcopy, the F&O ban list, the 52-week highs and the
  Nifty 500 list with industries. Use when
  the user says "scan the market", "scan NSE", "find me setups", "what is
  breaking out", "opening range breakouts", "scan for bull flags", "oversold
  stocks", "pre-market scan", "build my watchlist", "what should I look at
  today", or at the start of a trading day before /trader-news.
argument-hint: "[universe [--index nifty500] | daily | orb] [--at HH:MM]"
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
python "${CLAUDE_SKILL_DIR}/scan.py" universe                          # every EQ stock -> trader/universe.txt
python "${CLAUDE_SKILL_DIR}/scan.py" universe --index nifty500         # the Nifty 500 only, plus the industry map
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
files, `--no-write`, `--show N`, `--json`. For `daily` only: `--nse DIR` (a
folder of NSE files you saved yourself), `--no-nse` (do not read NSE's files)
and `--state DIR` (where they are kept, default `state/` beside `--out`).
For `universe`: `--index nifty500`. Exit code 0 when the scan found
something, 1 when it found nothing or could not run (the reason is one plain
line). Put a symbol list in quotes: `&` in `M&M` means something to a shell.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line, read by `daily` and `orb` |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. trader-news and trader-entry read `hits[].symbol`. `daily` adds a `nse` block and the optional hit fields below |
| `trader/state/industry.json` | written by `universe --index nifty500`: `industry` maps SYMBOL to NSE's industry name. Read by `daily` |
| `trader/state/sec_bhavdata_full_DDMMYYYY.csv` | NSE's full bhavcopy of the latest completed session, saved by `daily` so a second scan the same evening does not download it again. The copy of an older day is deleted when a new one is saved |
| `trader/state/CM_52_wk_High_low_DDMMYYYY.csv` | NSE's 52-week high/low file of the same day, kept the same way |
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

### A liquid universe: the Nifty 500

`universe --index nifty500` builds the list from NSE's own Nifty 500
constituents file (`content/indices/ind_nifty500list.csv`) instead: series EQ
rows with a real ISIN. `trader/universe.txt` keeps its two columns
(`SYMBOL|Name`). The file's Industry column goes to
`trader/state/industry.json`, and from then on `daily` prints each hit's
industry and writes it to the hit. The map covers the Nifty 500 only: a hit
outside it has no industry. Running plain `universe` afterwards puts the full
list back and leaves the map where it is. Real run on 2026-11-24:

```
python scan.py universe --index nifty500
NSE's Nifty 500 list (ind_nifty500list.csv): 501 rows.
Kept 495 stocks in series EQ (the series that can be traded intraday), in 20 industries.
Dropped: 3 series RR, 2 series BE, 1 placeholder without a real ISIN.
Wrote trader/universe.txt (one SYMBOL|Name a line) and trader/state/industry.json (SYMBOL -> industry).
```

The three RR rows are REITs, the two BE rows are trade-for-trade, and the
placeholder is a row NSE itself names "Dummy" with an ISIN that does not
start with IN. So the "Nifty 500" universe was 495 symbols on that day, not
500.

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

Hits are listed most traded first (price times volume).

### NSE's own files: official closes, delivery, the ban list

One Yahoo habit to know: for hours after the 15:30 close it leaves the latest
NSE session's daily close empty. So `daily` also reads NSE's openly published
files for the latest completed session (worked out from the NSE calendar in
the tool: today from 15:30 IST on a trading day, else the trading day
before). They are plain downloads from `nsearchives.nseindia.com`, one
request each, at least half a second apart, never NSE's website API:

| file | what the scan takes from it |
| --- | --- |
| `products/content/sec_bhavdata_full_DDMMYYYY.csv` (the full bhavcopy, about 405 KB, series EQ rows) | `CLOSE_PRICE` as that day's official close and `TTL_TRD_QNTY` as its volume, for a chart whose Yahoo bar for that day is empty or missing. A close Yahoo did fill is never overwritten. For each hit: `DELIV_PER` (delivery %) and `TURNOVER_LACS` (shown in crore: lakhs / 100) |
| `content/fo/fo_secban.csv` | the stocks in the F&O ban period for the trade date the file names. Downloaded on every run that has hits, never kept |
| `content/CM_52_wk_High_low_DDMMYYYY.csv` | the adjusted 52-week high. A hit that closed within 2 % of it, or above it, is tagged |

The optional fields on a daily hit in `trader/watchlist.json` (each one is
there only when the file said so; no old field changed):

| field | what |
| --- | --- |
| `delivery_pct` | the bhavcopy's `DELIV_PER`: the share of the day's traded quantity that was marked for delivery |
| `turnover_cr` | the day's turnover in rupees crore (1 crore = 10,000,000) |
| `fno_ban` | `true` when the symbol is on the ban list. Printed as "in the F&O ban period" |
| `near_52w_high`, `high_52w` | `true` and the high, when the close is within 2 % of the 52-week high or above it |
| `industry` | from `trader/state/industry.json`, when that file is there |

The watchlist also gets a `nse` block: the `session`, the `bhavcopy` file
name (null when it was not used), `bhavcopy_from`, `latest_close_from` (how
many scanned charts took their latest close from `bhavcopy`, `last_price` or
`yahoo`), `fno_ban_trade_date`, `fno_ban` and `high_52w_file`.

Read these plainly:

- The F&O ban is about derivatives positions. For a cash-segment trade it is
  information only. The file is the current one, so its trade date is
  printed; on a replay of an older day it is still today's list.
- The 52-week file dated for a session holds the highs up to the session
  BEFORE it (in the file of 23-Nov-2026 the newest high date is 20-NOV-2026,
  and 77 EQ stocks traded above their listed high on the 23rd). So the tag
  means "closed within 2 % of the high as it stood before this session, or
  above it". The high is NSE's adjusted high; the close is compared to it.
- A delivery percentage is a fact about the day's trades, not a signal.
- The delivery, turnover and 52-week fields go only on a hit whose latest
  close is that session's.

Fallbacks, each said in the output:

- The bhavcopy comes out in the evening after the session. Before that NSE
  answers 404: the tool does not retry or look elsewhere. For a chart whose
  latest bar is empty it then uses the session's last traded price from
  Yahoo's same answer (stamped in the final five minutes) as that day's
  close, says that this is not the official close, and leaves the delivery,
  turnover and 52-week fields out.
- If the ban list or the 52-week file cannot be read, the scan says so and
  goes on without those flags.
- `--offline` without `--nse DIR` reads no NSE file. `--nse DIR` wants the
  files under their NSE names in that folder. `--no-nse` skips them all.

Real run, replaying Monday evening from the holiday (02:21 IST on Tue
2026-11-24; Yahoo still had Monday's daily bars empty). `industry.json` was
there from the Nifty 500 run above:

```
python scan.py daily --symbols "RELIANCE,HDFCBANK,ICICIBANK,SBIN,INFY,TCS,ITC,LT,AXISBANK,KOTAKBANK,BHARTIARTL,HINDUNILVR,BAJFINANCE,M&M,MARUTI,SUNPHARMA,TATASTEEL,NTPC,POWERGRID,ONGC,COALINDIA,WIPRO,HCLTECH,TECHM,ADANIENT,ADANIPORTS,JSWSTEEL,HINDALCO,BAJAJ-AUTO,TITAN,EICHERMOT,APOLLOHOSP,MANAPPURAM,ITI,COFORGE" --now "2026-11-23 18:00"
Daily scan, Mon 2026-11-23 18:00 IST, closes through 2026-11-23 (Yahoo spark, NSE daily closes only).
Fetched 35 of 35 charts. 34 passed the liquidity filter (₹50+ and 500,000+ shares); 34 scanned.
Official closes: NSE's bhavcopy of 2026-11-23 (sec_bhavdata_full_23112026.csv, downloaded from NSE) gave the close and the volume of that day for 34 charts that Yahoo had left empty.
12 charts matched a setup: 4 20-day closing-high breakout, 6 bull flag, 1 reclaimed the 20-day average, 1 oversold (RSI under 30).
F&O ban list for trade date 25-NOV-2026 (fo_secban.csv): 1 stock; among the hits: MANAPPURAM. Information only for a cash-segment trade.
3 hits closed within 2 % of the 52-week high, or above it (CM_52_wk_High_low_23112026.csv: adjusted highs up to the session before).
Most traded first (price x volume):
  HDFCBANK         ₹817.80  RSI  65.1  bull flag  [delivery 57.5 %; ₹4,624.37 cr traded; Financial Services]
  ICICIBANK      ₹1,585.50  RSI  65.3  bull flag  [delivery 63.2 %; ₹3,569.60 cr traded; Financial Services]
  RELIANCE       ₹1,351.00  RSI  64.8  bull flag  [delivery 53.9 %; ₹2,094.38 cr traded; Oil Gas & Consumable Fuels]
  TCS            ₹2,460.00  RSI  57.2  reclaimed the 20-day average  [delivery 39.8 %; ₹1,818.90 cr traded; Information Technology]
  SBIN           ₹1,132.65  RSI  70.2  bull flag  [delivery 45.7 %; ₹1,712.18 cr traded; Financial Services]
  ITI              ₹276.21  RSI  64.9  20-day closing-high breakout  [delivery 2.7 %; ₹1,348.04 cr traded; Telecommunication]
  BAJFINANCE     ₹1,095.50  RSI  60.7  bull flag  [delivery 62.2 %; ₹1,066.04 cr traded; Financial Services]
  COFORGE        ₹2,142.30  RSI  68.1  20-day closing-high breakout  [delivery 45.1 %; ₹950.11 cr traded; Information Technology; near its 52-week high (2,177.80)]
  ITC              ₹301.00  RSI  72.4  bull flag  [delivery 60.3 %; ₹828.23 cr traded; Fast Moving Consumer Goods]
  APOLLOHOSP     ₹9,257.00  RSI  71.4  20-day closing-high breakout  [delivery 44.4 %; ₹735.44 cr traded; Healthcare; near its 52-week high (9,282.50)]
  EICHERMOT      ₹8,172.50  RSI  71.0  20-day closing-high breakout  [delivery 57.3 %; ₹622.53 cr traded; Automobile and Auto Components; near its 52-week high (8,230.00)]
  MANAPPURAM       ₹271.00  RSI  29.5  oversold (RSI under 30)  [delivery 46.3 %; ₹408.34 cr traded; Financial Services; in the F&O ban period]
Wrote trader/watchlist.json (mode daily, 12 hits).
A scan is a list of charts that match a rule, not a list of stocks to buy. Next: pick one chart, then /trader-news SYMBOL.
```

COFORGE as the full run below wrote it to `hits[]`:

```
{"symbol": "COFORGE", "setups": ["breakout_20d"], "price": 2142.3, "close_date": "2026-11-23", "rsi": 68.1, "volume": 4442752, "delivery_pct": 45.11, "turnover_cr": 950.11, "near_52w_high": true, "high_52w": 2177.8, "industry": "Information Technology"}
```

Real run, the whole universe of 2,419 EQ stocks, at 02:22 IST on Tue
2026-11-24 (121 Yahoo requests; the bhavcopy and the 52-week file came from
the copies the run above had saved, the ban list was downloaded again). This
run was not timed; the same 121 requests took 62 seconds an hour earlier:

```
python scan.py daily --show 12
Daily scan, Tue 2026-11-24 02:22 IST, closes through 2026-11-23 (Yahoo spark, NSE daily closes only).
Fetched 2,418 of 2,419 charts (1 failed or had no data). 469 passed the liquidity filter (₹50+ and 500,000+ shares), 8 had under 30 days; 461 scanned.
Official closes: NSE's bhavcopy of 2026-11-23 (sec_bhavdata_full_23112026.csv, the copy saved in trader/state) gave the close and the volume of that day for 461 charts that Yahoo had left empty.
150 charts matched a setup: 48 20-day closing-high breakout, 83 bull flag, 7 reclaimed the 20-day average, 13 oversold (RSI under 30), 7 oversold bounce.
F&O ban list for trade date 25-NOV-2026 (fo_secban.csv): 1 stock; among the hits: MANAPPURAM. Information only for a cash-segment trade.
21 hits closed within 2 % of the 52-week high, or above it (CM_52_wk_High_low_23112026.csv: adjusted highs up to the session before).
Most traded first (price x volume):
  HDFCBANK         ₹817.80  RSI  65.1  bull flag  [delivery 57.5 %; ₹4,624.37 cr traded; Financial Services]
  APLAPOLLO      ₹1,670.00  RSI  19.4  oversold (RSI under 30)  [delivery 29.8 %; ₹4,625.46 cr traded; Capital Goods]
  ICICIBANK      ₹1,585.50  RSI  65.3  bull flag  [delivery 63.2 %; ₹3,569.60 cr traded; Financial Services]
  RELIANCE       ₹1,351.00  RSI  64.8  bull flag  [delivery 53.9 %; ₹2,094.38 cr traded; Oil Gas & Consumable Fuels]
  TCS            ₹2,460.00  RSI  57.2  reclaimed the 20-day average  [delivery 39.8 %; ₹1,818.90 cr traded; Information Technology]
  SBIN           ₹1,132.65  RSI  70.2  bull flag  [delivery 45.7 %; ₹1,712.18 cr traded; Financial Services]
  DIVISLAB      ₹11,203.00  RSI  68.8  bull flag  [delivery 66.6 %; ₹1,398.91 cr traded; Healthcare]
  ITI              ₹276.21  RSI  64.9  20-day closing-high breakout  [delivery 2.7 %; ₹1,348.04 cr traded; Telecommunication]
  BAJFINANCE     ₹1,095.50  RSI  60.7  bull flag  [delivery 62.2 %; ₹1,066.04 cr traded; Financial Services]
  COFORGE        ₹2,142.30  RSI  68.1  20-day closing-high breakout  [delivery 45.1 %; ₹950.11 cr traded; Information Technology; near its 52-week high (2,177.80)]
  SHRIRAMFIN       ₹995.90  RSI  60.2  bull flag  [delivery 43.1 %; ₹943.93 cr traded; Financial Services]
  BSE            ₹3,757.00  RSI  58.5  bull flag  [delivery 46.9 %; ₹905.58 cr traded; Financial Services]
  ... and 138 more in the watchlist (--show N for more).
Wrote trader/watchlist.json (mode daily, 150 hits).
A scan is a list of charts that match a rule, not a list of stocks to buy. Next: pick one chart, then /trader-news SYMBOL.
```

All 150 hits got a delivery % and a turnover, and 95 of them an industry
(the map covers the Nifty 500 only). The same scan at 01:07 IST, with
Yahoo's last prices and volumes instead of the bhavcopy, had 468 liquid
charts and 149 hits. The bhavcopy's traded quantity is a little higher than
the volume Yahoo carried (RELIANCE 15,392,522 against 15,388,955 in the
saved Yahoo answer), which is enough to lift a stock over the 500,000-share
floor. For the 39 charts kept in the tests the last price Yahoo carried was
already equal to NSE's official close, so those prices did not move. That
does not hold for every stock: in the same bhavcopy 20MICRONS has a last
price of 182.50 and a close of 180.44.

The fallback, also run for real, by pinning the clock to a session that had
not happened yet so that NSE answered 404 for its bhavcopy:

```
python scan.py daily --symbols "RELIANCE,TCS,M&M" --now "2026-11-25 16:00" --no-write
Daily scan, Wed 2026-11-25 16:00 IST, closes through 2026-11-23 (Yahoo spark, NSE daily closes only).
Fetched 3 of 3 charts. 3 passed the liquidity filter (₹50+ and 500,000+ shares); 3 scanned.
NSE's bhavcopy of 2026-11-25 was not used (HTTP 404: NSE publishes it in the evening after the session, so it may not be out yet). 3 charts take the session's last traded price from Yahoo as that day's close, not the official close.
2 charts matched a setup: 1 bull flag, 1 reclaimed the 20-day average.
F&O ban list for trade date 25-NOV-2026 (fo_secban.csv): 1 stock; none of the hits is on it. Information only for a cash-segment trade.
Most traded first (price x volume):
  RELIANCE       ₹1,351.00  RSI  64.8  bull flag  [Oil Gas & Consumable Fuels]
  TCS            ₹2,460.00  RSI  57.2  reclaimed the 20-day average  [Information Technology]
A scan is a list of charts that match a rule, not a list of stocks to buy. Next: pick one chart, then /trader-news SYMBOL.
```

Not measured: at what time in the evening NSE puts the bhavcopy up. The tool
does not guess; it asks once and falls back on a 404.

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
- NSE's files (the equity list, the Nifty 500 list, the bhavcopy, the ban
  list, the 52-week file) are the exchange's own openly published files on
  `nsearchives.nseindia.com`: plain downloads, one request a file a run, no
  login and no website API. They are end-of-day files: nothing in them is
  live. The copies in the tests (`tests/fixtures/trader-scanner/nse/`) are
  real cuts of the files of 2026-11-23.
- Partial results are said as partial: "Fetched 2,418 of 2,419 charts (1
  failed or had no data)". Never round a partial scan up to "the whole
  market".
- The tools do not model brokerage, STT, exchange charges or GST. Any loss or
  risk figure elsewhere in this pack is before charges.

## Steps

1. Once a week or so: `scan.py universe`, or `scan.py universe --index
   nifty500` for the liquid names only (it also refreshes the industry map).
2. Before the open: `scan.py daily`. Tell the user the counts (fetched,
   liquid, scanned, hits), which day the closes run through, whether those
   closes are NSE's official ones or the last-price stand-in, and the top
   few by setup. Pass on a hit's delivery %, turnover, industry and flags as
   facts, never as a reason to buy.
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
- Delivery %, turnover, the F&O ban flag and the 52-week tag are facts from
  NSE's end-of-day files. Never present one as a signal or a forecast.

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
