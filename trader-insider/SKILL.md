---
name: trader-insider
description: >-
  Insider and Big-Deals Tracker for NSE stocks. Reads the disclosures NSE
  publishes openly after a trade: the last session's bulk deals and block
  deals (which client bought or sold a large quantity, at what price, worth
  how many crore), insider-trading disclosures under the SEBI insider-trading
  (PIT) rules (promoter, promoter group, director or KMP; buy, sell or pledge;
  quantity, value, dates, mode, holding before and after) and takeover-code
  (SAST Regulation 29) disclosures. Alerts once per new item, flags anything
  in a stock you hold, watch or plan to trade, labels a client that bought and
  sold the same stock the same day, shows the delay between the trade and the
  disclosure, and prints how fresh each feed is. Use when the user says "what
  are promoters buying", "insider trades", "bulk deals today", "block deals",
  "who is buying X", "who sold X", "any big deals in my stocks", "did any
  promoter sell", "any pledges", "takeover disclosures", or "check the deals".
argument-hint: "[bulk | insider | sast | new] [--symbol X] [--holdings] [--min-crore N]"
---

# trader-insider

When somebody trades a large chunk of an NSE stock, or a promoter, director
or other insider trades their own company's shares, the rules make them say
so in public. NSE posts those disclosures as plain files. This skill reads
them, notices what is new, and tells you who did what, in which stock, for
how much, and how long after the trade the disclosure came.

It is fast. It is not early. Every item here was filed AFTER the trade: a
bulk deal the same evening, an insider trade usually two to six days later.
There is no inside information here, only public filings read quickly. Say
that every time.

Tool in this folder (standard library Python, no keys, no account). Use
`python3` instead of `python` on macOS/Linux.

```bash
python "${CLAUDE_SKILL_DIR}/deals.py" --help
```

## What it reads and writes

All four sources are files on NSE's public archive host
(`https://nsearchives.nseindia.com/`), fetched with plain GET requests, each
once per run. No login, no cookies, no page scraping.

- `content/equities/bulk.csv` and `block.csv`: the last session's bulk deals
  and block deals. One row per client and side: date, symbol, client name,
  BUY or SELL, quantity, price. NSE posts them in the evening after the close.
- `content/RSS/Insider_Trading.xml`: disclosures under the SEBI (Prohibition
  of Insider Trading) Regulations. Each item links to an XBRL file; the tool
  fetches it and reads the person, the category, the type, the number of
  securities, the value, the trade dates, the mode and the holding before
  and after.
- `content/RSS/Sast_Regulation29.xml`: disclosures under Regulation 29 of the
  takeover code (SAST), made when someone's holding crosses 5% or then
  changes by 2% or more. Company, the acquirer or seller named, date, link.
  The linked files are zips and PDFs: the tool lists the link and does NOT
  open them. Open the link yourself to read the percentages.
- Reads `trader/portfolio.json`, `trader/watchlist.json` and `trader/plan.json`
  (when they exist) to flag items in your stocks.
- Reads `trader/universe.txt` (`SYMBOL|Name`, written by `/trader-scanner`) to
  turn company names into symbols. If it is not there, it downloads NSE's
  `EQUITY_L.csv` once and keeps it as `trader/state/EQUITY_L.csv`.
- Writes `trader/state/insider.json` (what has been shown, so each item alerts
  once) and keeps the XBRL files it has read in `trader/state/insider-xbrl/`.

## How fresh each feed is: read this first

Every run ends with a "How fresh each source is" block. Read it out.

- **Bulk and block deals**: the files hold ONE session. On 2026-11-24 they
  held Monday 2026-11-23, the last completed session. The tool compares the
  file's date with the NSE calendar and says if the file is older than it
  should be.
- **Insider trading feed: STALE when this was written.** On 2026-11-24 the
  newest item in NSE's feed was dated 02-May-2026, 206 days old, although NSE
  had rebuilt the feed file that same night. So the tool cannot show recent
  insider trades from this feed, and it says so in capitals on every run. It
  never presents a May filing as news. If the feed starts moving again the
  tool picks it up with no change. Until then, for recent insider trades the
  user has to open the "Insider Trading" page under Corporate Filings on the
  NSE website themselves.
- **SAST Regulation 29 feed**: live. On 2026-11-24 the newest item was from
  18:19 IST the evening before. It holds the latest 20 items only.

