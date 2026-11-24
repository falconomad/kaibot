import { useEffect, useMemo, useRef, useState } from "react";
import type { Curve } from "../lib/model";
import { day, money } from "../lib/fmt";
import { NAME } from "../brand/brand";

// Pot vs SPY scaled to the same start. One y-axis (both are dollars). Brand system v1 (#265): the pot's line is the
// brand blue and ends in the live point; the benchmark is neutral grey, told apart by its dots and its label.
const W = 760, H = 240, M = { t: 12, r: 92, b: 24, l: 64 };

// Brand system v1 (section 4): the line draws itself once per app load, left to right in --d-draw (a clip that grows
// with a transform; no repaint of the line), and the live point pops and pulses once when a new value arrives.
let drawn = false;
/** Tests only. */
export const resetChartDraw = () => { drawn = false; };

/** Optional chapter boundaries (M5 S48): a dashed rule at the first session on or after each `session`. */
export interface ChartMarker { session: string; label: string }

/** The chart's ranges: [key, sessions kept (0: all), label]. */
export const RANGES = [["1W", 5, "1W"], ["1M", 21, "1M"], ["3M", 63, "3M"], ["ALL", 0, "All"]] as const;
export type RangeKey = (typeof RANGES)[number][0];
/** Stakeholder, 19 Nov 2026: with 3 sessions every range drew the same line. Only ranges that show less than All. */
export const rangesFor = (points: number): RangeKey[] => RANGES.filter(([, keep]) => keep === 0 || points > keep + 1).map(([k]) => k);

/** The change from `from` to `to` in percent, precise enough that a tiny move is not "0.0%" (−2.74 on 95,000: −0.003%). */
export function changePct(to: number, from: number): string {
  if (!from) return "";
  const p = ((to - from) / from) * 100, a = Math.abs(p);
  if (a < 0.0005) return "0.0%";
  const dp = a < 0.01 ? 3 : a < 1 ? 2 : 1;
  return `${p > 0 ? "+" : "−"}${a.toFixed(dp)}%`;
}

/** While there are fewer than 5 sessions, why the line barely moves: "3 days so far · 16% invested, the rest in cash". */
export function chartCaption(sessions: number, investedShare: number | null): string | null {
  if (sessions >= 5 || sessions < 1) return null;
  const days = `${sessions} day${sessions === 1 ? "" : "s"} so far`;
  return investedShare !== null && Number.isFinite(investedShare) && investedShare >= 0
    ? `${days} · ${Math.round(investedShare * 100)}% invested${investedShare < 0.995 ? ", the rest in cash" : ""}` : days;
}

/** `compact` (v4 Home): no table button on phones and the SVG's own axis text is hidden there (the chart stretches);
 *  an HTML scale (top and bottom value, first and last day) stands in. The legend shows at every width.
 *  `base`: where each line's change is measured from (the run's start for All, the range's first point otherwise). */
