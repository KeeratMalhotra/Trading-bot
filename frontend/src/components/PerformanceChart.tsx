import { useEffect, useRef, useState } from "react";
import { AreaSeries, createChart, LineSeries, LineStyle, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import clsx from "clsx";
import { useStore } from "../store";
import { chartOptions } from "../lib/chart";

const RANGES: { id: string; label: string; days: number }[] = [
  { id: "1W", label: "1W", days: 7 },
  { id: "1M", label: "1M", days: 30 },
  { id: "3M", label: "3M", days: 91 },
  { id: "ALL", label: "All", days: 100000 },
];

/** Account value vs holding BTC, both rebased to 0% at the start of the range. */
export function PerformanceChart() {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const team = useRef<ISeriesApi<"Area"> | null>(null);
  const btc = useRef<ISeriesApi<"Line"> | null>(null);
  const history = useStore((s) => s.history);
  const live = useStore((s) => s.live);
  const [range, setRange] = useState("ALL");
  const [stats, setStats] = useState<{ team: number; btc: number } | null>(null);

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, { ...chartOptions, rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.08 } } });
    const fmt = { type: "custom" as const, formatter: (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`, minMove: 0.01 };
    team.current = c.addSeries(AreaSeries, {
      lineColor: "#e8e8e8",
      topColor: "rgba(232,232,232,0.10)",
      bottomColor: "rgba(232,232,232,0.0)",
      lineWidth: 2,
      priceLineVisible: false,
      priceFormat: fmt,
    });
    btc.current = c.addSeries(LineSeries, {
      color: "#5b626d",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      priceFormat: fmt,
    });
    team.current.createPriceLine({ price: 0, color: "rgba(255,255,255,0.10)", lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: false, title: "" });
    chart.current = c;
    return () => c.remove();
  }, []);

  useEffect(() => {
    if (!history || !team.current || !btc.current) return;
    const days = RANGES.find((r) => r.id === range)!.days;
    const now = live?.ts ?? Date.now() / 1000;
    const t0 = now - days * 86400;
    const eq = [...history.equity];
    if (live) eq.push([Math.floor(live.ts), live.account.equity]);
    const e = eq.filter(([t]) => t >= t0);
    const b = history.btc.filter(([t]) => t >= t0);
    if (live) {
      const q = live.quotes.find((x) => x.s === "BTC-USD");
      if (q) b.push([Math.floor(live.ts), q.p]);
    }
    const rebase = (xs: [number, number][]) => {
      if (!xs.length) return [];
      const base = xs[0][1];
      const seen = new Set<number>();
      return xs
        .filter(([t]) => (seen.has(t) ? false : (seen.add(t), true)))
        .map(([t, v]) => ({ time: t as UTCTimestamp, value: (v / base - 1) * 100 }));
    };
    const te = rebase(e);
    const tb = rebase(b);
    team.current.setData(te);
    btc.current.setData(tb);
    chart.current?.timeScale().fitContent();
    setStats(te.length && tb.length ? { team: te[te.length - 1].value, btc: tb[tb.length - 1].value } : null);
  }, [history, range, live?.ts]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="relative h-full">
      <div className="absolute top-2 left-3 z-10 flex items-center gap-4 text-[11px]">
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-[2px] bg-accent" />
          <span className="text-soft">Account</span>
          {stats && <span className={clsx("num", stats.team >= 0 ? "text-up" : "text-down")}>{`${stats.team >= 0 ? "+" : ""}${stats.team.toFixed(2)}%`}</span>}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-0 border-t border-dashed border-mute" />
          <span className="text-soft">BTC buy &amp; hold</span>
          {stats && <span className={clsx("num", stats.btc >= 0 ? "text-up" : "text-down")}>{`${stats.btc >= 0 ? "+" : ""}${stats.btc.toFixed(2)}%`}</span>}
        </span>
      </div>
      <div className="absolute top-1.5 right-16 z-10 flex gap-0.5">
        {RANGES.map((r) => (
          <button
            key={r.id}
            onClick={() => setRange(r.id)}
            className={clsx("num text-[10.5px] px-2 py-0.5 rounded", range === r.id ? "bg-line2 text-hi" : "text-mute hover:text-soft")}
          >
            {r.label}
          </button>
        ))}
      </div>
      <div ref={el} className="absolute inset-0 top-7" />
    </div>
  );
}
