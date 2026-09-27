import clsx from "clsx";
import { useStore } from "../store";
import { pct, tone, usd } from "../lib/format";
import { Num } from "./Num";

export function AccountStrip() {
  const a = useStore((s) => s.live?.account);
  if (!a) return null;
  const ex = a.exposure;
  return (
    <section className="grid grid-cols-[1.35fr_1fr_1fr_1fr_1.2fr] gap-px bg-line border border-line rounded-[10px] overflow-hidden">
      <Cell label="Net liquidation value">
        <Num value={a.equity} format={(v) => usd(v)} className="text-[30px] leading-none font-medium text-hi" />
        <div className="mt-2 text-[11px] text-mute">
          Cash <span className="num text-soft">{usd(a.cash)}</span>
          <span className="mx-1.5">·</span>
          Margin in use <span className="num text-soft">{usd(a.margin)}</span>
        </div>
      </Cell>
      <Cell label={`${a.month} · month to date`}>
        <div className={clsx("num text-[24px] leading-none font-medium", tone(a.pnl_mtd))}>{usd(a.pnl_mtd, { sign: true })}</div>
        <div className="mt-2 text-[11px] text-mute">
          <span className={clsx("num", tone(a.ret_mtd))}>{pct(a.ret_mtd)}</span> · {a.days_left} day{a.days_left === 1 ? "" : "s"} left
        </div>
      </Cell>
      <Cell label="Today">
        <div className={clsx("num text-[24px] leading-none font-medium", tone(a.pnl_today))}>{usd(a.pnl_today, { sign: true })}</div>
        <div className="mt-2 text-[11px] text-mute">
          <span className={clsx("num", tone(a.pnl_today))}>{pct(a.pnl_today / Math.max(a.equity - a.pnl_today, 1))}</span> since
          midnight ET
        </div>
      </Cell>
      <Cell label="Since inception">
        <div className={clsx("num text-[24px] leading-none font-medium", tone(a.pnl_all))}>{usd(a.pnl_all, { sign: true })}</div>
        <div className="mt-2 text-[11px] text-mute">
          <span className={clsx("num", tone(a.ret_all))}>{pct(a.ret_all)}</span> on {usd(a.deposits, { whole: true })}
        </div>
      </Cell>
      <Cell label="Exposure">
        <div className="flex items-baseline gap-3">
          <span className={clsx("num text-[24px] leading-none font-medium", ex.net >= 0 ? "text-hi" : "text-down")}>
            {ex.net >= 0 ? "+" : "−"}
            {Math.abs(ex.net * 100).toFixed(0)}%
          </span>
          <span className="text-[11px] text-mute">net</span>
        </div>
        <div className="mt-2 flex items-center gap-2 text-[11px] text-mute">
          <span className="num text-soft">{(ex.long * 100).toFixed(0)}%</span> long
          <span className="num text-soft">{(ex.short * 100).toFixed(0)}%</span> short
          <span className="num text-soft">{(ex.gross * 100).toFixed(0)}%</span> gross
        </div>
      </Cell>
    </section>
  );
}

function Cell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="bg-panel px-5 py-4 min-w-0">
      <div className="label mb-2.5">{label}</div>
      {children}
    </div>
  );
}
