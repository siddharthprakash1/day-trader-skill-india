# The Day Trader skill, India

11 Claude skills for day trading Indian stocks on NSE: scan the market for setups, track news and filings, time every entry and exit, and replay every outcome before you risk a rupee.

Free, MIT, no signup, no API key. The tools are plain Python that read free
public data: Yahoo Finance charts and quarterly figures for NSE symbols,
Google News, and the files and RSS feeds NSE publishes itself (the list of
listed equities, the daily bhavcopy, bulk and block deals, announcements,
insider-trading and takeover disclosures). Everything is in rupees, India
Standard Time and NSE hours (9:15 AM to 3:30 PM).

This is a port of Jake Schincariol's
[day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill),
which is built for US stocks. The method is his. The market calendar, the
clock, the symbols, the money and the order step were rewritten for India.

**Nothing in this pack places an order.** There is no broker connection at
all. The last step prints an order ticket in plain words and you type it
into your own broker app (Groww, Zerodha, Upstox or any other) yourself.
Practice mode is the default.

## Install

As a plugin, in Claude Code:

```
/plugin marketplace add siddharthprakash1/day-trader-skill-india
/plugin install day-trader-skill-india@day-trader-skill-india
```

Claude Code namespaces plugin skills, so installed as a plugin they show up
as `/day-trader-skill-india:trader-scanner` and so on. Copy the folders
instead if you want plain `/trader-scanner`:

```bash
git clone https://github.com/siddharthprakash1/day-trader-skill-india.git
cp -r day-trader-skill-india/trader-* ~/.claude/skills/
```

Project-local instead of global: copy the same folders into your project's
`.claude/skills/`. No Claude Code at all? Paste any single `SKILL.md` at the
top of a chat and it runs as a mode. You lose the Python tools, but the
method works.

The tools need Python 3.8 or newer. Nothing to pip install. On Windows the
command is `python`; on macOS and Linux it is usually `python3`.

## The eleven

| command | job | what it does |
| --- | --- | --- |
| `/trader-scanner` | Chart Scanner | Scans NSE stocks for setups: opening-range breakouts, flags, oversold bounces. |
| `/trader-news` | News Monitor | Shows new headlines (Google News) and NSE announcements for the stocks you watch, and how far each has moved today. |
| `/trader-entry` | Entry Timer | Watches a setup bar by bar and tells you the moment to get in. |
| `/trader-exit` | Exit Planner | Sets your stop and your target before you enter, so you know when to get out. |
| `/trader-timemachine` | Time Machine | Replays the rest of the session 10,000 times from the stock's own last 60 days, plus every past day the same rule fired. |
| `/trader-budget` | Budget Manager | Sizes every position to your budget and your max loss per trade, in whole shares. |
| `/trader-risk` | Risk Manager | Checks every trade against your limits and blocks anything over them. |
| `/trader-broker` | Order Ticket | Prints the order in plain words for you to enter in your broker app, and keeps the log. |
| `/trader-alerts` | Price Alerts | Rings when a price, a stop, the opening bell or your flat-by time hits. |
| `/trader-analyst` | Earnings Analyst | Lines up a company's quarterly revenue, profit and EPS and says what changed. |
| `/trader-insider` | Insider and Big-Deals Tracker | Reads NSE's bulk and block deals and the insider-trading and takeover disclosures as they are published. |

The original's earnings analyst read SEC filings and its eleventh skill
tracked US Congress trade disclosures. Here the analyst reads Yahoo's
quarterly figures and NSE's results feed, and the eleventh skill tracks what
India publishes instead: bulk and block deals, and the trades promoters and
directors must disclose.

## How to use it

