import clsx from "clsx";
import { useEffect, useState } from "react";
import { AGENT_COLOR, useStore } from "../store";
import { left, price, qty, tone, usd } from "../lib/format";
import type { Live, Position } from "../types";

/** One-line exit plan for a position, from the plans of the agents holding it. */
function exitPlan(p: Position, live: Live, now: number): string {
  const sym = p.key.split(":")[1];
  const parts: string[] = [];
  for (const t of live.oracle.open.filter((t) => t.symbol === sym)) {
    parts.push(`Stop ${price(t.stop)} · target ${price(t.target)} · ${left(t.expires, now)}`);
  }
  const others = Object.keys(p.owners).filter((a) => a !== "ORACLE");
  const onlyCopies = others.length === 1 && others[0] === "NOVA" && parts.length > 0;
  if (others.length && !onlyCopies) {
    const reg = live.regime;
    if (sym === "BTC-USD" && p.side === "long" && reg.btc_on && reg.btc_sma200) {
      parts.push(`Trend: exits on a daily close < ${price(reg.btc_sma200)}`);
    } else if (p.venue === "spot" && live.positions.some((x) => x.key === `perp:${sym}` && x.side === "short")) {
      parts.push("Funding carry · hedged with a short perp");
    } else {
      parts.push(`Breakout: exits on a 10-day ${p.side === "long" ? "low" : "high"}`);
    }
  }
  return parts.join("  |  ") || "—";
}

export function Positions() {
  const live = useStore((s) => s.live);
  const set = useStore((s) => s.set);
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 5000);
    return () => clearInterval(t);
  }, []);
  const positions = live?.positions ?? [];
  const total = positions.reduce((a, p) => a + p.pnl, 0);

  return (
    <section className="card flex flex-col min-h-0 h-full">
      <div className="flex items-center justify-between px-4 h-9 border-b border-line shrink-0">
        <span className="label">Positions · {positions.length}</span>
        <span className="text-[11px] text-mute">
          Unrealized <span className={clsx("num", tone(total))}>{usd(total, { sign: true })}</span>
        </span>
      </div>
      <div className="flex-1 min-h-0 overflow-auto scroll">
        <table className="tbl">
          <thead>
            <tr>
              <th>Instrument</th>
              <th>Side</th>
              <th className="!text-right">Size</th>
              <th className="!text-right">Avg price</th>
              <th className="!text-right">Mark</th>
              <th className="!text-right">Value</th>
              <th className="!text-right">Unrealized</th>
              <th className="!text-right">% of acct</th>
              <th>Exit plan</th>
              <th>Held by</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => {
              const ownerTotal = Object.values(p.owners).reduce((a, v) => a + Math.abs(v), 0) || 1;
              return (
                <tr key={p.key} className="cursor-pointer" onClick={() => set({ view: "chart", symbol: p.key.split(":")[1] })}>
                  <td>
                    <div className="text-hi font-medium">{p.instrument}</div>
                    <div className="text-[10.5px] text-mute">{p.venue === "perp" ? `US perpetual-style futures · ${p.product}` : "Spot"}</div>
                  </td>
                  <td className={p.side === "long" ? "text-up" : "text-down"}>{p.side === "long" ? "Long" : "Short"}</td>
                  <td className="text-right num text-text">
                    {p.venue === "perp" ? `${p.contracts} ct` : qty(p.qty)}
                    {p.venue === "perp" && <div className="text-[10.5px] text-mute">{qty(Math.abs(p.qty))}</div>}
                  </td>
                  <td className="text-right num text-soft">{price(p.avg)}</td>
                  <td className="text-right num text-text">{price(p.mark)}</td>
                  <td className="text-right num text-text">{usd(Math.abs(p.value), { whole: true })}</td>
                  <td className={clsx("text-right num", tone(p.pnl))}>
                    {usd(p.pnl, { sign: true })}
                    {p.funding ? <div className="text-[10.5px] text-mute">funding {usd(-p.funding, { sign: true })}</div> : null}
                  </td>
                  <td className="text-right num text-soft">{(p.weight * 100).toFixed(1)}%</td>
                  <td className="num text-[11px] text-soft">{live ? exitPlan(p, live, now) : "—"}</td>
                  <td>
                    <div className="flex h-[3px] w-24 rounded overflow-hidden bg-line2" title={Object.entries(p.owners).map(([a, v]) => `${a} ${((Math.abs(v) / ownerTotal) * 100).toFixed(0)}%`).join(" · ")}>
                      {Object.entries(p.owners).map(([a, v]) => (
                        <div key={a} style={{ width: `${(Math.abs(v) / ownerTotal) * 100}%`, background: AGENT_COLOR[a] }} />
                      ))}
                    </div>
                    <div className="text-[10.5px] text-mute mt-1">{Object.keys(p.owners).join(" · ") || "—"}</div>
                  </td>
                </tr>
              );
            })}
            {!positions.length && (
              <tr>
                <td colSpan={10} className="text-center text-mute py-8">
                  No open positions
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
