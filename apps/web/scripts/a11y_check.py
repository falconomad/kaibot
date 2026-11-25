"""Accessibility check in a real browser for the screens added since brand v1 (#327). Dev-only, like mobile_check.py.

What vitest cannot see in jsdom (no Tab key, no layout, no colours) is measured here in Chromium on a 390 px phone,
against the design audit's mock (no production data, account or key):

- every page: no uncaught error and at least mobile_check.MIN_WORDS words (a blank page passes axe); axe-core, all its WCAG A/AA and best-practice rules with text contrast as drawn; no serious or critical
  finding;
- focus groups (Strategy): reached with the Tab key alone, a 2 px focus ring that nothing clips, Enter opens the sheet
  with focus inside, Tab stays inside, Escape closes it and focus returns; the card says open or closed
  (aria-expanded); Space on the round button adds the group (aria-pressed); a stock you never trade keeps its contrast;
- My lists: a ticker typed by keyboard is said as "Checking ..." and then its answer in the polite live region; a
  refused ticker and one no checker answered are chips with Retry and Remove of at least 44 px;
- the stock summary card behind an accepted ticker: Enter opens it, "Loading ..." then the answer is said, Escape
  returns focus;
- every control on those screens and in their sheets is at least 44 by 44 px.

    cd apps/web && VITE_SUPABASE_URL=http://localhost:54321 VITE_SUPABASE_PUBLISHABLE_KEY=mock npx vite build --outDir /tmp/kb-dist-a11y --emptyOutDir
    python3 scripts/a11y_check.py --dist /tmp/kb-dist-a11y
"""
# ruff: noqa: E501  (the browser scripts in this file are JavaScript inside strings; a few lines run past 120)
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import mobile_check  # noqa: E402  (the mock's request handler, and the design audit's tools it reuses)

audit, mock_data = mobile_check.audit, mobile_check.mock_data

AXE = mobile_check.AXE
# Every rule axe runs by default; serious and critical findings only (the issue's bar).
AXE_ALL = """async () => {
  const r = await axe.run(document, { resultTypes: ['violations'] });
  return r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .flatMap((v) => v.nodes.map((n) => `${v.id}: ${n.target.join(' ')} ${(n.any[0] || n.all[0] || n.none[0] || {}).message || v.help}`));
}"""
# Controls under 44 x 44 px inside `root` (a link inside a sentence is text, not a target).
SMALL = """(root) => {
  const TAP = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=radio], [role=switch], [tabindex]:not([tabindex="-1"])';
  const out = [];
  for (const el of document.querySelector(root).querySelectorAll(TAP)) {
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    if (!r.width || !r.height || cs.visibility === 'hidden' || el.closest('.sr-only')) continue;
    if (el.tagName === 'A' && cs.display === 'inline') continue;
    if (r.width < 43.5 || r.height < 43.5) out.push(`${el.className || el.tagName} "${(el.getAttribute('aria-label') || el.innerText || '').trim().slice(0, 30)}" ${Math.round(r.width)}x${Math.round(r.height)}`);
  }
  return out;
}"""
# The focus ring of the focused element, as drawn: its style, and whether an ancestor that hides overflow cuts it off.
RING = """() => {
  const el = document.activeElement, cs = getComputedStyle(el), r = el.getBoundingClientRect();
  const reach = Math.max(0, parseFloat(cs.outlineWidth) + parseFloat(cs.outlineOffset));   // how far outside the box it is drawn
  let clipped = null;
  for (let p = el.parentElement; p && p !== document.documentElement; p = p.parentElement) {
    const o = getComputedStyle(p);
    if (o.overflowX === 'visible' && o.overflowY === 'visible') continue;
    const b = p.getBoundingClientRect();
    if (r.left - reach < b.left - 0.5 || r.right + reach > b.right + 0.5 || r.top - reach < b.top - 0.5 || r.bottom + reach > b.bottom + 0.5) { clipped = p.className || p.tagName; break; }
  }
  return { visible: el.matches(':focus-visible'), style: cs.outlineStyle, width: cs.outlineWidth, color: cs.outlineColor, clipped };
}"""
FOCUS = "(sel) => !!document.activeElement && document.activeElement.matches(sel)"
SAID = "() => [...document.querySelectorAll('[role=status], [aria-live=polite]')].map((e) => e.textContent.replace(/\\s+/g, ' ').trim()).filter(Boolean)"

REFUSED = "We could not find ZZZT at the broker. Check the spelling."


