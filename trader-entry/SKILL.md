---
name: trader-entry
description: >-
  Entry Timer for the Indian market (NSE). Watches a setup bar by bar and tells
  you the moment to get in. Reads free Yahoo 1-minute or 5-minute bars for NSE
  symbols (RELIANCE, TCS, M&M), builds the opening range (the first 15
  minutes, 9:15 to 9:30 India time, by default) and calls the entry on the
  first completed bar that CLOSES above the range high, or above a level you
  give it (like a 20-day high from the scanner), with an optional volume
  check. Two more setups: VWAP reclaim (the first close back above the
  session VWAP after three bars under it) and gap and go (a day that opens 1
  percent or more above the previous close, then closes above its range
  high), each with its natural stop. Says waiting and how far, triggered with
  the time and the price in rupees, expired, or not open yet, and writes
  trader/entry.json for /trader-exit. Long side only. It never places an
  order. Use when the user says "tell me when to get in", "watch RELIANCE",
  "has it broken out", "did it break the opening range", "did it reclaim
  VWAP", "is it a gap and go", "entry signal", "time my entry", "check my
  watchlist", or after /trader-scanner and /trader-news, before /trader-exit.
argument-hint: "[SYMBOL] [--setup orb|level|vwap|gap] [--interval 5m|1m]"
---

# trader-entry

The Entry Timer turns a setup into one moment: the bar that confirms it. It
reads bars, 1-minute or 5-minute, and waits for each one to finish. A price
that pokes above the high in the middle of a bar is not an entry. A bar that
CLOSES above it is. Then it says the time and the price, writes them down for
the Exit Planner, and stops. It never buys anything. Day trading loses most
people money, and a clean signal does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
Use `python3` on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/entry.py" check TCS                    # 15-minute opening range, 5-minute bars
python "${CLAUDE_SKILL_DIR}/entry.py" check TCS --write            # also save trader/entry.json
python "${CLAUDE_SKILL_DIR}/entry.py" check TCS --setup level --level 2460
python "${CLAUDE_SKILL_DIR}/entry.py" check TCS --interval 1m --min-volume-x 1.5
python "${CLAUDE_SKILL_DIR}/entry.py" check TCS --setup vwap             # first close back above VWAP
python "${CLAUDE_SKILL_DIR}/entry.py" check TCS --setup gap --gap-pct 1  # gap up, then above the range high
python "${CLAUDE_SKILL_DIR}/entry.py" watch TCS --until 14:30      # one line per closed bar, stops on the trigger
python "${CLAUDE_SKILL_DIR}/entry.py" watchlist                    # every hit in trader/watchlist.json
```

Symbols are NSE symbols: `RELIANCE`, `HDFCBANK`, `M&M`, `BAJAJ-AUTO` (quote
`"M&M"` in a shell). Yahoo calls them `RELIANCE.NS`; the tool adds the `.NS`
itself and drops it again in what it prints and writes. Prices are rupees.

Other flags: `--or-minutes N` (default 15), `--min-bars-below N` (vwap,
default 3), `--gap-pct X` (gap, default 1.0), `--json`, `--offline FILE` (a
saved Yahoo chart JSON, `.json` or `.json.gz`, or a folder of
`<SYMBOL>.json`), `--now "2026-11-23 09:45"` (India time) to replay a day.
`watch` with `--now` or `--offline` runs on a simulated clock and replays the
day bar by bar without waiting.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/watchlist.json` | the scanner's hits (`hits[].symbol`), for `watchlist` |
| `trader/config.json` | `no_entries_before` (09:30) and `no_entries_after` (14:30), India time. Optional |
| Yahoo chart API | today's bars, `TCS.NS?range=1d&interval=5m` (or `1m`): free, unofficial, can lag. NSE data on Yahoo is typically delayed |
| `trader/entry.json` | with `check --write`, or when `watch` triggers: symbol, setup, status, time, bar, price, level, opening range. For vwap and gap also `stop_ref`: the natural stop and the `/trader-exit` flag that sets it |

## Clock and calendar

- Every time is India Standard Time (UTC+5:30, no daylight saving). The tool
  works it out itself and never uses the computer's own time zone.
- The session is the NSE regular session, 09:15 to 15:30: 75 five-minute
  bars, the first at 9:15 and the last at 15:25. There are no half days.
- The pre-open session (09:00 to 09:15) is ignored. So is the Muhurat special
  session: that day is treated as closed.
- Weekends and the 2026 NSE holiday list are closed days. NSE had not
  published 2027 when this was written: for another year the tool knows only
  weekends, and says so.

## The rule

- **Opening range (orb).** The high and low of the first 15 minutes: the
  9:15, 9:20 and 9:25 five-minute bars, or fifteen 1-minute bars. The level
  to beat is the range high.
- **Level.** A price you give, like a 20-day high from `/trader-scanner`.
- **The trigger** is the first completed bar after the range that CLOSES
  above the level. A wick above is not a trigger. A close exactly at the
  level is not above it.
- **The time** is that bar's close time (the 9:40 bar closes at 9:45 AM).
  The price is its close, to the paisa.
