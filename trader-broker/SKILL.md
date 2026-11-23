---
name: trader-broker
description: >-
  Order Ticket for NSE intraday trades. It never places an order and has no
  connection to any broker: it shows each proposed order as a plain-words
  ticket that YOU type into your own broker app (Groww, Zerodha, Upstox...)
  as an Intraday (MIS) order, practice mode by default, and never without
  your yes. The ticket gives the buy, the separate stop-loss and target
  orders to place right after it, the max loss at the stop in rupees (before
  charges) and the flat-by time. It records your yes word for word, records
  the fill you report back, builds the sell ticket that closes an intraday
  trade before your broker's auto square-off, and logs each trade's max loss
  so trader-risk can hold the daily limit. Use when the user says "place the
  order", "buy it", "show me the ticket", "what do I type into Groww",
  "close my position", "flatten", "get me out", "I got filled at", or after
  /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker

This skill is the last step before a real order, so it is the strictest. It
does not send anything anywhere. There is no broker API in this pack. The
user places every order by hand, in their own broker app, and only after two
things are true for that one order:

1. **trader-risk passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit yes for this one order** in the chat.

No PASS, no ticket. No yes, nothing happens.

**Claude never places the order, never opens the broker's order form, never
logs in to a broker, and never asks for or handles passwords, PINs or OTPs.**
If the user pastes one, tell them to delete that message and change it.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
The commands use `python`; use `python3` on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --filled-qty 15 --filled-price 2442.90
python "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol TCS --side sell --qty 15 --price 2467.65
python "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol TCS --qty 15
python "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` renders tickets, checks that a reply is a real yes for that
order, builds the flatten ticket, and keeps the log in
`trader/state/broker.json`. `--now "2026-11-23 09:46"` pins the clock (India
time, IST, a fixed UTC+5:30).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget writes it; `flatten` replaces it) |
| `trader/state/risk.json` | trader-risk's verdicts; only a PASS from today is READY |
| `trader/state/broker.json` | every yes, no, placed, not-placed, rejected and filled, with each entry's max loss |
| `trader/portfolio.json` | the pack's own record of cash and shares, updated by `fill` and by `result --filled-qty` |
| `trader/plan.json` | the flat-by time for `flatten` |
| `trader/config.json` | `paper` (practice mode), `broker` (a label printed on the ticket), `flat_by` |

## Practice mode and live

`"paper": true` (the default) is practice mode: the ticket is written down
as a trade and nothing is placed anywhere. The user watches the price and
reports what would have happened. It still counts toward the day's limits.

`"paper": false` is live: the ticket is what the user types into their
broker app with real money. Switching to live is the user's own edit of
their own config. Never switch it for them, and never suggest it before they
have practised.

## Reading the ticket

Indian brokers differ, and many have no bracket order for equity intraday.
So the ticket spells out three separate orders:

- **The entry**: the buy, on NSE, product **Intraday (MIS)**, market or limit, valid today.
- **The stop-loss**: a separate SL or SL-M sell order for the same shares, placed right after the
  buy fills. (SL-M triggers a market sell at the stop. SL triggers a limit sell.)
- **The target**: a limit sell for the same shares, or just a price alert. When one of the two
  exits fills, the user cancels the other, or the leftover order can open a short position.

If the user's broker has a bracket or OCO order, that does the same in one go.

The max loss on the ticket is **before brokerage, STT and other charges**
(exchange fees, GST, stamp duty). The tools do not model them. A market
order and a stop both fill at whatever the market gives, so the real loss
can be bigger.

