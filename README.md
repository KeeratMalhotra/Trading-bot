# Bot Battle: AI Crypto Trading Arena

Three trading bots with different risk levels each get **$10,000 in demo money** and trade live crypto markets against each other. Every thought, order, fill, stop move and exit shows up on a dashboard built for streaming.

![Dashboard](docs/screenshots/dashboard.png)

| Bot | Risk | Style |
|---|---|---|
| **SENTINEL** 🟢 | Low | 0.5% risk per trade, BTC & ETH only, 1h signals / 6h trend, limit (maker) entries, −2% daily circuit breaker |
| **TACTICIAN** 🟡 | Medium | 1% risk per trade, 6 majors, 15m signals / 1h trend, limit entries, −4% daily circuit breaker |
| **BERSERKER** 🔴 | High | 2% risk per trade, all 10 coins incl. volatile alts, 15m / 1h, market (taker) orders, big size, −8% daily circuit breaker |

## Quick start

**Docker (easiest)**
```bash
docker compose up -d --build     # → http://localhost:8000
```

**Without Docker** (Python 3.11+, Node 20+)
```bash
./run.sh                          # → http://localhost:8000
```

Battle state is saved in SQLite, so restarting keeps balances, open trades and history.

### Streaming with OBS
1. Add a **Browser Source** → URL `http://localhost:8000`, width **1920**, height **1080**.
2. In the dashboard, the 🎥 button turns on **Auto camera**: the chart jumps to every trade, then returns to the race.
3. The 🔊 button turns on trade sounds (win / loss / alert). Click it once so the browser allows audio.
4. If viewers can open the URL, set `ADMIN_TOKEN` in `.env` so only you can change settings, pause bots or reset.

## What makes the demo feel real

- **Live market data**: Coinbase public websocket ticker (price and top of the order book) plus REST candle history. If Coinbase is unreachable it falls back to a simulator, and the UI then shows a **SIMULATED MARKET** badge.
- **Coinbase Advanced fee tiers**: maker/taker fees on every fill. The default tier is `Advanced 2` (0.15% / 0.25%). `auto` derives the tier from the arena's own 30-day volume. Verify the current schedule at coinbase.com. The table lives in `backend/app/config.py`.
- **Execution modeling**:
  - Market orders get 80–350 ms latency and fill at the bid/ask at that moment, walking deeper book levels when bigger than the top-of-book size.
  - Resting limit orders fill only when price trades *through* them, so partial fills happen.
  - Post-only orders are rejected if they would cross.
- **US taxes (2026)**:
  - FIFO lots per account.
  - Short-term gains are taxed as ordinary income (10–37% federal, IRS Rev. Proc. 2025-32 brackets); long-term 0/15/20%.
  - 3.8% NIIT, the $3,000 loss deduction, and an approximate state rate for all 50 states + DC.
  - Every trade shows estimated tax and after-tax P&L.
  - Per-bot CSV export in Form 8949 / 1099-DA style.
- **Spot only, long only, no leverage**, the same way a US retail account on Coinbase works.

## How the bots think

`market data → regime detection → strategies → risk manager → execution → trade management`

- **Regime detection**: higher-timeframe trend (EMA50/200 + slope), ADX trend strength, and ATR volatility percentile. Each market is classified as Uptrend, Downtrend, Ranging or Volatile.
- **Strategies** (closed candles only, no repainting): Trend Pullback, Breakout (volume + squeeze), Mean Reversion (Bollinger + RSI), Momentum (high risk only). A Trend Ride strategy is also available, but it is off by default because it lost in backtests.
- **Risk manager**. A setup only becomes a trade if:
  - confidence is at or above the bot's bar;
  - reward/risk **after fees and slippage** is at or above the bot's minimum;
  - fees eat no more than 30% of the planned risk;
  - there is free capital and room under the exposure and position caps.

  Position size = risk budget ÷ (stop distance + costs).
- **Trade management**:
  - Stop and take-profit orders are placed at entry.
  - Stop moves to breakeven + fees at about 1R, then a trailing ATR stop.
  - Partial profit-taking (low and medium bots).
  - Time stop for dead trades; the stop tightens when the higher-timeframe trend flips.
- **Self-protection**:
  - Daily loss circuit breaker (closes everything, resumes at midnight ET).
  - Position size is cut after 3 or 5 losses in a row.
  - A strategy is **benched** for 6h after it loses about 3.5R over its last 5 trades.

## Honest backtest

Real Coinbase history replayed through the exact same engine. Each 5-minute candle is split into 4 ticks, and stops are checked before targets.

```bash
cd backend && .venv/bin/python -m app.backtest --days 60
```

Last 60 days (to Sep 26 2026), Advanced 2 fees:

| Bot | Return | Trades | Win % | Gross P&L (before fees) | Fees | Max DD |
|---|---|---|---|---|---|---|
| SENTINEL | **+0.26%** | 7 | 57% | +$86 | $60 | 1.5% |
| TACTICIAN | **−0.46%** | 42 | 45% | +$477 | $523 | 8.7% |
| BERSERKER | **−19.72%** | 81 | 40% | −$568 | $1,405 | 22.5% |
| *Buy & hold BTC* | *+31.6%* | – | – | – | – | – |

What this means:
- In a strong bull market, all three bots badly trailed simply holding BTC.
- Fees are the biggest enemy. Tactician was profitable *before* fees.
- High risk mostly bought pain.

This is typical for short-term trading. It's also great content: viewers see *why* most traders lose, in real numbers. Past results don't predict future ones.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `ADMIN_TOKEN` | *(empty)* | Required for settings/reset/pause when set |
| `MARKET_SOURCE` | `auto` | `auto`, `coinbase`, or `sim` |
| `STARTING_BALANCE` | `10000` | Demo dollars per bot |
| `FEE_MODE` | `Advanced 2` | `auto` or any tier name |
| `DATA_DIR` | `backend/data` | Where the SQLite state lives |

Tax profile (filing status, other income, state) and fee tier can also be changed live in **Settings** (⚙).

## Going live with real money (future)

Everything goes through an `ExecutionVenue` interface (`backend/app/execution/base.py`). `PaperExchange` implements it today. `CoinbaseLiveVenue` (`execution/coinbase_live.py`) implements the same interface against the Coinbase Advanced Trade API:
- CDP JWT auth;
- market and post-only limit orders;
- cancel, fill polling and the real fee tier;
- a per-order USD cap and a kill switch.

It is **deliberately not wired in**. Before enabling it:
1. Sync bot balances from the real account instead of the virtual $10k.
2. Test the adapter with tiny size on a funded account.
3. Use a CDP key with **trade permission only, never withdraw**.
4. Keep `LIVE_MAX_ORDER_USD` small.

## Project layout

```
backend/app/
  market/      Coinbase feed, candle builder, simulator, MarketHub (single market clock)
  engine/      indicators, regime, strategies, risk manager, bot brain
  execution/   venue interface, paper exchange, fee tiers, Coinbase live adapter
  accounting/  FIFO portfolio, US tax engine
  arena.py     runs the 3 bots, persistence, views
  main.py      FastAPI REST + WebSocket, serves the dashboard
  backtest.py  historical replay
frontend/src/  React + Tailwind + lightweight-charts dashboard
```

---

**Demo money only. Not financial advice.** Fees, slippage and taxes are estimates. Talk to a tax professional about your real situation.
