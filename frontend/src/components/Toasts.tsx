import clsx from "clsx";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, CalendarDays, Crown, Flame, Hash, OctagonX, Rocket, Target, Timer, TrendingUp, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { AGENT_COLOR, useStore } from "../store";
import { pct, price, usd } from "../lib/format";
import type { Event, Milestone, TradeCard, WeeklyReport } from "../types";

const CARD_S = 14;
const MILESTONE_S = 16;
const WEEKLY_S = 120;
const MAX_SHOWN = 2;          // more than two would cover the chart; the rest wait their turn
const GOLD = "#e3b35b";
type Item = { id: string; kind: "card" | "milestone"; ev: Event; dur: number; until: number | null };

function heldFor(sec: number | null): string {
  if (sec == null) return "";
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  if (d >= 10) return `${d} days`;
  if (d) return `${d}d ${h}h`;
  return h ? `${h}h ${Math.floor((sec % 3600) / 60)}m` : `${Math.max(1, Math.floor(sec / 60))}m`;
}

/** Only events that arrive while the page is open (not the history in the snapshot). */
function useNewEvents(kinds: string[], onEvent: (e: Event) => void) {
  const events = useStore((s) => s.events);
  const seen = useRef<Set<string> | null>(null);
  useEffect(() => {
    if (seen.current === null) {
      seen.current = new Set(events.map((e) => e.id));
      return;
    }
    for (const e of events) {
      if (seen.current.has(e.id)) continue;
      seen.current.add(e.id);
      if (kinds.includes(e.kind)) onEvent(e);
    }
  }, [events]); // eslint-disable-line react-hooks/exhaustive-deps
}

