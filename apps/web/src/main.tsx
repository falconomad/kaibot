import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import type { Session } from "./lib/supa";
import { Suspense, lazy, useEffect, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import "./tokens.css";
import "./v4tokens.css";
import "./app.css";
import { api, stopRealtime, supa } from "./lib/api";
import { resetUserData, watchUser } from "./lib/session";
import { Shell } from "./components/Shell";
import { NotOwner, SignIn } from "./pages/SignIn";
import { Live } from "./pages/Live";
import { Skeleton } from "./components/Skeleton";
import { ErrorBoundary, PageBoundary } from "./components/ErrorBoundary";
import { ROUTE_LOADERS as load } from "./routes";
import { STALE } from "./lib/poll";
import { primeGlowArrival, primeGlowFromUrl } from "./lib/glow";

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
const HowItDecides = lazy(() => load["/how"]().then((m) => ({ default: m.HowItDecides })));
// Invites (migration 0045): the registration page and an invited user's page, neither in the first load.
const Register = lazy(() => import("./pages/Register").then((m) => ({ default: m.Register })));
const Member = lazy(() => import("./pages/Member").then((m) => ({ default: m.Member })));

/** A lazy page inside the shell: while its chunk loads, the header and nav stay put and only the page area shows a skeleton.
 *  Each page has its own error boundary (#738): a page that throws (or whose chunk fails) shows a short message with
 *  Retry in the page area, and the header, the navigation and the halt bar keep working. */
const page = (name: string, el: ReactNode, variant: "list" | "detail" = "list") => (
  <PageBoundary key={name} name={name}><Suspense fallback={<Skeleton variant={variant} />}>{el}</Suspense></PageBoundary>
);

// Polls pause in hidden tabs; a refocus refetches only data older than 30 s (lib/poll.ts), so switching back to the tab
// doesn't fire every query at once.
const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true, staleTime: STALE.default,
  refetchIntervalInBackground: false } } });

// Sign-out, or another account in this tab: drop every cached answer and the realtime channels (lib/session.ts). Wired
// before Gate subscribes, so the cache is empty before the next screen renders.
watchUser(supa.auth, () => resetUserData(qc, stopRealtime));

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
  // Not the owner: an invited member (0045's kb_me) gets their own page; anyone else, or before 0045, "Not your dashboard".
  // A member whose profile was not made at confirmation gets it made here first (0046's kb_profile_repair, #635).
  const me = useQuery({ queryKey: ["me", session?.user.id], queryFn: api.meAtSignIn, enabled: !!session && owner.data === false,
    staleTime: Infinity, retry: 1 });
  const { pathname, search } = useLocation();

  if (session === undefined || (session && owner.isPending)) return <Skeleton variant="center" />;
  if (!session) return pathname === "/register"
    ? <Suspense fallback={<Skeleton variant="center" />}><Register search={search} /></Suspense> : <SignIn />;
  if (owner.data === false && me.isPending) return <Skeleton variant="center" />;
  if (owner.data === false && me.data?.member) {
    return <Suspense fallback={<Skeleton variant="center" />}><Member name={me.data.name} canInvite={me.data.can_invite === true} /></Suspense>;
  }
  if (owner.isError || owner.data === false) return <NotOwner failed={owner.isError} retry={() => { void owner.refetch(); }} />;
  return (
    <Suspense fallback={<Skeleton variant="center" />}>
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<PageBoundary key="Home" name="Home"><Live /></PageBoundary>} />
        <Route path="positions" element={page("Positions", <Positions />)} />
        <Route path="positions/:id" element={page("Position detail", <TradeDetailPage />, "detail")} />
        <Route path="activity" element={page("Activity", <Tape />)} />
        <Route path="more" element={page("More", <More />)} />
        {/* v4: Trades is now Positions and Tape is Activity; old links keep working */}
        <Route path="trades" element={<Navigate to="/positions" replace />} />
        <Route path="trades/:id" element={<OldTrade />} />
        <Route path="tape" element={<Navigate to="/activity" replace />} />
        <Route path="holdings" element={<Navigate to="/positions" replace />} />
        <Route path="report" element={page("Report", <Report />)} />
        <Route path="health" element={page("Engine", <Health />)} />
        <Route path="gate" element={page("Go / no-go", <GoNoGo />)} />
        <Route path="strategy" element={page("Strategy", <Strategy />)}>
          <Route index element={<StrategyHome />} />
          <Route path="fine-tune" element={<FineTune />} />
        </Route>
        <Route path="settings" element={page("Settings", <Settings />)} />
        <Route path="how" element={page("How it decides", <HowItDecides />)} />
      </Route>
      <Route path="start" element={<PageBoundary key="Start" name="Start"><Onboarding /></PageBoundary>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </Suspense>
  );
}

// The installable web app (#481): the service worker and the install offer, fetched after the first screen.
if (import.meta.env.PROD) void import("./pwa/start");

// Landing from the emailed sign-in link: the glow starts where sign-in had it (lib/glow.ts). Otherwise, a signed-out
// first load (sign-in or /register) plays sign-in's entrance, the mirror of the sign-out drift.
if (!primeGlowFromUrl()) primeGlowArrival();

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={qc}>
    <BrowserRouter><ErrorBoundary name="App"><Gate /></ErrorBoundary></BrowserRouter>
  </QueryClientProvider>,
);
