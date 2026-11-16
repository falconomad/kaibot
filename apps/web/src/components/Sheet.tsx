// v4 bottom sheet (docs/design/mobile_first_v4.md, "Bottom sheet"): detail behind a tap. A modal dialog that slides up
// from the bottom on phones and sits centred near the bottom on wider screens.
// - Opens with transform only (320 ms), closes in 240 ms; with reduced motion it appears and goes in place.
// - Closes on the X, Escape, a tap on the scrim, and a downward swipe on the grabber or the title bar.
// - Focus moves into the sheet and returns to the element that opened it; Tab stays inside while it is open.
// - Height never animates; long content scrolls inside the sheet.
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { reducedMotion } from "../lib/a11y";

const CLOSE_MS = 240;
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode }) {
  const [shown, setShown] = useState(open);   // mounted
  const [leaving, setLeaving] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(null);
  const drag = useRef<{ y: number; dy: number } | null>(null);
  const id = useId();

  useEffect(() => {
    if (open) { opener.current = document.activeElement; setShown(true); setLeaving(false); return; }
    if (!shown) return;
    setLeaving(true);
    const t = setTimeout(() => { setShown(false); setLeaving(false); (opener.current as HTMLElement | null)?.focus?.(); }, reducedMotion() ? 0 : CLOSE_MS);
    return () => clearTimeout(t);
  }, [open]); 

  useEffect(() => {
    if (!shown || leaving) return;
    const el = panel.current;
    (el?.querySelector<HTMLElement>("[data-autofocus]") ?? el)?.focus();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = overflow; };
  }, [shown, leaving]);

  // Keys are handled on the sheet itself (React events bubble through the portal to whatever opened it, and an
  // opener that stops propagation would otherwise hide them from a document listener).
  const onKey = (e: React.KeyboardEvent) => {
    const el = panel.current;
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); return; }
    if (e.key !== "Tab" || !el) return;
    const f = [...el.querySelectorAll<HTMLElement>(FOCUSABLE)];
    if (!f.length) { e.preventDefault(); return; }
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === el)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };

  if (!shown || typeof document === "undefined") return null;
  const down = (e: React.PointerEvent) => { drag.current = { y: e.clientY, dy: 0 }; (e.target as Element).setPointerCapture?.(e.pointerId); };
  const move = (e: React.PointerEvent) => {
    if (!drag.current || !panel.current) return;
    drag.current.dy = Math.max(0, e.clientY - drag.current.y);
    panel.current.style.transform = `translateY(${drag.current.dy}px)`;
  };
  const up = () => {
    const d = drag.current; drag.current = null;
    if (!panel.current || !d) return;
    panel.current.style.transform = "";
    if (d.dy > 80) onClose();
  };
  return createPortal(
    <div className={`sheet-root ${leaving ? "leaving" : ""}`} onKeyDown={onKey}>
      <div className="sheet-scrim" onClick={onClose} aria-hidden="true" />
      <div ref={panel} className="sheet" role="dialog" aria-modal="true" aria-labelledby={id} tabIndex={-1}>
        <div className="sheet-head" onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
          <span className="sheet-grab" aria-hidden="true" />
          <h2 id={id} className="sheet-title">{title}</h2>
          <button type="button" className="sheet-x" aria-label="Close" onClick={onClose}>
            <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M6 6 L18 18 M18 6 L6 18" /></svg>
          </button>
        </div>
        <div className="sheet-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
