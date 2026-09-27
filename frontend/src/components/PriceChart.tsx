import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useStore } from "../store";
import { chartOptions } from "../lib/chart";
import type { Candle } from "../types";

const prec = (p: number) => (p >= 10 ? 2 : p >= 1 ? 4 : 5);

export function PriceChart({ symbol, tf }: { symbol: string; tf: number }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const vol = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const lines = useRef<Map<string, IPriceLine>>(new Map());
  const levels = useRef<number[]>([]);
  const first = useRef(0);
  const [loaded, setLoaded] = useState(0);
  const candle = useStore((s) => s.candle);
  const fills = useStore((s) => s.fills);
  const live = useStore((s) => s.live);
  const subscribe = useStore((s) => s.subscribe);

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, { ...chartOptions, rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.2 } } });
    candles.current = c.addSeries(CandlestickSeries, {
      upColor: "#3ecf8e",
      downColor: "#f0616d",
      borderVisible: false,
      wickUpColor: "rgba(62,207,142,0.6)",
      wickDownColor: "rgba(240,97,109,0.6)",
      autoscaleInfoProvider: (orig: () => { priceRange: { minValue: number; maxValue: number } } | null) => {
        const r = orig();
        if (!r || !levels.current.length) return r;
        return { ...r, priceRange: { minValue: Math.min(r.priceRange.minValue, ...levels.current), maxValue: Math.max(r.priceRange.maxValue, ...levels.current) } };
      },
    });
    vol.current = c.addSeries(HistogramSeries, { priceScaleId: "v", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    c.priceScale("v").applyOptions({ scaleMargins: { top: 0.86, bottom: 0 } });
    markers.current = createSeriesMarkers(candles.current, []);
    chart.current = c;
    return () => {
      c.remove();
      lines.current.clear();
    };
  }, []);

  useEffect(() => {
    let cancel = false;
    subscribe(symbol, tf);
    fetch(`/api/candles?symbol=${symbol}&tf=${tf}&limit=300`)
      .then((r) => r.json())
      .then((rows: Candle[]) => {
        if (cancel || !candles.current || !vol.current || !rows.length) return;
        const p = prec(rows[rows.length - 1].close);
        candles.current.applyOptions({ priceFormat: { type: "price", precision: p, minMove: 1 / 10 ** p } });
        candles.current.setData(rows.map((c) => ({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close })));
        vol.current.setData(rows.map((c) => ({ time: c.time as UTCTimestamp, value: c.volume, color: c.close >= c.open ? "rgba(62,207,142,0.18)" : "rgba(240,97,109,0.18)" })));
        first.current = rows[0].time;
        chart.current?.timeScale().setVisibleLogicalRange({ from: Math.max(0, rows.length - 150), to: rows.length + 5 });
        for (const l of lines.current.values()) candles.current.removePriceLine(l);
        lines.current.clear();
        setLoaded((n) => n + 1);
      });
    return () => {
      cancel = true;
    };
  }, [symbol, tf, subscribe]);

  useEffect(() => {
    if (!candle || candle.symbol !== symbol || candle.tf !== tf || !candles.current) return;
    const c = candle.c;
    candles.current.update({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close });
    vol.current?.update({ time: c.time as UTCTimestamp, value: c.volume, color: c.close >= c.open ? "rgba(62,207,142,0.18)" : "rgba(240,97,109,0.18)" });
  }, [candle, symbol, tf]);

  const mk = useMemo(() => {
    const out: SeriesMarker<Time>[] = [];
    for (const f of fills) {
      if (f.symbol !== symbol) continue;
      const t = Math.floor(f.ts / tf) * tf;
      out.push({
        time: t as UTCTimestamp,
        position: f.side === "buy" ? "belowBar" : "aboveBar",
        shape: f.side === "buy" ? "arrowUp" : "arrowDown",
        color: f.side === "buy" ? "#3ecf8e" : "#f0616d",
        text: f.venue === "perp" ? `${f.side === "buy" ? "B" : "S"} ${f.contracts}` : f.side === "buy" ? "B" : "S",
      });
    }
    return out.sort((a, b) => (a.time as number) - (b.time as number));
  }, [fills, symbol, tf]);

  useEffect(() => {
    if (!first.current) return;
    markers.current?.setMarkers(mk.filter((m) => (m.time as number) >= first.current));
  }, [mk, loaded]);

  // position levels: average price, and ORACLE stops / targets
  useEffect(() => {
    const s = candles.current;
    if (!s || !live) return;
    const want = new Map<string, { price: number; color: string; title: string; style: LineStyle }>();
    for (const p of live.positions) {
      if (!p.instrument.startsWith(symbol.split("-")[0])) continue;
      want.set(`${p.key}:avg`, { price: p.avg, color: "#8a919c", title: `${p.venue === "perp" ? "PERP" : "SPOT"} ${p.side.toUpperCase()} AVG`, style: LineStyle.Dashed });
    }
    for (const t of live.oracle.open) {
      if (t.symbol !== symbol) continue;
      want.set(`${t.id}:s`, { price: t.stop, color: "#f0616d", title: "ORACLE STOP", style: LineStyle.Dotted });
      want.set(`${t.id}:t`, { price: t.target, color: "#3ecf8e", title: "ORACLE TARGET", style: LineStyle.Dotted });
    }
    levels.current = [...want.entries()].filter(([k]) => !k.endsWith(":avg")).map(([, v]) => v.price);
    for (const [k, l] of lines.current) {
      if (!want.has(k)) {
        s.removePriceLine(l);
        lines.current.delete(k);
      }
    }
    for (const [k, v] of want) {
      const o = { price: v.price, color: v.color, title: v.title, lineStyle: v.style, lineWidth: 1 as const, axisLabelVisible: true };
      const l = lines.current.get(k);
      if (l) l.applyOptions(o);
      else lines.current.set(k, s.createPriceLine(o));
    }
  }, [live, symbol, loaded]);

  return <div ref={el} className="absolute inset-0" />;
}
