---
name: trader-risk
description: >-
  Risk Manager for NSE intraday trades, in rupees. Checks every proposed
  order against your limits and stops anything over them, before it can be
  shown to you as a ticket. For an intraday entry: a stop is required, the
  loss at the stop must fit your max loss per trade (measured from the higher
  of the plan's entry and a fresh price), reward:risk must meet your minimum,
  the day's total risk must fit your daily loss limit, no more than your
  trades per day, and no new entries outside your entry window (India time)
  or when NSE is closed. It also keeps the basics: blocked symbols, order
  size, position cap, cash buffer, margin, short sales, F&O contracts,
  malformed orders, duplicates and a daily order limit. On top sits an
  account-level circuit breaker that reads what really happened (the scored
  trades in the Practice Journal, after charges) and stops NEW entries once
  the day's realised loss reaches the daily loss limit, after a losing streak
  (3 in a row by default), within 30 minutes of a losing exit, for a second
  entry in a stock that was stopped out today, or at an optional weekly loss
  limit. Getting out is never blocked by a day limit or by the breaker. Prints
  PASS or BLOCK with the reason and records the verdicts so trader-broker only
  shows an order that passed. Use when the user says "check my order", "is
  this safe", "risk check", "does this break my rules", "can I take this
  trade", "how much have I risked today", "can I still trade today", "am I on
  a losing streak", "breaker status", "how much of my daily limit is left", or
  before /trader-broker, always.
argument-hint: "[path to orders.json]"
---

# trader-risk

The Risk Manager is the gate between a proposal and a ticket. Every order
written by trader-budget or trader-broker's flatten passes through it. A
BLOCK is final for that order: it is not shown to the user for a yes. Most
people who day trade lose money. (SEBI's own study found 93% of individual
F&O traders lost money over FY22 to FY24; this pack does not touch F&O, and
cash-segment intraday trading is hard too.) These limits keep each loss and
each day's losses small. They do not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
The commands use `python`; use `python3` on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/check.py"                           # trader/ files; writes trader/state/risk.json
python "${CLAUDE_SKILL_DIR}/check.py" --json
python "${CLAUDE_SKILL_DIR}/check.py" --no-write                # look without recording
python "${CLAUDE_SKILL_DIR}/check.py" --now "2026-11-23 09:46"  # pin the clock (India time, IST)
python "${CLAUDE_SKILL_DIR}/check.py" breaker                   # the circuit breaker's state now; read-only
python "${CLAUDE_SKILL_DIR}/check.py" breaker --symbol INFY     # and whether a new entry in INFY is blocked
python "${CLAUDE_SKILL_DIR}/check.py" breaker --real --json     # count real trades instead; as JSON
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget, or trader-broker's flatten) |
| `trader/config.json` | the limits (trader-budget ships `config.example.json`) |
| `trader/portfolio.json` | cash and holdings; if missing, a fresh account funded with the budget |
| `trader/prices.json` | a fresh price raises the entry it measures risk from; also values holdings |
| `trader/state/broker.json` | what the user took today: duplicates, trades per day, daily loss |
| `trader/journal.json` | trader-journal's trades, read only: the scored results (net P&L after charges, exit times) feed the circuit breaker. `--journal PATH` points elsewhere; the default is `journal.json` next to the config |
| `trader/state/risk.json` | the verdicts it writes, with a fingerprint of each exact order |

There is no broker connection. "Placed" in the log means the user told
Claude they placed it themselves (or took it as a practice trade).

## Entries and exits

An **entry** opens or adds to a position: a buy, or a sell that would go
short. An **exit** only reduces what you already hold. Getting out must
always be possible, so an exit is checked for format, asset and short sales
only. Every other rule (day limits, entry window, loss, reward:risk, blocked
list, size, duplicates) turns into a warning for an exit, never a BLOCK. The
circuit breaker does not look at exits at all.

## The checks

Orders are checked in file order, as if each passed order had filled, so a
batch that is fine order by order but breaks a limit together is caught.

| check | blocks when |
| --- | --- |
| format | no symbol, side not buy or sell, no qty, a qty that is not whole shares or a rupee-amount order (NSE has no fractional shares), a limit order with no limit price, an unknown order type or validity; a bracket that is not valid for the day or misses its stop-loss or target |
| asset | the symbol is not a plain NSE equity symbol (letters, digits, `&` and `-`, like `TCS`, `M&M`, `BAJAJ-AUTO`); a futures or options contract (like `NIFTY26NOV24500CE` or `TCS26NOVFUT`) while `allow_options` is false |
| short | a sell of more than you hold (`allow_short` false) |
| blocked | (entries) the symbol is on the `blocked` list |
| size | (entries) under `min_order_inr` or over `max_order_inr` |
| position | (entries) the stock would be over `max_position_pct` of the account |
| cash | (entries) the buy would leave less than the cash buffer, or need margin |
| stop | (entries) no stop while `require_stop` is true (the order's `stop_loss_stop_price`, or its plan stop); a stop on the wrong side of the entry |
| loss | (entries) qty x (entry minus stop) over `max_loss_per_trade_inr` |
| reward:risk | (entries) (target minus entry) / (entry minus stop) under `min_reward_risk` |
| daily loss | (entries) the max losses at the stops of today's entries, plus this one, over `max_daily_loss_inr` |
| trades | (entries) `max_trades_per_day` entries already taken today |
| window | (entries) before `no_entries_before` or after `no_entries_after`, or NSE is closed |
| duplicate | (entries) the same symbol and side twice in one batch, or already taken today |
| daily limit | (entries) `max_orders_per_day` orders already taken today |

How the numbers work:

- **The entry it measures from**: the limit price for a limit order. For a
  market order, the HIGHER of the plan's entry and a fresh price from
  `trader/prices.json` (under 15 minutes old). A plan written at 09:45 cannot
  understate the risk at 09:56 after the price ran up. An older price is not
  used, and it says so.
- **Before charges.** Every max loss is before brokerage, STT and other
  charges (exchange fees, GST, stamp duty). The tool does not model them, and
  each per-order result says so. They come off every trade, win or lose.
- **Daily loss** counts the full max loss of every entry taken today, win or
  lose, as trader-broker logged it. Three full-size trades use up a ₹1,500 day.
- **The window** is India time (IST, a fixed UTC+5:30, no daylight saving),
  worked out in the tool, never from the computer's clock zone. The default
  is 09:30 to 14:30: the 15-minute opening range ends at 09:30, and a late
  entry has little time before the 15:00 flat-by. NSE's regular session is
  09:15 to 15:30. Weekends and NSE holidays are closed. There are no early
  closes. The pre-open session (09:00 to 09:15) is ignored, and Muhurat
  trading day is treated as closed. Set both `no_entries_*` to null to switch
  the window off.
- **Holidays**: the 2026 list from NSE's circular of 12 December 2025. NSE had
  not published 2027 when this was written, so for any other year the tool
  knows weekends only and prints a warning: check nseindia.com.

Warnings do not block, but say them to the user: LIVE mode, a stop that
lives only in the plan and not on the order, a price too old to use, a year
without a holiday list.

## The circuit breaker

The checks above look at one order and at the risk you planned. The circuit
breaker looks at the account and at what really happened: the scored trades
in `trader/journal.json` (trader-journal: net P&L after charges, exit times)
and today's entries in trader-broker's log. When it trips, every NEW entry is
blocked with a `breaker:` reason in rupees and India time. Exits are never
blocked by it.

| rule | blocks new entries when | config key (default) |
| --- | --- | --- |
| daily loss | today's scored trades have lost the daily loss limit or more, after charges (exactly at the limit counts); or that loss, plus the planned max loss of today's open trades, plus this order would go over it | `max_daily_loss_inr` (1500) |
| losing streak | the last N scored trades all lost, counted across days. No new entries for the rest of the day of the last loss; with a number of minutes, until that long after the last losing exit | `loss_streak_limit` (3), `loss_streak_cooldown_minutes` (null = the rest of that day) |
| re-entry | the last losing exit was less than M minutes ago (at exactly M minutes the entry is allowed again) | `reentry_cooldown_minutes` (30) |
| re-entry, same stock | the stock was stopped out today: no second entry in it today | `block_reentry_after_stop` (true) |
| weekly loss | this week's scored trades (Monday on) have lost the weekly limit or more | `max_weekly_loss_inr` (null = off) |

`"circuit_breaker": false` switches all of it off; 0 or null switches one
rule off. These keys are optional in `trader/config.json`: a key that is not
there takes the default above.

How it counts:

- **Practice and real are separate.** The breaker counts trades of the mode
  the order is in: practice trades while `"paper"` is true, real trades while
  it is false. A practice losing streak never blocks a real order, and the
  other way round. Every breaker line names the mode it counted.
- **A win is a net P&L above zero after charges, a loss is below zero**, the
  same rule as the journal's scoreboard. A trade at exactly zero ends a streak.
- **A trade that is not scored is neither a win nor a loss.** The status says
  how many are unscored. Today's open trades count at their planned max loss
  (before charges), the way the daily loss check already counts them, in the
  "still at risk" figure and in what is left of the daily limit.
- **It only knows what the journal knows.** A simulated score is written after
  the 15:30 close, so for the breaker to see a loss during the day, the exit
  has to be in the journal: `journal.py score --symbol X --exit-price P
  --exit-time HH:MM`, or a sell fill in trader-broker's log followed by
  `journal.py import` and `journal.py score`. If the log has a sell fill today
  that the journal has not scored, the breaker says so.
- **No journal, or no trades of that mode in it**: the breaker passes and says
  it has no history.
- **Times**: the exit time in the journal. For a simulated exit that is the
  start of the 5-minute bar that touched the stop or the target. An exit with
  no time uses the time it was scored, or the flat-by time, and says so.

A circuit breaker is discipline: it caps what a bad run can cost and stops
the trade taken to win a loss back. It is not an edge. It does not make a
trade win and nothing here claims it improves returns.

## Steps

1. If the price may have moved since the plan, refresh it first
   (trader-alerts: `alerts.py quote TCS --write`).
2. Run `check.py`. Exit code 0 means every order passed, 1 means at least one
   was blocked.
3. Tell the user the result in plain words, order by order: the max loss at
   the stop (before charges), reward:risk, and how much of today's limits it
   uses. For a BLOCK, give the reason and the fix (fewer shares, a closer
   stop from trader-exit, wait for the window, or skip the trade). Never
   change a limit yourself.
4. If the order changes in any way, run the check again. trader-broker matches
   each order to its verdict by fingerprint, so an edited order is not ready
   until it passes again, and a verdict from an earlier day does not count today.
5. A PASS goes to `/trader-broker`, which shows the ticket and asks the user
   for an explicit yes. The PASS is never that yes.
6. When the user asks "can I still trade today", or after a losing exit, run
   `check.py breaker` and read out what it says: realised today, what is
   left of the daily limit, the streak, which rule tripped and when new
   entries are allowed again. If the breaker blocks, do not look for a way
   round it (another symbol, a smaller size, switching mode): the fix is to
   wait. Only the user changes a threshold, in `trader/config.json`.

## Example: TCS on Monday 2026-11-23

Real numbers: the TCS order from trader-budget's example (entry 2,443.50,
the close of the 09:40 five-minute bar on Yahoo's `TCS.NS` data; stop
2,419.35; target 2,467.65; 15 shares), checked with
`check.py --now "2026-11-23 09:46"` on a fresh ₹50,000 account with the
example config:

```
PASS  #1 buy TCS 15 shares, entry
          entry 2,443.50 (the plan's entry), stop 2,419.35, target 2,467.65: risk 24.15 a share
          max loss ₹362.25 (limit ₹500), reward:risk 1.00 (min 1)
          before brokerage, STT and other charges
          today before this: 0 of 3 trades, ₹0 of ₹1,500 daily loss

Circuit breaker (practice trades): no practice trades in the journal yet, so no history to count. It passes.
1 passed, 0 blocked at 2026-11-23 09:46 IST. Cash after the passed orders: ₹13,347.50 (practice mode).
Every order passed. trader-broker still asks you yes or no for each one, and you enter it in your own broker app yourself.
Education and tooling, not financial advice.
```

One more share and it is blocked, by the position cap:

```
BLOCK #1 buy TCS 16 shares, entry
          entry 2,443.50 (the plan's entry), stop 2,419.35, target 2,467.65: risk 24.15 a share
          max loss ₹386.40 (limit ₹500), reward:risk 1.00 (min 1)
          before brokerage, STT and other charges
          today before this: 0 of 3 trades, ₹0 of ₹1,500 daily loss
        - position: TCS would be ₹39,096, 78.2% of the ₹50,000 account, over your 75% cap
```

The same 15 shares checked at 09:56, with the real close of the 09:50 bar
(2,455.30) in `trader/prices.json`, are blocked twice:

```
BLOCK #1 buy TCS 15 shares, entry
          TCS last 2,455.30 is above the plan's entry 2,443.50: the risk is measured from 2,455.30
          entry 2,455.30 (the latest price), stop 2,419.35, target 2,467.65: risk 35.95 a share
          max loss ₹539.25 (limit ₹500), reward:risk 0.34 (min 1)
          before brokerage, STT and other charges
          today before this: 0 of 3 trades, ₹0 of ₹1,500 daily loss
        - loss: 15 shares x 35.95 risk a share = ₹539.25 at the stop, over your ₹500 max loss per trade
        - reward:risk: 0.34 (target 2,467.65, entry 2,455.30, stop 2,419.35) is under your minimum 1
```

At 09:20 it is blocked by the window (`window: no new entries before 09:30
IST (it is 09:20)`). On Tuesday 2026-11-24 it is blocked all day (`window:
the market is closed today (NSE holiday: Guru Nanak Jayanti, Tue 2026-11-24
10:00 IST)`). A 14:58 sell that closes the 15 shares passes, even though it
is after the 14:30 entry cutoff: it is an exit.

## Example: the circuit breaker on Monday 2026-11-23

A small practice journal, made in a temporary folder with trader-journal
from real prices (Yahoo's `TCS.NS` and `INFY.NS` 5-minute bars of that day):
TCS, 15 shares from 2,443.50, out at the target 2,467.65 at 10:07 (net
+₹301.87 after charges); INFY, 33 shares from 1,123.00, out at the stop
1,114.10 at 10:57 (net -₹354.04). Both exits were reported to the journal
with `journal.py score --exit-price ... --exit-time ...`. Then
`check.py breaker --now "2026-11-23 11:10"`, 13 minutes after the loss:

```
Circuit breaker at 2026-11-23 11:10 IST (Mon), practice trades: TRIPPED, no new entries
  Journal            2 practice trades: 2 scored, 0 not scored yet
  Realised today     -₹52.17 after charges (2 scored: 1 won, 1 lost)
  Still at risk      ₹0 planned max loss in 0 open trades today, before charges
  Daily loss limit   ₹1,500: ₹1,447.83 left
  This week          -₹52.17 after charges since Mon 2026-11-23 (2 scored). Weekly loss limit: off
  Losing streak      1 in a row (limit 3)
  Last losing exit   INFY at 10:57 IST today (-₹354.04), 13 minutes ago
  Stopped out today  INFY (10:57)
  Tripped            1 rule
    - re-entry: the last losing exit (INFY, -₹354.04 after charges) was at 10:57 IST today, 13 minutes ago. No new entries until 11:27 IST today (30 minutes after a loss).
  New entries again  from 11:27 IST on Mon 2026-11-23, inside your entry window
It counts practice trades only, because "paper" is true in the config. Getting out is never blocked.
A circuit breaker is discipline: it caps what a bad run can cost. It does not make a trade win or improve returns.
Education and tooling, not financial advice.
```

Exit code 1 means tripped, 0 means clear. The TCS order from the first
example, checked at 11:10 with that journal, is blocked by the same rule:

```
        - breaker: re-entry: the last losing exit (INFY, -₹354.04 after charges) was at 10:57 IST today, 13 minutes ago. No new entries until 11:27 IST today (30 minutes after a loss)

Circuit breaker (practice trades): TRIPPED, new entries are blocked (re-entry). Detail: check.py breaker.
0 passed, 1 blocked at 2026-11-23 11:10 IST. Cash after the passed orders: ₹50,000 (practice mode).
```

At 11:27 the cooldown is over and the same order passes (`Circuit breaker
(practice trades): clear. Today -₹52.17 after charges, ₹1,447.83 of the
₹1,500 daily loss limit left, losing streak 1 of 3.`). INFY stays shut for
the day: `check.py breaker --symbol INFY --now "2026-11-23 11:30"` says
`clear for the account, but no new entry in INFY`, with the reason
`re-entry: INFY was stopped out today at 10:57 IST (-₹354.04 after charges).
No second entry in INFY today.`

## Rules

- A PASS only means the order may be shown to the user as a ticket. It is not a yes.
- Every number comes from the config, the order, the portfolio, the prices or the log.
- Prices come from Yahoo Finance's unofficial chart data (NSE symbols with `.NS`, in INR). They can
  lag: NSE data on Yahoo is typically delayed. The price in the user's broker app is the one that
  fills, and a stop can fill worse than its price in a fast market.
- The account it checks against is the pack's own record (`trader/portfolio.json`), built from the
  fills the user reports. It is not read from the broker. If the record and the broker app
  disagree, the broker app is right: fix the record.

## What it never does

It never places an order, never edits an order to make it pass, never
loosens a limit, never blocks a sell that only gets the user out, never
writes to the journal, and never treats a PASS as the user's yes.

## What changed from the US version

India time and the NSE calendar, rupees and the `_inr` config keys, whole
shares only (rupee-amount orders are refused), an NSE symbol check with F&O
contracts blocked, validity `day` or `ioc` only, and no extended hours. The
US-only day-trade counting rule and its config key are removed.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Nothing in this pack places an order. Claude never logs in to a broker and never sends an order:
  the user enters every order themselves in their own broker app, after trader-risk passes that
  exact order and they have said an explicit yes to it in the chat.
- Practice mode by default (`"paper": true`): write the trade down first, risk nothing.
- Long side, cash-segment equity only. No F&O, margin (MTF) or short selling unless the config
  allows it and the user confirms.
- Broker passwords, PINs and OTPs never go in the chat.
