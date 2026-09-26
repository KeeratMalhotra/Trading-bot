export type BotId = "low" | "medium" | "high";

export interface Price {
  s: string;
  p: number;
  b: number;
  a: number;
  chg: number;
  ts: number;
}

export interface TaxEstimate {
  federal: number;
  niit: number;
  state: number;
  total: number;
  effective_rate: number;
  marginal_federal: number;
  state_rate: number;
  short_term: number;
  long_term: number;
  loss_carryforward: number;
}

export interface Bot {
  id: BotId;
  name: string;
  label: string;
  color: string;
  tagline: string;
  equity: number;
  cash: number;
  start: number;
  total_return: number;
  status: string;
  halted: boolean;
  paused: boolean;
  open: number;
  exposure: number;
  throttle: number;
  benched: string[];
  rank: number;
  today: {
    pnl: number;
    pnl_pct: number;
    realized: number;
    fees: number;
    trades: number;
    wins: number;
    losses: number;
    tax: number;
    after_tax: number;
    start_equity: number;
    limit_used: number;
  };
  all: {
    trades: number;
    wins: number;
    losses: number;
    win_rate: number | null;
    profit_factor: number | null;
    fees: number;
    realized: number;
    best: number;
    worst: number;
    max_dd: number;
    volume: number;
    slippage: number;
    streak: number;
  };
  tax: TaxEstimate;
  profile: {
    risk_per_trade: number;
    daily_loss_limit: number;
    max_open: number;
    symbols: string[];
    signal_tf: string;
    trend_tf: string;
    entry_order: string;
    min_net_rr: number;
    min_confidence: number;
    strategies: string[];
  };
}

export interface Position {
  id: string;
  bot: BotId;
  symbol: string;
  strategy: string;
  status: "opening" | "open" | "closing";
  qty: number;
  entry: number;
  price: number;
  stop: number;
  initial_stop: number;
  target: number;
  partial: number | null;
  partial_done: boolean;
  opened: number;
  pnl: number;
  pnl_pct: number;
  r: number;
  confidence: number;
  headline: string;
  reasons: string[];
  trailing: boolean;
  be: boolean;
  value: number;
  timeline: { ts: number; kind: string; title: string }[];
}

export interface Trade {
  id: string;
  bot: BotId;
  symbol: string;
  strategy: string;
  opened: number;
  closed: number;
  qty: number;
  entry: number;
  exit: number;
  gross: number;
  fees: number;
  net: number;
  r: number;
  ret_pct: number;
  reason: string;
  reason_label: string;
  confidence: number;
  headline: string;
  tax: number;
  after_tax: number;
  term: string;
  stop: number;
  target: number;
}

export interface BotEvent {
  id: string;
  ts: number;
  bot: BotId | "system";
  kind: "thought" | "setup" | "pass" | "order" | "fill" | "open" | "stop" | "partial" | "close" | "risk" | "system";
  title: string;
  text: string;
  symbol: string | null;
  level: "info" | "good" | "bad" | "warn" | "action";
  data: Record<string, unknown>;
}

export interface Fees {
  mode: string;
  tier: string;
  maker: number;
  taker: number;
  volume30d: number;
  tiers?: { name: string; min: number; maker: number; taker: number }[];
}

export interface MarketStatus {
  source: string;
  connected: boolean;
  message: string;
}

export interface Live {
  ts: number;
  prices: Price[];
  bots: Bot[];
  positions: Position[];
  market: MarketStatus;
  fees: Fees;
  regimes: Record<string, Record<string, string>>;
}

export interface TaxSettings {
  filing_status: "single" | "mfj";
  other_income: number;
  state: string;
  state_name: string;
  state_rate: number;
  state_rate_override: number | null;
}

export interface Meta {
  mode: string;
  started: number;
  starting_balance: number;
  symbols: string[];
  market: MarketStatus;
  fees: Fees;
  tax: TaxSettings;
  states: { code: string; name: string; rate: number }[];
  timeframes: Record<string, string>;
}

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}
