// M5 config controls: native inputs where possible, proper ARIA where not. All keyboard operable.
import { useRef, type CSSProperties, type KeyboardEvent, type ReactNode } from "react";

export interface RadioOpt<T extends string | number> { value: T; label: string; content?: ReactNode; disabled?: boolean; className?: string; style?: CSSProperties }

/** WAI-ARIA radio group with a roving tabindex: Tab enters at the checked option, arrows move and select. */
export function RadioGroup<T extends string | number>({ options, value, onChange, label, labelledBy, className, optClass, style }: {
  options: RadioOpt<T>[]; value: T | null; onChange: (v: T) => void; label?: string; labelledBy?: string; className?: string; optClass?: string;
  style?: CSSProperties;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const enabled = options.map((o, i) => (o.disabled ? -1 : i)).filter((i) => i >= 0);
  const cur = options.findIndex((o) => o.value === value);
  const focusIdx = cur >= 0 && !options[cur].disabled ? cur : enabled[0];
  function key(e: KeyboardEvent, i: number) {
    const dir = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    const jump = e.key === "Home" ? enabled[0] : e.key === "End" ? enabled[enabled.length - 1] : null;
    if (!dir && jump === null) return;
    e.preventDefault();
    const pos = enabled.indexOf(i);
    const next = jump ?? enabled[(pos + dir + enabled.length) % enabled.length];
    refs.current[next]?.focus();
    onChange(options[next].value);
  }
  return (
    <div role="radiogroup" aria-label={label} aria-labelledby={labelledBy} className={className} style={style}>
      {options.map((o, i) => (
        <button key={String(o.value)} ref={(el) => { refs.current[i] = el; }} type="button" role="radio" aria-checked={o.value === value}
          aria-disabled={o.disabled || undefined} aria-label={o.content ? o.label : undefined} tabIndex={i === focusIdx ? 0 : -1}
          className={[optClass, o.className].filter(Boolean).join(" ")} style={o.style}
          onClick={() => !o.disabled && onChange(o.value)} onKeyDown={(e) => key(e, i)}>
          {o.content ?? o.label}
        </button>
      ))}
    </div>
  );
}

export function Switch({ on, onChange, label, disabled }: { on: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} className="kb-tog" disabled={disabled} onClick={() => onChange(!on)}>
      <span aria-hidden="true" />
    </button>
  );
}

export function PillSwitch({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} className="kb-pillsw" onClick={() => onChange(!on)}>
      <span aria-hidden="true" className="dot">{on ? "✓" : ""}</span>{label}
    </button>
  );
}

const pctOf = (v: number, min: number, max: number) => (max > min ? ((v - min) / (max - min)) * 100 : 100);

/** Native range input (keyboard + screen reader for free) with the v3 track. */
export function Slider({ label, value, min, max, step, onChange, text, lo, hi, note, id }: {
  label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; text: string;
  lo?: string; hi?: string; note?: string; id?: string;
}) {
  return (
    <div className="kb-slider">
      <input id={id} className="kb" type="range" aria-label={label} min={min} max={max} step={step} value={value} aria-valuetext={text}
        disabled={min === max} onChange={(e) => onChange(Number(e.target.value))}
        style={{ "--p": `${pctOf(value, min, max)}%` } as CSSProperties} />
      <div className="num kb-slider-cap"><span>{lo}</span><span>{note}</span><span>{hi}</span></div>
    </div>
  );
}

export function Stepper({ label, value, min, max, onChange, text }: {
  label: string; value: number; min: number; max: number; onChange: (v: number) => void; text: string;
}) {
  return (
    <div className="kb-stepper" role="group" aria-label={label}>
      <button type="button" aria-label={`Fewer: ${label}`} disabled={value <= min} onClick={() => onChange(Math.max(min, value - 1))}>−</button>
      <output className="num" aria-live="polite">{text}</output>
      <button type="button" aria-label={`More: ${label}`} disabled={value >= max} onClick={() => onChange(Math.min(max, value + 1))}>+</button>
    </div>
  );
}
