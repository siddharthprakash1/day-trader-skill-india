---
name: trader-alerts
description: >-
  Price Alerts for NSE stocks. Rings when a price, a stop or the opening bell
  hits. Turns the trade plan into three alerts in one go (the stop, the
  target and "flat by" at your flat-by time, 15:00 IST by default), plus
  above / below / stop / target levels, percent moves from the previous
  close, any time of day, and the NSE opening bell at 09:15, each ringing
  once so you are not spammed. Knows NSE hours (09:15 to 15:30 India time)
  and the 2026 trading holidays, works out India time itself, fetches free
  Yahoo quotes in rupees one symbol at a time, and writes trader/prices.json
  for sizing and the risk check. Use when the user says "alert me when",
  "tell me if TCS hits", "watch my stop", "watch my trade", "remind me to
  close by 3", "is the market open", "when does the market open", "is today
  a trading holiday", "get me a price", or right after /trader-exit writes a
  plan.
argument-hint: "[NSE symbol] [price]"
---

# trader-alerts

Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when a stop alert rings,
nothing is sold by this skill, and nothing in this pack talks to a broker.
The only thing that gets the user out of a trade is an order they place
themselves in their own broker app. The alerts tell them what the price did
and when it is time to be flat.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
Use `python3` instead of `python` on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/alerts.py" clock                          # NSE open or closed, next open
python "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                # stop, target, flat-by from trader/plan.json
python "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol TCS --stop 2419.35
python "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol TCS --target 2467.65
python "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:00 --label "flat by 15:00"
python "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol "M&M" --pct 3
python "${CLAUDE_SKILL_DIR}/alerts.py" check                          # exit 0 = something rang
python "${CLAUDE_SKILL_DIR}/alerts.py" quote TCS --write              # -> trader/prices.json
python "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write       # portfolio + plan symbols
python "${CLAUDE_SKILL_DIR}/alerts.py" list
python "${CLAUDE_SKILL_DIR}/alerts.py" reset tcs-plan-stop
```

`--now "2026-11-23 15:00"` pins the clock (India time; a trailing ` IST` is
fine); `--offline DIR` reads saved quote files (`yahoo-<SYMBOL>.json`)
instead of the network.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the trade from trader-exit, for `add --from-plan` |
| `trader/config.json` | `flat_by`, used when the plan has none (default 15:00) |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest quotes, merged in, for trader-budget and trader-risk |
| `trader/portfolio.json` | the symbols held, for `quote --holdings` |

Symbols are NSE symbols: `RELIANCE`, `HDFCBANK`, `M&M`, `BAJAJ-AUTO`. Yahoo
wants `.NS` on the end; the tool adds it for the request and strips it
again, so the rules and `trader/prices.json` always show the plain NSE
symbol. Typing `TCS.NS` or `tcs` works too. Put `M&M` in quotes on a command
line.

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `time` | the India clock passed `at` ("15:00") on a trading day | once per trading day |
| `opening_bell` | the NSE is open (09:15 IST) | once per trading day |

A rule with `"date"` is watched on that India date only; `--from-plan` rules
carry the plan's date, so yesterday's stop never rings today.

`add --from-plan` writes three rules: the plan's stop, its target, and a
`time` rule at the plan's `flat_by` (else `flat_by` in the config, else
15:00). Why 15:00: brokers square off intraday (MIS) positions on their own
from about 15:10 to 15:20 IST and charge for each position they close, so
the user wants to be out first.

## Quotes

- One source: `query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS`,
  Yahoo Finance's unofficial free JSON endpoint, one request per symbol,
  half a second apart. On a 429 it waits and tries once more; on a 429 or a
  5xx after that it tries `query1`. It identifies itself as
  `day-trader-skill-india/1.0`. Prices are in rupees.
- Yahoo's NSE prices are unofficial and can lag. They are fine for alerts
  and for checking a size, not for timing an order. The broker's price is
  the one that fills. Each price in `trader/prices.json` keeps its quote
  time, and trader-risk only uses a fresh one.
- If Yahoo is down there is no second source, and the tool says so. The
  user reads the last traded price in their broker app and tells Claude,
  who writes it into `trader/prices.json` as
  `{"as_of": "...Z", "prices": {"TCS": 2460.0}}`. Claude never logs in to a
  broker to fetch it.
- A check reads one quote per symbol when it runs. It is not a live feed: a
  price can pass a level and come back between two checks.

## The NSE clock

Regular session 09:15 to 15:30 India Standard Time, Monday to Friday. IST is
a fixed UTC+5:30 with no daylight saving; the tool works it out itself and
ignores the computer's own time zone. Closed on these 2026 trading holidays
(NSE circular of 12 Dec 2025):

- Jan 15 (Municipal Corporation Election, Maharashtra), Jan 26 (Republic
  Day), Mar 3 (Holi), Mar 26 (Shri Ram Navami), Mar 31 (Shri Mahavir
  Jayanti), Apr 3 (Good Friday), Apr 14 (Dr. Baba Saheb Ambedkar Jayanti),
  May 1 (Maharashtra Day), May 28 (Bakri Id), Jun 26 (Muharram), Sep 14
  (Ganesh Chaturthi), Oct 2 (Mahatma Gandhi Jayanti), Oct 20 (Dussehra),
  Nov 10 (Diwali Balipratipada), Nov 24 (Guru Nanak Jayanti), Dec 25
  (Christmas).

What the clock does not know:

- **2027 and later.** NSE had not published its 2027 list on 2026-11-24.
  Outside 2026 the tool knows weekends only, treats every weekday as open,
  and prints a line telling the user to check the trading holidays on
  nseindia.com.
- **The pre-open session** (09:00 to 09:15) is ignored: the market is
  "closed, before the open" until 09:15.
- **The Muhurat session** on Diwali and any other special session on a
  weekend or holiday are ignored: those days count as closed.
- NSE has no early-close days, so nothing here shifts a time alert. A
  `"before_close"` field left in an old rule is accepted and ignored.

## Steps

1. Right after trader-exit writes the plan, run `add --from-plan`. Confirm
   the three alerts back to the user: the stop, the target and the flat-by
   time.
2. Run `check` while the trade is open: every few minutes from a Claude
   scheduled task or `/loop`. Each run fetches only the symbols in today's
   rules and rings each alert once.
3. Tell the user what rang in one line each. A stop or target alert means
   the price reached that level on Yahoo: the user looks at the position in
   their own broker app, and if an exit order they placed there has filled,
   trader-broker records it. A flat-by alert means close anything still
   open: trader-broker `flatten` writes the sell ticket, trader-risk checks
   it, the user says an explicit yes, and the user enters it in their broker
   app. Never act for them.

## Example: TCS, Monday 2026-11-23

Real output. The plan: TCS, entry 2443.50 at 9:45 AM, stop 2419.35, target
2467.65, flat by 15:00. These runs were made on Tuesday 2026-11-24, an NSE
holiday, so `--now` replays Monday's clock, and the quote Yahoo returns is
Monday's last price, 2460.00 stamped 15:29 IST. It is not the price at the
replayed minute; on a live day each check reads the quote of that moment.

```
$ python alerts.py clock
India 2026-11-24 01:12 IST (Tuesday): NSE closed, NSE holiday: Guru Nanak Jayanti.
Next open: 2026-11-25 09:15 IST.

