# Design R1: Visual / Brand Designer position

## 1. Brand concept
**"The chart room."** Kaibot should feel like a navigator's desk: paper, ink, ruled lines, instruments that tell the truth quietly. The product's real promise is keeping three things apart: what the machine calculated, what the AI thinks and what the broker reports. **Name:** "kaibot" in lowercase IBM Plex Sans Medium, ink on paper. **Mark:** a keel line. A solid stroke (calculated) over a dotted stroke (AI), joined by a square node (the gate). The provenance rule becomes the logo; it works at 16px in monochrome.

## 2. Theme, palette, semantics
**Light "paper" is the default. Dark "night watch" is a full alternative**, switched on manually or after 20:00 CET.

| Token | Light | Dark | Role |
|---|---|---|---|
| paper | #F6F4EF | #11161C | background |
| surface | #FFFFFF | #171E26 | cards, tables |
| rule | #DAD6CC | #2A333D | 1px borders |
| ink | #1B2430 | #E6E3DC | text, calculated numbers |
| slate | #5B6573 | #98A1AC | labels |
| sea | #1F5F6B | #5FA8B4 | brand, primary action |
| gain | #1F5FAE | #6FA3E8 | positive P&L |
| loss | #C2560C | #F0904A | negative P&L |
| pass | #2F6F4F | #6DBF94 | gate pass |
| warn | #A86A12 | #E0A845 | near limit |
| fail | #B42318 | #F07466 | breach, kill switch |
| ai | #5B4B8A | #A898DA | AI frame |
| ai-tint | #F2EFF8 | #1E1A2B | AI fill |
| stale | #A3A9B1 | #5C6570 | expired values |

**Rules**
- **P&L:** gains are blue and losses orange, always signed with a true minus (−€310.50) and ▲/▼.
- **Crimson is reserved for breaches, hard fails and the kill switch.**
- **Gate states:** each has a shape and a text label as well as a colour, so it works in greyscale. Pass is a check, warn a triangle, fail an octagon-cross.
- **Thesis status:** Intact is a solid ring, Under pressure a half ring, Invalidated a crossed ring, Needs review a dashed slate ring.
- **PAPER:** a permanent hatched chip, "PAPER · PA3…". When the kill switch is engaged, the whole header turns crimson.
- **Stale:** the value turns the stale colour and gets a clock glyph, with its as-of time on hover.

**Provenance.** Calculated values are ink, set in mono, on plain surfaces, with a source chip ("calc · 14:02" or "alpaca · 14:02"). AI analysis sits in an ai-tint panel with a dashed violet left rule and an "AI analysis" eyebrow, and its prose is in a **serif**. Any number the AI quotes is set in mono and links to its calculation. That gives three cues (font, border, colour), and any one of them is enough.

## 3. Type
- **UI:** IBM Plex Sans 400/500/600.
- **Numbers:** IBM Plex Mono with `tnum` and `zero`, right-aligned and decimal-aligned. Money has 2 decimal places and % has 1.
- **AI prose:** Source Serif 4, 15/24.
- **Scale (px):** 11 caps eyebrow · 13 table · 14 body · 16 card title · 20 section · 28 screen · 40 portfolio value (Mono Light).
- **€ first,** with USD, the FX rate and its timestamp below in smaller slate text.

## 4. Component character
- **Spacing:** 4px base, compact density.
- **Radii:** 4px for controls, 6px for cards. No pills except the PAPER chip.
- **Borders over shadows:** 1px rules everywhere. Shadows only on popovers and modals.
- **Tables:** 32px rows, hairline rules, no zebra striping, sticky header.
- **Buttons:** Approve is solid sea, *not green*. Warning approve is outlined warn, with the acknowledgement list inline. A disabled Approve is hatched, with a lock icon and the reason in text. Reject is a ghost button.
- **Header strip:** 40px, on ink. Each limit is a mini meter with a tick at the limit.
- **Icons:** Lucide at 1.5px stroke, used sparingly.

## 5. Hills I'll fight on
1. **Light paper is the default.** Dark-plus-neon is the visual language of casino apps, and the 07:40 check happens in daylight.
2. **No red/green for P&L, and crimson means breach only.** If "down 2%" and "rule violated" share a colour, the user learns to ignore the alarm.
3. **AI speaks in serif inside a dashed violet frame.** Provenance has to survive greyscale, screenshots and colour blindness, and a change of typeface does all three.
