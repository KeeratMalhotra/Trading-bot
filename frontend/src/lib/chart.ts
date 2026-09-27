import { ColorType, CrosshairMode, LineStyle, type ChartOptions, type DeepPartial, type Time } from "lightweight-charts";

const etFull = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
const etTick = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false });
const etDay = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", day: "numeric" });
const etMonth = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", year: "2-digit" });

export const chartOptions: DeepPartial<ChartOptions> = {
  autoSize: true,
  layout: {
    background: { type: ColorType.Solid, color: "transparent" },
    textColor: "#5b626d",
    fontFamily: "JetBrains Mono Variable, ui-monospace, monospace",
    fontSize: 10,
    attributionLogo: false,
  },
  grid: { vertLines: { visible: false }, horzLines: { color: "rgba(255,255,255,0.035)" } },
  crosshair: {
    mode: CrosshairMode.Normal,
    vertLine: { color: "rgba(255,255,255,0.12)", style: LineStyle.Solid, labelBackgroundColor: "#23272e" },
    horzLine: { color: "rgba(255,255,255,0.12)", style: LineStyle.Solid, labelBackgroundColor: "#23272e" },
  },
  rightPriceScale: { borderVisible: false },
  timeScale: {
    borderVisible: false,
    timeVisible: true,
    secondsVisible: false,
    tickMarkFormatter: (t: Time, type: number) => {
      const d = new Date((t as number) * 1000);
      if (type <= 1) return etMonth.format(d);
      return type === 2 ? etDay.format(d) : etTick.format(d);
    },
  },
  localization: { timeFormatter: (t: Time) => etFull.format(new Date((t as number) * 1000)) },
};
