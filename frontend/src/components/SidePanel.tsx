import { useState } from "react";
import clsx from "clsx";
import { Download } from "lucide-react";
import { useStore, botColor, botName } from "../store";
import { coin, pct, timeShortET, tone, usd } from "../lib/format";
import { OracleRadar } from "./OracleRadar";

export function SidePanel() {
  const [tab, setTab] = useState<"oracle" | "trades" | "tax">("oracle");
  return (
    <div className="panel flex flex-col min-h-0 h-full">
      <div className="flex items-center gap-1 px-3 pt-2.5 pb-1.5">
        {(["oracle", "trades", "tax"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={clsx(
              "panel-title px-2 py-1 rounded-md transition-colors",
              tab === t ? "!text-white bg-white/10" : "hover:!text-ink-200",
            )}
          >
            {t === "oracle" ? "Oracle forecasts" : t === "trades" ? "Closed trades" : "Taxes & fees"}
          </button>
        ))}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto scroll-thin px-2 pb-2">
        {tab === "oracle" ? <OracleRadar /> : tab === "trades" ? <ClosedTrades /> : <TaxPanel />}
      </div>
    </div>
  );
}

function ClosedTrades() {
  const trades = useStore((s) => s.trades);
  const focus = useStore((s) => s.focus);
  if (!trades.length)
    return <div className="text-center text-sm text-ink-400 py-8">No closed trades yet. Every exit will land here with fees and tax.</div>;
  return (
    <div>
      {trades.slice(0, 60).map((t) => (
        <div
          key={t.id}
          onClick={() => focus(t.symbol)}
          className="grid grid-cols-[1fr_auto] gap-2 px-2.5 py-2 rounded-xl hover:bg-white/[0.03] cursor-pointer"
        >
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: botColor(t.bot) }} />
              <span className="text-[10px] font-bold tracking-wider" style={{ color: botColor(t.bot) }}>
                {botName(t.bot)}
              </span>
              <span className="text-sm font-semibold text-white">{coin(t.symbol)}</span>
              <span className="num text-[10px] text-ink-400">{timeShortET(t.closed)}</span>
            </div>
            <div className="text-[11px] text-ink-400 truncate">
              {t.strategy} · {t.reason_label} · fees {usd(t.fees)}
            </div>
          </div>
          <div className="text-right">
            <div className={clsx("num text-sm font-semibold", tone(t.net))}>{usd(t.net, { sign: true })}</div>
            <div className="num text-[10px] text-ink-400">
              {t.r >= 0 ? "+" : ""}
              {t.r.toFixed(2)}R · after tax <span className={tone(t.after_tax)}>{usd(t.after_tax, { sign: true })}</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function TaxPanel() {
  const bots = useStore((s) => s.live?.bots ?? []);
  const meta = useStore((s) => s.meta);
  const tax = meta?.tax;
  return (
    <div className="px-1.5">
      {tax && (
        <div className="text-[11px] text-ink-400 px-1 pb-2 leading-snug">
          US estimate · {tax.filing_status === "mfj" ? "Married filing jointly" : "Single"} · {usd(tax.other_income, { compact: true })} other
          income · {tax.state_name} {tax.state_rate ? `(${pct(tax.state_rate, 2, false)})` : ""}. Short-term gains are taxed as ordinary
          income. Change in settings.
        </div>
      )}
      <div className="space-y-2">
        {bots.filter((b) => !b.benchmark).map((b) => (
          <div key={b.id} className="rounded-xl bg-white/[0.03] p-3">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-bold tracking-wider" style={{ color: b.color }}>
                {b.name}
              </span>
              <a
                href={`/api/export/${b.id}.csv`}
                className="flex items-center gap-1 text-[10px] text-ink-400 hover:text-white"
                title="Form 8949-style disposal report (FIFO)"
              >
                <Download className="w-3 h-3" /> 8949 CSV
              </a>
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 mt-2 text-[12px]">
              <Line label="Realized P&L (net of fees)" value={usd(b.all.realized, { sign: true })} cls={tone(b.all.realized)} />
              <Line label="Fees paid" value={usd(b.all.fees)} />
              <Line label="Slippage" value={usd(b.all.slippage)} />
              <Line label="Federal" value={usd(b.tax.federal)} />
              <Line label={`State (${pct(b.tax.state_rate, 1, false)})`} value={usd(b.tax.state)} />
              <Line label="NIIT 3.8%" value={usd(b.tax.niit)} />
              <Line label="Est. tax YTD" value={usd(b.tax.total)} cls="text-medium font-semibold" />
              <Line
                label="After-tax profit"
                value={usd(b.all.realized - b.tax.total, { sign: true })}
                cls={clsx("font-semibold", tone(b.all.realized - b.tax.total))}
              />
            </div>
            {b.tax.total < 0 && <div className="text-[10px] text-ink-400 mt-1">Net loss: deductible up to $3,000/yr against income, rest carries forward.</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

function Line({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-ink-400 truncate">{label}</span>
      <span className={clsx("num", cls ?? "text-ink-200")}>{value}</span>
    </div>
  );
}
