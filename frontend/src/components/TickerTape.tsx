import clsx from "clsx";
import { useStore } from "../store";
import { coin, pct, price } from "../lib/format";

export function TickerTape() {
  const prices = useStore((s) => s.live?.prices ?? []);
  const focus = useStore((s) => s.focus);
  if (!prices.length) return <div className="h-9" />;
  const items = [...prices, ...prices];
  return (
    <div className="relative h-9 overflow-hidden border-y border-white/5 bg-black/20 shrink-0">
      <div className="absolute inset-y-0 left-0 w-16 bg-gradient-to-r from-ink-950 to-transparent z-10" />
      <div className="absolute inset-y-0 right-0 w-16 bg-gradient-to-l from-ink-950 to-transparent z-10" />
      <div className="marquee flex w-max h-full items-center">
        {items.map((p, i) => (
          <button
            key={i}
            onClick={() => focus(p.s)}
            className="flex items-center gap-2 px-5 text-[13px] hover:bg-white/5 h-full"
          >
            <span className="font-semibold text-white">{coin(p.s)}</span>
            <span className="num text-ink-200">{price(p.p)}</span>
            <span className={clsx("num text-xs", p.chg >= 0 ? "text-up" : "text-down")}>{pct(p.chg)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
