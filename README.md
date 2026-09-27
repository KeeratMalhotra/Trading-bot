# QUORUM

Three trading agents share one account on live US crypto markets. They trade long and short, copy each other's winning strategies, and a desk moves capital to whichever agent is working. The dashboard is a minimalist, broker-style view of the account.

![Dashboard](docs/screenshots/dashboard.png)

*Screenshot: a replay of the last 120 days of real market data, then continued live.*

## The team

| Agent | Role | What it does |
|---|---|---|
| **ATLAS** | Regime strategist | Bull market (BTC above its 200-day average): holds BTC. Otherwise it trades breakouts in both directions, shorting breakdowns in bear markets. |
| **ORACLE** | Machine learning | Two gradient-boosted models (long and short) trained on hourly data for 10 coins since 2020, with 64 features. It forecasts every coin's 14-day trade outcome every hour and takes only its top-10% ideas: longs in bull markets, shorts in bear markets. Stop at 2× and target at 4× daily volatility, placed exactly where the model was trained to expect them (shorts use the mirrored levels). Retrained daily. |
| **NOVA** | Adaptive learner | Shadow-tracks every strategy on the team, including its teammates'. Each week it backs all strategies with a positive 90-day record, risk-weighted, and goes to cash when nothing works. |
| **DESK** | Allocator + risk | At each month end it moves capital between the agents by their 90-day risk-adjusted return (each keeps 20–60%). It nets the agents' orders so opposite trades never pay fees, caps leverage and margin, and applies news vetoes. |

**News desk:** live headlines from Cointelegraph, Decrypt, The Block and CoinDesk. A severe headline about a coin (hack, exploit, delisting, outage…) blocks new longs in that coin for 48 hours. Legal and crime headlines (lawsuit, fraud, arrest…) only block smaller coins; for BTC and ETH they're almost always about people, not the network. Headlines about a resolution ("lawsuit dismissed", "funds returned") don't count. Several market-wide severe headlines pause all new longs for 12 hours. Matching is on whole words, so "hackathon" isn't a hack and "link" isn't Chainlink. Every veto, and what it held back, shows up in the activity log. This is live-only; there's no free headline archive to backtest it on.

**Instruments:** Coinbase spot, plus Coinbase Derivatives **US perpetual-style futures** (nano contracts such as `BIP-20DEC30-CDE`, 0.01 BTC each, with hourly funding). Shorts and cheap directional trades go through futures. Futures are Section 1256 contracts, taxed 60% long-term / 40% short-term.

## Results: real engine, replayed on real data

`python -m app.team.replay --start 2022-01-01 --balance 30000`

This is the live engine code run hour by hour, with 4 price ticks per hour:
- small-account fees, with futures at 0.05% per side plus slippage;
- extra slippage when stops fire;
- hourly funding;
- whole-contract sizing and margin limits.

| Jan 2022 → Sep 2026 | QUORUM | BTC buy & hold |
|---|---|---|
| Total return | **+126%** | +83% |
| Max drawdown | **43%** | 67% |
| Worst month | **−16.4%** | −37.1% |
| Up / down months | 30 / 27 | 32 / 25 |
| 2022 | **+16.8%** | −64.2% |
| 2023 | +33.6% | +155.8% |
| 2024 | +15.8% | +120.8% |
| 2025 | **+3.8%** | −6.3% |
| 2026 (to Sep) | **+20.3%** | −3.5% |

It was positive every calendar year, including the 2022 crash. It lags far behind BTC in strong bull years.

### Out-of-sample: 2017–2021

The team was designed on 2022–2026 data, so that period can't show whether the design works. Nobody looked at 2017–2021 while building it, so the same code was run there unchanged (see [Longer history](#longer-history)). ORACLE was retrained walk-forward from Sept 2016, and each model saw only earlier data.

| Jan 2017 → Dec 2021 (never used for design) | QUORUM | BTC buy & hold |
|---|---|---|
| Total return | **+1,192%** (67%/yr) | +4,648% (116%/yr) |
| Max drawdown | **48%** | 84% |
| Sharpe | 1.34 | 1.36 |
| Worst month | **−25.8%** | −36.9% |
| 2017 / 2018 / 2019 / 2020 / 2021 | +464% / **+9%** / +29% / +53% / +7% | +1,324% / −73% / +94% / +305% / +59% |

It sat out the 2018 crash and ended positive in every year. It matched BTC risk-adjusted and had about half of BTC's worst drawdown, but made a fraction of BTC's bull-market gains. Run straight through from Jan 2017 to Sep 2026, $30,000 became about $878,000, with positive results in all ten calendar years.