- **Only closed bars count.** A bar is closed when the clock passes its start
  plus 5 (or 1) minutes and Yahoo has caught up to that moment (it waits a
  minute at most). Yahoo's live tick, like 10:49:45, is never a bar.
- **Volume, if you want it.** `--min-volume-x 1.5` also needs the trigger
  bar's volume at 1.5 times the range bars' average. The 9:15 bar holds the
  opening rush, so 1.0 is already strict.
- **Time limits.** No entries before 9:30 (the range is not set until then)
  or after 14:30 (config). Brokers square off intraday positions themselves
  from about 15:10, so a late entry has little room.
- **What it says.** Waiting (and how far the last close is from the level),
  triggered, expired, or no data. Before 9:15: not open yet. From 9:15 to
  9:30: the range is still forming. Weekends and NSE holidays: the market is
  closed, with the next open.

## Two more setups

Both are long side only, trigger on a completed bar's close like the
opening range, obey the same time limits and write the same
`trader/entry.json`.

- **VWAP reclaim (`--setup vwap`).** VWAP is the session's volume-weighted
  average price, worked out from the day's own bars from 9:15: the sum of
  typical price times volume, over the sum of volume. Typical price is
  (high + low + close) / 3. The trigger is the first completed bar that
  closes above VWAP right after at least 3 bars in a row closed at or under
  it (`--min-bars-below N`). A close exactly at VWAP is not above it.
  With `--min-volume-x X` the trigger bar also needs X times the average
  volume of the session's earlier bars.
- **Its natural stop** is the pullback low: the lowest low of those bars at
  or under VWAP and of the trigger bar.
- **Yahoo's quirks.** A bar with no volume keeps its close but adds nothing
  to VWAP, and the tool says how many it left out. A missing bar breaks the
  run of bars under VWAP, because its close is unknown. Yahoo's NSE bars
  near 15:15 to 15:25 are often missing: that is after the last entry time,
  so it changes nothing. When no bar so far has any volume the tool says
  VWAP could not be computed, and calls nothing.
- **One more quirk worth knowing.** Today's bars (`range=1d`, what a live
  check reads) carry the 9:15 bar's volume. Yahoo's multi-day data, which a
  replay of a past day reads, usually does not, so the same morning can give
  a slightly different VWAP when you replay it later.
- **Gap and go (`--setup gap`).** The day's open (the open of the 9:15 bar)
  is at least 1 percent above the previous close (`--gap-pct X`). Then the
  trigger is the first completed bar after the opening range that closes
  above the opening-range high: the same bar the orb setup calls, on gap
  days only. A day that opens with a smaller gap is expired at once.
- **Its natural stop** is the opening-range low.
- **The previous close** is Yahoo's own on a live check. On a replayed day
  it is the close of the previous session's last 5-minute bar. Neither is
  NSE's official close, which averages the last half hour.
- **The stop is still set by `/trader-exit`.** That tool only has a default
  stop for the opening-range setup, so for these two the Entry Timer prints
  the exact flag and saves it as `stop_ref` in `trader/entry.json`:
  `--stop price:X` for vwap, `--stop or-low` for gap.

## Steps

1. Get the symbol from the user, or run `entry.py watchlist` after
   `/trader-scanner`. Check `/trader-news` for it first.
2. Run `entry.py check SYMBOL`. Tell the user the status in one line:
   waiting and how far, triggered at what time and price, or expired.
3. To wait for it, Claude can run `entry.py watch SYMBOL --until 14:30` in
   the background, or `entry.py check SYMBOL --write` on a `/loop` every 5
   minutes. `watch` wakes just after each bar closes, prints one line per
   bar, and exits 0 on the trigger (it writes `trader/entry.json`) or 1 when
   the setup expires. Keep the computer awake while it watches: a sleeping
   laptop pauses it, and it catches up, late, when it wakes.
4. When it triggers, say the time and the price, and that nothing was placed.
   If the trigger is more than a bar old, say that too: a late entry is a
   different trade.
5. Hand off to `/trader-exit` to set the stop and the target BEFORE anything
   is bought.

## Example: TCS on Monday 2026-11-23

These are runs of this tool on Yahoo's five-minute bars for `TCS.NS`,
replayed with `--now`. The opening range is 2,405.00 to 2,433.70. At 9:42
the 9:40 bar is still open, so `entry.py check TCS --now "2026-11-23 09:42"`
waits:

```
TCS  Mon 2026-11-23 09:42 IST  opening-range breakout (15-minute range) on 5-minute bars (Yahoo, free, can lag)
Opening range 9:15-9:30: high ₹2,433.70, low ₹2,405.00 (3 of 3 bars).
WAITING  last close ₹2,429.00 (9:35 bar), ₹4.70 under the range high ₹2,433.70 (-0.19%).
The 9:40 bar is still forming (₹2,443.50 so far, closes 9:45 AM); it does not count until it closes.
```

At 9:45 the bar has closed (`--now "2026-11-23 09:45" --write`):

