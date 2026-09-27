const usd2 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const usd0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export function usd(x: number | null | undefined, opts: { sign?: boolean; whole?: boolean } = {}): string {
  const v = Number.isFinite(x as number) ? (x as number) : 0;
  const body = "$" + (opts.whole ? usd0 : usd2).format(Math.abs(v));
  if (opts.sign) return (v > 0 ? "+" : v < 0 ? "−" : "") + body;
  return (v < 0 ? "−" : "") + body;
}

export function pct(x: number | null | undefined, digits = 2, sign = true): string {
  const v = Number.isFinite(x as number) ? (x as number) * 100 : 0;
  const s = Math.abs(v).toFixed(digits) + "%";
  if (!sign) return (v < 0 ? "−" : "") + s;
  return (v > 0 ? "+" : v < 0 ? "−" : "") + s;
}

export function price(p: number | null | undefined): string {
  if (!Number.isFinite(p as number) || !p) return "—";
  if (p >= 1000) return p.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (p >= 10) return p.toFixed(2);
  if (p >= 1) return p.toFixed(4);
  return p.toFixed(5);
}

export function qty(q: number): string {
  const a = Math.abs(q);
  if (a >= 1000) return q.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (a >= 1) return q.toFixed(3);
  return q.toFixed(5);
}

export const coin = (s: string) => s.split("-")[0];
export const tone = (x: number) => (x > 0.00001 ? "text-up" : x < -0.00001 ? "text-down" : "text-soft");

const etT = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const etD = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", day: "numeric" });
const etDT = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
export const timeET = (ts: number) => etT.format(new Date(ts * 1000));
export const dateET = (ts: number) => etD.format(new Date(ts * 1000));
export const dateTimeET = (ts: number) => etDT.format(new Date(ts * 1000));

export function ago(ts: number, now = Date.now() / 1000): string {
  const s = Math.max(0, now - ts);
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export function left(ts: number, now = Date.now() / 1000): string {
  const s = Math.max(0, ts - now);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  return d ? `${d}d ${h}h` : `${h}h ${Math.floor((s % 3600) / 60)}m`;
}
