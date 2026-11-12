import { describe, expect, it } from "vitest";
import mainSrc from "./main.tsx?raw";
import shellSrc from "./components/Shell.tsx?raw";
import { ROUTE_LOADERS, isLazyRoute } from "./routes";

describe("lazy routes (#80)", () => {
  it("keeps the first screen (sign-in, shell, Home) out of the lazy set", () => {
    expect(isLazyRoute("/")).toBe(false);
    for (const eager of ["./pages/SignIn", "./pages/Live", "./components/Shell"]) expect(mainSrc).toContain(`from "${eager}"`);
  });

  it("imports no other page eagerly: every other page goes through a loader", () => {
    const eagerPages = [...mainSrc.matchAll(/from "\.\/pages\/(\w+)"/g)].map((m) => m[1]).sort();
    expect(eagerPages).toEqual(["Live", "SignIn"]);
    expect(Object.keys(ROUTE_LOADERS)).toHaveLength(10);
    for (const path of Object.keys(ROUTE_LOADERS)) expect(mainSrc).toContain(`load["${path}"]()`);
  });

  it("has a loader for every nav link except Home, so hover and focus can warm it", () => {
    const navTargets = [...shellSrc.matchAll(/\{ to: "([^"]+)"/g)].map((m) => m[1]);
    expect(navTargets.length).toBeGreaterThanOrEqual(8);
    for (const to of navTargets) expect(isLazyRoute(to)).toBe(to !== "/");
    expect(shellSrc.match(/\{\.\.\.warm\(n\.to\)\}/g)).toHaveLength(2);
  });

  it("does not treat prototype keys or unknown paths as routes", () => {
    for (const to of ["toString", "constructor", "/nope", ""]) expect(isLazyRoute(to)).toBe(false);
  });
});

describe("first load stays small (idle polish M7)", () => {
  const src = import.meta.glob(["./lib/*.ts", "./components/*.tsx", "./pages/Live.tsx", "./pages/SignIn.tsx", "./main.tsx"],
    { query: "?raw", import: "default", eager: true }) as Record<string, string>;
  const deps = (f: string) => [...(src[f] ?? "").matchAll(/^import (?!type )[^;]*?from "(\.[^"]+)"/gm)].map((m) => m[1]);
  const resolve = (from: string, rel: string) => {
    const parts = from.split("/").slice(0, -1);
    for (const p of rel.split("/")) { if (p === "..") parts.pop(); else if (p !== ".") parts.push(p); }
    const base = parts.join("/");
    return [`${base}.ts`, `${base}.tsx`].find((c) => c in src) ?? null;
  };
  it("keeps the agent room, gate rows, estimator and config editor out of Home's static imports", () => {
    const seen = new Set<string>(), todo = ["./main.tsx"];
    while (todo.length) {
      const f = todo.pop()!;
      if (seen.has(f)) continue;
      seen.add(f);
      for (const d of deps(f)) { const r = resolve(f, d); if (r) todo.push(r); }
    }
    expect(seen.has("./pages/Live.tsx")).toBe(true);
    for (const heavy of ["./lib/roomview.ts", "./lib/gateview.ts", "./lib/estimator.ts", "./lib/config.ts", "./lib/multiday.ts", "./lib/engine.ts"])
      expect(seen.has(heavy), heavy).toBe(false);
  });
});
