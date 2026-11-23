---
name: trader-journal
description: >-
  Practice Journal for NSE intraday trades, with a charges calculator. Writes
  every practice (or real) trade down, then scores it against what the price
  really did on Yahoo's 5-minute bars: target first, stop first, or flat at
  your flat-by time, with the R multiple, the gross P&L, the itemised charges
  of an intraday round trip (brokerage, STT, stamp duty, exchange, SEBI, IPFT,
  GST) and the net P&L in rupees. Keeps a scoreboard over weeks: win rate,
  average R, total charges, net P&L, largest loss, longest losing run, by
  setup and by symbol, and says plainly when there are too few trades to
  conclude anything. Imports the trades trader-broker logged. It never places
  an order. Use when the user says "how am I doing", "score my trades",
  "journal this trade", "practice results", "what did that trade make", "how
  much are the charges", "what would the charges be", "is this worth it after
  charges", "show my scoreboard", or after a practice trade's day is over.
argument-hint: "[add | import | score | report | charges --buy P --sell P --qty N | charges --plan | list]"
---

# trader-journal

The pack's default is practice mode (`"paper": true`): you say yes to a
ticket, place nothing, and it is written down. The Practice Journal is what
scores those trades afterwards. For each one it reads the day's real
5-minute bars and works out how a stop and a target at those prices would
have ended, takes the charges off, and keeps the running scoreboard, so you
see honest results over weeks before you risk money. Most people who day
trade lose money; SEBI's own study found 93% of individual F&O traders lost
money over FY22 to FY24. A journal does not change that. It only shows it.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
Use `python3` on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/journal.py" add                      # from trader/plan.json + trader/orders.json
python "${CLAUDE_SKILL_DIR}/journal.py" add --symbol TCS --date 2026-11-23 --entry 2443.50 --entry-time "9:45 AM" --stop 2419.35 --target 2467.65 --qty 15 --flat-by 15:00
python "${CLAUDE_SKILL_DIR}/journal.py" import                   # every entry trader-broker logged as placed
python "${CLAUDE_SKILL_DIR}/journal.py" score                    # every open trade whose session is over
python "${CLAUDE_SKILL_DIR}/journal.py" score --symbol TCS --date 2026-11-23 --exit-price 2465.00   # a fill you report
python "${CLAUDE_SKILL_DIR}/journal.py" report                   # the scoreboard
python "${CLAUDE_SKILL_DIR}/journal.py" charges --buy 2443.50 --sell 2467.65 --qty 15
python "${CLAUDE_SKILL_DIR}/journal.py" charges --plan           # the current plan, after charges
python "${CLAUDE_SKILL_DIR}/journal.py" list
```

Other flags: `--now "2026-11-23 16:00"` (India time), `--offline DIR` (saved
Yahoo chart files, no network), `--again` (score afresh), `--exit-time`,
`--auto-squareoff`, `--real` / `--practice` and `--setup` on `add`,
`report --since 2026-11-01 --only practice|real --json`, `charges --json`.

Symbols are NSE symbols (`TCS`, `RELIANCE`, `M&M`); prices are rupees; every
time is India Standard Time (UTC+5:30, no daylight saving), worked out by the
tool and never taken from the computer's own time zone. The NSE session is
09:15 to 15:30; the pre-open and the Muhurat session are ignored, and the
2026 NSE holiday list is built in (a trade dated on a holiday is refused).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | `add`, `charges --plan`: symbol, date, entry, entry time, stop, target, flat by, setup (from trader-exit) |
| `trader/orders.json` | `add`, `charges --plan`: the quantity, practice or real, and the price the order was sized at (from trader-budget) |
| `trader/state/broker.json` | `import`: every buy trader-broker logged as placed, with the buy fill if you reported one. `score`: a sell fill you reported is the real exit |
| `trader/config.json` | `flat_by`, `paper`, and an optional `"charges"` block with your own broker's rates |
| Yahoo chart API | `score`: one request a symbol for the day's 5-minute bars, asked for as `TCS.NS`. Free, unofficial, can lag, keeps about 60 days |
| `trader/journal.json` | the only file it writes: every trade, open or scored, with the exit, R, itemised charges and net P&L |

A trade is one symbol, one date, one entry time. Adding or importing the same
one twice changes nothing.

## How a trade is scored

`entry_time` is the close of the entry bar (9:45 AM is the 09:40 bar), so the
walk starts at the next bar. Bar by bar, the stop is checked before the
target:

| what the bars did | exit | outcome |
| --- | --- | --- |
| a bar's low touches the stop | at the stop | `stop` |
| a bar's high touches the target | at the target | `target` |
| one bar touches both | at the stop: the pack's conservative rule | `stop` |
| neither by flat_by | the close of the last completed bar before flat_by (15:00 means the close of the 14:55 bar) | `flat` |

- **Simulated.** That exit is what a stop and a target at those prices would
  have done on Yahoo's 5-minute bars; a real fill can be worse (a gap, a fast
  market, a stop that slips). When a bar opens under the stop the tool still
  counts the stop price and says so in a note.
- **As filled.** If you report the real exit (`--exit-price`, or a sell
  recorded with `ticket.py fill` in trader-broker's log for all the shares),
  that price is used instead and the trade is marked "as filled". Add
  `--auto-squareoff` when the broker closed the position for you.
- **Left open, with the reason.** A trade whose session is not over (before
  15:30 India time). A day Yahoo has no bars for, or a missing bar anywhere
  before the exit: nothing is guessed across a hole. A day more than 58 days
  back. Yahoo's NSE bars are often empty from 15:15 to 15:25, which is why
  `flat_by` defaults to 15:00.
- **An entry off the 5-minute grid** (9:47, from the broker log) starts in
  the bar that holds it, and in that one bar only the stop counts, because
  part of the bar happened before the entry.
- **R** = (exit - entry) / (entry - stop), before charges. The scoreboard
  also shows R after charges. A **win** is a net P&L above zero, so a trade
  that closed a little green can still be a loss.

## Charges

One intraday (MIS) round trip on NSE, from Groww's pricing page
(groww.in/pricing), read 2026-11-24:

| charge | rate |
| --- | --- |
| Brokerage | ₹20 or 0.1 % of the order value per executed order, whichever is lower, minimum ₹5. The buy and the sell are two orders |
| STT | 0.025 % of the sell value |
| Stamp duty | 0.003 % of the buy value |
| Exchange transaction charge (NSE) | 0.00307 % of both sides |
| SEBI turnover charge | 0.0001 % of both sides |
| IPFT charge (NSE) | 0.0001 % of both sides |
| GST | 18 % of brokerage + exchange + SEBI + IPFT |
| DP charges | ₹0 for intraday |
| Auto square-off by the broker | ₹50 a position + 18 % GST, only when the broker closes it (`--auto-squareoff`) |

They live in one `CHARGES` table at the top of `journal.py`. Another broker's
rates go in `trader/config.json`, any line you want to change:

```json
{"charges": {"brokerage_flat": 0, "brokerage_pct": 0, "brokerage_min": 0}}
```

Lines: `brokerage_flat`, `brokerage_pct`, `brokerage_min`, `stt_sell_pct`,
`stamp_buy_pct`, `exchange_pct`, `sebi_pct`, `ipft_pct`, `gst_pct`,
`dp_charge`, `auto_squareoff_fee`. Rates change: check your broker's pricing
page now and then. Every line is worked out in exact decimal rupees and
rounded half up to the paisa. Your contract note can differ by a few paise,
because the exchange and the broker round in their own way (STT, for one, is
rounded to the rupee).

## Steps

1. Right after a practice yes (trader-broker `approve` and `result`), run
   `journal.py import`, or `journal.py add` straight from the plan. Read back
   the line it journaled.
2. Before a trade, `journal.py charges --plan` answers "is this worth it
   after charges": the net result at the target, at the stop, the reward:risk
   after charges and the break-even move. Say the numbers; do not call the
   trade good or bad.
3. After 15:30 India time, run `journal.py score`. Read back each outcome
   with the word "simulated" or "as filled", and any trade left open with its
   reason.
4. If the user really placed the trade and knows the exit, score that one
   with `--exit-price` (and `--auto-squareoff` if the broker closed it).
5. `journal.py report` for "how am I doing". Read the numbers as they are.
   Under 20 scored trades, say the line it prints: too few to conclude
   anything.
6. Never turn a scoreboard into a forecast, and never suggest going live
   because a few practice trades won.

## Example: TCS and INFY on Monday 2026-11-23

Real runs of this tool on Yahoo's 5-minute bars for that session (the saved
charts are in `tests/fixtures/trader-journal/`).

TCS: opening range 2,405.00 to 2,433.70, entry 2,443.50 at 9:45 AM (the
09:40 bar, bar 5), midpoint stop 2,419.35, 1R target 2,467.65, 15 shares.
`journal.py add`, then `journal.py score --now "2026-11-23 16:00"`:

```
TCS 2026-11-23 practice: bought 15 at ₹2,443.50 at 09:45, stop ₹2,419.35, target ₹2,467.65, flat by 15:00
  TARGET, simulated: the 10:05 bar reached the target before any bar touched the stop.
  Out at ₹2,467.65: +1.00R, gross +₹362.25, charges ₹60.38, net +₹301.87.
