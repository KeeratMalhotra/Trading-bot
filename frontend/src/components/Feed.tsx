import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import {
  ArrowDownRight,
  ArrowUpRight,
  Ban,
  Brain,
  CircleCheck,
  CircleX,
  Coins,
  Lightbulb,
  Radio,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useStore, botColor } from "../store";
import type { BotEvent } from "../types";
import { timeET } from "../lib/format";

const NAMES: Record<string, string> = { low: "SENTINEL", medium: "TACTICIAN", high: "BERSERKER", system: "ARENA" };

function iconFor(ev: BotEvent) {
  switch (ev.kind) {
    case "thought":
      return Brain;
    case "setup":
      return Lightbulb;
    case "pass":
      return Ban;
    case "order":
      return ev.data?.side === "sell" ? ArrowDownRight : ArrowUpRight;
    case "fill":
      return Coins;
    case "open":
      return ArrowUpRight;
    case "stop":
      return ShieldCheck;
    case "partial":
      return Sparkles;
    case "close":
      return ev.level === "good" ? CircleCheck : CircleX;
    case "risk":
      return ShieldAlert;
    default:
      return Radio;
  }
}

const FILTERS = [
  { id: "all", label: "All" },
  { id: "trades", label: "Trades" },
  { id: "low", label: "Sentinel" },
  { id: "medium", label: "Tactician" },
  { id: "high", label: "Berserker" },
];
const TRADE_KINDS = new Set(["order", "fill", "open", "close", "partial", "stop", "risk"]);

export function Feed() {
  const events = useStore((s) => s.events);
  const filter = useStore((s) => s.feedFilter);
  const setS = useStore((s) => s.set);
  const focus = useStore((s) => s.focus);

  const shown = events
    .filter((e) => {
      if (filter === "all") return true;
      if (filter === "trades") return TRADE_KINDS.has(e.kind);
      return e.bot === filter;
    })
    .slice(-70)
    .reverse();

  return (
    <div className="panel flex flex-col min-h-0 h-full">
      <div className="flex items-center justify-between px-4 pt-3 pb-2">
        <div className="flex items-center gap-2">
          <span className="live-dot text-up" />
          <span className="panel-title">Live bot brain</span>
        </div>
        <div className="flex gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              onClick={() => setS({ feedFilter: f.id })}
              className={clsx(
                "text-[10px] font-semibold px-2 py-1 rounded-md transition-colors",
                filter === f.id ? "bg-white/10 text-white" : "text-ink-400 hover:text-ink-200",
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto scroll-thin px-2 pb-2">
        <AnimatePresence initial={false}>
          {shown.map((ev) => (
            <FeedItem key={ev.id} ev={ev} onClick={() => ev.symbol && focus(ev.symbol)} />
          ))}
        </AnimatePresence>
        {!shown.length && <div className="text-center text-sm text-ink-400 py-10">Waiting for the bots to speak…</div>}
      </div>
    </div>
  );
}

function FeedItem({ ev, onClick }: { ev: BotEvent; onClick: () => void }) {
  const Icon = iconFor(ev);
  const col = botColor(ev.bot);
  const loud = ["open", "close", "risk", "partial"].includes(ev.kind);
  const quiet = ev.kind === "thought";
  const conf = typeof ev.data?.confidence === "number" ? (ev.data.confidence as number) : null;
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: -14, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0 }}
      transition={{ type: "spring", stiffness: 380, damping: 32 }}
      onClick={onClick}
      className={clsx(
        "relative flex gap-3 rounded-xl px-3 py-2.5 mb-1 cursor-pointer",
        loud ? "bg-white/[0.045]" : "hover:bg-white/[0.025]",
        ev.level === "bad" && loud && "bg-down/[0.07]",
        ev.level === "good" && loud && "bg-up/[0.06]",
      )}
    >
      <div className="absolute left-0 top-2.5 bottom-2.5 w-[2px] rounded-full" style={{ background: col, opacity: quiet ? 0.35 : 0.9 }} />
      <div
        className={clsx("mt-0.5 grid place-items-center w-7 h-7 shrink-0 rounded-lg")}
        style={{
          background: ev.level === "good" ? "rgba(34,197,94,.12)" : ev.level === "bad" ? "rgba(244,63,94,.12)" : ev.level === "warn" ? "rgba(251,191,36,.10)" : `${col}14`,
          color: ev.level === "good" ? "#22c55e" : ev.level === "bad" ? "#f43f5e" : ev.level === "warn" ? "#fbbf24" : col,
        }}
      >
        <Icon className="w-3.5 h-3.5" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[10px]">
          <span className="font-bold tracking-widest" style={{ color: col }}>
            {NAMES[ev.bot]}
          </span>
          {conf != null && <span className="num text-ink-300 bg-white/5 rounded px-1">{conf}% conf</span>}
          <span className="num text-ink-400 ml-auto">{timeET(ev.ts)}</span>
        </div>
        <div className={clsx("text-[13px] leading-snug mt-0.5", quiet ? "text-ink-200" : "text-white font-semibold")}>{ev.title}</div>
        {ev.text && <div className={clsx("text-[12px] leading-snug mt-0.5", quiet ? "text-ink-400" : "text-ink-300")}>{ev.text}</div>}
      </div>
    </motion.div>
  );
}
