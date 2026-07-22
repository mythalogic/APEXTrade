#!/usr/bin/env python3
"""
APEX - AI Day Trading Platform
Main Entry Point

Usage:
    python APEXTrade.py              # Start the full platform
    python APEXTrade.py --kill       # Activate kill switch immediately
    python APEXTrade.py --resume     # Reset kill switch
    python APEXTrade.py --status     # Print current system status
    python APEXTrade.py --check      # Run API connectivity health check
    python APEXTrade.py --backfill   # Run historical data backfill via yfinance

Architecture:
    5 services orchestrated by APScheduler running in-process:
    1. Data Pipeline   — OHLCV bars + indicators  (every 5 min, market hours)
    2. Strategy Engine — Claude AI strategy gen    (6am EST + drift trigger)
    3. Execution Engine— Order placement           (continuous, market hours)
    4. Drift Monitor   — Performance tracking      (every 30 min, market hours)
    5. Web Dashboard   — FastAPI REST + React UI   (always on, port 8000)
"""
import argparse
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, time as dt_time

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_ERROR

# ─── Logging Setup (before any other imports) ─────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join("logs", "apex.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("APEX")

# ─── APEX Imports ─────────────────────────────────────────────────────────────
from apex.config import config
from apex.database import init_db, get_db_session, SystemState, Strategy
from apex.alert_manager import AlertManager
from apex.risk_manager import RiskManager
from apex.data_pipeline import DataPipeline
from apex.strategy_engine import StrategyEngine
from apex.execution_engine import ExecutionEngine
from apex.drift_monitor import DriftMonitor

EST = pytz.timezone("US/Eastern")

# ─── Global service instances ────────────────────────────────────────────────
_alert_manager: AlertManager = None
_risk_manager: RiskManager = None
_data_pipeline: DataPipeline = None
_strategy_engine: StrategyEngine = None
_execution_engine: ExecutionEngine = None
_drift_monitor: DriftMonitor = None
_scheduler: BackgroundScheduler = None


# ─── Service Initialisation ──────────────────────────────────────────────────

def initialise_services() -> None:
    global _alert_manager, _risk_manager, _data_pipeline
    global _strategy_engine, _execution_engine, _drift_monitor

    logger.info("Initialising APEX services...")
    init_db(config.db_path)

    _alert_manager = AlertManager(config)
    _risk_manager = RiskManager(config)
    _data_pipeline = DataPipeline(config)
    _strategy_engine = StrategyEngine(config, _alert_manager)
    _execution_engine = ExecutionEngine(config, _risk_manager, _alert_manager)
    _drift_monitor = DriftMonitor(
        config, _strategy_engine, _execution_engine, _alert_manager
    )
    logger.info("All APEX services initialised.")


# ─── Scheduled Jobs ──────────────────────────────────────────────────────────

def job_pre_market() -> None:
    """6:00am EST — fetch overnight data, call Claude, generate daily strategy."""
    logger.info("=== PRE-MARKET JOB ===")
    try:
        if not _data_pipeline.is_market_day():
            logger.info("Not a trading day — pre-market job skipped.")
            return
        _risk_manager.reset_daily_counters()
        _risk_manager.update_system_status("pre_market")
        market_context = _data_pipeline.build_market_context()
        strategy = _strategy_engine.generate_strategy(
            market_context, trigger_reason="pre_market"
        )
        if strategy:
            logger.info(f"Pre-market: strategy v{strategy.version} active ({strategy.strategy_type})")
            _risk_manager.update_system_status("active")
        else:
            logger.error("Pre-market: strategy generation FAILED.")
            _alert_manager.send_error_alert("Pre-market strategy generation failed.")
    except Exception as exc:
        logger.exception(f"Pre-market job error: {exc}")
        _alert_manager.send_error_alert(f"Pre-market job error: {exc}")


def job_data_pipeline() -> None:
    """Every 5 min (market hours) — fetch OHLCV bars and recompute indicators."""
    if not _is_market_hours() or _risk_manager.is_kill_switch_active():
        return
    try:
        _data_pipeline.fetch_and_store_bars()
    except Exception as exc:
        logger.error(f"Data pipeline error: {exc}")