**Read these caveats before trusting the numbers:**
- **2022–2026 is in-sample for the team design.** ORACLE's forecasts are walk-forward, so each model saw only earlier data. But the team structure was chosen after looking at 2022–2026 results: which strategies each agent uses, how NOVA and the desk allocate, and dropping a month-to-date brake that halved returns. 2017–2021 is the honest test, and it has its own caveats below.
- **2017–2021 had few coins.** BTC, ETH and LTC only until 2019, then XRP (until its 2021 delisting) and LINK. ADA, DOGE, SOL and AVAX joined in 2021.
- **The perps didn't exist back then.** Coinbase launched US perpetual-style futures in July 2025. Every earlier year assumes today's futures, fees and slippage. For BTC and ETH, dated monthly futures would have been a close substitute.
- **Funding is a proxy before live recording.** Funding is the biggest cost: about 5× the fees over 2017–2026. Before Coinbase's rates were recorded, history comes from Deribit (2019 onward) and BitMEX (2016–2019). Coinbase doesn't publish a history, so the app records its real hourly rates from the first day it runs and uses them from then on.
- **ORACLE's machine learning is the least reliable part.** Its long trades made +118% over 2022–2026 when retrained from 2022, but only +36% when the same walk-forward started in 2016. Both are valid tests. In 2017–2021 its long picks were no better than chance. ATLAS's trend following is what held up in both periods.
- **The engine replay and the daily research sim differ by several points in some years** (e.g. 2022: +17% vs +24%). NOVA and the desk allocate by the research sim.
- **It lost money in 27 of 57 months in 2022–2026.** No strategy here makes money every month. The target is positive over a quarter or a year.
- At the worst point the account was 48% below its peak. Expect live results to be worse than any backtest.

## Run it

```bash
docker compose up -d --build        # → http://localhost:8000
# or, with Python 3.11+ and Node 20+:
./run.sh
```

**First start takes about 10 minutes before the first trade.** It downloads 6+ years of hourly candles and funding history, trains the models, and rebuilds 240 days of forecast history. The top bar shows progress. Everything is cached in the data volume, so later restarts take seconds, and state (positions, stops, history) survives restarts.

Prices are always live Coinbase prices. If Coinbase can't be reached at startup, the app keeps retrying; it never falls back to simulated prices. If the price feed stops for over a minute, trading pauses: the top bar says so, the activity log records it, and trading resumes when prices return.

### Streaming 24/7

1. **Start it so it restarts itself.**
   - **Docker:** `docker compose up -d --build`. It restarts after crashes and after reboots, as long as Docker starts at login.
   - **`./run.sh`:** restarts the server if it ever exits. On a Mac it also keeps the computer from sleeping while running.
2. **Keep the computer awake and online.** Turn off sleep (on Windows: Power settings → Sleep → Never), stay plugged in, and prefer wired internet. A closed MacBook lid still sleeps unless an external display and power are connected.
3. **OBS:** add a Browser Source at `http://localhost:8000`, 1920×1080, and tick *Refresh browser when scene becomes active*. The page reconnects by itself after a server restart.
4. **Check on it:** `curl localhost:8000/api/health` shows the feed, forecaster, desk, news desk and funding recorder status.
5. **Labelling:** keep the **PAPER TRADING** badge and put a line in the stream description, for example "Paper trading: simulated orders on live Coinbase prices. Not financial advice." Viewers may act on what they see.
6. **Closing and reopening:** the account is saved every 10 seconds and on shutdown, and restored on the next start. That covers cash, positions, stops and history. While it's closed, nothing is simulated: no trades, no stops, and no funding for those hours. A stop crossed in the meantime fills at the price when it reopens. ORACLE retrains for a minute or two after each start before opening new trades.
7. **Changing the starting amount / starting over:** `TEAM_BALANCE` only applies to a new account. To start over, stop the app, set `TEAM_BALANCE`, and delete the saved account. The downloaded history is kept, so it's trading again within minutes:
   - `./run.sh`: `rm -f backend/data/botbattle.sqlite3*`
   - Docker: `docker compose stop && docker compose run --rm botbattle sh -c 'rm -f /data/botbattle.sqlite3*' && docker compose up -d`

### Configuration (`.env`)

