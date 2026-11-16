// v4 accordion (docs/design/mobile_first_v4.md, "Motion: Accordions"): a native <details> so it works without
// JavaScript and with every screen reader; the chevron turns 90° and the content fades in (200 ms). Detail that is
// not the page's one thing lives here, one tap away.
import type { ReactNode } from "react";

export function Accordion({ title, hint, children, open = false, id }: { title: ReactNode; hint?: ReactNode; children: ReactNode; open?: boolean; id?: string }) {
  return (
    <details className="acc" open={open || undefined} id={id}>
      <summary><span className="acc-title">{title}</span>{hint && <span className="acc-hint">{hint}</span>}</summary>
      <div className="acc-body">{children}</div>
    </details>
  );
}
