# Headline tag sample v1 (story S105, #379)

300 real headlines, labelled by hand, used to measure `kaibot.news.tags`.

| File | What |
|---|---|
| `headlines_v1.jsonl` | the raw sample: `n`, `request`, `source`, `source_id`, `created_at`, `symbols`, `headline`. Nothing else of the article is stored |
| `labels_v1.jsonl` | the hand labels, one line per headline (`n`, `tags`), written before any rule existed |
| `draw.py` | the seeded request list (`python -m kaibot.news.tagset.draw`); it fetches nothing |
| `measure.py` | precision, recall and the misses per tag (`python -m kaibot.news.tagset.measure`) |

Headlines are untrusted text. They are data to match patterns on; nothing written in one is ever followed.

## How the sample was drawn (19 Nov 2026)

Source: the Alpaca news endpoint (`/v1beta1/news`, the one the engine's poller reads), through the read-only
`get_news` tool of the Alpaca paper connector. No other source, no scraping, and no other tool of that connector.

1. `draw.py` with `SEED = 379105` draws 37 requests. Each is one minute drawn uniformly between
   2026-04-29 00:00 UTC and 2026-11-18 00:00 UTC, and 8 companies drawn from the S&P 500 members in force on that
   date (`src/kaibot/ingest/sp500/changes.csv` as committed at `935ee25`).
2. Each request asks for the first 10 headlines at or after that minute for those 8 symbols
   (`start=<minute>`, `sort=asc`, `limit=10`, no `end`, `include_content=false`).
3. The requests were sent in order, one at a time, several seconds apart. The run stopped when 300 distinct
   headlines were in hand: after request 30, which is exactly 30 x 10 with no duplicate id or headline.
   Requests 31 to 37 of the list were not sent.
4. Every headline returned was kept, in the order returned. Nothing was dropped or chosen by content.

Requests sent for the story: 33 of the 40 allowed (3 in the first attempt below, 30 here).

### The 30 requests

| # | start (UTC) | symbols |
|---|---|---|
| 1 | 2026-10-14T06:42:00Z | AAPL,DUK,EVRG,GPC,KLAC,NVR,PANW,VTRS |
| 2 | 2026-10-04T20:08:00Z | AMZN,COR,DG,HCA,KO,MMM,STZ,WAB |
| 3 | 2026-08-12T11:04:00Z | BRK.B,CDNS,COHR,DD,ED,IP,NTRS,SWK |
| 4 | 2026-07-13T14:43:00Z | AWK,BG,BIIB,CL,CRM,EQIX,ESS,RSG |
| 5 | 2026-08-03T05:35:00Z | ABBV,APP,BAC,ED,EXPE,IR,MMM,SYY |
| 6 | 2026-08-31T16:22:00Z | BG,EOG,GNRC,LII,LLY,LNT,MGM,RDDT |
| 7 | 2026-06-08T16:45:00Z | EFX,EOG,GD,GPC,GWW,HIG,LVS,MSCI |
| 8 | 2026-07-07T17:15:00Z | ACN,ALLE,DHI,GWW,MLM,RMD,RSG,WTW |
| 9 | 2026-11-05T15:00:00Z | AEP,ANET,HAL,HII,NKE,PH,PSKY,ROK |
| 10 | 2026-09-20T04:11:00Z | BRK.B,EL,INTC,INTU,LIN,MTD,TFC,V |
| 11 | 2026-08-24T01:08:00Z | EME,FDX,J,RCL,RDDT,SBUX,TMO,WDC |
| 12 | 2026-10-30T06:11:00Z | CB,ERIE,LNT,NUE,PNC,PNW,PPL,XYL |
| 13 | 2026-05-07T10:53:00Z | BEN,CVS,GD,JBHT,PRU,UBER,WDC,WMT |
| 14 | 2026-08-12T14:22:00Z | AVY,CASY,IRM,KHC,MRVL,NSC,RTX,TEL |
| 15 | 2026-07-25T03:56:00Z | ADI,CB,D,FE,LRCX,OTIS,PSKY,RJF |
| 16 | 2026-10-25T04:40:00Z | CPT,EMR,EXPD,GWW,LLY,MA,OXY,SNPS |
| 17 | 2026-08-30T18:02:00Z | ADP,FOX,HSIC,NCLH,PNR,ROST,SW,VRT |
| 18 | 2026-05-10T03:00:00Z | BAC,BKR,CSGP,CVNA,JNJ,NTRS,ULTA,UPS |
| 19 | 2026-05-29T23:29:00Z | ARES,BR,CCI,CF,IBM,PFE,SYK,ULTA |
| 20 | 2026-09-07T22:57:00Z | AMT,ARES,BMY,BRO,DG,EXE,IVZ,L |
| 21 | 2026-09-22T17:40:00Z | BXP,CSX,DG,EIX,GOOG,HD,IFF,WYNN |
| 22 | 2026-09-16T14:23:00Z | ADSK,BKNG,CAT,ETN,LIN,MRK,MRSH,MSCI |
| 23 | 2026-09-09T10:49:00Z | DIS,EL,NOC,NTAP,PGR,SBUX,TSLA,V |
| 24 | 2026-10-23T03:38:00Z | COP,HST,JKHY,KHC,LHX,PH,SYY,ZBRA |
| 25 | 2026-11-02T19:35:00Z | BLK,DOC,EG,EMR,NDSN,SWKS,TRV,WFC |
| 26 | 2026-06-09T02:43:00Z | BBY,CBOE,EQR,MPC,PANW,SMCI,TECH,USB |
| 27 | 2026-09-24T13:32:00Z | AVGO,C,CPAY,L,LOW,MPC,PFE,SWK |
| 28 | 2026-11-03T05:00:00Z | AXP,PANW,PM,RVTY,STX,TGT,TRMB,WM |
| 29 | 2026-07-03T05:49:00Z | ACGL,CRL,EVRG,HII,NUE,PNR,RJF,TSCO |
| 30 | 2026-10-29T14:57:00Z | AJG,BBY,CNP,MAR,MPC,MPWR,ORCL,ORLY |

### First attempt, and what it showed about the feed

The first list used the same seed with dates from 2016-01-04 to 2026-10-30, because the design
(`docs/research/event_driven/design.md`, 2.2) expects Benzinga headlines from 2015. Three requests were sent:

| # | request | result |
|---|---|---|
| 1 | `start=2022-08-29`, `AAL,DUK,ETR,HAL,INCY,NVR,OXY,PG`, `sort=asc`, `limit=9` | 9 headlines, the earliest dated 2026-04-29 |
| 2 | `start=2021-11-22T00:00:00Z`, `AMGN,COF,DHI,HON,LDOS,MMM,STT,VZ`, `sort=asc`, `limit=9` | 9 headlines, the earliest dated 2026-04-29 |
| 3 | as 2, with `end=2021-12-22T00:00:00Z` | no headline |

So on 19 Nov 2026 this endpoint, with the key behind that connector, **returned nothing older than 29 Apr 2026**,
and every headline carried `source: "fiscal.ai"`, none `benzinga`. The 18 headlines of requests 1 and 2 are not in
the sample (they are the start of the feed, not a random date). The draw was then repeated over the months the
endpoint does cover, which is the sample above.

## What this sample is, and is not

- It is **not** "across years": it covers 7 May to 5 Nov 2026 by request date (headlines dated 7 May to
  6 Nov 2026). The story's "300 headlines across years" needs either an endpoint that still serves the older
  Benzinga headlines or an export of stored headlines.
- It is the feed as served on the draw date: `fiscal.ai` headlines. They are written in a different style from the
  Benzinga headlines the design describes (for example "Adjusts Price Target on X to $90 From $95", where Benzinga
  writes "Raises" or "Lowers"). Numbers measured here say nothing about the older Benzinga wording.
- A symbol in this feed is not always the S&P 500 company. Several headlines are about a foreign company with the
  same ticker (`BBY` Balfour Beatty, `DG` Vinci, `EL` EssilorLuxottica, `BMY` Bloomsbury, `L` Loblaw, `STX` Shield
  Therapeutics, `EOG` Europa Oil & Gas, `COR` Camino Minerals, `BG` BAWAG, `AAL` Anglo American) or about a bank's
  research on another company (`RJF`, `BAC`, `CF`). A tag describes the headline, not the symbol attached to it.
- The headlines were typed into the fixture from the tool's answers by the agent that drew them (the tool returns
  text to the session, not a file). Each line keeps the provider's `source_id` so it can be checked.

