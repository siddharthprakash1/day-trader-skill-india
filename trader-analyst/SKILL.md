---
name: trader-analyst
description: >-
  The Earnings Analyst for NSE stocks. Lays out a company's reported numbers
  quarter by quarter, in rupees crore: revenue, gross, operating, EBITDA and
  net margins, net income and diluted EPS, each against the same quarter a
  year earlier, the change on the quarter before, and the last four quarters
  added up. Figures come from Yahoo Finance's free fundamentals feed, which
  has holes for Indian companies, so it prints every gap ("no figure from
  Yahoo for the quarter ending ...") and never computes growth or a trailing
  total across one. It also reads NSE's financial-results, corporate-action
  and board-meeting feeds to say whether a report or an ex-date falls inside
  the trade. Use when the user says "how did TCS's results look", "analyze
  the earnings", "what changed this quarter", "is revenue growing", "compare
  margins", "did it just report", "does it report this week", "any ex-date
  coming", or before trading a stock around its results.
argument-hint: "[symbols]"
---

# trader-analyst

Results move a stock more than anything else on its calendar, and the press
release is written to put the best number first. This skill lines the numbers
up quarter by quarter and says plainly what changed. For a day trader it
answers two questions before a trade: what did the last report say, and does
a report or a corporate action land inside this trade?

Tool in this folder (standard library Python, no keys, no account). Use
`python3` instead of `python` on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/facts.py" --help
```

## What it reads and writes

- Reads Yahoo Finance's unofficial fundamentals-timeseries endpoint for
  `SYMBOL.NS`, one request per symbol: quarterly and yearly revenue, gross
  profit, operating income, EBITDA, net income, diluted EPS, unusual items
  and net interest income.
- Reads three public NSE feeds, once per run: `Financial_Results.xml`,
  `Corporate_action.xml` and `Board_Meetings.xml` on NSE's archive host.
  Their titles are company names, not symbols, so the tool matches them with
  `trader/universe.txt` (written by `/trader-scanner`'s `scan.py universe`)
  or, when that is missing, NSE's `EQUITY_L.csv`, downloaded once and kept in
  `trader/state/EQUITY_L.csv`.
- Writes `trader/metrics.json` with `--metrics`: one row per company (latest
  quarter, last-four-quarter totals, growth, margins, and which quarters
  Yahoo is missing), for your notes or a sheet. `metrics.example.json` in
  this folder is a real run for HDFCBANK, M&M, RELIANCE, SBIN, TCS and ZEEL
  on 2026-11-24.
- Amounts are in rupees crore (1 crore = 10,000,000 rupees), EPS in rupees,
  times in India Standard Time.

## How good the data is (read this first)

Yahoo's quarterly data for Indian companies is thin and has holes. Checked on
2026-11-24 across ten symbols:

- It carries about five quarters at most, so growth against a year earlier is
  possible for one or two quarters only.
- TCS, RELIANCE, M&M, ITC and ZEEL came back without the quarter ending
  2025-09-30. SBIN and ICICIBANK had revenue for that quarter but no net
  income or EPS. INFY had revenue but no EPS.
- HDFCBANK had two quarters only, the newest ending 2025-06-30: five quarters
  behind.
- TATAMOTORS (the symbol from before the 2025 demerger) returned nothing.
- INFY's figures come in US dollars. The tool prints them in USD millions and
  says so. It does not convert.
- Banks have no gross profit, operating income or EBITDA lines.
- Yahoo does not say whether a figure is consolidated or standalone. The tool
  prints "not stated" every time. Check against the filing.

So treat the table as a fast first read, and open the company's results
filing before acting on any number in it.

## The rules

- **Compare like with like.** Growth is against the same quarter a year
  earlier, and only when Yahoo has that quarter. Quarter-on-quarter change is
  shown, but many businesses are seasonal.
- **Never across a hole.** A missing quarter is printed as "no figure from
  Yahoo for the quarter ending ...". No growth is computed against it, no
  change on the quarter after it, and the last four quarters are added up
  only when all four are there, back to back. Repeat the gap in your summary.
- **Blank is not zero.** A dash is a figure Yahoo does not have. A bank's
  gross margin is blank.
- **Say what is derived.** Margins, growth and the last-four-quarter totals
  are worked out by the tool from Yahoo's figures; the EPS total is the four
  quarterly EPS figures added up. Fiscal labels (Q2 FY27) come from the month
  the company's yearly figures end in.
- **Say when the data is old.** When SEBI's deadline for a later quarter has
  passed and Yahoo still does not have it, the tool says how many quarters
  behind Yahoo is. Do not describe old figures as the latest results.
- **Flag one-offs.** The tool calls out unusual items that are 5% or more of
  net income, net income above operating income (gains outside the
  business), and EPS moving very differently from net income (a bonus issue,
  split or buyback changed the share count).
- **Never guess the next results date.** It is usually not in these free
  sources. The tool prints SEBI's deadline (45 days after a quarter ends, 60
  after the financial year ends) and where to look: the company's investor
  relations page, or its board-meeting intimation on the NSE or BSE
  corporate filings page.
- **The feeds are short rolling windows.** When checked they held 3, 7 and 0
  items. "Nothing in the feeds" is not "nothing scheduled".

## Steps

1. Run it for the companies in question, or with no symbols for everything on
   `trader/watchlist.json`:

   ```bash
   python "${CLAUDE_SKILL_DIR}/facts.py" TCS
   python "${CLAUDE_SKILL_DIR}/facts.py" TCS HDFCBANK "M&M" --metrics trader/metrics.json
   ```

   `--quarters N` sets how many quarters to show, `--json` gives everything
   machine-readable, `--no-events` skips the NSE feeds, `--now "2026-11-24
   09:00"` pins the clock (India time). Exit code 0 when every symbol has
   figures, 1 when some do, 2 when none do.
2. Read the "Gaps" block before the table. If the quarter you care about is
   missing or the figures are old, say so first and get the numbers from the
   company's results filing instead.
3. Write the summary: one line on the quarter (revenue, net income, EPS, and
   growth only where the tool printed it), one on margins, one on anything
   unusual (a one-off, a gap, old data), and whether the figures are stated
   as consolidated (they are not). Plain words, real numbers, no adjectives
   the numbers do not support.
4. Read the "Results and actions" lines. If the tool prints INSIDE TODAY'S
   TRADE (results filed today, a board meeting today, or an ex-date today),
   say it before `/trader-exit` sets the stop: a stop does not protect
   against a gap or a halt. If nothing is listed, say the feeds are short
   windows and point to the company's announcements for the date.

## Example (a real run, 2026-11-24 02:24 IST, an NSE holiday)

`facts.py TCS HDFCBANK RELIANCE "M&M" SBIN ZEEL --metrics trader/metrics.json`
printed, for TCS:

```
Tata Consultancy Services Limited (TCS). ₹ crore except EPS (₹). Source: Yahoo Finance (TCS.NS), fetched 2026-11-24 02:24 IST.
Yahoo does not state whether these figures are consolidated or standalone: check them against the company's results filing.

quarter end fiscal     revenue  rev yoy   gross    oper  EBITDA     net   net inc  EPS dil  EPS yoy
2026-09-30  Q2 FY27     73,188        -   40.9%   24.0%   27.5%   19.0%    13,884    38.37        -
2026-06-30  Q1 FY27     72,275   +13.9%   44.8%   24.1%   26.9%   18.5%    13,349    36.90    +4.6%
2026-03-31  Q4 FY26     70,698        -   41.2%   25.3%   28.3%   19.4%    13,718    37.94        -
2025-12-31  Q3 FY26     67,087        -   45.4%   25.2%   23.8%   15.9%    10,657    29.45        -
2025-09-30  Q2 FY26    no figure from Yahoo for this quarter
2025-06-30  Q1 FY26     63,437        -   44.4%   24.5%   29.2%   20.1%    12,760    35.27        -
A dash is a figure Yahoo does not have, or growth that cannot be worked out without one. It is never zero.

Gaps (nothing is estimated or computed across them):
  - no figure from Yahoo for the quarter ending 2025-09-30.

Last 4 quarters (2025-12 to 2026-09): revenue ₹283,248 cr (₹2.83 lakh crore), net income ₹51,608 cr, diluted EPS ₹142.66.

What changed:
  - Quarter ending 2026-09-30 (Q2 FY27): revenue ₹73,188 cr, net income ₹13,884 cr, diluted EPS ₹38.37.
  - No growth against a year earlier: Yahoo has no figure for the quarter ending 2025-09-30.
  - Gross margin 40.9% (-3.9 points vs the quarter before).
  - Operating margin 24.0% (-0.1 points vs the quarter before).
  - EBITDA margin 27.5% (+0.6 points vs the quarter before).
  - Net margin 19.0% (+0.5 points vs the quarter before).
  - Revenue +1.3% vs the quarter before (many businesses are seasonal: compare with a year earlier first).

Results and actions (NSE feeds):
  - Nothing for TCS in NSE's results, corporate-action or board-meeting feeds.
  - Next results: Yahoo has the quarter ending 2026-09-30. The next one ends 2026-12-31; SEBI's deadline for its results is 2027-02-14, and the company picks the day. The date is not in these free sources: look at the company's investor relations page, or its board-meeting intimation on the NSE or BSE corporate filings page.
```

(The yearly table, FY23 to FY26, is cut from this paste.) The honest summary:
"TCS's September 2026 quarter on Yahoo: revenue ₹73,188 crore, net income
₹13,884 crore, EPS ₹38.37, net margin 19.0%. I cannot give you growth
against a year earlier: Yahoo is missing the September 2025 quarter, so
check the results filing for that. Yahoo does not say if these are
consolidated."

From the same run:

- HDFCBANK: two quarters only, and "Yahoo stops at the quarter ending
  2025-06-30: 5 later quarters (2025-09-30 to 2026-09-30) should have been
  reported by now and are not here. These figures are old." Gross, operating
  and EBITDA margins are dashes. No trailing total.
- ZEEL: `ex-date 2026-11-25 (in 1 day): BUY BACK, record date 25-Nov-2026`,
  then "Nothing for ZEEL lands today in NSE's feeds, but ex-date 2026-11-25
  ahead: a trade closed today is not exposed, anything held past it is."
- The feeds: financial results 3 items (newest 2026-11-03), corporate
  actions 7 items, board meetings 0 items. The board-meetings feed has been
  empty every time it was checked, so that part of the tool is tested only
  on a made-up item.

## What it never does

- Never says a stock is cheap, expensive, a buy or a sell. It reports what
  Yahoo carries and what NSE's feeds list.
- Never invents a number, fills a missing quarter, or estimates one from the
  yearly figure.
- Never computes growth or a last-four-quarter total across a hole.
- Never guesses a results date.
- Never converts a currency, and never calls a figure consolidated when
  Yahoo does not say so.
- Never calls NSE's website API or any page behind a bot check: only the
  public archive feeds, with plain requests, one per file per run.

## Safety

Education and tooling, not financial advice. This skill only reads figures
and feeds. Nothing in this pack places an order: Claude never logs in to a
broker and never sends an order. The user enters every order themselves in
their own broker app, after `/trader-risk` passes that exact order and they
have said an explicit yes to it in the chat. Most people who day trade lose
money, and a clean set of results does not change that.
