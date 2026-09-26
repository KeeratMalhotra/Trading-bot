const usd2 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

export function usd(x: number, opts: { sign?: boolean; compact?: boolean } = {}): string {
  const v = Number.isFinite(x) ? x : 0;
  const body = opts.compact && Math.abs(v) >= 10000 ? usd0.format(Math.abs(v)) : usd2.format(Math.abs(v));
  if (opts.sign) return (v >= 0 ? "+" : "−") + body;
  return (v < 0 ? "−" : "") + body;
}

export function pct(x: number, digits = 2, sign = true): string {
  const v = Number.isFinite(x) ? x * 100 : 0;
  const s = Math.abs(v).toFixed(digits) + "%";
  if (!sign) return (v < 0 ? "−" : "") + s;
  return (v >= 0 ? "+" : "−") + s;
}

export function price(p: number): string {
  if (!Number.isFinite(p) || p === 0) return "—";
  if (p >= 1000) return "$" + p.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (p >= 10) return "$" + p.toFixed(2);
  if (p >= 1) return "$" + p.toFixed(4);
  return "$" + p.toFixed(5);
}

export function qty(q: number): string {
  if (q >= 1000) return q.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (q >= 1) return q.toFixed(3);
  return q.toFixed(5);
}

export function coin(symbol: string): string {
  return symbol.split("-")[0];
}

const etTime = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit", second: "2-digit" });
const etShort = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });
const etDate = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

export const timeET = (ts: number) => etTime.format(new Date(ts * 1000));
export const timeShortET = (ts: number) => etShort.format(new Date(ts * 1000));
export const dateET = (ts: number) => etDate.format(new Date(ts * 1000));

export function duration(sec: number): string {
  sec = Math.max(0, Math.floor(sec));
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s.toString().padStart(2, "0")}s`;
  return `${s}s`;
}

export const tone = (x: number) => (x > 0 ? "text-up" : x < 0 ? "text-down" : "text-ink-300");
