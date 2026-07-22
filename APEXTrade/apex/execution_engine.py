"""
APEX Execution Engine
- Scans for entry signals every 5 minutes against active strategy conditions
- Places and manages orders via Alpaca API
- Monitors open positions for exit signals and stop losses
- Syncs positions from broker every 60 seconds
- Logs every trade to the trades table
"""
import json
import logging
import operator
from datetime import datetime, time
from typing import Optional, List, Dict

import pytz

from apex.database import (
    get_db_session, OHLCVBar, Strategy, Trade, Position, SystemState
)
from apex.risk_manager import RiskManager
from apex.alert_manager import AlertManager

logger = logging.getLogger(__name__)

EST = pytz.timezone("US/Eastern")

# ─── Safe operator map (no eval) ─────────────────────────────────────────────
_OPS = {
    "lt": operator.lt,
    "gt": operator.gt,
    "lte": operator.le,
    "gte": operator.ge,
    "eq": operator.eq,
}

# Valid indicator attribute names on OHLCVBar
_VALID_INDICATORS = frozenset({
    "rsi_14", "ema_9", "ema_21", "ema_50",
    "macd", "macd_signal", "atr_14", "volume_ratio",
})


class ExecutionEngine:
    def __init__(self, config, risk_manager: RiskManager, alert_manager: AlertManager):
        self.config = config
        self.risk_manager = risk_manager
        self.alert_manager = alert_manager
        self._trading_client = None
        self._init_alpaca()

    def _init_alpaca(self) -> None:
        try:
            from alpaca.trading.client import TradingClient
            self._trading_client = TradingClient(
                self.config.alpaca_api_key,
                self.config.alpaca_api_secret,
                paper=self.config.alpaca_paper,
            )
            logger.info("Execution engine: Alpaca trading client initialised.")
        except Exception as exc:
            logger.error(f"Alpaca trading client init failed: {exc}")

    # ─── Signal Scanner ───────────────────────────────────────────────────────

    def scan_and_execute(self) -> None:
        """
        Evaluate entry conditions for each ticker in the active strategy.
        If conditions are met and all risk checks pass, place a market buy order.
        """
        if self.risk_manager.is_kill_switch_active():
            return

        strategy = self._get_active_strategy()
        if not strategy:
            logger.debug("No active strategy. Scan skipped.")
            return

        now_est = datetime.now(EST)
        if self._in_avoid_window(strategy, now_est.time()):
            logger.debug(f"In avoid window at {now_est.strftime('%H:%M')} EST. No new entries.")
            return

        for symbol in strategy.get_tickers():
            try:
                self._evaluate_entry(symbol, strategy)
            except Exception as exc:
                logger.error(f"Entry evaluation error for {symbol}: {exc}")

    def _evaluate_entry(self, symbol: str, strategy: Strategy) -> None:
        """Check entry conditions for one symbol. Place order if all pass."""
        # Skip if already holding this symbol
        with get_db_session() as session:
            existing = session.query(Position).filter_by(symbol=symbol).first()
            if existing:
                return

        latest = self._get_latest_bar(symbol)
        if latest is None:
            logger.debug(f"No bar data for {symbol}. Skipping.")
            return

        prev = self._get_previous_bar(symbol, latest)
        conditions = strategy.get_entry_conditions()

        if not conditions:
            return

        if not self._evaluate_all_conditions(conditions, latest, prev_bar=prev):
            return

        # All entry conditions met — run risk checks
        capital = self._get_capital()
        qty = self.risk_manager.calculate_position_qty(
            price=latest.close,
            capital=capital,
            max_pct=strategy.max_position_pct,
        )
        if qty <= 0:
            logger.warning(f"Calculated qty=0 for {symbol} @ ${latest.close:.2f}. Skipping.")
            return

        allowed, reason = self.risk_manager.pre_order_check(
            symbol=symbol, qty=qty, price=latest.close, capital=capital
        )
        if not allowed:
            logger.info(f"Order blocked for {symbol}: {reason}")
            return

        # Place the order
        self._place_buy_order(symbol, qty, latest.close, strategy)

    # ─── Position Monitor ─────────────────────────────────────────────────────

    def monitor_positions(self) -> None:
        """
        For each open position: evaluate exit conditions and check stop loss.
        Close position via market sell if exit is triggered.
        """
        if self.risk_manager.is_kill_switch_active():
            return

        strategy = self._get_active_strategy()
        if not strategy:
            return

        with get_db_session() as session:
            positions = session.query(Position).all()
            position_list = [(p.symbol, p.avg_entry_price, p.stop_loss_price) for p in positions]

        for symbol, avg_entry, stop_price in position_list:
            try:
                self._evaluate_exit(symbol, avg_entry, stop_price, strategy)
            except Exception as exc:
                logger.error(f"Exit evaluation error for {symbol}: {exc}")

    def _evaluate_exit(self, symbol: str, avg_entry: float, stop_price: Optional[float],
                        strategy: Strategy) -> None:
        latest = self._get_latest_bar(symbol)
        if latest is None:
            return

        prev = self._get_previous_bar(symbol, latest)

        # 1. Stop loss check (always enforced from stop_loss_pct field)
        stop_threshold = avg_entry * (1 - strategy.stop_loss_pct / 100)
        if latest.close <= stop_threshold:
            self._place_sell_order(symbol, "stop_loss", strategy)
            return

        # 2. Exit conditions check
        exit_conditions = strategy.get_exit_conditions()
        if exit_conditions:
            position_context = _FakePosition(avg_entry=avg_entry)
            if self._evaluate_all_conditions(exit_conditions, latest,
                                              prev_bar=prev, position=position_context):
                self._place_sell_order(symbol, "exit_signal", strategy)

    # ─── Order Placement ─────────────────────────────────────────────────────

    def _place_buy_order(self, symbol: str, qty: int, signal_price: float,
                          strategy: Strategy) -> None:
        logger.info(f"Placing BUY order: {symbol} x{qty} (signal @ ${signal_price:.2f})")
        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            order_req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )
            order = self._trading_client.submit_order(order_data=order_req)
            fill_price = float(getattr(order, "filled_avg_price", None) or signal_price)
            slippage = round(fill_price - signal_price, 4)

            # Record in trades table
            with get_db_session() as session:
                trade = Trade(
                    strategy_id=strategy.id,
                    broker_order_id=str(order.id),
                    symbol=symbol,
                    side="buy",
                    qty=float(qty),
                    entry_price=fill_price,
                    entry_time=datetime.utcnow(),
                    slippage=slippage,
                    status="open",
                )
                session.add(trade)

                # Set stop_loss_price on position after sync
                stop_price = round(fill_price * (1 - strategy.stop_loss_pct / 100), 4)
                existing_pos = session.query(Position).filter_by(symbol=symbol).first()
                if existing_pos:
                    existing_pos.stop_loss_price = stop_price

            self.alert_manager.send_trade_entry(symbol, "BUY", qty, fill_price, strategy.version)
            logger.info(f"BUY order placed: {symbol} x{qty} @ ${fill_price:.2f}")

        except Exception as exc:
            logger.error(f"Failed to place BUY order for {symbol}: {exc}")
            self.alert_manager.send_error_alert(f"Order placement failed: {symbol} BUY — {exc}")

    def _place_sell_order(self, symbol: str, exit_reason: str, strategy: Strategy) -> None:
        logger.info(f"Placing SELL order: {symbol} ({exit_reason})")
        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            # Get current position qty from Alpaca
            try:
                broker_position = self._trading_client.get_open_position(symbol)
                qty = float(broker_position.qty)
            except Exception:
                with get_db_session() as session:
                    pos = session.query(Position).filter_by(symbol=symbol).first()
                    qty = pos.qty if pos else 1

            order_req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            order = self._trading_client.submit_order(order_data=order_req)
            fill_price = float(getattr(order, "filled_avg_price", None) or 0)

            # Close trade record in DB
            self._close_trade_record(symbol, fill_price, exit_reason, qty)

        except Exception as exc:
            logger.error(f"Failed to place SELL order for {symbol}: {exc}")
            self.alert_manager.send_error_alert(f"Sell order failed: {symbol} — {exc}")

    def _close_trade_record(self, symbol: str, exit_price: float,
                              exit_reason: str, qty: float) -> None:
        with get_db_session() as session:
            trade = (
                session.query(Trade)
                .filter_by(symbol=symbol, status="open")
                .order_by(Trade.entry_time.desc())
                .first()
            )
            if not trade:
                return

            gross_pnl = round((exit_price - trade.entry_price) * trade.qty, 4)
            net_pnl = round(gross_pnl - (trade.commission or 0), 4)

            trade.exit_price = exit_price
            trade.exit_time = datetime.utcnow()
            trade.exit_reason = exit_reason
            trade.gross_pnl = gross_pnl
            trade.net_pnl = net_pnl
            trade.status = "closed"

            # Get strategy version for alert
            strategy_id = trade.strategy_id
            strategy = session.query(Strategy).filter_by(id=strategy_id).first()
            version = strategy.version if strategy else 0

        self.risk_manager.update_daily_pnl(net_pnl, self.alert_manager)
        self.alert_manager.send_trade_exit(symbol, qty, exit_price, net_pnl, exit_reason, version)
        logger.info(f"Trade closed: {symbol} @ ${exit_price:.2f} | P&L: ${net_pnl:.2f} | {exit_reason}")

    # ─── Position Sync ────────────────────────────────────────────────────────

    def sync_positions_from_broker(self) -> None:
        """Sync open positions from Alpaca into the positions table."""
        if self._trading_client is None:
            return
        try:
            broker_positions = self._trading_client.get_all_positions()
            with get_db_session() as session:
                # Clear and rebuild from broker truth
                session.query(Position).delete()
                for bp in broker_positions:
                    session.add(Position(
                        symbol=bp.symbol,
                        qty=float(bp.qty),
                        side="long" if float(bp.qty) > 0 else "short",
                        avg_entry_price=float(bp.avg_entry_price),
                        current_price=float(bp.current_price),
                        market_value=float(bp.market_value),
                        unrealized_pnl=float(bp.unrealized_pl),
                        unrealized_pnl_pct=float(bp.unrealized_plpc) * 100,
                        last_synced=datetime.utcnow(),
                    ))
        except Exception as exc:
            logger.error(f"Position sync failed: {exc}")

    # ─── Bulk Close / Cancel ──────────────────────────────────────────────────

    def close_all_positions(self, reason: str = "market_close") -> int:
        """Close all open positions. Returns count of positions closed."""
        if self._trading_client is None:
            return 0
        closed = 0
        try:
            positions = self._trading_client.get_all_positions()
            for pos in positions:
                try:
                    self._place_sell_order(pos.symbol, reason,
                                           self._get_active_strategy() or _NullStrategy())
                    closed += 1
                except Exception as exc:
                    logger.error(f"Failed to close {pos.symbol}: {exc}")
        except Exception as exc:
            logger.error(f"close_all_positions failed: {exc}")
        return closed

    def cancel_all_open_orders(self) -> int:
        """Cancel all open orders on Alpaca. Returns count cancelled."""
        if self._trading_client is None:
            return 0
        try:
            cancel_statuses = self._trading_client.cancel_orders()
            count = len(cancel_statuses) if cancel_statuses else 0
            logger.info(f"Cancelled {count} open orders.")
            return count
        except Exception as exc:
            logger.error(f"cancel_all_open_orders failed: {exc}")
            return 0

    # ─── Condition Evaluator (no eval(), uses operator module) ────────────────

    def _evaluate_all_conditions(self, conditions: List[dict], bar: OHLCVBar,
                                   prev_bar: Optional[OHLCVBar] = None,
                                   position=None) -> bool:
        """ALL conditions must be True (AND logic)."""
        return all(
            self._evaluate_single(cond, bar, prev_bar, position)
            for cond in conditions
        )

    def _evaluate_single(self, cond: dict, bar: OHLCVBar,
                           prev_bar: Optional[OHLCVBar],
                           position) -> bool:
        """Evaluate one structured condition. Safe — no eval()."""
        ctype = cond.get("type")

        if ctype == "indicator":
            ind = cond.get("indicator")
            op_str = cond.get("op")
            val = cond.get("val")
            if ind not in _VALID_INDICATORS or op_str not in _OPS:
                return False
            ind_val = getattr(bar, ind, None)
            if ind_val is None:
                return False
            try:
                return _OPS[op_str](float(ind_val), float(val))
            except (TypeError, ValueError):
                return False

        if ctype == "volume_ratio":
            op_str = cond.get("op")
            val = cond.get("val")
            if bar.volume_ratio is None or op_str not in _OPS:
                return False
            try:
                return _OPS[op_str](float(bar.volume_ratio), float(val))
            except (TypeError, ValueError):
                return False

        if ctype == "ema_cross_above":
            return self._check_ema_cross(cond, bar, prev_bar, direction="above")

        if ctype == "ema_cross_below":
            return self._check_ema_cross(cond, bar, prev_bar, direction="below")

        if ctype == "profit_pct":
            if position is None:
                return False
            op_str = cond.get("op")
            val = cond.get("val")
            if op_str not in _OPS:
                return False
            try:
                pnl_pct = (bar.close - position.avg_entry_price) / position.avg_entry_price * 100
                return _OPS[op_str](pnl_pct, float(val))
            except (TypeError, ValueError, ZeroDivisionError):
                return False

        if ctype == "loss_pct":
            if position is None:
                return False
            op_str = cond.get("op")
            val = cond.get("val")
            if op_str not in _OPS:
                return False
            try:
                loss_pct = (position.avg_entry_price - bar.close) / position.avg_entry_price * 100
                return _OPS[op_str](loss_pct, float(val))
            except (TypeError, ValueError, ZeroDivisionError):
                return False

        logger.warning(f"Unknown condition type: {ctype}")
        return False

    def _check_ema_cross(self, cond: dict, bar: OHLCVBar,
                          prev_bar: Optional[OHLCVBar], direction: str) -> bool:
        if prev_bar is None:
            return False
        fast = cond.get("fast")
        slow = cond.get("slow")
        if fast not in _VALID_INDICATORS or slow not in _VALID_INDICATORS:
            return False
        curr_fast = getattr(bar, fast, None)
        curr_slow = getattr(bar, slow, None)
        prev_fast = getattr(prev_bar, fast, None)
        prev_slow = getattr(prev_bar, slow, None)
        if any(v is None for v in [curr_fast, curr_slow, prev_fast, prev_slow]):
            return False
        if direction == "above":
            return prev_fast <= prev_slow and curr_fast > curr_slow
        return prev_fast >= prev_slow and curr_fast < curr_slow

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _get_active_strategy(self) -> Optional[Strategy]:
        with get_db_session() as session:
            state = session.query(SystemState).filter_by(id=1).first()
            if state and state.active_strategy_id:
                s = session.query(Strategy).filter_by(id=state.active_strategy_id).first()
                if s:
                    session.expunge(s)
                    return s
        return None

    def _get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        with get_db_session() as session:
            bar = (
                session.query(OHLCVBar)
                .filter_by(symbol=symbol)
                .order_by(OHLCVBar.timestamp.desc())
                .first()
            )
            if bar:
                session.expunge(bar)
                return bar
        return None

    def _get_previous_bar(self, symbol: str, latest: OHLCVBar) -> Optional[OHLCVBar]:
        with get_db_session() as session:
            bar = (
                session.query(OHLCVBar)
                .filter(
                    OHLCVBar.symbol == symbol,
                    OHLCVBar.timestamp < latest.timestamp,
                )
                .order_by(OHLCVBar.timestamp.desc())
                .first()
            )
            if bar:
                session.expunge(bar)
                return bar
        return None

    def _get_capital(self) -> float:
        try:
            account = self._trading_client.get_account()
            return float(account.portfolio_value)
        except Exception:
            return 100_000.0

    def _in_avoid_window(self, strategy: Strategy, current_time: time) -> bool:
        """Return True if current time falls within an avoid_times window."""
        for window in strategy.get_avoid_times():
            try:
                start_str, end_str = window.split("-")
                start = datetime.strptime(start_str.strip(), "%H:%M").time()
                end = datetime.strptime(end_str.strip(), "%H:%M").time()
                if start <= current_time <= end:
                    return True
            except (ValueError, AttributeError):
                continue
        return False


# ─── Helper classes ───────────────────────────────────────────────────────────

class _FakePosition:
    """Lightweight stand-in passed to condition evaluator for exit checks."""
    def __init__(self, avg_entry: float):
        self.avg_entry_price = avg_entry


class _NullStrategy:
    """Minimal strategy object used when no active strategy exists during shutdown."""
    id = 0
    version = 0
    stop_loss_pct = 1.5

    def get_tickers(self):
        return []

    def get_exit_conditions(self):
        return []
