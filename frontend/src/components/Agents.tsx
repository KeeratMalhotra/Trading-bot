import clsx from "clsx";
import { useStore } from "../store";
import { pct, tone, usd } from "../lib/format";

export function Agents() {
  const agents = useStore((s) => s.live?.agents ?? []);
  const filter = useStore((s) => s.logFilter);
  const set = useStore((s) => s.set);
  return (
    <section className="card overflow-hidden">
      <div className="flex items-center justify-between px-4 h-9 border-b border-line">
        <span className="label">Desk</span>
        <span className="text-[11px] text-mute">Capital re-allocated monthly by 90-day risk-adjusted return</span>
      </div>
      <table className="tbl">
        <thead>
          <tr>
            <th>Agent</th>
            <th>Now</th>
            <th className="!text-right">Allocation</th>
            <th className="!text-right">Capital</th>
            <th className="!text-right">Invested</th>
            <th className="!text-right">Today</th>
            <th className="!text-right">Month</th>
            <th className="!text-right">Since start</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((a) => (
            <tr
              key={a.id}
              onClick={() => set({ logFilter: filter === a.id ? "all" : a.id })}
              className={clsx("cursor-pointer", filter === a.id && "[&>td]:bg-white/[0.02]")}
              title={a.about}
            >
              <td>
                <div className="flex items-center gap-2.5">
                  <span className="dot" style={{ background: a.color }} />
                  <span className="font-semibold text-hi tracking-wide">{a.id}</span>
                  <span className="text-mute text-[11px]">{a.role}</span>
                </div>
              </td>
              <td className="text-soft max-w-[340px] truncate">{a.mode}</td>
              <td className="text-right">
                <div className="flex items-center justify-end gap-2">
                  <div className="w-16 h-[3px] rounded bg-line2 overflow-hidden">
                    <div className="h-full" style={{ width: `${a.weight * 100}%`, background: a.color }} />
                  </div>
                  <span className="num text-text w-9 text-right">{(a.weight * 100).toFixed(0)}%</span>
                </div>
              </td>
              <td className="text-right num text-text">{usd(a.capital, { whole: true })}</td>
              <td className="text-right num text-soft">{(Math.min(a.invested, 9.99) * 100).toFixed(0)}%</td>
              <td className={clsx("text-right num", tone(a.pnl_today))}>{usd(a.pnl_today, { sign: true })}</td>
              <td className={clsx("text-right num", tone(a.pnl_mtd))}>{usd(a.pnl_mtd, { sign: true })}</td>
              <td className={clsx("text-right num", tone(a.pnl_all))}>
                {usd(a.pnl_all, { sign: true })}
                <span className="text-mute ml-2 text-[11px]">{pct(a.pnl_all / Math.max(a.capital - a.pnl_all, 1), 1)}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
