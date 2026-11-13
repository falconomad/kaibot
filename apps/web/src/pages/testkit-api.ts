// Stand-in for lib/api in the page state tests (#166): the real module builds a Supabase client from env at import time.
// Every RPC returns a promise that never settles, so a test can only see what it seeded into the query cache.
export { coerce } from "../lib/coerce";

const never = () => new Promise<never>(() => {});
export const api = new Proxy({}, { get: () => never }) as typeof import("../lib/api").api;
export const supa = {
  auth: { signOut: never, signInWithOtp: never, getSession: never, onAuthStateChange: () => ({ data: { subscription: { unsubscribe() {} } } }) },
  rpc: never,
} as unknown as typeof import("../lib/api").supa;
