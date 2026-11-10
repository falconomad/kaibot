# Round 1: Backend / Quant Engineer position

Gates run in **USD on Alpaca `equity`** (E); € is display only. Q = qty, P = limit price, S = stop.

## 1. Gate rules
"Within 20% of a limit" means **post-trade value ≥ 0.8 × limit**.

| Rule | Formula | Inputs | Type | Warn |
|---|---|---|---|---|
| Kill switch | off | DB flag | Hard | — |
| Reconciled | last recon match, ≤ 5 min old | recon log | Hard | — |
| Market hours | `clock.is_open`, ≤ close − 10 min | Alpaca clock | Hard | — |
| Stale price | quote age ≤ 60 s | IEX quote | Hard | > 30 s |
| Expiry | computed_at ≤ 5 min | proposal | Hard | — |
| Duplicate | no open order or live approval, same symbol and side | orders | Hard | — |
| Short | sell Q ≤ `qty_available` | positions | Hard | — |
| Position | (MV_sym + Q·P)/E ≤ 10%, both sleeves | positions | Hard | 8% |
| Sector | (MV_sec + Q·P)/E ≤ 30% | sector map | Hard | 24% |
| Open risk | Σ Qᵢ·max(0, Lastᵢ−Sᵢ) + pending + new ≤ 2% | satellite | Hard | 1.6% |
| Daily loss | E/last_equity − 1 > −1.5% | account | Hard | day loss + new max loss ≥ 1.2% |
| Drawdown | 1 − E/HWM < 8% | equity history | Soft | 6.4% |
| Correlation | max ρ (top 5) ≤ 0.7 | daily bars | Soft | > 0.7 |
| Thesis | Intact | thesis | Soft | not Intact |

## 2. Sizing
`Q = floor(min(0.005E/(P−S), (0.10E − MV_sym)/P, sector_headroom/P))`, with S < P.

Worked example (E = $100k, P = 61.20, S = 58.40):
- Risk-based size: 500/2.80 = 178.6 → 178 shares. But 178 × 61.20 = 10.9%, which **fails the position cap**.
- The cap binds: **163 shares, max loss $456.40 (0.46%)**.
- **Staged**: size at max entry 61.51 (+0.5%), giving 500/3.11 → **160 shares, max loss $497.60**. Sizing 163 at 61.20 and filling at 61.51 loses $507, which breaches 0.5%.

**FX**: r = ECB reference rate, timestamped. € P&L = V$·r₁ − C$·r₀, split as price (V$ − C$)·r₁ and FX C$·(r₁ − r₀), where r₀ is the fill-date rate. The parts sum exactly.

## 3. Staged orders, idempotency, reconciliation
- **Executor**: in-process APScheduler (persisted), not GitHub Actions cron.
- **Fire time**: `clock.next_open + 5 min`, never a hard-coded "15:35 CET". DST dates differ; half-days exist.
- **At fire**: quote ≤ 60 s old, full gate re-run, Q = min(approved Q, fresh size). Cancel if the quote is above max entry or at/below S.
- **Order**: bracket (limit entry, stop, target), TIF day. Any remainder is cancelled at open + 30.
- **Idempotency**: `client_order_id = kb-{proposal_id}-{attempt}`. Persist `submitting` before the HTTP call. On timeout or a duplicate error, GET by client_order_id and adopt; never resend.
- **Reconciliation**: `trade_updates` websocket plus a 30 s poll. Diff positions and orders against the ledger. Any diff turns the chip red and blocks orders.

## 4. Ill-defined requirements, with my definitions
- **Daily loss**: equity vs Alpaca `last_equity` (prior close), realised plus unrealised.
- **Drawdown HWM**: peak daily closing equity since account reset.
- **Sector**: yfinance `sector` (Yahoo's 11, not GICS), cached with its date and user-overridable. Unknown fails closed, with a one-tap assign.
- **Correlation**: Pearson, daily log returns, 252 aligned days (minimum 120, otherwise "n/a").
- **"±0.5%"**: for a buy only the upper bound matters, so max entry = 1.005P.
- **5 years of financials**: yfinance gives about 4 annual periods. Accept 4 or fund another source.
- **DCF**: 2-stage FCF (TTM OCF − capex). Growth = 3-yr revenue CAGR clipped to [−5%, 25%], terminal 2.5%, r = 9%. Range: r ± 1 pp × g ± 3 pp. Subtract net debt, divide by diluted shares. **Negative FCF shows "not meaningful"**, which covers Nebius.
- **Data**: live quotes are IEX only (thin venue); a missing quote means fail. Daily bars come from SIP history (free beyond 15 min).
- **Not computable in v1**: NBBO, true slippage, gap-through-stop risk, GICS.

## 5. Hills I'll fight on
1. **Size staged orders at max entry.** Otherwise the approved max loss is false.
2. **Gate in USD.** € limits would drift with EUR/USD.
3. **No cron for execution.** A late run breaks the 30-minute lapse window and idempotency.
