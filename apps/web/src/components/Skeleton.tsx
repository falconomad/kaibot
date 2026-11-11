/** Shimmer placeholders shaped like the screen that's loading, so nothing jumps when data arrives. */
const Bar = ({ w = "100%", h = 14, r = 10 }: { w?: number | string; h?: number; r?: number }) => (
  <span className="sk" style={{ width: w, height: h, borderRadius: r }} />
);

export function Skeleton({ variant = "list" }: { variant?: "dashboard" | "list" | "detail" | "center" }) {
  if (variant === "center")
    return <div className="center" aria-busy="true" aria-label="Loading"><div className="sk-stack" style={{ width: "min(420px, 100%)" }}>
      <Bar w={140} h={20} /><Bar h={52} r={14} /><Bar h={52} r={999} /></div></div>;
  if (variant === "dashboard")
    return (
      <div className="sk-stack" aria-busy="true" aria-label="Loading">
        <div className="sk-row">
          <div className="sk-stack" style={{ flex: "999 1 560px" }}>
            <Bar w={150} /><Bar w={320} h={56} r={14} /><Bar w={260} h={16} />
            <div style={{ display: "flex", gap: 6 }}>{[0, 1, 2, 3].map((i) => <Bar key={i} w={52} h={36} r={999} />)}</div>
            <Bar h={300} r={24} />
          </div>
          <div className="sk-stack" style={{ flex: "1 1 320px" }}><Bar h={220} r={24} /><Bar h={140} r={24} /></div>
        </div>
        <Bar h={72} r={18} /><Bar h={72} r={18} />
      </div>
    );
  return (
    <div className="sk-stack" aria-busy="true" aria-label="Loading">
      <Bar w={220} h={32} r={12} />
      {variant === "detail" ? <div className="sk-row"><Bar h={300} r={24} /><Bar h={300} r={24} /></div>
        : <div className="sk-row">{[0, 1, 2].map((i) => <Bar key={i} h={92} r={20} />)}</div>}
      {[0, 1, 2, 3, 4].map((i) => (
        <div key={i} style={{ display: "flex", alignItems: "center", gap: 14 }}><Bar w={40} h={40} r={999} /><Bar h={16} /><Bar w={90} h={16} /></div>
      ))}
    </div>
  );
}