export function PotChart({ data, start, markers = [], compact = false, base, caption = null, quiet = false }: { data: Curve[]; start: number; markers?: ChartMarker[];
  compact?: boolean; base?: { pot: number; spy: number }; caption?: string | null;
  /** Live prices move the last point every 10 s: it follows without the pop, which that often would be a loop. */ quiet?: boolean }) {
  const [hover, setHover] = useState<number | null>(null);
  const [draw] = useState(() => !drawn && data.length > 1);
  useEffect(() => { if (draw) drawn = true; }, [draw]);
  // A new last value (a poll, a live price): the point pops once. Not on the first render, not when nothing changed.
  const lastPot = data.at(-1)?.pot;
  const prevPot = useRef(lastPot);
  const [pops, setPops] = useState(0);
  useEffect(() => {
    if (!quiet && lastPot !== undefined && prevPot.current !== undefined && lastPot !== prevPot.current) setPops((n) => n + 1);
    prevPot.current = lastPot;
  }, [lastPot]);
  const [table, setTable] = useState(false);
  const ref = useRef<SVGSVGElement>(null);
  const g = useMemo(() => {
    const vals = data.flatMap((d) => [d.pot, d.spy]).concat(start);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.08 || start * 0.01; lo -= pad; hi += pad;
    const x = (i: number) => M.l + (data.length > 1 ? (i / (data.length - 1)) * (W - M.l - M.r) : 0);
    const y = (v: number) => M.t + (1 - (v - lo) / (hi - lo)) * (H - M.t - M.b);
    const step = niceStep((hi - lo) / 4);
    const ticks: number[] = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) ticks.push(v);
    const line = (k: "pot" | "spy") => data.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d[k]).toFixed(1)}`).join("");
    const pot = line("pot");
    const area = data.length ? `${pot}L${x(data.length - 1).toFixed(1)},${H - M.b}L${x(0).toFixed(1)},${H - M.b}Z` : "";
    // the fill fades out within 32 units below the line's highest point: a flat line is a line, never a block
    const top = data.length ? Math.min(...data.map((d) => y(d.pot))) : M.t;
    return { x, y, ticks, pot, spy: line("spy"), area, step, fillTop: top, lo, hi };
  }, [data, start]);

  // Fewer than two points draw no line: say what will appear instead of an empty frame.
  if (data.length < 2) return <div className="chart-empty"><b>No history yet</b><span className="muted">The line starts at the first close and runs next to the S&amp;P 500 from the same start.</span></div>;
  const last = data[data.length - 1];
  const from = base ?? { pot: start, spy: start };
  const h = hover !== null ? data[hover] : null;

  function move(e: React.PointerEvent) {
    const r = ref.current!.getBoundingClientRect();
    const sx = ((e.clientX - r.left) / r.width) * W;
    const i = Math.round(((sx - M.l) / (W - M.l - M.r)) * (data.length - 1));
    setHover(Math.max(0, Math.min(data.length - 1, i)));
  }
  // keep the two end labels from colliding
  let ly1 = g.y(last.pot), ly2 = g.y(last.spy);
  if (Math.abs(ly1 - ly2) < 14) { const mid = (ly1 + ly2) / 2; const s = ly1 < ly2 ? -1 : 1; ly1 = mid + s * 7; ly2 = mid - s * 7; }

  return (
    <div className={compact ? "potchart compact" : "potchart"}>
      <div className="legend chart-legend">
        <span title="after costs"><i className="sw pot" aria-hidden="true" />{NAME} <b className="num">{whole(last.pot)}</b> <span className="num">{changePct(last.pot, from.pot)}</span></span>
        <span><i className="sw spy" aria-hidden="true" />SPY, same start{last.spyClose ? " (last close)" : ""} <b className="num">{whole(last.spy)}</b> <span className="num">{changePct(last.spy, from.spy)}</span></span>
        <span className="spacer" />
        <button className="chart-table-btn" style={{ minHeight: 44, fontSize: 15, padding: "0 14px" }} onClick={() => setTable(!table)}>{table ? "Show chart" : "Show table"}</button>
      </div>
      {caption && <p className="chart-caption">{caption}</p>}
      {table ? (
        <div style={{ maxHeight: 260, overflow: "auto" }}>
          <table className="list" tabIndex={0} aria-label="Pot and SPY by session"><thead><tr><th>Session</th><th className="r">Pot</th><th className="r">SPY, same start</th></tr></thead>
            <tbody>{data.map((d) => <tr key={d.session}><td>{day(d.session)}</td><td className="r num">{money(d.pot)}</td><td className="r num">{money(d.spy)}</td></tr>)}</tbody>
          </table>
        </div>
      ) : (
        <div className="chart">
          <svg ref={ref} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio={compact ? "none" : undefined} role="img" aria-label={`Pot ${money(last.pot)} versus SPY ${money(last.spy)} at the end${markers.length ? `; ${markers.length} chapter change${markers.length === 1 ? "" : "s"} marked` : ""}`}
            onPointerMove={move} onPointerLeave={() => setHover(null)}>
            {g.ticks.map((t) => (
              <g key={t}>
                <line x1={M.l} x2={W - M.r} y1={g.y(t)} y2={g.y(t)} stroke={t === start ? "var(--rule-strong)" : "var(--rule)"} strokeWidth={t === start ? 1 : 0.5}
                  strokeDasharray={t === start ? undefined : "2 3"} />
                <text x={M.l - 8} y={g.y(t) + 4} textAnchor="end" fontSize="12" fill="var(--slate)" className="num axis">{tick(t, g.step)}</text>
              </g>
            ))}
            {markers.map((mk) => {
              const i = data.findIndex((d) => d.session >= mk.session);
              if (i <= 0) return null;
              return (
                <g key={mk.session + mk.label} className="fade-late">
                  <line x1={g.x(i)} x2={g.x(i)} y1={M.t} y2={H - M.b} stroke="var(--ai)" strokeWidth="1" strokeDasharray="4 4" />
                  <text x={g.x(i) + 4} y={M.t + 10} fontSize="12" fill="var(--ai)">{mk.label}</text>
                </g>
              );
            })}
            <text className="axis" x={M.l} y={H - 6} fontSize="12" fill="var(--slate)">{day(data[0].session)}</text>
            <text className="axis" x={W - M.r} y={H - 6} fontSize="12" fill="var(--slate)" textAnchor="end">{last.live ? "now" : day(last.session)}</text>
            <defs>
              <linearGradient id="kb-potfill" gradientUnits="userSpaceOnUse" x1="0" y1={g.fillTop.toFixed(1)} x2="0" y2={(g.fillTop + 32).toFixed(1)}>
                <stop offset="0" stopColor="var(--s-pot)" stopOpacity="0.14" /><stop offset="1" stopColor="var(--s-pot)" stopOpacity="0" />
              </linearGradient>
            </defs>
            {draw && <clipPath id="kb-potdraw"><rect className="chart-draw" x={0} y={0} width={W} height={H} /></clipPath>}
            <g clipPath={draw ? "url(#kb-potdraw)" : undefined}>
              <path className="morph" d={g.area} fill="url(#kb-potfill)" />
              <path className="morph" d={g.spy} fill="none" stroke="var(--s-spy)" strokeWidth="1.5" strokeDasharray="0.1 5" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
              <path className="morph" d={g.pot} fill="none" stroke="var(--s-pot)" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
            </g>
            {/* the live point: the same point as in the logo, with one faint ring */}
            <circle className="fade-late" cx={g.x(data.length - 1)} cy={g.y(last.pot)} r="9" fill="none" stroke="var(--live)" strokeOpacity="0.35" strokeWidth="1.5" />
            <circle key={`p${pops}`} className={pops ? "fade-late point-pop" : "fade-late"} cx={g.x(data.length - 1)} cy={g.y(last.pot)} r="5" fill="var(--live)" />
            {pops > 0 && <circle key={`r${pops}`} className="point-pulse" cx={g.x(data.length - 1)} cy={g.y(last.pot)} r="5" fill="none" stroke="var(--live)" strokeWidth="1.5" />}
            <text className="fade-late axis" x={W - M.r + 10} y={ly1 + 4} fontSize="12" fontWeight="600" fill="var(--ink)">Pot {whole(last.pot)}{last.live ? " now" : ""}</text>
            <text className="fade-late axis" x={W - M.r + 10} y={ly2 + 4} fontSize="12" fill="var(--slate)">SPY {whole(last.spy)}</text>
            {h && hover !== null && (
              <g>
                <line x1={g.x(hover)} x2={g.x(hover)} y1={M.t} y2={H - M.b} stroke="var(--stale)" strokeWidth="1" strokeDasharray="3 3" />
                <circle cx={g.x(hover)} cy={g.y(h.pot)} r="4" fill="var(--s-pot)" stroke="var(--surface)" strokeWidth="2" />
                <circle cx={g.x(hover)} cy={g.y(h.spy)} r="4" fill="var(--s-spy)" stroke="var(--surface)" strokeWidth="2" />
              </g>
            )}
          </svg>
          {compact && g.ticks.length > 0 && (
            <div className="chart-scale" aria-hidden="true">
              <span className="num" style={{ top: `${(g.y(g.ticks.at(-1)!) / H) * 100}%` }}>{tick(g.ticks.at(-1)!, g.step)}</span>
              {g.ticks.length > 1 && <span className="num" style={{ top: `${(g.y(g.ticks[0]) / H) * 100}%` }}>{tick(g.ticks[0], g.step)}</span>}
            </div>
          )}
          {/* Phones stretch the compact chart to fit (preserveAspectRatio none), which would draw a circle as an oval:
              there the live point is a dot laid over the chart at the line's end, in percent of the chart's box. */}
          {compact && <span key={`d${pops}`} className={`chart-point${pops ? " point-pop" : ""}`} aria-hidden="true"
            style={{ left: `${(g.x(data.length - 1) / W) * 100}%`, top: `${(g.y(last.pot) / H) * 100}%` }} />}
          {compact && <div className="chart-days" aria-hidden="true"><span>{day(data[0].session)}</span><span>{last.live ? "now" : day(last.session)}</span></div>}
          {h && hover !== null && (
            <div className="tip" style={{ left: `${(g.x(hover) / W) * 100}%`, top: 0, transform: `translateX(${hover > data.length / 2 ? "-105%" : "5%"})` }}>
              <div className="muted">{day(h.session)}</div>
              <div className="num">Pot {money(h.pot)}</div>
              <div className="num muted">SPY {money(h.spy)}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function niceStep(raw: number) {
  const p = 10 ** Math.floor(Math.log10(raw)); const f = raw / p;
  return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * p;
}
/** Axis labels that never repeat: thousands only when the step is a whole thousand (audit #13). */
function tick(v: number, step: number) { return step >= 1000 ? `$${(v / 1000).toFixed(0)}k` : whole(v); }
/** The end labels use the same figure as the hero, to the dollar. */
function whole(v: number) { return `$${Math.round(v).toLocaleString("en-US")}`; }
