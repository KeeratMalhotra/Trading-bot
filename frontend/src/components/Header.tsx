import { useEffect, useState } from "react";
import { Settings, Swords, Volume2, VolumeX, Video, VideoOff } from "lucide-react";
import clsx from "clsx";
import { useStore } from "../store";
import { duration, pct } from "../lib/format";
import { sounds } from "../lib/sound";

function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

const clockFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
});

export function Header() {
  const meta = useStore((s) => s.meta);
  const live = useStore((s) => s.live);
  const connected = useStore((s) => s.connected);
  const sound = useStore((s) => s.sound);
  const autoCam = useStore((s) => s.autoCam);
  const setS = useStore((s) => s.set);
  const now = useNow();
  const market = live?.market ?? meta?.market;
  const fees = live?.fees ?? meta?.fees;
  const isSim = market?.source === "sim";
  const ok = connected && market?.connected;
  const elapsed = meta ? now / 1000 - meta.started : 0;

  return (
    <header className="flex items-center gap-4 px-5 h-14 shrink-0">
      <div className="flex items-center gap-3">
        <div className="relative grid place-items-center w-9 h-9 rounded-xl bg-gradient-to-br from-low/90 via-medium/80 to-high/90 shadow-lg shadow-black/40">
          <Swords className="w-5 h-5 text-ink-950" strokeWidth={2.5} />
        </div>
        <div className="leading-tight">
          <div className="text-[15px] font-bold tracking-[0.18em] text-white">BOT BATTLE</div>
          <div className="text-[11px] text-ink-400">3 AI traders · $10K each · live crypto markets</div>
        </div>
      </div>

      <div className="ml-4 flex items-center gap-2">
        <span className="px-2.5 py-1 rounded-lg text-[11px] font-bold tracking-wider bg-sky-400/10 text-sky-300 border border-sky-400/20">
          DEMO MONEY
        </span>
        <span
          className={clsx(
            "flex items-center gap-2 px-2.5 py-1 rounded-lg text-[11px] font-semibold border",
            ok && !isSim && "text-up border-up/20 bg-up/10",
            ok && isSim && "text-medium border-medium/20 bg-medium/10",
            !ok && "text-down border-down/20 bg-down/10",
          )}
          title={market?.message}
        >
          <span className="live-dot" />
          {!connected ? "RECONNECTING" : isSim ? "SIMULATED MARKET" : ok ? "LIVE PRICES · COINBASE" : "FEED DOWN"}
        </span>
        {fees && (
          <span
            className="hidden lg:inline-flex px-2.5 py-1 rounded-lg text-[11px] text-ink-300 border border-white/5 bg-white/[0.03] num"
            title="Coinbase Advanced fee tier used for every simulated fill"
          >
            Fees {fees.tier}: {pct(fees.maker, 2, false)} maker / {pct(fees.taker, 2, false)} taker
          </span>
        )}
      </div>

      <div className="ml-auto flex items-center gap-5">
        {meta && (
          <div className="text-right leading-tight hidden md:block">
            <div className="text-[10px] uppercase tracking-widest text-ink-400">Battle time</div>
            <div className="num text-sm text-ink-200">{duration(elapsed)}</div>
          </div>
        )}
        <div className="text-right leading-tight">
          <div className="text-[10px] uppercase tracking-widest text-ink-400">New York</div>
          <div className="num text-sm text-white">{clockFmt.format(now)}</div>
        </div>
        <div className="flex items-center gap-1">
          <IconBtn
            title={autoCam ? "Auto camera ON: chart jumps to every trade" : "Auto camera OFF"}
            active={autoCam}
            onClick={() => setS({ autoCam: !autoCam })}
          >
            {autoCam ? <Video className="w-4 h-4" /> : <VideoOff className="w-4 h-4" />}
          </IconBtn>
          <IconBtn
            title={sound ? "Sound ON" : "Sound OFF"}
            active={sound}
            onClick={() => {
              sounds.unlock();
              setS({ sound: !sound });
            }}
          >
            {sound ? <Volume2 className="w-4 h-4" /> : <VolumeX className="w-4 h-4" />}
          </IconBtn>
          <IconBtn title="Settings" onClick={() => setS({ settingsOpen: true })}>
            <Settings className="w-4 h-4" />
          </IconBtn>
        </div>
      </div>
    </header>
  );
}

function IconBtn({
  children,
  onClick,
  title,
  active,
}: {
  children: React.ReactNode;
  onClick: () => void;
  title: string;
  active?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={clsx(
        "grid place-items-center w-9 h-9 rounded-xl border transition-colors",
        active ? "text-white border-white/15 bg-white/10" : "text-ink-400 border-white/5 hover:text-white hover:bg-white/5",
      )}
    >
      {children}
    </button>
  );
}
