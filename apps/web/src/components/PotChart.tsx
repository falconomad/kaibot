import { useMemo, useRef, useState } from "react";
import type { Curve } from "../lib/model";
import { day, money } from "../lib/fmt";

// Pot vs SPY scaled to the same start. One y-axis (both are dollars). Brand system v1 (#265): the pot's line is the
// brand blue and ends in the live point; the benchmark is neutral grey, told apart by its dots and its label.
const W = 760, H = 240, M = { t: 12, r: 92, b: 24, l: 64 };

/** Optional chapter boundaries (M5 S48): a dashed rule at the first session on or after each `session`. */
export interface ChartMarker { session: string; label: string }

/** `compact` (v4 Home): no legend or table button; on phones also no axis labels (CSS), the line only. */
export function PotChart({ data, start, markers = [], compact = false }: { data: Curve[]; start: number; markers?: ChartMarker[]; compact?: boolean }) {
  const [hover, setHover] = useState<number | null>(null);
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
    return { x, y, ticks, pot, spy: line("spy"), area, step };
  }, [data, start]);

  // Fewer than two points draw no line: say what will appear instead of an empty frame.
  if (data.length < 2) return <div className="chart-empty"><b>No history yet</b><span className="muted">The line starts at the first close and runs next to the S&amp;P 500 from the same start.</span></div>;
  const last = data[data.length - 1];
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
      <div className="legend" style={{ marginBottom: 6 }}>
        <span><i className="sw" style={{ borderColor: "var(--s-pot)" }} />Pot, after costs</span>
        <span><i className="sw" style={{ borderColor: "var(--s-spy)", borderTopStyle: "dotted" }} />SPY, same start</span>
        <span className="spacer" />
        <button style={{ minHeight: 44, fontSize: 13, padding: "0 14px" }} onClick={() => setTable(!table)}>{table ? "Show chart" : "Show table"}</button>
      </div>
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
              <linearGradient id="kb-potfill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor="var(--s-pot)" stopOpacity="0.28" /><stop offset="1" stopColor="var(--s-pot)" stopOpacity="0" />
              </linearGradient>
            </defs>
            {/* Lines start flat on the baseline and rise into shape once, when the chart first appears. */}
            <g className="rise" style={{ transformOrigin: `0 ${H - M.b}px` }}>
              <path className="morph" d={g.area} fill="url(#kb-potfill)" />
              <path className="morph" d={g.spy} fill="none" stroke="var(--s-spy)" strokeWidth="1.5" strokeDasharray="0.1 5" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
              <path className="morph" d={g.pot} fill="none" stroke="var(--s-pot)" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
            </g>
            {/* the live point: the same point as in the logo, with one faint ring */}
            <circle className="fade-late" cx={g.x(data.length - 1)} cy={g.y(last.pot)} r="9" fill="none" stroke="var(--live)" strokeOpacity="0.35" strokeWidth="1.5" />
            <circle className="fade-late" cx={g.x(data.length - 1)} cy={g.y(last.pot)} r="5" fill="var(--live)" />
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