class Mock(mock_data.Mock):
    """The audit's mock, with one stock the owner never trades, a ticker the broker refuses and one no checker answers."""

    def rpc(self, fn: str, a: dict):
        r = super().rpc(fn, a)
        if fn == "kb_get_config":
            never = sorted(next(iter(r["themes"].values()))["symbols"])[0] if r.get("themes") else None
            r["draft"] = {"updated_at": mock_data.iso(self.now), "body": {"focus": {"themes": [], "symbols": [], "exclude": [never] if never else [], "mode": "all"}}}
        if fn == "kb_check_ticker" and a.get("p_symbol") == "ZZZT":
            return {"symbol": "ZZZT", "status": "refused", "reason": REFUSED, "etf": None, "in_catalogue": False, "name": None}
        if fn == "kb_check_ticker" and a.get("p_symbol") == "SLOW":
            return {"symbol": "SLOW", "status": "pending", "reason": None, "etf": None, "in_catalogue": False, "name": None}
        return r


def summary(symbol: str, day: str) -> dict:
    return {"summary": {"symbol": symbol, "day": day, "ai": True, "sources": [{"name": "Finnhub profile", "as_of": day}],
                        "body": {"about": f"{symbol} is a US company listed on NASDAQ (invented for this check).",
                                 "news": [{"headline": "An invented headline for the check", "gist": None, "date": day, "source": "example"}], "news_status": "ok",
                                 "earnings": {"date": day, "hour": "amc"}, "earnings_status": "ok", "actions": [], "actions_status": "ok"}}}


def handler(dist: pathlib.Path, now: dt.datetime, held: list):
    """The mock's handler; a request for a stock summary is held (in `held`) until the check has read "Loading ..."."""
    base = mobile_check.serve(Mock("running"), dist, now)

    def handle(route):
        if route.request.url.startswith(audit.APP + "/api/summary"):
            held.append(route)
            return None
        return base(route)
    return handle


def answer(held: list, now: dt.datetime) -> None:
    while held:
        route = held.pop()
        symbol = route.request.url.split("symbol=")[1].split("&")[0]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(summary(symbol, str(now.date()))))


def tab_to(pg, sel: str, limit: int = 150) -> bool:
    for _ in range(limit):
        pg.keyboard.press("Tab")
        if pg.evaluate(FOCUS, sel):
            return True
    return False


def ring_ok(pg, what: str, fails: list[str]) -> None:
    ring = pg.evaluate(RING)
    print(f"focus ring on {what}: {ring}")
    if not ring["visible"] or ring["style"] != "solid" or ring["width"] != "2px" or ring["clipped"]:
        fails.append(f"{what}: no visible 2 px focus ring: {ring}")


def axe_ok(pg, what: str, fails: list[str]) -> None:
    pg.evaluate(AXE)
    found = pg.evaluate(AXE_ALL)
    print(f"axe on {what}: {len(found)} serious or critical")
    if found:
        fails.append(f"{what}: axe found {len(found)}: {found[:4]}")


def targets_ok(pg, root: str, what: str, fails: list[str]) -> None:
    small = pg.evaluate(SMALL, root)
    print(f"targets under 44 px in {what}: {small}")
    if small:
        fails.append(f"{what}: {len(small)} controls under 44 px: {small[:4]}")


def sheet_by_keyboard(pg, opener_sel: str, what: str, fails: list[str], during=None) -> None:
    """The focused opener: Enter opens a dialog with focus inside, Tab stays inside, Escape closes, focus returns."""
    state = lambda: pg.evaluate("(s) => document.querySelector(s + '[data-a11y-opener]')?.getAttribute('aria-expanded')", opener_sel)  # noqa: E731
    pg.evaluate("() => document.activeElement.setAttribute('data-a11y-opener', '')")
    before = state()
    pg.keyboard.press("Enter")
    pg.wait_for_selector("[role=dialog]")
    pg.wait_for_timeout(1000)   # the sheet's slide and the rows' rise are over
    inside = pg.evaluate("() => document.querySelector('[role=dialog]').contains(document.activeElement)")
    opened = state()
    if during:
        during()
    stays = True
    for _ in range(6):
        pg.keyboard.press("Tab")
        stays = stays and pg.evaluate("() => document.querySelector('[role=dialog]').contains(document.activeElement)")
    axe_ok(pg, f"{what} (open)", fails)
    targets_ok(pg, "[role=dialog]", f"{what} (open)", fails)
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(500)
    closed = pg.evaluate("() => !document.querySelector('[role=dialog]')")
    back = pg.evaluate("() => document.activeElement.hasAttribute('data-a11y-opener')")
    after = state()
    print(f"{what}: aria-expanded {before} -> {opened} -> {after} | focus inside {inside} | Tab stays inside {stays} | Escape closes {closed} | focus returns {back}")
    if [before, opened, after] != ["false", "true", "false"] or not (inside and stays and closed and back):
        fails.append(f"{what}: keyboard or aria-expanded: {[before, opened, after]} inside={inside} stays={stays} closed={closed} back={back}")
    pg.evaluate("() => document.querySelector('[data-a11y-opener]')?.removeAttribute('data-a11y-opener')")


