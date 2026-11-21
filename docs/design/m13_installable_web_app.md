# kaibo on the home screen: the installable web app

Issue #481 (M13 · ticket 4.7 of the engine audit). Date: 21 Nov 2026. **Status: built** (Frontend and Design), except
push notifications, which are a design only: `docs/design/m13_push_notifications.md`.
Brand: `docs/design/brand/brand_system_v1.md` (the name is always lowercase **kaibo**).

## Why

Both app stores ask for a legal entity before they list a trading app. A web app that the owner adds to the phone's
home screen needs no store: it has kaibo's icon, opens full screen without the browser's bars, and is the same site.

## What the owner sees

- **Settings, "On your phone".** One card.
  - Chrome or Edge (Android, desktop): a button, "Add kaibo to this device". It opens the browser's own install
    question. After a yes: "Added. Look for the kaibo icon on your home screen."
  - Safari (iPhone, iPad): three steps in words, because Safari has no install question a page can open:
    open the page in Safari, tap Share, choose "Add to Home Screen".
  - Already opened from the home screen: "kaibo is on your home screen and you are using it from there."
- **From the home screen** kaibo opens in its own window: dark status bar, no address bar, the same tab bar.
- **Sign-in inside the installed app.** An installed app keeps its own storage. On an iPhone the emailed sign-in link
  opens in Safari, and the installed app never sees that sign-in. So after "Email me a link", the installed app also
  shows a field: "Type the 6-digit code from the same email here." In a browser tab the field is not shown; the link
  works there as before.
- **Without a connection** the app still opens and each page says "You seem to be offline." It never shows a stored
  price, pot or position.

## What was built

| Part | Where | What |
|---|---|---|
| Manifest | `apps/web/src/brand/assets.ts` (written by the build) | `display: standalone`, `id`, `start_url` and `scope` at `/`, the page colour `#0A0B0D` as background and theme colour. Name from `BRAND` |
| Icons | `apps/web/public/icons/`, `apple-touch-icon.png` | Unchanged: the brand kit's icons of #254 (192, 512, maskable 512, 180 for the iPhone), drawn by `npm run icons` |
| iPhone tags | `apps/web/index.html` | `apple-mobile-web-app-capable`, status bar style `black` (a plain dark bar above the page, so nothing slides under the clock) |
| Service worker | `apps/web/src/pwa/sw.ts`, written by the build as `/sw.js` | The app shell only (below) |
| Start | `apps/web/src/pwa/start.ts`, `install.ts` | Registers the worker and keeps the browser's install offer. Loaded after the first screen, production builds only |
| Settings card | `apps/web/src/components/InstallApp.tsx` | "On your phone" |
| Sign-in by code | `apps/web/src/pwa/CodeEntry.tsx` | Installed app only, loaded on demand |
| Checks | `scripts/pwa_check.mjs` (every build, so CI), `scripts/pwa_browser_check.py` (`npm run check:pwa`, headless Chromium) | See "How it is verified" |

### The service worker keeps the app shell and nothing else

A trading dashboard must never show an old number as if it were now. The worker therefore stores only the files that
are the app itself: the page, the app's JavaScript and CSS, and the four font files.

| Request | What the worker does |
|---|---|
| Anything to another host: Supabase (data, sign-in, the price stream), the logo CDN | Nothing. It goes to the network as if there were no worker |
| `/api/*` on this site: live marks, stock summaries, the ticker check, the logo lookup | Nothing |
| Anything that is not a GET | Nothing |
| Opening a page | Network first. The stored page answers only when the network does not |
| The app's own files (`/assets/*.js`, `/assets/*.css`, the fonts) | From the store; stored on first use only if the answer really is JavaScript or CSS |
| Everything else on this site (the manifest, icons, favicon) | Nothing |

A new build has a new file list, so a new store; the old store is deleted when the new worker takes over. The
worker's text names no other host and no key.

## One setting outside the repository (needed before the iPhone path works)

The sign-in email must contain the 6-digit code. In the Supabase dashboard, under Authentication, in the email templates: the
**Magic Link** template, add a line with `{{ .Token }}` next to the link, for example
"Or type this code in the app: {{ .Token }}". Until then the email has only the link: the installed app on an iPhone
cannot sign in, and the browser tab keeps working. This is Product's or the stakeholder's to change; nothing in this
repository can.

## How it is verified

- **Every build (CI):** `scripts/pwa_check.mjs` fails the build when the manifest is not installable (name, root
  start and scope, `standalone`, 192 and 512 px icons that exist, a maskable one), when `index.html` lacks the
  manifest link or the iPhone tags, or when the worker lists a file the build does not have, names another host, or
  lost one of the rules that keep data out of the store.
- **Unit tests:** the worker's text is run against a stand-in worker scope (`src/pwa/sw.test.ts`): what it stores,
  what it never touches, offline, a new build. The Settings card, the install offer and the code field are mounted in
  jsdom (`src/pwa/install.test.tsx`).
- **A real browser:** `npm run check:pwa` opens the build in headless Chromium at a phone's size. Chromium reports no
  installability error; the worker controls the page and its store holds exactly the build's shell list; two calls to
  `/api/marks` and to a second host each reach the server and are not in the store; offline the app opens on
  `/positions` and both data calls fail; a new build replaces the store.
- **Not done here: real phones.** No iPhone or Android phone was available to this session. The checks to run by
  hand, once, after the deploy:

| Phone | Steps | Expected |
|---|---|---|
| Android, Chrome | Open the site, sign in, Settings, "Add kaibo to this device" | The install question; a kaibo icon on the home screen; it opens without the address bar, already signed in (Chrome shares its storage with the installed app) |
| iPhone, Safari | Open the site, Share, "Add to Home Screen" | A kaibo icon; it opens full screen with a dark status bar and asks to sign in |
| iPhone, installed app | "Email me a link", then type the code from the email (needs the template change above) | The dashboard |
| Either | Turn on flight mode, open kaibo | The app opens; pages say "You seem to be offline."; no price is shown as live |
| Either | Turn flight mode off | The pages load by themselves or on "Try again" |

Home-screen web apps stay available on iPhones in the EU: Apple announced their removal for iOS 17.4 and withdrew it
on 1 March 2024, before that release. One guide found while writing this still says otherwise, so the iPhone rows
above should be run on an iPhone in Germany before this is called done.

## Limits and what is left out

- **The first-load budget.** First-load JavaScript is 203.8 kB of 204.0 kB (203.7 kB before). Everything new loads on
  demand; the budget is not raised.
- **No splash images for the iPhone.** iOS shows the page's dark background while the app starts. Per-device splash
  images are a later polish.
- **No "new version" prompt.** A page is always fetched from the network when there is one, so an open app picks up
  a new build at its next start.
- **The browser check scripts** (`mobile_check.py`, `a11y_check.py`, `perf_check.py`) block service workers, so they
  measure the pages as before.
- **Push notifications** are a design only, in `docs/design/m13_push_notifications.md`.
