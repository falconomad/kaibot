const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const money = (v: number | null | undefined) => (v === null || v === undefined ? "—" : usd.format(v));
export const signedMoney = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") + usd.format(Math.abs(v));
export const pct = (v: number, digits = 2) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(digits) + "%";
export const px = (v: number | null | undefined) => (v === null || v === undefined ? "—" : "$" + v.toFixed(2));
export const tone = (v: number | null | undefined) => (!v ? "flat" : v > 0 ? "gain" : "loss");

const etTime = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false });
const cetTime = new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/Berlin", hour: "2-digit", minute: "2-digit", hour12: false });
const etDate = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short", day: "numeric", month: "short" });

export const timeEt = (iso: string) => etTime.format(new Date(iso));
export const timeBoth = (iso: string) => `${etTime.format(new Date(iso))} ET (${cetTime.format(new Date(iso))} CET)`;
export const dateEt = (iso: string) => etDate.format(new Date(iso));
export const day = (isoDate: string) =>
  new Intl.DateTimeFormat("en-US", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(isoDate + "T00:00:00Z"));

export const R = (pnl: number | null, risk: number | null) =>
  pnl === null || !risk ? "—" : (pnl >= 0 ? "+" : "−") + Math.abs(pnl / risk).toFixed(1) + "R";

export const EXIT_LABEL: Record<string, string> = {
  stop: "stop hit", target: "target reached", trail: "trailing stop", time: "time exit 15:55",
};
