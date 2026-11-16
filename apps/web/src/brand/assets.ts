// What the build writes from the brand (#254): the name in index.html and the web app manifest. Used by vite.config.ts
// and the tests only, so none of it ships in the app's JavaScript. The icons the manifest lists are drawn from
// symbol.svg by `npm run icons` (scripts/gen_icons.py) to the list in icons.json.
import iconSpec from "./icons.json";
import { BRAND } from "./brand";

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** index.html says %BRAND% wherever the name goes (the title and the meta tags); the build fills it in. */
export const brandHtml = (html: string) => html.replace(/%BRAND%/g, esc(BRAND));

export interface IconSpec { file: string; sizes: number[]; scale: number; background?: string | null; use: "favicon" | "apple" | "manifest"; purpose?: "any" | "maskable" }
export const ICONS = iconSpec.icons as IconSpec[];
/** The colour behind the symbol in an icon: its own, or the list's (null = transparent). scripts/gen_icons.py reads it the same way. */
export const backgroundOf = (i: IconSpec): string | null => (i.background === undefined ? iconSpec.background : i.background);

export const MANIFEST_PATH = "manifest.webmanifest";

/** The web app manifest: the name under a home-screen icon, and the icons.
 *  `display` stays "browser": the sign-in link in the email opens in the browser, and a standalone app keeps its own
 *  storage, so it would never see that sign-in. */
export function manifest() {
  return {
    name: BRAND,
    short_name: BRAND,
    start_url: "/",
    display: "browser",
    icons: ICONS.filter((i) => i.use === "manifest").map((i) => ({
      src: `/${i.file}`, sizes: i.sizes.map((s) => `${s}x${s}`).join(" "), type: "image/png", purpose: i.purpose ?? "any",
    })),
  };
}
export const manifestJson = () => JSON.stringify(manifest(), null, 2) + "\n";