## How the labels were made

All 300 headlines were read one by one and labelled before `kaibot/news/tags.py` existed (the labels are their
own commit, before the rules). The labeller was the same agent that drew the sample, not a second person, and it
had seen each article's one-line summary while drawing. The labels use **the headline text alone**, because that
is all the function sees. The labels are not edited after the rules are run; a label that looks wrong afterwards
is listed as "label disputed" in the results below and stays as it is.

The guide that was applied:

| Tag | Yes | No |
|---|---|---|
| `results` | the headline reports a company's periodic financial results as released (quarter or year; a "trading update") | a date for future results, an earnings call, analysts reacting "following earnings", clinical-trial or study results, a quarterly production or market report |
| `guidance_up` / `guidance_down` | the company raises / cuts its own forecast | guidance issued or updated with no direction in the headline |
| `analyst_up` | an upgrade; a price target raised, including "adjusts ... to $X from $Y" with X above Y | "adjusts price target" with no direction or only the new figure; a new coverage ("initiates at Buy"); a rating maintained |
| `analyst_down` | a downgrade; a price target cut, including "to $X from $Y" with X below Y | as above |
| `deal` | the headline is about an acquisition, a merger or the sale of a business: announced, agreed, or its progress (delay, a regulator's step) | a minority stake or investment, a strategic review, a licence farm-out, a small tender for shares |
| `offering` | new shares or debt being sold, an IPO included | a programme prospectus with no sale, a credit facility, a holder or insider selling existing shares |
| `buyback` | the company buying back its shares, a routine disclosure of such purchases included | |
| `management_change` | a named or titled person at manager level or above is appointed, hired, promoted or leaves; a succession or "leadership changes" | a board "reaffirming confidence", a former officer becoming an adviser elsewhere, a firm appointed as adviser |
| `legal_regulatory` | a court, a regulator or an investigator acts on or about the company: a lawsuit, a ruling, an investigation (a law firm's too), an approval, a denial, a designation, a proposed rule, a filing for approval | a routine holdings notification, a layoff notice, a data breach, political criticism, being picked for a regulator's pilot |
| `listing` | a new listing of a security on an exchange (an IPO, a depositary receipt, an ETF named with its exchange) or an index change | a ticker change, a ranking that is not an index |

A tag describes the headline, not the symbol attached to it (see above). A headline may carry several tags.

Counts of positives in the 300: `analyst_up` 32, `legal_regulatory` 19, `deal` 12, `analyst_down` 11,
`results` 10, `management_change` 10, `offering` 4, `listing` 3, `buyback` 2, `guidance_up` 2, `guidance_down` 0.