def job_signal_scanner() -> None:
    """Every 5 min (market hours) — evaluate entry conditions and place orders."""
    if not _is_market_hours() or _risk_manager.is_kill_switch_active():
        return
    try:
        _execution_engine.scan_and_execute()
    except Exception as exc:
        logger.error(f"Signal scanner error: {exc}")
        _alert_manager.send_error_alert(f"Signal scanner error: {exc}")


def job_position_monitor() -> None:
    """Every 60 sec — monitor open positions for exit signals and stop losses."""
    if not _is_market_hours() or _risk_manager.is_kill_switch_active():
        return
    try:
        _execution_engine.monitor_positions()
    except Exception as exc:
        logger.error(f"Position monitor error: {exc}")


def job_sync_positions() -> None:
    """Every 60 sec — sync open positions from Alpaca broker."""
    if _risk_manager.is_kill_switch_active():
        return
    try:
        _execution_engine.sync_positions_from_broker()
    except Exception as exc:
        logger.error(f"Position sync error: {exc}")


def job_drift_check() -> None:
    """Every 30 min (market hours) — check strategy drift and regenerate if needed."""
    if not _is_market_hours() or _risk_manager.is_kill_switch_active():
        return
    try:
        _drift_monitor.check_drift()
    except Exception as exc:
        logger.error(f"Drift check error: {exc}")
        _alert_manager.send_error_alert(f"Drift monitor error: {exc}")


def job_post_market() -> None:
    """4:02pm EST — close all positions, send EOD summary."""
    logger.info("=== POST-MARKET JOB ===")
    try:
        if not _data_pipeline.is_market_day():
            return
        closed = _execution_engine.close_all_positions(reason="market_close")
        logger.info(f"Post-market: {closed} position(s) closed.")
        _strategy_engine.send_eod_summary()
        _risk_manager.update_system_status("idle")
    except Exception as exc:
        logger.exception(f"Post-market job error: {exc}")
        _alert_manager.send_error_alert(f"Post-market job error: {exc}")


def _on_scheduler_error(event) -> None:
    if event.exception:
        logger.error(f"Scheduler job failed: {event.exception}")


# ─── Market Hours Helper ─────────────────────────────────────────────────────

def _is_market_hours() -> bool:
    """Return True if current EST time is within NYSE trading hours."""
    now = datetime.now(EST)
    if now.weekday() >= 5:
        return False
    return dt_time(9, 30) <= now.time() <= dt_time(16, 5)


# ─── Scheduler Setup ─────────────────────────────────────────────────────────

def setup_scheduler() -> BackgroundScheduler:
    global _scheduler
    _scheduler = BackgroundScheduler(timezone=EST)
    _scheduler.add_listener(_on_scheduler_error, EVENT_JOB_ERROR)

    # Pre-market: 6:00am EST Mon–Fri
    _scheduler.add_job(
        job_pre_market,
        CronTrigger(hour=6, minute=0, day_of_week="mon-fri", timezone=EST),
        id="pre_market", name="Pre-Market Strategy", misfire_grace_time=300,
    )
    # Data pipeline: every 5 min
    _scheduler.add_job(
        job_data_pipeline,
        IntervalTrigger(minutes=5),
        id="data_pipeline", name="Data Pipeline (OHLCV)",
    )
    # Signal scanner: every 5 min (1 min offset)
    _scheduler.add_job(
        job_signal_scanner,
        IntervalTrigger(minutes=5, seconds=60),
        id="signal_scanner", name="Signal Scanner",
    )
    # Position monitor: every 60 sec
    _scheduler.add_job(
        job_position_monitor,
        IntervalTrigger(seconds=60),
        id="position_monitor", name="Position Monitor",
    )
    # Position sync: every 60 sec (offset 30s)
    _scheduler.add_job(
        job_sync_positions,
        IntervalTrigger(seconds=60, start_date=datetime.now(EST).replace(second=30)),
        id="sync_positions", name="Position Sync",
    )
    # Drift check: every 30 min
    _scheduler.add_job(
        job_drift_check,
        IntervalTrigger(minutes=30),
        id="drift_check", name="Drift Monitor",
    )
    # Post-market: 4:02pm EST Mon–Fri
    _scheduler.add_job(
        job_post_market,
        CronTrigger(hour=16, minute=2, day_of_week="mon-fri", timezone=EST),
        id="post_market", name="Post-Market Report", misfire_grace_time=300,
    )

    return _scheduler


