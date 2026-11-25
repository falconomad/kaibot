// Error boundaries (#738): without one, a single render error anywhere unmounted the whole app and left a blank page.
// Each page sits in one (main.tsx), and so does each independent card on Home (the pot, its chart, the holdings, the
// market notices, the headlines), so one broken card leaves the rest of the page, the header, the navigation and the
// halt bar in place. A last one around the whole app catches a failing shell or sign-in.
// Reporting: the console only, with the boundary's name. The app has no client-error reporting path, and adding a
// third-party service is not wanted. The fallback reuses the shared error state's classes (States.tsx, app.css .state),
// so it adds no CSS.
import { Component, Fragment, type ErrorInfo, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

export const BOUNDARY_TEXT = "This part couldn't load.";

interface Props {
  /** What this boundary holds ("Home", "Pot", "Holdings"): said in the console log, and in the fallback's data-part. */
  name: string;
  children: ReactNode;
  /** No card around the fallback: for a boundary that is already inside a card or a row. */
  compact?: boolean;
  /** When this changes after an error (a new path), the boundary tries its children again by itself. */
  resetKey?: unknown;
}
interface State { error: unknown; attempt: number }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, attempt: 0 };

  static getDerivedStateFromError(error: unknown): Partial<State> {
    // A thrown null or undefined still counts as an error; a falsy throw (0, "") is kept and checked against null.
    return { error: error ?? new Error("render error") };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error(`[boundary] ${this.props.name} failed to render:`, error, info.componentStack ?? "");
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error !== null && prev.resetKey !== this.props.resetKey) this.retry();
  }

  /** Re-mount the children: a new key gives them fresh state (a lazy chunk that failed to load stays failed; Reload). */
  retry = () => this.setState((s) => ({ error: null, attempt: s.attempt + 1 }));

  render() {
    if (this.state.error === null) return <Fragment key={this.state.attempt}>{this.props.children}</Fragment>;
    const body = (
      <div className="state state-error" role="alert" data-part={this.props.name}>
        <b>{BOUNDARY_TEXT}</b>{" "}
        <span className="muted">Reload, or open the <Link to="/health">Engine page</Link>. Nothing in the engine changed.</span>
        <span><button type="button" onClick={this.retry}>Retry</button></span>
      </div>
    );
    return this.props.compact ? body : <section className="card">{body}</section>;
  }
}

/** A page's boundary: a new path tries again, so leaving a broken page and coming back does not keep the fallback. */
export function PageBoundary({ name, children }: { name: string; children: ReactNode }) {
  const { pathname } = useLocation();
  return <ErrorBoundary name={name} resetKey={pathname}>{children}</ErrorBoundary>;
}
