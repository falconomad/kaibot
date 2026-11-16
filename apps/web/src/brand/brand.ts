// The app's display name, written once (#254; stakeholder, 16 Nov 2026: "on the app UI the name should be kaibo").
// Display only: what a person reads on a page, in a sheet, in the browser tab or under the home-screen icon. Code
// identifiers, routes, storage keys, the Supabase schema and RPC names, env vars, the repo and the engine keep their
// names; nothing here renames them. To change the name, change BRAND. To change the logo, swap symbol.svg and run
// `npm run icons` (see components/Logo.tsx and assets.ts).

/** The wordmark, lowercase: the header, sign-in, the browser tab, the home-screen label. */
export const BRAND = "kaibo";
/** The name inside a sentence: "Kaibo trades on its own." */
export const NAME = BRAND.charAt(0).toUpperCase() + BRAND.slice(1);

/** The repo's folder on the Mac that runs the engine. A real folder, not the display name: it changes only if the repo
 *  is renamed. Show it inside <code>, as a path. */
export const REPO_DIR = "kaibot";

// The one place in apps/web that knows the earlier name. Lowercase "kaibot" is a module, a folder or a command and is
// never rewritten; only the capitalised name in a sentence is.
const EARLIER_NAME = /\bKaibot\b/g;

/** Text written outside apps/web that is shown as it is (the engine's reasons for refusing a ticker, the shared
 *  presets file) may still say the earlier name. It passes through here on its way to the screen; the source keeps
 *  its wording. */
export function brandText<T extends string | null | undefined>(s: T): T {
  return (typeof s === "string" ? s.replace(EARLIER_NAME, NAME) : s) as T;
}
