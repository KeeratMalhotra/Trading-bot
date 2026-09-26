import clsx from "clsx";
import { motion } from "motion/react";
import { BrainCircuit, ShieldAlert } from "lucide-react";
import { useStore } from "../store";
import { coin, timeShortET } from "../lib/format";

/** ORACLE's live forecast for every coin: expected outcome of a 14-day trade, in R. */
export function OracleRadar() {
  const o = useStore((s) => s.live?.oracle);
  const positions = useStore((s) => s.live?.positions ?? []);
  const focus = useStore((s) => s.focus);
  if (!o) return <div className="text-center text-sm text-ink-400 py-8">ORACLE is offline.</div>;

  const held = new Set(positions.filter((p) => p.bot === "oracle").map((p) => p.symbol));
  const preds = o.predictions ?? [];
  const lo = Math.min(-1, ...preds.map((p) => p.pred));
  const hi = Math.max(1, o.threshold ?? 0, ...preds.map((p) => p.pred));
  const x = (v: number) => ((v - lo) / (hi - lo)) * 100;
  const tr = o.track_record;

  return (
    <div className="px-1.5 pb-1">
      <div className="flex items-center gap-2 px-1 pb-2">
        <BrainCircuit className="w-4 h-4 text-[#a78bfa]" />
        <div className="text-[11px] text-ink-300 leading-tight">
          {o.ready ? (
            <>
              14-day forecasts{o.bar_t ? ` for the ${timeShortET(o.bar_t + 3600)} close` : ""} · trades only in the top 10%
            </>
          ) : (
            o.status
          )}
        </div>
      </div>

      {o.ready && o.regime === false && (
        <div className="flex items-start gap-2 rounded-lg bg-medium/10 border border-medium/20 px-2.5 py-2 mb-2 text-[11px] text-medium">
          <ShieldAlert className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>Risk-off: BTC is below its 200-day average. In testing ORACLE only had an edge in bull markets, so it's standing aside.</span>
        </div>
      )}

      <div className="space-y-1">
        {preds.map((p) => {
          const pass = o.regime && p.pred >= (o.threshold ?? Infinity);
          return (
            <div
              key={p.symbol}
              onClick={() => focus(p.symbol)}
              className="grid grid-cols-[46px_1fr_52px] items-center gap-2 px-1.5 py-1 rounded-lg hover:bg-white/[0.03] cursor-pointer"
              title={p.reasons.join(" · ")}
            >
              <span className="text-[12px] font-semibold text-white flex items-center gap-1">
                {coin(p.symbol)}
                {held.has(p.symbol) && <span className="w-1.5 h-1.5 rounded-full bg-[#a78bfa]" title="ORACLE holds this" />}
              </span>
              <div className="relative h-3 rounded bg-white/[0.04]">
                <div className="absolute top-0 bottom-0 w-px bg-white/20" style={{ left: `${x(0)}%` }} />
                <motion.div
                  className={clsx("absolute top-0.5 bottom-0.5 rounded-sm", pass ? "bg-[#a78bfa]" : p.pred >= 0 ? "bg-up/50" : "bg-down/50")}
                  animate={{ left: `${Math.min(x(0), x(p.pred))}%`, width: `${Math.abs(x(p.pred) - x(0))}%` }}
                  transition={{ type: "spring", stiffness: 160, damping: 22 }}
                />
                {o.threshold != null && Number.isFinite(o.threshold) && (
                  <div className="absolute -top-0.5 -bottom-0.5 w-[2px] bg-[#a78bfa]" style={{ left: `${x(o.threshold)}%` }} title="Trade bar" />
                )}
              </div>
              <span className={clsx("num text-[11px] text-right", pass ? "text-[#c4b5fd] font-semibold" : p.pred >= 0 ? "text-ink-200" : "text-ink-400")}>
                {p.pred >= 0 ? "+" : ""}
                {p.pred.toFixed(2)}R
              </span>
            </div>
          );
        })}
      </div>
      {o.ready && o.threshold != null && Number.isFinite(o.threshold) && (
        <div className="flex items-center gap-2 px-1.5 pt-1.5 text-[10px] text-ink-400">
          <span className="w-2.5 h-[2px] bg-[#a78bfa]" /> trade bar {o.threshold >= 0 ? "+" : ""}
          {o.threshold.toFixed(2)}R · 1R = the planned risk (stop 2× daily volatility)
        </div>
      )}

      {tr && (
        <div className="mt-3 rounded-xl bg-white/[0.03] p-2.5">
          <div className="panel-title !text-[10px] mb-1.5">Honest track record (walk-forward, real engine, after fees)</div>
          <div className="grid grid-cols-2 gap-2">
            {[tr.dev, tr.holdout].map((t, i) => (
              <div key={i} className="rounded-lg bg-black/20 p-2">
                <div className="text-[9px] text-ink-400">{i === 0 ? "Development" : "Unseen holdout"}</div>
                <div className="text-[9px] text-ink-400">{t.period}</div>
                <div className="flex items-baseline gap-1.5 mt-1">
                  <span className={clsx("num text-sm font-semibold", t.return_pct >= 0 ? "text-up" : "text-down")}>
                    {t.return_pct >= 0 ? "+" : ""}
                    {t.return_pct}%
                  </span>
                  <span className="num text-[10px] text-ink-400">vs HODL {t.hodl_return_pct >= 0 ? "+" : ""}{t.hodl_return_pct}%</span>
                </div>
                <div className="num text-[9px] text-ink-400">
                  max drop {t.max_dd_pct}% (HODL {t.hodl_max_dd_pct}%) · {t.trades} trades · {Math.round(t.win_rate * 100)}% wins
                </div>
              </div>
            ))}
          </div>
          <div className="text-[10px] text-ink-400 mt-1.5 leading-snug">{tr.caveat}</div>
        </div>
      )}
    </div>
  );
}
