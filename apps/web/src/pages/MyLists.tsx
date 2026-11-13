// M5 S69 (#97): 'My lists' in the Strategy page's Focus card, built to the approved board 10 / 10m (design canvas,
// docs/design/v3/gen/mylists.py). The owner types or pastes any US tickers; each one is checked by kb_check_ticker and
// shows accepted or refused with the server's reason. Only accepted tickers are written into the draft
// (lib/tickers.withList); refused, unchecked and still-checking ones never are, so they can never be applied.
// Server text (reasons, names) is always rendered as plain text.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { RadioGroup } from "../components/ConfigControls";
import { StockBadge, companyName } from "../components/StockBadge";
import { api } from "../lib/api";
import type { ConfigBody } from "../lib/config";
import { estShort, type Estimate } from "../lib/estimator";
import {
  DB_UPDATE, MAX_NAME, MAX_TICKERS, MIN_ONLY, NOT_A_TICKER, POLL_LIMIT_MS, POLL_MS, addable, isMissingFunction, itemFromCheck,
  itemFromSaved, itemsFromBody, listWords, looksLikeTicker, needsCheck, onlyShort, parseTickers, rpcErrorText, saveBlocker, tally, withList,
  type Item, type SavedList,
} from "../lib/tickers";
import { LockIcon } from "./StrategyShared";

const NEW = "\u0000new";
/** Set once kb_delete_ticker_list answered "does not exist" (database update not applied yet); kept for the session. */
let deleteMissing = false;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export interface ListInfo { checking: number; refused: string[] }
export type ListEditor = ReturnType<typeof useListEditor>;

