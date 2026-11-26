// The halt reason as a tappable chip (#724, ticket 8.6 of the second audit). A halt's plain words used to sit in a
// tooltip with its code (`title=`), which a phone cannot show. The chip is a button: a tap opens a sheet with the plain
// words, the engine's own reason, when it began (ET and CET), whether it is over, and its code. Used on Engine (the
// trading-state line and "Recent halts") and by Activity's halt rows. Lazy with those pages, CSS included.
import { useState } from "react";
import { Link } from "react-router-dom";
import type { Halt } from "../lib/api";
import { haltText } from "../lib/derive";
import { haltTone } from "../lib/experiment";
import { dateEt, timeBoth, timeEt } from "../lib/fmt";
import { NAME } from "../brand/brand";
import { Sheet } from "./Sheet";
import "./haltchip.css";

const TAG = { breach: "veto", ended: "downgrade", note: "info", info: "info" } as const;

/** The sheet's body: what the halt is, in words, then the engine's own reason, when, and whether it is over. */
export function HaltSheetBody({ h, cleared, resolved, link = true }: { h: Halt; cleared?: string | null; resolved?: boolean; link?: boolean }) {
  const words = haltText(h);
  const over = cleared ? `Over: the engine cleared it at ${timeBoth(cleared)}.` : resolved ? "Over: the engine's heartbeat came back after it." : null;
  return (
    <>
      <p>{words}</p>
      {h.reason && h.reason !== words && <p className="halt-why"><span className="muted">In the engine's words: </span>{h.reason}</p>}
      <div className="kv">
        <span>Began</span><span /><span className="v num">{dateEt(h.ts)} · {timeBoth(h.ts)}</span>
        <span>Now</span><span /><span className="v">{over ? "over" : haltTone(h.kind) === "breach" ? "in force" : "noted"}</span>
        <span>Code</span><span /><span className="v num">{h.kind}</span>
      </div>
      {over && <p className="muted">{over}</p>}
      <p className="muted">{NAME} applies its limits by itself. Open trades keep their stop at the broker.</p>
      {link && <p className="muted" style={{ fontSize: 15 }}>{dateEt(h.ts)} {timeEt(h.ts)} ET · <Link to="/health">Engine health</Link></p>}
    </>
  );
}

/** A halt as a chip that opens its sheet. `label`: the chip's words (the halt's plain words by default). */
export function HaltChip({ h, label, cleared, resolved, link }: { h: Halt; label?: string; cleared?: string | null; resolved?: boolean; link?: boolean }) {
  const [open, setOpen] = useState(false);
  const tone = cleared || resolved ? "allow" : TAG[haltTone(h.kind)];
  return (
    <>
      <button type="button" className="halt-chip" aria-haspopup="dialog" onClick={() => setOpen(true)} data-kind={h.kind}>
        <span className={`tag ${tone}`}>{label ?? haltText(h)}</span>
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title="Halt">
        <HaltSheetBody h={h} cleared={cleared} resolved={resolved} link={link} />
      </Sheet>
    </>
  );
}
