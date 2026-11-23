---
name: trader-news
description: >-
  News Monitor for NSE stocks. Checks Yahoo Finance's headline feed for every
  stock you watch and shows how far each one is from its previous close, in
  rupees. Shows only what is new since the last check, newest first, in India
  time, tags each item (earnings, analyst, deal, halt, regulatory, macro...)
  and never repeats one. It is honest about its limits: Yahoo's coverage of
  NSE stocks is thin, and exchange filings (NSE / BSE corporate
  announcements) are not read by this tool, so it says "no headlines from
  Yahoo for X" and tells you to check the announcements page by hand. Use
  when the user says "any news on TCS", "why is this moving", "what's the
  news on my watchlist", "morning briefing", "pre-market news", "monitor the
  news", "did anything break", or after /trader-scanner finds setups.
argument-hint: "[NSE symbols]"
---

# trader-news

A chart can look perfect and still be about to gap on results, a board
meeting, a regulator's order or a circuit halt. Before a setup becomes a
trade, this skill checks what it can for every stock on the watchlist and
tells you only what is new. It also prints how far each stock is from its
previous close, so a headline and a move sit side by side.

Read the next section before trusting it. This tool sees a small part of
the news on an Indian stock.

Tool in this folder (standard library Python, no keys, no account). Use
`python3` instead of `python` on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/news.py"                               # watchlist + plan + portfolio
python "${CLAUDE_SKILL_DIR}/news.py" --tickers TCS INFY "M&M" --hours 12
python "${CLAUDE_SKILL_DIR}/news.py" --tickers TCS --all --hours 72 --json
python "${CLAUDE_SKILL_DIR}/news.py" --no-moves                    # skip the price line per symbol
```

Symbols are NSE symbols (`RELIANCE`, `HDFCBANK`, `M&M`, `BAJAJ-AUTO`). The
tool adds `.NS` for Yahoo and strips it again in what it prints.

## What it does not see

Say this to the user the first time the skill runs in a session, in plain
words.

- **Exchange filings are not read.** In India the news that moves a stock
  is usually a filing with the exchange: quarterly results, a board meeting
  date, a dividend or bonus, a pledge, an order win, a regulator's letter.
  None of that comes through Yahoo's feed, and this tool does not fetch it
  from anywhere. Before a trade, the user or Claude opens the stock's
  corporate announcements page on the NSE or BSE website and reads the last
  few entries by hand. The tool prints that reminder on every run.
- **Yahoo's coverage of NSE stocks is thin.** Checked on 2026-11-24 with
  `region=IN`: the feeds for `RELIANCE.NS` and `HDFCBANK.NS` came back
  empty. `TCS.NS` and `INFY.NS` had 13 items each, mostly global press
  releases and market-research reports, and the newest Infosys item was
  from 2026-10-29. The Indian business press is largely missing.
- **A headline is not always about your company.** Yahoo matches loosely.
  The same day the `M&M.NS` feed led with stories about Mars, the maker of
  M&M's chocolate, and the `SBIN.NS` feed mixed State Bank of India with
  unrelated firms called SBI. Read each title and throw out the ones about
  someone else.
- **So silence means nothing.** When Yahoo has nothing, the tool prints
  `no headlines from Yahoo for X`, with the date of the newest item when the
  feed has old ones. Never tell the user "there is no news on X". Say
  "Yahoo had nothing; I have not checked the exchange filings" and then
  check them.

The tool never fills these gaps from another source and never invents a
headline.

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | the scanner's hits (trader-scanner) |
| `trader/plan.json` | the trade you are planning (trader-exit) |
| `trader/portfolio.json` | what you hold (trader-broker records fills there) |
| Yahoo Finance headline RSS | `https://feeds.finance.yahoo.com/rss/2.0/headline?s=TCS.NS&region=IN&lang=en-IN` |
| Yahoo Finance chart endpoint | the last price against the previous close, in INR (unofficial, NSE prices on it can lag) |
| `trader/state/news.json` | what has been shown (kept 30 days), so an item shows once |