# ─── Dashboard Startup ────────────────────────────────────────────────────────

def start_dashboard() -> None:
    """Launch FastAPI dashboard in a background daemon thread."""
    try:
        import uvicorn
        from dashboard.api import app, set_config
        set_config(config)

        def _run():
            uvicorn.run(
                app,
                host="0.0.0.0",
                port=config.dashboard_port,
                log_level="warning",
                access_log=False,
            )

        t = threading.Thread(target=_run, daemon=True, name="dashboard")
        t.start()
        logger.info(f"Dashboard started → http://localhost:{config.dashboard_port}")
    except Exception as exc:
        logger.error(f"Dashboard failed to start: {exc}")


# ─── CLI Commands ────────────────────────────────────────────────────────────

def cmd_status() -> None:
    init_db(config.db_path)
    with get_db_session() as session:
        state = session.query(SystemState).filter_by(id=1).first()
        if not state:
            print("System not initialised. Run APEXTrade.py first.")
            return
        strat = None
        if state.active_strategy_id:
            strat = session.query(Strategy).filter_by(id=state.active_strategy_id).first()

    w = 52
    print(f"\n{'═' * w}")
    print(f"  APEX System Status")
    print(f"{'═' * w}")
    print(f"  Status          : {state.system_status.upper()}")
    print(f"  Trading Mode    : {state.trading_mode.upper()}")
    print(f"  Kill Switch     : {'ACTIVE ⛔' if state.kill_switch_active else 'Off ✓'}")
    if state.kill_switch_reason:
        print(f"  Kill Reason     : {state.kill_switch_reason}")
    print(f"  Daily P&L       : ${state.daily_pnl:+.2f}")
    print(f"  Daily Trades    : {state.daily_trade_count}")
    print(f"  Loss Limit      : {state.daily_loss_limit_pct}%")
    print(f"  Max Position    : {state.max_position_size_pct}%")
    if strat:
        print(f"  Active Strategy : v{strat.version} {strat.strategy_type} [{strat.confidence}]")
    print(f"  Last Updated    : {state.last_updated}")
    print(f"{'═' * w}\n")


def cmd_check() -> bool:
    """Verify all external API connections."""
    print("\n  Running APEX Health Check...\n")
    init_db(config.db_path)
    all_ok = True

    # Alpaca
    try:
        from alpaca.trading.client import TradingClient
        tc = TradingClient(config.alpaca_api_key, config.alpaca_api_secret, paper=config.alpaca_paper)
        acct = tc.get_account()
        print(f"  ✓ Alpaca API     Connected  (Account: {str(acct.id)[:8]}...)")
    except Exception as exc:
        print(f"  ✗ Alpaca API     FAILED — {exc}")
        all_ok = False

    # Claude
    try:
        import anthropic
        ac = anthropic.Anthropic(api_key=config.anthropic_api_key)
        ac.messages.create(
            model=config.claude_model,
            max_tokens=5,
            messages=[{"role": "user", "content": "ping"}],
        )
        print(f"  ✓ Claude API     Connected  (Model: {config.claude_model})")
    except Exception as exc:
        print(f"  ✗ Claude API     FAILED — {exc}")
        all_ok = False

    # Telegram
    try:
        import requests
        r = requests.get(
            f"https://api.telegram.org/bot{config.telegram_bot_token}/getMe",
            timeout=10,
        )
        r.raise_for_status()
        result = r.json()
        if result.get("ok"):
            print(f"  ✓ Telegram Bot   Connected  (@{result['result']['username']})")
        else:
            raise ValueError(result)
    except Exception as exc:
        print(f"  ✗ Telegram Bot   FAILED — {exc}")
        all_ok = False

    # Database
    try:
        with get_db_session() as session:
            session.query(SystemState).filter_by(id=1).first()
        print(f"  ✓ Database       Connected  ({config.db_path})")
    except Exception as exc:
        print(f"  ✗ Database       FAILED — {exc}")
        all_ok = False

    print(f"\n  {'✓ ALL SYSTEMS GO — ready to trade.' if all_ok else '✗ ISSUES DETECTED — fix before starting.'}\n")
    return all_ok


