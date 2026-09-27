import { useEffect, useState } from "react";
import clsx from "clsx";
import { useStore } from "../store";
import { pct, price } from "../lib/format";

const clock = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  weekday: "short",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function TopBar() {
  const live = useStore((s) => s.live);
  const meta = useStore((s) => s.meta);
  const connected = useStore((s) => s.connected);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const ok = connected && live?.market.connected;
  const qs = live?.quotes ?? [];
  const btc = qs.find((q) => q.s === "BTC-USD");
  const eth = qs.find((q) => q.s === "ETH-USD");
  const reg = live?.regime;

  return (
    <header className="h-12 shrink-0 flex items-center gap-6 px-5 border-b border-line">
      <div className="flex items-center gap-2.5">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" className="text-hi">
          <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" />
          <path d="M15 15l4 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
        <span className="text-[13px] font-semibold tracking-[0.22em] text-hi">QUORUM</span>
        <span className="text-mute text-[11px] ml-1">{meta?.account ?? ""}</span>
        <span
          className="text-[10px] tracking-wider text-soft border border-line2 rounded px-1.5 py-px"
          title="Simulated orders on live Coinbase prices. No real money."
        >
          PAPER TRADING
        </span>
      </div>
      <div className="flex items-center gap-5 text-[12px]">
        {[btc, eth].filter(Boolean).map((q) => (
          <span key={q!.s} className="flex items-baseline gap-1.5">
            <span className="text-mute">{q!.s.split("-")[0]}</span>
            <span className="num text-text">{price(q!.p)}</span>
            <span className={clsx("num text-[11px]", q!.chg >= 0 ? "text-up" : "text-down")}>{pct(q!.chg)}</span>
          </span>
        ))}
        {reg?.btc_on != null && (
          <span className="flex items-baseline gap-1.5">
            <span className="text-mute">Regime</span>
            <span className={reg.btc_on ? "text-up" : "text-down"}>{reg.btc_on ? "Bull" : "Risk-off"}</span>
          </span>
        )}
        {reg?.fear_greed != null && (
          <span className="flex items-baseline gap-1.5">
            <span className="text-mute">Fear &amp; Greed</span>
            <span className="num text-text">{Math.round(reg.fear_greed)}</span>
          </span>
        )}
      </div>
      {live && !live.engine.desk_ready && (
        <span className="text-[11px] text-[#e3b35b]">Warming up · {live.engine.forecaster}</span>
      )}
      {live && live.engine.desk_ready && live.engine.paused && (
        <span className="text-[11px] text-[#e3b35b]">Trading paused · waiting for live prices</span>
      )}
      <div className="ml-auto flex items-center gap-5 text-[12px]">
        <span className="flex items-center gap-2 text-soft">
          <span className={clsx("dot", ok ? "bg-up" : "bg-down")} />
          {ok ? "Live prices · Coinbase" : connected ? live?.market.message || "Reconnecting to Coinbase" : "Reconnecting"}
        </span>
        <span className="num text-soft">{clock.format(now)} ET</span>
      </div>
    </header>
  );
}
