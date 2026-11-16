// The app's display name, written once (#254; stakeholder, 16 Nov 2026: "on the app UI the name should be kaibo").
// It is always lowercase (stakeholder, 16 Nov 2026: "dont capitalize"): on its own, inside a sentence and as the
// first word of one. "kaibo manages each trade itself." "kaibo's books and the broker disagree."
// Display only: what a person reads on a page, in a sheet, in the browser tab or under the home-screen icon. Code
// identifiers, routes, storage keys, the Supabase schema and RPC names, env vars, the repo and the engine keep their
// names; nothing here renames them. To change the name, change BRAND. To change the logo, swap symbol.svg and run
// `npm run icons` (see components/Logo.tsx and assets.ts).

/** The name: one lowercase form wherever a person reads it (the wordmark, the browser tab, the home-screen label). */
export const BRAND = "kaibo";
/** The same word, for a sentence: `${NAME} trades on its own.` reads "kaibo trades on its own." Never capitalised,
 *  not even as the first word. Its own export only so that a sentence in the source reads as a sentence. */
export const NAME = BRAND;

/** The repo's folder on the Mac that runs the engine. A real folder, not the display name: it changes only if the repo
 *  is renamed. Show it inside <code>, as a path. */
export const REPO_DIR = "kaibot";

// The one place in apps/web that knows the earlier name. Text from outside writes the name with a capital, the earlier
// one ("Kaibot only trades ...") or today's ("Kaibo default"): both become the lowercase name. Lowercase "kaibot" is a
// module, a folder or a command and is never rewritten.
const CAPITALISED = /\bKaibot?\b/g;

/** Text written outside apps/web that is shown as it is (the engine's reasons for refusing a ticker, the shared
 *  presets file) may still say the earlier name, or capitalise the name. It passes through here on its way to the
 *  screen; the source keeps its wording. */
export function brandText<T extends string | null | undefined>(s: T): T {
  return (typeof s === "string" ? s.replace(CAPITALISED, BRAND) : s) as T;
}
