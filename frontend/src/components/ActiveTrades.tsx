import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { useStore, botColor } from "../store";
import type { Position } from "../types";
import { coin, duration, pct, price, tone, usd } from "../lib/format";

const NAMES: Record<string, string> = { low: "SENTINEL", medium: "TACTICIAN", high: "BERSERKER" };

export function ActiveTrades() {
  const positions = useStore((s) => s.live?.positions ?? []);
  const focus = useStore((s) => s.focus);
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);
  const total = positions.reduce((a, p) => a + p.pnl, 0);

  return (
    <div className="panel flex flex-col min-h-0 h-full">
      <div className="flex items-center justify-between px-4 pt-3 pb-2">
        <div className="flex items-center gap-3">
          <span className="panel-title">Active trades</span>
          <span className="num text-[11px] text-ink-300 bg-white/5 rounded-md px-1.5">{positions.length}</span>
        </div>
        {positions.length > 0 && (
          <div className="text-[11px] text-ink-400">
            Open P&amp;L after fees <span className={clsx("num font-semibold", tone(total))}>{usd(total, { sign: true })}</span>
          </div>
        )}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto scroll-thin px-2 pb-2">
        <div className="grid grid-cols-[110px_80px_1fr_110px_110px_minmax(180px,1.4fr)_70px] gap-3 px-3 pb-1.5 text-[9px] uppercase tracking-widest text-ink-400">
          <span>Bot</span>
          <span>Coin</span>
          <span>Strategy</span>
          <span className="text-right">Entry / now</span>
          <span className="text-right">P&amp;L</span>
          <span>Stop · progress · target</span>
          <span className="text-right">Age</span>
        </div>
        <AnimatePresence initial={false}>
          {positions.map((p) => (
            <Row key={p.id} p={p} now={now} onClick={() => focus(p.symbol)} />
          ))}
        </AnimatePresence>
        {!positions.length && (
          <div className="text-center py-8">
            <div className="text-sm text-ink-300">No open trades right now</div>
            <div className="text-xs text-ink-400 mt-1">The bots only trade when the math works after fees. Waiting is a position too.</div>
          </div>
        )}
      </div>
    </div>
  );
}

function Row({ p, now, onClick }: { p: Position; now: number; onClick: () => void }) {
  const col = botColor(p.bot);
  const lo = Math.min(p.stop, p.initial_stop, p.price);
  const hi = Math.max(p.target, p.price);
  const span = hi - lo || 1;
  const x = (v: number) => ((v - lo) / span) * 100;
  const up = p.price >= p.entry;
  const badge = p.status === "opening" ? "FILLING" : p.status === "closing" ? "CLOSING" : p.trailing ? "TRAILING" : p.be ? "RISK-FREE" : null;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 20 }}
      onClick={onClick}
      className="grid grid-cols-[110px_80px_1fr_110px_110px_minmax(180px,1.4fr)_70px] gap-3 items-center px-3 py-2 rounded-xl hover:bg-white/[0.03] cursor-pointer"
    >
      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full" style={{ background: col }} />
        <span className="text-[11px] font-bold tracking-wider" style={{ color: col }}>
          {NAMES[p.bot]}
        </span>
      </div>
      <div className="leading-tight">
        <div className="text-sm font-semibold text-white">{coin(p.symbol)}</div>
        <div className="num text-[10px] text-ink-400">{usd(p.value, { compact: true })}</div>
      </div>
      <div className="leading-tight min-w-0">
        <div className="text-xs text-ink-200 truncate">{p.strategy}</div>
        <div className="flex gap-1 mt-0.5">
          <span className="num text-[9px] text-ink-400">{Math.round(p.confidence)}% conf</span>
          {badge && (
            <span
              className={clsx(
                "text-[9px] font-bold px-1 rounded",
                badge === "TRAILING" || badge === "RISK-FREE" ? "text-up bg-up/10" : "text-medium bg-medium/10",
              )}
            >
              {badge}
            </span>
          )}
        </div>
      </div>
      <div className="text-right leading-tight">
        <div className="num text-[11px] text-ink-400">{price(p.entry)}</div>
        <div className={clsx("num text-xs", up ? "text-up" : "text-down")}>{price(p.price)}</div>
      </div>
      <div className="text-right leading-tight">
        <div className={clsx("num text-sm font-semibold", tone(p.pnl))}>{usd(p.pnl, { sign: true })}</div>
        <div className={clsx("num text-[10px]", tone(p.pnl))}>
          {pct(p.pnl_pct)} · {p.r >= 0 ? "+" : ""}
          {p.r.toFixed(2)}R
        </div>
      </div>
      <div>
        <div className="relative h-2 rounded-full bg-white/5">
          <div className="absolute top-0 bottom-0 rounded-full bg-down/40" style={{ left: 0, width: `${x(p.entry)}%` }} />
          <div className="absolute top-0 bottom-0 rounded-full bg-up/25" style={{ left: `${x(p.entry)}%`, right: 0 }} />
          <motion.div
            className="absolute -top-1 w-1 h-4 rounded-full bg-white shadow-[0_0_8px_rgba(255,255,255,.7)]"
            animate={{ left: `calc(${x(p.price)}% - 2px)` }}
            transition={{ type: "spring", stiffness: 200, damping: 25 }}
          />
          <div className="absolute -top-0.5 w-[2px] h-3 bg-ink-200/60" style={{ left: `${x(p.entry)}%` }} />
          {p.stop > p.initial_stop && <div className="absolute -top-0.5 w-[2px] h-3 bg-down" style={{ left: `${x(p.stop)}%` }} />}
        </div>
        <div className="flex justify-between mt-1 num text-[9px]">
          <span className="text-down">{price(p.stop)}</span>
          <span className="text-up">{price(p.target)}</span>
        </div>
      </div>
      <div className="num text-[11px] text-ink-300 text-right">{duration(now - p.opened)}</div>
    </motion.div>
  );
}
