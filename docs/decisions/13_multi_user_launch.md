# Decision 13: Multi-user launch readiness (M6) — DRAFT

- **Status:** draft requested by the stakeholder on 2026-11-11. Not approved, not scheduled.
- **Precondition:** the engine passes the go/no-go bar (M4) on paper. Nothing here starts before that, except the legal question in S63, which can be asked early.
- **Not legal advice.** Offering automated trading to other people may be a regulated activity (EU/Germany and the US both have rules for investment advice, portfolio management and order routing). S63 gets proper advice before any other story is built.

## Where we are today

Kaibot is single-owner. One account exists and it is the owner; every other sign-in is refused. All tables are locked and the dashboard reads through owner-checked `kb_*` functions. Broker keys and the database service key live only on the owner's Mac. One engine trades one paper account.

## What other customers would need

| Area | Today | Needed |
|---|---|---|
| Accounts | One owner | Sign-up, one account per customer, roles (customer, admin) |
| Data separation | Everything belongs to the owner | Every run, trade, config and report carries a customer id; row-level rules per customer; tests that one customer can never read another's rows |
| Broker | One Alpaca paper account, keys in `.env` | Each customer connects their own broker account (OAuth where the broker offers it); Kaibot never holds withdrawal rights; keys encrypted at rest |
| Engine | One process, one account | One isolated engine worker per customer (or a scheduler that runs them), each with its own limits, heartbeat and kill switch |
| Hosting | The owner's Mac | Always-on cloud hosting with monitoring; the free tier will not carry several customers |
| Privacy | No customer data | Privacy policy, data-processing records, export and delete on request (GDPR; the data is in Frankfurt) |
| Legal | Personal paper trading | Regulatory position confirmed; terms of use, risk disclosures, no performance promises |
| Money | Free | Pricing, billing, refunds (only if the stakeholder wants to charge) |
| Support | The owner | Status page, incident process, a way to contact support, an admin view |

## Principles carried over

- Paper first for every customer; real money is a separate, later decision per customer and per jurisdiction.
- Every customer sees the same honest reporting: results after costs against SPY, every veto, and a pause button that always works.
- A customer's settings can only tighten their level, exactly as in M5.

## Draft stories

S56 accounts and roles · S57 per-customer data separation · S58 broker connection per customer · S59 one engine worker per customer · S60 cloud hosting and monitoring · S61 privacy: policy, export, delete · S62 terms, risk disclosures, onboarding consent · S63 regulatory advice (first) · S64 admin view and support · S65 pricing and billing (optional)
