import clsx from "clsx";
import { motion } from "motion/react";
import { Crown, Flame, Gauge, OctagonAlert, Pause, Shield, Swords } from "lucide-react";
import type { Bot } from "../types";
import { AnimatedNumber } from "./AnimatedNumber";
import { pct, tone, usd } from "../lib/format";
import { useStore } from "../store";

const ICON = { low: Shield, medium: Gauge, high: Flame } as const;

export function BotCard({ bot }: { bot: Bot }) {
  const Icon = ICON[bot.id] ?? Swords;
  const leader = bot.rank === 1 && bot.equity !== bot.start;
  const filter = useStore((s) => s.feedFilter);
  const setS = useStore((s) => s.set);
  const wr = bot.all.win_rate;
  const limitUsed = Math.min(1, bot.today.limit_used);

  return (
    <motion.div
      layout
      transition={{ type: "spring", stiffness: 300, damping: 30 }}
      onClick={() => setS({ feedFilter: filter === bot.id ? "all" : bot.id })}
      className={clsx("panel relative overflow-hidden p-4 cursor-pointer select-none", filter === bot.id && "ring-1 ring-white/20")}
      style={{ order: bot.rank }}
    >
      {/* color wash */}
      <div
        className={clsx("pointer-events-none absolute -top-20 -right-16 w-64 h-64 rounded-full blur-3xl", leader && "leader-glow")}
        style={{ background: bot.color, opacity: leader ? 0.16 : 0.07 }}
      />
      <div className="absolute top-0 left-0 right-0 h-[2px]" style={{ background: bot.color }} />

      <div className="relative flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div
            className="grid place-items-center w-10 h-10 rounded-xl"
            style={{ background: `${bot.color}1f`, color: bot.color }}
          >
            <Icon className="w-5 h-5" />
          </div>
          <div className="leading-tight">
            <div className="flex items-center gap-2">
              <span className="text-[15px] font-bold tracking-[0.12em] text-white">{bot.name}</span>
              <span
                className="text-[10px] font-semibold px-1.5 py-0.5 rounded-md"
                style={{ color: bot.color, background: `${bot.color}1a` }}
              >
                {bot.label.toUpperCase()}
              </span>
            </div>
            <div className="text-[11px] text-ink-400 mt-0.5 flex items-center gap-1.5">
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
                  <span className="live-dot !w-1.5 !h-1.5" style={{ color: bot.color }} />
                  {bot.status}
                </>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          {leader && <Crown className="w-4 h-4 text-medium" />}
          <span className={clsx("num text-sm font-bold", bot.rank === 1 ? "text-white" : "text-ink-400")}>#{bot.rank}</span>
        </div>
      </div>

      <div className="relative mt-3 flex items-end justify-between gap-3">
        <div>
          <div className="text-[10px] uppercase tracking-widest text-ink-400">Balance</div>
          <AnimatedNumber value={bot.equity} format={(v) => usd(v)} className="text-[28px] leading-none font-semibold text-white" />
          <div className={clsx("num text-xs mt-1", tone(bot.total_return))}>{pct(bot.total_return)} all-time</div>
        </div>
        <div className="text-right">
          <div className="text-[10px] uppercase tracking-widest text-ink-400">Today</div>
          <AnimatedNumber
            value={bot.today.pnl}
            format={(v) => usd(v, { sign: true })}
            className={clsx("text-xl font-semibold", tone(bot.today.pnl))}
            flash={false}
          />
          <div className={clsx("num text-xs", tone(bot.today.pnl))}>{pct(bot.today.pnl_pct)}</div>
        </div>
      </div>

      <div className="relative mt-3 grid grid-cols-4 gap-2 text-center [@media(max-height:900px)]:hidden">
        <Stat label="After tax" value={usd(bot.today.after_tax, { sign: true })} cls={tone(bot.today.after_tax)} />
        <Stat label="Win rate" value={wr == null ? "—" : `${Math.round(wr * 100)}%`} sub={`${bot.all.wins}W ${bot.all.losses}L`} />
        <Stat label="Fees" value={usd(bot.all.fees)} cls="text-ink-200" />
        <Stat label="Open" value={`${bot.open}/${bot.profile.max_open}`} />
      </div>

      <div className="relative mt-3">
        <div className="flex justify-between text-[10px] text-ink-400 mb-1">
          <span>Daily loss limit used</span>
          <span className="num">
            {Math.round(limitUsed * 100)}% of −{Math.round(bot.profile.daily_loss_limit * 100)}%
          </span>
        </div>
        <div className="h-1 rounded-full bg-white/5 overflow-hidden">
          <motion.div
            className="h-full rounded-full"
            animate={{ width: `${Math.max(2, limitUsed * 100)}%` }}
            style={{ background: limitUsed > 0.66 ? "#f43f5e" : limitUsed > 0.33 ? "#fbbf24" : bot.color }}
          />
        </div>
      </div>

      {bot.halted && (
        <div className="absolute inset-0 grid place-items-center bg-ink-950/70 backdrop-blur-[2px]">
          <div className="text-center">
            <OctagonAlert className="w-8 h-8 text-down mx-auto" />
            <div className="mt-1 text-sm font-bold tracking-widest text-down">CIRCUIT BREAKER</div>
            <div className="text-[11px] text-ink-300">Daily loss limit hit · resumes midnight ET</div>
          </div>
        </div>
      )}
    </motion.div>
  );
}

function Stat({ label, value, sub, cls }: { label: string; value: string; sub?: string; cls?: string }) {
  return (
    <div className="rounded-lg bg-white/[0.03] py-1.5">
      <div className="text-[9px] uppercase tracking-widest text-ink-400">{label}</div>
      <div className={clsx("num text-[13px] font-medium", cls ?? "text-white")}>{value}</div>
      {sub && <div className="num text-[9px] text-ink-400">{sub}</div>}
    </div>
  );
}
