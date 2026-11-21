// What the build writes from the brand (#254): the name in index.html and the web app manifest. Used by vite.config.ts
// and the tests only, so none of it ships in the app's JavaScript. The icons the manifest lists are drawn from
// Design's files in logo/ by `npm run icons` (scripts/gen_icons.py) to the list in icons.json.
import iconSpec from "./icons.json";
import { BRAND } from "./brand";

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** index.html says %BRAND% wherever the name goes (the title and the meta tags); the build fills it in. */
export const brandHtml = (html: string) => html.replace(/%BRAND%/g, esc(BRAND));

export interface IconSpec { file: string; source: string; sizes: number[]; scale: number; background?: string | null; use: "favicon" | "apple" | "manifest"; purpose?: "any" | "maskable" }
export const ICONS = iconSpec.icons as IconSpec[];
/** The colour behind the symbol in an icon: its own, or the list's (null = transparent). scripts/gen_icons.py reads it the same way. */
export const backgroundOf = (i: IconSpec): string | null => (i.background === undefined ? iconSpec.background : i.background);

export const MANIFEST_PATH = "manifest.webmanifest";

/** The colour behind the app while it starts and of the phone's status bar: the page colour of the dark theme
 *  (tokens: --paper; index.html's theme-color says the same, checked in brand.test.tsx). */
export const APP_COLOUR = "#0A0B0D";

/** The web app manifest (M13 4.7, #481): the name under the home-screen icon, the icons, and how the installed app
 *  opens. `display` is "standalone": from the home screen it opens in its own window, without the browser's bars.
 *  An installed app keeps its own storage, so it signs in by itself, with the code from the sign-in email
 *  (pwa/CodeEntry.tsx); the emailed link keeps working in a browser tab. `id` and `scope` pin the app to this site's
 *  root, so a later change of `start_url` is still the same app to the phone. */
export function manifest() {
  return {
    id: "/",
    name: BRAND,
    short_name: BRAND,
    description: "Paper trading dashboard",
    lang: "en",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: APP_COLOUR,
    theme_color: APP_COLOUR,
    icons: ICONS.filter((i) => i.use === "manifest").map((i) => ({
      src: `/${i.file}`, sizes: i.sizes.map((s) => `${s}x${s}`).join(" "), type: "image/png", purpose: i.purpose ?? "any",
    })),
  };
}
export const manifestJson = () => JSON.stringify(manifest(), null, 2) + "\n";
