# Production-grade trading engine architecture: open-source evidence and a recommendation for Kaibot (researched 12 Nov 2026)

Reading notes for the writer:
- Every bullet under "Cited Findings" was read in a fetched page this session. Anything I know only from prior knowledge is in "Gaps" and labelled as unverified.
- Several sources are secondary (blog comparisons, mirrors of GitHub pages). They are labelled where used.
- "ET" means US Eastern Time throughout.

---

## 1. Architecture of the main open-source engines as of 2026

### Takeaway
Only three of the named engines are both actively released in 2026 and able to trade US stocks live through Alpaca: LEAN (C# core, paid tier needed for its CLI), Lumibot (Python, MIT, very frequent releases) and, on paper, Blankly (stalled since March 2024). NautilusTrader is the strongest architecture reference but has no Alpaca adapter and is mid-way through a v1 (Cython) to v2 (Rust) migration with only release candidates of v2 available.

### Cited Findings

#### Summary table

| Engine | Core design | Backtest-live parity mechanism | Alpaca | Language | Licence | Health (Nov 2026) |
|---|---|---|---|---|---|---|
| NautilusTrader | Event-driven, single-threaded core, message bus | Same kernel in Backtest / Sandbox / Live contexts | Not in the 17 official integrations | Rust core, Python control plane | LGPL-3.0-or-later | Active; v1.231.0 (2 Aug 2026) is the intended last 1.x; v2 at 2.0.0rc9 |
| QuantConnect LEAN | Event-driven streaming "timeslices" | Same algorithm, pluggable brokerage and reality models | Yes (brokerage and data provider) | C# engine, Python 3.11 via Python.NET | "Open-source" (licence name not on the pages read) | Active; Alpaca break fixed 6 Jul 2026 |
| Zipline-reloaded | Event loop plus Pipeline API | None: backtest only | No live trading | Python/Cython | Not verified | Latest release 3.1.1 (23 Jul, year not shown); "limited" maintenance |
| backtrader | Event-driven (`next()`, `notify_order()`) | Broker/store plug-ins | Via `alpaca-backtrader-api`, last pushed Dec 2024 | Python | GPL-3.0 (secondary source) | Not maintained |
| vectorbt / PRO | Vectorised arrays | None native; PRO is a research and signal layer | No | Python/Numba (PRO: optional Rust extension) | Open version "source-available"; PRO paid, private repo | PRO v2026.10.5 |
| Freqtrade | Not stated in sources | Dry-run/live modes | No (crypto exchanges only) | Python | GPL-3.0 (secondary source) | Active; releases 2026.8, 2026.9, 2026.10 |
| pysystemtrade | Backtest plus production system | One project for both | No (Interactive Brokers, futures) | Python | GPL v3 | Maintained; moved to `pst-group` org Jan 2026 |
| Lumibot | Iteration lifecycle (`on_trading_iteration`) | "Just swap the broker class" | Yes | Python | MIT (PyPI and repo); one blog says GPL-3.0 | Very active; 4.6.46 on 3 Nov 2026 |
| Blankly | Not stated | Same strategy, exchange swap | Yes (listed "working") | Python | LGPL-3.0 | Stalled; last push 31 Mar 2024 |

#### NautilusTrader
- Describes itself as an "open-source, production-grade, Rust-native engine for multi-asset, multi-venue trading systems", with Python as "the control plane for strategy logic, configuration, and orchestration" — [NautilusTrader overview](https://nautilustrader.io/docs/latest/concepts/overview/)
- Parity claim: "The same strategy and execution-algorithm code can run across backtest and live systems, reducing deployment divergence." The same page warns that live execution adds venue, transport, timing, persistence and reconciliation behaviour that simulation may not reproduce — [NautilusTrader overview](https://nautilustrader.io/docs/latest/concepts/overview/)
- Three environment contexts share one kernel: Backtest (historical data, simulated execution), Sandbox (real-time data, simulated execution), Live (real-time data, live venue). "Backtest, sandbox, and live systems share the `NautilusKernel` struct" — [NautilusTrader architecture](https://nautilustrader.io/docs/latest/concepts/architecture/)
- Components: NautilusKernel, MessageBus (pub/sub, request/response, command/event), Cache (instruments, accounts, orders, positions in memory, optional database backing), DataEngine, ExecutionEngine, RiskEngine (validates fields, balances, quantities, notionals, reduce-only, trading state, plus submission and modification rate limits), Portfolio (balances, net positions, margin, PnL, exposure), Trader — [NautilusTrader architecture](https://nautilustrader.io/docs/latest/concepts/architecture/)
- Order path: strategy `submit_order` → RiskEngine (a failed check yields `OrderDenied` and the order never reaches the venue) → ExecutionEngine → venue `ExecutionClient` → venue events (Accepted, Filled, Canceled, Rejected, Expired) update the Cache and notify the strategy — [NautilusTrader architecture](https://nautilustrader.io/docs/latest/concepts/architecture/)
- Threading: "the core consumes and dispatches messages on a single thread"; network adapters, logging and persistence run on other threads and pass events in through channels — [NautilusTrader architecture](https://nautilustrader.io/docs/latest/concepts/architecture/)
- Crash-only design: release builds abort on panic so that an external supervisor restarts the process; recovery runs through normal start-up; venue commands are not safe to retry blindly, so reconciliation handles that boundary — [NautilusTrader architecture](https://nautilustrader.io/docs/latest/concepts/architecture/)
- Component state machine: stable states PRE_INITIALIZED, READY, RUNNING, STOPPED, DEGRADED, FAULTED, DISPOSED, plus transitional states — [NautilusTrader architecture](https://nautilustrader.io/docs/latest/concepts/architecture/)
- Order model: MARKET, LIMIT, STOP_MARKET, STOP_LIMIT, MARKET_TO_LIMIT, MARKET_IF_TOUCHED, LIMIT_IF_TOUCHED, trailing stops; time-in-force values include `AT_THE_OPEN` and `AT_THE_CLOSE`; contingencies OCO, OUO, OTO — [NautilusTrader overview](https://nautilustrader.io/docs/latest/concepts/overview/)
- Latest stable is 1.231.0, released 2 August 2026; licence LGPL-3.0-or-later; Python >=3.12,<3.15; wheels for Linux x86_64, Linux ARM64, macOS ARM64 and Windows x86_64 (no Intel macOS wheels) — [PyPI nautilus-trader](https://pypi.org/project/nautilus-trader)
- The PyPI page lists 17 integrations (AX, Betfair, Binance, BitMEX, Bybit, Coinbase, Databento, Deribit, Derive, dYdX, Hyperliquid, Interactive Brokers, Kraken, Lighter, OKX, Polymarket, Tardis). Alpaca is not among them; Interactive Brokers is the only equities brokerage — [PyPI nautilus-trader](https://pypi.org/project/nautilus-trader)
- The project aims for a bi-weekly release schedule, says the API is becoming more stable but "breaking changes can occur between releases", and plans a formal deprecation process only once the 2.x API is stable — [PyPI nautilus-trader](https://pypi.org/project/nautilus-trader)
- v1.231.0 is "intended to be the final 1.x release with the legacy Cython v1 core". The v2 Rust + PyO3 runtime is at release-candidate status; `develop` becomes v2-only; v1 gets critical security backports for about three months; published tutorials still use v1; some v2 features are deferred (Redis-backed `LiveNode` injection, SQL cache position loads). The newest version referenced is 2.0.0rc9. The release notes mention no Alpaca or new equities adapter — [newreleases.io, nautilus_trader v1.231.0](https://newreleases.io/project/github/nautechsystems/nautilus_trader/release/v1.231.0)
- Secondary comparison: about 19K GitHub stars, "steep learning curve", 5M+ rows per second, nanosecond timestamps — [autotradelab comparison, via reader mirror](https://hn.jakechampion.name/reader/https://autotradelab.com/blog/nautilus-vs-vectorbt-vs-freqtrade-20-python-quant-trading-frameworks-compared) (undated article)

#### QuantConnect LEAN
- LEAN is "an open-source algorithmic trading engine built for easy strategy research, backtesting, and live trading"; core in C#; algorithms in Python 3.11 or C#; "LEAN uses Python.Net to bridge between the C# engine and your algorithm", and cross-language calls are slow — [LEAN algorithm engine](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/algorithm-engine)
- Each timeslice runs a fixed sequence: fills, margin calls, settlement, dividends, splits, delistings, `on_data`, universe selection, then the Alpha, Portfolio Construction, Risk Management and Execution models — [LEAN algorithm engine](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/algorithm-engine)
- Look-ahead prevention: "you can't access price data beyond the Time Frontier"; LEAN streams data instead of loading it in batch — [LEAN algorithm engine](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/algorithm-engine)
- The same algorithm runs in backtest and live "with no code changes"; in live mode order events arrive asynchronously from the brokerage thread and scheduled events run on a separate thread — [LEAN algorithm engine](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/algorithm-engine)
- LEAN CLI: "To use the CLI, you must be a member in an organization on a paid tier." Local US Equity algorithms need specific QuantConnect datasets; Alpaca's data provider serves raw equity data, and adjusted prices need the US Equity Security Master download — [LEAN CLI, Alpaca brokerage](https://www.quantconnect.com/docs/v2/lean-cli/live-trading/brokerages/alpaca)
- Alpaca integration fragility: Alpaca removed five pattern-day-trader account fields; new live Alpaca deployments on QuantConnect failed at initialisation ("Required property 'pattern_day_trader' not found in JSON") until staff fixed it on 6 July 2026; users had no workaround — [QuantConnect forum 21072](https://www.quantconnect.com/forum/discussion/21072/alpaca-brokerage-api-no-longer-compatible-with-quantconnect/p1)
- Daily-resolution pitfall with Alpaca: the portfolio construction model fired at 16:00 New York time, LEAN converted the market orders to market-on-open, and Alpaca rejected them ("opg orders must be submitted after 7:00pm and before 9:28am"). The accepted fix was a custom Execution Model that schedules the MOO order for 19:01 — [QuantConnect forum 18268](https://www.quantconnect.com/forum/discussion/18268/day-resolution-trade-and-have-issue-with-alpaca/) (post date not shown; a filename timestamp suggests about November 2024)
- Secondary comparison: about 16K stars, 20+ brokers, "C# core shows through", cloud costs scale — [autotradelab comparison](https://hn.jakechampion.name/reader/https://autotradelab.com/blog/nautilus-vs-vectorbt-vs-freqtrade-20-python-quant-trading-frameworks-compared)

#### Zipline-reloaded
- Latest release is 3.1.1 dated 23 Jul (the releases page shows no year): Python 3.13 compatibility, NumPy 2.0 compatibility. Before it: 3.1 (26 Sep), 3.0.4 (15 May, Python 3.12) — [zipline-reloaded releases](https://github.com/stefan-jansen/zipline-reloaded/releases)
- Rated "limited" maintenance, last release "8+ months ago", backtesting only — [autotradelab comparison](https://hn.jakechampion.name/reader/https://autotradelab.com/blog/nautilus-vs-vectorbt-vs-freqtrade-20-python-quant-trading-frameworks-compared)
- Research-oriented, built around the Pipeline API with a data-bundle system; installation friction; about 25 minutes to a first backtest including bundle ingestion — [DEV Community comparison](https://dev.to/pickuma/python-backtesting-frameworks-compared-backtrader-vectorbt-and-zipline-reloaded-41l8)

#### backtrader
- "Not maintained; creator declared it 'complete'"; about 15K stars; live trading "possible, but dated" — [autotradelab comparison](https://hn.jakechampion.name/reader/https://autotradelab.com/blog/nautilus-vs-vectorbt-vs-freqtrade-20-python-quant-trading-frameworks-compared)
- Event-driven; logic in `next()` and `notify_order()`; direct live connections to Interactive Brokers and Oanda; a 5-year test took about 14.2 s against 0.7 s for vectorbt and 2.9 s for Zipline-reloaded — [DEV Community comparison](https://dev.to/pickuma/python-backtesting-frameworks-compared-backtrader-vectorbt-and-zipline-reloaded-41l8)
- Alpaca's own `alpaca-backtrader-api` bridge: last pushed 9 December 2024, 645 stars, Apache-2.0 — [ecosyste.ms, alpaca-backtrader-api](https://awesome.ecosyste.ms/projects/github.com%2Falpacahq%2Falpaca-backtrader-api)

#### vectorbt and VectorBT PRO
- vectorbt: vectorised (strategies as multi-dimensional arrays), no native live trading, "source-available, not open-source", about 4.3K stars; PRO adds event-driven callbacks and real-time scheduling for about $50 a month — [autotradelab comparison](https://hn.jakechampion.name/reader/https://autotradelab.com/blog/nautilus-vs-vectorbt-vs-freqtrade-20-python-quant-trading-frameworks-compared)
- VectorBT PRO is at v2026.10.5; sold as monthly, annual or lifetime membership through a private GitHub repository; organisation access is for "internal, non-commercial work" and commercial use needs the author's consent; the free vectorbt is positioned as the trial; for live trading PRO is only the research and signal layer and the user supplies the broker interface, state and live risk controls — [VectorBT PRO FAQ](https://vectorbt.pro/faq)

#### Freqtrade
- Crypto only; about 46K stars; 380+ contributors; FreqAI machine learning; built-in look-ahead analysis — [autotradelab comparison](https://hn.jakechampion.name/reader/https://autotradelab.com/blog/nautilus-vs-vectorbt-vs-freqtrade-20-python-quant-trading-frameworks-compared)
- Releases 2026.8, 2026.9 and 2026.10 are listed as the latest, so the monthly cadence continues; release notes mention only crypto exchanges (Hyperliquid, Bitget, Bybit, Gate) — [newreleases.io, freqtrade](https://newreleases.io/project/github/freqtrade/freqtrade/release/2025.11)

#### pysystemtrade
- Rob Carver's "open-source backtesting and trading engine for systematic futures trading"; connects to Interactive Brokers; GNU v3; 4,779 commits; Andy Geach became primary maintainer in 2024; moved to the `pst-group` organisation in January 2026 so that there are always at least two owners; aimed at people comfortable with Python who want "a head start on building their own system"; no mention of equities or Alpaca — [pysystemtrade README, via mirror](https://backiee.wasmer.app/https_github_com/pst-group/pysystemtrade)

#### Lumibot
- Version 4.6.46 was released 3 November 2026; the 4.x line began 20 August 2025; several releases a month, with nine on 25 June 2026 alone — [PyPI lumibot](https://pypi.org/project/lumibot/4.5.32/)
- "Same code for backtest and live trading... Just swap the broker class." Brokers: Alpaca, Interactive Brokers, Tradier, Schwab, Tradovate, ProjectX futures, Bitunix, some CCXT paths. Backtest data: Yahoo, Alpaca, ThetaData, Polygon, Databento, pandas/CSV — [PyPI lumibot](https://pypi.org/project/lumibot/4.5.32/)
- Dividend-adjusted returns exist only for Yahoo and pandas sources; Alpaca, Polygon and Tradier backtests lack them — [PyPI lumibot](https://pypi.org/project/lumibot/4.5.32/)
- Licence: MIT on PyPI and on the repository page — [PyPI lumibot](https://pypi.org/project/lumibot/4.5.32/), [Lumibot repo mirror](https://gh.r-mj.com/Lumiwealth/lumibot); contradicted by [autotradelab comparison](https://hn.jakechampion.name/reader/https://autotradelab.com/blog/nautilus-vs-vectorbt-vs-freqtrade-20-python-quant-trading-frameworks-compared), which says GPL-3.0 and that it "pushes paid courses"
- The repository promotes the vendor's BotSpot no-code platform, a paid AI bootcamp and an affiliate data link — [Lumibot repo mirror](https://gh.r-mj.com/Lumiwealth/lumibot)

#### Blankly
- Last pushed 31 March 2024; 2,092 stars; LGPL-3.0; 29 open issues; Alpaca listed as "working" for live, websockets, paper and backtesting — [ecosyste.ms, blankly](https://awesome.ecosyste.ms/projects/github.com%2Fblankly-finance%2Fblankly)
- "Not maintained; development stalled", community pull requests unmerged — [autotradelab comparison](https://hn.jakechampion.name/reader/https://autotradelab.com/blog/nautilus-vs-vectorbt-vs-freqtrade-20-python-quant-trading-frameworks-compared)

### Inferences
- Treat Zipline-reloaded, backtrader and Blankly as unsuitable foundations for a 2026 rebuild: one is backtest-only and two are unmaintained.
- Freqtrade and pysystemtrade are out on scope (crypto; futures via IB) but are useful design references.
- Lumibot's release pattern (dozens of patch releases a month, nine in one day) suggests fast-moving code that would need version pinning and regression tests before any upgrade.
- LEAN's two Alpaca incidents show that even a staffed vendor integration breaks when Alpaca changes its API, and that daily-bar strategies collide with Alpaca's auction-order windows. A custom engine must handle both itself.

### Gaps
- Could not open the NautilusTrader GitHub releases or issues pages (blocked), so I could not check for a community or in-progress Alpaca adapter. The official integration list and the August 2026 release notes show none.
- LEAN's licence (Apache 2.0, from prior knowledge) was not on the pages read. Whether LEAN can be run locally with Alpaca without the paid-tier CLI by building the engine from source was not verified.
- Zipline-reloaded 3.1.1's release year, backtrader's last commit date and pysystemtrade's latest commit date were not shown on the pages read.
- Alpaca's own SDK (`alpaca-py`) release status and example strategies were not checked. From prior knowledge it is the current official Python SDK and is a client library, not an engine.
- Lumibot's order management, reconciliation and crash-recovery behaviour were not examined; its full lifecycle-method list was not on the pages read.
- The autotradelab and DEV Community comparisons are undated blog posts; star counts and "maintained" labels are approximate.

---

## 2. The standard component model; targets versus orders

### Takeaway
The engines converge on the same pipeline: data handler → strategy/alpha → portfolio construction → risk → execution → broker adapter, connected by an event queue or message bus and driven by a clock. LEAN and pysystemtrade-style systems make the strategy emit desired holdings ("targets") and leave a separate execution layer to work out orders; NautilusTrader and QuantStart-style systems let strategies emit orders that pass through a risk gate.

### Cited Findings
- QuantStart's reference design has six parts: Event, Event Queue, DataHandler (abstract base class for historical or live data), Strategy (market data → signals), Portfolio (orders, positions, sizing, risk), ExecutionHandler (simulated or real brokerage), and a loop. Four event types: MarketEvent, SignalEvent, OrderEvent, FillEvent — [QuantStart, Event-Driven Backtesting Part I](https://www.quantstart.com/articles/Event-Driven-Backtesting-with-Python-Part-I)
- QuantStart's loop: an outer "heartbeat" loop advances the data; an inner loop drains the queue and routes each event by type — [QuantStart Part I](https://www.quantstart.com/articles/Event-Driven-Backtesting-with-Python-Part-I)
- QuantStart's reasons for event-driven design: components "can be used for both historical backtesting and live trading with minimal switch-out of components"; no look-ahead because data arrival is an event; realistic execution modelling. Costs: "significantly more complex to implement and test" and slower — [QuantStart Part I](https://www.quantstart.com/articles/Event-Driven-Backtesting-with-Python-Part-I)
- LEAN's framework has five modules with one-way data flow: Universe Selection → Alpha → Portfolio Construction → Risk Management → Execution — [LEAN Algorithm Framework](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview)
- LEAN separates signal, target and order: the Alpha model emits `Insight` objects ("an object that represents a trading signal"); Portfolio Construction turns them into `PortfolioTarget` objects ("the target position size for an asset", holding "the target number of units to hold"); Risk Management may adjust targets; the Execution model "places trades to reach the target" — [LEAN Algorithm Framework](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview)
- LEAN's separation rule: "the models should not communicate"; the universe and Insights should be the same whichever portfolio, risk or execution model is plugged in — [LEAN Algorithm Framework](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview)
- LEAN states where the target model fits and where it does not: it "works best for portfolio rebalancing, ranking, and multi-factor style strategies"; technical entry signals tied to exit criteria with no defined hold period are hard to express, and the listed workarounds are "still not ideal"; LEAN advises the classic (direct-order) design when an algorithm needs special order types or passes information between components — [LEAN Algorithm Framework](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview)
- Target-model pitfall: with one signal, the default equal-weight model "will allocate 100% of buying power to a single asset", and a later signal forces a large sell, causing churn — [LEAN Algorithm Framework](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview)
- NautilusTrader's strategies submit orders; the RiskEngine sits between strategy and ExecutionEngine; the Portfolio is derived state, not a decision-maker — [NautilusTrader architecture](https://nautilustrader.io/docs/latest/concepts/architecture/)
- NautilusTrader lists its architectural techniques as domain-driven design, event-driven architecture, messaging patterns, ports and adapters, and crash-only design — [NautilusTrader architecture](https://nautilustrader.io/docs/latest/concepts/architecture/)

### Inferences
- Kaibot's two strategy families map onto the two models. Daily, multi-day strategies fit a target-position reconciliation loop: once per decision time, compute target shares per symbol, subtract broker-confirmed positions and working orders, and send only the difference. Intraday entry/exit rules with brackets fit signal-triggered orders (LEAN's "classic" design). One engine can host both if every order, whichever path created it, passes the same risk gate and order manager.
- A target loop is self-healing: a missed fill, a restart or a manual trade is corrected at the next reconciliation because the loop compares desired with actual holdings. Signal-triggered orders are not self-healing and need explicit state recovery.
- A minimal Kaibot component set: `Clock`, `MarketData` port, `Strategy` (pure function of data and state → targets or order intents), `PortfolioConstructor`, `RiskGate`, `OrderManager`, `Broker` port (Alpaca and simulated implementations), `Store` (Supabase), and a single-threaded event loop.

### Gaps
- pysystemtrade's production design (optimal positions compared with actual positions, then an order stack of instrument, contract and broker orders) is the best-known Python example of target reconciliation. I know it from prior knowledge, but the production documentation was not reachable this session, so it is uncited.
- No primary source was read on how LEAN's execution models turn a target into order quantities in detail.

---

## 3. Backtest-live parity

### Takeaway
Parity comes from structure, not from discipline: one decision core, with only the edges (clock, data source, broker) swapped. NautilusTrader does this with a shared kernel across Backtest, Sandbox and Live; LEAN and Lumibot do it by swapping the brokerage behind the same algorithm class. All of them warn that live adds behaviour a simulator does not reproduce.

### Cited Findings
- NautilusTrader's three contexts differ only in data source and execution venue; user actors, strategies and execution algorithms follow the same lifecycle in each. A Sandbox context (real-time data, simulated execution) sits between backtest and live — [NautilusTrader overview](https://nautilustrader.io/docs/latest/concepts/overview/), [NautilusTrader architecture](https://nautilustrader.io/docs/latest/concepts/architecture/)
- The kernel "selects environment-specific clocks", so time is injected, not read from the system — [NautilusTrader architecture](https://nautilustrader.io/docs/latest/concepts/architecture/)
- NautilusTrader's own caveats: live latency can produce behaviour that differs from backtests; live adds venue, transport, timing, persistence and reconciliation behaviour — [NautilusTrader architecture](https://nautilustrader.io/docs/latest/concepts/architecture/), [NautilusTrader overview](https://nautilustrader.io/docs/latest/concepts/overview/)
- Reconciliation is a live-only concern: backtesting does not need it because the engine controls both execution and state — [NautilusTrader reconciliation](https://nautilustrader.io/docs/latest/concepts/execution/reconciliation)
- LEAN handles the simulated side with "reality modeling" (fee models, fill models) and blocks access to data beyond the time frontier; in live mode order events arrive asynchronously, which backtests do not exercise — [LEAN algorithm engine](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/algorithm-engine)
- Lumibot runs the same strategy class through `MyStrategy.backtest(...)` or a `Trader` with a broker attached — [PyPI lumibot](https://pypi.org/project/lumibot/4.5.32/)
- A concrete parity failure: a LEAN daily strategy that backtested with market-on-open fills was rejected live because Alpaca only accepts `opg` orders between 19:00 and 09:28 ET — [QuantConnect forum 18268](https://www.quantconnect.com/forum/discussion/18268/day-resolution-trade-and-have-issue-with-alpaca/)
- Vectorised backtests "need rewriting for live streaming data", assume fills at the next bar's open or close, and are prone to off-by-one look-ahead errors; event-based code "can often be reused in production with minimal changes" — [IBKR Campus, vector vs event backtesting (18 Jun 2025)](https://www.interactivebrokers.com/campus/quant-news/a-practical-breakdown-of-vector-based-vs-event-based-backtesting/)
- Kaibot today has a separate backtest simulator that reimplements the trading rules — task brief; the project index also records a ten-year walk-forward run from that code path — Kaibot project doc `claude/kaibot-v2-index.md`

### Inferences
- The direct fix for Kaibot's duplicated rules is to delete the separate rule implementation and give the live engine a `SimBroker` and `SimClock` behind the same interfaces as `AlpacaBroker` and the wall clock. The backtester then becomes "the live engine with different adapters".
- The `SimBroker` should enforce Alpaca's real rules so that parity failures show up in simulation: `opg` and `cls` submission windows, bracket time-in-force limits, fractional orders being day-only, GTC expiry, stops electing as market orders (details in section 4).
- Practical parity tests (my design suggestions, not sourced): (1) golden-file replay — run a recorded day through the engine and diff the emitted order intents against a stored file; (2) shadow comparison — replay yesterday's live bars through the simulator and diff its decisions against the live decision log; any difference is a bug in data, clock or rules; (3) a determinism test — two runs of the same replay must produce byte-identical decision logs, which requires no wall-clock reads, no unordered iteration and seeded randomness.
- Main pitfalls to design against: reading `now()` inside strategy code; live bars that are incomplete or revised when polled; fills assumed at bar extremes; asynchronous fill events arriving between decisions; restart mid-day (state rebuilt from the store rather than replayed).

### Gaps
- No primary source was found describing golden-file or shadow-diff testing for trading engines specifically; those items are inference.
- NautilusTrader's design-principles page and its backtest fill-model documentation were not read.

---

## 4. Order management and reliability

### Takeaway
The broker is the source of truth and the engine must reconcile against it at start-up and continuously. NautilusTrader's reconciliation rules and Alpaca's documented order behaviour give a concrete checklist: persist every order intent before sending, use a deterministic `client_order_id`, never blind-retry a timed-out submission, and never treat a missing report as "flat".

### Cited Findings

#### Reconciliation (NautilusTrader as reference implementation)
- "At startup, reconciliation aligns cached order and position state with venue reports before trader components start"; failures in connection, reconciliation or start-up abort start-up — [NautilusTrader live trading](https://nautilustrader.io/docs/latest/concepts/live/)
- Start-up steps: build a mass status from order-status, fill and position reports; de-duplicate; move cached orders to the venue's state and create "external" orders for unknown IDs; infer missing fills; apply events in time order; compare net positions per instrument; block start-up if positions are unresolved — [NautilusTrader reconciliation](https://nautilustrader.io/docs/latest/concepts/execution/reconciliation)
- Position rule: an explicit position report is authoritative; "a missing report, a null quantity, or a venue that doesn't publish positions is not evidence of flat". If a position query fails, that cycle is skipped — [NautilusTrader reconciliation](https://nautilustrader.io/docs/latest/concepts/execution/reconciliation)
- Runtime checks: in-flight orders delayed past a threshold are queried and, after retries, a still-unconfirmed `SUBMITTED` order is rejected with reason `INFLIGHT_TIMEOUT`; open-order checks run at intervals; an `ACCEPTED` order not found at the venue resolves to `REJECTED`, a `PARTIALLY_FILLED` one to `CANCELED`; a pending cancel or update that is not found stays unresolved; a targeted single-order query always precedes a terminal "not found" decision — [NautilusTrader reconciliation](https://nautilustrader.io/docs/latest/concepts/execution/reconciliation)
- Mismatch handling: missed fills are generated and applied; "partially filled then canceled" keeps the fill history; on differing fill data the cached data is kept and the discrepancy logged; a position mismatch generates a synthetic order tagged `RECONCILIATION` — [NautilusTrader reconciliation](https://nautilustrader.io/docs/latest/concepts/execution/reconciliation)
- External orders (placed by another process, or venue-initiated) get strategy ID `EXTERNAL`; the `client_order_id` is used when present, otherwise one is derived from the venue order ID — [NautilusTrader reconciliation](https://nautilustrader.io/docs/latest/concepts/execution/reconciliation)
- Stale-snapshot rule: a REST snapshot must not undo a fill that happened after the state it describes, because REST responses and streams can overlap — [NautilusTrader reconciliation](https://nautilustrader.io/docs/latest/concepts/execution/reconciliation)
- Persistence advice: "Persist all execution events to the cache database", because venues may filter or drop older trades — [NautilusTrader reconciliation](https://nautilustrader.io/docs/latest/concepts/execution/reconciliation)
- Shutdown: any submission without a venue confirmation makes shutdown return an error listing the unresolved client order IDs; "Submission acknowledgement ends submission recovery. It does not prove that an accepted order's cancel or update completed, or that positions are flat" — [NautilusTrader live trading](https://nautilustrader.io/docs/latest/concepts/live/)

#### Alpaca order behaviour
- `client_order_id` is optional, auto-generated if omitted, at most 128 characters; orders can be fetched by it (`get_order_by_client_id()` in Python); Alpaca suggests distinct IDs per strategy to run parallel algorithms on one account — [Alpaca create-order reference](https://docs.alpaca.markets/us/reference/postorder.md), [Alpaca, Working with /orders](https://docs.alpaca.markets/us/docs/working-with-orders.md)
- On a request timeout: "The order may have been sent to the market for execution"; Alpaca says not to resend or mark it cancelled until confirmed — [Alpaca, Working with /orders](https://docs.alpaca.markets/us/docs/working-with-orders.md)
- Order statuses: `new`, `partially_filled`, `filled`, `done_for_day`, `canceled`, `expired`, `replaced`, `pending_cancel`, `pending_replace`, `accepted` (received, not yet routed; common outside session hours), `pending_new`, `accepted_for_bidding`, `stopped`, `rejected`, `suspended`, `calculated`. Terminal: `filled`, `canceled`, `expired`, `rejected`. A `pending_replace` order rejects cancel requests — [Alpaca, Placing Orders](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
- Order updates stream over the `trade_updates` websocket, each with an `event` field — [Alpaca, Working with /orders](https://docs.alpaca.markets/us/docs/working-with-orders.md)
- Time in force for equities: `day`, `gtc`, `opg`, `cls`, `ioc`, `fok`. `opg` (market- or limit-on-open): rejected if submitted after 09:28 and before 19:00 ET; after 19:00 it queues for the next open. `cls` (market- or limit-on-close): rejected after 15:50 and before 19:00 ET — [Alpaca create-order reference](https://docs.alpaca.markets/us/reference/postorder.md)
- GTC ageing: "Alpaca implements an aged order policy that will automatically cancel GTC orders 90 days after creation"; the order's `expires_at` shows the date; cancellation runs at 16:15 ET that day — [Alpaca, Placing Orders](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
- Bracket orders: `time_in_force` must be `day` or `gtc`; extended hours unsupported; exit legs activate only when the entry is completely filled; cancelling any leg cancels the rest; a partially filled take-profit shrinks the stop-loss to the remainder; in very volatile markets "both orders may fill before the cancellation occurs" — [Alpaca, Placing Orders](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
- OCO adds two exit orders to an existing position and supports replacement; OTO attaches one exit leg and does not support replacement — [Alpaca, Placing Orders](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
- Stops: "Once the order is elected, the stop order becomes a market order", so the fill price is not guaranteed; a stop-limit that gaps through stays as a resting limit order; trailing stops do not trigger outside regular hours and cannot be a bracket or OCO leg — [Alpaca, Placing Orders](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
- Fractional and notional orders: only `day` time in force (GTC, IOC, FOK, OPG, CLS unsupported); notional orders cannot be replaced — [Alpaca, Placing Orders](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
- Extended hours: only limit orders with `day` or `gtc`; sessions are overnight 20:00–04:00, pre-market 04:00–09:30, after-hours 16:00–20:00 ET — [Alpaca, Placing Orders](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
- Corporate actions: non-marketable GTC limit orders have prices adjusted and opting out is unsupported; bracket legs are "always sent with a DNR/DNC (Do Not Reduce/Do Not Cancel) instruction", so their prices are not adjusted; for splits Alpaca "may cancel or adjust trailing stops at its discretion"; an order can show `replaced` after a corporate action — [Alpaca, Placing Orders](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
- Errors: 403 "Buying power or shares is not sufficient"; 422 "Input parameters are not recognized" — [Alpaca create-order reference](https://docs.alpaca.markets/us/reference/postorder.md)
- LEAN processes dividends, splits and delistings as engine events inside every timeslice, before strategy code runs — [LEAN algorithm engine](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/algorithm-engine)

### Inferences
- Idempotent submission pattern for Kaibot: write an `order_intent` row (deterministic ID such as `{strategy}-{symbol}-{decision_date}-{purpose}-{attempt}`, at most 128 characters) to Postgres first, then submit with that value as `client_order_id`. After a timeout or crash, look the order up by client ID before deciding anything. This turns Alpaca's "do not resend" warning into code.
- Order state machine for Kaibot: `INTENT_PERSISTED → SUBMITTED → ACCEPTED → PARTIALLY_FILLED → FILLED | CANCELED | EXPIRED | REJECTED`, with `PENDING_CANCEL` and `PENDING_REPLACE` as non-terminal states, plus an explicit `UNKNOWN` (in-flight timeout) state that blocks new orders in that symbol until a targeted query resolves it.
- What to persist: order intents, every broker order event (append-only), fills, the decision record that produced each intent (inputs hash, config version, code version), daily position and equity snapshots, risk-limit state (daily loss so far, halt flags) and kill-switch state. Positions should be derived from broker reports and checked against the fill ledger, not stored as the only truth.
- Swing-trade consequences of Alpaca's rules: (1) hold whole shares if a resting GTC stop is wanted, because fractional orders are day-only; (2) add a daily job that re-arms any GTC stop within a few days of `expires_at`; (3) schedule the daily decision either after 19:00 ET (queue `opg` orders for the next open) or before 15:50 ET (send `cls` orders); (4) a stop is a market order once triggered, so overnight gap risk must be sized for, not assumed away; (5) once the entry bracket's legs exist, later changes to exits are easier with OCO orders, which support replacement.
- Corporate actions: because bracket legs carry DNR/DNC and are not price-adjusted, a forward split could leave a protective sell stop above the new price. I infer the engine should check a corporate-actions calendar daily, cancel and re-place protective orders around split dates, and compare broker share counts with its ledger the next morning. This reading of DNR/DNC for splits needs confirming with Alpaca.
- Partial fills: size exits from broker-confirmed filled quantity, not from the intended quantity; with Alpaca brackets the exits only arm on a complete entry fill, so a partially filled entry cancelled at the close leaves shares with no protective order unless the engine adds an OCO.

### Gaps
- Alpaca's response to a duplicate `client_order_id` was not documented on the pages read (prior knowledge: a 422 uniqueness error, which is what makes the ID usable as an idempotency key). Verify with a paper-account test.
- Alpaca's handling of positions through splits and dividends (share counts, cost basis, timing) and its corporate-actions API were not read.
- NautilusTrader's numeric defaults for reconciliation intervals and thresholds were not on the page read.
- No source was read on market-order price collars at Alpaca; the order page does not describe one.

---

## 5. Operations for a small always-on deployment

### Takeaway
The Knight Capital order and the FIA guide reduce to a short list that a solo operator can implement: one deployable version everywhere, no dead code paths behind reused flags, restricted limits after each release, an independent kill switch, heartbeats with an external dead-man alarm, and written incident steps. Free tooling covers the monitoring.

### Cited Findings
- Knight Capital, 1 August 2012: in 45 minutes the router sent more than 4 million orders while trying to fill 212 customer orders, traded more than 397 million shares and lost more than $460 million — [SEC press release 2013-222](https://www.sec.gov/news/press-release/2013-222)
- Cause chain from the SEC order: "Power Peg" code unused since 2003 was left on servers; in 2005 the cumulative-quantity check was moved and Power Peg was never retested; in 2012 the old flag was repurposed for new Retail Liquidity Program code; a technician failed to copy the new code to one of eight servers and there was no second-person review or written procedure requiring one; the eighth server ran Power Peg and kept sending child orders regardless of fills — [J. R. Varma's summary of the SEC order](https://www.jrvarma.in/blog/Y2013/Knight-Capital.html)
- An internal system sent 97 automated emails identifying the router error before the open; nobody acted on them — [SEC press release 2013-222](https://www.sec.gov/news/press-release/2013-222)
- Control failures listed by the SEC include: no control comparing orders leaving the router with orders entered; financial risk controls unable to stop orders exceeding capital thresholds; the account receiving the executions not linked to automated exposure controls; inadequate code deployment and testing controls; no adequate written incident-response procedures. Penalty: $12 million — [SEC press release 2013-222](https://www.sec.gov/news/press-release/2013-222)
- FIA kill switch: disables trading for a participant and cancels working orders; a last resort — [FIA Guide to the Development and Operation of Automated Trading Systems (March 2015)](https://www.fia.org/sites/default/files/2019-09/FIA-Guide-to-the-Development-and-Operation-of-Automated-Trading-Systems.pdf), section 1.11
- FIA monitoring: systems "should use a combination of monitoring tools and heartbeat detection"; alerts should fire and operators should be able to discontinue activity — [FIA Guide](https://www.fia.org/sites/default/files/2019-09/FIA-Guide-to-the-Development-and-Operation-of-Automated-Trading-Systems.pdf), section 7.1
- FIA incident response: keep standard operating procedures; report whether "there might be orders left in the market"; review procedures after each incident — [FIA Guide](https://www.fia.org/sites/default/files/2019-09/FIA-Guide-to-the-Development-and-Operation-of-Automated-Trading-Systems.pdf), section 7.2
- FIA deployment: source control able to reproduce any production version (5.1.3); phased deployment (5.3.7); "substantive changes to a trading system should be activated initially with appropriately restricted risk limits" (5.3.9); if a change cannot be validated, revert or disable it (5.3.10); risk limits and algorithm parameters may be configuration that changes without a release (5.3.11) — [FIA Guide](https://www.fia.org/sites/default/files/2019-09/FIA-Guide-to-the-Development-and-Operation-of-Automated-Trading-Systems.pdf)
- FIA environments: development and test separate from production, and "production should never refer to them" (5.1.2) — [FIA Guide](https://www.fia.org/sites/default/files/2019-09/FIA-Guide-to-the-Development-and-Operation-of-Automated-Trading-Systems.pdf)
- FIA security: access restricted and logged; passwords changed after any suspected breach; multi-factor authentication preferred (6.2–6.4) — [FIA Guide](https://www.fia.org/sites/default/files/2019-09/FIA-Guide-to-the-Development-and-Operation-of-Automated-Trading-Systems.pdf)
- FIA post-trade: reconcile an independent drop-copy feed against the system's own trade notifications in near real time; monitor daily position and loss limits with alerts before the limit (2.1, 2.2); keep an audit trail (2.4) — [FIA Guide](https://www.fia.org/sites/default/files/2019-09/FIA-Guide-to-the-Development-and-Operation-of-Automated-Trading-Systems.pdf)
- Market hours: Alpaca's calendar endpoint lists market days from 1970 to 2029 with open and close times, and early closes show in those times; `GET /v2/clock` returns `timestamp`, `is_open`, `next_open`, `next_close` (the reference labels it "LegacyClock" without naming a replacement) — [Alpaca market calendar](https://docs.alpaca.markets/reference/querymarketcalendar-1), [Alpaca clock](https://docs.alpaca.markets/us/reference/legacyclock.md)
- Dead-man alarm: Healthchecks.io's free plan allows 20 checks and keeps 100 log entries per check; email, Telegram, Slack and Pushover are listed without a paid-plan note, SMS and phone calls are paid; it is open source and self-hostable; a paid plan is $20 a month for 100 checks — [Healthchecks.io comparison page](https://healthchecks.io/docs/healthchecks_dead_mans_snitch_comparison)
- Single-instance guarantee: `pg_try_advisory_lock(key)` returns true or false at once; Postgres releases a session-level lock when the connection closes, so no time-to-live or heartbeat is needed; under PgBouncer transaction pooling the unlock can go to a different backend and silently do nothing, so use `pg_try_advisory_xact_lock` inside one transaction there; `pg_locks` shows held advisory locks — [DEV Community, Postgres advisory locks for distributed cron](https://dev.to/mukesh_13/postgres-advisory-locks-for-distributed-cron-killing-duplicate-job-runs-without-a-redis-dependency-1bfl)
- Crash recovery by restart: NautilusTrader aborts on unrecoverable faults and relies on an external supervisor plus normal start-up reconciliation — [NautilusTrader architecture](https://nautilustrader.io/docs/latest/concepts/architecture/)

### Inferences
- Knight lessons translated for Kaibot: (1) one container image, identified by git SHA, is the only thing that trades; the engine logs and stores that SHA with every decision and refuses to start if the SHA recorded in the database for "current release" differs; (2) delete retired strategy code instead of hiding it behind flags, and never reuse a flag name; (3) an order-count and notional "sanity governor" outside strategy code that halts trading if orders per minute or open notional exceed hard caps; (4) alerts must page a human channel that is actually watched; (5) after each deploy, run with reduced limits for a set period.
- Kill switches for one machine: a `trading_enabled` flag in Supabase read every loop (dashboard button); a "flatten and halt" command that cancels all open orders and optionally closes positions; and an independent path that does not need the engine at all (a small script or Alpaca's own dashboard) in case the engine is the problem.
- Dead-man switch: the engine pings a Healthchecks.io URL every loop during market hours; if pings stop, the owner is alerted. Because Alpaca protective stops rest at the broker, an engine outage leaves open positions protected, which is an argument for always having broker-side stops on overnight positions.
- Single instance with Supabase: hold a session-level advisory lock on a direct (non-pooled) connection for the life of the process, or keep a lease row (`holder_id`, `expires_at`) renewed every loop. If Kaibot connects through Supabase's transaction-mode pooler, the session-lock approach will misbehave as the article describes; check which connection mode is used.
- Time: fetch `/v2/clock` at start-up, compare its `timestamp` with the local clock and refuse to trade if the drift exceeds a few seconds; schedule from Alpaca's calendar (handles holidays and early closes) and keep all timestamps in UTC with exchange time derived.
- Deployment target: NautilusTrader-style "crash and restart under a supervisor" maps to Docker `restart: unless-stopped` or systemd plus the start-up reconciliation in section 4. A free-tier cloud VM avoids laptop sleep and home-network outages; whichever host is used, the lease prevents the Mac and the VM from both trading.
- Disaster recovery checklist: database backups or exports of the order and fill ledger; a runbook for "engine down with positions open" (check Alpaca dashboard, confirm stops resting, decide flatten or hold); API keys stored as environment secrets, paper and live keys in separate files, with a key-rotation step after any suspected leak.

### Gaps
- The FIA guide text read did not cover clock synchronisation, start-of-day and end-of-day checklists, or capacity; no other primary source on NTP practice was read.
- The text of SEC Rule 15c3-5 itself was not read; the control list here is the SEC's description of Knight's failures.
- Free alerting options other than Healthchecks.io (Telegram bots, ntfy, Pushover pricing) and Supabase free-plan behaviour (connection modes, pausing of inactive projects, backups) were not verified this session.
- Free-tier cloud VM options and their reliability were not researched.
- Alpaca's own cancel-all-orders and close-all-positions endpoints were not read this session (prior knowledge says both exist).

---

## 6. Testing practice for trading systems

### Takeaway
Industry guidance asks for layered testing (unit, functional, non-functional, acceptance), a production-like environment, a rule that a failed necessary test blocks release, and pre-trade limits enforced outside strategy code. For a solo engine the equivalents are a fake broker, recorded-day replays, a paper soak period with reduced limits, and rehearsed outage drills.

### Cited Findings
- Pre-trade controls in the FIA guide: maximum order size, with orders blocked where no limit is set for an instrument (1.1); maximum intraday position including working orders (1.2); market-data reasonability checks for staleness, spread and deviation, blocking orders during investigation (1.3); price tolerance against a reference price (1.4); repeated automated execution limit that disables a strategy until a person re-enables it (1.5); message throttles that never reject a cancel (1.9) — [FIA Guide](https://www.fia.org/sites/default/files/2019-09/FIA-Guide-to-the-Development-and-Operation-of-Automated-Trading-Systems.pdf)
- FIA testing: testing proportional to the change, in a production-like environment; failing a necessary test should be "a sufficient condition for preventing the release"; unit, functional (integration and regression) and non-functional (load, stress) testing; acceptance testing; conformance testing before deploying a new exchange-facing interface and after material changes (5.2) — [FIA Guide](https://www.fia.org/sites/default/files/2019-09/FIA-Guide-to-the-Development-and-Operation-of-Automated-Trading-Systems.pdf)
- FIA risk management: pre-trade controls automated where possible with manual override; liquidation trades should generally be allowed even when a limit is breached (7.3) — [FIA Guide](https://www.fia.org/sites/default/files/2019-09/FIA-Guide-to-the-Development-and-Operation-of-Automated-Trading-Systems.pdf)
- Regression lesson from Knight: code left in the system must be covered by regression tests even if unused; Varma calls the missing 2005 retest the biggest failure — [J. R. Varma on the SEC order](https://www.jrvarma.in/blog/Y2013/Knight-Capital.html)
- Market-access rule failures cited by the SEC: no check immediately before orders reached the market, risk controls not tied to aggregate capital thresholds, no adequate deployment testing — [SEC press release 2013-222](https://www.sec.gov/news/press-release/2013-222)
- NautilusTrader's RiskEngine checks fields, balances, quantities, notionals, reduce-only behaviour and trading state, and applies submission and modification rate limits, before any order reaches a venue — [NautilusTrader architecture](https://nautilustrader.io/docs/latest/concepts/architecture/)
- A Sandbox mode (live data, simulated execution) is a named intermediate stage between backtest and live in NautilusTrader — [NautilusTrader overview](https://nautilustrader.io/docs/latest/concepts/overview/)
- Lock and concurrency code should be tested with several real connections, not mocks — [DEV Community, Postgres advisory locks](https://dev.to/mukesh_13/postgres-advisory-locks-for-distributed-cron-killing-duplicate-job-runs-without-a-redis-dependency-1bfl)
- Alpaca paper fills are simulated, so paper P&L overstates live results — Kaibot project doc `claude/kaibot-engine-and-market-research.md`

### Inferences
- Suggested test pyramid for Kaibot (design proposal):
  1. Unit and property tests on the risk gate. Invariants: no order above max notional; position after fill never above the per-symbol cap; daily-loss halt blocks all opening orders but allows closing orders (FIA 7.3); price collar rejects limit prices more than X% from the last trade; stale-data guard blocks orders when the latest bar is older than N minutes; unknown symbol or missing limit means reject (FIA 1.1).
  2. Fake-broker simulation tests: scripted scenarios for partial fill then cancel, reject, timeout then late fill, duplicate fill event, websocket disconnect, order `replaced` after a corporate action, `opg` submitted in the rejection window, GTC expiry.
  3. Replay tests on recorded days with golden decision files (section 3).
  4. Start-up reconciliation tests: start the engine against a fake broker holding positions and open orders it does not know, and assert it adopts or flags them and does not double-submit.
  5. Paper soak: run each release on the Alpaca paper account for a fixed number of sessions with a daily diff of engine ledger against broker positions, orders and cash before promotion.
  6. Outage drills on paper: kill the process mid-order, cut the network, pause the database, restart during market hours; pass criterion is zero duplicate orders and a correct position ledger afterwards.
- A "repeated execution" breaker (FIA 1.5) is the retail version of Knight's missing control: for example, halt a strategy after N orders in the same symbol within M minutes.
- Because paper fills are simulated, the soak test validates plumbing and state handling, not execution quality.

### Gaps
- No primary source on property-based testing (for example Hypothesis stateful tests) for trading systems was read; the invariants above are my proposals.
- No source describing how retail engineers specifically adapt Rule 15c3-5 was read; the mapping is inference from the FIA guide and the Knight order.
- No published chaos-testing practice for small trading bots was found.

---

## 7. Research platform design

### Takeaway
Practitioners use two tiers: a fast vectorised layer to screen ideas across many parameters, then the event-driven engine to confirm survivors with realistic orders. Columnar storage (Parquet queried with DuckDB or Polars) makes multi-year minute data practical on one machine, and a trial registry is needed for honest statistics.

### Cited Findings
- Hybrid workflow: many quant teams use "vectorized backtests to screen and narrow a universe of candidates, then migrating promising designs into an event-driven framework for final validation before live deployment" — [IBKR Campus (18 Jun 2025)](https://www.interactivebrokers.com/campus/quant-news/a-practical-breakdown-of-vector-based-vs-event-based-backtesting/)
- Speed gap: a 500-ticker, 10-year daily vectorised backtest "completes in under one second on a modern workstation"; event-based minute data for 500 symbols over 2,500 days "can take 15–30 minutes, even with optimized Cython or PyPy code" — [IBKR Campus](https://www.interactivebrokers.com/campus/quant-news/a-practical-breakdown-of-vector-based-vs-event-based-backtesting/)
- Vectorised weaknesses: fills assumed at bar open or close, no partial fills or spreads, off-by-one look-ahead errors, path-dependent logic such as trailing stops is hard — [IBKR Campus](https://www.interactivebrokers.com/campus/quant-news/a-practical-breakdown-of-vector-based-vs-event-based-backtesting/)
- One benchmark of the same 5-year strategy: vectorbt about 0.7 s, Zipline-reloaded 2.9 s, backtrader 14.2 s; the author recommends a two-framework stack (vectorised prototyping, event-driven execution) — [DEV Community comparison](https://dev.to/pickuma/python-backtesting-frameworks-compared-backtrader-vectorbt-and-zipline-reloaded-41l8)
- VectorBT PRO positions itself as the research and signal layer with chunking, caching, parallel execution and built-in cross-validation, and leaves broker connectivity, state and live risk to the user — [VectorBT PRO FAQ](https://vectorbt.pro/faq)
- Parquet with DuckDB: query Parquet directly to get projection and filter pushdown; aim for row groups of 100K–1M rows; keep files between 100 MB and 10 GB; use Hive-style partition folders keyed on the filter columns so only matching files are read; prefer Snappy, LZ4 or zstd to gzip; on TPC-H scale factor 30, Parquet queries ran a median 2.8 times slower than DuckDB's native format, so load into DuckDB for repeated or join-heavy work — [DuckDB performance guide, file formats](https://duckdb.org/docs/2.0/guides/performance/file_formats.html)
- LEAN's streaming design is presented as protection against look-ahead: batch loading gives an algorithm "access to future data points" — [LEAN algorithm engine](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/algorithm-engine)
- Freqtrade ships built-in look-ahead analysis as a framework feature — [autotradelab comparison](https://hn.jakechampion.name/reader/https://autotradelab.com/blog/nautilus-vs-vectorbt-vs-freqtrade-20-python-quant-trading-frameworks-compared)
- Lumibot backtests on Alpaca data lack dividend adjustment, a reminder that the data source decides whether multi-week return tests are right — [PyPI lumibot](https://pypi.org/project/lumibot/4.5.32/)
- Kaibot's existing research already requires a trial registry that includes killed variants as a prerequisite for a deflated Sharpe ratio, and notes survivorship bias of about 0.1–0.9% a year from testing only current index members — Kaibot project doc `claude/kaibot-engine-and-market-research.md`

### Inferences
- Data layout proposal: minute bars in Parquet partitioned by year (and month if needed), sorted by symbol and timestamp, zstd-compressed; daily bars and corporate actions in separate tables; a DuckDB file for derived features. Store raw (unadjusted) prices plus split and dividend tables and compute adjusted series "as of" a date, so that a ten-year test and the live engine see the same numbers.
- Point-in-time rule: every feature row carries the timestamp at which it became knowable (bar close time, filing acceptance time), and joins use "latest known at or before decision time". Index membership needs dated membership tables to avoid survivorship bias.
- Ten-year minute tests get fast by (1) precomputing features once per data version and caching them keyed by code hash and data version, (2) running the vectorised screen first so the event engine only confirms a handful of candidates, (3) processing symbols or years in parallel, and (4) letting the event engine skip minutes with no bars for symbols it is not watching. For daily strategies the event-driven confirmation only needs daily bars plus an intraday fill model, which is small.
- Parity link: the vectorised layer is for ranking ideas; go/no-go numbers should come from the event engine (the live engine with the simulated broker), so that the reported backtest is the code that will trade.
- Reproducibility record for each run: git SHA, config hash, data snapshot ID, random seed, universe definition and cost model, written to a `runs` table (Supabase already exists) together with every trial's result, including failures. A dedicated tracking tool is optional at this scale.

### Gaps
- No sources were read on experiment-tracking tools (MLflow and similar), feature-store products or data-versioning tools (DVC, lakeFS); the proposals above rest on general practice.
- Polars was not researched; DuckDB guidance is the only columnar-engine source read.
- No measured timing for a ten-year, minute-bar event-driven run on Kaibot-sized universes was found; the IBKR Campus figures are the closest reference.
- The open-source vectorbt's maintenance state and exact licence were not confirmed from a primary source.

---

## 8. Recommendation: adopt an engine, restructure the custom one, or hybrid

### Takeaway
The evidence favours option (c): keep a slim custom live engine restructured around ports and adapters, a simulated broker and clock, a target-reconciliation loop and NautilusTrader-style reconciliation, and pair it with a vectorised research layer. Migrating to NautilusTrader or LEAN now carries more risk than benefit because of missing or fragile Alpaca support, a framework in mid-rewrite, and conflicts with the free, Python-only, single-small-machine constraints.

### Cited Findings
- NautilusTrader has no Alpaca integration among its 17 listed adapters; its only equities brokerage is Interactive Brokers — [PyPI nautilus-trader](https://pypi.org/project/nautilus-trader)
- NautilusTrader is between cores: 1.231.0 is the intended last Cython v1 release, v2 is at release candidate 9, tutorials still use v1, some v2 features are deferred, and breaking changes between releases are expected until 2.x is stable — [newreleases.io, v1.231.0](https://newreleases.io/project/github/nautechsystems/nautilus_trader/release/v1.231.0), [PyPI nautilus-trader](https://pypi.org/project/nautilus-trader)
- NautilusTrader ships Linux ARM64 and macOS ARM64 wheels, needs Python 3.12+, and is LGPL-3.0-or-later — [PyPI nautilus-trader](https://pypi.org/project/nautilus-trader)
- LEAN's CLI needs a paid organisation tier; local US equity work needs QuantConnect datasets; Alpaca data arrives raw and adjusted prices need a separate download — [LEAN CLI, Alpaca](https://www.quantconnect.com/docs/v2/lean-cli/live-trading/brokerages/alpaca)
- LEAN is a C# engine with Python attached through Python.NET, and warns that cross-language calls are slow — [LEAN algorithm engine](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/algorithm-engine)
- LEAN's Alpaca path broke in July 2026 with no user-side workaround, and its daily-resolution default collides with Alpaca's auction windows unless a custom execution model is written — [QuantConnect forum 21072](https://www.quantconnect.com/forum/discussion/21072/alpaca-brokerage-api-no-longer-compatible-with-quantconnect/p1), [QuantConnect forum 18268](https://www.quantconnect.com/forum/discussion/18268/day-resolution-trade-and-have-issue-with-alpaca/)
- LEAN's own guidance says its target framework suits rebalancing and ranking strategies and fits entry/exit technical rules poorly — [LEAN Algorithm Framework](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview)
- Lumibot is the only actively released pure-Python engine found with Alpaca support and a permissive licence, but it releases many times a month and its Alpaca backtests lack dividend adjustment — [PyPI lumibot](https://pypi.org/project/lumibot/4.5.32/)
- QuantStart's argument for a custom event-driven design is that live and historical components swap behind abstract base classes, at the cost of more complexity to build and test — [QuantStart Part I](https://www.quantstart.com/articles/Event-Driven-Backtesting-with-Python-Part-I)
- pysystemtrade's maintainers describe it as a head start for people building "their own system", i.e. a pattern source — [pysystemtrade README, via mirror](https://backiee.wasmer.app/https_github_com/pst-group/pysystemtrade)
- VectorBT PRO states that the user must supply broker connectivity, state and live risk controls, which is the hybrid split in practice — [VectorBT PRO FAQ](https://vectorbt.pro/faq)
- Kaibot already has risk limits, a Supabase schema and a dashboard — task brief and Kaibot project doc `claude/kaibot-v2-index.md`

### Inferences

#### Option comparison

| Criterion | (a) Migrate to NautilusTrader | (a') Migrate to LEAN | (b) Restructure custom engine | (c) Hybrid: research library + slim custom engine |
|---|---|---|---|---|
| Alpaca support | None official; Kaibot would write and maintain data, execution and instrument adapters | Exists; proven fragile; paid tier for CLI | Already working via Alpaca SDK | Same as (b) |
| Effort | High: adapter plus learning a large framework during its v1→v2 change | High: C#/Python.NET stack, data licensing, rewrite of strategies and dashboard feed | Medium: refactor about 1,000 lines into ports, add sim broker, order manager, reconciliation | Medium plus a small research package |
| Main risk | Building on a moving API; adapter bugs are Kaibot's alone | Vendor dependency and cost; debugging inside C# | Owner must build reconciliation and OMS correctly | Same as (b); two code paths must not both claim to be "the backtest" |
| Daily + intraday | Strong (bar and tick events, MOO/MOC time-in-force) | Daily path needs custom execution model for Alpaca | Direct control of `opg`/`cls`/GTC rules | Same as (b) |
| Reuse of existing risk limits and Supabase dashboard | Low: Nautilus has its own cache, risk engine and persistence | Low | High | High |
| Free / small machine | Fits | CLI tier and datasets cost money | Fits | Fits |
| Fit for AI-agent development | Large codebase, sparse v2 examples | Mixed-language | Small, fully owned, testable codebase | Same as (b) |

#### Reasoning
- The decisive facts are the absent Alpaca adapter and the v2 transition for NautilusTrader, and cost plus integration fragility for LEAN. Adopting either means Kaibot would own a broker adapter inside someone else's fast-changing framework, which is harder than owning a small engine.
- Kaibot's scale (minute polling, bracket orders, daily decisions, one account) does not need NautilusTrader's performance. It needs NautilusTrader's discipline: risk gate before the venue, single-threaded deterministic core, shared kernel across backtest and live, start-up and continuous reconciliation, crash-only restart. These can be copied as patterns.
- Lumibot is the realistic "adopt" candidate and is worth a time-boxed spike as a comparison point, but its iteration-polling lifecycle is close to what Kaibot already has, so adopting it would not by itself deliver the missing pieces (reconciliation, idempotent order intents, audit trail) and would add upgrade churn.
- Option (c) is option (b) plus a deliberately separate research layer. The rule that keeps it honest: the vectorised layer only ranks ideas; any number used for a go-live decision comes from the event engine running with the simulated broker.

#### Suggested shape of the rebuilt engine
1. `core/` (pure, no I/O): strategies returning targets or order intents; portfolio constructor; risk gate; order state machine. Deterministic and clock-injected.
2. `ports/`: `Broker`, `MarketData`, `Clock`, `Store`, `Notifier` interfaces.
3. `adapters/`: `AlpacaBroker`, `SimBroker` (enforces Alpaca's order rules), `AlpacaData`, `ParquetData`, `WallClock`, `SimClock`, `SupabaseStore`, `MemoryStore`.
4. `runtime/`: single-threaded loop; start-up sequence (acquire lease → load state → reconcile with broker → refuse to start on unresolved differences → run); periodic reconciliation; heartbeat; kill-switch check.
5. Two strategy styles on one order manager: daily target-reconciliation jobs (after 19:00 ET for `opg`, or before 15:50 ET for `cls`) and intraday signal-triggered brackets.
6. `research/`: Parquet/DuckDB data store, vectorised screening, trial registry; confirmation runs call the engine with `SimBroker`.

#### Migration order (lowest risk first)
1. Introduce the `Broker` and `Clock` ports and move existing rules behind them without changing behaviour; add golden-file replay tests to prove it.
2. Add `SimBroker` and retire the separate backtest rule implementation; rerun the existing walk-forward to confirm results match within tolerance.
3. Add order intents with deterministic client order IDs, the order state machine and start-up reconciliation.
4. Add the target-reconciliation loop for the first daily strategy, with GTC stop re-arming and corporate-action checks.
5. Add lease, heartbeat, dead-man alarm and the deployment gate (git SHA check, reduced limits after release).
6. Revisit NautilusTrader after 2.0 final ships and if an Alpaca adapter appears; the ports-and-adapters layout keeps that door open, since strategies in `core/` would need only a thin wrapper.

### Gaps
- No effort measurements exist for any option; the high/medium labels are judgement, not data.
- I could not confirm whether a community Alpaca adapter for NautilusTrader exists (GitHub pages were blocked), which would lower option (a)'s effort.
- Lumibot's internals were not reviewed in enough depth to rule it in or out on reliability grounds; a short hands-on spike would settle it.
- Kaibot's current code was not inspected in this task, so the fit of the proposed module split to the existing 1,000-line module is assumed from the brief.