Everything lands in a `trader/` folder in your project, and each skill reads
what the last one wrote:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker   (one trade)
alerts: stop, target, flat by 3:00 PM                                         (while it is open)
news / analyst / insider                                                      (on a schedule)
```

An example, replayed on real data: TCS on Monday 23 November 2026. Every
number below is what the tools printed for that session.

1. **`/trader-scanner`** at 10:00 AM over 20 large NSE stocks: 5 had closed
   above their 15-minute opening range. TCS is one: range 2,405.00 to
   2,433.70, the 9:40 candle closed at 2,443.50, 0.40 % over the top.
2. **`/trader-news`**: new headlines for the stock, any NSE announcement
   it filed, and how far it is from yesterday's close. Headlines are leads:
   open the story or the filing before you rely on it.
3. **`/trader-entry`** watched TCS bar by bar. At 9:42 it said waiting (the
   9:40 bar was still forming). At 9:45 it said triggered: the first
   5-minute close above the range, 2,443.50.
4. **`/trader-exit`** set the plan before the entry: stop at the range's
   midpoint, 2,419.35, so 24.15 a share at risk; target one times that risk,
   2,467.65; flat by 3:00 PM either way.
5. **`/trader-timemachine`** replayed the rest of the day to 3:00 PM 10,000
   times, each 5-minute candle drawn from TCS's own candles at that time of
   day over the previous 58 full sessions (seed 19, so anyone can repeat it):
   target first 23.8 %, stop first 47.1 %, still open at 3:00 PM 29.1 %. It
   also re-ran the same rule on those 58 real sessions: it fired 28 times, 6
   hit the target, 13 the stop, 9 neither. Its verdict: strongly toward the
   stop. On the real day the target was hit at 10:05 AM. One day proves
   nothing either way, and that is the point of looking before you risk a
   rupee.
6. **`/trader-budget`** with a 50,000 rupee budget and a 500 rupee max loss
   per trade: the position cap (75 % of the budget) decides it, 15 shares
   (36,652.50 rupees). If the stop fills at 2,419.35, the loss is 362.25
   rupees before charges.
7. **`/trader-risk`** checks it against your limits: the max loss, the daily
   loss limit, trades per day, reward to risk, the entry window, position
   size and cash. PASS or BLOCK, with the reason.
8. **`/trader-broker`** shows you the ticket in plain words: buy 15 TCS,
   Intraday (MIS), with the stop-loss and target orders to place after it
   fills. You say yes for that exact order and enter it in your own broker
   app. Nothing is sent for you.
9. **`/trader-alerts`** rings on the stop, the target and at 3:00 PM to
   flatten whatever is still open.

## The tools

All standard-library Python. The data tools read public endpoints and are
polite about it.

```bash
python trader-scanner/scan.py universe                                # NSE's EQ-series stocks
python trader-scanner/scan.py daily                                   # setups on daily closes, whole universe
python trader-scanner/scan.py orb --at 10:00                          # opening-range breakouts so far
python trader-news/news.py --tickers RELIANCE TCS --hours 12          # headlines and today's move
python trader-entry/entry.py check TCS --setup orb                    # waiting / triggered / expired
python trader-entry/entry.py watch TCS --every 30                     # bar by bar until it triggers
python trader-exit/exit.py plan --stop or-mid --target 1R --write     # -> trader/plan.json
python trader-timemachine/timemachine.py run --write                  # 10,000 replays + the history
python trader-budget/size.py --write                                  # -> trader/orders.json
python trader-risk/check.py                                           # PASS / BLOCK per order
python trader-broker/ticket.py show                                   # the ticket you enter yourself
python trader-alerts/alerts.py add --from-plan                        # stop, target, flat by 3:00 PM
python trader-alerts/alerts.py check                                  # rings what hit, once
python trader-analyst/facts.py TCS                                    # quarters, growth, EPS
python trader-insider/deals.py new                                    # new deals and disclosures
```

```bash
python -m unittest discover -s tests -v
```

The tests run offline, on fixtures cut from real Yahoo data for NSE stocks.

## What changed from the US original

| | original | this port |
| --- | --- | --- |
| Market | NYSE and Nasdaq, 9:30 AM to 4:00 PM New York time | NSE, 9:15 AM to 3:30 PM IST, no daylight saving |
| Holidays | NYSE 2026 and 2027 | NSE 2026 (the 2027 list was not published when this was written) |
| Symbols | US tickers | NSE symbols (`RELIANCE`, `M&M`, `BAJAJ-AUTO`), read from Yahoo as `RELIANCE.NS` |
| Universe | Nasdaq Trader symbol directories | NSE's `EQUITY_L.csv`, EQ series only |
| Money | dollars | rupees; config keys end in `_inr` |
| Day defaults | no entries before 9:45 or after 3:30, flat by 3:55 | no entries before 9:30 or after 2:30, flat by 3:00 |
| Orders | sent to Alpaca or Interactive Brokers after your yes | never sent: a ticket you enter in your own broker app |
| News | Yahoo headlines and SEC 8-K filings | Google News by company name, NSE's announcements feed, Yahoo headlines |
| Earnings | SEC XBRL company facts | Yahoo's quarterly figures (they can have holes) and NSE's results feed |
| Disclosures | US Congress trade reports | NSE bulk and block deals, insider-trading and takeover disclosures |
| Daily closes | Yahoo | Yahoo, with NSE's official bhavcopy for the latest session |
| US rules | pattern-day-trader guard, fractional shares | removed |

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation and it cannot see the future.
Most people who day trade lose money. SEBI's own study found that 93% of
individual traders in equity futures and options lost money over FY22 to
FY24. You can lose money. Decide for yourself, and talk to a SEBI-registered
adviser if you need advice.

**Nothing places an order.** No skill here connects to a broker, logs in to
one or sends an order. `/trader-broker` shows a ticket only after
`/trader-risk` passes that exact order, asks for your explicit yes, and then
you enter it yourself. Never give Claude your broker password, PIN or OTP.

**Practice first.** `"paper": true` is the default in `config.example.json`:
write the trade down, place nothing, and see how the plan would have gone.

**The Time Machine is a simulation, not a prediction.** It is built from the
stock's own last 60 days of 5-minute bars. The future can do things those 60
days never did: a result, a circuit limit, a headline. Treat its odds as a
reality check, never as a promise.

**A stop is a plan, not a guarantee.** In a fast market or across a gap, a
stop order can fill well past its price. The max loss shown is the loss at
the stop price, before charges.

**Charges are not modelled.** Every intraday trade pays brokerage, STT,
exchange and SEBI charges, stamp duty and GST. On a small position these can
be a large part of a 1R target. Check your broker's charges calculator.

**Auto square-off.** Brokers close intraday positions for you some minutes
before the close (commonly between 3:10 PM and 3:20 PM) and charge for it.
The default flat-by time of 3:00 PM is there to get you out first. Check
your own broker's time.

**The data is free and unofficial.** Yahoo Finance's chart endpoint is not an
official API: its NSE prices are delayed, and it can lag, change or
rate-limit you. The tools read 1-minute or 5-minute bars, not every tick.
Check anything that matters against your broker before you act on it.

**Long side, cash-segment equity only.** No F&O, no MTF or margin, no short
selling. Stocks in the BE (trade-for-trade) series cannot be traded intraday
and are left out of the universe.

**Only openly published data.** The tools read files and feeds that NSE,
Google News and Yahoo publish without a login. They do not use the exchange
websites' internal APIs and do not try to get past bot checks. NSE's RSS
feeds are short rolling windows, so a filing can scroll off before a check
sees it; BSE-only filings are not read.

**Yahoo's quarterly figures for Indian companies are patchy.** In testing,
none of ten large companies came back complete: quarters were missing, one
bank's figures were over a year old and one company was reported in US
dollars. `/trader-analyst` shows every gap and never computes growth across
one. Read the company's own results filing before relying on a number.

**NSE's insider-trading feed can be stale.** When this was written its
newest item was months old, while bulk deals, block deals and takeover
disclosures were current. `/trader-insider` prints how fresh each source is
on every run.

**Disclosures are public and late.** Bulk deals, block deals and insider
disclosures are published after the trade. There is no inside information
here, and copying anyone is not a strategy.

**Holidays.** The NSE holiday list for 2026 is built in. For any other year
the tools only know weekends, and say so. The Muhurat session is treated as
a closed day.

**Taxes.** Intraday equity gains are speculative business income in India.
Nothing here is tax advice.

## Files

```
trader-scanner/     SKILL.md, scan.py
trader-news/        SKILL.md, news.py
trader-entry/       SKILL.md, entry.py
trader-exit/        SKILL.md, exit.py
trader-timemachine/ SKILL.md, timemachine.py
trader-risk/        SKILL.md, check.py
trader-budget/      SKILL.md, size.py, config.example.json
trader-broker/      SKILL.md, ticket.py
trader-alerts/      SKILL.md, alerts.py, alerts.example.json
trader-analyst/     SKILL.md, facts.py, metrics.example.json
trader-insider/     SKILL.md, deals.py
tests/              unittest suite + offline fixtures
```

MIT. Ported to the Indian market from
[github.com/Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill)
by Jake Schincariol.
