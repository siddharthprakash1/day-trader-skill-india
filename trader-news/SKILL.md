---
name: trader-news
description: >-
  News Monitor for NSE stocks. For every stock you watch it reads three free
  sources: NSE's corporate announcements feed (what the company filed with
  the exchange, with "!" on results, board meetings, director or auditor
  changes, acquisitions, fund raising, credit ratings, insolvency, buybacks
  and dividends), Google News searched by company name (the Indian business
  press, quote pages dropped, the same story from ten publishers shown once)
  and Yahoo Finance headlines. It shows only what is new since the last
  check, newest first, in India time, with how far each stock is from its
  previous close in rupees, tags each item (earnings, analyst, deal, halt,
  regulatory, macro...) and never repeats one. Honest about its limits: the
  NSE feed is a short rolling window, BSE-only filings are not read, and a
  source with nothing says so. Use when the user says "any news on TCS",
  "why is this moving", "any filings", "what's the news on my watchlist",
  "morning briefing", "pre-market news", "monitor the news", "did anything
  break", or after /trader-scanner finds setups.
argument-hint: "[NSE symbols]"
---

# trader-news

A chart can look perfect and still be about to gap on results, a board
meeting, a regulator's order or a circuit halt. Before a setup becomes a
trade, this skill checks what was filed and what was published for every
stock on the watchlist and tells you only what is new. It also prints how
far each stock is from its previous close, so a filing, a headline and a
move sit side by side.

Tool in this folder (standard library Python, no keys, no account). Use
`python3` instead of `python` on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/news.py"                               # watchlist + plan + portfolio
python "${CLAUDE_SKILL_DIR}/news.py" --tickers TCS INFY "M&M" --hours 12
python "${CLAUDE_SKILL_DIR}/news.py" --tickers TCS --name "TCS"    # search Google News for another name
python "${CLAUDE_SKILL_DIR}/news.py" --tickers HDFCBANK --max-items 0 --all --json
python "${CLAUDE_SKILL_DIR}/news.py" --sources nse google          # leave Yahoo's feed out
python "${CLAUDE_SKILL_DIR}/news.py" --no-moves                    # skip the price line per symbol
```

Symbols are NSE symbols (`RELIANCE`, `HDFCBANK`, `M&M`, `BAJAJ-AUTO`). The
tool adds `.NS` for Yahoo and strips it again in what it prints.

## The three sources

| source (`source` in `--json`) | what it is | requests |
| --- | --- | --- |
| NSE corporate announcements (`nse-announcement`) | the filings companies make to NSE: `https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml` | one a run |
| Google News by company name (`google-news`) | `https://news.google.com/rss/search?q="HDFC Bank" when:2d&hl=en-IN&gl=IN&ceid=IN:en` | one a symbol |
| Yahoo Finance headlines (`yahoo`) | `https://feeds.finance.yahoo.com/rss/2.0/headline?s=TCS.NS&region=IN&lang=en-IN` | one a symbol |

**NSE announcements: filings beat headlines.** A filing is what the company
itself told the exchange. An item is yours when the PDF in its link is
named after your symbol (`.../corporate/HDFCBANK_2311...pdf`) or, failing
that, when the item's title is your company's official name. The fallback
matters: on 2026-11-24 the one filing in the feed was from PTC Industries,
NSE symbol `PTCIL`, and its PDF was named `PTCINDUSTRIES_...pdf`. A line
that starts with `!` has a subject that usually matters to a trade, and
the next line says why:

| `important:` | subjects it catches |
| --- | --- |
| results | financial results, outcome of board meeting, board meeting intimation |
| management | change in directors, key managerial personnel, auditors, resignation |
| deal | acquisition, merger, amalgamation, demerger, scheme of arrangement, open offer |
| fund raising | raising of funds, QIP, preferential issue, rights issue |
| rating | credit rating |
| insolvency | insolvency, default, liquidation, fraud |
| halt | trading halt, suspension, delisting |
| payout | buyback, dividend, bonus, stock split, record date |

The `!` is a keyword match on the subject, not a judgement. A filing
without it can still matter. Open the PDF.

