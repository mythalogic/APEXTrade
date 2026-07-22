# APEX — AI Day Trading Platform

APEX is a personal, single-user day-trading system that uses the Claude API to generate a trading strategy each morning, executes it through Alpaca, monitors its own performance in real time, and automatically regenerates the strategy when it starts to underperform. Every strategy change and trade is pushed to you over Telegram, and hard risk controls (daily loss limit, position sizing, kill switch) run before every order.

> ⚠️ **This is trading software that can place real orders with real money.** It ships in paper mode by default. Read the [Safety](#safety--risk-controls) section before ever setting `ALPACA_PAPER=false`. This project is not financial advice.

---

## How it works

A single Python process (`APEXTrade.py`) runs five services orchestrated by an in-process APScheduler:

| Service | Module | Schedule |
|---|---|---|
| Data Pipeline | `data_pipeline.py` | Every 5 min (market hours) — fetch OHLCV bars, compute RSI/MACD/EMA/ATR/volume ratio |
| Strategy Engine | `strategy_engine.py` | 6:00am EST + on drift — call Claude, validate JSON, version & store strategy |
| Execution Engine | `execution_engine.py` | Continuous (market hours) — scan entry signals, place/manage Alpaca orders, sync positions |
| Drift Monitor | `drift_monitor.py` | Every 30 min — check win rate, drawdown, VIX spike; regenerate strategy if breached |
| Web Dashboard | FastAPI + React | Always on, port 8000 |

A typical day: at 6am the Data Pipeline builds a market-context JSON, the Strategy Engine asks Claude for a structured strategy (entry/exit conditions, stop loss, position limits, reasoning), and you get a Telegram alert. During market hours the Execution Engine trades that strategy while the Drift Monitor watches performance. If, say, the win rate drops below its baseline, trading pauses, Claude generates a new version, and trading resumes — with an alert explaining what changed and why. At close, open positions are handled and an end-of-day summary is sent.

---

## Project structure

```
apex/
├── APEXTrade.py          # Main entry point (scheduler, CLI, service wiring)
├── config.py             # Loads all settings from environment / .env
├── database.py           # SQLAlchemy ORM models (7 tables) + init/seed
├── data_pipeline.py      # OHLCV fetch, indicators, market-context builder, backfill
├── strategy_engine.py    # Claude strategy generation, JSON validation, versioning
├── execution_engine.py   # Alpaca order placement, exit/stop management, position sync
├── drift_monitor.py      # Win-rate / drawdown / VIX drift detection + regeneration
├── risk_manager.py       # Pre-order checks, position sizing, kill switch
├── alert_manager.py      # Telegram alerts with retry + full alert log
├── requirements.txt
└── _env.template         # Copy to .env and fill in credentials
```

**Database (SQLite, WAL mode) — 7 tables:** `strategies`, `trades`, `positions`, `ohlcv_bars`, `drift_events`, `alerts_log`, `system_state`. Full audit trail: every strategy version, drift event, and alert is persisted so any trading day can be reconstructed.

---

## Requirements

- Python 3.11+
- Accounts / API keys for:
  - [Alpaca](https://docs.alpaca.markets) — market data + order execution (a paper account is enough to start)
  - [Anthropic Claude](https://docs.anthropic.com) — strategy generation
  - [Telegram Bot](https://core.telegram.org/bots) — alerts (via @BotFather)
  - [Alpha Vantage](https://www.alphavantage.co/support/#api-key) — news headlines (free tier is fine)

---

## Setup

```bash
# 1. Clone and enter the project
git clone <your-private-repo>
cd apex

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure credentials
cp _env.template .env
#    then edit .env and fill in your keys (see below)

# 5. Verify connectivity to Alpaca, Claude, Telegram and the DB
python APEXTrade.py --check
```

The database is created and seeded automatically on first run (or first CLI command) — there's no separate init step required.

### Configuration (`.env`)

Copy `_env.template` to `.env` and set your values. Key settings:

| Variable | Purpose | Default |
|---|---|---|
| `ALPACA_API_KEY` / `ALPACA_API_SECRET` | Broker credentials | — |
| `ALPACA_PAPER` | `true` = paper (safe), `false` = **live/real money** | `true` |
| `ANTHROPIC_API_KEY` | Claude API key | — |
| `CLAUDE_MODEL` | Model used for strategy generation | `claude-sonnet-4-6` |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Alert delivery | — |
| `ALPHA_VANTAGE_KEY` | News headlines for market context | `demo` |
| `DAILY_LOSS_LIMIT_PCT` | Stop all trading past this daily loss % | `2.0` |
| `MAX_POSITION_PCT` | Max % of capital in any single trade | `2.0` |
| `DEFAULT_TICKERS` | Symbols to trade | `SPY,QQQ,NVDA` |
| `DASHBOARD_*` | Dashboard port / login | port `8000` |
| `DB_PATH` | SQLite path | `apex.db` |

**Never commit `.env`.** Change `DASHBOARD_PASSWORD` and `DASHBOARD_SECRET_KEY` before deploying anywhere reachable.

---

## Usage

```bash
python APEXTrade.py            # Start the full platform (scheduler + dashboard)
python APEXTrade.py --check    # API connectivity health check
python APEXTrade.py --status   # Print current system status
python APEXTrade.py --backfill # Backfill historical OHLCV data via yfinance
python APEXTrade.py --kill     # Activate the kill switch immediately (halt all trading)
python APEXTrade.py --resume   # Reset the kill switch
```

Once running, open the dashboard at `http://localhost:8000`. The process handles `SIGINT`/`SIGTERM` gracefully — on shutdown it cancels open orders before exiting. Logs are written to `logs/apex.log`.

---

## Safety & risk controls

These run automatically and are designed to be non-bypassable:

- **Kill switch** — a global flag in `system_state`. Every service checks it before acting. If the database can't be read, the check fails *closed* (blocks orders). Trigger manually with `--kill`.
- **Daily loss limit** — checked before every order; if breached, the kill switch auto-activates. A warning alert fires at 80% of the limit.
- **Position size limit** — orders exceeding the max % of capital are rejected at creation.
- **Max daily trade count** — enforced per active strategy.
- **Strategy fallback** — if Claude returns malformed JSON, the last valid strategy is kept instead of trading with no rules.
- **Drift cooldown** — a minimum gap between regenerations to prevent regeneration spam.

### Before going live

Default to paper trading and validate first. A reasonable go-live bar (per the project docs) is roughly four weeks of paper trading with win rate above 50%, max drawdown under limits, the daily loss limit never breached, the kill switch tested, and no unplanned crashes. Keep all circuit breakers enabled at all times, and re-validate any change to the strategy-generation prompt before trusting it.

---

## Deployment

Runs comfortably on a small VPS (Ubuntu 22.04, ~2 vCPU / 2GB RAM). Install as above, run under `systemd` (or a process manager) for auto-restart, put the dashboard behind HTTPS if it's internet-facing, and back up `apex.db` regularly.

---

## Further documentation

- `APEX_PRODUCT_DESIGN.md` — full system design
- `APEX_GAP_ANALYSIS.md` — known open questions and design gaps
- `APEX_QUICK_REFERENCE.md` — one-page operator/developer reference

---

## Disclaimer

APEX is a personal tool provided as-is, with no warranty. Trading involves substantial risk of loss. Nothing here is financial advice. You are solely responsible for any trades placed through this software, including in live mode.
