import { useEffect } from "react";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { CircleCheck, CircleX, OctagonAlert, Rocket, Sparkles } from "lucide-react";
import { useStore, botColor, botName, type Toast } from "../store";

/** Big, stream-friendly trade announcements. */
export function Toasts() {
  const toasts = useStore((s) => s.toasts);
  return (
    <div className="pointer-events-none fixed top-24 left-1/2 -translate-x-1/2 z-50 flex flex-col items-center gap-2 w-[560px] max-w-[92vw]">
      <AnimatePresence>
        {toasts.map((t) => (
          <ToastCard key={t.id} t={t} />
        ))}
      </AnimatePresence>
    </div>
  );
}

function ToastCard({ t }: { t: Toast }) {
  const dismiss = useStore((s) => s.dismissToast);
  const ev = t.ev;
  useEffect(() => {
    const id = setTimeout(() => dismiss(t.id), ev.kind === "risk" ? 9000 : 6500);
    return () => clearTimeout(id);
  }, [t.id, dismiss, ev.kind]);
  const col = botColor(ev.bot);
  const good = ev.level === "good";
  const bad = ev.level === "bad";
  const Icon = ev.kind === "open" ? Rocket : ev.kind === "partial" ? Sparkles : ev.kind === "risk" ? OctagonAlert : good ? CircleCheck : CircleX;
  const accent = ev.kind === "open" ? col : good ? "#22c55e" : bad ? "#f43f5e" : "#fbbf24";

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: -30, scale: 0.9 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, scale: 0.95, transition: { duration: 0.25 } }}
      transition={{ type: "spring", stiffness: 420, damping: 28 }}
      className="pointer-events-auto w-full rounded-2xl border bg-ink-900/90 backdrop-blur-xl shadow-2xl shadow-black/60 overflow-hidden"
      style={{ borderColor: `${accent}55` }}
      onClick={() => dismiss(t.id)}
    >
      <div className="h-[3px]" style={{ background: `linear-gradient(90deg, ${col}, ${accent})` }} />
      <div className="flex items-center gap-3 px-4 py-3">
        <div className="grid place-items-center w-10 h-10 rounded-xl" style={{ background: `${accent}22`, color: accent }}>
          <Icon className="w-5 h-5" />
        </div>
        <div className="min-w-0">
          <div className="text-[10px] font-bold tracking-[0.2em]" style={{ color: col }}>
            {botName(ev.bot)}
          </div>
          <div className={clsx("text-base font-bold text-white leading-tight")}>{ev.title}</div>
          <div className="text-xs text-ink-300 line-clamp-2">{ev.text}</div>
        </div>
      </div>
    </motion.div>
  );
}
