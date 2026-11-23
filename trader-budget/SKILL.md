---
name: trader-budget
description: >-
  Budget Manager for NSE intraday trades, in rupees. Sizes every position to
  your budget and your max loss per trade. Reads the trade plan from
  trader-exit (entry, stop, target), finds the whole shares that fit inside
  four limits (max loss per trade, position cap, max order, cash after your
  buffer), says which limit decided it, and writes ONE proposed bracket order
  (entry, stop-loss, target) to trader/orders.json. A stop too far away for
  your max loss is a plain refusal, not a smaller order. Nothing is placed:
  the user enters every order themselves in their own broker app. Use when
  the user says "how many shares", "size this trade", "how much should I
  buy", "I have 50,000 rupees", "I only want to risk 500 a trade", "set my
  rules", "keep 10% in cash", or after /trader-exit writes a plan.
argument-hint: "[budget in rupees]"
---

# trader-budget

The Budget Manager turns a plan ("buy TCS at 2,443.50, stop 2,419.35") into
an exact size ("15 shares"), inside rules the user sets once. The stop and
the budget decide the size: the further away the stop, the fewer shares. It
writes a proposal only. trader-risk checks it, trader-broker shows it as a
ticket and asks the user yes or no, and the user types it into their own
broker app (Groww, Zerodha, Upstox...) as an Intraday (MIS) order. Most
people who day trade lose money. Sizing to a max loss keeps each loss small;
it does not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
The commands use `python`; use `python3` on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/size.py"                         # trader/plan.json + trader/config.json
python "${CLAUDE_SKILL_DIR}/size.py" --write                 # write trader/orders.json
python "${CLAUDE_SKILL_DIR}/size.py" --limit --write         # a limit order at the plan's entry
python "${CLAUDE_SKILL_DIR}/size.py" --limit 2440.00 --json  # a limit order at 2440.00
python "${CLAUDE_SKILL_DIR}/size.py" --budget 30000          # try another budget without editing the config
```

`--now "2026-11-23 09:46"` pins the clock. It is India time (IST, a fixed
UTC+5:30, worked out in the tool and never taken from the computer's time
zone). A plan for another day is refused: make a new one with trader-exit.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | the rules (start from `config.example.json` in this folder) |
| `trader/plan.json` | the trade: entry, stop, target, setup, flat-by time (trader-exit writes it) |
| `trader/prices.json` | optional: a fresh price above the plan's entry means a bigger risk (trader-alerts writes it) |
| `trader/portfolio.json` | optional: its cash caps the size (trader-broker keeps it) |
| `trader/orders.json` | the one proposed bracket order it writes with `--write`: an internal record, never sent anywhere |

Symbols are NSE symbols: `TCS`, `HDFCBANK`, `M&M`, `BAJAJ-AUTO`. A `.NS`
suffix (Yahoo's) is stripped.

## Step 1: set the rules once

If `trader/config.json` does not exist, copy `config.example.json` into
`trader/config.json` and ask the user for each value in plain words. All
money is in rupees.

- **budget**: how much money this is for (example: 50000). Only money they can afford to lose.
- **cash_buffer_pct**: how much always stays in cash (default 10).
- **max_loss_per_trade_inr**: the most one trade may lose if the stop fills at the stop (default 500).
- **max_position_pct**, **max_order_inr**, **min_order_inr**: the biggest position (75% of the
  budget), the biggest order (40000) and the smallest order (500).
- The day limits trader-risk enforces: **max_daily_loss_inr** (1500), **max_trades_per_day** (3),
  **min_reward_risk** (1.0), **require_stop** (true), **no_entries_before** / **no_entries_after**
  ("09:30" / "14:30") and **flat_by** ("15:00"), all India time.
- **broker**: a label only, for example "groww" or "zerodha". It is printed on the ticket. Nothing
  connects to a broker.
- **blocked**: symbols they never want to trade.
- Keep **paper** true (practice mode: write the trade down, place nothing) until they have watched
  it work on paper for a while.

Why those times: the 15-minute opening range is the 09:15, 09:20 and 09:25
bars, so the earliest entry is 09:30. Brokers square off intraday positions
by themselves between about 15:10 and 15:20 IST and charge for it, so
`flat_by` 15:00 gets the user out first.

## Step 2: size the trade

```bash
python "${CLAUDE_SKILL_DIR}/size.py" --write
```

Whole shares only: NSE has no fractional shares. The size is the smallest of
four limits:

| limit | shares |
| --- | --- |
| max loss | `max_loss_per_trade_inr` / risk a share (entry minus stop) |
| position cap | budget x `max_position_pct` / entry |
| max order | `max_order_inr` / entry |
| cash | (budget minus the cash buffer) / entry, never more than the cash in `trader/portfolio.json` |

The entry it sizes at: the limit price with `--limit`. For a market order, the
plan's entry, or the price in `trader/prices.json` if it is fresh (under 15
minutes old) and higher. A price that ran up since the plan means a bigger
loss at the same stop, so fewer shares. A lower price never shrinks the risk.

The size uses the user's own cash only. Brokers offer intraday leverage on
MIS orders; this tool never counts on it.

It refuses, and writes no order, when: the size is zero shares, the order is
under the minimum, reward:risk is under `min_reward_risk`, the stop is not
below the entry, the target is not above it (the move already happened), the
plan is for another day, the symbol is blocked, or the plan is a short. Say
the reason to the user in one line.

## Step 3: show the user

Show the four limits, which one decided the size, the shares, the rupees and
the max loss at the stop. Say plainly that:

- the max loss is **before brokerage, STT and other charges** (exchange fees, GST, stamp duty). The
  tool does not model them. They come off every trade, win or lose.
- a stop is an order, not a promise: in a fast market it can fill below the stop.

Then hand over to `/trader-risk`.

## Example: TCS on Monday 2026-11-23

Real numbers, from Yahoo's 5-minute bars for `TCS.NS` (saved in
`tests/fixtures/trader-budget/`). The opening range (09:15 to 09:30) was
2,405.00 to 2,433.70. The first 5-minute bar to close above 2,433.70 was the
09:40 bar, at 2,443.50, complete at 09:45. The plan was built by hand from
those bars the way trader-exit does it: entry 2,443.50, stop 2,419.35 (the
range midpoint), target 2,467.65 (1R), flat by 15:00. With the example
config, `size.py --now "2026-11-23 09:46" --write` prints:

```
TCS buy, opening-range breakout, entry 2,443.50 at 9:45 AM
Stop 2,419.35: opening-range midpoint. Target 2,467.65: 1R.
Sized at 2,443.50 (the plan's entry). Risk 24.15 a share (entry 2,443.50 minus stop 2,419.35). Reward:risk 1.00.

Limits (whole shares; the smallest one wins):
  max loss      ₹500 max loss / 24.15 risk a share                                          20 shares
  position cap  75% of ₹50,000 = ₹37,500 / 2,443.50                                         15 shares  <- binds
  max order     ₹40,000 / 2,443.50                                                          16 shares
  cash          budget ₹50,000 minus the ₹5,000 cash buffer = ₹45,000 / 2,443.50            18 shares

Size: 15 shares of TCS = ₹36,652.50 at 2,443.50. Max loss at the stop: 15 x 24.15 = ₹362.25.
Before brokerage, STT and other charges. A stop is an order, not a promise: in a fast market it can fill below 2,419.35.

Proposed order (a proposal only: nothing has been placed, and Claude never places it):
  buy 15 TCS on NSE, Intraday (MIS), market, valid today, target 2,467.65, stop-loss 2,419.35
```

Here the position cap decided the size, not the max loss: 20 shares would fit
the ₹500 max loss, but 20 shares cost more than 75% of the budget. It writes:

```json
{"symbol": "TCS", "side": "buy", "qty": "15", "type": "market", "time_in_force": "day",
 "order_class": "bracket", "take_profit_limit_price": "2467.65", "stop_loss_stop_price": "2419.35",
 "why": "opening-range breakout on TCS: 15 shares, bound by your position cap (15 x 24.15 = ₹362.25 at the stop, before charges)",
 "plan": {"entry": 2443.5, "stop": 2419.35, "target": 2467.65, "risk_per_share": 24.15, "max_loss": 362.25,
          "setup": "orb", "entry_time": "9:45 AM", "flat_by": "15:00"}}
```

That record is for trader-risk and trader-broker only. Ten minutes later the
price had run: the 09:50 bar closed at 2,455.30. With that price in
`trader/prices.json`, the same plan at 09:56 sizes to 13 shares at 35.95 a
share and reward:risk falls to 0.34, so it is refused:

```
No order: reward:risk 0.34 (target 2,467.65, entry 2,455.30, stop 2,419.35) is under your minimum 1, so trader-risk would block it. No order. Skip it, or make a new plan with trader-exit.
```

One day of one stock shows how the sizing works. It says nothing about what
this setup does on other days.

## Rules

- Proposals only. Nothing in this skill talks to a broker.
- Never size money the user has not said is for this. Ask for the budget and the max loss.
- Every number comes from the config, the plan or the prices. Nothing is guessed.
- No projections: never say what a trade will make. A target is a plan, not a forecast.
- Prices come from Yahoo Finance's unofficial chart data (NSE symbols with `.NS`, in INR). They can
  lag: NSE data on Yahoo is typically delayed. The price in the user's broker app is the one that fills.
- Only go ahead to trader-broker after the user has seen the size and the max loss. A ticket is
  only ever acted on after their explicit yes.

## What it never does

It never places, approves or schedules an order, never raises a limit on its
own, never shrinks a stop to fit more shares, never counts on intraday
leverage, and never sizes F&O, margin (MTF) or short positions.

## What changed from the US version

Rupees and the `_inr` config keys, whole shares always (the `fractional`
setting is gone), India time, and no broker API. The old weights mode
(`--weights`, `--targets`, splitting a budget across a list of stocks) was
removed: it produced rupee-amount and no-stop orders, which this intraday
pack cannot use.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Nothing in this pack places an order. Claude never logs in to a broker and never sends an order:
  the user enters every order themselves in their own broker app, after trader-risk passes that
  exact order and they have said an explicit yes to it in the chat.
- Practice mode by default (`"paper": true`): write the trade down first, risk nothing.
- Long side, cash-segment equity only. No F&O, margin (MTF) or short selling unless the config
  allows it and the user confirms.
- Broker passwords, PINs and OTPs never go in the chat.
