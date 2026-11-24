---
name: trader-backtest
description: >-
  Backtest for NSE intraday setups, after charges. Re-runs the pack's three
  entry rules (opening-range breakout, VWAP reclaim, gap and go) on every past
  session Yahoo still has, about 60 days of 5-minute bars, across the stocks
  you name, your watchlist or the whole Nifty 500, with the stop and target
  trader-exit would set and flat by 15:00 India time. Sizes each trade from a
  fixed rupee risk in whole shares, takes off the same Groww intraday charges
  as trader-journal, and reports per setup: trades, win rate, average R before
  and after charges, net P&L in rupees, largest loss, longest losing run, how
  trades ended (target, stop, time), results by fortnight and the best and
  worst symbols. Prints its caveats every time and says "too few trades to
  conclude anything" under 30 trades. It predicts nothing and recommends
  nothing. Use when the user says "backtest this", "does the opening range
  breakout work", "test the setups", "how did VWAP reclaim do", "test it on
  the Nifty 500", "is this setup worth it after charges", "which setup is
  better", or before trusting any setup with real money.
argument-hint: "[run SYMBOL... | run --universe nifty500|watchlist|FILE [--limit N] | show]"
---

# trader-backtest

The Time Machine replays one plan. The backtest asks a wider question: over
the last 60 days, on hundreds of stocks, what did each of the pack's setups
do once the charges were paid? It fetches each stock's 5-minute bars once,
walks every session bar by bar with no look-ahead, and adds it all up in
rupees. Most people who day trade lose money, and the numbers below are one
reason why: a setup with no edge before charges loses steadily after them.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
Use `python3` on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/backtest.py" run TCS INFY RELIANCE       # the symbols you name
python "${CLAUDE_SKILL_DIR}/backtest.py" run --universe watchlist    # trader/watchlist.json
python "${CLAUDE_SKILL_DIR}/backtest.py" run --universe nifty500 --limit 40
python "${CLAUDE_SKILL_DIR}/backtest.py" run --universe nifty500     # about 500 requests, several minutes
python "${CLAUDE_SKILL_DIR}/backtest.py" run --universe my-list.txt --setups orb,gap --risk 500
python "${CLAUDE_SKILL_DIR}/backtest.py" show                        # the saved result again
```

Other flags: `--setups orb,vwap,gap` (default all three), `--risk RUPEES`,
`--max-order RUPEES` (0 lifts the limit), `--target-r 2`, `--gap-pct 1`,
`--min-bars-below 3`, `--flat-by 15:00`, `--min-trades 30`, `--no-fetch`
(only what is already in today's cache), `--offline DIR` (saved
`<SYMBOL>-60d5m.json` files), `--pause SECONDS` (0.5 or more), `--trades`
(keep every simulated trade in the JSON), `--json`, `--quiet`, `--no-write`,
`--now "2026-11-24 10:00"` (India time). Every time is India Standard Time
(UTC+5:30), worked out by the tool, never the computer's own zone. Prices
are rupees.

## What it reads and writes

| file | what |
| --- | --- |
| Yahoo chart API | `<SYMBOL>.NS?range=60d&interval=5m`: one request per symbol, free, unofficial. Yahoo keeps 5-minute bars for about 60 days and no longer |
| NSE archive | `ind_nifty500list.csv`, for `--universe nifty500`: one request. Only series EQ is kept (a stock in series BE cannot be traded intraday) |
| `trader/cache/` | each symbol's answer for the day (`TCS-60d5m-2026-11-24.json.gz`) and the Nifty 500 list. A second run the same day asks nobody anything; a stopped run picks up where it left off. About 30 MB for the whole Nifty 500; yesterday's files are replaced |
| `trader/config.json` | optional: `max_loss_per_trade_inr` (500), `max_order_inr` (40,000), `no_entries_before` (09:30), `no_entries_after` (14:30), `flat_by` (15:00) and a `charges` block, read the way trader-journal reads it. Without the file, the pack's example config |
| `trader/watchlist.json` | the scanner's hits, for `--universe watchlist` |
| `trader/backtest.json` | the result: the settings, the data counts, each setup's numbers, the assumptions and the caveats |

It is polite to the free endpoints: at least 0.5 seconds between requests,
one retry at most (after a pause, on Yahoo's other host), and it stops by
itself when Yahoo rate limits five symbols in a row. Progress goes to stderr,
one line every 25 symbols.

## The three setups

The same rules as `/trader-entry`, long side only, on 5-minute bars:

- **orb**, opening-range breakout: the first completed bar after the
  15-minute range (the 9:15, 9:20 and 9:25 bars) that closes above the range
  high. Stop at the range midpoint, which is `/trader-exit`'s default.
- **vwap**, VWAP reclaim: the first completed bar that closes above the
  session VWAP right after three bars in a row closed at or under it. VWAP is
  the sum of typical price times volume over the sum of volume, from 9:15.
  Stop at the lowest low of those bars and the trigger bar.
- **gap**, gap and go: the day opens 1 percent or more above the previous
  session's last bar, then the same trigger as orb. Stop at the range low.

## How one trade is simulated

- **No look-ahead.** A signal on a bar's close enters at that close, to the
  paisa. Nothing after the entry bar decides the entry, the stop, the target
  or the size. One trade per setup, symbol and session: the first trigger
  between 9:30 and 14:30.
- **Stop and target** as trader-exit sets them: the stop rounded down to the
  paisa, the target at entry plus 2 times the risk. What trader-exit refuses
  (a stop at or above the entry, or closer than 0.05 percent) is no trade.
- **Size.** Whole shares from the fixed rupee risk: 500 divided by the risk
  a share, rounded down. No order above `max_order_inr`, so with the pack's
  40,000 limit most trades risk less than 500. Under one share: no trade.
- **The walk.** Each later bar in order. Low at or under the stop: out at the
  stop. Else high at or above the target: out at the target. A bar that
  touches both counts as the stop. Neither by 15:00: out at the close of the
  14:55 bar.
- **Charges.** One Groww equity-intraday round trip, the same table as
  `/trader-journal` (brokerage, STT, stamp duty, exchange, SEBI, IPFT, GST).
  R after charges is the net P&L over the rupees at risk.
- **Holes.** A session counts for a symbol only when Yahoo has every bar from
  9:15 to 14:55. The others are left out and counted, never patched.

## Steps

1. Ask what to test: named symbols, the watchlist, or the Nifty 500. For the
   Nifty 500 say first that it is about 500 requests and several minutes, and
   offer `--limit 40` as a quick look.
2. Run it. For the whole index, Claude can run it in the background and read
   the result when it ends. If it stops part way, run the same command again:
   the cache holds what was fetched.
3. Read the user the table for each setup: trades, win rate, average R before
   and after charges, net P&L. Then read the caveats, every time.
4. Under 30 trades say "too few trades to conclude anything" and stop there.
5. Never turn a result into advice. Do not rank the setups as a tip, and do
   not say what a setup will do next month. If the user wants to try one, the
   next step is practice mode and `/trader-journal`, not real money.

## Example: the Nifty 500, 26 August to 23 November 2026

A real run on Tuesday 2026-11-24 (an NSE holiday; the last session was
Monday). `run --universe nifty500 --limit 40` went first: 41 requests, 36
seconds. Then `run --universe nifty500` fetched the other 456 symbols in
about six minutes. NSE's file held 496 EQ rows; one was a placeholder Yahoo
does not know, and the tool now drops such rows. This is the second run that
day, straight from the cache, trimmed where marked:

```
Backtest on Yahoo's 5-minute bars (free, unofficial, NSE symbols), as of 2026-11-24 21:30 IST
Universe: the Nifty 500 as NSE lists it today: 495 symbols asked, 495 with usable bars, 0 failed.
Sessions: 28,746 symbol-days from 2026-08-26 to 2026-11-23; 897 more left out (a bar missing between 9:15 and 15:00).
  In 28,177 of them the 9:15 bar came with no volume (a quirk of Yahoo's 60-day data): the VWAP there starts at the 9:20 bar, so it is not quite the VWAP trader-entry works out live from today's bars.
Requests: 0 this run, 495 symbols read from today's cache (trader/cache).
Rules: in at the trigger bar's close (09:30 to 14:30), stop and target as trader-exit sets them (2R target), out at the close of the last bar before 15:00. A bar that touches both the stop and the target counts as the stop.
Sizing: ₹500.00 at risk a trade, whole shares, no order above ₹40,000.00. Charges: Groww's pricing page (groww.in/pricing), read 2026-11-24, equity intraday on NSE.

Opening-range breakout (orb): first close above the 15-minute range high; stop at the range midpoint
  Trades               15,224 in 493 symbols, 2026-08-26 to 2026-11-23
  Win rate             36.3 % after charges (a win is a net P&L above zero)
  Average R            +0.00R before charges, -0.24R after
  Net P&L              -₹861,881.01 (gross +₹57,957.33, charges ₹919,838.34, about ₹60.42 a trade)
  Largest loss         -₹561.28 (URBANCO, 2026-11-04 at 09:50)
  Longest losing run   44 trades in a row (in order of date, time, symbol)
  Ended by             target 19.2 %, stop 47.1 %, time (flat-by) 33.7 %
  By fortnight:
    16-31 Aug 2026     867 trades  win  32.4 %  -0.34R after charges  net -₹63,857.33
    1-15 Sep 2026    2,366 trades  win  34.3 %  -0.29R after charges  net -₹152,224.02
    16-30 Sep 2026   2,751 trades  win  34.5 %  -0.31R after charges  net -₹204,224.37
    1-15 Oct 2026    2,321 trades  win  33.0 %  -0.33R after charges  net -₹207,543.04
    16-31 Oct 2026   2,585 trades  win  39.1 %  -0.14R after charges  net -₹89,904.79
    1-15 Nov 2026    2,851 trades  win  39.5 %  -0.15R after charges  net -₹88,057.60
    16-30 Nov 2026   1,483 trades  win  39.4 %  -0.15R after charges  net -₹56,069.86
  Best symbols by net P&L (a handful of trades each: mostly luck, not a pattern):
    FINCABLES     28 trades  net +₹5,296.47
    IFCI          16 trades  net +₹4,283.77
    ... (3 more)
  Worst symbols by net P&L (a handful of trades each: mostly luck, not a pattern):
    J&KBANK       36 trades  net -₹9,308.17
    TATACHEM      37 trades  net -₹7,319.09
    ... (3 more)
  199 stopped trades met a bar that OPENED under the stop: counted at the stop price here, worse in real life.
  12,544 of these trades were cut by the ₹40,000.00 order limit, so they risked less than ₹500.00.
  No trade: 13,464 no trigger, 58 under one share.

VWAP reclaim (vwap): first close back above VWAP after 3 bars at or under it; stop at the pullback low
  Trades               21,947 in 493 symbols, 2026-08-26 to 2026-11-23
  Win rate             33.4 % after charges (a win is a net P&L above zero)
  Average R            -0.04R before charges, -0.37R after
  Net P&L              -₹1,483,492.16 (gross -₹147,938.38, charges ₹1,335,553.78, about ₹60.85 a trade)
  Largest loss         -₹561.27 (IDEA, 2026-10-05 at 10:10)
  Longest losing run   48 trades in a row (in order of date, time, symbol)
  Ended by             target 20.3 %, stop 50.5 %, time (flat-by) 29.3 %
  ... (fortnights and symbols)

Gap and go (gap): opens 1 % or more above the previous close, then a close above the range high; stop at the range low
  Trades               1,088 in 443 symbols, 2026-08-27 to 2026-11-23
  Win rate             39.2 % after charges (a win is a net P&L above zero)
  Average R            -0.01R before charges, -0.13R after
  Net P&L              -₹65,306.66 (gross -₹7,898.21, charges ₹57,408.45, about ₹52.77 a trade)
  Largest loss         -₹561.06 (LEMONTREE, 2026-11-23 at 13:55)
  Longest losing run   19 trades in a row (in order of date, time, symbol)
  Ended by             target 6.1 %, stop 21.2 %, time (flat-by) 72.7 %
  ... (fortnights and symbols)

Read this before the numbers above:
  - About 60 sessions is one market phase. A setup that did well or badly in it proves little about the next one.
  - Fills are optimistic: every entry is at the trigger bar's close and every stop and target at its exact price. A real order slips, most of all a stop in a fast market or after a gap.
  - Survivorship: the Nifty 500 list is today's list. Stocks that fell out of it during these weeks are not tested.
  - Yahoo's 5-minute bars for NSE have holes and can be wrong. Sessions with a missing bar were left out, not repaired, and the closing-auction price is not in them.
  - Testing several setups and then picking the best one overstates it: some of any difference between them is luck.
  - The totals add up every simulated trade as if each stood alone. Nobody can take hundreds of trades a day; this is not the result of one account.
Numbers from the past, not a prediction and not advice. Most people who day trade lose money. Education and tooling, not financial advice.
Wrote trader/backtest.json.
```

What that run says, and no more: in those 60 sessions none of the three
setups made money before charges on this universe (average R between -0.04
and 0.00), and the charges, about ₹60 a round trip on an order of up to
₹40,000, turned that into a steady loss. With `--max-order 0` the same
cached bars gave -0.15R (orb), -0.21R (vwap) and -0.13R (gap) after charges:
bigger orders dilute the brokerage, they do not create an edge.

On a single stock the tool says what a handful of trades is worth. The same
run for `TCS` alone printed, under the gap setup:

```
  Only 7 trades: too few trades to conclude anything. Under 30, a good or a bad result is mostly luck.
```

## Rules

- Every number is the past on Yahoo's bars. It is not a prediction, and a
  setup that lost here is not proven useless any more than one that won
  would be proven good.
- Read the caveats to the user every time: one market phase, optimistic
  fills, survivorship, holes in the data, and that picking the best one of
  several setups overstates it.
- "Too few trades to conclude anything" is the whole answer under 30 trades.
- Do not tune `--gap-pct`, `--min-bars-below` or `--target-r` until a number
  looks good: every extra try makes the best result more of an accident.
- The best and worst symbols are a few dozen trades each at most. Never
  present them as stocks the setup "works on".
- The previous close for gap is the last 5-minute bar of the day before, not
  NSE's official close. Yahoo's 60-day data has no volume on the 9:15 bar,
  so the backtest's VWAP starts at 9:20. Both are said in the output.

## What it never does

It never places an order, never logs in to a broker, never fetches more than
once per symbol per day, never fills a hole in the data, never looks past
the entry bar to decide an entry, and never calls a result advice. A user
who wants to act on a setup still goes through `/trader-entry`,
`/trader-exit`, `/trader-risk` and their own explicit yes, in practice mode
first.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Nothing in this pack places an order. Claude never logs in to a broker and never sends an order:
  the user enters every order themselves in their own broker app, after trader-risk passes that
  exact order and they have said an explicit yes to it in the chat.
- Practice mode by default (`"paper": true`): write the trade down first, risk nothing.
- Long side, cash-segment equity only. No F&O, margin (MTF) or short selling unless the config
  allows it and the user confirms.
- Broker passwords, PINs and OTPs never go in the chat.
