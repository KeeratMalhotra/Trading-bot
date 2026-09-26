import { useEffect } from "react";
import clsx from "clsx";
import { LineChart, Trophy } from "lucide-react";
import { useStore } from "./store";
import { Header } from "./components/Header";
import { TickerTape } from "./components/TickerTape";
import { BotCard } from "./components/BotCard";
import { RaceChart } from "./components/RaceChart";
import { PriceChart } from "./components/PriceChart";
import { Feed } from "./components/Feed";
import { ActiveTrades } from "./components/ActiveTrades";
import { SidePanel } from "./components/SidePanel";
import { Toasts } from "./components/Toasts";
import { SettingsDrawer } from "./components/SettingsDrawer";
import { coin, pct, price } from "./lib/format";

const TFS = [
  { tf: 60, label: "1m" },
  { tf: 300, label: "5m" },
  { tf: 900, label: "15m" },
  { tf: 3600, label: "1h" },
];
const REGIME: Record<string, { label: string; cls: string }> = {
  TRENDING_UP: { label: "Uptrend", cls: "text-up bg-up/10" },
  TRENDING_DOWN: { label: "Downtrend", cls: "text-down bg-down/10" },
  RANGING: { label: "Ranging", cls: "text-ink-200 bg-white/5" },
  VOLATILE: { label: "Volatile", cls: "text-medium bg-medium/10" },
};

export default function App() {
  const live = useStore((s) => s.live);
  const connected = useStore((s) => s.connected);

  // auto camera: return to the race view after a while
  useEffect(() => {
    const t = setInterval(() => {
      const { camUntil, set } = useStore.getState();
      if (camUntil && Date.now() > camUntil) set({ chartTab: "race", camUntil: 0 });
    }, 1000);
    return () => clearInterval(t);
  }, []);

  if (!live) {
    return (
      <div className="h-full grid place-items-center">
        <div className="text-center">
          <div className="text-sm tracking-[0.3em] text-white font-bold">BOT BATTLE</div>
          <div className="text-xs text-ink-400 mt-2">{connected ? "Loading arena…" : "Connecting to the arena…"}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col lg:h-full lg:min-h-[820px]">
      <Header />
      <TickerTape />
      <main className="flex-1 min-h-0 grid gap-3 p-3 lg:grid-rows-[auto_minmax(260px,1fr)_minmax(200px,34%)] lg:grid-cols-12">
        <div className="lg:col-span-12 grid grid-cols-1 md:grid-cols-3 gap-3">
          {live.bots.map((b) => (
            <BotCard key={b.id} bot={b} />
          ))}
        </div>
        <div className="lg:col-span-8 min-h-[420px] lg:min-h-0">
          <ChartPanel />
        </div>
        <div className="lg:col-span-4 lg:row-span-1 h-[520px] lg:h-auto min-h-0">
          <Feed />
        </div>
        <div className="lg:col-span-8 h-[360px] lg:h-auto min-h-0">
          <ActiveTrades />
        </div>
        <div className="lg:col-span-4 h-[420px] lg:h-auto min-h-0">
          <SidePanel />
        </div>
      </main>
      <footer className="px-5 pb-2 text-[10px] text-ink-400 flex flex-wrap gap-x-4 justify-between shrink-0">
        <span>
          Demo money only · live Coinbase prices · simulated fills with real fee tiers, slippage &amp; US tax estimates.
        </span>
        <span>Not financial advice. Past performance does not predict future results.</span>
      </footer>
      <Toasts />
      <SettingsDrawer />
    </div>
  );
}

function ChartPanel() {
  const tab = useStore((s) => s.chartTab);
  const symbol = useStore((s) => s.focusSymbol);
  const tf = useStore((s) => s.tf);
  const setS = useStore((s) => s.set);
  const focus = useStore((s) => s.focus);
  const subscribe = useStore((s) => s.subscribe);
  const live = useStore((s) => s.live);
  const symbols = useStore((s) => s.meta?.symbols ?? []);
  const camUntil = useStore((s) => s.camUntil);
  const q = live?.prices.find((p) => p.s === symbol);
  const regime = live?.regimes?.high?.[symbol] ?? live?.regimes?.medium?.[symbol];
  const rg = regime ? REGIME[regime] : null;

  return (
    <div className="panel h-full flex flex-col min-h-0">
      <div className="flex items-center gap-2 px-3 pt-2.5 pb-2 flex-wrap">
        <div className="flex bg-white/5 rounded-lg p-0.5">
          <TabBtn active={tab === "race"} onClick={() => setS({ chartTab: "race", camUntil: 0 })}>
            <Trophy className="w-3.5 h-3.5" /> Race
          </TabBtn>
          <TabBtn active={tab === "chart"} onClick={() => setS({ chartTab: "chart", camUntil: 0 })}>
            <LineChart className="w-3.5 h-3.5" /> Chart
          </TabBtn>
        </div>
        {tab === "chart" && (
          <>
            <div className="flex items-baseline gap-2 ml-2">
              <span className="text-lg font-bold text-white">{coin(symbol)}</span>
              <span className="num text-lg text-white">{q ? price(q.p) : "—"}</span>
              {q && <span className={clsx("num text-xs", q.chg >= 0 ? "text-up" : "text-down")}>{pct(q.chg)} 24h</span>}
              {rg && <span className={clsx("text-[10px] font-semibold px-1.5 py-0.5 rounded", rg.cls)}>{rg.label}</span>}
              {camUntil > 0 && <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded text-sky-300 bg-sky-400/10">AUTO CAM</span>}
            </div>
            <div className="ml-auto flex items-center gap-2">
              <div className="flex gap-0.5 flex-wrap">
                {symbols.map((s) => (
                  <button
                    key={s}
                    onClick={() => focus(s)}
                    className={clsx(
                      "text-[10px] font-semibold px-1.5 py-1 rounded-md",
                      s === symbol ? "bg-white/10 text-white" : "text-ink-400 hover:text-ink-200",
                    )}
                  >
                    {coin(s)}
                  </button>
                ))}
              </div>
              <div className="flex bg-white/5 rounded-lg p-0.5">
                {TFS.map((t) => (
                  <button
                    key={t.tf}
                    onClick={() => {
                      setS({ tf: t.tf });
                      subscribe(symbol, t.tf);
                    }}
                    className={clsx("num text-[10px] px-2 py-1 rounded-md", tf === t.tf ? "bg-white/10 text-white" : "text-ink-400")}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </div>
          </>
        )}
        {tab === "race" && <div className="ml-auto text-[11px] text-ink-400">Return since the battle started</div>}
      </div>
      <div className="relative flex-1 min-h-0">
        <div className={clsx("absolute inset-0", tab !== "race" && "invisible")}>
          <RaceChart />
        </div>
        <div className={clsx("absolute inset-0", tab !== "chart" && "invisible")}>
          <PriceChart symbol={symbol} tf={tf} />
        </div>
      </div>
    </div>
  );
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        "flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-md transition-colors",
        active ? "bg-white/10 text-white" : "text-ink-400 hover:text-ink-200",
      )}
    >
      {children}
    </button>
  );
}
