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
import { useStore, botColor, botName } from "../store";
import { baseChartOptions } from "../lib/chart";
import type { Candle } from "../types";
import { usd } from "../lib/format";

function precisionFor(p: number) {
  if (p >= 1000) return 2;
  if (p >= 10) return 2;
  if (p >= 1) return 4;
  return 5;
}

export function PriceChart({ symbol, tf }: { symbol: string; tf: number }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volume = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const lines = useRef<Map<string, IPriceLine>>(new Map());
  const range = useRef<{ first: number; last: number }>({ first: 0, last: 0 });
  const [loaded, setLoaded] = useState(0);
  const levels = useRef<number[]>([]);

  const candle = useStore((s) => s.candle);
  const trades = useStore((s) => s.trades);
  const positions = useStore((s) => s.live?.positions ?? []);
  const subscribe = useStore((s) => s.subscribe);

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      ...baseChartOptions,
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.22 } },
    });
    chart.current = c;
    candles.current = c.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#f43f5e",
      borderVisible: false,
      wickUpColor: "rgba(34,197,94,0.7)",
      wickDownColor: "rgba(244,63,94,0.7)",
      // keep every open trade's entry / stop / target on screen
      autoscaleInfoProvider: (orig: () => { priceRange: { minValue: number; maxValue: number } } | null) => {
        const r = orig();
        const lv = levels.current;
        if (!r || !lv.length) return r;
        return { ...r, priceRange: { minValue: Math.min(r.priceRange.minValue, ...lv), maxValue: Math.max(r.priceRange.maxValue, ...lv) } };
      },
    });
    volume.current = c.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    c.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
    markers.current = createSeriesMarkers(candles.current, []);
    return () => {
      c.remove();
      chart.current = null;
      lines.current.clear();
    };
  }, []);

  // load history on symbol/timeframe change
  useEffect(() => {
    let cancelled = false;
    subscribe(symbol, tf);
    fetch(`/api/candles?symbol=${symbol}&tf=${tf}&limit=300`)
      .then((r) => r.json())
      .then((rows: Candle[]) => {
        if (cancelled || !candles.current || !volume.current) return;
        const last = rows[rows.length - 1]?.close ?? 1;
        const prec = precisionFor(last);
        candles.current.applyOptions({ priceFormat: { type: "price", precision: prec, minMove: 1 / 10 ** prec } });
        candles.current.setData(rows.map((c) => ({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close })));
        volume.current.setData(
          rows.map((c) => ({
            time: c.time as UTCTimestamp,
            value: c.volume,
            color: c.close >= c.open ? "rgba(34,197,94,0.25)" : "rgba(244,63,94,0.25)",
          })),
        );
        range.current = { first: rows[0]?.time ?? 0, last: rows[rows.length - 1]?.time ?? 0 };
        chart.current?.timeScale().setVisibleLogicalRange({ from: Math.max(0, rows.length - 140), to: rows.length + 6 });
        for (const l of lines.current.values()) candles.current.removePriceLine(l);
        lines.current.clear();
        setLoaded((n) => n + 1);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol, tf, subscribe]);

  // live candle
  useEffect(() => {
    if (!candle || candle.symbol !== symbol || candle.tf !== tf || !candles.current) return;
    const c = candle.c;
    if (c.time < range.current.last) return;
    range.current.last = c.time;
    candles.current.update({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close });
    volume.current?.update({
      time: c.time as UTCTimestamp,
      value: c.volume,
      color: c.close >= c.open ? "rgba(34,197,94,0.25)" : "rgba(244,63,94,0.25)",
    });
  }, [candle, symbol, tf]);

  // trade markers
  const markerData = useMemo(() => {
    const bucket = (ts: number) => Math.floor(ts / tf) * tf;
    const out: SeriesMarker<Time>[] = [];
    for (const t of trades) {
      if (t.symbol !== symbol) continue;
      const col = botColor(t.bot);
      out.push({ time: bucket(t.opened) as UTCTimestamp, position: "belowBar", shape: "arrowUp", color: col, text: `${botName(t.bot)[0]} BUY` });
      out.push({
        time: bucket(t.closed) as UTCTimestamp,
        position: "aboveBar",
        shape: "arrowDown",
        color: t.net >= 0 ? "#22c55e" : "#f43f5e",
        text: `${botName(t.bot)[0]} ${usd(t.net, { sign: true })}`,
      });
    }
    for (const p of positions) {
      if (p.symbol !== symbol || p.status === "opening" || p.bot === "hodl") continue;
      out.push({ time: bucket(p.opened) as UTCTimestamp, position: "belowBar", shape: "arrowUp", color: botColor(p.bot), text: `${botName(p.bot)[0]} BUY` });
    }
    return out.sort((a, b) => (a.time as number) - (b.time as number));
  }, [trades, positions, symbol, tf]);

  useEffect(() => {
    const { first } = range.current;
    if (!first) return;
    markers.current?.setMarkers(markerData.filter((m) => (m.time as number) >= first));
  }, [markerData, loaded, symbol]);

  // position price lines (entry / stop / target)
  useEffect(() => {
    const s = candles.current;
    if (!s) return;
    const want = new Map<string, { price: number; color: string; title: string; style: LineStyle }>();
    for (const p of positions) {
      if (p.symbol !== symbol) continue;
      const n = botName(p.bot);
      want.set(`${p.id}:e`, { price: p.entry, color: botColor(p.bot), title: `${n} ENTRY`, style: LineStyle.Dashed });
      want.set(`${p.id}:s`, { price: p.stop, color: "#f43f5e", title: `${n} ${p.trailing ? "TRAIL" : "STOP"}`, style: LineStyle.Dotted });
      if (p.target != null) want.set(`${p.id}:t`, { price: p.target, color: "#22c55e", title: `${n} TARGET`, style: LineStyle.Dotted });
      if (p.hard_stop != null) want.set(`${p.id}:h`, { price: p.hard_stop, color: "rgba(244,63,94,0.45)", title: `${n} EMERGENCY`, style: LineStyle.Dotted });
    }
    for (const [k, l] of lines.current) {
      if (!want.has(k)) {
        s.removePriceLine(l);
        lines.current.delete(k);
      }
    }
    levels.current = [...want.entries()].filter(([k]) => !k.endsWith(":h")).map(([, v]) => v.price);
    for (const [k, v] of want) {
      const opts = { price: v.price, color: v.color, title: v.title, lineStyle: v.style, lineWidth: 1 as const, axisLabelVisible: true };
      const l = lines.current.get(k);
      if (l) l.applyOptions(opts);
      else lines.current.set(k, s.createPriceLine(opts));
    }
  }, [positions, symbol, loaded]);

  return <div ref={el} className="absolute inset-0" />;
}