/** All of the list editor's state. Lives in StrategyHome so the Focus card and the side card read the same list. */
export function useListEditor(body: ConfigBody, edit: (b: ConfigBody) => void, universe: string[], report: (i: ListInfo) => void, active = true) {
  const qc = useQueryClient();
  const lists = useQuery({ queryKey: ["tickerLists"], queryFn: api.tickerLists, enabled: active, staleTime: 60_000, refetchOnWindowFocus: false,
    retry: (n, e) => !isMissingFunction(e) && n < 1 });
  const [sel, setSel] = useState<string>(NEW);
  const [name, setName] = useState(body.focus.custom?.name ?? "");
  const [items, setItems] = useState<Item[]>(() => itemsFromBody(body));
  const [draft, setDraft] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [save, setSave] = useState<{ kind: "idle" | "saving" | "saved" } | { kind: "error"; text: string }>({ kind: "idle" });
  const [del, setDel] = useState<{ kind: "idle" | "confirm" | "busy" } | { kind: "error"; text: string }>({ kind: "idle" });
  const [, force] = useState(0);
  const touched = useRef(false);
  const alive = useRef(true);
  const started = useRef(new Set<string>());
  const bodyRef = useRef(body);
  bodyRef.current = body;
  useEffect(() => () => { alive.current = false; }, []);

  // A draft that already carries a list: select the saved list of the same name once the lists arrive.
  useEffect(() => {
    if (touched.current || sel !== NEW || !name || !lists.data) return;
    if (lists.data.lists.some((l) => l.name === name)) setSel(name);
  }, [lists.data, name, sel]);

  const settle = (symbol: string, next: Item) =>
    alive.current && setItems((xs) => xs.map((x) => (x.symbol === symbol && (x.state === "checking" || x.state === "slow") ? next : x)));

  // Check every ticker that needs it: ask again every 2 s while pending, for at most 30 s, then say "still checking".
  async function check(symbol: string) {
    const t0 = Date.now();
    try {
      for (;;) {
        const c = await api.checkTicker(symbol);
        if (!alive.current) return;
        if (c?.status !== "pending") { settle(symbol, itemFromCheck({ ...c, symbol })); return; }
        if (Date.now() - t0 >= POLL_LIMIT_MS) { settle(symbol, { symbol, state: "slow" }); return; }
        await sleep(POLL_MS);
      }
    } catch (e) {
      settle(symbol, { symbol, state: "error", reason: isMissingFunction(e) ? DB_UPDATE : rpcErrorText(e) });
    } finally { started.current.delete(symbol); }
  }
  useEffect(() => {
    for (const s of needsCheck(items)) if (!started.current.has(s)) { started.current.add(s); void check(s); }
  }, [items]); // eslint-disable-line react-hooks/exhaustive-deps

  // Write the accepted tickers into the draft whenever the owner changed the list (never on a mere visit).
  const wrote = useRef(false);
  useEffect(() => {
    if (!touched.current) return;
    const t = tally(items);
    if (!t.accepted.length && !wrote.current) return;
    wrote.current = true;
    edit(withList(bodyRef.current, items, name, universe));
  }, [items, name]); // eslint-disable-line react-hooks/exhaustive-deps

  const t = tally(items);
  useEffect(() => { report({ checking: t.checking.length, refused: t.refused.map((i) => i.symbol) }); },
    [t.checking.length, t.refused.map((i) => i.symbol).join()]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => report({ checking: 0, refused: [] }), []); // eslint-disable-line react-hooks/exhaustive-deps

  const change = (fn: () => void) => { touched.current = true; setSave({ kind: "idle" }); setDel({ kind: "idle" }); fn(); };
  function add(raw: string) {
    const { add: fresh, overflow } = addable(items.map((i) => i.symbol), parseTickers(raw));
    setMsg(overflow ? `${overflow} left out: a list holds at most ${MAX_TICKERS} tickers.` : null);
    if (!fresh.length) return;
    change(() => setItems((xs) => [...xs, ...fresh.map((s): Item => (looksLikeTicker(s) ? { symbol: s, state: "checking" } : { symbol: s, state: "refused", reason: NOT_A_TICKER }))]));
  }
  const saved = lists.data?.lists ?? [];
  const cur = saved.find((l) => l.name === sel) ?? null;
  const okSyms = t.accepted.map((i) => i.symbol);
  const same = !!cur && cur.name === name.trim() && !t.refused.length && !t.checking.length
    && [...cur.symbols.map((s) => (typeof s === "string" ? s : s.symbol))].sort().join() === [...okSyms].sort().join();

  return {
    lists, saved, sel, cur, name, items, draft, msg, save, del, t, same, isNew: sel === NEW,
    deleteMissing: () => deleteMissing,
    pick(l: SavedList | null) {
      change(() => {
        started.current.clear();
        setSel(l ? l.name : NEW); setName(l ? l.name : ""); setDraft(""); setMsg(null);
        setItems(l ? l.symbols.map(itemFromSaved) : []);
        if (!l) wrote.current = false;
      });
    },
    setName: (v: string) => change(() => setName(v.slice(0, MAX_NAME))),
    setDraft(v: string) {
      // Typing a separator (or pasting a list) adds what came before it; the unfinished last word stays in the box.
      if (!/[\s,;|]/.test(v)) { setDraft(v); return; }
      const parts = v.split(/[\s,;|]+/);
      const tail = /[\s,;|]$/.test(v) ? "" : parts.pop() ?? "";
      add(parts.join(" "));
      setDraft(tail);
    },
    commit() { add(draft); setDraft(""); },
    drop: (symbol: string) => change(() => setItems((xs) => xs.filter((x) => x.symbol !== symbol))),
    retry: (symbol: string) => change(() => setItems((xs) => xs.map((x) => (x.symbol === symbol ? { symbol, state: "checking" } : x)))),
    async saveList() {
      if (saveBlocker(name, items)) return;
      const nm = name.trim();
      setSave({ kind: "saving" });
      try {
        await api.saveTickerList(nm, okSyms);
        if (!alive.current) return;
        touched.current = true;
        setSel(nm); setName(nm);
        setItems((xs) => xs.filter((x) => x.state === "accepted"));
        setSave({ kind: "saved" });
        await qc.invalidateQueries({ queryKey: ["tickerLists"] });
      } catch (e) { if (alive.current) setSave({ kind: "error", text: rpcErrorText(e) }); }
    },
    askDelete: () => setDel({ kind: "confirm" }),
    cancelDelete: () => setDel({ kind: "idle" }),
    async deleteList() {
      if (!cur) return;
      setDel({ kind: "busy" });
      try {
        await api.deleteTickerList(cur.name);
        if (!alive.current) return;
        setDel({ kind: "idle" });
        await qc.invalidateQueries({ queryKey: ["tickerLists"] });
        setSel(NEW);
      } catch (e) {
        if (isMissingFunction(e)) { deleteMissing = true; setDel({ kind: "idle" }); force((n) => n + 1); }
        else if (alive.current) setDel({ kind: "error", text: rpcErrorText(e) });
      }
    },
  };
}

const XIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden="true"><path d="M6 6 L18 18 M18 6 L6 18" strokeLinecap="round" /></svg>
);
const WarnIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--warn)" strokeWidth="2" aria-hidden="true" style={{ flex: "none", marginTop: 2 }}><path d="M12 4 L21 20 H3 Z" strokeLinejoin="round" /><path d="M12 10 V14 M12 17 V17.5" strokeLinecap="round" /></svg>
);
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

/** The 'My lists' card (board 10, inside Focus). */
export function MyListsCard({ ed, mode }: { ed: ListEditor; mode: ConfigBody["focus"]["mode"] }) {
  const { t } = ed;
  const short = onlyShort(ed.items, mode);
  const blocker = saveBlocker(ed.name, ed.items);
  const canSave = !blocker && !ed.same && ed.save.kind !== "saving";
  const listsMissing = ed.lists.isError && isMissingFunction(ed.lists.error);
  const saveNote = ed.save.kind === "error" ? ed.save.text
    : ed.save.kind === "saved" ? (ed.same ? "Saved. Refused tickers were left out." : "Saved.")
    : ed.same ? "Saved lists stay here for next time."
    : blocker ?? (t.refused.length ? `Saves the ${t.accepted.length} accepted. Refused tickers are left out.` : `Saves ${plural(t.accepted.length, "ticker")}.`);
  const outside = mode === "prefer" && t.fresh.length > 0;
  return (
    <section aria-labelledby="ml-h" className="kb-ml">
      <div className="kb-ml-head">
        <h3 id="ml-h" className="kb-ml-title">My lists</h3>
        <span className={`num ${t.full ? "kb-warn" : "muted"}`} aria-live="polite" style={{ fontSize: 13, fontWeight: 600 }}>{t.total} of {MAX_TICKERS}</span>
        <span className="muted kb-ml-sub">Your own tickers. Type any US stock and Kaibot checks it before it can be traded.</span>
      </div>

      {ed.lists.isPending ? (
        <div className="kb-lgrid" role="status" aria-busy="true" aria-label="Loading your lists">
          {[0, 1].map((i) => <span key={i} className="sk" style={{ height: 68, borderRadius: 16 }} />)}
        </div>
      ) : (
        <RadioGroup label="Saved lists" className="kb-lgrid" optClass="kb-lcard" value={ed.sel}
          onChange={(v) => { if (v !== ed.sel) ed.pick(ed.saved.find((l) => l.name === v) ?? null); }}
          options={[...ed.saved.map((l) => ({ value: l.name, label: l.name, description: plural(l.symbols.length, "ticker"),
            content: <LCard name={l.name} meta={plural(l.symbols.length, "ticker")} /> })),
          { value: NEW, label: "New list", description: ed.isNew ? (t.total ? "Not saved yet" : "Empty") : "Start from scratch",
            content: <LCard name="New list" meta={ed.isNew ? (t.total ? "Not saved yet" : "Empty") : "Start from scratch"} /> }]} />
      )}
      {listsMissing && <span className="muted" style={{ fontSize: 13 }}>Saved lists need a database update that isn't applied yet. You can still check tickers and use them.</span>}
      {ed.lists.isError && !listsMissing && <span className="loss" role="alert" style={{ fontSize: 13 }}>Couldn't load your saved lists: {rpcErrorText(ed.lists.error)}</span>}

      <div className="kb-ml-field">
        <label htmlFor="ml-name" className="kb-ml-label">List name</label>
        <span className="kb-search"><input id="ml-name" type="text" value={ed.name} maxLength={MAX_NAME} placeholder="e.g. AI infrastructure" autoComplete="off"
          onChange={(e) => ed.setName(e.target.value)} /></span>
      </div>

      <div className="kb-ml-field">
        <label htmlFor="ml-add" className="kb-ml-label">Add tickers</label>
        <div className="kb-row kb-ml-addrow">
          <span className="kb-search"><input id="ml-add" type="text" value={ed.draft} disabled={t.full} autoCapitalize="characters" autoComplete="off" spellCheck={false}
            aria-describedby="ml-help" placeholder={t.full ? `Full: ${MAX_TICKERS} tickers` : "Type or paste: NBIS, CRWV, FSLY"} style={{ textTransform: "uppercase" }}
            onChange={(e) => ed.setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); ed.commit(); } }} /></span>
          <button type="button" onClick={ed.commit} disabled={t.full || !ed.draft.trim()}>Add</button>
        </div>
        <span id="ml-help" className={t.full || ed.msg ? "kb-warn" : "muted"} style={{ fontSize: 12 }} role="status">
          {t.full ? `This list is full: ${MAX_TICKERS} tickers is the most Kaibot can scan in time. Remove one to add another.`
            : ed.msg ?? "Separate with commas or spaces, or paste a whole list. Each ticker is checked as you add it."}
        </span>
      </div>

      {ed.items.length > 0 ? (
        <ul className="kb-chips" aria-label="Tickers in this list">
          {ed.items.map((i) => <Chip key={i.symbol} item={i} onDrop={() => ed.drop(i.symbol)} onRetry={() => ed.retry(i.symbol)} />)}
        </ul>
      ) : <span className="muted kb-ml-empty">No tickers yet. Add at least {MIN_ONLY} to trade only this list.</span>}
      <span className="num muted" aria-live="polite" style={{ fontSize: 13 }}>
        {!t.total ? "Nothing added yet" : `${t.accepted.length} accepted${t.refused.length ? ` · ${t.refused.length} refused` : ""}${t.checking.length ? ` · ${t.checking.length} checking` : ""}`}
      </span>
      {short > 0 && (
        <div className="kb-ml-warn swap" role="alert"><WarnIcon /><span>“Only these” needs at least {MIN_ONLY} accepted tickers. This list has {t.accepted.length}. Add {short} more.</span></div>
      )}
      {outside && (
        <div className="kb-ml-warn swap" role="status"><WarnIcon /><span>“Prefer these” keeps all 59 catalogue stocks, so {listWords(t.fresh.map((i) => i.symbol))} {t.fresh.length === 1 ? "is" : "are"} left out. Tickers new to Kaibot are traded only with “Only these”.</span></div>
      )}

      <div className="kb-row">
        <button type="button" className={canSave ? "primary" : undefined} disabled={!canSave} onClick={() => void ed.saveList()}>
          {ed.save.kind === "saving" ? "Saving…" : ed.same ? "Saved" : ed.cur ? "Save changes" : "Save list"}
        </button>
        <span className={`swap ${ed.save.kind === "error" ? "loss" : "muted"}`} style={{ flex: "1 1 200px", fontSize: 13 }} role={ed.save.kind === "error" ? "alert" : undefined}>{saveNote}</span>
        {ed.cur && !ed.deleteMissing() && ed.del.kind !== "confirm" && ed.del.kind !== "busy" && (
          <button type="button" className="linkbtn" style={{ fontSize: 13 }} onClick={ed.askDelete}>Delete list</button>
        )}
        {ed.cur && (ed.del.kind === "confirm" || ed.del.kind === "busy") && (
          <span className="kb-row" style={{ gap: 8, fontSize: 13 }}>
            <span>Delete “{ed.cur.name}”? Applied settings keep their copy.</span>
            <button type="button" onClick={() => void ed.deleteList()} disabled={ed.del.kind === "busy"}>{ed.del.kind === "busy" ? "Deleting…" : "Delete"}</button>
            <button type="button" className="linkbtn" onClick={ed.cancelDelete}>Keep it</button>
          </span>
        )}
      </div>
      {ed.cur && ed.deleteMissing() && <span className="muted" style={{ fontSize: 13 }}>Deleting a list needs a database update that isn't applied yet. The list stays saved.</span>}
      {ed.del.kind === "error" && <span className="loss" role="alert" style={{ fontSize: 13 }}>{ed.del.text}</span>}

      <div className="kb-ml-lock">
        <span style={{ flex: "none", marginTop: 1 }}><LockIcon /></span>
        <span><b style={{ color: "var(--ink)", fontWeight: 600 }}>Safety filters always on.</b> Every ticker must be tradable at the broker, trade enough each day, have a tight spread, cost $5 or more and have enough history. These cannot be switched off.</span>
      </div>
    </section>
  );
}