def cmd_kill() -> None:
    init_db(config.db_path)
    rm = RiskManager(config)
    am = AlertManager(config)
    rm.activate_kill_switch(reason="CLI --kill", alert_manager=am)
    print("KILL SWITCH ACTIVATED. All trading halted.")


def cmd_resume() -> None:
    init_db(config.db_path)
    rm = RiskManager(config)
    rm.reset_kill_switch()
    print("Kill switch reset. System status: idle.")


def cmd_backfill() -> None:
    init_db(config.db_path)
    dp = DataPipeline(config)
    print(f"Starting historical backfill for: {config.default_tickers}")
    dp.historical_backfill(tickers=config.default_tickers, years=2)
    print("Backfill complete.")


# ─── Graceful Shutdown ────────────────────────────────────────────────────────

def _handle_shutdown(signum, frame) -> None:
    logger.info("Shutdown signal received — cancelling open orders before exit...")
    if _execution_engine:
        try:
            cancelled = _execution_engine.cancel_all_open_orders()
            logger.info(f"Shutdown: {cancelled} open orders cancelled.")
        except Exception as exc:
            logger.error(f"Shutdown order cancellation error: {exc}")
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    logger.info("APEX shut down cleanly. Goodbye.")
    sys.exit(0)


# ─── Startup Check: Run Pre-market if no strategy today ──────────────────────

def _maybe_run_immediate_premarket() -> None:
    """If market is open and no strategy has been generated today, run now."""
    with get_db_session() as session:
        today = datetime.utcnow().date()
        latest = (
            session.query(Strategy)
            .filter(Strategy.status == "active")
            .order_by(Strategy.created_at.desc())
            .first()
        )
        if not latest or (latest.created_at and latest.created_at.date() < today):
            if _is_market_hours():
                logger.info("No strategy for today & market is open — running pre-market now.")
                threading.Thread(target=job_pre_market, daemon=True).start()


# ─── Main ────────────────────────────────────────────────────────────────────

BANNER = r"""
    ╔══════════════════════════════════════════╗
    ║          A P E X  v 1 . 0               ║
    ║     AI Day Trading Platform              ║
    ║     paper mode | claude-sonnet-4-6       ║
    ╚══════════════════════════════════════════╝
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="APEX — AI Day Trading Platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--kill",     action="store_true", help="Activate kill switch")
    parser.add_argument("--resume",   action="store_true", help="Reset kill switch")
    parser.add_argument("--status",   action="store_true", help="Print system status")
    parser.add_argument("--check",    action="store_true", help="Run API health check")
    parser.add_argument("--backfill", action="store_true", help="Run historical data backfill")
    args = parser.parse_args()

    if args.kill:
        cmd_kill()
        return
    if args.resume:
        cmd_resume()
        return
    if args.status:
        cmd_status()
        return
    if args.check:
        cmd_check()
        return
    if args.backfill:
        cmd_backfill()
        return

    # ── Normal Startup ────────────────────────────────────────────────────────
    print(BANNER)

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    # Initialise all services (DB, Alpaca, Claude, Telegram)
    initialise_services()

    # Start FastAPI dashboard in background thread
    start_dashboard()

    # Health check (warn but don't block if any service fails)
    if not cmd_check():
        logger.warning("Health check had failures — some features may not work.")

    # Update system status to idle/ready
    _risk_manager.update_system_status("idle")

    # Start APScheduler (non-blocking, runs in background threads)
    sched = setup_scheduler()
    sched.start()
    logger.info("APScheduler started. APEX is running.")
    logger.info(f"Dashboard: http://localhost:{config.dashboard_port}")
    logger.info("Press Ctrl+C to stop.")

    # Send startup notification
    _alert_manager.send_system_startup(config.trading_mode)

    # If market is already open and we have no strategy today, generate one now
    _maybe_run_immediate_premarket()

    # Keep main thread alive — all work happens in scheduler threads
    try:
        while True:
            time.sleep(30)
    except (KeyboardInterrupt, SystemExit):
        _handle_shutdown(None, None)


if __name__ == "__main__":
    main()