```

The 10:00 bar's high was 2,466.00, under the target; the 10:05 bar's high
was 2,482.00. No bar from 09:45 on went near the stop.

INFY the same morning: opening range 1,107.70 to 1,120.50, entry 1,123.00 at
9:50 AM (the 09:45 bar, bar 6), midpoint stop 1,114.10, trader-exit's default
2R target 1,140.80, 33 shares from trader-budget:

```
INFY 2026-11-23 practice: bought 33 at ₹1,123.00 at 09:50, stop ₹1,114.10, target ₹1,140.80, flat by 15:00
  STOP, simulated: the 10:55 bar touched the stop first.
  Out at ₹1,114.10: -1.00R, gross -₹293.70, charges ₹60.34, net -₹354.04.
```

INFY got as high as 1,132.40 (the 10:15 bar), never reached 1,140.80, and
the 10:55 bar's low was exactly the stop, 1,114.10. A touch counts.

`journal.py charges --buy 2443.50 --sell 2467.65 --qty 15`:

```
Charges for one intraday round trip on NSE: buy 15 at ₹2,443.50, sell 15 at ₹2,467.65
  Buy value            ₹36,652.50
  Sell value           ₹37,014.75
  Brokerage                ₹40.00  buy ₹20.00 + sell ₹20.00 (₹20.00 or 0.1 % of the order value, whichever is lower, minimum ₹5.00, per order)
  STT                       ₹9.25  0.025 % of the sell value
  Stamp duty                ₹1.10  0.003 % of the buy value
  Exchange (NSE)            ₹2.26  0.00307 % of both sides (₹73,667.25)
  SEBI turnover             ₹0.07  0.0001 % of both sides
  IPFT (NSE)                ₹0.07  0.0001 % of both sides
  GST                       ₹7.63  18 % of brokerage + exchange + SEBI + IPFT (₹42.40)
  DP charges                ₹0.00  none for intraday
  Total charges            ₹60.38
  Gross P&L               ₹362.25  (₹2,467.65 - ₹2,443.50) x 15
  Net P&L                 ₹301.87  charges took 16.7 % of your gross profit
  Break-even         sell at ₹2,447.52 or higher: the price has to rise ₹4.02 a share (0.16 %) just to cover the charges
