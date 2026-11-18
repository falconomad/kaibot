// Types for scripts/logo_domains.mjs, so src tests can import its pick under strict TypeScript.
export const SEARCH_URL: string;
export const CDN: string;
export function cleanDomain(website: unknown): string | null;
export function pickWebsite(symbol: string, results: unknown): string | null;