**Flat by 15:00 IST.** Brokers close intraday positions by themselves
between about 15:10 and 15:20 IST (Groww's help page says 15:20) and charge
for each position they close (about ₹50 plus GST at Groww when this was
written). Being flat by 15:00 avoids that and the last-minutes rush.

## Steps: the entry

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY or BLOCKED goes back to `/trader-risk`.
2. Show ONE ticket, all of it, and ask: "Do you want to place this order
   yourself? Reply yes or no." One order per question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`.
   If it prints "Not recorded", the reply was not an explicit yes for this
   order (for example "sure", "ok", "yes to all", or a yes that names a
   different stock or amount). Ask again. Never turn a reply into a yes yourself.
4. After "Recorded your yes", the user enters the order in their own broker
   app, exactly as the ticket reads, then the stop-loss and the target.
   Claude waits. In practice mode nothing is entered anywhere.
5. The user tells Claude what happened. Record it as they report it:
   `ticket.py result --index N --status placed --filled-qty Q --filled-price P`
   (add `--broker-id ID` if they want their broker's order id kept), or
   `--status not-placed` if they changed their mind, or `--status rejected
   --note "why"` if the broker refused it. A rejected order is not tried
   again without a new risk check and a new yes. The log keeps the entry's
   max loss for trader-risk's daily limit.

## Steps: while it is open, and out by 15:00

- trader-alerts watches the stop, the target and the flat-by time
  (`alerts.py add --from-plan`). The user's own stop-loss order at the broker
  does the stopping; an alert only rings.
- When the stop-loss or the target fills, the user says so and Claude records it:
  `ticket.py fill --symbol TCS --side sell --qty 15 --price 2419.35`.
- By `flat_by`, anything still open gets closed:
  1. The user reads the share count off the positions page of their broker app.
  2. `ticket.py flatten --symbol TCS --qty 15` writes a market sell for those
     shares to `trader/orders.json`.
  3. `/trader-risk`. A sell that only closes what the user holds is never
     blocked by the daily limits or the entry window.
  4. `ticket.py show`, then the user's yes for that one order, as above.
  5. The user, in their broker app: cancel the open stop-loss and target
     orders for those shares first, then exit the position. Then `result`
     with the fill.

## Example: TCS on Monday 2026-11-23

Real numbers. The order is trader-budget's example: entry 2,443.50 (the close
of the 09:40 five-minute bar on Yahoo's `TCS.NS` data, the first to close
above the 09:15 to 09:30 range high of 2,433.70), stop 2,419.35, target
2,467.65, 15 shares. After trader-risk passed it,
`ticket.py show --now "2026-11-23 09:46"` printed:

```
ORDER 1 of 1, PRACTICE (paper: write it down, place nothing)
  BUY 15 TCS on NSE, product Intraday (MIS), market order, valid today
  stop-loss 2,419.35   -> place a separate SL / SL-M sell order for 15 shares right after the buy fills
  target    2,467.65   -> place a limit sell for 15 shares, or set a price alert; cancel the other when one fills
  (two separate exit orders, because many brokers have no bracket order for equity intraday; or use your broker's bracket/OCO order if it has one)
  max loss about ₹362.25 if the stop fills at 2,419.35 (before charges; it can fill worse in a fast market)
  flat by 15:00 IST: your broker auto-squares-off intraday positions around 15:10-15:20 and charges for it
  why: opening-range breakout on TCS: 15 shares, bound by your position cap (15 x 24.15 = ₹362.25 at the stop, before charges)
  READY: trader-risk PASS, waiting for your yes
  Practice mode: do you want to take this as a practice trade (written down, nothing placed)? Reply yes or no.
  Claude never places it, never opens your broker's order form and never handles your password, PIN or OTP.
```

With `"paper": false` the first line reads `ORDER 1 of 1, LIVE: you enter
this yourself in your broker app (groww)` and the question is `Do you want
to place this order yourself? Reply yes or no.`

A reply of "sure" is refused:

```
Not recorded: only a reply that starts with "yes" counts (not ok, sure, go, do it, haan). Ask the user again with the ticket for order 1.
```

The user replies "yes". `approve` records it, and the user reports a fill of
15 shares at 2,442.90 (the real open of the 09:45 bar):

```
> ticket.py result --index 1 --status placed --filled-qty 15 --filled-price 2442.90 --now "2026-11-23 09:47"
Recorded order 1 as placed (as you reported it).
Added the fill to trader/portfolio.json.
Practice: watch the stop-loss 2,419.35 and the target 2,467.65 yourself (trader-alerts), and record the sell with ticket.py fill when one is hit.
```

That day the price first reached the target in the 10:05 bar (high 2,482.00),
so the sell is recorded at the target:

```
> ticket.py fill --symbol TCS --side sell --qty 15 --price 2467.65 --now "2026-11-23 10:06"
Recorded sell 15 TCS at ₹2,467.65 on 2026-11-23 in trader/portfolio.json. Cash now ₹50,371.25 (before charges).
```

That is ₹371.25 before charges on one practice trade on one day. It shows
how the flow works, not what to expect: on other days the same setup hits
the stop instead, and brokerage, STT and the other charges come off either way.

## What it never does

- Never places an order, opens a broker's order form, or logs in to a broker. There is nothing here
  that could.
- Never shows a ticket that trader-risk has not passed today, and never takes one yes for two orders.
- Never treats "ok", "sure" or silence as a yes, and never records a yes the user did not type.
- Never asks for, reads, stores or repeats a broker password, PIN, OTP or API key.
- Never switches `"paper"` to live. The user does that themselves.
- Never records a fill the user did not report, and never promises a fill price.
- Never runs from a scheduled or background task: nobody is there to say yes.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Nothing in this pack places an order. Claude never logs in to a broker and never sends an order:
  the user enters every order themselves in their own broker app, after trader-risk passes that
  exact order and they have said an explicit yes to it in the chat.
- Practice mode by default (`"paper": true`): write the trade down first, risk nothing.
- Long side, cash-segment equity only. No F&O, margin (MTF) or short selling unless the config
  allows it and the user confirms.
- Broker passwords, PINs and OTPs never go in the chat.