/** Trade cards and milestones: slide in over the chart's lower-left corner, then leave. */
export function Toasts() {
  const [items, setItems] = useState<Item[]>([]);
  useNewEvents(["card", "milestone"], (e) => {
    const kind = e.kind as Item["kind"];
    setItems((xs) => [...xs, { id: e.id, kind, ev: e, dur: kind === "card" ? CARD_S : MILESTONE_S, until: null }].slice(-12));
  });
  useEffect(() => {
    const tick = () => {
      const now = Date.now() / 1000;
      setItems((xs) => {
        const live = xs.filter((x) => x.until == null || x.until > now);
        let free = MAX_SHOWN - live.filter((x) => x.until != null).length;
        const out = live.map((x) => (x.until == null && free-- > 0 ? { ...x, until: now + x.dur } : x));
        return out.length === xs.length && out.every((x, i) => x === xs[i]) ? xs : out;
      });
    };
    tick();
    const t = setInterval(tick, 400);
    return () => clearInterval(t);
  }, [items.length]);
  const shown = items.filter((x) => x.until != null);
  return (
    <div className="absolute left-3 bottom-8 z-20 flex flex-col-reverse gap-2 w-[360px] pointer-events-none">
      <AnimatePresence initial={false}>
        {[...shown].reverse().map((it) => (
          <motion.div
            key={it.id}
            layout
            initial={{ opacity: 0, x: -24, scale: 0.97 }}
            animate={{ opacity: 1, x: 0, scale: 1 }}
            exit={{ opacity: 0, x: -24 }}
            transition={{ type: "spring", stiffness: 380, damping: 30 }}
          >
            {it.kind === "card" ? <CardToast c={it.ev.data.card as TradeCard} /> : <MilestoneToast m={it.ev.data.milestone as Milestone} />}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

function CardToast({ c }: { c: TradeCard }) {
  const win = c.pnl >= 0;
  const color = AGENT_COLOR[c.agent] ?? "#8a919c";
  const Icon = c.why === "TARGET" ? Target : c.why === "STOP" ? OctagonX : c.why === "TIME" ? Timer : TrendingUp;
  const label = { TARGET: "Target hit", STOP: "Stopped out", TIME: "14-day window ended", PLAN: "Trade closed" }[c.why];
  return (
    <div className="rounded-lg border border-line2 bg-[#0f1216]/95 backdrop-blur px-3.5 py-2.5 shadow-lg shadow-black/40" style={{ boxShadow: `inset 3px 0 0 ${color}` }}>
      <div className="flex items-center gap-2 text-[10.5px]">
        <Icon size={12} strokeWidth={2.2} className={win ? "text-up" : "text-down"} />
        <span className="font-semibold tracking-wide" style={{ color }}>
          {c.agents.join(" + ")}
        </span>
        <span className="text-mute uppercase tracking-[0.08em]">{label}</span>
        <span className="ml-auto num text-mute">#{c.n}</span>
      </div>
      <div className="flex items-baseline justify-between mt-1">
        <span className="text-hi font-medium">
          {c.instrument} <span className="text-soft font-normal">{c.side}</span>
        </span>
        <span className={clsx("num text-[18px] leading-none font-medium", win ? "text-up" : "text-down")}>{usd(c.pnl, { sign: true })}</span>
      </div>
      <div className="num text-[11px] text-soft mt-1">
        {price(c.entry)} <ArrowRight size={10} strokeWidth={2.2} className="inline -mt-0.5" /> {price(c.exit)} ·{" "}
        <span className={win ? "text-up" : "text-down"}>{pct(c.ret, 1)}</span>
        {c.R != null && <> · {`${c.R >= 0 ? "+" : "−"}${Math.abs(c.R).toFixed(1)}R`}</>}
        {c.opened != null && <> · held {heldFor(c.closed - c.opened)}</>}
      </div>
    </div>
  );
}

function MilestoneToast({ m }: { m: Milestone }) {
  const Icon = { ath: Crown, return: Rocket, streak: Flame, trades: Hash, days: CalendarDays }[m.kind] ?? Crown;
  return (
    <div className="rounded-lg border px-3.5 py-2.5 bg-[#15120b]/95 backdrop-blur shadow-lg shadow-black/40" style={{ borderColor: `${GOLD}66` }}>
      <div className="flex items-center gap-2 text-[10.5px] uppercase tracking-[0.1em]" style={{ color: GOLD }}>
        <Icon size={13} strokeWidth={2.2} />
        Milestone
      </div>
      <div className="text-hi font-medium text-[14px] mt-1">{m.title}</div>
      <div className="text-[11.5px] text-soft mt-0.5">{m.text}</div>
    </div>
  );
}

/** The weekly earnings call: takes over the chart for two minutes when a week closes. */
export function WeeklyOverlay() {
  const [rep, setRep] = useState<{ r: WeeklyReport; until: number } | null>(null);
  useNewEvents(["weekly"], (e) => setRep({ r: e.data.report as WeeklyReport, until: Date.now() / 1000 + WEEKLY_S }));
  useEffect(() => {
    if (!rep) return;
    const t = setTimeout(() => setRep(null), Math.max(0, rep.until * 1000 - Date.now()));
    return () => clearTimeout(t);
  }, [rep]);
  return (
    <AnimatePresence>
      {rep && (
        <motion.div
          className="absolute inset-0 z-30 grid place-items-center bg-bg/70 backdrop-blur-[2px] p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          <motion.div initial={{ y: 12, scale: 0.98 }} animate={{ y: 0, scale: 1 }} className="relative w-full max-w-[760px]">
            <button onClick={() => setRep(null)} className="absolute right-3 top-3 text-mute hover:text-soft" aria-label="Close">
              <X size={14} />
            </button>
            <WeeklyCard r={rep.r} />
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function WeeklyCard({ r, compact = false }: { r: WeeklyReport; compact?: boolean }) {
  const order = Object.entries(r.agents).sort((a, b) => a[1].rank - b[1].rank);
  return (
    <div className={clsx("rounded-xl border border-line2 bg-panel", compact ? "p-3" : "p-5")}>
      <div className="flex items-baseline justify-between gap-3">
        <div className="min-w-0">
          {!compact && (
            <div className="text-[10.5px] uppercase tracking-[0.12em]" style={{ color: GOLD }}>
              Weekly earnings call
            </div>
          )}
          <div className={clsx("text-hi font-medium whitespace-nowrap", compact ? "text-[13px]" : "text-[17px]")}>{r.label}</div>
          {compact && (
            <div className="text-[11px] text-mute">
              Winner <span style={{ color: AGENT_COLOR[r.winner] }}>{r.winner}</span>
            </div>
          )}
        </div>
        <div className="text-right">
          <div className={clsx("num font-medium", compact ? "text-[15px]" : "text-[22px]", r.account.pnl >= 0 ? "text-up" : "text-down")}>
            {usd(r.account.pnl, { sign: true })}
          </div>
          <div className="text-[11px] text-mute">
            account <span className="num">{pct(r.account.ret)}</span>
            {r.btc_ret != null && (
              <>
                {" "}
                · BTC <span className="num">{pct(r.btc_ret)}</span>
              </>
            )}
          </div>
        </div>
      </div>
      <div className={clsx("grid grid-cols-3 gap-2", compact ? "mt-2.5" : "mt-4")}>
        {order.map(([id, a]) => (
          <div
            key={id}
            className={clsx("rounded-lg border min-w-0", compact ? "px-2 py-1.5" : "px-3 py-2", a.rank === 1 ? "bg-[#15120b]" : "border-line")}
            style={a.rank === 1 ? { borderColor: `${GOLD}66` } : undefined}
          >
            <div className="flex items-center gap-1.5 min-w-0">
              {a.rank === 1 && !compact && <Crown size={12} style={{ color: GOLD }} />}
              <span className={clsx("font-semibold tracking-wide truncate", compact ? "text-[10.5px]" : "text-[12px]")} style={{ color: AGENT_COLOR[id] }}>
                {id}
              </span>
              <span className={clsx("ml-auto num leading-none font-semibold text-hi shrink-0", compact ? "text-[14px]" : "text-[18px]")}>{a.grade}</span>
            </div>
            <div className={clsx("num mt-1", compact ? "text-[11px]" : "text-[12px]", a.ret > 0 ? "text-up" : a.ret < 0 ? "text-down" : "text-soft")}>
              {pct(a.ret, 1)}
              {!compact && <span className="text-mute"> · {usd(a.pnl, { sign: true, whole: true })}</span>}
            </div>
            {!compact && a.line && <div className="text-[11.5px] text-soft mt-1.5 leading-snug">“{a.line}”</div>}
          </div>
        ))}
      </div>
      <div className={clsx("grid grid-cols-2 gap-x-6 gap-y-1 text-[11.5px]", compact ? "mt-2.5" : "mt-4")}>
        <Line k="Best trade" v={r.best ? `${r.best.agent} ${r.best.instrument} ${r.best.side} ${usd(r.best.pnl, { sign: true })}` : "—"} tone="text-up" />
        <Line k="Worst trade" v={r.worst ? `${r.worst.agent} ${r.worst.instrument} ${r.worst.side} ${usd(r.worst.pnl, { sign: true })}` : "—"} tone="text-down" />
        <Line k="Trades closed" v={String(r.trades)} />
        <Line k="Season (weeks won)" v={Object.entries(r.wins).map(([a, n]) => `${a} ${n}`).join(" · ")} />
        {r.desk.length > 0 && <Line k="Desk" v={r.desk[r.desk.length - 1]} />}
        {r.fans_result && <Line k="Fans" v={r.fans_result} />}
      </div>
    </div>
  );
}

function Line({ k, v, tone }: { k: string; v: string; tone?: string }) {
  return (
    <div className="flex gap-2 min-w-0">
      <span className="text-mute shrink-0">{k}</span>
      <span className={clsx("num truncate", tone ?? "text-text")}>{v}</span>
    </div>
  );
}
