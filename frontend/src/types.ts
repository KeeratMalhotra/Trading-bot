export type AgentId = "ATLAS" | "ORACLE" | "NOVA";

export interface Exposure {
  net: number;
  gross: number;
  long: number;
  short: number;
  by_coin: Record<string, number>;
}

export interface Account {
  equity: number;
  deposits: number;
  cash: number;
  pnl_today: number;
  pnl_mtd: number;
  ret_mtd: number;
  pnl_all: number;
  ret_all: number;
  month: string;
  days_left: number;
  exposure: Exposure;
  fees: { spot: number; perp: number };
  funding: number;
  netting_saved: number;
  margin: number;
  interest?: number;
  cash_apy?: number;
  idle_cash?: number;
  funding_source?: "coinbase" | "deribit" | "mixed" | null;
  funding_hours?: number;
  tax_ytd: {
    total: number;
    federal: number;
    state: number;
    niit: number;
    short_term: number;
    long_term: number;
    section_1256: number;
    spot_short_term: number;
    spot_long_term: number;
    interest?: number;
  };
  fee_tier: string;
  costs: { long_venue: string; carry_on: boolean };
}

export interface Agent {
  id: AgentId;
  color: string;
  role: string;
  about: string;
  weight: number;
  capital: number;
  mode: string;
  detail: [string, number][];
  invested: number;
  pnl_today: number;
  pnl_mtd: number;
  pnl_all: number;
  fees: number;
  funding: number;
  targets: { key: string; weight: number }[];
}

export interface Position {
  key: string;
  instrument: string;
  product?: string;
  venue: "spot" | "perp";
  side: "long" | "short";
  qty: number;
  contracts?: number;
  avg: number;
  mark: number;
  value: number;
  pnl: number;
  funding?: number;
  weight: number;
  owners: Record<string, number>;
}

export interface OracleTrade {
  id: string;
  symbol: string;
  side: "long" | "short";
  entry: number;
  stop: number;
  target: number;
  opened: number;
  expires: number;
  forecast: number;
  thr: number;
  reasons: string[];
  exit?: number;
  closed?: number;
  ret?: number;
  R?: number;
  why?: string;
}

export interface Live {
  ts: number;
  account: Account;
  agents: Agent[];
  positions: Position[];
  oracle: { open: OracleTrade[]; closed: OracleTrade[]; wins: number; trades: number };
  regime: { btc_on: boolean | null; btc_close?: number; btc_sma200?: number; fear_greed?: number | null; day?: number };
  nova: { picks: Record<string, number>; sharpe: Record<string, number>; labels: Record<string, string> };
  quotes: { s: string; p: number; chg: number }[];
  market: { source: string; connected: boolean; message?: string };
  engine: { forecaster: string; ready: boolean; desk_ready: boolean; paused?: boolean };
}

export interface Event {
  id: string;
  ts: number;
  kind: string;
  agent: string;
  title: string;
  text: string;
  level: "info" | "good" | "bad" | "warn";
  symbol: string | null;
  data: Record<string, unknown>;
}

export interface Fill {
  ts: number;
  venue: string;
  symbol: string;
  instrument: string;
  side: string;
  qty: number;
  contracts: number | null;
  price: number;
  fee: number;
  realized: number;
  reason?: string;
}

export interface History {
  equity: [number, number][];
  btc: [number, number][];
  agents: Record<string, [number, number][]>;
  day_pnl: Record<string, number>;
  months: Record<string, { pnl: number; ret: number }>;
}

export interface OracleState {
  state: string;
  status: string;
  ready: boolean;
  bar_t: number | null;
  thr_long: number | null;
  thr_short: number | null;
  trained_at: number | null;
  model: { train_seconds?: number; samples?: number; features?: number; hours?: number };
  forecasts: { symbol: string; long: number | null; short: number | null; dvol: number; reasons: string[] }[];
}

export interface NewsItem {
  id: string;
  title: string;
  link: string;
  ts: number;
  source: string;
  score: number;
  severe: boolean;
  coins: string[];
}

export interface News {
  status: string;
  mood_24h: number | null;
  vetoes: Record<string, number>;
  headline_risk_until: number | null;
  items: NewsItem[];
}

export interface Meta {
  agents: Record<string, { color: string; role: string; about: string }>;
  symbols: string[];
  started: number;
  deposits: number;
  account: string;
  tax: { filing_status: string; other_income: number; state: string; state_name: string; state_rate: number };
  states: { code: string; name: string; rate: number }[];
}

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}
