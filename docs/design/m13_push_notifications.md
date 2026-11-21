# Push notifications for the installed app: design note

Issue #481 (M13 · ticket 4.7; the note also serves #487, ticket 4.8). Date: 21 Nov 2026.
**Status: design only. Nothing here is built; there is no push backend.** Every choice below is this note's proposal
for Product and the stakeholder, not a decision.
Brand: `docs/design/brand/brand_system_v1.md` (lowercase **kaibo**).

## What the owner should get

A notification on the phone when something happened that they would want to know without opening the app, and only
then. Proposed list, most important first:

| Kind | Example (title, then body) | Opens |
|---|---|---|
| Approval needed (#487) | "kaibo needs your approval" · "A large order is waiting. Open kaibo to approve or decline." | The approval screen |
| Halt | "kaibo stopped new buys" · "The daily loss limit was reached. Open trades keep their stops." | Engine |
| Books differ from the broker | "kaibo stopped new buys" · "Its books and the broker don't agree. Open kaibo to look." | Engine, "Books and broker" |
| Engine offline in market hours | "kaibo's engine is not running" · "No new trades until it is back. Stops stay at the broker." | Engine |
| Fill | "kaibo bought a stock" · "Open kaibo to see it." | Positions |
| Daily note | "Today's note is ready" | Home |

Rules for the words:

- **No amounts, no tickers and no pot value in a notification by default.** A notification shows on a locked screen.
  The owner can turn "show details on the lock screen" on in Settings; then a fill reads "kaibo bought 20 XOM".
- Plain words, no exclamation marks, no "alert", no colour words. A notification never asks for a trade.
- A notification only points into the app. **Nothing is decided from the notification itself**: no "Approve" button
  on it. An approval needs the signed-in app (see `docs/design/m13_large_transaction_approval.md`). This also fits
  the iPhone, where buttons on web notifications cannot be relied on.
- At most one notification per kind and event; fills of one rebalance are grouped ("kaibo placed 6 orders").

## Asking for permission

- Never on the first screen. In Settings, under "On your phone", a row "Notifications" with a switch per kind, all
  off. Turning the first one on asks the browser for permission, from that tap.
- If the browser refuses, the row says in words how to allow it in the phone's settings.
- On an iPhone the row appears only in the installed app, with one line: "Add kaibo to your home screen first."

## What the platforms allow

| Platform | Push for a web app | Notes |
|---|---|---|
| Android, Chrome | Yes, in the browser and installed | |
| iPhone and iPad | Only for a web app added to the home screen, iOS 16.4 or later; permission only from a tap | Every push must show a notification; no silent pushes |
| Desktop Chrome, Edge, Firefox, Safari | Yes | |

So the installable app of this ticket is the precondition: `display: standalone` and the service worker are in place.

## How it would be built (proposal)

1. **Subscription.** The installed app asks the browser for a push subscription (the standard Push API, with a VAPID
   public key in the build) and stores it with the owner's account: a table `kaibot.push_subscription` (endpoint,
   keys, the kinds switched on, the device's name, created and last-success times), owner-only through `kb_*`
   functions, as every other table. One row per device; the Settings row lists them and can remove one.
2. **Sending.** The engine stays as it is: outgoing connections only, and it already writes its events to Supabase
   (decision 11). A small sender reads new rows of the kinds above and sends through the Web Push protocol. Two
   places it could run, both on free tiers: a Supabase Edge Function called by a database webhook, or a Vercel
   function on a schedule. The VAPID private key lives only there. The engine never talks to a phone.
3. **The service worker** gets two more handlers: `push` (show the notification) and `notificationclick` (open or
   focus the app on the page named in the message). It still stores nothing but the app shell.
4. **Failure.** A subscription the push service reports as gone is deleted. A failed send is retried a few times,
   then dropped and counted on the Engine page. Notifications are a convenience: **no safety rule may depend on one
   arriving.** An approval that is never seen expires and the order is not sent (fail closed, #487).

## Open questions for Product

1. Which kinds are on by default once the owner allows notifications: only approvals and halts (this note's
   proposal), or fills too?
2. Where the sender runs (Supabase Edge Function or Vercel). Backend-quant and QA-Ops to say.
3. Multi-user (M13): subscriptions are per user and per device from the start in this design; the sender must send
   only to the owner of the run the event belongs to.
4. Email as the second channel for approvals, for an owner who has not installed the app.
