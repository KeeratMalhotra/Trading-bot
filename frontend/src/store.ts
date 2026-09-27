import { create } from "zustand";
import type { Candle, Event, Fill, History, Live, Meta, News, OracleState } from "./types";

interface State {
  connected: boolean;
  meta: Meta | null;
  live: Live | null;
  events: Event[];
  fills: Fill[];
  history: History | null;
  oracle: OracleState | null;
  news: News | null;
  candle: { symbol: string; tf: number; c: Candle } | null;
  // view
  view: "performance" | "chart";
  symbol: string;
  tf: number;
  logFilter: string;
  set: (p: Partial<State>) => void;
  subscribe: (symbol: string, tf: number) => void;
}

let ws: WebSocket | null = null;
let retry = 1000;

export const useStore = create<State>((set) => ({
  connected: false,
  meta: null,
  live: null,
  events: [],
  fills: [],
  history: null,
  oracle: null,
  news: null,
  candle: null,
  view: "performance",
  symbol: "BTC-USD",
  tf: 3600,
  logFilter: "all",
  set: (p) => set(p),
  subscribe: (symbol, tf) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ op: "sub", symbol, tf }));
  },
}));

export function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    retry = 1000;
    useStore.setState({ connected: true });
    const { symbol, tf } = useStore.getState();
    useStore.getState().subscribe(symbol, tf);
  };
  ws.onclose = () => {
    useStore.setState({ connected: false });
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 15000);
  };
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    const st = useStore.getState();
    switch (msg.t) {
      case "snapshot":
        useStore.setState({
          meta: msg.meta,
          live: msg.live,
          events: msg.events,
          fills: msg.fills,
          history: msg.history,
          oracle: msg.oracle,
          news: msg.news,
        });
        break;
      case "live":
        useStore.setState({ live: msg.data });
        break;
      case "events": {
        const fills = msg.items.filter((e: Event) => e.kind === "fill" && e.data?.fill).map((e: Event) => e.data.fill as Fill);
        useStore.setState({
          events: [...st.events, ...msg.items].slice(-500),
          fills: fills.length ? [...fills.reverse(), ...st.fills].slice(0, 200) : st.fills,
        });
        break;
      }
      case "history":
        useStore.setState({ history: msg.data });
        break;
      case "oracle":
        useStore.setState({ oracle: msg.data });
        break;
      case "news":
        useStore.setState({ news: msg.data });
        break;
      case "candle":
        useStore.setState({ candle: { symbol: msg.symbol, tf: msg.tf, c: msg.c } });
        break;
    }
  };
}

export const AGENT_COLOR: Record<string, string> = {
  ATLAS: "#7dd3fc",
  ORACLE: "#c4b5fd",
  NOVA: "#fcd34d",
  DESK: "#8a919c",
  NEWS: "#8a919c",
};
