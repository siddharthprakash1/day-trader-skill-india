---
name: trader-exit
description: >-
  Exit Planner for the Indian market (NSE). Sets your stop and your target
  before you enter, so you know when to get out. Reads the entry from
  trader-entry (or a price you give), puts the stop at the opening-range
  midpoint by default (or the range low, your price, a percent, or a multiple
  of the ATR), rounds it down to the paisa, and sets the target at 2R (or 1R,
  or your price), all in rupees. Refuses a stop at or above the entry, a
  target at or below it, a stop too tight to survive the spread, or an entry
  at or after the flat-by time (15:00 India time by default), and says why.
  Writes trader/plan.json for the Time Machine, the Budget Manager and Price
  Alerts. Long side only. Use when the user says "where's my stop", "set my
  stop and target", "plan the exit", "when do I get out", "stop loss",
  "what's my R", "risk reward", or right after /trader-entry triggers, before
  /trader-timemachine.
argument-hint: "[--stop or-mid|or-low|price:X|pct:X|atr:K] [--target 2R|1R|price:X]"
---

# trader-exit

The Exit Planner decides how a trade ends before it starts. The stop is where
the setup has failed. The target is where you take what you came for. Both
are set BEFORE the entry, written down and handed on, so nobody has to decide
anything in the middle of a fast move. A stop is a plan, not a guarantee: a
gap or a fast market can fill past it. Day trading loses most people money,
and a neat plan does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
Use `python3` on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/exit.py" plan                           # trader/entry.json: midpoint stop, 2R target
python "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 1R --write
python "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5            # needs the session's bars (Yahoo or --offline)
python "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol TCS --entry 2443.50 --or-high 2433.70 --or-low 2405.00
python "${CLAUDE_SKILL_DIR}/exit.py" show                           # the current trader/plan.json
```

Other flags: `--flat-by 15:00`, `--json`, `--now "2026-11-23 09:46"` (India
time), `--entry PRICE` with a waiting entry.json to plan ahead, `--setup
level`, `--offline FILE` (a saved Yahoo chart JSON for an ATR stop).

Symbols are NSE symbols (`TCS`, `RELIANCE`, `M&M`); prices are rupees; every
time is India Standard Time (UTC+5:30, no daylight saving), worked out by the
tool and never taken from the computer's own time zone.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | the trigger from trader-entry: symbol, entry price, time, bar, opening range |
| `trader/config.json` | `flat_by` (15:00) and `min_reward_risk` (1.0: a note here, trader-risk enforces it) |
| Yahoo chart API | only for an `atr:K` stop: the session's bars up to the entry bar, asked for as `TCS.NS`. Free, unofficial, can lag |
| `trader/plan.json` | with `--write`: entry, stop and its rule, target and its R, risk a share, reward:risk, flat by. trader-timemachine, trader-budget and trader-alerts read it |

## Stops and targets

| stop | where it goes |
| --- | --- |
| `or-mid` | the opening-range midpoint (the default for an opening-range breakout) |
| `or-low` | the opening-range low: wider, so fewer shares for the same max loss |
| `price:X` | your price |
| `pct:X` | X percent under the entry |
| `atr:K` | K times the 14-bar ATR under the entry, on the session's bars up to the entry bar. With fewer than 14 bars by then it averages all of them and says so |

A level setup has no opening range to lean on, so it needs a stop you name.

- **Rounding.** The stop rounds AWAY from the entry to the paisa (down, for
  a long). Risk = entry minus stop. Target = entry + R x risk, rounded half
  up to the paisa. `2R` by default; `1R`, `1.5R` or `price:X` also work.
- **Your broker's price steps.** The tool works to ₹0.01. Many NSE shares
  trade in bigger steps (₹0.05 or more, by price). If the broker app will not
  take a price, move the stop to the next allowed price further from the
  entry, never closer, and plan again with `--stop price:X`.
- **Refused, with the reason.** A stop at or above the entry. A target at or
  below it. A risk under 0.05% of the price: too tight to survive the
  spread, normal wiggle would stop you out. An entry.json that has not
  triggered (unless you give `--entry`). A short: long side only.
- **Flat by.** 15:00 by default (or `flat_by` in the config). Out by then,
  win or lose, no overnight hold. Brokers close intraday (MIS) positions
  themselves from about 15:10 to 15:20 and charge a fee for doing it, so the
  plan gets you out first. The NSE session ends at 15:30 every trading day; a
  flat-by at or after that is refused, and so is an entry at or after the
  flat-by time.
- **Before charges.** Risk a share is the price gap only. Brokerage, STT,
  exchange fees and GST come on top, and this pack does not model them.

## Steps

1. Run it right after `/trader-entry` triggers. Before that, `--entry PRICE`
   plans ahead at the level you expect.
2. Use the default stop for an opening-range breakout unless the user names
   another. A level setup needs one: ask.
3. Run `exit.py plan --write`. Read back the entry, the stop and its rule,
   the target and its rule, the risk a share, reward:risk and flat by.
4. If it refuses, say the reason and the fix (a wider stop, a farther
   target). Never loosen anything on your own.
5. Say once that the stop is a plan, not a guarantee.
6. Hand off to `/trader-timemachine` to replay the plan, then `/trader-budget`
   to size it to the max loss per trade. Offer `/trader-alerts` for the stop,
   the target and the flat-by time. Claude can keep `entry.py watch` running
   in the background or on a `/loop` until a setup triggers, then come here.

## Example: TCS on Monday 2026-11-23

These are runs of this tool on the entry that trader-entry wrote from
Yahoo's five-minute bars for `TCS.NS`: 2,443.50 at 9:45 AM (the 9:40 bar,
bar 5), opening range 2,405.00 to 2,433.70. `exit.py plan --now "2026-11-23
09:46" --write` uses the defaults, a midpoint stop and a 2R target:

```
Exit plan: TCS long, Mon 2026-11-23, opening-range breakout on 5-minute bars
  Entry         ₹2,443.50 at 9:45 AM (bar 5)
  Stop          ₹2,419.35  opening-range midpoint: (₹2,433.70 + ₹2,405.00) / 2 = ₹2,419.35
  Target        ₹2,491.80  2R (two times the risk)
  Risk a share  ₹24.15 (0.99% of the entry)
  Reward:risk   2.0
  Flat by       15:00 IST, win or lose: no overnight hold
Set before the entry. If price hits the stop, you are out; never move it further away.
A stop is a plan, not a guarantee: a gap or a fast market can fill past it.
Wrote trader/plan.json.
Next: /trader-timemachine replays it, then /trader-budget sizes it to your max loss. Nothing was placed.
```

`--target 1R` puts the target at 2,443.50 + 24.15 = 2,467.65. `--stop
atr:1.5` reads the six bars up to the entry bar instead:

```
  Stop          ₹2,416.10  1.5 x ATR(6) under the entry: ATR ₹18.2666 over the 6 bars to the entry (fewer than 14 so far), rounded down
  Target        ₹2,498.30  2R (two times the risk)
```

A stop 50 paise under the entry (`--stop price:2443`) is refused:

```
exit.py: the stop is ₹0.50 under the entry, less than 0.05% of the price (₹1.22 on ₹2,443.50): too tight to survive the spread, normal wiggle would stop you out. Widen it.
```

trader-budget then sizes the plan: with a ₹500 max loss per trade the risk
alone allows floor(500 / 24.15) = 20 shares, and its other limits (the order
cap, the cash buffer) can make that smaller.

## Rules

- The stop and the target are set before the entry, never after.
- Never move a stop further from the entry once the trade is on.
- Plain numbers only: entry, stop, target, risk a share, reward:risk. It
  never prints a P&L or a return.
- Nothing here promises winning trades or money. A 2R target is not a
  prediction that the price gets there.
- Long side only.

## What it never does

It never places an order, never sizes a position, never widens a stop to
make a plan pass, and never calls a stop a guarantee. It does not put a
stop-loss order into any broker either: the user types that in themselves.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Nothing in this pack places an order. Claude never logs in to a broker and never sends an order:
  the user enters every order themselves in their own broker app, after trader-risk passes that
  exact order and they have said an explicit yes to it in the chat.
- Practice mode by default (`"paper": true`): write the trade down first, risk nothing.
- Long side, cash-segment equity only. No F&O, margin (MTF) or short selling unless the config
  allows it and the user confirms.
- Broker passwords, PINs and OTPs never go in the chat.