**Google News: the headline source.** Yahoo has no idea what the Indian
press wrote; Google News does. The search is by company name, not symbol.
The name comes from `--name "..."` (one ticker), else from `"names"` in
`trader/config.json` (`{"names": {"TCS": "TCS", "M&M": "Mahindra"}}`), else
from the official name with "Limited" taken off ("HDFC Bank Limited"
becomes "HDFC Bank"). Official names are read from `trader/universe.txt`
(written by `/trader-scanner`); for a symbol that is not there the tool
downloads NSE's `EQUITY_L.csv` once and keeps the map in
`trader/state/nse-names.json` for 7 days (it is written even with
`--dry-run`, so the list is fetched once). What the tool does to the
results:

- drops bare quote pages ("... stock price, news, quote and history");
- shows the same story once: near-identical titles from several
  publishers become the earliest one, with `(+N more)`;
- shows at most `--max-items` (default 8) per symbol and says how many it
  left out. Titles that name the company come first, tagged ones among
  them first, then the newest. `--max-items 0 --all` shows everything.

**Yahoo Finance: a minor third source.** Empty for `RELIANCE.NS` and
`HDFCBANK.NS` on 2026-11-24, 13 mostly global press releases for `TCS.NS`.
It is kept because it does carry a story now and then.

## What it does not see

Say this to the user the first time the skill runs in a session, in plain
words.

- **The NSE feed is a short rolling window.** It holds only the newest
  filings: one item at 02:15 IST on a holiday, more during market hours. A
  filing that came and went between two checks is missed, and anything
  filed before the window is not there at all. So run the check on a loop
  during the session, and before a trade open the stock's corporate
  announcements page on the NSE website for the full list.
- **BSE-only filings are not read.** A company listed on both exchanges
  files with both, but this tool reads NSE's feed only.
- **A search by name is loose.** Google News returns anything that mentions
  the name: a job fair at an HDFC Bank branch, a concert at a hall named
  after Infosys, a story about Mahindra Finance in a search for Mahindra &
  Mahindra. Yahoo is looser still: its `M&M.NS` feed led with Mars, the
  maker of M&M's chocolate. Read each title and throw out the ones that are
  not about the company. A short name finds more (`--name "TCS"`), a long
  one finds less.
- **It reads titles, not stories.** Nothing here is the text of an article
  or of a filing.
- **So silence means nothing.** Every source that had nothing for a symbol
  prints a note: `no NSE announcements for your symbols in the feed's
  window right now (it holds 1 filing ...)`, `no headlines from Google News
  for X in the last 48 hours, searched "..."`, `no headlines from Yahoo for
  X`. Never tell the user "there is no news on X". Say which sources were
  checked and what they had.

The tool never fills a gap by guessing and never invents a headline.

## What it reads and writes

| file | what |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | the scanner's hits (trader-scanner) |
| `trader/plan.json` | the trade you are planning (trader-exit) |
| `trader/portfolio.json` | what you hold (trader-broker records fills there) |
| `trader/universe.txt` | `SYMBOL\|Name` lines from trader-scanner: the company names |
| `trader/config.json` | optional `"names"`: your own search name per symbol |
| `trader/state/nse-names.json` | the downloaded symbol to name map, refreshed after 7 days |
| `trader/state/news.json` | what has been shown (kept 30 days), so an item shows once |

