---
name: trader-market
description: >-
  Market Health Check for the Indian market (NSE). Before any scan, says in
  plain words what kind of day the last session was and what condition the
  market is in going into the next one: advances and declines across every
  series EQ stock and across the Nifty 500 (from NSE's own bhavcopy), which
  industries rose most and least, new 52-week highs against new lows, the
  share of Nifty 500 stocks above their 20-day average, the Nifty 50 against
  its 20-day and 50-day averages and the India VIX against its own last year.
  Ends with one rule-based reading (supportive, mixed or weak for long
  breakouts) with the exact rules and thresholds printed beside it, and says
  how fresh each source is. A missing source is named and left out, never
  guessed. It describes a session that has ended; it does not predict and it
  never places an order. Use when the user says "how is the market", "market
  health", "market breadth", "advance decline", "what kind of day was it",
  "is the market weak", "is it a good backdrop for breakouts", "which sectors
  are strong", "how many new highs", "where is VIX", "check the market before
  I scan", or at the start of a trading day before /trader-scanner.
argument-hint: "[check | check --no-trend | check --date YYYY-MM-DD | show | rules]"
---

# trader-market

A breakout that works on a day when most stocks are rising is a different
trade from the same chart on a day when four out of five are falling. The
Market Health Check gives that backdrop before `/trader-scanner` runs: how
many stocks rose and fell in the last completed NSE session, where the
strength and the weakness sat, and where the Nifty 50 and the India VIX
stand. It counts and it describes. It does not predict the next session, and
a supportive backdrop does not make any one breakout work. Most people who
day trade lose money; SEBI's own study found 93% of individual F&O traders
lost money over FY22 to FY24.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
Use `python3` on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/market.py" check                     # the full check -> trader/market.json
python "${CLAUDE_SKILL_DIR}/market.py" check --no-trend          # skip the slower 20-day part
python "${CLAUDE_SKILL_DIR}/market.py" check --date 2026-11-20   # an earlier session
python "${CLAUDE_SKILL_DIR}/market.py" check --set breadth_support_pct=60 --set move_pct=4
python "${CLAUDE_SKILL_DIR}/market.py" show                      # print the saved check again, no network
python "${CLAUDE_SKILL_DIR}/market.py" rules                     # the reading's rules and thresholds
```

Other flags: `--now "2026-11-24 10:00"` (India time), `--offline DIR` (saved
files, no network), `--refresh` (ignore the cache), `--cache DIR`, `--out
FILE`, `--no-write`, `--show N` (industries at each end), `--json`,
`--quiet`.

Every time is India Standard Time (UTC+5:30, no daylight saving), worked out
by the tool and never taken from the computer's own time zone. The session is
NSE's regular one, 09:15 to 15:30; the pre-open and the Muhurat session are
ignored (Muhurat day counts as closed), and the 2026 NSE holiday list is
built in. The check is always about the latest COMPLETED session: on a
holiday, a weekend or before 15:30 that is the trading day before.

## What it reads and writes

All of it is openly published, read with plain GET requests, no login and no
cookies. NSE's files come from its public archive host, never from the
website's API.

| source | what it gives |
| --- | --- |
| `nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv` | NSE's full bhavcopy: each stock's previous close, high, low and close. Series EQ rows only. Published in the evening after the session |
| `nsearchives.nseindia.com/content/indices/ind_nifty500list.csv` | the Nifty 500's constituents and each one's industry. Not dated: it is the list as of the day it is fetched |
| `nsearchives.nseindia.com/content/CM_52_wk_High_low_DDMMYYYY.csv` | each stock's 52-week high and low as they stood BEFORE that session, adjusted for corporate actions |
| Yahoo chart API, `^NSEI` and `^INDIAVIX`, one year of daily bars | the Nifty 50 and the India VIX. Free, unofficial, and NSE data on it is delayed |
| `trader/config.json` | optional: a `"market"` block with your own thresholds |
| `trader/cache/` | written: every file it fetched, so it is fetched once (about 8 MB for the 20 bhavcopies) |
| `trader/market.json` | written: the whole check, for the other skills and for `show` |

## The parts

- **Breadth.** A stock advanced when its `CLOSE_PRICE` is above the
  `PREV_CLOSE` on the same row. Advances, declines, unchanged, the
  advance/decline ratio, the share up or down 3 % or more (`move_pct`) and
  the median change, for every EQ stock and again for the Nifty 500.
- **Industries.** The Nifty 500 grouped by the list's Industry column: the
  median change and the share of stocks that rose, strongest and weakest
  first. Industries with fewer than 5 stocks are not printed.
- **52-week highs and lows.** A new high is a day's high above the 52-week
  high as it stood before the session; a new low is a day's low under the
  52-week low. Touching the old level is not a new one.
- **Trend.** The share of Nifty 500 stocks that closed above the plain
  average of their own last 20 closes. This needs the bhavcopies of the last
  20 sessions: each is fetched once, at least 0.5 s apart, and kept in
  `trader/cache/`, so the first run takes under a minute and later runs ask
  only for the new day. Weekends and NSE holidays are skipped. A missing
  file is named and the average is taken over the sessions that could be
  read; under 15 of the 20 the part is left out. `--no-trend` skips it.
- **Index backdrop.** The Nifty 50's close, its change, and how far it is
  from its 20-day and 50-day average close. The India VIX's level, its
  change, the low and high of the last year, and its percentile: the share of
  the last year's closes that were under today's.

## The reading

Five signals, each voting +1, 0 or -1 by a fixed rule. The votes are added.

| signal | +1 | -1 |
| --- | --- | --- |
| breadth | 55 % or more of Nifty 500 stocks closed up | 45 % or less |
| trend | 60 % or more are above their 20-day average | 40 % or less |
| highs and lows (all EQ) | new highs are 2 times new lows or more | new lows are 2 times new highs or more (0 when the two together are under 20) |
| Nifty 50 | above both its 20-day and 50-day average | under both |
| India VIX | at or under the 50th percentile of its last year, and not up 10 % or more on the day | at or above the 80th percentile, or up 10 % or more on the day |

**SUPPORTIVE** for long breakouts at +2 or more, **WEAK** at -2 or less,
**MIXED** between them. A signal whose source could not be read is left out
and named, and with fewer than 3 signals there is no reading at all.

These thresholds are this pack's own round numbers, not a tested edge. They
are printed with every reading so that anyone can see why it says what it
says, and every one can be changed: `market.py rules` lists them, `--set
NAME=VALUE` changes one for a run, and `trader/config.json` changes it for
good:

```json
{"market": {"breadth_support_pct": 60, "vix_stress_pctile": 85}}
```

## Steps

1. Run `market.py check` once before scanning. NSE publishes the bhavcopy in
   the evening, so the same day's session can be checked that night or any
   time the next morning.
2. Read back the reading with its score and the votes behind it, then the
   two or three numbers that explain it (breadth, the strongest and weakest
   industries, the VIX). Use the tool's own words: supportive, mixed or weak
   by the stated rules.
3. Read out anything under "Not available" or "Left out", and the session
   date of each source. If the bhavcopy answered 404, say NSE has not
   published it yet and offer to run again later.
4. Then go on to `/trader-scanner`. The check never changes a scan, a plan or
   an order; it is the backdrop the user reads the scan against.
5. Never turn the reading into "buy" or "do not trade". If the user asks
   whether to trade today, give the numbers and the rule, and leave the
   decision with them.

## Example: the session of Monday 2026-11-23

A real run on Tuesday 2026-11-24 (an NSE holiday, so Monday was the latest
session), `market.py check`, first run with an empty cache:

```
Market Health Check: NSE session of Mon 2026-11-23
Checked Tue 2026-11-24 21:18 IST. NSE is closed today (NSE holiday: Guru Nanak Jayanti).

Breadth (NSE full bhavcopy, series EQ; up = closed above the previous close)
  All EQ stocks  2,773 stocks: 748 up, 1,997 down, 28 unchanged; advance/decline 0.37; 27.0 % up, 72.0 % down
                 up 3 % or more: 129 (4.7 %); down 3 % or more: 318 (11.5 %); median change -0.96 %
  Nifty 500      495 stocks: 133 up, 360 down, 2 unchanged; advance/decline 0.37; 26.9 % up, 72.7 % down
                 up 3 % or more: 13 (2.6 %); down 3 % or more: 45 (9.1 %); median change -0.98 %

Industries (Nifty 500 list, by median change; 18 industries with 5 stocks or more)
  Rose most
    Information Technology           25 stocks  median +0.86 %   68.0 % up
    Realty                           11 stocks  median +0.05 %   63.6 % up
    Services                         14 stocks  median +0.05 %   50.0 % up
    Healthcare                       44 stocks  median -0.22 %   40.9 % up
    Consumer Durables                16 stocks  median -0.40 %   37.5 % up
  Rose least
    Metals & Mining                  20 stocks  median -1.44 %   10.0 % up
    Capital Goods                    69 stocks  median -1.54 %   23.2 % up
    Telecommunication                 9 stocks  median -1.56 %   22.2 % up
    Power                            19 stocks  median -1.88 %   10.5 % up
    Oil Gas & Consumable Fuels       19 stocks  median -2.05 %    5.3 % up

52-week highs and lows (the day's high and low against the 52-week high and low before the session)
  All EQ stocks  77 new highs, 73 new lows, of 2,773 stocks compared
  Nifty 500      16 new highs, 7 new lows, of 495 stocks compared
                 highs: APOLLOHOSP, AXISBANK, CUB, DLF, EICHERMOT, FEDERALBNK, HONASA, INDHOTEL, KOTAKBANK, MAHABANK, MEESHO, PHOENIXLTD, and 4 more
                 lows: AADHARHFC, APLAPOLLO, COCHINSHIP, INOXWIND, KEC, PIIND, TATAPOWER

Trend (Nifty 500 stocks above their own 20-day average close)
  308 of 494 (62.3 %) closed above the average of their last 20 closes (2026-10-26 to 2026-11-23)
  Left out as a likely split or bonus in the window (a close more than 25 % under the one before it; not verified): CONCORDBIO

Index backdrop (Yahoo daily bars; NSE data on Yahoo is delayed)
  Nifty 50     25,349.65, -1.75 % on the day (from 25,800.60); 3.83 % above its 20-day average (24,414.26); 7.78 % above its 50-day average (23,520.10)
  India VIX    17.70, +2.19 % on the day (from 17.32); range since 2025-11-24: 9.15 to 27.89, 45.6 % of the way up; above 80.7 % of the closes in it

Reading: MIXED for long breakouts
  Score 0 from 5 signals: supportive at +2 or more, weak at -2 or less, mixed between them; no reading with fewer than 3 signals.
  -1  26.9 % of Nifty 500 stocks closed up
      rule: +1 at 55 % or more, -1 at 45 % or less
  +1  62.3 % of Nifty 500 stocks closed above their 20-day average
      rule: +1 at 60 % or more, -1 at 40 % or less
   0  77 new 52-week highs, 73 new lows (NSE EQ)
      rule: +1 when new highs are 2 times new lows or more, -1 when new lows are 2 times new highs or more, 0 when the two together are under 20
  +1  Nifty 50 at 25,349.65, 20-day average 24,414.26, 50-day average 23,520.10
      rule: +1 above both its 20-day and 50-day average, -1 under both, 0 between them
  -1  India VIX at 17.70 (+2.19 % on the day), above 80.7 % of its last year's closes
      rule: +1 at or under the 50th percentile of its last year and not up 10 % or more on the day, -1 at or above the 80th percentile or up 10 % or more on the day
The reading describes the session that has ended, by the rules printed beside it. It is not advice and not a prediction: a supportive backdrop does not make any one breakout work.

Sources and how fresh each one is
  NSE full bhavcopy                        sec_bhavdata_full_23112026.csv  session 2026-11-23; downloaded
  Nifty 500 list                           ind_nifty500list.csv            read; downloaded; the list is not dated, fetched 2026-11-24; 495 of its 501 rows are series EQ
  NSE 52-week high/low                     CM_52_wk_High_low_23112026.csv  session 2026-11-23; downloaded; the highs and lows as they stood before that session
  NSE bhavcopies for the 20-day average    sec_bhavdata_full_DDMMYYYY.csv  read; 20 of 20 sessions read; 2026-10-26 to 2026-11-23
  Yahoo daily bars, Nifty 50 (^NSEI)       yahoo-NSEI.json                 session 2026-11-23; downloaded
  Yahoo daily bars, India VIX (^INDIAVIX)  yahoo-INDIAVIX.json             session 2026-11-23; downloaded
  24 requests made in this run.
Wrote trader/market.json
```

In plain words: a broad down day (about 27 % of stocks rose, the Nifty 50
fell 1.75 %) inside a market that was still above its averages, with only
Information Technology clearly higher and the VIX in the upper fifth of its
year. Two votes for, two against, one neutral: mixed. Run again the same
evening, every line under "Sources" read "the copy saved in trader/cache"
and the run ended with `0 requests made in this run.`

## What the numbers can and cannot say

- **Splits and bonuses.** The bhavcopy's prices are not adjusted, and its
  `PREV_CLOSE` is the plain close of the session before (seen in the real
  files: CONCORDBIO on 2026-11-04 shows `PREV_CLOSE` 1,227.90 and a close of
  655.50). So a close more than 25 % (`split_drop_pct`) under the close
  before it is treated as a likely split or bonus: the stock is left out of
  that day's breadth, and out of the trend when the drop is inside the 20
  sessions, and it is named. This is a guess by size, not a check against
  NSE's corporate-action records: a smaller adjustment (a 1:4 bonus is 20 %)
  is not caught, and a real fall of more than 25 % is left out too.
- **The Nifty 500 list is today's list.** A check of an earlier date uses the
  constituents as they are now. A few rows of the list are not series EQ
  (REITs, trade-for-trade stocks) and are not counted.
- **The 20-day average is over the pack's calendar.** A Muhurat session is
  not one of the 20 sessions (the pack treats that day as closed).
- **Yahoo can be late.** When its daily close for the session is still
  empty, its last price of that session stands in and the line says so; when
  it has no bar for the session at all, the index is left out. An older
  close is never shown as the session's.
- **Index levels are points, not rupees.** This tool prints no rupee amount.
- **One day is one day.** Breadth swings from session to session; the
  reading is a label for one session's numbers, not a forecast of the next.

## Rules

- Every number comes from a file named under "Sources". Nothing is estimated
  across a missing file: the part is left out and the line says why.
- The reading is always given with its score, its votes and its rules. Never
  quote the label alone, and never call it a signal to buy.
- No predictions, no "the market will", no advice to trade or to stay out.
- Polite to the sources: one request a file, at least 0.5 s apart on one
  host, no retry on NSE's host, and nothing fetched twice in a day.

## What it never does

It never places an order, never talks to a broker, never changes the
watchlist, the plan or the orders, and never writes anywhere but
`trader/market.json` and `trader/cache/`. It does not read intraday data,
F&O data, FII or DII flows, or news. It does not rank stocks: that is
`/trader-scanner`.

## Safety

- Education and tooling, not financial advice. A reading is a count by stated rules, not a recommendation.
- Nothing in this pack places an order. Claude never logs in to a broker and never sends an order:
  the user enters every order themselves in their own broker app, after trader-risk passes that
  exact order and they have said an explicit yes to it in the chat.
- Practice mode by default (`"paper": true`): write the trade down first, risk nothing.
- Long side, cash-segment equity only. No F&O, margin (MTF) or short selling unless the config
  allows it and the user confirms.
- Broker passwords, PINs and OTPs never go in the chat.
