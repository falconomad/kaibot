# Design v2: Visual system for an autonomous engine
*Visual / Brand Designer · 10 Nov 2026 · extends 02_design.md.*

## 1. Changes to the chart-room system

**Engine state.** One indicator, the first slot in the header. Shape, colour and word each carry the state.

| State | Shape | Colour | Label |
|---|---|---|---|
| Running | ● filled dot | sea | "Running · cycle 14:02:31" |
| Paused (by user) | ❚❚ outline | slate | "Paused · exits managed" |
| Cooling (auto, 60 min) | ▲ triangle | warn | "Cooling · 42 min" |
| Halted | ■ octagon-cross | fail + crimson header | "Halted · stale feed" |
| Stopped (Flatten-and-stop) | ■ square | ink | "Stopped by you 14:10" |
| Market closed | ○ hollow ring | slate | "Closed · opens 15:30 CET" |
| Calibration | ◌ dashed ring | sea | "Calibrating · starts Wed 15:30" |

**Motion rules**
- The engine proves it is alive with the mono cycle timestamp, not a pulse.
- No flashing, colour flashes, count-up tweens, confetti or sound.
- Values change in place; a changed number gets a 1px underline fading over 600ms.
- The pot updates at most every 60 seconds.
- `prefers-reduced-motion` removes all motion.

**Agent-room transcript.** All three voices live inside the violet AI system, told apart by rule and label:
- **Proposer:** solid 2px violet left rule.
- **Critic:** dashed violet rule, indented 16px.
- **Judge:** double rule. The verdict is in sans caps: NO OBJECTION / DOWNGRADE / VETO.

Prose is in serif and sources are chips. Python's outcome closes each transcript in mono ink ("Engine: entered 40 NVDA @ 182.14"): the room advises, Python decides. Invalid room output shows in the stale colour: "Output invalid → no trade."

**Shadow (hypothetical) styling.** ORB, news and the baselines get a hatched SHADOW chip, a dotted 1px row outline, slate numbers with ▲/▼ only, and dashed chart lines. They are never summed into the pot.

**Flatten-and-stop.** A solid ink button with a ■ glyph and the label "Flatten and stop". It is not crimson (crimson means breach). One confirm: "Flatten 3 positions and stop". Pause is a ghost button beside it.

## 2. Onboarding tone
- **Layout:** one 560px column on paper, one decision per screen, plain sans. No rockets, coins or rising charts.
- **Amount:** entered in mono, with the € reference shown in slate.
- **Level:** shown as a bad day and a good day at equal size, bad day first.
- **Copy:** stated plainly: "Expect +2–10 bp on a typical day; 40–50% of days lose."
- **Contract screen:** a ruled will/won't ledger, then one confirm: "Start at next open".

## 3. Palette
- **Confirmed:** all tokens in 02_design.md.
- **Currency:** $ is now primary and € the reference.
- **Comparison chart:** the pot is ink, SPY a dashed slate line and cash a flat rule line.
- **New tokens:**

| Token | Light | Dark | Role |
|---|---|---|---|
| shadow | #8A8FA3 | #7C8196 | shadow rows and lines |
| hatch | #E8E2D0 | #262D36 | PAPER and SHADOW chips |
