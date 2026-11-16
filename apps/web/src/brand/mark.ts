// Reads Design's logo files as delivered (src/brand/logo/*.svg, copied unchanged from docs/design/logo/concepts/
// 1-rising-k, #255) so the Logo component draws their own paths: nothing is redrawn or re-spaced here.
// The wordmark is hand-drawn from two kinds of shape, and both are read below: a capsule (a straight stroke with round
// ends: "M a L b A r r 0 0 0 c L d A r r 0 0 0 a z") and a circle or ring ("M x y a r r 0 1 0 2r 0 a ... z").
export interface Part { d: string; fill: string; evenodd: boolean }
export interface Mark {
  /** The paths, in the file's order and with the file's colours. */
  parts: Part[];
  /** The file's own <g transform="translate(x y)"> around the paths (the symbol has one), or [0, 0]. */
  shift: [number, number];
  /** The box that hugs the drawn shapes (x, y, width, height), so the mark is sized by its letters, not by clear space. */
  box: [number, number, number, number];
}
type Pt = [number, number];

const nums = (s: string) => (s.match(/-?\d*\.?\d+/g) ?? []).map(Number);
const mid = (a: Pt, b: Pt): Pt => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];

/** A capsule's two end centres and its radius; null when the path is not a capsule. */
export function capsule(d: string): { ends: [Pt, Pt]; r: number } | null {
  if (!/^M[^LAaz]+L[^LAaz]+A[^LAaz]+L[^LAaz]+A[^LAaz]+z$/.test(d)) return null;
  const n = nums(d);
  return { ends: [mid([n[0], n[1]], [n[11], n[12]]), mid([n[2], n[3]], [n[9], n[10]])], r: n[4] };
}

/** A circle's centre and radius (for a ring, its outer circle); null when the path is not one. */
export function circle(d: string): { c: Pt; r: number } | null {
  if (!/^M[^a]+a/.test(d)) return null;
  const n = nums(d);
  return { c: [n[0] + n[2], n[1]], r: n[2] };
}

function bounds(d: string): [number, number, number, number] {
  const cap = capsule(d), cir = circle(d);
  const pts: Pt[] = cap ? cap.ends : cir ? [cir.c] : [];
  const r = cap?.r ?? cir?.r ?? 0;
  if (!pts.length) throw new Error(`logo: a shape that is neither a capsule nor a circle: ${d.slice(0, 40)}`);
  return [Math.min(...pts.map((p) => p[0])) - r, Math.min(...pts.map((p) => p[1])) - r, Math.max(...pts.map((p) => p[0])) + r, Math.max(...pts.map((p) => p[1])) + r];
}

const round = (v: number) => Math.round(v * 100) / 100;

export function parseMark(svg: string): Mark {
  const parts = [...svg.matchAll(/<path\b([^>]*)\/>/g)].map((m) => ({
    fill: /\bfill="([^"]+)"/.exec(m[1])?.[1] ?? "", d: /\bd="([^"]+)"/.exec(m[1])?.[1] ?? "", evenodd: /fill-rule="evenodd"/.test(m[1]),
  }));
  const t = /<g transform="translate\((-?[\d.]+) (-?[\d.]+)\)">/.exec(svg);
  const shift: [number, number] = t ? [Number(t[1]), Number(t[2])] : [0, 0];
  const b = parts.map((p) => bounds(p.d));
  const x0 = Math.min(...b.map((v) => v[0])), y0 = Math.min(...b.map((v) => v[1]));
  const x1 = Math.max(...b.map((v) => v[2])), y1 = Math.max(...b.map((v) => v[3]));
  return { parts, shift, box: [round(x0 + shift[0]), round(y0 + shift[1]), round(x1 - x0), round(y1 - y0)] };
}

/** Where a stroke starts when it is drawn out from one of its ends: moved back along its own line by its whole length,
 *  so none of it shows inside its outline yet. `from` picks the end it grows out of. */
export function drawnFrom(d: string, from: "bottom" | "left"): Pt {
  const cap = capsule(d);
  if (!cap) return [0, 0];
  const [p, q] = cap.ends;
  const start = (from === "bottom" ? p[1] > q[1] : p[0] < q[0]) ? p : q, end = start === p ? q : p;
  const len = Math.hypot(end[0] - start[0], end[1] - start[1]), reach = len + 2 * cap.r;
  return [round(((start[0] - end[0]) / len) * reach), round(((start[1] - end[1]) / len) * reach)];
}
