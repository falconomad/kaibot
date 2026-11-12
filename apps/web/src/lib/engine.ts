// Engine version for the Engine health page: the code the engine runs (heartbeat `code_sha`) and the strategy
// fingerprint it logged at start-up (engine_start event, `fingerprint`). Pure: unit-tested in engine.test.ts.
//
// Fingerprint format (src/kaibot/evaluation/fingerprint.py):  <all>;core=<core>;<rule>=<rule fingerprint>;...
// A string without parts (older engines, or a plain-list spec) is one hash for everything; "unknown" means the engine
// could not compute it. Evidence only counts while a rule's part stays the same (decision 15, gate "frozen code").
import type { Health } from "./api";

export interface FingerprintPart { key: string; hash: string }
export interface Fingerprint { all: string | null; core: string | null; rules: FingerprintPart[]; unknown: boolean; raw: string }

export function parseFingerprint(raw: unknown): Fingerprint | null {
  if (typeof raw !== "string" || !raw.trim()) return null;
  const s = raw.trim().slice(0, 2000);
  if (s === "unknown") return { all: null, core: null, rules: [], unknown: true, raw: s };
  const [head, ...rest] = s.split(";");
  let core: string | null = null;
  const rules: FingerprintPart[] = [];
  for (const part of rest) {
    const i = part.indexOf("=");
    if (i <= 0) continue;
    const key = part.slice(0, i).trim(), hash = part.slice(i + 1).trim();
    if (!hash) continue;
    if (key === "core") core = hash;
    else rules.push({ key, hash });
  }
  return { all: head.includes("=") ? null : head, core, rules, unknown: false, raw: s };
}

export interface EngineVersion {
  /** Code version in the latest engine heartbeat (what runs now), or null when the heartbeat has none. */
  running: string | null;
  /** The latest engine start: when, which code, which fingerprint. */
  start: { ts: string; codeSha: string | null; fingerprint: Fingerprint | null } | null;
  /** The running code differs from the code at the last start (a restart is missing from the log, or the log is old). */
  mismatch: boolean;
}

const str = (v: unknown) => (typeof v === "string" && v.trim() ? v.trim().slice(0, 80) : null);

export function engineVersion(h: Pick<Health, "heartbeat" | "events">): EngineVersion {
  const beat = h.heartbeat.find((b) => b.process === "engine");
  const running = str((beat?.detail as Record<string, unknown> | null | undefined)?.code_sha);
  const ev = [...h.events].filter((e) => e.type === "engine_start").sort((a, b) => b.ts.localeCompare(a.ts))[0];
  const start = ev ? { ts: ev.ts, codeSha: str(ev.payload?.code_sha), fingerprint: parseFingerprint(ev.payload?.fingerprint) } : null;
  return { running, start, mismatch: !!running && !!start?.codeSha && running !== start.codeSha };
}
