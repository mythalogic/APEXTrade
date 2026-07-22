"""
APEX Drift Monitor
- Runs every 30 minutes during market hours
- Checks win rate, intraday drawdown, daily P&L, and VIX spike
- Triggers strategy regeneration when thresholds are breached
- Logs every drift event to drift_events table
- Enforces drift cooldown (prevents spam regeneration)
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

from apex.database import (
    get_db_session, Strategy, Trade, DriftEvent, SystemState, OHLCVBar
)
from apex.alert_manager import AlertManager

logger = logging.getLogger(__name__)


class DriftMonitor:
    def __init__(self, config, strategy_engine, execution_engine, alert_manager: AlertManager):
        self.config = config
        self.strategy_engine = strategy_engine
        self.execution_engine = execution_engine
        self.alert_manager = alert_manager
        self._last_regen_at: Optional[datetime] = None

    # ─── Main Drift Check ─────────────────────────────────────────────────────

    def check_drift(self) -> None:
        """
        Evaluate all drift conditions against the active strategy.
        Triggers regeneration if any threshold is breached.
        Called every 30 minutes during market hours.
        """
        strategy = self._get_active_strategy()
        if not strategy:
            logger.debug("Drift check: no active strategy.")
            return

        # Enforce cooldown between regenerations
        if self._in_cooldown():
            logger.debug("Drift check: in cooldown period.")
            return

        # ─── Check 1: Win Rate Drop ────────────────────────────────────────
        fired, drift_type, baseline, actual = self._check_winrate(strategy)
        if fired:
            self._handle_drift(strategy, drift_type, baseline, actual, "regenerate")
            return

        # ─── Check 2: Intraday Drawdown ────────────────────────────────────
        fired, drift_type, baseline, actual = self._check_drawdown(strategy)
        if fired:
            self._handle_drift(strategy, drift_type, baseline, actual, "regenerate")
            return

        # ─── Check 3: VIX Spike ────────────────────────────────────────────
        fired, drift_type, baseline, actual = self._check_vix_spike()
        if fired:
            self._handle_drift(strategy, drift_type, baseline, actual, "regenerate")
            return

        # ─── Check 4: Dead zone (no signals for 3h) ────────────────────────
        self._check_dead_zone(strategy)

    # ─── Drift Checks ────────────────────────────────────────────────────────

    def _check_winrate(self, strategy: Strategy) -> Tuple[bool, str, float, float]:
        """Win rate drop > drift_winrate_threshold% below baseline."""
        with get_db_session() as session:
            recent_trades = (
                session.query(Trade)
                .filter(Trade.strategy_id == strategy.id, Trade.status == "closed")
                .order_by(Trade.exit_time.desc())
                .limit(10)
                .all()
            )

        if len(recent_trades) < self.config.drift_min_trades:
            return False, "", 0.0, 0.0

        wins = sum(1 for t in recent_trades if t.net_pnl and t.net_pnl > 0)
        current_winrate = wins / len(recent_trades) * 100

        # Baseline: all closed trades for this strategy (not just last 10)
        with get_db_session() as session:
            all_trades = (
                session.query(Trade)
                .filter(Trade.strategy_id == strategy.id, Trade.status == "closed")
                .all()
            )

        if len(all_trades) < self.config.drift_min_trades:
            return False, "", 0.0, 0.0

        all_wins = sum(1 for t in all_trades if t.net_pnl and t.net_pnl > 0)
        baseline_winrate = all_wins / len(all_trades) * 100
        baseline_winrate = max(baseline_winrate, 50.0)  # Floor at 50% (minimum acceptable)

        drop = baseline_winrate - current_winrate
        if drop > self.config.drift_winrate_threshold:
            logger.warning(
                f"Win rate drift: {baseline_winrate:.1f}% → {current_winrate:.1f}% "
                f"(drop: {drop:.1f}%, threshold: {self.config.drift_winrate_threshold}%)"
            )
            return True, "win_rate", baseline_winrate, current_winrate

        return False, "", 0.0, 0.0

    def _check_drawdown(self, strategy: Strategy) -> Tuple[bool, str, float, float]:
        """Intraday drawdown > drift_drawdown_threshold% from session peak."""
        with get_db_session() as session:
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            trades = (
                session.query(Trade)
                .filter(
                    Trade.strategy_id == strategy.id,
                    Trade.status == "closed",
                    Trade.exit_time >= today_start,
                )
                .order_by(Trade.exit_time.asc())
                .all()
            )

        if not trades:
            return False, "", 0.0, 0.0

        # Calculate peak-to-trough drawdown
        running_pnl = 0.0
        peak_pnl = 0.0
        max_drawdown = 0.0
        for t in trades:
            running_pnl += t.net_pnl or 0
            if running_pnl > peak_pnl:
                peak_pnl = running_pnl
            dd = peak_pnl - running_pnl
            if dd > max_drawdown:
                max_drawdown = dd

        # Express as % of a $100K reference capital
        capital_ref = 100_000.0
        max_drawdown_pct = max_drawdown / capital_ref * 100

        if max_drawdown_pct > self.config.drift_drawdown_threshold:
            logger.warning(
                f"Drawdown drift: {max_drawdown_pct:.2f}% "
                f"(threshold: {self.config.drift_drawdown_threshold}%)"
            )
            return True, "drawdown", self.config.drift_drawdown_threshold, max_drawdown_pct

        return False, "", 0.0, 0.0

    def _check_vix_spike(self) -> Tuple[bool, str, float, float]:
        """VIX spike > drift_vix_spike_threshold% during the session."""
        try:
            # Get VIX from today's bars if we have SPY bars as proxy
            # Or fetch VIX directly via yfinance
            import yfinance as yf
            vix = yf.Ticker("^VIX")
            hist = vix.history(period="2d", interval="1d")
            if len(hist) >= 2:
                prev_vix = float(hist["Close"].iloc[-2])
                curr_vix = float(hist["Close"].iloc[-1])
                spike_pct = (curr_vix - prev_vix) / prev_vix * 100 if prev_vix > 0 else 0
                if spike_pct > self.config.drift_vix_spike_threshold:
                    logger.warning(
                        f"VIX spike: {prev_vix:.2f} → {curr_vix:.2f} "
                        f"(+{spike_pct:.1f}%, threshold: {self.config.drift_vix_spike_threshold}%)"
                    )
                    return True, "vix_spike", prev_vix, curr_vix
        except Exception as exc:
            logger.debug(f"VIX spike check skipped: {exc}")
        return False, "", 0.0, 0.0

    def _check_dead_zone(self, strategy: Strategy) -> None:
        """Log warning if no trades or signals in last 3 hours (do not regenerate)."""
        with get_db_session() as session:
            three_hours_ago = datetime.utcnow() - timedelta(hours=3)
            recent_trade = (
                session.query(Trade)
                .filter(
                    Trade.strategy_id == strategy.id,
                    Trade.entry_time >= three_hours_ago,
                )
                .first()
            )
        if not recent_trade:
            logger.info(
                "Dead zone detected: no trades in last 3 hours for active strategy. "
                "Logging only (no regeneration)."
            )

    # ─── Drift Handler ────────────────────────────────────────────────────────

    def _handle_drift(self, old_strategy: Strategy, drift_type: str,
                       metric_baseline: float, metric_actual: float,
                       action: str) -> None:
        """Log drift event, pause trading, regenerate strategy, resume."""
        logger.warning(
            f"DRIFT DETECTED [{drift_type}]: baseline={metric_baseline:.2f}, "
            f"actual={metric_actual:.2f}, action={action}"
        )

        # Log drift event
        with get_db_session() as session:
            event = DriftEvent(
                detected_at=datetime.utcnow(),
                strategy_id=old_strategy.id,
                drift_type=drift_type,
                metric_baseline=metric_baseline,
                metric_actual=metric_actual,
                threshold=self._threshold_for(drift_type),
                action_taken=action,
            )
            session.add(event)
            session.flush()
            drift_event_id = event.id

        # Pause trading
        self._pause_trading()

        # Build fresh market context and regenerate
        try:
            from apex.data_pipeline import DataPipeline
            dp = DataPipeline(self.config)
            market_context = dp.build_market_context()

            trigger_map = {
                "win_rate": "drift_winrate",
                "drawdown": "drift_drawdown",
                "daily_loss": "drift_daily_loss",
                "vix_spike": "drift_vix",
            }
            trigger_reason = trigger_map.get(drift_type, "drift_winrate")

            new_strategy = self.strategy_engine.generate_strategy(
                market_context, trigger_reason=trigger_reason
            )

            # Update drift event with new strategy id
            if new_strategy:
                with get_db_session() as session:
                    de = session.query(DriftEvent).filter_by(id=drift_event_id).first()
                    if de:
                        de.new_strategy_id = new_strategy.id

                # Send strategy update alert
                self.alert_manager.send_strategy_update(
                    old_strategy, new_strategy, drift_type,
                    metric_baseline, metric_actual
                )
                self._last_regen_at = datetime.utcnow()

        except Exception as exc:
            logger.error(f"Strategy regeneration after drift failed: {exc}")
            self.alert_manager.send_error_alert(f"Drift regen failed: {exc}")
        finally:
            self._resume_trading()

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _pause_trading(self) -> None:
        with get_db_session() as session:
            state = session.query(SystemState).filter_by(id=1).first()
            if state:
                state.system_status = "paused"
                state.last_updated = datetime.utcnow()
        logger.info("Trading paused for strategy regeneration.")

    def _resume_trading(self) -> None:
        with get_db_session() as session:
            state = session.query(SystemState).filter_by(id=1).first()
            if state and state.system_status == "paused":
                state.system_status = "active"
                state.last_updated = datetime.utcnow()
        logger.info("Trading resumed with new strategy.")

    def _in_cooldown(self) -> bool:
        if self._last_regen_at is None:
            return False
        elapsed = (datetime.utcnow() - self._last_regen_at).total_seconds() / 60
        return elapsed < self.config.drift_cooldown_minutes

    def _threshold_for(self, drift_type: str) -> float:
        mapping = {
            "win_rate": self.config.drift_winrate_threshold,
            "drawdown": self.config.drift_drawdown_threshold,
            "vix_spike": self.config.drift_vix_spike_threshold,
            "daily_loss": 2.0,
        }
        return mapping.get(drift_type, 0.0)

    def _get_active_strategy(self) -> Optional[Strategy]:
        with get_db_session() as session:
            state = session.query(SystemState).filter_by(id=1).first()
            if state and state.active_strategy_id:
                s = session.query(Strategy).filter_by(id=state.active_strategy_id).first()
                if s:
                    session.expunge(s)
                    return s
        return None
