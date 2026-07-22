"""
APEX Risk Manager
Enforces hard risk limits before every order.
Manages the kill switch (activate / check / reset).
All circuit breakers are non-bypassable.
"""
import logging
from datetime import datetime, date
from typing import Optional, Tuple

from apex.database import get_db_session, SystemState, Trade

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, config):
        self.config = config

    # ─── Pre-Order Checks (non-bypassable) ───────────────────────────────────

    def pre_order_check(self, symbol: str, qty: float, price: float,
                         capital: float) -> Tuple[bool, str]:
        """
        Run ALL risk checks before placing an order.
        Returns (allowed: bool, reason: str).
        ALL checks must pass — a single failure blocks the order.
        """
        # 1. Kill switch
        if self.is_kill_switch_active():
            return False, "Kill switch is active"

        with get_db_session() as session:
            state = session.query(SystemState).filter_by(id=1).first()
            if not state:
                return False, "System state not found"

            # 2. Daily loss limit
            limit_dollars = capital * state.daily_loss_limit_pct / 100
            if state.daily_pnl <= -limit_dollars:
                logger.warning(
                    f"Daily loss limit hit: P&L=${state.daily_pnl:.2f}, "
                    f"limit=${-limit_dollars:.2f}"
                )
                return False, f"Daily loss limit exceeded (${state.daily_pnl:.2f})"

            # 3. Position size limit
            order_value = qty * price
            max_position_dollars = capital * state.max_position_size_pct / 100
            if order_value > max_position_dollars:
                return False, (
                    f"Order value ${order_value:.2f} exceeds "
                    f"max position ${max_position_dollars:.2f}"
                )

            # 4. Max daily trade count
            if state.active_strategy_id:
                from apex.database import Strategy
                strategy = session.query(Strategy).filter_by(
                    id=state.active_strategy_id
                ).first()
                if strategy and state.daily_trade_count >= strategy.max_trades_day:
                    return False, (
                        f"Max daily trade count reached "
                        f"({state.daily_trade_count}/{strategy.max_trades_day})"
                    )

        return True, "OK"

    def calculate_position_qty(self, price: float, capital: float,
                                 max_pct: Optional[float] = None) -> int:
        """
        Calculate number of whole shares to buy given capital and max position %.
        Returns 0 if price > max position value (can't afford even 1 share).
        """
        with get_db_session() as session:
            state = session.query(SystemState).filter_by(id=1).first()
            pct = max_pct or (state.max_position_size_pct if state else 2.0)

        max_value = capital * pct / 100
        qty = int(max_value / price)
        return max(qty, 0)

    # ─── Daily Loss Limit Enforcement ────────────────────────────────────────

    def update_daily_pnl(self, pnl_delta: float, alert_manager=None) -> None:
        """Update daily P&L and auto-trigger kill switch if limit is hit."""
        with get_db_session() as session:
            state = session.query(SystemState).filter_by(id=1).first()
            if not state:
                return
            state.daily_pnl += pnl_delta
            state.daily_trade_count += 1
            state.last_updated = datetime.utcnow()

            # Check if 80% of limit hit → warning alert
            if alert_manager and not state.kill_switch_active:
                capital_estimate = 100_000  # TODO: fetch from Alpaca account
                limit_dollars = capital_estimate * state.daily_loss_limit_pct / 100
                if state.daily_pnl <= -(limit_dollars * 0.80):
                    alert_manager.send_loss_limit_warning(
                        state.daily_pnl, state.daily_loss_limit_pct, capital_estimate
                    )

        # Auto-trigger kill switch if daily loss limit breached
        with get_db_session() as session:
            state = session.query(SystemState).filter_by(id=1).first()
            if state and not state.kill_switch_active:
                capital_estimate = 100_000
                limit_dollars = capital_estimate * state.daily_loss_limit_pct / 100
                if state.daily_pnl <= -limit_dollars:
                    logger.critical(
                        f"Daily loss limit auto-triggered kill switch. "
                        f"P&L: ${state.daily_pnl:.2f}"
                    )
                    self.activate_kill_switch(
                        reason=f"Auto: daily loss limit hit (${state.daily_pnl:.2f})",
                        alert_manager=alert_manager,
                    )

    def reset_daily_counters(self) -> None:
        """Reset daily P&L and trade count at start of each trading day."""
        with get_db_session() as session:
            state = session.query(SystemState).filter_by(id=1).first()
            if state:
                state.daily_pnl = 0.0
                state.daily_trade_count = 0
                state.last_updated = datetime.utcnow()
        logger.info("Daily counters reset.")

    # ─── Kill Switch ─────────────────────────────────────────────────────────

    def is_kill_switch_active(self) -> bool:
        """Quick check — all services call this before any action."""
        try:
            with get_db_session() as session:
                state = session.query(SystemState).filter_by(id=1).first()
                return bool(state and state.kill_switch_active)
        except Exception as exc:
            logger.error(f"Kill switch check failed: {exc}")
            return True  # Fail-safe: if DB is broken, block all orders

    def activate_kill_switch(self, reason: str = "Manual",
                               alert_manager=None) -> None:
        """
        Activate kill switch. Sets DB flag so ALL services stop immediately.
        """
        logger.critical(f"KILL SWITCH ACTIVATING: {reason}")
        with get_db_session() as session:
            state = session.query(SystemState).filter_by(id=1).first()
            if state:
                state.kill_switch_active = True
                state.kill_switch_reason = reason
                state.kill_switch_at = datetime.utcnow()
                state.system_status = "killed"
                state.last_updated = datetime.utcnow()

    def reset_kill_switch(self) -> None:
        """Reset kill switch — user confirms via dashboard before calling this."""
        logger.info("Kill switch reset. System resuming normal operation.")
        with get_db_session() as session:
            state = session.query(SystemState).filter_by(id=1).first()
            if state:
                state.kill_switch_active = False
                state.kill_switch_reason = None
                state.kill_switch_at = None
                state.system_status = "idle"
                state.last_updated = datetime.utcnow()

    # ─── System Mode ─────────────────────────────────────────────────────────

    def set_trading_mode(self, mode: str) -> None:
        """Set trading mode: 'paper' or 'live'."""
        if mode not in ("paper", "live"):
            raise ValueError(f"Invalid trading mode: {mode}")
        with get_db_session() as session:
            state = session.query(SystemState).filter_by(id=1).first()
            if state:
                state.trading_mode = mode
                state.last_updated = datetime.utcnow()
        logger.info(f"Trading mode set to: {mode.upper()}")

    def update_system_status(self, status: str) -> None:
        valid = {"idle", "pre_market", "active", "paused", "killed"}
        if status not in valid:
            return
        with get_db_session() as session:
            state = session.query(SystemState).filter_by(id=1).first()
            if state:
                state.system_status = status
                state.last_updated = datetime.utcnow()

    def update_risk_settings(self, daily_loss_limit_pct: Optional[float] = None,
                               max_position_size_pct: Optional[float] = None) -> None:
        """Update risk thresholds from the dashboard settings panel."""
        with get_db_session() as session:
            state = session.query(SystemState).filter_by(id=1).first()
            if not state:
                return
            if daily_loss_limit_pct is not None:
                if not (0.1 <= daily_loss_limit_pct <= 10.0):
                    raise ValueError("daily_loss_limit_pct must be 0.1–10.0")
                state.daily_loss_limit_pct = daily_loss_limit_pct
            if max_position_size_pct is not None:
                if not (0.5 <= max_position_size_pct <= 20.0):
                    raise ValueError("max_position_size_pct must be 0.5–20.0")
                state.max_position_size_pct = max_position_size_pct
            state.last_updated = datetime.utcnow()
