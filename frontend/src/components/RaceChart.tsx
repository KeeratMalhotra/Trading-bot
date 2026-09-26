import { useEffect, useRef } from "react";
import { createChart, LineSeries, LineStyle, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import clsx from "clsx";
import { useStore } from "../store";
import { baseChartOptions } from "../lib/chart";
import { pct, tone } from "../lib/format";

/** The "race": % return of every bot over time. HODL is the dashed benchmark line. */
export function RaceChart() {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<Record<string, ISeriesApi<"Line">>>({});
  const lastT = useRef<Record<string, number>>({});
  const equity = useStore((s) => s.equity);
  const live = useStore((s) => s.live);
  const start = useStore((s) => s.meta?.starting_balance ?? 10000);
  const botKey = (live?.bots ?? []).map((b) => b.id).join(",");
  const zeroLine = useRef(false);

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      ...baseChartOptions,
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.18, bottom: 0.12 } },
    });
    chart.current = c;
    return () => {
      c.remove();
      chart.current = null;
      series.current = {};
    };
  }, []);

  // one line per bot (created when the bot list is known)
  useEffect(() => {
    const c = chart.current;
    const bots = useStore.getState().live?.bots ?? [];
    if (!c || !bots.length) return;
    for (const b of bots) {
      if (series.current[b.id]) continue;
      series.current[b.id] = c.addSeries(LineSeries, {
        color: b.color,
        lineWidth: b.benchmark ? 2 : 3,
        lineStyle: b.benchmark ? LineStyle.Dashed : LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerRadius: 4,
        title: b.benchmark ? "HODL" : "",
        priceFormat: { type: "custom", formatter: (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`, minMove: 0.01 },
      });
    }
    const first = Object.values(series.current)[0];
    if (zeroLine.current || !first) return;
    zeroLine.current = true;
    first.createPriceLine({ price: 0, color: "rgba(255,255,255,0.18)", lineStyle: LineStyle.Dotted, lineWidth: 1, axisLabelVisible: false, title: "" });
  }, [botKey]);

  // full history
  useEffect(() => {
    for (const [id, s] of Object.entries(series.current)) {
      const rows = equity[id] ?? [];
      const seen = new Set<number>();
      const data = rows
        .filter(([t]) => (seen.has(t) ? false : (seen.add(t), true)))
        .map(([t, e]) => ({ time: t as UTCTimestamp, value: (e / start - 1) * 100 }));
      s.setData(data);
      lastT.current[id] = data.length ? (data[data.length - 1].time as number) : 0;
    }
    chart.current?.timeScale().fitContent();
  }, [equity, start, botKey]);

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
      <div className="absolute top-2 left-3 right-24 z-10 flex flex-wrap gap-1.5">
        {bots.map((b) => (
          <div
            key={b.id}
            className={clsx(
              "flex items-center gap-1.5 rounded-lg bg-ink-950/75 border px-2 py-1 backdrop-blur",
              b.benchmark ? "border-dashed border-white/15" : "border-white/5",
            )}
          >
            <span className="num text-[10px] text-ink-400">#{b.rank}</span>
            <span className="w-2 h-2 rounded-full" style={{ background: b.color }} />
            <span className="text-[11px] font-semibold text-white tracking-wider">{b.name}</span>
            <span className={clsx("num text-[11px]", tone(b.total_return))}>{pct(b.total_return)}</span>
          </div>
        ))}
      </div>
      {!Object.values(equity).some((r) => r.length > 1) && (
        <div className="absolute inset-0 grid place-items-center pointer-events-none">
          <div className="text-center text-ink-400 text-sm">The race just started. Equity curves draw in as the bots trade.</div>
        </div>
      )}
    </div>
  );
}
