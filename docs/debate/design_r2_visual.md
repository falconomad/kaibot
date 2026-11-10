# Design R2: Visual / Brand Designer rebuttal

## Concessions
- **Data-viz:** "one limit, one meter", the amber band at 80% and gate rows showing actual against limit are all right.
- **UX Lead:** Approve never moves, and the AI commentary sits below the gate. I adopt both.

## Conflicts and compromises
1. **12-column holdings table (Data-viz).** That turns a calm ledger into a terminal. **Compromise:** 9 columns by default (Ticker, Sleeve, Thesis, Qty, Value €, Weight with micro-meter, Day P&L, Total P&L, FX €). Avg cost, Price $ and Sector go behind a "Detail" toggle that persists. FX stays.
2. **Red-tinted risk zone on the ladder (Data-viz).** If every trade shows red, crimson stops meaning breach. **Compromise:** the risk zone takes a 12% orange loss tint and the reward zone an 8% blue gain tint. Crimson appears only if the stop or max loss fails a gate.
3. **40% opacity for stale values (Data-viz).** That measures about 2.4:1, which is unreadable, and the user still needs to read the old value. **Compromise:** full opacity in the stale token (≥3.4:1), plus a clock glyph and a dotted underline.
4. **Seven chips (UX Lead).** Bare chips only report a breach once it has happened. **Compromise:** seven slots at 44px. AI spend, daily loss, open risk and drawdown become 56px numbered mini meters. PAPER, kill switch and reconciliation stay as chips. No sparklines.
5. **AI rail open by default (UX Lead).** An open rail at 380px would outshout the "Needs you" queue. **Compromise:** a collapsed 48px rail showing the latest call ("Hold · 3 sources"). "/" expands it, and it remembers its last state.

## Palette after a contrast check (WCAG)
| Token | Was | Now | Contrast on paper |
|---|---|---|---|
| loss (light) | #C2560C (4.1:1) | **#B4500A** | 4.7:1 |
| warn (light) | #A86A12 (4.0:1) | **#94600F** | 4.9:1 |
| stale (light) | #A3A9B1 (2.2:1) | **#7D848D** | 3.4:1, always with the clock glyph |
| stale (dark) | #5C6570 | **#7A838E** | 4.4:1 |

All other tokens are confirmed at 5.4:1 or better. AI violet on ai-tint measures 6.6:1.
