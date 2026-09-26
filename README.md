# Bot Battle: AI Crypto Trading Arena

Six bots each get **$10,000 in demo money** and trade live crypto markets against each other: a machine-learning bot, a trend follower, three rule-based bots with different risk levels, and a buy-and-hold benchmark. Every thought, forecast, order, fill, stop move and exit shows up on a dashboard built for streaming.

![Dashboard](docs/screenshots/dashboard.png)

| Bot | Type | Style |
|---|---|---|
| **ORACLE** 🟣 | Machine learning (Gen 2) | Gradient-boosted model trained on 6 years of hourly data from 10 coins (64 features). Forecasts each coin's 14-day trade outcome every hour and trades only its top 10% of forecasts, only while BTC is above its 200-day average. Retrains daily. |
| **NOMAD** 🔵 | Trend follower (Gen 2) | Daily charts. Buys 20-day-high breakouts in a bull market, no profit target, 3-ATR trailing stop judged on daily closes, goes to cash when BTC falls below its 200-day average. A few trades a month. |
| **SENTINEL** 🟢 | Low | 0.5% risk per trade, BTC & ETH only, 1h signals / 6h trend, limit (maker) entries, −2% daily circuit breaker |
| **TACTICIAN** 🟡 | Medium | 1% risk per trade, 6 majors, 15m signals / 1h trend, limit entries, −4% daily circuit breaker |
| **BERSERKER** 🔴 | High | 2% risk per trade, all 10 coins incl. volatile alts, 15m / 1h, market (taker) orders, big size, −8% daily circuit breaker |
| **HODL** ⚪ | Benchmark | Buys BTC once at the start and never sells. The line every bot has to beat. |

The SENTINEL/TACTICIAN/BERSERKER rows are the Gen 1 rule bots: Low, Medium and High risk.

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

## ORACLE: how it was trained and tested

- **Data**: every hourly Coinbase candle for 10 coins since 2020-01-01 (about 460k candles), cached in `DATA_DIR/history`.
- **Features (64)**: momentum over 1h to 30 days, volatility regime, trend distance, ADX, RSI, Bollinger squeeze, range position, drawdown, volume, candle shape, time of day. Also BTC context, relative strength vs BTC, cross-coin momentum rank, and market breadth. Every feature uses only data up to the moment of the decision.
- **Target**: the outcome of a long trade with a stop at 2× daily volatility, a target at 4×, and a 14-day limit, measured in R (1R = the planned risk).
- **Validation**:
  - Walk-forward: retrained every 60 days on past data only, with purging and a 24h embargo so no future information leaks in.
  - The design was chosen on **2022-01 → 2026-03** only. The last 6 months (**2026-03-26 → today**) were kept aside as an untouched **holdout** and checked once, at the end.
  - Both periods were then replayed through the real trading engine (`app.backtest_long`) with fees.
- **Live**: at first start it downloads the history (about 3 minutes), trains in seconds, retrains daily, and forecasts every hour. Its forecasts appear in the **Oracle forecasts** panel.

```bash
cd backend
.venv/bin/python -m app.ml.research --tp 4 --sl 2 --horizon 336 --kind reg --retrain-days 60 --tag _Dr   # walk-forward
.venv/bin/python -m app.backtest_long --period dev        # 2022-01-01 .. 2026-03-26
.venv/bin/python -m app.backtest_long --period holdout    # 2026-03-26 .. now
```

### Results: real engine, $10,000 start, Coinbase Advanced 2 fees

| Bot | Dev 2022–2026/03 | Max drop | Holdout 2026/03–09 | Max drop |
|---|---|---|---|---|
| ORACLE | **+34.5%** (407 trades, 46% wins) | 25.1% | **+12.4%** (17 trades) | 3.5% |
| NOMAD | **+63.9%** (139 trades, 35% wins) | 27.9% | **+13.8%** (6 trades) | 7.4% |
| SENTINEL (Gen 1) | −26.2% | 26.2% | −3.4% | 3.7% |
| HODL BTC | +52.5% | **67.4%** | +17.8% | 28.6% |

How to read this:
- **The Gen 2 bots are the first ones that made money after fees.** They did it through both the 2022 crash and the 2026 holdout. The Gen 1 intraday bots lost steadily.
- **Neither beat simply holding BTC on raw return in the holdout.** NOMAD beat it over the development period. Their real strength is risk: roughly half of HODL's worst drop in dev, and a fifth or less in the holdout. They sat in cash through the 2022 crash while HODL fell 65%.
- **The ML model is only modestly predictive.** Out-of-sample AUC is about 0.56 (0.5 = coin flip). Most of ORACLE's edge comes from the bull-market filter. In the holdout, the filter alone did as well as the model.
- **Small changes matter.** NOMAD ranged from +64% to +82% depending only on its circuit-breaker setting. ORACLE made +4% with passive limit entries versus +34.5% with market entries (limit orders filled more on losers). Treat any single number as ±20%.
- The holdout has very few trades. It is evidence, not proof.

## Gen 1 5-minute backtest

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

The 1-hour Gen 1 bot (SENTINEL) also runs in the 4-year replay above. TACTICIAN and BERSERKER need sub-hour candles, so they are tested with this 5-minute replay only.

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
  ml/          ORACLE: history store, features, walk-forward research, live service
  arena.py     runs the 6 bots, persistence, views
  main.py      FastAPI REST + WebSocket, serves the dashboard
  backtest.py       5-minute historical replay (Gen 1 bots)
  backtest_long.py  multi-year hourly replay of the real engine (ORACLE, NOMAD, SENTINEL, HODL)
frontend/src/  React + Tailwind + lightweight-charts dashboard
```

---

**Demo money only. Not financial advice.** Fees, slippage and taxes are estimates. Talk to a tax professional about your real situation.
