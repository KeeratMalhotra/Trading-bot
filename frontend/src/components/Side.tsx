import clsx from "clsx";
import { useState } from "react";
import { useStore } from "../store";
import { ago, left, pct, price, tone, usd } from "../lib/format";

/** Right column: monthly calendar, ORACLE forecasts, news, costs & tax. */
export function Side() {
  const [tab, setTab] = useState<"calendar" | "forecasts" | "news" | "costs">("calendar");
  const tabs = [
    ["calendar", "Calendar"],
    ["forecasts", "Forecasts"],
    ["news", "News"],
    ["costs", "Costs & tax"],
  ] as const;
  return (
    <section className="card flex flex-col min-h-0 h-full">
      <div className="flex items-center gap-1 px-3 h-9 border-b border-line shrink-0">
        {tabs.map(([id, label]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={clsx("text-[11px] px-2 py-0.5 rounded", tab === id ? "bg-line2 text-hi" : "text-mute hover:text-soft")}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto scroll">
        {tab === "calendar" && <Calendar />}
        {tab === "forecasts" && <Forecasts />}
        {tab === "news" && <NewsList />}
        {tab === "costs" && <Costs />}
      </div>
    </section>
  );
}

function Calendar() {
  const h = useStore((s) => s.history);
  const live = useStore((s) => s.live);
  if (!h || !live) return null;
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date(live.ts * 1000));
  const [y, m] = today.split("-").map(Number);
  const first = new Date(Date.UTC(y, m - 1, 1));
  const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const lead = first.getUTCDay();
  const cells: (string | null)[] = [...Array(lead).fill(null)];
  for (let d = 1; d <= days; d++) cells.push(`${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`);
  const pnl = { ...h.day_pnl, [today]: live.account.pnl_today };
  const vals = Object.values(pnl).map(Math.abs);
  const max = Math.max(1, ...vals);
  const months = Object.entries(h.months).slice(-12).reverse();
  return (
    <div className="p-3">
      <div className="flex items-baseline justify-between mb-2">
        <span className="text-hi font-medium">{live.account.month}</span>
        <span className={clsx("num", tone(live.account.pnl_mtd))}>{usd(live.account.pnl_mtd, { sign: true })}</span>
      </div>
      <div className="grid grid-cols-7 gap-1 text-center">
        {["S", "M", "T", "W", "T", "F", "S"].map((d, i) => (
          <div key={i} className="text-[10px] text-mute pb-0.5">
            {d}
          </div>
        ))}
        {cells.map((d, i) => {
          if (!d) return <div key={i} />;
          const v = pnl[d];
          const has = v !== undefined;
          const a = has ? 0.12 + 0.55 * Math.min(1, Math.abs(v) / max) : 0;
          return (
            <div
              key={i}
              title={has ? `${d}: ${usd(v, { sign: true })}` : d}
              className={clsx("rounded h-11 flex flex-col items-center justify-center border", d === today ? "border-line2" : "border-transparent")}
              style={{ background: has ? (v >= 0 ? `rgba(62,207,142,${a})` : `rgba(240,97,109,${a})`) : "rgba(255,255,255,0.02)" }}
            >
              <span className="text-[10px] text-soft">{Number(d.slice(8))}</span>
              {has && <span className="num text-[9.5px] text-hi">{Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}k` : v.toFixed(0)}</span>}
            </div>
          );
        })}
      </div>
      <div className="label mt-5 mb-1.5">Monthly returns</div>
      <table className="tbl">
        <tbody>
          {months.map(([k, v]) => (
            <tr key={k}>
              <td className="text-soft !px-1">{new Date(k + "-15").toLocaleString("en-US", { month: "short", year: "numeric" })}</td>
              <td className={clsx("text-right num !px-1", tone(v.pnl))}>{usd(v.pnl, { sign: true })}</td>
              <td className={clsx("text-right num !px-1 w-16", tone(v.ret))}>{pct(v.ret, 1)}</td>
            </tr>
          ))}
          {!months.length && (
            <tr>
              <td className="text-mute text-center py-4">First month in progress</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function Forecasts() {
  const o = useStore((s) => s.oracle);
  const live = useStore((s) => s.live);
  if (!o) return null;
  const bull = live?.regime.btc_on;
  const side: "long" | "short" = bull === false ? "short" : "long";
  const thr = side === "long" ? o.thr_long : o.thr_short;
  const rows = [...o.forecasts].sort((a, b) => (b[side] ?? -9) - (a[side] ?? -9));
  const all = rows.flatMap((r) => [r.long ?? 0, r.short ?? 0]);
  const lo = Math.min(-1, ...all);
  const hi = Math.max(1, thr && Number.isFinite(thr) ? thr : 0, ...all);
  const x = (v: number) => ((v - lo) / (hi - lo)) * 100;
  const held = new Set(live?.oracle.open.map((t) => t.symbol));
  const open = live?.oracle.open ?? [];
  const closed = live?.oracle.closed ?? [];
  const now = Date.now() / 1000;
  return (
    <div className="p-3">
      {open.length > 0 && (
        <div className="mb-4">
          <div className="label mb-1.5">Open ORACLE trades</div>
          <table className="tbl">
            <thead>
              <tr>
                <th className="!px-1">Trade</th>
                <th className="!px-1 !text-right">Entry</th>
                <th className="!px-1 !text-right">Stop</th>
                <th className="!px-1 !text-right">Target</th>
                <th className="!px-1 !text-right">Left</th>
              </tr>
            </thead>
            <tbody>
              {open.map((t) => (
                <tr key={t.id} title={`Forecast ${t.forecast >= 0 ? "+" : ""}${t.forecast.toFixed(2)}R (bar ${t.thr.toFixed(2)}R)`}>
                  <td className="!px-1">
                    <span className={t.side === "long" ? "text-up" : "text-down"}>{t.side === "long" ? "L" : "S"}</span>{" "}
                    <span className="text-hi">{t.symbol.split("-")[0]}</span>
                  </td>
                  <td className="!px-1 text-right num text-soft">{price(t.entry)}</td>
                  <td className="!px-1 text-right num text-down">{price(t.stop)}</td>
                  <td className="!px-1 text-right num text-up">{price(t.target)}</td>
                  <td className="!px-1 text-right num text-soft">{left(t.expires, now)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="text-[11px] text-soft leading-snug mb-3">
        {o.ready ? (
          <>
            Expected 14-day outcome of a trade, in R (1R = the planned risk). ORACLE is trading the{" "}
            <span className={side === "long" ? "text-up" : "text-down"}>{side}</span> side while the regime is {bull ? "bull" : "risk-off"}; it takes
            ideas above the bar ({thr != null && Number.isFinite(thr) ? `${thr >= 0 ? "+" : ""}${thr.toFixed(2)}R` : "—"}).
          </>
        ) : (
          o.status
        )}
      </div>
      <table className="tbl">
        <thead>
          <tr>
            <th className="!px-1">Coin</th>
            <th className="!px-1">{side === "long" ? "Long" : "Short"} forecast</th>
            <th className="!px-1 !text-right">R</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const v = r[side] ?? 0;
            const pass = thr != null && v >= thr;
            return (
              <tr key={r.symbol} title={r.reasons.join(" · ")}>
                <td className="!px-1 text-hi">
                  {r.symbol.split("-")[0]}
                  {held.has(r.symbol) && <span className="dot ml-1.5 bg-[#c4b5fd]" />}
                </td>
                <td className="!px-1 w-full">
                  <div className="relative h-2 bg-line rounded-sm">
                    <div className="absolute top-0 bottom-0 w-px bg-line2" style={{ left: `${x(0)}%` }} />
                    <div
                      className={clsx("absolute top-0 bottom-0 rounded-sm", pass ? "bg-[#c4b5fd]" : "bg-soft/40")}
                      style={{ left: `${Math.min(x(0), x(v))}%`, width: `${Math.abs(x(v) - x(0))}%` }}
                    />
                    {thr != null && Number.isFinite(thr) && <div className="absolute -top-0.5 -bottom-0.5 w-[2px] bg-[#c4b5fd]" style={{ left: `${x(thr)}%` }} />}
                  </div>
                </td>
                <td className={clsx("!px-1 text-right num", pass ? "text-[#c4b5fd]" : "text-soft")}>
                  {v >= 0 ? "+" : ""}
                  {v.toFixed(2)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {closed.length > 0 && (
        <div className="mt-4">
          <div className="flex items-baseline justify-between mb-1.5">
            <span className="label">Closed ORACLE trades</span>
            <span className="text-[11px] text-mute">
              {live?.oracle.wins}/{live?.oracle.trades} winners
            </span>
          </div>
          <table className="tbl">
            <tbody>
              {closed.slice(0, 12).map((t) => (
                <tr key={t.id + String(t.closed)}>
                  <td className="!px-1">
                    <span className={t.side === "long" ? "text-up" : "text-down"}>{t.side === "long" ? "L" : "S"}</span>{" "}
                    <span className="text-hi">{t.symbol.split("-")[0]}</span>
                  </td>
                  <td className="!px-1 text-mute">{t.why === "TARGET" ? "target" : t.why === "STOP" ? "stop" : "time"}</td>
                  <td className={clsx("!px-1 text-right num", tone(t.ret ?? 0))}>{pct(t.ret ?? 0)}</td>
                  <td className={clsx("!px-1 text-right num", tone(t.R ?? 0))}>
                    {(t.R ?? 0) >= 0 ? "+" : ""}
                    {(t.R ?? 0).toFixed(2)}R
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {o.model?.samples && (
        <div className="text-[10.5px] text-mute mt-3 leading-snug">
          2 gradient-boosted models · {o.model.features} features · {o.model.samples.toLocaleString()} training samples · retrained daily
          {o.trained_at ? ` · last ${ago(o.trained_at)} ago` : ""}
        </div>
      )}
    </div>
  );
}

function NewsList() {
  const n = useStore((s) => s.news);
  const now = Date.now() / 1000;
  if (!n) return null;
  const vetoes = Object.entries(n.vetoes);
  return (
    <div className="p-3">
      <div className="flex items-center justify-between text-[11px] mb-2">
        <span className="text-soft">
          24h headline tone{" "}
          <span className={clsx("num", tone(n.mood_24h ?? 0))}>{n.mood_24h == null ? "—" : `${n.mood_24h >= 0 ? "+" : ""}${n.mood_24h.toFixed(2)}`}</span>
        </span>
        {n.headline_risk_until && <span className="text-[#e3b35b]">Headline risk: new longs paused</span>}
      </div>
      {vetoes.length > 0 && (
        <div className="text-[11px] text-[#e3b35b] mb-2">
          Long vetoes: {vetoes.map(([s, t]) => `${s.split("-")[0]} (${Math.ceil((t - now) / 3600)}h)`).join(", ")}
        </div>
      )}
      <div className="space-y-2.5">
        {n.items.map((i) => (
          <a key={i.id} href={i.link} target="_blank" rel="noreferrer" className="block group">
            <div className="flex items-center gap-2 text-[10.5px] text-mute">
              <span>{i.source}</span>
              <span>{ago(i.ts, now)} ago</span>
              {i.coins.map((c) => (
                <span key={c} className="text-soft">
                  {c.split("-")[0]}
                </span>
              ))}
              <span className={clsx("ml-auto num", i.severe ? "text-down" : tone(i.score))}>{i.severe ? "severe" : i.score ? (i.score > 0 ? "+" : "") + i.score.toFixed(2) : ""}</span>
            </div>
            <div className="text-[12px] leading-snug text-text group-hover:text-hi">{i.title}</div>
          </a>
        ))}
        {!n.items.length && <div className="text-mute text-center py-6">Loading headlines</div>}
      </div>
    </div>
  );
}

function Costs() {
  const a = useStore((s) => s.live?.account);
  const meta = useStore((s) => s.meta);
  if (!a) return null;
  const t = a.tax_ytd;
  const fees = a.fees.spot + a.fees.perp;
  const rows: [string, string, string?][] = [
    ["Exchange fees", usd(-fees, { sign: true })],
    ["  Spot", usd(-a.fees.spot, { sign: true })],
    ["  Futures", usd(-a.fees.perp, { sign: true })],
    ["Funding", usd(-a.funding, { sign: true }), a.funding <= 0 ? "text-up" : "text-down"],
    ["Funding rates", a.funding_source === "deribit" || !a.funding_hours ? "Deribit (proxy)" : `Coinbase · ${a.funding_hours}h recorded`],
    ["Saved by internal netting", usd(a.netting_saved, { sign: true }), "text-up"],
    ...(a.cash_apy || a.interest
      ? ([[`Interest on idle cash${a.cash_apy ? ` · ${(a.cash_apy * 100).toFixed(2)}%` : ""}`, usd(a.interest ?? 0, { sign: true }), "text-up"]] as [string, string, string][])
      : []),
    ["Spot fee tier", a.fee_tier],
    ["Directional longs via", a.costs.long_venue === "perp" ? "Perpetual-style futures" : "Spot"],
  ];
  const tax: [string, string][] = [
    ["Short-term gains", usd(t.spot_short_term, { sign: true })],
    ["Long-term gains", usd(t.spot_long_term, { sign: true })],
    ["Section 1256 (60/40)", usd(t.section_1256, { sign: true })],
    ...(t.interest ? ([["Interest (ordinary income)", usd(t.interest, { sign: true })]] as [string, string][]) : []),
    ["Federal", usd(t.federal)],
    ["State", usd(t.state)],
    ["NIIT", usd(t.niit)],
  ];
  return (
    <div className="p-3 space-y-5">
      <div>
        <div className="label mb-1.5">Trading costs since inception</div>
        <table className="tbl">
          <tbody>
            {rows.map(([k, v, c]) => (
              <tr key={k}>
                <td className={clsx("!px-1", k.startsWith("  ") ? "text-mute pl-4" : "text-soft")}>{k.trim()}</td>
                <td className={clsx("!px-1 text-right num", c ?? "text-text")}>{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <div className="flex items-baseline justify-between mb-1.5">
          <span className="label">Estimated tax · {new Date().getFullYear()}</span>
          <span className="num text-hi">{usd(t.total)}</span>
        </div>
        <table className="tbl">
          <tbody>
            {tax.map(([k, v]) => (
              <tr key={k}>
                <td className="!px-1 text-soft">{k}</td>
                <td className="!px-1 text-right num text-text">{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="text-[10.5px] text-mute mt-2 leading-snug">
          {meta?.tax.filing_status === "mfj" ? "Married filing jointly" : "Single"} · {usd(meta?.tax.other_income ?? 0, { whole: true })} other income ·{" "}
          {meta?.tax.state_name}. Futures are Section 1256 contracts: 60% long-term / 40% short-term, marked to market at year end. Estimate only.
        </div>
        <a href="/api/export/8949.csv" className="inline-block mt-2 text-[11px] text-soft hover:text-hi underline underline-offset-2">
          Download tax report (CSV)
        </a>
      </div>
    </div>
  );
}
