import clsx from "clsx";
import { useStore } from "./store";
import { TopBar } from "./components/TopBar";
import { AccountStrip } from "./components/AccountStrip";
import { Agents } from "./components/Agents";
import { PerformanceChart } from "./components/PerformanceChart";
import { PriceChart } from "./components/PriceChart";
import { BacktestChart } from "./components/BacktestChart";
import { Toasts, WeeklyOverlay } from "./components/Toasts";
import { Positions } from "./components/Positions";
import { Log } from "./components/Log";
import { Side } from "./components/Side";
import { price } from "./lib/format";

const TFS = [
  { tf: 300, label: "5m" },
  { tf: 900, label: "15m" },
  { tf: 3600, label: "1h" },
  { tf: 21600, label: "6h" },
  { tf: 86400, label: "1d" },
];

export default function App() {
  const live = useStore((s) => s.live);
  const connected = useStore((s) => s.connected);
  if (!live) {
    return (
      <div className="h-full grid place-items-center">
        <div className="text-center">
          <div className="text-[13px] font-semibold tracking-[0.22em] text-hi">QUORUM</div>
          <div className="text-[11px] text-mute mt-2">{connected ? "Loading account" : "Connecting"}</div>
        </div>
      </div>
    );
  }
  return (
    <div className="h-full flex flex-col min-h-[900px]">
      <TopBar />
      <main className="flex-1 min-h-0 flex flex-col gap-3 p-3">
        <AccountStrip />
        <div className="flex-1 min-h-0 grid grid-cols-[minmax(0,1fr)_340px] gap-3">
          <div className="min-h-0 flex flex-col gap-3">
            <Agents />
            <div className="flex-1 min-h-0 grid grid-rows-[minmax(220px,1.05fr)_minmax(180px,1fr)] gap-3">
              <ChartCard />
              <Positions />
            </div>
          </div>
          <div className="min-h-0 min-w-0 grid grid-rows-[minmax(0,1.15fr)_minmax(0,1fr)] gap-3">
            <Log />
            <Side />
          </div>
        </div>
      </main>
    </div>
  );
}

function ChartCard() {
  const view = useStore((s) => s.view);
  const symbol = useStore((s) => s.symbol);
  const tf = useStore((s) => s.tf);
  const set = useStore((s) => s.set);
  const subscribe = useStore((s) => s.subscribe);
  const quotes = useStore((s) => s.live?.quotes ?? []);
  const symbols = useStore((s) => s.meta?.symbols ?? []);
  const q = quotes.find((x) => x.s === symbol);
  return (
    <section className="card flex flex-col min-h-0">
      <div className="flex items-center gap-3 px-3 h-9 border-b border-line shrink-0">
        <div className="flex gap-0.5">
          {(["performance", "chart", "backtest"] as const).map((v) => (
            <button key={v} onClick={() => set({ view: v })} className={clsx("text-[11px] px-2 py-0.5 rounded", view === v ? "bg-line2 text-hi" : "text-mute hover:text-soft")}>
              {v === "performance" ? "Performance" : v === "chart" ? "Market" : "Backtest"}
            </button>
          ))}
        </div>
        {view === "chart" && (
          <>
            <span className="text-hi font-medium ml-2">{symbol}</span>
            <span className="num text-text">{q ? price(q.p) : "—"}</span>
            <div className="ml-auto flex items-center gap-3">
              <div className="flex gap-0.5">
                {symbols.map((s) => (
                  <button
                    key={s}
                    onClick={() => set({ symbol: s })}
                    className={clsx("text-[10.5px] px-1.5 py-0.5 rounded", s === symbol ? "bg-line2 text-hi" : "text-mute hover:text-soft")}
                  >
                    {s.split("-")[0]}
                  </button>
                ))}
              </div>
              <div className="flex gap-0.5">
                {TFS.map((t) => (
                  <button
                    key={t.tf}
                    onClick={() => {
                      set({ tf: t.tf });
                      subscribe(symbol, t.tf);
                    }}
                    className={clsx("num text-[10.5px] px-1.5 py-0.5 rounded", tf === t.tf ? "bg-line2 text-hi" : "text-mute hover:text-soft")}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
      <div className="relative flex-1 min-h-0">
        <div className={clsx("absolute inset-0", view !== "performance" && "invisible")}>
          <PerformanceChart />
        </div>
        <div className={clsx("absolute inset-0", view !== "chart" && "invisible")}>
          <PriceChart symbol={symbol} tf={tf} />
        </div>
        <div className={clsx("absolute inset-0", view !== "backtest" && "invisible")}>
          <BacktestChart />
        </div>
        <Toasts />
        <WeeklyOverlay />
      </div>
    </section>
  );
}
