// #130: a kb_book answer in the RPC's own shape (migrations/0018 section 5), for tests only. Invented numbers, set in
// the future on purpose so nobody reads them as a track record.
export const RAW_BOOK = {
  run_id: 1, session: "2027-03-11",
  mtm: { ts: "2027-03-11T21:05:00Z", at_close: true, cash: 1450, invested: 3051.7, equity: 4501.7, equity_reported: 4500.4,
    start_equity: 5000, prev_close_equity: 4513.95, open_equity: null, dividends_to_date: 3.1 },
  positions: [
    { rule: "trend_momentum", symbol: "AAPL", qty: 4, avg_cost: 231.4, last: 238.1, last_ts: "2027-03-11T21:05:00Z", mark: 238.1,
      opened_session: "2027-01-19", dividends: 1.0, stop: { price: 196.7, state: "resting" } },
    { rule: "trend_momentum", symbol: "MSFT", qty: 2, avg_cost: 448.2, last: 441.9, last_ts: "2027-03-11T21:05:00Z", mark: 441.9,
      opened_session: "2027-02-02", dividends: 0, stop: { price: 381.0, state: "resting" } },
    { rule: "trend_momentum", symbol: "ZZZQ", qty: 3, avg_cost: 100, last: null, last_ts: null, mark: 98,
      opened_session: "2027-03-01", dividends: 0, stop: null },
    { rule: "mom_12_1_band", symbol: "COST", qty: 1, avg_cost: 905.0, last: 921.5, last_ts: "2027-03-11T21:05:00Z", mark: 921.5,
      opened_session: "2027-02-01", dividends: 0, stop: { price: 800, state: "re-arming" } },
  ],
  exposure: { gross: 0.6779, overnight: 0.6779, cap: null,
    by_rule: [{ rule: "trend_momentum", value: 2130.2, share: 0.4732 }, { rule: "mom_12_1_band", value: 921.5, share: 0.2047 }], market_filter: [] },
  reconciled: { ok: true, ts: "2027-03-11T21:06:00Z", positions: { AAPL: { ledger: 4, broker: 4 } }, cash_diff: 0 },
};
export const RAW_EMPTY_BOOK = { run_id: 1, session: null, mtm: null, positions: [], exposure: null, reconciled: null };
