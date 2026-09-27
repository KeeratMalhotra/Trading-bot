import { useEffect, useRef, useState } from "react";
import { createChart, LineSeries, LineStyle, PriceScaleMode, type IChartApi, type ISeriesApi, type Time, type UTCTimestamp } from "lightweight-charts";
import { chartOptions } from "../lib/chart";
import type { Backtest, BacktestStats } from "../types";

const MIX_COLOR = "#e3b35b";
const BTC_COLOR = "#8a919c";
const money = (v: number) => (v >= 1e6 ? `$${(v / 1e6).toFixed(v >= 1e7 ? 1 : 2)}M` : `$${Math.round(v / 1e3)}k`);
const signed = (x: number) => `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(0)}%`;
const mon = new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
const when = (ts: number) => mon.format(new Date(ts * 1000));
const downYears = (s: BacktestStats) => Object.values(s.years).filter((r) => r < 0).length;

/** Hypothetical long backtest: holding BTC alone vs the QUORUM + BTC split (log scale, dollars). */
export function BacktestChart() {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const btcS = useRef<ISeriesApi<"Line"> | null>(null);
  const mixS = useRef<ISeriesApi<"Line"> | null>(null);
  const [bt, setBt] = useState<Backtest | null>(null);

  useEffect(() => {
    fetch("/api/backtest")
      .then((r) => r.json())
      .then(setBt)
      .catch(() => setBt({ available: false } as Backtest));
  }, []);

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      ...chartOptions,
      rightPriceScale: { borderVisible: false, mode: PriceScaleMode.Logarithmic, scaleMargins: { top: 0.14, bottom: 0.05 } },
      timeScale: { ...chartOptions.timeScale, timeVisible: false },
      localization: { timeFormatter: (t: Time) => when(t as number), priceFormatter: money },
    });
    const fmt = { type: "custom" as const, formatter: money, minMove: 1 };
    btcS.current = c.addSeries(LineSeries, { color: BTC_COLOR, lineWidth: 1, lineStyle: LineStyle.Dashed, priceLineVisible: false, priceFormat: fmt });
    mixS.current = c.addSeries(LineSeries, { color: MIX_COLOR, lineWidth: 2, priceLineVisible: false, priceFormat: fmt });
    chart.current = c;
    return () => c.remove();
  }, []);

  useEffect(() => {
    if (!bt?.available || !btcS.current || !mixS.current) return;
    const pts = (xs: [number, number][]) => xs.map(([t, v]) => ({ time: t as UTCTimestamp, value: v }));
    btcS.current.setData(pts(bt.series.btc));
    mixS.current.setData(pts(bt.series.mix));
    chart.current?.timeScale().fitContent();
  }, [bt]);

  const ok = bt?.available;
  const share = ok ? Math.round(bt.btc_share * 100) : 0;
  const name = `${100 - share}% QUORUM + ${share}% BTC`;
  return (
    <div className="relative h-full">
      {ok && (
        <div className="absolute top-2 left-3 right-3 z-10 flex items-start justify-between gap-4 text-[11px]">
          <div className="space-y-1">
            <Legend color={MIX_COLOR} label={`${name}, ${bt.rebalance}`} s={bt.stats.mix} />
            <Legend color={BTC_COLOR} dashed label="BTC buy & hold" s={bt.stats.btc} />
          </div>
          <div className="text-right text-mute leading-snug" title={bt.note}>
            <div className="text-[#e3b35b] tracking-wider text-[10px]">HYPOTHETICAL BACKTEST</div>
            <div>
              {money(bt.balance)} from {when(bt.start)} · not live results
            </div>
          </div>
        </div>
      )}
      <div ref={el} className="absolute inset-x-0 top-12 bottom-6" />
      {ok && bt.crashes.length > 0 && (
        <div className="absolute bottom-1 left-3 right-3 z-10 flex gap-5 text-[10.5px] text-mute truncate">
          <span className="text-soft">When BTC crashed:</span>
          {bt.crashes.map((c) => (
            <span key={c.from} className="num">
              {when(c.from)} – {when(c.to)}: BTC <span className="text-down">{signed(c.btc)}</span> · {name}{" "}
              <span className={c.mix >= 0 ? "text-up" : "text-down"}>{signed(c.mix)}</span>
            </span>
          ))}
        </div>
      )}
      {bt && !ok && (
        <div className="absolute inset-0 grid place-items-center text-[11px] text-mute">
          No backtest file (backend/app/team/backtest_mix.json). See "Longer history" in the README.
        </div>
      )}
    </div>
  );
}

function Legend({ color, dashed, label, s }: { color: string; dashed?: boolean; label: string; s: BacktestStats }) {
  const years = Object.keys(s.years).length;
  return (
    <div className="flex items-center gap-2 whitespace-nowrap">
      {dashed ? <span className="w-3 h-0 border-t border-dashed" style={{ borderColor: color }} /> : <span className="w-3 h-[2px]" style={{ background: color }} />}
      <span className="text-soft">{label}</span>
      <span className="num text-hi">{money(s.final)}</span>
      <span className="text-mute">
        worst drop <span className="num text-down">{signed(-s.max_dd)}</span> · down years{" "}
        <span className="num text-soft">
          {downYears(s)} of {years}
        </span>
      </span>
    </div>
  );
}
