import { ColorType, CrosshairMode, LineStyle, type DeepPartial, type ChartOptions, type Time } from "lightweight-charts";

const etFull = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});
const etTick = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });
const etDay = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", day: "numeric" });

export const baseChartOptions: DeepPartial<ChartOptions> = {
  autoSize: true,
  layout: {
    background: { type: ColorType.Solid, color: "transparent" },
    textColor: "#6b7686",
    fontFamily: "JetBrains Mono Variable, ui-monospace, monospace",
    fontSize: 11,
    attributionLogo: false,
  },
  grid: {
    vertLines: { color: "rgba(255,255,255,0.03)" },
    horzLines: { color: "rgba(255,255,255,0.04)" },
  },
  crosshair: {
    mode: CrosshairMode.Normal,
    vertLine: { color: "rgba(255,255,255,0.15)", style: LineStyle.Dashed, labelBackgroundColor: "#1c2330" },
    horzLine: { color: "rgba(255,255,255,0.15)", style: LineStyle.Dashed, labelBackgroundColor: "#1c2330" },
  },
  rightPriceScale: { borderVisible: false },
  timeScale: {
    borderVisible: false,
    timeVisible: true,
    secondsVisible: false,
    tickMarkFormatter: (t: Time, type: number) => {
      const d = new Date((t as number) * 1000);
      return type <= 2 ? etDay.format(d) : etTick.format(d);
    },
  },
  localization: {
    timeFormatter: (t: Time) => etFull.format(new Date((t as number) * 1000)),
  },
};
