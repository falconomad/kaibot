import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import "./tokens.css";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const qc = new QueryClient();

function EngineStatus() {
  // Polling only (frontend v2 plan): status every 5 s. Controls are never optimistic.
  const { data, isError } = useQuery({
    queryKey: ["status"],
    queryFn: () => fetch(`${API}/api/v1/status`).then((r) => r.json()),
    refetchInterval: 5000,
  });
  if (isError) return <p>Engine unreachable</p>;
  return <p className="num">Engine: {data?.state ?? "…"}</p>;
}

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={qc}>
    <header style={{ padding: 12, borderBottom: "1px solid var(--ink)" }}>
      <b>kaibot</b> <span className="num">PAPER</span>
    </header>
    <main style={{ padding: 24 }}>
      <EngineStatus />
    </main>
  </QueryClientProvider>,
);