$ python alerts.py clock --now "2026-11-23 09:35"
India 2026-11-23 09:35 IST (Monday): NSE OPEN, regular session.
Today's session: 09:15 to 15:30 IST.

$ python alerts.py quote TCS "M&M" BAJAJ-AUTO --write
TCS        ₹2,460.00 (+3.53% vs prev close ₹2,376.20)  2026-11-23 15:29 IST  [yahoo]
M&M        ₹3,487.40 (-1.62% vs prev close ₹3,545.00)  2026-11-23 15:29 IST  [yahoo]
BAJAJ-AUTO ₹10,643.00 (-1.13% vs prev close ₹10,765.00)  2026-11-23 15:29 IST  [yahoo]
Wrote 3 price(s) to trader/prices.json.
Yahoo quotes are free and unofficial and NSE prices on it can lag: for alerts only. Your broker's price is the one that fills.

$ python alerts.py add --from-plan
Added tcs-plan-stop: {"id": "tcs-plan-stop", "symbol": "TCS", "type": "stop", "price": 2419.35, "note": "plan stop: opening-range midpoint", "date": "2026-11-23"}
Added tcs-plan-target: {"id": "tcs-plan-target", "symbol": "TCS", "type": "target", "price": 2467.65, "note": "plan target: 1R (one times the risk)", "date": "2026-11-23"}
Added tcs-flat-by: {"id": "tcs-flat-by", "symbol": "TCS", "type": "time", "at": "15:00", "label": "flat by 15:00", "date": "2026-11-23"}

$ python alerts.py check --now "2026-11-23 15:00"
NSE OPEN (2026-11-23 15:00 IST, regular session).
ALERT Flat by 15:00: it is 15:00 IST (set for 15:00), the market closes at 15:30. If TCS is still open, close it yourself in your broker app: trader-broker flatten --symbol TCS writes the sell ticket, then trader-risk and your yes. Brokers square off intraday positions on their own from about 15:10 IST and charge for it.
An alert is information, not an order. Nothing here buys or sells: look at the position in your broker app, and if an exit order you placed there has filled, record it with trader-broker. Yahoo's NSE prices can lag; the broker's price is the real one.

$ python alerts.py check --now "2026-11-23 15:05"
NSE OPEN (2026-11-23 15:05 IST, regular session).
Nothing hit. 3 rule(s) watched today, TCS ₹2,460.00.
```

The flat-by alert rings once. On Wednesday the plan's three alerts are no
longer watched; with `add --opening-bell`, the bell rings at 09:15.

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a quote as live. Yahoo's NSE prices are unofficial and can
  lag; say the quote's own time when it matters.
- Confirm every new rule back to the user: symbol, type, level, day.
- An alert never replaces a stop-loss order. If the user wants the stop to
  work while they are away from the screen, they place a stop-loss order
  themselves in their broker app; this skill only tells them what Yahoo saw.

## What it never does

It never places, cancels or changes an order, never logs in to a broker,
never claims a quote is live, and never predicts where a price is going.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Nothing in this pack places an order. Claude never logs in to a broker and never sends an order:
  the user enters every order themselves in their own broker app, after trader-risk passes that
  exact order and they have said an explicit yes to it in the chat.
- Practice mode by default (`"paper": true`): write the trade down first, risk nothing.
- Long side, cash-segment equity only. No F&O, margin (MTF) or short selling unless the config
  allows it and the user confirms.
- Broker passwords, PINs and OTPs never go in the chat.
