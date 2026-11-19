// #311 on Home: the engine's daily note in plain words, after the close (lib/engineevents.ts). A small card: who wrote
// it, then the paragraph cut to three lines; the full text and how it was made open in a sheet (v4: detail behind a tap).
import { useState } from "react";
import { type DailyNote, isToday, noteLabel } from "../lib/engineevents";
import { NAME, brandText } from "../brand/brand";
import { Sheet } from "./Sheet";

const dayName = (s: string) =>
  new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(s + "T00:00:00Z"));

export function DailyNoteCard({ note, now = new Date() }: { note: DailyNote; now?: Date }) {
  const [open, setOpen] = useState(false);
  const head = `${isToday(note, now) ? "Today" : dayName(note.session)} in plain words`;
  const label = noteLabel(note, now);
  const body = brandText(note.text);
  return (
    <section aria-label={head} className="note-card">
      <button type="button" className="note-btn" aria-haspopup="dialog" onClick={() => setOpen(true)}>
        <span className="note-head"><b>{head}</b><span className={`note-src ${note.source}`}>{label}</span></span>
        <span className="note-text">{body}</span>
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title={head}>
        <p className="note-full">{body}</p>
        <p className="muted">{label}.{note.source === "model"
          ? " A small AI model wrote it from the engine's own records of the day: the plan, the orders, the fills and the holdings. Every number in it is checked against those records."
          : ` ${NAME} wrote it with a fixed template from the engine's records${note.why ? ` (the AI note was not used: ${brandText(note.why)})` : ""}.`}</p>
        <p className="muted">It explains only. Nothing that trades reads it.</p>
      </Sheet>
    </section>
  );
}
