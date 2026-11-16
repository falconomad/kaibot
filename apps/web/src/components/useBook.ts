// #130: the book's queries, kept apart from the book's components so Home's first load stays small (the section
// itself is loaded lazily; the pot line only needs the series).
import { useQuery } from "@tanstack/react-query";
import { api, type Run } from "../lib/api";
import { poll, STALE } from "../lib/poll";

/** kb_book and kb_equity_series for a paper run. The book changes once per close: cached for minutes, polled gently. */
export function useBook(run: Run) {
  const on = run.kind === "paper";
  const book = useQuery({ queryKey: ["book", run.id], queryFn: () => api.book(run.id), enabled: on, staleTime: STALE.daily, ...poll(on ? 300_000 : false) });
  const series = useQuery({ queryKey: ["equity_series", run.id], queryFn: () => api.equitySeries(run.id), enabled: on, staleTime: STALE.daily, retry: false });
  return { book, series };
}