Times print in India Standard Time (a fixed UTC+5:30; the tool never uses
the computer's own time zone). `--now "2026-11-23 09:45"` pins the clock in
India time; `--offline DIR` reads saved `yahoo-<SYMBOL>.xml` and
`chart-<SYMBOL>.json` files instead of the network.

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story and read it. Quote the source and link it. Never
  summarise a paywalled story you could not read: say so.
- **Filings beat headlines.** If the company's own filing with NSE or BSE
  and a headline disagree, the filing is what the company told the exchange.
  This tool does not read filings, so go and read it.
- **Say what it touches.** Every item lists the symbols it touches and its
  tags: earnings, analyst, legal, deal, capital, people, risk, halt,
  regulatory, macro. The tags are plain keyword matches (results, SEBI,
  upper circuit, bonus issue, RBI...), a hint about where to look first,
  nothing more.
- **MOVING** marks a stock 3 % or more from its previous close
  (`--move-pct`). A move with no headline is worth saying out loud too, and
  on an NSE stock it usually means the reason is in a filing this tool
  cannot see.
- **The price line carries its own time.** When the quote is from another
  day (a holiday, a weekend, before the open) the line ends with the date.
- **No predictions.** Report what was published and how far the price is
  from the previous close. Never say what the stock will do next.

## Steps

1. Run the check: `python "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives everything
   machine readable, `--dry-run` leaves the state alone.
2. Read the lines that matter first: every MOVING symbol, anything tagged
   earnings, halt, legal, regulatory or risk, and every `no headlines from
   Yahoo` note.
3. For each stock that is a trade candidate, check the exchange by hand:
   the corporate announcements and the board-meeting / results calendar on
   the NSE or BSE website. Do this even when Yahoo showed headlines.
4. Write a short brief, per symbol: what was published (source and link),
   the move, what the filings page showed, and one line on why it matters to
   the setup (for example "results are due this week: an overnight gap can
   jump past any stop, and this is a same-day trade").
5. If a story or a filing changes the picture, say so before `/trader-entry`
   and `/trader-exit`. Never act on a headline alone.

During the session: `/loop 15m /trader-news`, or a scheduled task each
morning before the 09:15 open.

## Example: a real run, 2026-11-24 01:15 IST

Run live on Tuesday 2026-11-24, an NSE holiday (Guru Nanak Jayanti), so the
prices are Monday's last ones and each price line says so.

```
$ python news.py --tickers TCS RELIANCE HDFCBANK INFY "M&M" --hours 48 --dry-run
HDFCBANK   ₹817.80 -3.33% vs previous close ₹846.00 at 15:29 IST on 2026-11-23  MOVING
INFY       ₹1,103.40 +0.49% vs previous close ₹1,098.00 at 15:29 IST on 2026-11-23
M&M        ₹3,487.40 -1.62% vs previous close ₹3,545.00 at 15:29 IST on 2026-11-23
RELIANCE   ₹1,351.00 -1.92% vs previous close ₹1,377.50 at 15:29 IST on 2026-11-23
TCS        ₹2,460.00 +3.53% vs previous close ₹2,376.20 at 15:29 IST on 2026-11-23  MOVING

  2026-11-23 06:21 IST  TCS         Yahoo Finance CORPWATCH: India's IT giants turn captive tech centres into billion-dollar deals
      tags: deal  http://www.intellinews.com/corpwatch-india-s-it-giants-turn-captive-tech-centres-into-billion-dollar-deals-487312/?.tsrc=rss
note: no headlines from Yahoo for HDFCBANK (its feed is empty)
note: no headlines from Yahoo for INFY in the last 48 hours, the newest of its 13 is from 2026-10-29
note: no headlines from Yahoo for M&M in the last 48 hours, the newest of its 16 is from 2026-11-18
note: no headlines from Yahoo for RELIANCE (its feed is empty)
Checked 2026-11-24 01:15 IST. Yahoo's coverage of NSE stocks is thin, and a headline it lists is not always about your company. Exchange filings are not read here: check the stock's corporate announcements on the NSE or BSE website yourself. Headlines are leads, not facts; open the story before acting. Not financial advice.
```

How to read it. Two stocks moved 3 % or more on Monday: HDFC Bank fell
3.33 % and TCS rose 3.53 %. Yahoo has one TCS headline from that morning
and nothing at all for HDFC Bank. That is the normal picture for an NSE
stock on this feed: a real move and no story. The honest brief is "HDFCBANK
is down 3.33 % and Yahoo's feed is empty; I have not read its exchange
filings yet", followed by reading them. It is not "no news on HDFC Bank".

## What it never does

- Never invents or embellishes a story, a quote or a number, and never
  presents an empty feed as "no news".
- Never reads, scrapes or guesses exchange filings. That check is by hand.
- Never trades on news, and never tells the user to buy or sell because of a
  headline.
- Never hammers a server: one request per feed per symbol, half a second
  between calls.

## Safety

- Education and tooling, not financial advice. This skill only reads and reports.
- Nothing in this pack places an order. Claude never logs in to a broker and never sends an order:
  the user enters every order themselves in their own broker app, after trader-risk passes that
  exact order and they have said an explicit yes to it in the chat.
- Practice mode by default (`"paper": true`): write the trade down first, risk nothing.
- Broker passwords, PINs and OTPs never go in the chat.
