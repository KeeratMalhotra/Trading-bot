import { create } from "zustand";
import type { BotEvent, Candle, Live, Meta, Trade } from "./types";
import { sounds } from "./lib/sound";

export interface Toast {
  id: string;
  ev: BotEvent;
}

type EquityMap = Record<string, [number, number][]>;

interface State {
  connected: boolean;
  meta: Meta | null;
  live: Live | null;
  events: BotEvent[];
  trades: Trade[];
  equity: EquityMap;
  candle: { symbol: string; tf: number; c: Candle } | null;
  toasts: Toast[];
  // view state
  chartTab: "race" | "chart";
  focusSymbol: string;
  tf: number;
  autoCam: boolean;
  camUntil: number;
  sound: boolean;
  feedFilter: string;
  settingsOpen: boolean;
  adminToken: string;
  // actions
  set: (p: Partial<State>) => void;
  focus: (symbol: string, byCamera?: boolean) => void;
  dismissToast: (id: string) => void;
  subscribe: (symbol: string, tf: number) => void;
  api: (path: string, body?: unknown) => Promise<Response>;
}

let ws: WebSocket | null = null;
let retry = 1000;
const TOAST_KINDS = new Set(["open", "close", "risk", "partial"]);

const saved = (k: string, d: string) => (typeof localStorage !== "undefined" ? localStorage.getItem(k) ?? d : d);

export const useStore = create<State>((set, get) => ({
  connected: false,
  meta: null,
  live: null,
  events: [],
  trades: [],
  equity: {},
  candle: null,
  toasts: [],
  chartTab: "race",
  focusSymbol: "BTC-USD",
  tf: 900,
  autoCam: saved("bb.autocam", "1") === "1",
  camUntil: 0,
  sound: saved("bb.sound", "0") === "1",
  feedFilter: "all",
  settingsOpen: false,
  adminToken: saved("bb.token", ""),

  set: (p) => {
    if ("autoCam" in p) localStorage.setItem("bb.autocam", p.autoCam ? "1" : "0");
    if ("sound" in p) localStorage.setItem("bb.sound", p.sound ? "1" : "0");
    if ("adminToken" in p) localStorage.setItem("bb.token", p.adminToken ?? "");
    set(p);
  },
  focus: (symbol, byCamera = false) => {
    set({ focusSymbol: symbol, chartTab: "chart", camUntil: byCamera ? Date.now() + 75_000 : 0 });
    get().subscribe(symbol, get().tf);
  },
  dismissToast: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
  subscribe: (symbol, tf) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ op: "sub", symbol, tf }));
  },
  api: (path, body) =>
    fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json", "X-Admin-Token": get().adminToken },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
}));

function handleEvents(items: BotEvent[]) {
  const st = useStore.getState();
  const events = [...st.events, ...items].slice(-400);
  const toasts = [...st.toasts];
  let camSymbol: string | null = null;
  for (const ev of items) {
    if (TOAST_KINDS.has(ev.kind) && (ev.kind !== "risk" || ev.level === "bad" || ev.level === "warn")) {
      toasts.push({ id: ev.id, ev });
      if (st.sound) {
        if (ev.kind === "open") sounds.open();
        else if (ev.kind === "close") (ev.level === "good" ? sounds.win : sounds.loss)();
        else if (ev.kind === "partial") sounds.win();
        else if (ev.level === "bad") sounds.alert();
      }
    }
    if ((ev.kind === "open" || ev.kind === "close" || ev.kind === "order") && ev.symbol) camSymbol = ev.symbol;
  }
  useStore.setState({ events, toasts: toasts.slice(-4) });
  if (camSymbol && st.autoCam) st.focus(camSymbol, true);
}

export function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    retry = 1000;
    useStore.setState({ connected: true });
    const { focusSymbol, tf } = useStore.getState();
    useStore.getState().subscribe(focusSymbol, tf);
  };
  ws.onclose = () => {
    useStore.setState({ connected: false });
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 15000);
  };
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    switch (msg.t) {
      case "snapshot":
        useStore.setState({
          meta: msg.meta,
          live: msg.live,
          events: msg.events,
          trades: msg.trades,
          equity: msg.equity,
        });
        break;
      case "live":
        useStore.setState({ live: msg });
        break;
      case "events":
        handleEvents(msg.items);
        break;
      case "trades":
        useStore.setState({ trades: [...msg.items.reverse(), ...useStore.getState().trades].slice(0, 300) });
        break;
      case "equity":
        useStore.setState({ equity: msg.data });
        break;
      case "meta":
        useStore.setState({ meta: msg.data });
        break;
      case "candle":
        useStore.setState({ candle: { symbol: msg.symbol, tf: msg.tf, c: msg.c } });
        break;
    }
  };
}

export const botColor = (id: string) =>
  ({ low: "#34d399", medium: "#fbbf24", high: "#f43f5e", system: "#94a3b8" })[id] ?? "#94a3b8";
