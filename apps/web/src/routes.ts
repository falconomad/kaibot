// Every screen that is not the first one (sign-in, Home) is its own chunk, fetched when its route is first shown.
// The loaders live here so main.tsx can build the lazy components and the nav can warm a chunk on hover or focus.
export const ROUTE_LOADERS = {
  "/trades": () => import("./pages/Trades"),
  "/trades/:id": () => import("./pages/TradeDetail"),
  "/tape": () => import("./pages/Tape"),
  "/report": () => import("./pages/Report"),
  "/health": () => import("./pages/Health"),
  "/gate": () => import("./pages/Gate"),
  "/strategy": () => import("./pages/Strategy"),
  "/strategy/fine-tune": () => import("./pages/FineTune"),
  "/settings": () => import("./pages/Settings"),
  "/start": () => import("./pages/Onboarding"),
} as const;

export type LazyRoute = keyof typeof ROUTE_LOADERS;
export const isLazyRoute = (to: string): to is LazyRoute => Object.prototype.hasOwnProperty.call(ROUTE_LOADERS, to);

/** Start fetching a route's chunk before the click lands. Home ("/") is in the first load, so it is a no-op there.
 *  A failed warm-up is ignored: the real navigation retries the import and shows its own error. */
export function preloadRoute(to: string): boolean {
  if (!isLazyRoute(to)) return false;
  void ROUTE_LOADERS[to]().catch(() => undefined);
  return true;
}
