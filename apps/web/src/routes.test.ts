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
