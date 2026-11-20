# News clustering sample v1 (#360)

91 real headlines for measuring the news desk's clustering rule offline (`python -m kaibot.news.clustereval`).
No live calls and no LLM calls read these files.

| File | What |
|---|---|
| `sample_v1.jsonl` | `n`, `drawn_for`, `created_at`, `source`, `symbols`, `headline`. Nothing else of the article is stored |
| `labels_v1.jsonl` | `n` -> `story`: the hand label. Headlines about the same news event share a story id (the smallest `n` of the group) |

**Where the headlines come from.** The free Alpaca news endpoint (`/v1beta1/news`, Benzinga), as downloaded by
`scripts/fetch_history.py --news` into a research store (`data/news/symbol=XYZ/all.parquet`). The store was fetched
on **10 Nov 2026** (the file dates). The copy used here was left in a cloud work folder. The draw (21 Nov 2026) took
every stored headline of JPM, XOM, DIS, KO, INTC, UNH and PFE from 2026-10-20 00:00 to 2026-10-23 00:00 UTC, and
of NVDA from 2026-10-20 to 2026-10-21. A headline stored for two of these symbols appears once. No headline was
dropped or chosen by content. The store has no article id, URL or author, so every item is fed to the desk as
source `benzinga`.

**How the labels were made.** By hand, from the headlines alone, before the clustering rule was ever run on them
(the labels and the sample are in one commit, before the commit that adds the measurement). "Same story" means the
same news event reported again, in other words an echo: the AMD and Intel oneDNN announcement twice; JPMorgan's
bitcoin forecast twice; the Turkish unit twice; the Axios AI-agent report twice; the $35 billion Chinese Nvidia
orders three times; RBC's UnitedHealth target twice; CNBC's 20 Oct final trades and its recap; the Shell/Exxon
chemicals sale twice. Different articles about one upcoming event (two SpaceX earnings previews) and a daily
template on different days ("10 Information Technology Stocks With Whale Alerts") count as different stories.
Eight stories have more than one headline. The other 72 headlines are each their own story.

Headlines are untrusted text: data to compare, never instructions.