Rates: Groww's pricing page (groww.in/pricing), read 2026-11-24, equity intraday on NSE.
Each line is rounded to the paisa; your contract note can differ by a few paise.
```

`journal.py charges --plan`, with the TCS plan as the current one:

```
Charges on the plan: TCS, buy 15 at ₹2,443.50, stop ₹2,419.35, target ₹2,467.65
  If the target hits  gross +₹362.25, charges ₹60.38, net +₹301.87
  If the stop hits    gross -₹362.25, charges ₹60.18, net -₹422.43
  Reward : risk       1.00 before charges, 0.71 after charges (net ₹301.87 : net ₹422.43)
  Break-even          ₹2,447.52: the price has to rise ₹4.02 a share (0.16 %) before the trade makes anything
```

A 1R plan on paper is 0.71 after charges. `journal.py report` with those two
trades in the journal:

```
Practice Journal scoreboard: 2 scored trades, 2026-11-23 to 2026-11-23
  2 practice, 0 real. Exits: 2 simulated, 0 as filled. Still open: 0.

  Outcomes            1 target, 1 stop, 0 flat
  After charges       1 win, 1 loss (a win is a net P&L above zero)
  Win rate            50.0 %
  Average R           +0.00R before charges, -0.19R after
  Total R             +0.00R before charges, -0.38R after
  Gross P&L           +₹68.55
  Total charges       ₹120.72
  Net P&L             -₹52.17
  Largest loss        -₹354.04 (INFY 2026-11-23, entry 09:50)
  Longest losing run  1 trade in a row
  Charges vs gross    charges took 176.1 % of your gross profit

By setup:
  orb              2 trades  1 won, 1 lost  +0.00R  gross +₹68.55  charges ₹120.72  net -₹52.17

By symbol:
  INFY             1 trade   0 won, 1 lost  -1.00R  gross -₹293.70  charges ₹60.34  net -₹354.04
  TCS              1 trade   1 won, 0 lost  +1.00R  gross +₹362.25  charges ₹60.38  net +₹301.87

Only 2 scored trades: too few to conclude anything. Under 20 trades a good or a bad run is mostly luck.
Simulated = what a stop and a target at those prices would have done on Yahoo's 5-minute bars; a real fill can be worse.
Numbers only, no prediction. Most people who day trade lose money. Education and tooling, not financial advice.
```

One win and one loss, ₹68.55 ahead before charges and ₹52.17 behind after
them.

## Rules

- Every number comes from the journal or from the bars. Nothing is estimated
  across a hole in the data: the trade stays open and says why.
- A simulated exit is always labelled simulated, and a reported one "as
  filled". Never present a simulated result as money made.
- Numbers only. No predictions, no "you are profitable", no advice to size up
  or to go live.
- Under 20 scored trades the scoreboard proves nothing, and it says so.
- Charges are the published rates on the date above, not a contract note.
- Long side only, like the rest of the pack.

## What it never does

It never places an order, never talks to a broker, never changes the plan,
the orders or the broker log, and never writes anywhere but
`trader/journal.json`. It does not predict the next trade from the last
ones, does not model slippage, and does not treat practice results as proof
of anything: a practice fill at the stop price is kinder than a real market.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Nothing in this pack places an order. Claude never logs in to a broker and never sends an order:
  the user enters every order themselves in their own broker app, after trader-risk passes that
  exact order and they have said an explicit yes to it in the chat.
- Practice mode by default (`"paper": true`): write the trade down first, risk nothing.
- Long side, cash-segment equity only. No F&O, margin (MTF) or short selling unless the config
  allows it and the user confirms.
- Broker passwords, PINs and OTPs never go in the chat.