function LCard({ name, meta }: { name: string; meta: string }) {
  return (<>
    <span className="kb-lcard-top"><span className="kb-lcard-name">{name}</span><span aria-hidden="true" className="kb-radio" /></span>
    <span className="num kb-lcard-meta">{meta}</span>
  </>);
}

function Chip({ item: i, onDrop, onRetry }: { item: Item; onDrop: () => void; onRetry: () => void }) {
  const remove = (
    <button type="button" className="kb-chip-x" aria-label={`Remove ${i.symbol}`} onClick={onDrop}><XIcon /></button>
  );
  if (i.state === "checking")
    return (
      <li className="kb-chip checking" role="status" aria-label={`${i.symbol}, checking`}>
        <span className="sk" aria-hidden="true" style={{ width: 28, height: 28, borderRadius: 999, flex: "none" }} />
        <span className="num kb-chip-sym muted">{i.symbol}</span>
        <span className="sk" aria-hidden="true" style={{ width: 74, height: 12, borderRadius: 6, flex: "none" }} />
        <span className="muted" style={{ fontSize: 12 }}>checking</span>
      </li>
    );
  if (i.state === "accepted") {
    const nm = i.name || (companyName(i.symbol) !== i.symbol ? companyName(i.symbol) : "");
    return (
      <li className="kb-chip ok" aria-label={`${i.symbol}${nm ? `, ${nm}` : ""}, accepted`}>
        <StockBadge symbol={i.symbol} size={28} />
        <span className="num kb-chip-sym">{i.symbol}</span>
        {nm && <span className="kb-chip-name muted">{nm}</span>}
        {remove}
      </li>
    );
  }
  const slow = i.state === "slow";
  const why = slow ? "still checking. Try again in a minute." : i.reason ?? "";
  return (
    <li className={`kb-chip ${slow ? "slow" : "no"}`} aria-label={slow ? `${i.symbol}, still checking` : `${i.symbol}, refused: ${why}`}>
      {!slow && <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--loss)" strokeWidth="2.2" aria-hidden="true" style={{ flex: "none" }}><circle cx="12" cy="12" r="8.5" /><path d="M6 18 L18 6" /></svg>}
      <span className="num kb-chip-sym">{i.symbol}</span>
      <span className="kb-chip-why" title={why}>{slow ? "Still checking" : i.state === "error" ? `Not checked · ${why}` : `Refused · ${why}`}</span>
      {(slow || i.state === "error") && <button type="button" className="linkbtn" style={{ fontSize: 12 }} onClick={onRetry} aria-label={`Check ${i.symbol} again`}>Retry</button>}
      {remove}
    </li>
  );
}

