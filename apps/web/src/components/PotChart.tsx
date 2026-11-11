import { useMemo, useRef, useState } from "react";
import type { Curve } from "../lib/model";
import { day, money } from "../lib/fmt";

// Pot vs SPY scaled to the same start. One y-axis (both are dollars). Palette validated with the dataviz
// validator (light #1A6FA0/#A8640F on #FFF, dark #4E9BCB/#BE8028 on #171E26); SPY is also dashed + labelled.
const W = 760, H = 240, M = { t: 12, r: 92, b: 24, l: 64 };

export function PotChart({ data, start }: { data: Curve[]; start: number }) {
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
    return { x, y, ticks, pot, spy: line("spy"), area };
  }, [data, start]);

  if (!data.length) return <p className="muted">No sessions yet.</p>;
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
    <div>
      <div className="legend" style={{ marginBottom: 6 }}>
        <span><i className="sw" style={{ borderColor: "var(--s-pot)" }} />Pot, after costs</span>
        <span><i className="sw" style={{ borderColor: "var(--s-spy)", borderTopStyle: "dashed" }} />SPY, same start</span>
        <span className="spacer" />
        <button style={{ minHeight: 32, fontSize: 13, padding: "0 14px" }} onClick={() => setTable(!table)}>{table ? "Show chart" : "Show table"}</button>
      </div>
      {table ? (
        <div style={{ maxHeight: 260, overflow: "auto" }}>
          <table className="list"><thead><tr><th>Session</th><th className="r">Pot</th><th className="r">SPY, same start</th></tr></thead>
            <tbody>{data.map((d) => <tr key={d.session}><td>{day(d.session)}</td><td className="r num">{money(d.pot)}</td><td className="r num">{money(d.spy)}</td></tr>)}</tbody>
          </table>
        </div>
      ) : (
        <div className="chart">
          <svg ref={ref} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Pot ${money(last.pot)} versus SPY ${money(last.spy)} at the end`}
            onPointerMove={move} onPointerLeave={() => setHover(null)}>
            {g.ticks.map((t) => (
              <g key={t}>
                <line x1={M.l} x2={W - M.r} y1={g.y(t)} y2={g.y(t)} stroke={t === start ? "var(--rule-strong)" : "var(--rule)"} strokeWidth={t === start ? 1 : 0.5}
                  strokeDasharray={t === start ? undefined : "2 3"} />
                <text x={M.l - 8} y={g.y(t) + 4} textAnchor="end" fontSize="11" fill="var(--slate)" className="num">{short(t)}</text>
              </g>
            ))}
            <text x={M.l} y={H - 6} fontSize="11" fill="var(--slate)">{day(data[0].session)}</text>
            <text x={W - M.r} y={H - 6} fontSize="11" fill="var(--slate)" textAnchor="end">{day(last.session)}</text>
            <defs>
              <linearGradient id="kb-potfill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor="var(--s-pot)" stopOpacity="0.28" /><stop offset="1" stopColor="var(--s-pot)" stopOpacity="0" />
              </linearGradient>
            </defs>
            {/* Lines start flat on the baseline and rise into shape once, when the chart first appears. */}
            <g className="rise" style={{ transformOrigin: `0 ${H - M.b}px` }}>
              <path className="morph" d={g.area} fill="url(#kb-potfill)" />
              <path className="morph" d={g.spy} fill="none" stroke="var(--s-spy)" strokeWidth="2" strokeDasharray="5 4" strokeLinejoin="round" />
              <path className="morph" d={g.pot} fill="none" stroke="var(--s-pot)" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
            </g>
            <circle className="fade-late" cx={g.x(data.length - 1)} cy={g.y(last.pot)} r="4.5" fill="var(--s-pot)" stroke="var(--surface)" strokeWidth="2" />
            <text className="fade-late" x={W - M.r + 10} y={ly1 + 4} fontSize="12" fontWeight="600" fill="var(--ink)">Pot {short(last.pot)}</text>
            <text className="fade-late" x={W - M.r + 10} y={ly2 + 4} fontSize="12" fill="var(--slate)">SPY {short(last.spy)}</text>
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
function short(v: number) { return v >= 10000 ? `$${(v / 1000).toFixed(v % 1000 === 0 ? 0 : 1)}k` : `$${v.toFixed(0)}`; }
