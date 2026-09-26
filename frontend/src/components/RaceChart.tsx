import { useEffect, useRef } from "react";
import { createChart, LineSeries, LineStyle, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import clsx from "clsx";
import { useStore } from "../store";
import { baseChartOptions } from "../lib/chart";
import { pct, tone, usd } from "../lib/format";

const ORDER = ["low", "medium", "high"] as const;
const COLORS: Record<string, string> = { low: "#34d399", medium: "#fbbf24", high: "#f43f5e" };

/** The "race": % return of every bot over time. */
export function RaceChart() {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<Record<string, ISeriesApi<"Line">>>({});
  const lastT = useRef<Record<string, number>>({});
  const equity = useStore((s) => s.equity);
  const live = useStore((s) => s.live);
  const start = useStore((s) => s.meta?.starting_balance ?? 10000);

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      ...baseChartOptions,
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.15, bottom: 0.15 } },
    });
    chart.current = c;
    for (const id of ORDER) {
      series.current[id] = c.addSeries(LineSeries, {
        color: COLORS[id],
        lineWidth: 3,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerRadius: 4,
        priceFormat: { type: "custom", formatter: (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`, minMove: 0.01 },
      });
    }
    series.current.low.createPriceLine({ price: 0, color: "rgba(255,255,255,0.18)", lineStyle: LineStyle.Dotted, lineWidth: 1, axisLabelVisible: false, title: "" });
    return () => {
      c.remove();
      chart.current = null;
      series.current = {};
    };
  }, []);

  // full history
  useEffect(() => {
    for (const id of ORDER) {
      const s = series.current[id];
      const rows = equity[id] ?? [];
      if (!s) continue;
      const seen = new Set<number>();
      const data = rows
        .filter(([t]) => (seen.has(t) ? false : (seen.add(t), true)))
        .map(([t, e]) => ({ time: t as UTCTimestamp, value: (e / start - 1) * 100 }));
      s.setData(data);
      lastT.current[id] = data.length ? (data[data.length - 1].time as number) : 0;
    }
    chart.current?.timeScale().fitContent();
  }, [equity, start]);

  // live tail
  useEffect(() => {
    if (!live) return;
    const t = Math.floor(live.ts / 5) * 5;
    for (const b of live.bots) {
      const s = series.current[b.id];
      if (!s || t < (lastT.current[b.id] ?? 0)) continue;
      s.update({ time: t as UTCTimestamp, value: (b.equity / start - 1) * 100 });
      lastT.current[b.id] = t;
    }
  }, [live, start]);

  const bots = [...(live?.bots ?? [])].sort((a, b) => a.rank - b.rank);
  return (
    <div className="relative h-full w-full">
      <div ref={el} className="absolute inset-0" />
      <div className="absolute top-2 left-3 z-10 flex gap-2">
        {bots.map((b) => (
          <div key={b.id} className="flex items-center gap-2 rounded-lg bg-ink-950/70 border border-white/5 px-2.5 py-1.5 backdrop-blur">
            <span className="num text-[11px] text-ink-400">#{b.rank}</span>
            <span className="w-2.5 h-2.5 rounded-full" style={{ background: b.color }} />
            <span className="text-xs font-semibold text-white tracking-wider">{b.name}</span>
            <span className={clsx("num text-xs", tone(b.total_return))}>{pct(b.total_return)}</span>
            <span className="num text-[11px] text-ink-400">{usd(b.equity, { compact: true })}</span>
          </div>
        ))}
      </div>
      {!Object.values(equity).some((r) => r.length > 1) && (
        <div className="absolute inset-0 grid place-items-center pointer-events-none">
          <div className="text-center text-ink-400 text-sm">
            The race just started. Equity curves will draw in as the bots trade.
          </div>
        </div>
      )}
    </div>
  );
}
