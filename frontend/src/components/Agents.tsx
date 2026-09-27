import clsx from "clsx";
import { AnimatePresence, motion } from "motion/react";
import { Trophy } from "lucide-react";
import { useEffect, useState } from "react";
import { useStore } from "../store";
import { ago, pct, tone, usd } from "../lib/format";
import type { Agent } from "../types";

/** The desk: one card per agent, with this week's race and what the agent just said. */
export function Agents() {
  const agents = useStore((s) => s.live?.agents ?? []);
  const week = useStore((s) => s.live?.show?.week);
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 15000);
    return () => clearInterval(t);
  }, []);
  return (
    <section className="card overflow-hidden shrink-0">
      <div className="flex items-center justify-between px-4 h-9 border-b border-line">
        <span className="label">Desk</span>
        <span className="text-[11px] text-mute">
          {week ? `Weekly race · ${week.label}` : "Weekly race"} · capital re-allocated monthly by 90-day risk-adjusted return
        </span>
      </div>
      <div className="grid grid-cols-3 gap-px bg-line">
        {agents.map((a) => (
          <AgentCard key={a.id} a={a} now={now} />
        ))}
      </div>
    </section>
  );
}

function AgentCard({ a, now }: { a: Agent; now: number }) {
  const filter = useStore((s) => s.logFilter);
  const set = useStore((s) => s.set);
  const active = filter === a.id;
  const rank = a.week?.rank;
  return (
    <div
      onClick={() => set({ logFilter: active ? "all" : a.id })}
      className={clsx("bg-panel px-4 pt-3 pb-3 min-w-0 cursor-pointer flex flex-col gap-2", active && "bg-[#11141a]")}
      title={a.about}
    >
      <div className="flex items-center gap-2.5 min-w-0">
        <span className="dot shrink-0" style={{ background: a.color }} />
        <span className="font-semibold text-hi tracking-wide">{a.id}</span>
        <span className="text-mute text-[11px] truncate">{a.role}</span>
        <span className="ml-auto flex items-center gap-2 shrink-0 text-[10.5px]">
          {rank != null && (
            <span
              className={clsx("num px-1.5 py-px rounded border", rank === 1 ? "border-[#e3b35b]/60 text-[#e3b35b]" : "border-line2 text-mute")}
              title="Rank in this week's race (return on the capital it had at the start of the week)"
            >
              #{rank} this week
            </span>
          )}
          <span className="flex items-center gap-1 text-soft" title="Weeks won this season">
            <Trophy size={11} strokeWidth={2} className={a.wins ? "text-[#e3b35b]" : "text-mute"} />
            <span className="num">{a.wins ?? 0}</span>
          </span>
        </span>
      </div>
      <div className="text-[11.5px] text-soft truncate -mt-0.5">{a.mode}</div>
      <div className="grid grid-cols-4 gap-x-3 gap-y-1.5">
        <Stat label="Allocation">
          <span className="flex items-center gap-1.5">
            <span className="w-8 h-[3px] rounded bg-line2 overflow-hidden">
              <span className="block h-full" style={{ width: `${a.weight * 100}%`, background: a.color }} />
            </span>
            <span className="num text-text">{(a.weight * 100).toFixed(0)}%</span>
          </span>
        </Stat>
        <Stat label="Capital">
          <span className="num text-text">{usd(a.capital, { whole: true })}</span>
        </Stat>
        <Stat label="Invested">
          <span className="num text-soft">{(Math.min(a.invested, 9.99) * 100).toFixed(0)}%</span>
        </Stat>
        <Stat label="Fans">
          <span className="num text-soft">{a.fans != null ? `${Math.round(a.fans * 100)}%` : "—"}</span>
        </Stat>
        <Stat label="Today">
          <span className={clsx("num", tone(a.pnl_today))}>{usd(a.pnl_today, { sign: true, whole: Math.abs(a.pnl_today) >= 1000 })}</span>
        </Stat>
        <Stat label="This week">
          <span className={clsx("num", tone(a.week?.pnl ?? 0))}>{a.week ? pct(a.week.ret, 1) : "—"}</span>
        </Stat>
        <Stat label="Month">
          <span className={clsx("num", tone(a.pnl_mtd))}>{usd(a.pnl_mtd, { sign: true, whole: true })}</span>
        </Stat>
        <Stat label="Since start">
          <span className={clsx("num", tone(a.pnl_all))}>{usd(a.pnl_all, { sign: true, whole: true })}</span>
        </Stat>
      </div>
      <Speech a={a} now={now} />
    </div>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-[9.5px] uppercase tracking-[0.08em] text-mute leading-none mb-1">{label}</div>
      <div className="text-[12px] leading-none truncate">{children}</div>
    </div>
  );
}

function Speech({ a, now }: { a: Agent; now: number }) {
  const say = a.say;
  return (
    <div
      className="relative rounded-md border border-line2 bg-white/[0.02] pl-3 pr-2.5 py-2 h-[50px] overflow-hidden"
      style={{ boxShadow: `inset 2px 0 0 ${a.color}` }}
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={say?.text ?? "none"}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.3 }}
          className="flex items-start gap-2 h-full"
        >
          <p className="flex-1 min-w-0 text-[12px] leading-[1.35] text-text line-clamp-2">
            {say ? `“${say.text}”` : <span className="text-mute">…</span>}
          </p>
          {say && <span className="num text-[10px] text-mute shrink-0 pt-px">{ago(say.ts, now)}</span>}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