The price line comes from Yahoo Finance's chart endpoint: the last price
against the previous close, in INR (unofficial, NSE prices on it can lag).
Times print in India Standard Time (a fixed UTC+5:30; the tool never uses
the computer's own time zone). `--now "2026-11-23 09:45"` pins the clock in
India time; `--offline DIR` reads saved `nse-announcements.xml`,
`google-<SYMBOL>.xml`, `yahoo-<SYMBOL>.xml`, `chart-<SYMBOL>.json` and
`EQUITY_L.csv` files instead of the network.

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story and read it. Quote the source and link it. Never
  summarise a paywalled story you could not read: say so.
- **Filings beat headlines.** If the company's own filing and a headline
  disagree, the filing is what the company told the exchange. Read the PDF
  the item links to before repeating what it says: the feed gives the
  subject, not the numbers.
- **Say what it touches.** Every item lists the symbols it touches and its
  tags: earnings, analyst, legal, deal, capital, people, risk, halt,
  regulatory, macro. The tags are plain keyword matches (results, SEBI,
  upper circuit, bonus issue, RBI...), a hint about where to look first,
  nothing more.
- **MOVING** marks a stock 3 % or more from its previous close
  (`--move-pct`). A move with no filing and no headline is worth saying out
  loud too.
- **The price line carries its own time.** When the quote is from another
  day (a holiday, a weekend, before the open) the line ends with the date.
- **No predictions.** Report what was filed, what was published and how far
  the price is from the previous close. Never say what the stock will do
  next. "Share price prediction" pages turn up in the results: they are
  somebody's guess, not news.

## Steps

1. Run the check: `python "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives everything
   machine readable (each item carries `source`, `publisher`, `time_ist`,
   and for filings `important` and `why`), `--dry-run` leaves the seen
   list alone.
2. Read the lines that matter first: every `!` filing, every MOVING symbol,
   anything tagged earnings, halt, legal, regulatory or risk, and every
   `note:` line.
3. Throw out what is not about the company, and say that you did.
4. For each stock that is a trade candidate, open the filings: the PDF of
   every `!` item, and the stock's corporate announcements page on the NSE
   website for what the rolling window no longer holds (results dates and
   board meetings above all).
5. Write a short brief, per symbol: what was filed and what was published
   (source and link), the move, and one line on why it matters to the setup
   (for example "results are due this week: an overnight gap can jump past
   any stop, and this is a same-day trade").
6. If a story or a filing changes the picture, say so before `/trader-entry`
   and `/trader-exit`. Never act on a headline alone.

During the session run it on a loop, because the NSE feed only holds the
newest filings: `/loop 10m /trader-news`. Also a scheduled task each morning
before the 09:15 open.

## Example: real data, 2026-11-24 02:16 IST

The responses were saved from the three sources at 02:15 IST on Tuesday
2026-11-24, an NSE holiday (Guru Nanak Jayanti), and this is the tool run
on those saved files, so the prices are Monday's last ones and each price
line says so. The Google News links are cut here: each one is about 300
characters long.

```
$ python news.py --tickers TCS RELIANCE "M&M" --max-items 3 --dry-run
M&M        ₹3,487.40 -1.62% vs previous close ₹3,545.00 at 15:29 IST on 2026-11-23
RELIANCE   ₹1,351.00 -1.92% vs previous close ₹1,377.50 at 15:29 IST on 2026-11-23
TCS        ₹2,460.00 +3.53% vs previous close ₹2,376.20 at 15:29 IST on 2026-11-23  MOVING

  2026-11-23 22:55 IST  TCS         The Economic Times She worked at TCS and Accenture, then started a business from home with ₹11,000. Now the Gurugram woman ea
      https://news.google.com/rss/articles/CBMinwJBVV95...
  2026-11-23 22:00 IST  M&M         Trade Brains  Mahindra & Mahindra, Maruti Suzuki and 8 Other Auto Stocks Dominating the Nifty Auto Index; Check the Full List
      tags: macro  https://news.google.com/rss/articles/CBMi6gFBVV95...
  2026-11-23 21:19 IST  RELIANCE    ad-hoc-news.de Reliance Industries stock faces Macquarie downgrade and target cut
      tags: analyst  https://news.google.com/rss/articles/CBMiyAFBVV95...
  2026-11-23 19:54 IST  RELIANCE    Univest       Reliance Industries Share Price Prediction for Wednesday (25 Nov 2026): Stock Falls 1.92% and Breaks Friday's Low
      https://news.google.com/rss/articles/CBMinwFBVV95...
  2026-11-23 16:02 IST  M&M         MarketWatch   Mahindra & Mahindra falls Monday, still outperforms market
      https://news.google.com/rss/articles/CBMisAFBVV95...
  2026-11-23 16:00 IST  RELIANCE    MarketWatch   Reliance Industries slides Monday, still outperforms market
      https://news.google.com/rss/articles/CBMitAFBVV95...
  2026-11-23 13:22 IST  M&M         MarketsMojo   8245 Put Contracts at Rs 3500 Strike on Mahindra & Mahindra Ltd Signal Protective Hedging
      https://news.google.com/rss/articles/CBMiyAFBVV95...
  2026-11-23 11:15 IST  TCS         Business Today TCS, TechM, Infosys, LTM, Mphasis: Nifty IT up 2%; why IT stocks are rising today
      tags: macro  https://news.google.com/rss/articles/CBMi3wFBVV95...
  2026-11-23 06:21 IST  TCS         Yahoo Finance CORPWATCH: India's IT giants turn captive tech centres into billion-dollar deals
      tags: deal  http://www.intellinews.com/corpwatch-india-s-it-giants-turn-captive-tech-centres-into-billion-dollar-deals-487312/?.tsrc=rss
  2026-11-22 08:30 IST  TCS         TradingView   Bosch Maintains Silence On Reported TCS Deal Involving India Technology Operations
      tags: deal  https://news.google.com/rss/articles/CBMi1gFBVV95...
note: no NSE announcements for your symbols in the feed's window right now (it holds 1 filing, filed 2026-11-24 00:28 IST)
note: no headlines from Yahoo for M&M in the last 48 hours, the newest of its 16 is from 2026-11-18
note: no headlines from Yahoo for RELIANCE (its feed is empty)
note: 16 more Google News headlines for M&M not shown (--max-items 3)
note: 18 more Google News headlines for RELIANCE not shown (--max-items 3)
note: 17 more Google News headlines for TCS not shown (--max-items 3)
Checked 2026-11-24 02:16 IST. Read: NSE corporate announcements (a short rolling window of the newest filings: run this on a loop during the session, a filing that scrolled out between checks is missed); Google News by company name (it also finds stories that only mention the company); Yahoo Finance headlines (thin for NSE stocks, and not always about your company). Not read: filings made only to BSE, and NSE filings older than the feed's short rolling window (for the full list open the stock's corporate announcements page on the NSE or BSE website). Headlines are leads, not facts; open the story or the filing before acting. Not financial advice.
```

How to read it. TCS rose 3.53 % on Monday and the press has a reason on
offer (IT stocks up 2 %, a reported Bosch deal the company has not
confirmed): leads to open and read, not facts. The first TCS line is a
human-interest story that only mentions the company: throw it out.
Reliance has an analyst downgrade headline from a site worth checking
before it is repeated, and a "share price prediction" page that is not
news. The NSE note is the honest part: at 02:16 on a holiday the feed held
one filing and it was not for these three, which says nothing about what
they filed on Monday. That check is the announcements page on the NSE
website.

And a filing line, from the same saved feed (the one real filing in it):

```
$ python news.py --tickers PTCIL --sources nse --no-moves --dry-run
  2026-11-24 00:28 IST  PTCIL       NSE filing    Analysts/Institutional Investor Meet/Con. Call Updates: Ptc Industries Limited has informed the Exchange about Schedule of meet
      https://nsearchives.nseindia.com/corporate/PTCINDUSTRIES_24112026002830_Investor_Meet_27112026_Templeton_and_SBI_Life.pdf
```

An investor-meet schedule is routine, so it carries no `!`. No `!` filing
for a watched stock was in the feed when this was written, so none is shown
here: a flagged one prints as `! Outcome of Board Meeting: ...` with
`important: results` on the line below.

## What it never does

- Never invents or embellishes a story, a quote or a number, and never
  presents an empty source as "no news".
- Never reads anything behind a login or a bot check: only NSE's published
  RSS file and equity list, Google News RSS and Yahoo's feeds, with plain
  requests.
- Never trades on news, and never tells the user to buy or sell because of a
  headline or a filing.
- Never hammers a server: one request per feed, half a second between
  calls, the name list at most once a week.

## Safety

- Education and tooling, not financial advice. This skill only reads and reports.
- Nothing in this pack places an order. Claude never logs in to a broker and never sends an order:
  the user enters every order themselves in their own broker app, after trader-risk passes that
  exact order and they have said an explicit yes to it in the chat.
- Practice mode by default (`"paper": true`): write the trade down first, risk nothing.
- Broker passwords, PINs and OTPs never go in the chat.
