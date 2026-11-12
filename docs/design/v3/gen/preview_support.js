/* Local stand-in for the design canvas runtime ("./support.js"), used only by preview.py to check boards with a
   headless browser before a PR. It is NOT the real viewer. It implements only what the v3 boards use:
   <x-dc>, <helmet>, <sc-if value>, <sc-for list as>, {{dotted.path}} bindings in text and attributes,
   React-style event attributes (onClick, onInput, onChange, onKeyDown, onMouseMove ...) and the DCLogic base class
   (props, state, setState, componentDidMount, componentWillUnmount, renderVals).
   Never copy this file into ../project: the canvas supplies its own support.js. */
(function () {
  'use strict';

  class DCLogic {
    constructor(props) { this.props = props || {}; this.state = {}; }
    setState(patch) {
      const p = typeof patch === 'function' ? patch(this.state, this.props) : patch;
      this.state = Object.assign({}, this.state, p);
      this.forceUpdate();
    }
    forceUpdate() {
      if (!this.__render || this.__queued) return;
      this.__queued = true;
      Promise.resolve().then(() => { this.__queued = false; this.__render(); });
    }
    componentDidMount() {}
    componentWillUnmount() {}
    renderVals() { return {}; }
  }
  window.DCLogic = DCLogic;

  const BIND = /\{\{\s*([^{}]+?)\s*\}\}/g;
  const WHOLE = /^\s*\{\{\s*([^{}]+?)\s*\}\}\s*$/;
  const EVENTS = { change: 'input', focus: 'focusin', blur: 'focusout', doubleclick: 'dblclick' };

  function lookup(path, scope) {
    if (path === 'true') return true;
    if (path === 'false') return false;
    if (path === 'null') return null;
    if (/^-?\d+(\.\d+)?$/.test(path)) return Number(path);
    let cur = scope;
    for (const key of path.split('.')) {
      if (cur === null || cur === undefined) return undefined;
      cur = cur[key];
    }
    return cur;
  }

  // bindings that found no value: the real runtime may print "undefined" for these, so preview.py reports them
  const missing = {};
  function miss(path) { missing[path] = (missing[path] || 0) + 1; }

  function fill(text, scope) {
    return text.replace(BIND, (_, path) => { const v = lookup(path, scope); if (v === undefined) miss(path); return v === null || v === undefined ? '' : String(v); });
  }

  function listen(el, handlers) {
    el.__h = handlers;
    el.__types = el.__types || {};
    Object.keys(handlers).forEach((type) => {
      if (el.__types[type]) return;
      el.__types[type] = true;
      el.addEventListener(type, (e) => { const f = el.__h && el.__h[type]; if (f) f(e); });
    });
  }

  // template node -> array of real nodes
  function build(t, scope, out) {
    if (t.nodeType === 3) { out.push(document.createTextNode(fill(t.data, scope))); return; }
    if (t.nodeType !== 1) return;
    const tag = t.localName;
    if (tag === 'helmet') return;
    if (tag === 'sc-if') {
      const m = (t.getAttribute('value') || '').match(WHOLE);
      const v = m ? lookup(m[1], scope) : undefined;
      if (m && v === undefined) miss(m[1]);
      if (v) t.childNodes.forEach((c) => build(c, scope, out));
      return;
    }
    if (tag === 'sc-for') {
      const m = (t.getAttribute('list') || '').match(WHOLE);
      if (m && lookup(m[1], scope) === undefined) miss(m[1]);
      const list = (m && lookup(m[1], scope)) || [];
      const name = t.getAttribute('as') || 'item';
      list.forEach((item) => { const s = Object.create(scope); s[name] = item; t.childNodes.forEach((c) => build(c, s, out)); });
      return;
    }
    const el = document.createElementNS(t.namespaceURI, tag);
    const handlers = {};
    for (const at of Array.from(t.attributes)) {
      const name = at.name;
      if (name.indexOf('hint-') === 0) continue;
      const whole = at.value.match(WHOLE);
      if (name.indexOf('on') === 0 && whole) {
        const f = lookup(whole[1], scope);
        const type = name.slice(2).toLowerCase();
        if (typeof f === 'function') handlers[EVENTS[type] || type] = f; else miss(whole[1]);
        continue;
      }
      let v;
      if (whole) {
        v = lookup(whole[1], scope);
        if (v === undefined) miss(whole[1]);
        if (v === null || v === undefined || v === false) continue;
        if (v === true) v = '';
      } else {
        v = fill(at.value, scope);
      }
      el.setAttribute(name, String(v));
      if (name === 'value' && (tag === 'input' || tag === 'textarea' || tag === 'select')) el.__value = String(v);
    }
    listen(el, handlers);
    const kids = [];
    t.childNodes.forEach((c) => build(c, scope, kids));
    kids.forEach((k) => el.appendChild(k));
    if ('__value' in el) el.value = el.__value;
    out.push(el);
  }

  // patch the live nodes in place so focus, scroll, transitions and one-shot animations behave as in a real app
  function morph(parent, fresh) {
    const old = Array.from(parent.childNodes);
    const n = Math.max(old.length, fresh.length);
    for (let i = 0; i < n; i++) {
      const a = old[i], b = fresh[i];
      if (!a) { parent.appendChild(b); continue; }
      if (!b) { parent.removeChild(a); continue; }
      if (a.nodeType !== b.nodeType || a.nodeName !== b.nodeName || a.namespaceURI !== b.namespaceURI) { parent.replaceChild(b, a); continue; }
      if (a.nodeType === 3) { if (a.data !== b.data) a.data = b.data; continue; }
      for (const at of Array.from(a.attributes)) if (!b.hasAttribute(at.name)) a.removeAttribute(at.name);
      for (const at of Array.from(b.attributes)) if (a.getAttribute(at.name) !== at.value) a.setAttribute(at.name, at.value);
      listen(a, b.__h || {});
      if ('__value' in b && a.value !== b.__value) a.value = b.__value;
      morph(a, Array.from(b.childNodes));
    }
  }

  function boot() {
    const root = document.querySelector('x-dc');
    const script = document.querySelector('script[data-dc-script]');
    if (!root || !script) { console.error('preview_support: no <x-dc> or data-dc-script on this page'); return; }
    const helmet = root.querySelector('helmet');
    if (helmet) { Array.from(helmet.children).forEach((c) => document.head.appendChild(c)); helmet.remove(); }

    const template = document.createDocumentFragment();
    while (root.firstChild) template.appendChild(root.firstChild);

    const props = {};
    let spec = {};
    try { spec = JSON.parse(script.getAttribute('data-props') || '{}'); } catch (e) { console.error('preview_support: bad data-props', e); }
    Object.keys(spec).forEach((k) => { if (k[0] !== '$' && spec[k] && typeof spec[k] === 'object') props[k] = spec[k].default; });
    Object.assign(props, window.__DC_PROPS__ || {});

    let Component;
    try { Component = new Function('DCLogic', script.textContent + '\nreturn Component;')(DCLogic); }
    catch (e) { console.error('preview_support: the board script does not compile', e); return; }
    const comp = new Component(props);
    if (!comp.state) comp.state = {};

    comp.__render = () => {
      let vals;
      try { vals = comp.renderVals() || {}; } catch (e) { console.error('preview_support: renderVals threw', e); return; }
      const fresh = [];
      template.childNodes.forEach((c) => build(c, vals, fresh));
      morph(root, fresh);
      window.__dc.renders += 1;
    };
    window.__dc = { comp, props, spec, missing, renders: 0, ready: false };
    comp.__render();
    try { comp.componentDidMount(); } catch (e) { console.error('preview_support: componentDidMount threw', e); }
    window.__dc.ready = true;
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
