import clsx from "clsx";
import { AGENT_COLOR, useStore } from "../store";
import { timeET, dateET } from "../lib/format";

const FILTERS = ["all", "fills", "ATLAS", "ORACLE", "NOVA", "DESK", "NEWS"];

export function Log() {
  const events = useStore((s) => s.events);
  const filter = useStore((s) => s.logFilter);
  const set = useStore((s) => s.set);
  const shown = events
    .filter((e) => {
      if (filter === "all") return e.kind !== "fill" || true;
      if (filter === "fills") return e.kind === "fill";
      if (filter === "DESK") return e.agent === "DESK" && e.kind !== "fill";
      return e.agent === filter;
    })
    .slice(-150)
    .reverse();
  let lastDay = "";
  return (
    <section className="card flex flex-col min-h-0 h-full">
      <div className="flex items-center gap-1 px-3 h-9 border-b border-line shrink-0">
        <span className="label mr-2">Activity</span>
        {FILTERS.map((f) => (
          <button
            key={f}
            onClick={() => set({ logFilter: f })}
            className={clsx("text-[10.5px] px-1.5 py-0.5 rounded", filter === f ? "bg-line2 text-hi" : "text-mute hover:text-soft")}
          >
            {f === "all" ? "All" : f === "fills" ? "Fills" : f.charAt(0) + f.slice(1).toLowerCase()}
          </button>
        ))}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto scroll">
        {shown.map((e) => {
          const day = dateET(e.ts);
          const header = day !== lastDay;
          lastDay = day;
          return (
            <div key={e.id}>
              {header && <div className="px-4 pt-2.5 pb-1 text-[10.5px] text-mute">{day}</div>}
              <div className="fade-in grid grid-cols-[62px_64px_1fr] gap-2 px-4 py-1.5 hover:bg-white/[0.015]">
                <span className="num text-[11px] text-mute pt-px">{timeET(e.ts)}</span>
                <span className="text-[10.5px] font-semibold tracking-wide pt-px" style={{ color: AGENT_COLOR[e.agent] ?? "#8a919c" }}>
                  {e.agent}
                </span>
                <div className="min-w-0">
                  <div
                    className={clsx(
                      "text-[12px] leading-snug",
                      e.kind === "fill" ? "num text-text" : "text-hi",
                      e.level === "good" && "!text-up",
                      e.level === "bad" && "!text-down",
                      e.level === "warn" && "!text-[#e3b35b]",
                    )}
                  >
                    {e.title}
                  </div>
                  {e.text && <div className="text-[11.5px] leading-snug text-soft mt-0.5">{e.text}</div>}
                </div>
              </div>
            </div>
          );
        })}
        {!shown.length && <div className="text-center text-mute py-8">No activity yet</div>}
      </div>
    </section>
  );
}
