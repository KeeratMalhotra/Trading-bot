import clsx from "clsx";
import { motion } from "motion/react";
import { BrainCircuit, Compass, Crown, Flame, Gauge, Gem, OctagonAlert, Pause, Shield, Swords } from "lucide-react";
import type { Bot } from "../types";
import { AnimatedNumber } from "./AnimatedNumber";
import { pct, tone, usd } from "../lib/format";
import { useStore } from "../store";

const ICON: Record<string, typeof Shield> = {
  oracle: BrainCircuit,
  nomad: Compass,
  low: Shield,
  medium: Gauge,
  high: Flame,
  hodl: Gem,
};

export function BotCard({ bot }: { bot: Bot }) {
  const Icon = ICON[bot.id] ?? Swords;
  const leader = bot.rank === 1 && Math.abs(bot.equity - bot.start) > 0.01;
  const filter = useStore((s) => s.feedFilter);
  const oracle = useStore((s) => s.live?.oracle);
  const setS = useStore((s) => s.set);
  const wr = bot.all.win_rate;
  const limitUsed = Math.min(1, bot.today.limit_used);
  const status = bot.kind === "oracle" && oracle && !oracle.ready ? oracle.status : bot.status;

  return (
    <motion.div
      layout
      transition={{ type: "spring", stiffness: 300, damping: 30 }}
      onClick={() => setS({ feedFilter: filter === bot.id ? "all" : bot.id })}
      className={clsx(
        "panel relative overflow-hidden p-3 cursor-pointer select-none min-w-0",
        bot.benchmark && "!border-dashed !border-white/15",
        filter === bot.id && "ring-1 ring-white/25",
      )}
      style={{ order: bot.rank }}
    >
      <div
        className={clsx("pointer-events-none absolute -top-16 -right-14 w-48 h-48 rounded-full blur-3xl", leader && "leader-glow")}
        style={{ background: bot.color, opacity: leader ? 0.18 : 0.07 }}
      />
      <div className="absolute top-0 left-0 right-0 h-[2px]" style={{ background: bot.color, opacity: bot.benchmark ? 0.5 : 1 }} />

      <div className="relative flex items-start justify-between gap-2">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="grid place-items-center w-8 h-8 shrink-0 rounded-lg" style={{ background: `${bot.color}1f`, color: bot.color }}>
            <Icon className="w-4 h-4" />
          </div>
          <div className="leading-tight min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-[13px] font-bold tracking-[0.1em] text-white">{bot.name}</span>
              {leader && <Crown className="w-3.5 h-3.5 text-medium shrink-0" />}
            </div>
            <div className="text-[9px] font-semibold tracking-wider truncate" style={{ color: bot.color }}>
              {bot.benchmark ? "BENCHMARK" : `${bot.label.toUpperCase()} · ${bot.generation.toUpperCase()}`}
            </div>
          </div>
        </div>
        <span className={clsx("num text-sm font-bold shrink-0", bot.rank === 1 ? "text-white" : "text-ink-400")}>#{bot.rank}</span>
      </div>

      <div className="relative mt-1.5 text-[10px] text-ink-400 flex items-center gap-1.5 truncate">
        {bot.halted ? (
          <span className="text-down flex items-center gap-1">
            <OctagonAlert className="w-3 h-3" /> {bot.status}
          </span>
        ) : bot.paused ? (
          <span className="text-medium flex items-center gap-1">
            <Pause className="w-3 h-3" /> Paused
          </span>
        ) : (
          <>
            <span className="live-dot !w-1.5 !h-1.5 shrink-0" style={{ color: bot.color }} />
            <span className="truncate">{status}</span>
          </>
        )}
      </div>

      <div className="relative mt-2 flex items-end justify-between gap-2">
        <div className="min-w-0">
          <AnimatedNumber value={bot.equity} format={(v) => usd(v)} className="text-[22px] leading-none font-semibold text-white" />
          <div className={clsx("num text-[11px] mt-1", tone(bot.total_return))}>{pct(bot.total_return)} all-time</div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-[9px] uppercase tracking-widest text-ink-400">Today</div>
          <div className={clsx("num text-sm font-semibold", tone(bot.today.pnl))}>{usd(bot.today.pnl, { sign: true })}</div>
          <div className="num text-[9px] text-ink-400">
            after tax <span className={tone(bot.today.after_tax)}>{usd(bot.today.after_tax, { sign: true })}</span>
          </div>
        </div>
      </div>

      <div className="relative mt-2 grid grid-cols-3 gap-1.5 text-center [@media(max-height:900px)]:hidden">
        <Stat label="Win rate" value={wr == null ? "—" : `${Math.round(wr * 100)}%`} sub={`${bot.all.wins}W ${bot.all.losses}L`} />
        <Stat label="Open" value={`${bot.open}/${bot.profile.max_open}`} sub={`${bot.all.trades} closed`} />
        <Stat label="Fees" value={usd(bot.all.fees, { compact: true })} sub={`${bot.profile.symbols.length} coins`} />
      </div>

      {!bot.benchmark && (
        <div className="relative mt-2">
          <div className="h-1 rounded-full bg-white/5 overflow-hidden" title={`Daily loss limit: ${Math.round(limitUsed * 100)}% used`}>
            <motion.div
              className="h-full rounded-full"
              animate={{ width: `${Math.max(2, limitUsed * 100)}%` }}
              style={{ background: limitUsed > 0.66 ? "#f43f5e" : limitUsed > 0.33 ? "#fbbf24" : bot.color }}
            />
          </div>
        </div>
      )}

      {bot.halted && (
        <div className="absolute inset-0 grid place-items-center bg-ink-950/75 backdrop-blur-[2px]">
          <div className="text-center">
            <OctagonAlert className="w-7 h-7 text-down mx-auto" />
            <div className="mt-1 text-xs font-bold tracking-widest text-down">CIRCUIT BREAKER</div>
            <div className="text-[10px] text-ink-300">Resumes midnight ET</div>
          </div>
        </div>
      )}
    </motion.div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-md bg-white/[0.03] py-1">
      <div className="text-[8px] uppercase tracking-widest text-ink-400">{label}</div>
      <div className="num text-[12px] font-medium text-white">{value}</div>
      {sub && <div className="num text-[8px] text-ink-400">{sub}</div>}
    </div>
  );
}