```
TCS  Mon 2026-11-23 09:45 IST  opening-range breakout (15-minute range) on 5-minute bars (Yahoo, free, can lag)
Opening range 9:15-9:30: high ₹2,433.70, low ₹2,405.00 (3 of 3 bars).
TRIGGERED 9:45 AM at ₹2,443.50: the 9:40 bar (bar 5) closed above the range high ₹2,433.70 (+0.40%).
This is the moment to get in, if you take the trade. Nothing was placed.
Next: set the stop and target BEFORE you buy: /trader-exit (exit.py plan --from trader/entry.json).
Wrote trader/entry.json (status triggered).
```

With `--min-volume-x 1.5` the 9:40 bar is too thin (1.33x the range's volume)
and the trigger moves to 10:10 AM at ₹2,480.30 (the 10:05 bar, 2.05x).

The same morning RELIANCE never closed above its range high, and at 14:31 it
says so:

```
EXPIRED  no close above the range high ₹1,389.90 by 14:30, the last time for a new entry.
No entry on this setup today. Nothing was placed.
```

And on Tuesday 2026-11-24:

```
MARKET CLOSED  the NSE is closed today (NSE holiday: Guru Nanak Jayanti).
Next open: 2026-11-25 09:15 India time.
```

## Example: VWAP reclaim and gap and go, the same Monday

`entry.py check TCS --setup vwap --now "2026-11-23 09:40"`, on the same
saved bars. TCS closed under VWAP at 9:20, 9:25 and 9:30, then back above:

```
TCS  Mon 2026-11-23 09:40 IST  VWAP reclaim on 5-minute bars (Yahoo, free, can lag)
Session VWAP ₹2,416.16 from 9:15 on the day's own bars.
TRIGGERED 9:40 AM at ₹2,429.00: the 9:35 bar (bar 4) closed above VWAP ₹2,416.16 (+0.53%) after 3 bars at or under it.
This is the moment to get in, if you take the trade. Nothing was placed.
Natural stop for this setup: ₹2,401.10, the pullback low (the lowest low of the 3 bars at or under VWAP and the trigger bar).
Next: set the stop and target BEFORE you buy: /trader-exit (exit.py plan --from trader/entry.json --stop price:2401.10).
```

`entry.py check TCS --setup gap --now "2026-11-23 09:45"`:

```
TCS  Mon 2026-11-23 09:45 IST  gap and go (15-minute range) on 5-minute bars (Yahoo, free, can lag)
Gap: opened at ₹2,420.00, +1.84% from the previous close ₹2,376.20 (Yahoo's previous close). Gap and go needs +1.00% or more.
Opening range 9:15-9:30: high ₹2,433.70, low ₹2,405.00 (3 of 3 bars).
TRIGGERED 9:45 AM at ₹2,443.50: the 9:40 bar (bar 5) closed above the range high ₹2,433.70 (+0.40%).
This is the moment to get in, if you take the trade. Nothing was placed.
Natural stop for this setup: ₹2,405.00, the opening-range low.
Next: set the stop and target BEFORE you buy: /trader-exit (exit.py plan --from trader/entry.json --stop or-low).
```

RELIANCE opened only 0.25% up that morning, so its gap check ended at the
first bar:

```
EXPIRED  it opened at ₹1,381.00, +0.25% from the previous close ₹1,377.50: gap and go needs +1.00% or more.
No entry on this setup today. Nothing was placed.
```

A live check of INFY for that session, run the next day (so on Yahoo's
5-day data, without the 9:15 bar's volume), found the gap and go at 9:50 AM
at ₹1,123.00 and no VWAP reclaim. On the bars saved as `range=1d` the same
morning does show a reclaim at 9:40 AM: the quirk above, in practice.

`/trader-backtest` runs all three setups over the last 60 days and takes the
charges off. Read it before trusting any of them.

## Rules

- Bar by bar, on free Yahoo 1-minute or 5-minute bars. They are unofficial
  and can lag. Say so when a minute matters, and tell the user to trust the
  price in their own broker app over this one.
- A signal is information, not an order, and not a promise. Nothing here
  promises winning trades or money. The risk figures later in the pipeline
  are before charges (brokerage, STT, exchange fees and GST):
  `/trader-journal` and `/trader-backtest` take those off.
- Long side only. If the user wants a short, say that this pack does not
  plan shorts.
- One trigger per setup per day: the first close above. Later closes do not
  reset it.
- Never call an entry early because a bar looks like it will close above.

## What it never does

It never places an order, never sizes a position, never sets the stop (that
is `/trader-exit`), never moves the level after the fact, and never counts a
bar that has not closed. A background watch only writes
`trader/entry.json`: nobody is there to say yes to anything more.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Nothing in this pack places an order. Claude never logs in to a broker and never sends an order:
  the user enters every order themselves in their own broker app, after trader-risk passes that
  exact order and they have said an explicit yes to it in the chat.
- Practice mode by default (`"paper": true`): write the trade down first, risk nothing.
- Long side, cash-segment equity only. No F&O, margin (MTF) or short selling unless the config
  allows it and the user confirms.
- Broker passwords, PINs and OTPs never go in the chat.
