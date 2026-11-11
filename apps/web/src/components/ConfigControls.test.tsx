// Accessibility contract of the Strategy / Fine-tune controls (#80). No DOM library is installed, so the markup is
// checked through react-dom/server and the keyboard logic through the pure helper the components call.
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PillSwitch, RadioGroup, Slider, Stepper, Switch, radioKeyTarget } from "./ConfigControls";
import { Skeleton } from "./Skeleton";
import css from "../app.css?raw";
import strategySrc from "../pages/Strategy.tsx?raw";
import fineTuneSrc from "../pages/FineTune.tsx?raw";

const noop = () => {};
const tags = (html: string, tag: string) => html.match(new RegExp(`<${tag}\\b[^>]*>`, "g")) ?? [];
const attr = (tagHtml: string | undefined, name: string) => new RegExp(`\\s${name}="([^"]*)"`).exec(tagHtml ?? "")?.[1] ?? null;

describe("RadioGroup", () => {
  const opts = [{ value: "a", label: "Alpha" }, { value: "b", label: "Beta" }, { value: "c", label: "Gamma", disabled: true }, { value: "d", label: "Delta" }];
  const html = renderToStaticMarkup(<RadioGroup label="Pick one" options={opts} value="b" onChange={noop} />);
  const radios = tags(html, "button");

  it("is a labelled radiogroup of radios with exactly one checked", () => {
    expect(html).toMatch(/^<div role="radiogroup" aria-label="Pick one"/);
    expect(radios).toHaveLength(4);
    expect(radios.every((r) => attr(r, "role") === "radio" && attr(r, "type") === "button")).toBe(true);
    expect(radios.map((r) => attr(r, "aria-checked"))).toEqual(["false", "true", "false", "false"]);
  });

  it("has one tab stop, on the checked option (roving tabindex)", () => {
    expect(radios.map((r) => attr(r, "tabindex"))).toEqual(["-1", "0", "-1", "-1"]);
  });

  it("puts the tab stop on the first enabled option when nothing is checked, and never on a disabled one", () => {
    const none = tags(renderToStaticMarkup(<RadioGroup label="x" options={[{ ...opts[0], disabled: true }, ...opts.slice(1)]} value={null} onChange={noop} />), "button");
    expect(none.map((r) => attr(r, "tabindex"))).toEqual(["-1", "0", "-1", "-1"]);
    const onDisabled = tags(renderToStaticMarkup(<RadioGroup label="x" options={opts} value="c" onChange={noop} />), "button");
    expect(onDisabled.map((r) => attr(r, "tabindex"))).toEqual(["0", "-1", "-1", "-1"]);
  });

  it("marks disabled options with aria-disabled so they are still announced", () => {
    expect(radios.map((r) => attr(r, "aria-disabled"))).toEqual([null, null, "true", null]);
  });

  it("names a rich option by its label and reads the rest of the card as its description", () => {
    const rich = renderToStaticMarkup(<RadioGroup labelledBy="h" value="p" onChange={noop}
      options={[{ value: "p", label: "Steady", content: <span>card</span>, description: "Fewer, calmer trades. About 12 trades a month." }, { value: "q", label: "Plain" }]} />);
    expect(rich).toMatch(/^<div role="radiogroup" aria-labelledby="h"/);
    const [first, second] = tags(rich, "button");
    expect(attr(first, "aria-label")).toBe("Steady");
    const id = attr(first, "aria-describedby");
    expect(id).toBeTruthy();
    expect(rich).toContain(`<span id="${id}" class="sr-only">Fewer, calmer trades. About 12 trades a month.</span>`);
    expect(attr(second, "aria-label")).toBeNull();
    expect(attr(second, "aria-describedby")).toBeNull();
  });
});

describe("radioKeyTarget (keyboard)", () => {
  const enabled = [0, 1, 3];
  it("moves with the arrows, wraps around and skips disabled options", () => {
    expect(radioKeyTarget("ArrowRight", 0, enabled)).toBe(1);
    expect(radioKeyTarget("ArrowDown", 1, enabled)).toBe(3);
    expect(radioKeyTarget("ArrowRight", 3, enabled)).toBe(0);
    expect(radioKeyTarget("ArrowLeft", 0, enabled)).toBe(3);
    expect(radioKeyTarget("ArrowUp", 3, enabled)).toBe(1);
  });
  it("jumps with Home and End", () => {
    expect(radioKeyTarget("Home", 3, enabled)).toBe(0);
    expect(radioKeyTarget("End", 0, enabled)).toBe(3);
  });
  it("leaves every other key alone (Tab, Space, Enter, letters)", () => {
    for (const k of ["Tab", " ", "Enter", "a", "Escape"]) expect(radioKeyTarget(k, 1, enabled)).toBeNull();
  });
  it("recovers when focus sits on a disabled option", () => {
    expect(radioKeyTarget("ArrowRight", 2, enabled)).toBe(3);
    expect(radioKeyTarget("ArrowLeft", 2, enabled)).toBe(1);
    expect(radioKeyTarget("ArrowRight", 4, enabled)).toBe(0);
  });
  it("does nothing when no option is enabled", () => {
    expect(radioKeyTarget("ArrowRight", 0, [])).toBeNull();
    expect(radioKeyTarget("Home", 0, [])).toBeNull();
  });
});