/** The side card 'This list' (board 10): trades a month, accepted / refused / new / room left, and what apply does. */
export function ThisListCard({ ed, est, nextOpen }: { ed: ListEditor; est: Estimate; nextOpen: string }) {
  const { t } = ed;
  const fresh = t.fresh.map((i) => i.symbol);
  const measured = t.accepted.length > 0 && !est.unmeasured.length && !fresh.length;
  const big = !t.accepted.length ? "No tickers yet" : measured ? estShort(est) : "not measured yet";
  const note = !t.accepted.length ? "Add tickers to see what this list would do."
    : measured ? "From the replay of these catalogue stocks."
    : `${listWords(fresh.length ? fresh : est.unmeasured)} ${(fresh.length || est.unmeasured.length) === 1 ? "is" : "are"} new to Kaibot, so there is no measured trade rate. The figure appears after this list’s first replay.`;
  return (<>
    <section className="card kb-card" aria-labelledby="rs-h">
      <h2 id="rs-h" className="kb-h2">This list</h2>
      <div className="kb-impact">
        <span className="muted" style={{ fontSize: 12 }}>Trades a month</span>
        <span className={`num swap ${measured ? "" : "muted"}`} style={{ fontSize: 24, fontWeight: 700, letterSpacing: "-0.02em" }}>{big}</span>
        <span className="swap muted" style={{ fontSize: 13, lineHeight: 1.5 }}>{note}</span>
      </div>
      <dl className="num kb-ml-dl">
        <dt>Accepted</dt><dd className="swap">{t.accepted.length}</dd>
        <dt>Refused, never traded</dt><dd className="swap">{t.refused.length}</dd>
        <dt>New to Kaibot</dt><dd className="swap">{t.accepted.length ? fresh.length : "—"}</dd>
        <dt>Room left</dt><dd className="swap">{MAX_TICKERS - t.total} of {MAX_TICKERS}</dd>
      </dl>
    </section>
    <section className="card kb-card" aria-labelledby="ap-h">
      <h2 id="ap-h" className="kb-h2">When you apply</h2>
      <ul className="muted" style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 8, fontSize: 13, lineHeight: 1.5 }}>
        <li>Starts at the next open, likely {nextOpen}. Open trades keep their rules.</li>
        <li>The list is stored as it is now. Later edits to a saved list do not change what is running.</li>
        <li>Your report starts a new chapter and the change is logged as a trial.</li>
        <li>No shorts, no leverage, nothing held overnight. Same as every other focus.</li>
        <li>Kaibot checks every ticker again at each open; one that no longer passes is not traded that day.</li>
      </ul>
    </section>
  </>);
}
