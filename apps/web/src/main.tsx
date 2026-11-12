import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import type { Session } from "@supabase/supabase-js";
import { Suspense, lazy, useEffect, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./tokens.css";
import "./app.css";
import { api, supa } from "./lib/api";
import { Shell } from "./components/Shell";
import { SignIn } from "./pages/SignIn";
import { Live } from "./pages/Live";
import { Skeleton } from "./components/Skeleton";
import { ROUTE_LOADERS as load } from "./routes";
import { STALE } from "./lib/poll";

// Lazy routes (see routes.ts). Sign-in, the shell and Home stay in the first load so the first screen is one round trip.
const Settings = lazy(() => load["/settings"]().then((m) => ({ default: m.Settings })));
const Health = lazy(() => load["/health"]().then((m) => ({ default: m.Health })));
const Tape = lazy(() => load["/tape"]().then((m) => ({ default: m.Tape })));
const Report = lazy(() => load["/report"]().then((m) => ({ default: m.Report })));
const Onboarding = lazy(() => load["/start"]().then((m) => ({ default: m.Onboarding })));
const TradeDetailPage = lazy(() => load["/trades/:id"]().then((m) => ({ default: m.TradeDetailPage })));
const GoNoGo = lazy(() => load["/gate"]().then((m) => ({ default: m.Gate })));
const Strategy = lazy(() => load["/strategy"]().then((m) => ({ default: m.Strategy })));
const StrategyHome = lazy(() => load["/strategy"]().then((m) => ({ default: m.StrategyHome })));
const FineTune = lazy(() => load["/strategy/fine-tune"]().then((m) => ({ default: m.FineTune })));
const Trades = lazy(() => load["/trades"]().then((m) => ({ default: m.Trades })));

/** A lazy page inside the shell: while its chunk loads, the header and nav stay put and only the page area shows a skeleton. */
const page = (el: ReactNode, variant: "list" | "detail" = "list") => <Suspense fallback={<Skeleton variant={variant} />}>{el}</Suspense>;

// Polls pause in hidden tabs; a refocus refetches only data older than 30 s (lib/poll.ts), so switching back to the tab
// doesn't fire every query at once.
const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true, staleTime: STALE.default,
  refetchIntervalInBackground: false } } });

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
        <h1 style={{ margin: 0, fontSize: 18 }}>{owner.isError ? "Couldn't check who owns this dashboard" : "Not your dashboard"}</h1>
        <p className="muted">{owner.isError ? "The server didn't answer. Try again in a moment." : "This Kaibot belongs to another account. Nothing here is visible to you."}</p>
        {owner.isError && <button onClick={() => owner.refetch()}>Try again</button>}
        <button onClick={() => supa.auth.signOut()}>Sign out</button>
      </div></div>
    );
  return (
    <Suspense fallback={<Skeleton variant="center" />}>
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Live />} />
        <Route path="trades" element={page(<Trades />)} />
        <Route path="trades/:id" element={page(<TradeDetailPage />, "detail")} />
        <Route path="tape" element={page(<Tape />)} />
        <Route path="report" element={page(<Report />)} />
        <Route path="health" element={page(<Health />)} />
        <Route path="gate" element={page(<GoNoGo />)} />
        <Route path="strategy" element={page(<Strategy />)}>
          <Route index element={<StrategyHome />} />
          <Route path="fine-tune" element={<FineTune />} />
        </Route>
        <Route path="settings" element={page(<Settings />)} />
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
