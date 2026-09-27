import clsx from "clsx";
import { useStore } from "../store";
import { pct, tone, usd } from "../lib/format";
import type { Account } from "../types";
import { Num } from "./Num";

export function AccountStrip() {
  const a = useStore((s) => s.live?.account);
  if (!a) return null;
  const mix = a.mix;
  return (
    <section
      className={clsx(
        "grid gap-px bg-line border border-line rounded-[10px] overflow-hidden",
        mix ? "grid-cols-[1.35fr_1fr_1fr_1fr_1.25fr]" : "grid-cols-[1.35fr_1fr_1fr_1fr]",
      )}
    >
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
      {mix && <MixCell mix={mix} equity={a.equity} deposits={a.deposits} />}
    </section>
  );
}

/** What-if: the same starting amount with part of it held in BTC. Display only. */
function MixCell({ mix, equity, deposits }: { mix: NonNullable<Account["mix"]>; equity: number; deposits: number }) {
  const hist = useStore((s) => s.history?.mix);
  const ts = useStore((s) => s.live?.ts ?? 0);
  const share = Math.round(mix.btc_share * 100);
  const diff = mix.value - equity;
  const pts: [number, number][] = [...(hist ?? []), [ts, mix.value]];
  return (
    <div
      className="bg-panel px-5 py-4 min-w-0"
      title={
        `What the account would be worth if ${share}% of the starting ${usd(deposits, { whole: true })} had bought BTC ` +
        `instead (one spot fee), ${rebalanceText(mix.rebalance_months, share)}. Display only: it doesn't affect trading.`
      }
    >
      <div className="label mb-2.5">
        {100 - share}% QUORUM + {share}% BTC <span className="normal-case tracking-normal">· what if</span>
      </div>
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          <Num value={mix.value} format={(v) => usd(v)} className="text-[24px] leading-none font-medium text-hi" />
          <div className="mt-2 text-[11px] text-mute whitespace-nowrap">
            <span className={clsx("num", tone(mix.ret))}>{pct(mix.ret)}</span> · <span className={clsx("num", tone(diff))}>{usd(diff, { sign: true })}</span> vs account
          </div>
        </div>
        <Spark pts={pts} up={mix.ret >= 0} />
      </div>
    </div>
  );
}

function rebalanceText(months: number, share: number): string {
  const back = `rebalanced back to ${100 - share}/${share}`;
  if (!months) return "never rebalanced";
  if (months === 12) return `${back} every Jan 1`;
  if (months === 6) return `${back} every Jan 1 and Jul 1`;
  return `${back} every ${months} month${months === 1 ? "" : "s"}`;
}

function Spark({ pts, up }: { pts: [number, number][]; up: boolean }) {
  if (pts.length < 2) return null;
  const W = 110;
  const H = 38;
  const t0 = pts[0][0];
  const span = Math.max(pts[pts.length - 1][0] - t0, 1);
  const vs = pts.map((p) => p[1]);
  const lo = Math.min(...vs);
  const rng = Math.max(...vs) - lo || 1;
  const d = pts
    .map(([t, v], i) => `${i ? "L" : "M"}${(((t - t0) / span) * W).toFixed(1)},${(H - 3 - ((v - lo) / rng) * (H - 6)).toFixed(1)}`)
    .join(" ");
  return (
    <svg width={W} height={H} className="shrink-0 overflow-visible" aria-hidden>
      <path d={d} fill="none" stroke={up ? "var(--color-up)" : "var(--color-down)"} strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
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
