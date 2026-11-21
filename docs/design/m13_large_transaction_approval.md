# "A large order needs your approval": design note

Issue #487 (M13 · ticket 4.8). Date: 21 Nov 2026. **Status: design only, for Product and the stakeholder to decide.
Nothing here is built.** The stakeholder's decision of 21 Nov 2026 is the brief: when one transaction would buy or
sell a large part of the portfolio, the user is notified and must approve it before it is sent; no approval means
no send; protective exits are sent and notified.
Brand: `docs/design/brand/brand_system_v1.md` (lowercase **kaibo**). Push: `docs/design/m13_push_notifications.md`.

Mockups (phone, 390 px, brand v1; invented numbers): `docs/design/mockups/v4/approval/`, drawn by the repo's mockup
kit (`python docs/design/mockups/v4/approval/gen.py --shot`).

| Screen | File |
|---|---|
| The notification on a locked phone | `lock.png` |
| Home while an order waits | `home-waiting.png` |
| The approval sheet | `approve.png` |
| The confirmation | `confirm.png` |
| Every outcome, and its line in Activity | `states.png` |

## The flow in one paragraph

kaibo's rules decide after the close what to trade at the next close. If one order is larger than the owner's line
(a share of the pot), that order is not sent with the others: it waits. The owner gets a notification, opens kaibo,
sees the order and why it is large, and taps Approve or Decline. Approved, it is sent once in the normal window at
the close. Declined, or not answered in time, it is not sent. A sale that a risk rule requires never waits.

## 1. The notification

- Title: **"kaibo needs your approval"**. Body: "A large order is waiting. Open kaibo to approve or decline by 21:45."
- No amount and no stock name: it shows on a locked screen.
- No buttons on the notification. Approving needs the signed-in app, and an iPhone cannot be relied on to show
  buttons on a web notification.
- Sent once when the order starts to wait, and once more 30 minutes before the time runs out if there is still no
  answer. One more when it expired: "A large order was not sent: no answer in time." Nothing after that.
- Tapping it opens the approval sheet for that order. If the order no longer waits, it opens state 7 of
  `states.png` ("This order no longer waits"), never live buttons.
- An owner without the installed app sees the same row on Home at the next visit. Email as a second channel is an
  open question.

## 2. Home while an order waits

One status row at the top of Home, above every other row: **"A large order waits for your approval"**, second line
"Answer by 21:45 your time, or it is not sent". It uses the brand's blue with a clock, not red or amber: nothing is
wrong, kaibo is asking. Two or more: "2 large orders wait for your approval · The first is due by 21:45 your time",
opening a list.

## 3. The approval sheet

Top to bottom (`approve.png`):

1. "Waits for your approval" and the order as a sentence: **"Buy 22 NVDA, about $3,254"**, then the company, the
   rule and "on paper".
2. A bar: **"32% of your pot in one order"**, with a mark at "your line: 25%". This is the reason it waits, shown
   before anything else.
3. One sentence: "This one order is larger than the share of the pot you let kaibo move without asking, so it
   waits for you."
4. Three facts: why kaibo wants it (the rule's own plain reason), what pays for it, and what the pot looks like
   after it ("NVDA would be 52% of the pot").
5. The time: **"Answer by 21:45 your time (in 3 h 12 min)"**, with New York time, and what silence means: "With no
   answer the order is not sent, and nothing else changes."
6. Two buttons of equal size: **Decline** (plain) and **Approve** (blue). Decline is not red: saying no is a normal
   answer.
7. Small print: what Decline does, and "Sales that protect the pot never wait for you."

"About" is deliberate: the share count is set from the price at the close, so the sheet promises a size, not a
price. No price target, no forecast, nothing that argues for either answer.

## 4. The confirmation

Approve opens one more step (`confirm.png`), in plain words: "Approve this order? kaibo will send it at today's
close (22:00 your time): buy about 22 shares of NVDA for about $3,254, 32% of your pot." Buttons: **Back** and
**Yes, approve**. Decline needs no second step; it offers an optional one-line reason.

An approval can be taken back until the time runs out ("You can still take the approval back until 21:45").

## 5. Expiry

- The time to answer ends shortly before the order would be sent: this note proposes **15:45 in New York**, five
  minutes before the send window opens at 15:50. Always shown in the viewer's own time first.
- No answer: **the order is not sent** (fail closed). The rules decide again after the close, and may ask again.
- Nothing else is held back by one waiting order: the smaller orders of the same decision go out as usual.
  (Whether a sale that funds a waiting buy should also wait is an open question for Backend-quant.)

## 6. What never waits

A sale that a risk rule requires (a protective stop, a limit breach, "sell everything and stop") is sent at once,
however large. The owner is told afterwards: "kaibo sold a stock to protect the pot. It was a large sale. No
approval was needed." This matches the rule elsewhere in kaibo: nothing may stop the engine from taking risk off.

## 7. Rules that keep it safe

| Rule | Why |
|---|---|
| An approval is for the numbers on the screen | If the plan changes by more than a small band while it waits, the approval is void and kaibo asks again (state 4). The same idea as accepting a ledger difference (#439) |
| One answer per order, recorded once | A second tap, a second device or an old notification changes nothing (state 7) |
| An answer counts only when the server confirms it | Offline or with the engine not running, the buttons are off and say why (state 8) |
| Approved is sent once | In the normal send window, by the engine, never from the phone |
| Every decision leaves a record | Who, when, from which device, what the screen showed, the answer and the optional reason; also expiries. Shown in Activity in plain words |
| No safety rule depends on a notification arriving | A lost notification ends in "not sent", never in "sent without asking" |

## 8. Words

| Moment | Words |
|---|---|
| Home row | A large order waits for your approval · Answer by 21:45 your time, or it is not sent |
| Approved | Approved. kaibo sends it at today's close, 22:00 your time. |
| Declined | Declined. Not sent today. kaibo's rules decide again after the close. |
| Expired | Not sent: no answer by 21:45. Nothing was bought. |
| Changed | The order changed: look again |
| Activity | You approved: buy 22 NVDA · You declined: buy 22 NVDA · Expired: buy 22 NVDA |

No "alert", "warning", "urgent" or "opportunity", no exclamation marks, no countdown that flashes. The remaining
time is words ("in 3 h 12 min"), updated each minute.

## 9. Settings

"Run & settings" gets one line: "Ask me before one order moves more than 25% of the pot", with the level's value.
Whether the owner may change it, and within which bounds per level, is Backend-quant's proposal to make (#487).
It can never be switched off from the phone in this design.

## Accessibility

Both buttons 52 px high, side by side, Decline first in reading and focus order. The sheet is the existing bottom
sheet (focus moves in, Escape closes, focus returns). The share of the pot is said in words, not only by the bar.
Text contrast follows `docs/design/brand/contrast_v1.md`; the blue row uses the same pair as the experiment label's
text on its tint. Not yet run through axe: these are static mockups.

## Open questions

1. **The line.** 25% for every level, or tighter for Conservative? (Backend-quant, #487.)
2. **Sells.** A large sale by a rule's target list (not a protective one): does it wait too? This note assumes yes,
   because the stakeholder's words are "buy or sell".
3. **A funded pair.** If a waiting buy is paid for by a sale in the same decision, does the sale wait with it?
4. **Re-asking.** After a decline, may the rule ask for the same order at the next decision, or is there a pause?
5. **Sign-in age.** Should approving ask for a fresh sign-in when the session is old? Matters more with real money.
6. **Second channel.** Email for owners without the installed app.
7. **Desktop.** The same sheet, centred; not drawn here.