describe("Switch and PillSwitch", () => {
  it("is a native button with role switch, a name and its state", () => {
    const on = renderToStaticMarkup(<Switch label="Dip reversal" on onChange={noop} />);
    expect(on).toMatch(/^<button type="button" role="switch" aria-checked="true" aria-label="Dip reversal"/);
    expect(on).toContain('<span aria-hidden="true">');
    const off = renderToStaticMarkup(<Switch label="Dip reversal" on={false} disabled onChange={noop} />);
    expect(off).toContain('aria-checked="false"');
    expect(off).toMatch(/\sdisabled=""/);
  });
  it("names the pill switch by its visible text and hides the tick", () => {
    const html = renderToStaticMarkup(<PillSwitch label="Calmer markets only" on onChange={noop} />);
    expect(html).toMatch(/^<button type="button" role="switch" aria-checked="true"/);
    expect(html).toContain('<span aria-hidden="true" class="dot">✓</span>Calmer markets only');
  });
});

describe("Slider", () => {
  const html = renderToStaticMarkup(<Slider id="ft-x" label="Risk a trade" value={0.5} min={0.1} max={1} step={0.05} text="0.50% of the pot" lo="0.10%" hi="1.00%" onChange={noop} />);
  const input = tags(html, "input")[0];
  it("is a native range input (arrow keys, Home/End and Page keys come from the browser)", () => {
    expect(attr(input, "type")).toBe("range");
    expect([attr(input, "min"), attr(input, "max"), attr(input, "step"), attr(input, "value")]).toEqual(["0.1", "1", "0.05", "0.5"]);
  });
  it("has a label and speaks its value in words", () => {
    expect(attr(input, "aria-label")).toBe("Risk a trade");
    expect(attr(input, "aria-valuetext")).toBe("0.50% of the pot");
  });
  it("is disabled when the bounds leave no choice", () => {
    const fixed = renderToStaticMarkup(<Slider label="x" value={3} min={3} max={3} step={1} text="3" onChange={noop} />);
    expect(tags(fixed, "input")[0]).toMatch(/\sdisabled=""/);
  });
});

describe("Stepper", () => {
  const html = renderToStaticMarkup(<Stepper label="Open at once" value={1} min={1} max={4} text="1 at once" onChange={noop} />);
  it("is a labelled group with named buttons and a live value", () => {
    expect(html).toMatch(/^<div class="kb-stepper" role="group" aria-label="Open at once"/);
    const [less, more] = tags(html, "button");
    expect(attr(less, "aria-label")).toBe("Fewer: Open at once");
    expect(attr(more, "aria-label")).toBe("More: Open at once");
    expect(html).toContain('<output class="num" aria-live="polite">1 at once</output>');
  });
  it("disables the button that would leave the bounds", () => {
    const [less, more] = tags(html, "button");
    expect(less).toMatch(/\sdisabled=""/);
    expect(more).not.toMatch(/\sdisabled=""/);
  });
});

describe("Skeleton (Suspense fallback)", () => {
  it("announces loading in every variant", () => {
    for (const v of ["list", "detail", "dashboard", "center"] as const)
      expect(renderToStaticMarkup(<Skeleton variant={v} />)).toMatch(/role="status" aria-busy="true" aria-label="Loading"/);
  });
});

describe("page-level accessibility rules", () => {
  it("turns off animation and transitions for prefers-reduced-motion", () => {
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{\s*\*, \*::before, \*::after \{ animation: none !important; transition: none !important; \}/);
    expect(fineTuneSrc).toContain('behavior: reducedMotion() ? "auto" : "smooth"');
  });
  it("shows keyboard focus on every kind of control, including the search pills and the details summary", () => {
    expect(css).toMatch(/button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible \{ outline: 2px solid/);
    expect(css).toMatch(/summary:focus-visible \{ outline: 2px solid/);
    expect(css).toMatch(/\.kb-search:focus-within \{ outline: 2px solid/);
    expect(css).toMatch(/\.kb-srow > button:focus-visible \{ outline: 2px solid/);
  });
  it("opens the review sheet as a modal dialog (native focus trap and Escape) with a name and a focus fallback", () => {
    expect(strategySrc).toContain("d.showModal?.()");
    expect(strategySrc).toContain('<dialog ref={ref} className="kb-sheet" aria-labelledby="rv-h" onClose={onClose}');
    expect(strategySrc).toContain('<h2 id="rv-h"');
    expect(strategySrc).toContain("fallbackFocus()");
    expect(strategySrc).not.toMatch(/\.show\(\)/);
  });
  it("gives both pages a focusable heading for the page switch, and labels every text input", () => {
    expect(strategySrc).toContain('<h1 className="kb-h1" tabIndex={-1}>Strategy</h1>');
    expect(fineTuneSrc).toContain('<h1 className="kb-h1" tabIndex={-1}>Fine-tune</h1>');
    expect(fineTuneSrc).toContain('<span className="sr-only">Add a stock to your focus</span>');
  });
});
