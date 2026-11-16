import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import type { Session } from "@supabase/supabase-js";
import { Suspense, lazy, useEffect, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import "./tokens.css";
import "./v4tokens.css";
import "./app.css";
import { api, supa } from "./lib/api";
import { Shell } from "./components/Shell";
import { NotOwner, SignIn } from "./pages/SignIn";
import { Live } from "./pages/Live";
import { Skeleton } from "./components/Skeleton";
import { ROUTE_LOADERS as load } from "./routes";
import { STALE } from "./lib/poll";
import { primeGlowFromUrl } from "./lib/glow";

// Lazy routes (see routes.ts). Sign-in, the shell and Home stay in the first load so the first screen is one round trip.
const Settings = lazy(() => load["/settings"]().then((m) => ({ default: m.Settings })));
const Health = lazy(() => load["/health"]().then((m) => ({ default: m.Health })));
const Tape = lazy(() => load["/activity"]().then((m) => ({ default: m.Tape })));
const Report = lazy(() => load["/report"]().then((m) => ({ default: m.Report })));
const Onboarding = lazy(() => load["/start"]().then((m) => ({ default: m.Onboarding })));
const TradeDetailPage = lazy(() => load["/positions/:id"]().then((m) => ({ default: m.TradeDetailPage })));
const GoNoGo = lazy(() => load["/gate"]().then((m) => ({ default: m.Gate })));
const Strategy = lazy(() => load["/strategy"]().then((m) => ({ default: m.Strategy })));
const StrategyHome = lazy(() => load["/strategy"]().then((m) => ({ default: m.StrategyHome })));
const FineTune = lazy(() => load["/strategy/fine-tune"]().then((m) => ({ default: m.FineTune })));
const More = lazy(() => load["/more"]().then((m) => ({ default: m.More })));
const Positions = lazy(() => load["/positions"]().then((m) => ({ default: m.Positions })));

/** A lazy page inside the shell: while its chunk loads, the header and nav stay put and only the page area shows a skeleton. */
const page = (el: ReactNode, variant: "list" | "detail" = "list") => <Suspense fallback={<Skeleton variant={variant} />}>{el}</Suspense>;

// Polls pause in hidden tabs; a refocus refetches only data older than 30 s (lib/poll.ts), so switching back to the tab
// doesn't fire every query at once.
const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true, staleTime: STALE.default,
  refetchIntervalInBackground: false } } });

/** /trades/:id (before v4) -> /positions/:id, keeping the query (?tab=...). */
function OldTrade() {
  const { id } = useParams();
  const { search } = useLocation();
  return <Navigate to={`/positions/${id}${search}`} replace />;
}

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
  if (owner.isError || owner.data === false) return <NotOwner failed={owner.isError} retry={() => { void owner.refetch(); }} />;
  return (
    <Suspense fallback={<Skeleton variant="center" />}>
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Live />} />
        <Route path="positions" element={page(<Positions />)} />
        <Route path="positions/:id" element={page(<TradeDetailPage />, "detail")} />
        <Route path="activity" element={page(<Tape />)} />
        <Route path="more" element={page(<More />)} />
        {/* v4: Trades is now Positions and Tape is Activity; old links keep working */}
        <Route path="trades" element={<Navigate to="/positions" replace />} />
        <Route path="trades/:id" element={<OldTrade />} />
        <Route path="tape" element={<Navigate to="/activity" replace />} />
        <Route path="holdings" element={<Navigate to="/positions" replace />} />
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

// Landing from the emailed sign-in link: the glow starts where sign-in had it (lib/glow.ts).
primeGlowFromUrl();

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={qc}>
    <BrowserRouter><Gate /></BrowserRouter>
  </QueryClientProvider>,
);