| Variable | Default | |
|---|---|---|
| `TEAM_BALANCE` | `30000` | Starting account value (USD). Only used when a new account is created; see "Changing the starting amount" above. Paper fills don't model order-book depth, so very large amounts (e.g. $1M) look better on paper than they would live |
| `TAX_FILING_STATUS` | `single` | `single` or `mfj` |
| `TAX_OTHER_INCOME` | `75000` | Sets your tax bracket |
| `TAX_STATE` | `XX` | Two-letter state code (`XX` = federal only) |
| `ADMIN_TOKEN` | *(empty)* | Protects the settings API. Set it if anyone else can reach the dashboard |
| `CASH_APY` | `0` | Yield on idle cash (cash not needed as futures margin), e.g. `0.0375`. Only set it if your real account would earn it (e.g. Coinbase One with idle cash held as USDC). It's shown on the dashboard and counted as ordinary income in the tax estimate |
| `MARKET_SOURCE` | `auto` | `auto`/`coinbase` = live Coinbase prices; `sim` = offline simulator for demos |

## Dashboard

- **Account:** net liquidation value, month-to-date and today's P&L, since-inception return, net/gross exposure, and margin in use.
- **Desk:** each agent's current activity, allocation, capital, % invested, and P&L today, this month and since start. Click an agent to filter the activity log.
- **Performance:** account vs BTC buy & hold. **Market:** candles with the team's fills, average prices and ORACLE's stops and targets.
- **Positions:** spot and futures, contracts, average price, mark, unrealized P&L and funding, each position's exit plan, and which agents hold it.
- **Activity:** every decision, fill, plan change, allocation and news veto, time-stamped in New York time.
- **Side panel:**
  - daily P&L calendar and monthly returns;
  - ORACLE's live forecasts and open/closed trades;
  - news with tone scores;
  - costs: fees, funding (and whether the rates are Coinbase's recorded ones or the Deribit proxy), what netting saved, and interest on idle cash if `CASH_APY` is set;
  - an estimated US tax with CSV export.

The account is labelled **PAPER TRADING**: orders are simulated against live Coinbase prices. Keep that label (and a line in the stream description) while it's paper, because viewers may act on what they see.

## Research commands

```bash
cd backend
python -m app.ml.data                   # hourly history (Coinbase)
python -m app.ml.altdata                # funding rates (Deribit) + Fear & Greed
python -m app.ml.research --kind reg --tp 4 --sl 2 --horizon 336 --retrain-days 60 --tag _Dr                # ORACLE long, walk-forward
python -m app.ml.research --kind reg --side short --tp 4 --sl 2 --horizon 336 --retrain-days 60 --tag _Sr   # ORACLE short
python -m app.team.research --spot-maker 0.006 --spot-taker 0.012    # daily team simulation
python -m app.team.replay --start 2022-01-01 --balance 30000         # full engine replay (add --cash-apy 0.0375 for idle-cash yield)
```

### Longer history

The app itself only needs data from 2020. The 2017–2021 test above needs older history, and it's best kept in a separate data folder so the live app's cache stays as it is:

```bash
cd backend
export DATA_DIR=$PWD/data-long          # separate folder for the long test
python -m app.ml.data --since 2015-07-20              # Coinbase hourly candles back to 2015 (~3 min)
python -m app.ml.altdata --since 2016-01-01           # BTC/ETH funding back to 2016: Deribit + BitMEX (~2 min)
python -m app.ml.research --kind reg --tp 4 --sl 2 --horizon 336 --retrain-days 60 --tag _Dr --oos-start 2016-09-01
python -m app.ml.research --kind reg --side short --tp 4 --sl 2 --horizon 336 --retrain-days 60 --tag _Sr --oos-start 2016-09-01
python -m app.team.research --spot-maker 0.006 --spot-taker 0.012 --start 2017-01-01
python -m app.team.replay --start 2017-01-01 --end 2022-01-01 --balance 30000
```

## Going live (not built yet)

Orders are paper. `execution/coinbase_live.py` places **spot** orders only, was written for the older single-bot engine, and isn't used by QUORUM. Real money needs:
- futures orders;
- syncing balances, positions, margin and funding with the Coinbase Financial Markets account;
- stop orders resting on the exchange;
- a kill switch and order and loss limits.

Then roll it out read-only first, and after that with a small amount.

## Layout

```
backend/app/
  team/        QUORUM: engine (agents, desk, execution), research sim, account book, forecaster, news, funding recorder, replay
  ml/          history store, features, models, walk-forward research, funding/sentiment data
  market/      Coinbase websocket + candles
  accounting/  FIFO lots, US tax
  engine/, arena.py, backtest*.py   previous single-bot engine (still used by app.backtest / app.backtest_long)
frontend/src/  React + Tailwind + lightweight-charts dashboard
```

Not financial advice. Tax figures are estimates.
