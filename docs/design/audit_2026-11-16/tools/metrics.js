// Page measurements for the design audit (#226). Runs inside the page; returns plain JSON. Dev-only.
() => {
  // On a phone the layout viewport grows when content is too wide; the visual viewport is what the person sees.
  const vv = window.visualViewport, vw = Math.round(vv ? vv.width : window.innerWidth), vh = Math.round(vv ? vv.height : window.innerHeight), de = document.documentElement;
  const cs = (el) => getComputedStyle(el);
  const hidden = (el) => { for (let p = el; p && p !== document; p = p.parentElement) { const s = cs(p);
    if (s.display === "none" || s.visibility === "hidden" || parseFloat(s.opacity) === 0) return true; } return false; };
  const sr = (el) => !!el.closest(".sr-only, .skip");
  const vis = (el) => { if (sr(el) || hidden(el)) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const snip = (el) => (el.innerText || el.getAttribute("aria-label") || el.value || "").replace(/\s+/g, " ").trim().slice(0, 40);
  const sel = (el) => { const c = (el.getAttribute("class") || "").trim().split(/\s+/).filter(Boolean).slice(0, 2).join(".");
    return el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + (c ? "." + c : ""); };
  const desc = (el) => ({ sel: sel(el), text: snip(el) });
  const hScroller = (el) => { for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) { const s = cs(p);
    if ((s.overflowX === "auto" || s.overflowX === "scroll" || s.overflowX === "hidden") && p.scrollWidth > p.clientWidth + 1) return p; } return null; };
  const all = [...document.body.querySelectorAll("*")].filter((el) => !["SCRIPT", "STYLE", "NOSCRIPT"].includes(el.tagName));

  // 1. horizontal overflow
  const offenders = new Set();
  for (const el of all) { if (!vis(el)) continue; const r = el.getBoundingClientRect();
    if ((r.right > vw + 1 || r.left < -1) && !hScroller(el) && cs(el).position !== "fixed") offenders.add(el); }
  const offscreen = [...offenders].filter((el) => !offenders.has(el.parentElement)).map((el) => ({ ...desc(el), right: Math.round(el.getBoundingClientRect().right) }));
  const scrollers = [];
  for (const el of all) { if (!vis(el)) continue; const s = cs(el);
    if ((s.overflowX === "auto" || s.overflowX === "scroll") && el.scrollWidth > el.clientWidth + 1)
      scrollers.push({ ...desc(el), scrollWidth: el.scrollWidth, clientWidth: el.clientWidth }); }

  // 2. clipped text (hidden overflow with content that does not fit)
  const clipped = [];
  for (const el of all) { if (!vis(el)) continue; const s = cs(el);
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own) continue;
    const x = (s.overflowX === "hidden" || s.overflowX === "clip") && el.scrollWidth > el.clientWidth + 1;
    const y = (s.overflowY === "hidden" || s.overflowY === "clip") && el.scrollHeight > el.clientHeight + 2;
    if (x || y) clipped.push({ ...desc(el), axis: x ? "x" : "y", ellipsis: s.textOverflow === "ellipsis" }); }

  // 3. tap targets
  const TAP = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab], [role=radio], [role=switch], [role=checkbox], [tabindex]:not([tabindex="-1"])';
  const taps = [...new Set(document.querySelectorAll(TAP))].filter(vis);
  const small = [], tiny = []; let inline = 0;
  for (const el of taps) {
    let box = el;
    if (el.tagName === "INPUT" && ["checkbox", "radio"].includes(el.type) && el.closest("label")) box = el.closest("label");
    const r = box.getBoundingClientRect();
    if (r.width >= 44 && r.height >= 44) continue;
    const isInline = el.tagName === "A" && cs(el).display === "inline" && el.parentElement && el.parentElement.innerText.trim().length > snip(el).length + 12;
    if (isInline) { inline++; continue; }
    const row = { ...desc(el), w: Math.round(r.width), h: Math.round(r.height) };
    small.push(row); if (r.width < 24 || r.height < 24) tiny.push(row);
  }

  // 4. text: small fonts, words above the fold
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const smallFont = {}; let smallWords = 0, foldWords = 0, pageWords = 0, foldNums = 0, pageNums = 0; const foldText = [];
  const wc = (t) => (t.match(/[A-Za-z0-9$%−+.,:'’/-]*[A-Za-z0-9][A-Za-z0-9$%−+.,:'’/-]*/g) || []);
  const range = document.createRange();
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = n.textContent.replace(/\s+/g, " ").trim(); const p = n.parentElement;
    if (!t || !p || ["SCRIPT", "STYLE", "OPTION", "TITLE"].includes(p.tagName) || !vis(p)) continue;
    range.selectNodeContents(n); const r = range.getBoundingClientRect(); if (r.width === 0 || r.height === 0) continue;
    if (hScrollerClipped(n, r)) continue;
    const words = wc(t), nums = words.filter((w) => /\d/.test(w));
    pageWords += words.length; pageNums += nums.length;
    if (r.top < vh && r.bottom > 0 && r.left < vw) { foldWords += words.length; foldNums += nums.length; foldText.push(t); }
    const fs = parseFloat(cs(p).fontSize);
    if (fs < 12) { const k = fs.toFixed(1); smallFont[k] = smallFont[k] || { nodes: 0, words: 0, sample: [] }; smallFont[k].nodes++; smallFont[k].words += words.length; smallWords += words.length;
      if (smallFont[k].sample.length < 4) smallFont[k].sample.push(t.slice(0, 40)); }
  }
  function hScrollerClipped(n, r) { return r.left >= vw; }

  // 5. tables
  const tables = [...document.querySelectorAll("table")].filter(vis).map((t) => { const r = t.getBoundingClientRect();
    const head = t.querySelectorAll("thead th"); const shown = [...head].filter(vis).length;
    return { ...desc(t), width: Math.round(r.width), scrollWidth: t.scrollWidth, clientWidth: t.clientWidth, cols: head.length, colsShown: shown,
      rows: t.querySelectorAll("tbody tr").length, wider: t.scrollWidth > vw || r.width > vw }; });

  const fixedBottom = [...document.querySelectorAll("nav.tabbar")].filter(vis).map((n) => ({ items: n.querySelectorAll("a").length, h: Math.round(n.getBoundingClientRect().height),
    itemW: Math.round(Math.min(...[...n.querySelectorAll("a")].map((a) => a.getBoundingClientRect().width))) }))[0] || null;
  const header = document.querySelector("header.top");
  window.scrollTo(300, 0); const pageScrollsX = window.scrollX; window.scrollTo(0, 0);
  return {
    vw, vh, docScrollWidth: de.scrollWidth, docClientWidth: de.clientWidth, bodyScrollWidth: document.body.scrollWidth,
    layoutWidth: window.innerWidth, docOverflow: Math.max(de.scrollWidth, window.innerWidth) > vw + 1, pageScrollsX, pageHeight: de.scrollHeight, screens: +(de.scrollHeight / vh).toFixed(1),
    offscreen, scrollers, clipped, tapTotal: taps.length, tapSmall: small, tapTiny: tiny, tapInlineLinks: inline,
    smallFont, smallWords, foldWords, foldNums, pageWords, pageNums, foldText: foldText.join(" | ").slice(0, 1500),
    tables, cards: document.querySelectorAll(".card").length, tabbar: fixedBottom,
    headerH: header && vis(header) ? Math.round(header.getBoundingClientRect().height) : 0,
    animations: document.getAnimations().length, cls: +(window.__cls || 0).toFixed(4), shifts: window.__shifts || 0,
  };
}