A company is matched to a symbol by its name. Companies that are not in the
list (many on NSE's SME platform, for example) show "no NSE symbol matched"
and are listed by name. The insider XBRL carries the symbol itself.

## The rules

- **Quote the filing.** Every line comes from NSE's file, with the link.
  Names, categories, types and modes are printed as filed, spelling included.
- **Amounts as filed.** Insider values are the rupee figure in the filing,
  with the crore or lakh figure beside it (1 crore = 1,00,00,000 rupees).
  Bulk and block deal values are quantity times price, worked out by the tool.
- **Say the delay.** Each insider row shows the trade date, the date the
  company was told and how many days after the trade it reached the exchange.
- **A bulk deal is one side of a trade.** A row says one client bought or
  sold. The other side is often many small orders that never appear. Do not
  say "X sold to Y" unless both rows are there at the same price and quantity,
  and even then say "the file shows", not "X sold to Y".
- **Bought and sold the same day is not a view.** Trading firms often buy and
  sell the same stock in the same session and end near zero. The tool labels
  it "Bought and sold the same day" with the net shares. Never present it as
  someone accumulating a stock.
- **Promoters sell for many reasons.** Tax, a loan, a family settlement, a
  transfer between group companies. A sale is a fact, not a forecast. The
  same goes for a purchase.
- **A pledge is not a sale.** Shares pledged for a loan, a pledge released
  and a pledge invoked by a lender are three different things. Use the
  filing's words.
- **An ESOP exercise is not a market purchase.** A director who "bought"
  shares with mode ESOP exercised stock options at a fixed price. The tool
  says so on the row. An off-market transfer is not an exchange trade either.
- **When the filing contradicts itself, say so.** Some filings give type
  "Buy" with mode "Market Sale". The tool prints both and says they disagree.
  Do not pick one.

## Step 1: what is new

```bash
python "${CLAUDE_SKILL_DIR}/deals.py" new
python "${CLAUDE_SKILL_DIR}/deals.py" new --holdings          # only your stocks
python "${CLAUDE_SKILL_DIR}/deals.py" new --min-crore 25      # only the bigger deals
```

Shows everything not seen at the last check, across all four sources, and
records it in `trader/state/insider.json`. Exit code 1 means nothing new. On
the first run, items more than 7 days old are recorded without an alert, so
a stale feed does not arrive looking like news. `--min-crore` keeps both
sides of a client's dealing in a stock when either side is that large.
`--dry-run` writes nothing. Once in the evening after about 18:30 IST is
enough for the deal files; the feeds can be checked with `/loop 1h`.

## Step 2: look at one source

```bash
python "${CLAUDE_SKILL_DIR}/deals.py" bulk                    # bulk and block deals, per stock
python "${CLAUDE_SKILL_DIR}/deals.py" bulk --symbol LENSKART "M&M" --min-crore 10
python "${CLAUDE_SKILL_DIR}/deals.py" insider                 # with the detail from each XBRL file
python "${CLAUDE_SKILL_DIR}/deals.py" insider --symbol HCLTECH --json
python "${CLAUDE_SKILL_DIR}/deals.py" sast
```

Every command takes `--symbol X ...`, `--holdings`, `--json`, `--offline DIR`
(read saved files), `--now "2026-11-24 02:30"` (India time) and `--dry-run`.
Items in your holdings, watchlist or trade plan are flagged without asking:
`<- LENSKART is in your holdings`.

## Example: real output

From NSE's files for Monday 2026-11-23, read on 2026-11-24 at 02:22 IST
(`deals.py bulk --symbol LENSKART RATNAVEER POKARNA`):

```
BULK DEALS, session 2026-11-23: 5 rows in 3 stocks
  LENSKART  Lenskart Solutions Ltd
    - STEADVIEW CAPITAL MAURITIUS LIMITED-FDI: sold 11,520,222 at ₹725.00 (₹835.22 crore)
  RATNAVEER  Ratnaveer Precision Eng L
    - ARIHANT CAPITAL MARKETS LIMITED: bought 1,290,929 at ₹391.72 (₹50.57 crore), sold 1,077,569 at ₹393.68 (₹42.42 crore). Bought and sold the same day, net bought 213,360 shares
    - ARTHKUMBH VENTURES LLP: bought 500,000 at ₹390.99 (₹19.55 crore)
  POKARNA  Pokarna Limited
    - CAPRI GLOBAL VENTURES PRIVATE LIMITED: sold 225,000 at ₹660.20 (₹14.85 crore)

BLOCK DEALS, session 2026-11-23: 5 rows in 1 stock
  LENSKART  Lenskart Solutions Ltd
    - STEADVIEW CAPITAL MAURITIUS LIMITED-FDI: sold 11,520,222 at ₹725.00 (₹835.22 crore)
    - SOCIETE GENERALE - ODI: bought 6,176,351 at ₹725.00 (₹447.79 crore)
    - GOLDMAN SACHS INVESTMENTS MAURITIUS I LIMITED: bought 6,176,351 at ₹725.00 (₹447.79 crore)
    - LTR FOCUS FUND-FDI: sold 1,177,480 at ₹725.00 (₹85.37 crore)
    - GOLDMAN SACHS BANK EUROPESE-ODI: bought 345,000 at ₹725.00 (₹25.01 crore)

How fresh each source is:
  Bulk deals: dated 2026-11-23, the last completed NSE session.
  Block deals: dated 2026-11-23, the last completed NSE session.
```

That day's bulk file had 89 rows in 25 stocks, and most of them were trading
firms that bought and sold the same quantity and ended at net 0 shares.

The same morning `deals.py new` (a first check) reported 113 new items: 89
bulk rows, 5 block rows and 19 takeover-code filings, and these lines:

```
  Kfin Technologies Limited (KFINTECH), on the exchange 2026-11-23 18:15 IST
    - Acquirer / seller named in the filing (and persons acting in concert): Wasatch Advisors LP
    https://nsearchives.nseindia.com/corporate/Dis_11232026181546_54163.zip

How fresh each source is:
  Insider trading disclosures: newest item 2026-05-02 16:46 IST, 206 days old. STALE: NSE's feed has nothing newer, so recent filings are NOT here (the feed file itself was rebuilt Tue, 24 Nov 2026 01:50:07 +0530).
  Takeover-code disclosures: newest item 2026-11-23 18:19 IST, 1 day old.
  12 older items (more than 7 days old) recorded on this first check without an alert.
```

And what an insider row looks like (`deals.py insider`, from that stale feed,
so these are April and May filings, shown here only for the shape):

```
  Ravindra Energy Limited (RELTD), on the exchange 2026-05-02 16:46 IST
    - Shantanu Lath (Director) bought 70,000 Equity Shares, value as filed ₹70,00,000 (₹70.00 lakh), which works out to ₹100.00 each. Mode as filed: ESOP (an ESOP exercise or allotment, not a market purchase).
      Holding 50,000 (0.03%) -> 120,000 (0.07%). Traded 2026-04-29, told the company 2026-05-02, on the exchange 3 days after the trade.
  Mangalam Worldwide Limited (MWL), on the exchange 2026-05-02 14:20 IST
    - VIPIN PRAKASH MANGAL (Promoters) pledged 2,212,300 Equity Shares, value as filed ₹68,23,47,220 (₹68.23 crore), which works out to ₹308.43 each. Mode as filed: Pledge Creation on NSE (shares pledged as security for a loan, not a sale in the market).
      Holding 6,042,800 (20.35%) -> 1,530,500 (5.15%). Traded 2026-04-28 to 2026-04-29, told the company 2026-04-30, on the exchange 3 days after the trade.
```

Then say what it is: "These are public filings made after the trade. The
Lenskart rows say who dealt in the block window on Monday, not what anyone
will do next."

## If the user wants to act on it

It goes the long way. A disclosure is a reason to look at a chart, not a
setup. `/trader-scanner` and `/trader-entry` decide if there is a trade
today; `/trader-exit` sets the stop and the target first; `/trader-timemachine`
replays it; `/trader-budget` sizes it; `/trader-risk` checks it; and only
then `/trader-broker` shows a ticket, which the user types into their own
broker app after an explicit yes.

## What it never does

- Never says or implies that anyone has inside information, or that copying
  a promoter, a fund or a trading firm beats the market. No return claims.
- Never calls a same-day buy and sell "accumulation", a pledge a "sale" or
  an ESOP exercise a "market purchase".
- Never presents a stale feed's items as new, and never fills the gap with a
  guess. If the feed is stale it says so.
- Never opens or summarises the SAST zip and PDF files, and never states a
  percentage it did not read.
- Never calls NSE's website API, logs in anywhere, or scrapes a page. Only
  the openly published files listed above.
- Never places an order.

## Safety

- Education and tooling, not financial advice. Nothing here is a
  recommendation to buy or sell anything.
- These are PUBLIC disclosures made after the trade, under the exchange's
  bulk and block deal rules, the SEBI insider-trading (PIT) regulations and
  the takeover code (SAST). This skill reads them fast; it has no inside
  information, and it says so.
- Nothing in this pack places an order. Claude never logs in to a broker and
  never sends an order: the user enters every order themselves in their own
  broker app, after `/trader-risk` passes that exact order and they have said
  an explicit yes to it in the chat.
- Practice mode by default (`"paper": true` in `trader/config.json`): write
  the trade down first, risk nothing.
- Most people who day trade lose money. A large deal or an insider's trade
  in a stock does not change that.
- Broker passwords, PINs and OTPs never go in the chat.
