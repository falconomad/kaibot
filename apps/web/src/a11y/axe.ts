// Accessibility harness (#327): runs axe-core on rendered markup inside jsdom. Not a test file itself.
// The files that use it start with `// @vitest-environment jsdom`; every other test stays in the node environment.
import axe from "axe-core";

/** Rules left out, each with its reason. Anything not listed here runs.
 *  - color-contrast: jsdom has no layout or computed colours, so axe can only answer "cannot tell". Text contrast is
 *    measured from the tokens in contrast.test.ts, and in a real browser by scripts/mobile_check.py (axe, every page).
 *  - region: a page is rendered here without the shell that gives it its <main> and navigation landmarks; the shell is
 *    checked on its own with its landmarks in place. (Moderate impact, so it would not fail the check anyway.) */
export const EXCLUDED: Record<string, string> = {
  "color-contrast": "needs layout and computed colours (jsdom has neither); covered by contrast.test.ts and mobile_check.py",
  region: "pages are rendered without the shell's landmarks; the shell is checked on its own",
};

// jsdom has no canvas. axe draws text on one to tell an icon-font ligature from words (for the rule that compares a
// control's visible text with its accessible name); without it that rule stops with an error and reports nothing.
// The app has no icon font (icons are inline SVG), so a canvas that says "every text is plain words" is the truth here.
if (typeof HTMLCanvasElement !== "undefined") {
  HTMLCanvasElement.prototype.getContext = function getContext(this: HTMLCanvasElement) {
    return {
      canvas: this, font: "", textAlign: "left", textBaseline: "top",
      measureText: (s: string) => ({ width: 8 * s.length }),
      fillText() {}, clearRect() {},
      getImageData: (_x: number, _y: number, w: number, h: number) => ({ data: new Uint8ClampedArray(Math.max(1, w * h) * 4).fill(255) }),
    };
  } as unknown as HTMLCanvasElement["getContext"];
}

export interface Finding { id: string; impact: string; help: string; nodes: string[] }

/** Put `html` in the document and run axe on it. Returns the serious and critical findings (WCAG A and AA rules and
 *  axe's best practices). */
export async function audit(html: string): Promise<Finding[]> {
  document.body.innerHTML = `<main id="axe-root">${html}</main>`;
  return auditNode(document.getElementById("axe-root")!);
}

export async function auditNode(node: Element): Promise<Finding[]> {
  const r = await axe.run(node, {
    rules: Object.fromEntries(Object.keys(EXCLUDED).map((id) => [id, { enabled: false }])),
    resultTypes: ["violations"],
  });
  return r.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => ({ id: v.id, impact: v.impact ?? "", help: v.help, nodes: v.nodes.map((n) => n.html.slice(0, 200)) }));
}

/** For a readable failure: one line per finding. */
export const lines = (f: Finding[]) => f.map((x) => `${x.impact} ${x.id}: ${x.help}\n    ${x.nodes.join("\n    ")}`);
