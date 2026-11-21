# Broker adapters: the contract, what is Alpaca-only today, and what Interactive Brokers would need

M12 ticket 4.9 (#488). Stakeholder direction of 21 Nov 2026: the bot should later connect to Alpaca's or
Interactive Brokers' API for live trading. This note is design only. Nothing here builds an adapter or changes
trading; paper only remains the rule (`CLAUDE.md`), and no live step happens before the legal and approval steps
in `docs/research/engine_audit/report.md`.

## The contract

`tests/contract/test_broker_adapter_contract.py` is the test suite every adapter behind
`kaibot.broker.protocol.Broker` must pass. It runs each test against each entry of `DRIVERS`
(`tests/contract/adapter_drivers.py`). Today:

| Adapter | Runs against |
|---|---|
| `SimBroker` | itself, on flat bars |
| `AlpacaPaper` | `FakeAlpacaPaper` (`tests/contract/fake_alpaca_http.py`): Alpaca's paper REST API as documented, in process, no network, fake keys, any host but the paper endpoint refused |

What the contract pins, because the engine relies on it:

- **One order per client order id, for ever.** The same id twice returns the same order, also after it ended.
- **Lookups.** An order is found by its venue id and by its client order id; unknown is `BrokerError` 404 by id and
  `None` by client id.
- **Refusals** are `BrokerError` with status 403 or 422 and leave nothing at the venue. No answer at all is status 0.
- **Executions** appear on the order (`filled_qty`, `status`), in `positions()`, and as one `FILL` activity each,
  oldest first, with an id that never repeats or changes, the venue order id, quantity, price and a zoned time.
  `activities(after=...)` leaves out what came before; more than one page is returned whole.
- **Cancel** ends an open order and is harmless when repeated or unknown. **Replace** leaves exactly one working
  order under the client order id it was given; the venue may keep the order id or issue a new one (then the old
  order says `replaced` and `replaced_by`).
- **Book and time.** `account()` gives cash, equity and buying power as numbers; `close_position` cancels what
  holds the shares and sells; `clock()` and `calendar(day)` give the session in the documented shape.
- **A lost answer** to a submission is one order, findable by its client order id (healed inside the adapter or
  reported as status 0).
- **The order manager** run over the adapter sees the expected order lives and ends with a ledger equal to the
  venue; all adapters end with the same book.

A new adapter joins by adding a driver (how to make its venue execute, cut the network, lose an answer) and its
name to `DRIVERS`. The contract tests themselves do not change.

Limits of the contract today: it does not cover bracket and OTO legs, `opg` / `cls` orders, fractional orders,
corporate-action activities or the wash-trade refusal. Those are tested for `SimBroker` and the Alpaca adapter in
`tests/unit/` and should move into the contract before a second live adapter is built. The fake proves the
adapter's translation, not Alpaca's behaviour: audit ticket 1.11 (#436) adds the outside-in test on Alpaca paper.

## Alpaca-only assumptions in the code today

Found by reading `src/kaibot/broker/` and the engine's call sites. "Shape" means the engine reads Alpaca's JSON
field names directly.

| # | Assumption | Where | What another broker changes |
|---|---|---|---|
| 1 | Orders, positions, the account, the clock and the calendar are plain dicts in Alpaca's shape | `broker/protocol.py` docstring; every reader in `engine/` | An adapter must translate into these field names, or the protocol gets typed records first (recommended before a second adapter) |
| 2 | Order status words are Alpaca's | `engine/orders.py` `VENUE` | The adapter maps its states onto these words |
| 3 | The client order id is the idempotency key: unique per account for ever, up to 128 characters, enforced by the venue, and orders can be read back by it | `orders.client_order_id`, `COID_MAX`, `AlpacaPaper.submit`, `OrderManager.resolve` | The largest gap for a broker that does not enforce it (see IB below) |
| 4 | A replace creates a new order id and needs a new client id | `AlpacaPaper.replace`, `OrderManager.replace`, issue #197 | A venue that modifies in place is the easy case; the manager already follows either |
| 5 | Fills are read by polling account activities; an activity id is permanent and unique; fees arrive apart from fills as `FEE` activities | `OrderManager.read_fills`, `ledger.fees_from_activities` | An event-driven venue needs the adapter to keep an execution log it can serve as `activities()` |
| 6 | Activity type codes are Alpaca's enum, and an unknown code is refused before sending | `protocol.ALPACA_ACTIVITY_TYPES`, `ledger.*_ACTIVITIES` | The names sit in the protocol module itself; they should move behind the adapter |
| 7 | Dividends and splits arrive as account activities; a split's ratio is inferred from share counts | `ledger.actions_from_activities` | Needs the other broker's corporate-action source |
| 8 | Order types and times in force are Alpaca's set; `cls` is refused 15:50 to 19:00 ET and `opg` accepted 19:00 to 09:28 ET; fractional orders are DAY only | `protocol.ORDER_TYPES`, `TIMES_IN_FORCE`, `execution.py`, `SimBroker.check` | Windows and names differ per venue; `ExecutionPolicy` would take them from the adapter |
| 9 | Paper's closing auction is not a real auction, so daily rules send DAY market orders in the last ten minutes | `execution.py` `near_close`, issue #333 | A venue with a working closing auction could use `cls` again; that is a strategy change needing a pre-registration |
| 10 | Bracket and OTO orders with nested legs; a replaced leg may be listed under its parent or alone | `BrokerBase.submit_bracket`, `OrderManager.replace_leg`, issue #11 | Multi-leg orders are built differently elsewhere |
| 11 | Wash-trade refusal (403) when an opposite order is open; a sale needs shares no other sale holds | `SimBroker.wash_check`, `protect.py` release logic | Venue rule; the stop-release sequence depends on it |
| 12 | Paper-only guard: the base URL constant and a refusal of any other `ALPACA_BASE_URL` | `engine/config.py`, `broker/alpaca.py` | Each adapter needs its own guard that it is talking to a paper account |
| 13 | `account()` fields `status`, `cash`, `equity`, `buying_power`, `shorting_enabled` | `live_daily.py`, `reconcile.py` | Translate, and decide what stands in for `shorting_enabled` |
| 14 | The broker's clock and calendar are REST reads | `clock()`, `calendar()` | May need another source (exchange hours from contract data) |
| 15 | The fail-safes call Alpaca themselves: the watchdog builds `AlpacaPaper`, and the dead-man is SQL in Supabase that calls Alpaca's REST API with keys from Vault | `engine/watchdog.py`, `migrations/0006_failsafe.sql` | An off-box dead-man needs a broker reachable by plain HTTPS from Postgres |
| 16 | Rate limiting through the `alpaca_trading` budget; every call ends within 20 s | `broker/alpaca.py`, `config/sources.yaml` | Per-adapter budget and deadline |
| 17 | Market data comes from Alpaca's data API | `engine/alpaca.AlpacaData`, `engine/market.py` | Separate from the trading adapter; licensing differs per broker |

Items 1, 3, 5, 6 and 15 are the ones that shape an adapter; the rest are translation.

## What an Interactive Brokers adapter would need

Sources: IBKR's TWS API documentation, read 21 Nov 2026 where a link is given. Points marked "to verify" are from
general knowledge of the API and were not read in IBKR's documentation for this note.

**Connection.** The TWS API is a socket to a locally running IB Gateway or TWS, which acts as the server after the
user has logged in; a session without the application's window is not supported, the user must enter the
credentials by hand, both applications were designed to be restarted daily, and from version 974 an auto-restart
keeps a session from Sunday to Sunday, after which the credentials are entered again
([IBKR, the IB Gateway](https://www.interactivebrokers.com/docs/tws-api/doc/architecture/the-trader-workstation/the-ib-gateway)).
Consequences for Kaibot: a second process to supervise next to the engine, a weekly manual login that the
"always on" design (`docs/ops/always_on.md`) does not have today, and a synchronous facade over an event-driven
socket, because `Broker` is synchronous on purpose. IBKR also offers a REST "Web API" with its own session rules;
which of the two to build on is the first design decision (to verify).

**Idempotency (assumption 3).** IB's `orderId` is "the API client's order id" and `permId` is the host's
identifier; `orderRef` is a free-text reference that the documentation describes as intended for institutional
customers ([IBKR, Order reference](https://www.interactivebrokers.com/docs/tws-api/ref/order)); the execution
reference says the order id "may not be unique to an account"
([IBKR, Execution reference](https://www.interactivebrokers.com/docs/tws-api/ref/execution)). Nothing in what was
read says the venue refuses a second order with the same reference. So the property Kaibot's safety rests on, that
the venue itself refuses a duplicate, would have to be rebuilt in the adapter: a durable map from Kaibot's client
order id to IB's `orderId` / `permId`, written before the order is sent; `order_by_client_id` answered from open
and completed orders matched on `orderRef`; and a start-up scan before anything is sent. The contract tests
"the same client order id twice is one order" and "a lost answer is one order" are the acceptance test for this.

**Replace (assumption 4).** An order is modified by placing it again with the same order id; the client must be
connected with the same user and API client id as the original order, and IBKR advises changing only price, size
and time in force ([IBKR, Modify order](https://www.interactivebrokers.com/docs/tws-api/doc/orders/modify-order.md)).
This is the in-place branch of the contract's replace test. The engine must always connect with one fixed client id.

**Fills (assumptions 5 and 6).** Executions arrive as events and on request, each with an execution id; commissions
arrive in a separate report tied to that id; how far back executions can be requested is limited (to verify:
commonly the current day unless the gateway is configured otherwise). The adapter would persist every execution
it sees and serve `activities()` from that log, with the execution id as the activity id and corrections handled
explicitly (to verify how IB marks a corrected execution). The journal already books by activity id, so nothing
above the adapter changes.

**Order types and windows (assumptions 8 to 10).** Market, limit, stop, market-on-close and limit-on-close exist;
brackets are built from a parent order and child orders linked by `parentId`, sent with the `transmit` flag, and
one-cancels-all groups (`ocaGroup`) ([IBKR, Order reference](https://www.interactivebrokers.com/docs/tws-api/ref/order)).
Cut-off times for closing orders are the exchanges' and differ from Alpaca's 15:50 ET (to verify per exchange).
`ExecutionPolicy` would read its windows from the adapter instead of constants.

**Clock, calendar, corporate actions, account (assumptions 7, 13, 14).** No REST clock; trading hours come with
contract details, and dividends and splits are not delivered as account activities over the socket API (to verify;
IBKR's statement reports are the usual source). Account values arrive as a stream of key and value updates, to be
folded into `account()`'s four fields.

**Paper guard (assumption 12).** IB paper accounts are separate accounts with their own login and, by convention,
account ids that start with `DU` and their own default ports (to verify). The adapter's guard would refuse to start
unless the connected account is a paper account, checked from the account id the session reports, not from
configuration alone.

**Fail-safes (assumption 15).** The Supabase dead-man works because Alpaca is plain HTTPS with two keys. A socket
to a gateway on the engine's own machine cannot be reached from Postgres, and dies with that machine. An IB
deployment needs a different off-box protection: stops resting at the broker (already the multi-day design), plus
either IBKR's REST API from the dead-man if its session can be held without the machine, or a second small host.
This is the hardest open question and should be answered before any adapter code.

**Market data and pacing (assumptions 16, 17).** Market data needs its own subscriptions and has its own limits;
requests are paced per connection (to verify the current numbers). Both get their own budget in
`config/sources.yaml`.

### Order of work, if the decision is taken

1. Decide socket API or REST API, and the off-box fail-safe that goes with it (decision record).
2. Move Alpaca's names out of the protocol module (items 1, 2, 6): typed order, position, fill and account records
   that both existing adapters produce; the contract tests keep both honest during the move.
3. Extend the contract with what `tests/unit/` covers only per adapter (legs, auctions, corporate actions).
4. Build the IB adapter against a recorded or fake gateway until it passes the contract; then an outside-in run on
   an IB paper account, as audit ticket 1.11 does for Alpaca.
5. No live account before the report's legal and approval steps.
