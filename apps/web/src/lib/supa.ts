// The Supabase client of the dashboard, made of the three parts it uses: sign-in (auth-js), the kb_* RPCs
// (postgrest-js) and the price stream (realtime-js). `createClient` of @supabase/supabase-js builds the same three the
// same way and adds a file-storage client and an edge-functions client that no page calls; a class cannot be
// tree-shaken, so those shipped in the first load. Here they are not built, and realtime-js (with its Phoenix socket)
// is fetched on the first `realtime()` call, which is when a page first asks for streamed prices.
//
// What must stay as supabase-js does it, and is pinned by supa.test.ts against the real `createClient`:
// - the place the session is stored (`sb-<project ref>-auth-token`): another name would sign everyone out;
// - every request carries `apikey`, and `Authorization: Bearer <the session's token, else the key>`;
// - the realtime client gets the session's token when it changes, and asks for it when it connects.
// The one difference on the wire: `X-Client-Info` names the part that sent the request (gotrue-js, postgrest-js,
// realtime-js: each library's own default) and no longer "supabase-js".
import { AuthClient } from "@supabase/auth-js";
import { PostgrestClient } from "@supabase/postgrest-js";
import type { RealtimeClient } from "@supabase/realtime-js";

export type { Session } from "@supabase/auth-js";
export type { RealtimeChannel, RealtimeClient } from "@supabase/realtime-js";

export function createClient(supabaseUrl: string, supabaseKey: string) {
  const trimmed = supabaseUrl?.trim();
  if (!trimmed) throw new Error("supabaseUrl is required.");
  if (!/^https?:\/\//i.test(trimmed)) throw new Error("Invalid supabaseUrl: Must be a valid HTTP or HTTPS URL.");
  if (!supabaseKey) throw new Error("supabaseKey is required.");
  const base = new URL(trimmed.endsWith("/") ? trimmed : `${trimmed}/`);

  const auth = new AuthClient({
    url: new URL("auth/v1", base).href,
    headers: { Authorization: `Bearer ${supabaseKey}`, apikey: supabaseKey },
    storageKey: `sb-${base.hostname.split(".")[0]}-auth-token`,
    autoRefreshToken: true, persistSession: true, detectSessionInUrl: true, flowType: "pkce",
    hasCustomAuthorizationHeader: false,
  });

  const sessionToken = async () => (await auth.getSession()).data.session?.access_token ?? null;
  const authed = (inner: typeof fetch): typeof fetch => async (input, init) => {
    const token = await sessionToken();
    const headers = new Headers(init?.headers);
    if (!headers.has("apikey")) headers.set("apikey", supabaseKey);
    if (!headers.has("Authorization")) headers.set("Authorization", `Bearer ${token ?? supabaseKey}`);
    return inner(input, { ...init, headers });
  };
  const plainFetch: typeof fetch = (...args) => fetch(...args);

  const rest = new PostgrestClient(new URL("rest/v1", base).href, { schema: "public", fetch: plainFetch, wrapFetch: authed });

  // The price stream. Until it is asked for there is no realtime client; the token the session last changed to is
  // kept, and handed over when the client is made (supabase-js hands it over as the change happens).
  const realtimeUrl = new URL("realtime/v1", base);
  realtimeUrl.protocol = realtimeUrl.protocol.replace("http", "ws");
  let client: RealtimeClient | null = null;
  let loading: Promise<RealtimeClient> | null = null;
  let changedToken: string | undefined;
  const realtime = (): Promise<RealtimeClient> => {
    loading ??= import("@supabase/realtime-js").then(({ RealtimeClient: Realtime }) => {
      client = new Realtime(realtimeUrl.href, {
        headers: {}, accessToken: async () => (await sessionToken()) ?? supabaseKey, fetch: authed(plainFetch),
        params: { apikey: supabaseKey },
      });
      if (changedToken !== undefined) void client.setAuth(changedToken);
      return client;
    }).catch((e: unknown) => { loading = null; throw e; });   // a failed download is tried again on the next ask
    return loading;
  };
  auth.onAuthStateChange((event, session) => {
    const token = session?.access_token;
    if ((event === "TOKEN_REFRESHED" || event === "SIGNED_IN" || event === "INITIAL_SESSION") && changedToken !== token) {
      changedToken = token;
      void client?.setAuth(token);
    } else if (event === "SIGNED_OUT") {
      void client?.setAuth();
      changedToken = undefined;
    }
  });

  return {
    auth,
    rpc: (fn: string, args: Record<string, unknown> = {}) => rest.rpc(fn, args, { head: false, get: false, count: undefined }),
    /** The realtime client, downloaded and made on the first call; the same one after that. */
    realtime,
  };
}
