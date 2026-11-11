import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import type { Session } from "@supabase/supabase-js";
import { Suspense, lazy, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./tokens.css";
import "./app.css";
import { api, supa } from "./lib/api";
import { Shell } from "./components/Shell";
import { SignIn } from "./pages/SignIn";
import { Live } from "./pages/Live";
import { Skeleton } from "./components/Skeleton";
const Settings = lazy(() => import("./pages/Settings").then((m) => ({ default: m.Settings })));
const Health = lazy(() => import("./pages/Health").then((m) => ({ default: m.Health })));
const Tape = lazy(() => import("./pages/Tape").then((m) => ({ default: m.Tape })));
const Report = lazy(() => import("./pages/Report").then((m) => ({ default: m.Report })));
const Onboarding = lazy(() => import("./pages/Onboarding").then((m) => ({ default: m.Onboarding })));
const TradeDetailPage = lazy(() => import("./pages/TradeDetail").then((m) => ({ default: m.TradeDetailPage })));
const GoNoGo = lazy(() => import("./pages/Gate").then((m) => ({ default: m.Gate })));
const Strategy = lazy(() => import("./pages/Strategy").then((m) => ({ default: m.Strategy })));
const Trades = lazy(() => import("./pages/Trades").then((m) => ({ default: m.Trades })));

const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true, staleTime: 15_000 } } });

function Gate() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  useEffect(() => {
    supa.auth.getSession().then(({ data }) => setSession(data.session));
    const { data } = supa.auth.onAuthStateChange((_e, s) => setSession(s));
    return () => data.subscription.unsubscribe();
  }, []);
  // Server decides ownership: the first account to sign in claims the dashboard; everyone else is refused.
  const owner = useQuery({ queryKey: ["owner", session?.user.id], queryFn: api.claimOwner, enabled: !!session, staleTime: Infinity });

  if (session === undefined || (session && owner.isPending)) return <Skeleton variant="center" />;
  if (!session) return <SignIn />;
  if (owner.isError || owner.data === false)
    return (
      <div className="center"><div className="card">
        <h1 style={{ margin: 0, fontSize: 18 }}>Not your dashboard</h1>
        <p className="muted">This Kaibot belongs to another account. Nothing here is visible to you.</p>
        <button onClick={() => supa.auth.signOut()}>Sign out</button>
      </div></div>
    );
  return (
    <Suspense fallback={<div className="shell"><main><Skeleton /></main></div>}>
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Live />} />
        <Route path="trades" element={<Trades />} />
        <Route path="trades/:id" element={<TradeDetailPage />} />
        <Route path="tape" element={<Tape />} />
        <Route path="report" element={<Report />} />
        <Route path="health" element={<Health />} />
        <Route path="gate" element={<GoNoGo />} />
        <Route path="strategy" element={<Strategy />} />
        <Route path="settings" element={<Settings />} />
      </Route>
      <Route path="start" element={<Onboarding />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </Suspense>
  );
}

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={qc}>
    <BrowserRouter><Gate /></BrowserRouter>
  </QueryClientProvider>,
);