def main(a) -> int:
    dist = pathlib.Path(a.dist).resolve()
    now = dt.datetime.now(dt.UTC)
    session = "localStorage.setItem('sb-localhost-auth-token', JSON.stringify(" + json.dumps(mock_data.session_blob(now)) + "));"
    fails: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        held: list = []
        threw: list[str] = []

        def open_page(path: str, signed_in: bool = True):
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, color_scheme="dark",
                                      service_workers="block")  # #481: no app-shell store in this check
            if signed_in:
                ctx.add_init_script(session)
            ctx.route("**/*", handler(dist, now, held))
            pg = ctx.new_page()
            mobile_check.watch_errors(pg, threw)  # every uncaught error on every page of this check
            pg.goto(audit.APP + path, wait_until="load")
            pg.wait_for_selector("main > *, .center", timeout=8000)
            pg.wait_for_timeout(1500)
            return ctx, pg

        # 1. Every page, as drawn, with the mock's data.
        # A page that threw or shows under mobile_check.MIN_WORDS words fails here (a blank page has no axe finding).
        for name, path, act in audit.ROUTES:
            before = len(threw)
            ctx, pg = open_page(path, signed_in=act != "signed-out")
            if act == "lists":
                pg.get_by_role("radio", name="My lists").click()
                pg.wait_for_timeout(700)
            mobile_check.alive(pg, name, threw[before:], fails)
            del threw[before:]  # reported with the page's name
            axe_ok(pg, name, fails)
            ctx.close()

        # 2. Focus groups, by keyboard alone.
        ctx, pg = open_page("/strategy")
        if not tab_to(pg, ".kb-tcard-open"):
            fails.append("focus groups: the Tab key never reaches a group's card")
        ring_ok(pg, "a focus group's card", fails)
        never = pg.evaluate("() => null")

        def never_traded():
            nonlocal never
            never = pg.evaluate("() => [...document.querySelectorAll('.kb-peek li.off')].map((li) => li.innerText.replace(/\\s+/g, ' ').trim())")
        sheet_by_keyboard(pg, ".kb-tcard-open", "the All stocks group", fails, during=never_traded)
        # The mock's never-traded stock is in the first theme; open that one too, so the dimmed row is on screen for axe.
        pg.keyboard.press("Tab")
        if not pg.evaluate(FOCUS, ".kb-tcheck"):
            fails.append("focus groups: the round add button does not follow its card in the Tab order")
        ring_ok(pg, "the round add button", fails)
        if not tab_to(pg, ".kb-tcard-open", 3):
            fails.append("focus groups: the next group's card is not next in the Tab order")
        sheet_by_keyboard(pg, ".kb-tcard-open", "the first theme", fails, during=never_traded)
        print(f"never-traded rows in the opened group: {never}")
        if not never or "never traded" not in never[0]:
            fails.append(f"focus groups: the never-traded stock is not shown in its group: {never}")
        pg.keyboard.press("Tab")
        pressed = [pg.evaluate("() => document.activeElement.getAttribute('aria-pressed')")]
        pg.keyboard.press("Space")
        pg.wait_for_timeout(300)
        pressed.append(pg.evaluate("() => document.activeElement.getAttribute('aria-pressed')"))
        print(f"Space on the round add button: aria-pressed {pressed}")
        if pressed != ["false", "true"]:
            fails.append(f"focus groups: Space on the round button does not add the group: {pressed}")
        targets_ok(pg, "main", "Strategy", fails)
        ctx.close()

        # 3. My lists: the checking state and its answers, by keyboard.
        ctx, pg = open_page("/strategy")
        pg.get_by_role("radio", name="My lists").focus()
        pg.keyboard.press("Space")
        pg.wait_for_timeout(700)
        # A saved list, picked by keyboard: its card is in a sideways scroller on a phone, which must not cut the ring.
        # (Home moves to the first list and picks it, as in any radio group.)
        pg.get_by_role("radio", name="New list").focus()
        pg.keyboard.press("Home")
        pg.wait_for_timeout(700)
        if not pg.evaluate(FOCUS, '.kb-lcard[aria-checked="true"]'):
            fails.append("My lists: the Home key did not pick the first saved list")
        ring_ok(pg, "a saved list's card", fails)
        pg.locator("#ml-add").focus()
        heard: list[str] = []
        pg.keyboard.type("ZZZT,")
        pg.wait_for_timeout(1200)
        heard.append(pg.evaluate("() => document.querySelector('#ml-said').textContent"))
        pg.keyboard.type("SLOW,")
        pg.wait_for_timeout(400)
        heard.append(pg.evaluate("() => document.querySelector('#ml-said').textContent"))
        pg.wait_for_timeout(11_500)   # about 10 s with no answer: "waiting for the checker"
        heard.append(pg.evaluate("() => document.querySelector('#ml-said').textContent"))
        print(f"said while checking: {heard}")
        if heard != [f"ZZZT refused: {REFUSED}", "Checking SLOW.", "SLOW: no checker has answered yet. Try again in a minute."]:
            fails.append(f"My lists: the live region did not say the check and its answers: {heard}")
        live = pg.evaluate("() => { const e = document.querySelector('#ml-said'); return [e.getAttribute('role'), getComputedStyle(e).position, Math.round(e.getBoundingClientRect().width)]; }")
        if live != ["status", "absolute", 1]:
            fails.append(f"My lists: the live region is not a visually hidden polite status: {live}")
        chips = pg.evaluate("() => [...document.querySelectorAll('.kb-chips > li')].map((li) => li.className.replace('kb-chip ', ''))")
        print(f"chips: {chips}")
        if "no" not in chips or "slow" not in chips or "ok" not in chips:
            fails.append(f"My lists: expected accepted, refused and waiting chips: {chips}")
        axe_ok(pg, "My lists with refused and waiting tickers", fails)
        targets_ok(pg, ".kb-ml", "My lists", fails)
        # Retry, by keyboard: the waiting chip goes back to checking.
        pg.evaluate("() => document.querySelector('#ml-add').focus()")
        if not tab_to(pg, ".kb-chip.slow .linkbtn", 60):
            fails.append("My lists: the Tab key never reaches Retry")
        ring_ok(pg, "Retry on a waiting chip", fails)
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(400)
        again = pg.evaluate("() => document.querySelector('#ml-said').textContent")
        print(f"after Retry: {again!r}")
        if again != "Checking SLOW.":
            fails.append(f"My lists: Retry by keyboard did not start the check again: {again!r}")

        # 4. The stock summary card behind an accepted ticker.
        pg.evaluate("() => document.querySelector('#ml-add').focus()")
        if not tab_to(pg, ".kb-chip-info", 20):
            fails.append("stock summary: the Tab key never reaches an accepted ticker")
        ring_ok(pg, "an accepted ticker", fails)
        said: list = []

        def card():
            said.append(pg.evaluate("() => document.querySelector('[role=dialog] .ss [role=status]')?.textContent"))
            answer(held, now)
            pg.wait_for_timeout(800)
            said.append(pg.evaluate("() => document.querySelector('[role=dialog] .ss [role=status]')?.textContent"))
            said.append(pg.evaluate("() => document.querySelector('[role=dialog] .ss')?.getAttribute('aria-busy')"))
        sheet_by_keyboard(pg, ".kb-chip-info", "the stock summary sheet", fails, during=card)
        print(f"said by the summary card: {said}")
        if len(said) != 3 or not str(said[0]).startswith("Loading the summary for ") or not str(said[1]).endswith(" is shown.") or said[2] != "false":
            fails.append(f"stock summary: loading and the answer were not said: {said}")
        ctx.close()
        browser.close()
    for e in dict.fromkeys(threw):  # thrown in a later step (focus groups, My lists, the stock summary)
        fails.append(f"a page threw: {e}")
    print("\n".join("FAIL " + f for f in fails) or "PASS: axe clean on every page; focus groups, My lists and the stock summary work by keyboard; targets 44 px; no page threw or was blank")
    return 1 if fails else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", required=True)
    sys.exit(main(ap.parse_args()))
