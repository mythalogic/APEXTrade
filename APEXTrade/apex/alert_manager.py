"""
APEX Alert Manager
Sends formatted Telegram messages for all alert types.
Uses simple HTTP requests with retry logic (3 attempts, exponential backoff).
Logs every alert to the alerts_log table.
"""
import logging
import time
from datetime import datetime
from typing import Optional

import requests

from apex.database import get_db_session, AlertLog

logger = logging.getLogger(__name__)

# ─── Alert Types ─────────────────────────────────────────────────────────────
ALERT_MORNING_STRATEGY = "morning_strategy"
ALERT_STRATEGY_UPDATE = "strategy_update"
ALERT_TRADE = "trade"
ALERT_WARNING = "warning"
ALERT_KILL = "kill"
ALERT_EOD = "eod"
ALERT_ERROR = "error"


class AlertManager:
    def __init__(self, config):
        self.config = config
        self._base_url = f"https://api.telegram.org/bot{config.telegram_bot_token}"

    # ─── Public Send Methods ─────────────────────────────────────────────────

    def send_morning_strategy(self, strategy) -> None:
        now_str = datetime.utcnow().strftime("%a %d %b %Y")
        tickers = ", ".join(strategy.get_tickers())
        msg = (
            f"<b>APEX – Morning Strategy | {now_str}</b>\n\n"
            f"<b>Strategy v{strategy.version}</b> | {strategy.strategy_type.replace('_', ' ').title()} "
            f"| Confidence: {strategy.confidence.capitalize()}\n"
            f"Tickers: {tickers}\n\n"
            f"<b>Entry:</b> {strategy.entry_signal}\n"
            f"<b>Exit:</b> {strategy.exit_signal}\n\n"
            f"Stop loss: {strategy.stop_loss_pct}% | "
            f"Max position: {strategy.max_position_pct}% | "
            f"Max trades: {strategy.max_trades_day}\n\n"
            f"<i>Why:</i> {strategy.reasoning}"
        )
        self._send(ALERT_MORNING_STRATEGY, msg)

    def send_strategy_update(self, old_strategy, new_strategy, drift_type: str,
                              metric_baseline: float, metric_actual: float) -> None:
        now_str = datetime.utcnow().strftime("%H:%M UTC")
        drift_label = drift_type.replace("_", " ").title()
        tickers = ", ".join(new_strategy.get_tickers())
        msg = (
            f"<b>⚠️ APEX – STRATEGY UPDATED | {now_str}</b>\n\n"
            f"Drift detected: {drift_label} — "
            f"{metric_baseline:.1f}% → {metric_actual:.1f}%\n\n"
            f"<b>Previous:</b> {old_strategy.strategy_type.replace('_',' ').title()} "
            f"v{old_strategy.version}\n"
            f"<b>New:</b> {new_strategy.strategy_type.replace('_',' ').title()} "
            f"v{new_strategy.version} | Tickers: {tickers}\n\n"
            f"<b>Entry:</b> {new_strategy.entry_signal}\n"
            f"<b>Exit:</b> {new_strategy.exit_signal}\n\n"
            f"<i>Why:</i> {new_strategy.reasoning}"
        )
        self._send(ALERT_STRATEGY_UPDATE, msg)

    def send_trade_entry(self, symbol: str, side: str, qty: float,
                          price: float, strategy_version: int) -> None:
        msg = (
            f"<b>📈 APEX – Trade Entry</b>\n"
            f"{side.upper()} {qty:.0f} shares of <b>{symbol}</b> @ ${price:.2f}\n"
            f"Strategy v{strategy_version}"
        )
        self._send(ALERT_TRADE, msg)

    def send_trade_exit(self, symbol: str, qty: float, exit_price: float,
                         net_pnl: float, exit_reason: str, strategy_version: int) -> None:
        pnl_sign = "+" if net_pnl >= 0 else ""
        emoji = "✅" if net_pnl >= 0 else "🔴"
        msg = (
            f"<b>{emoji} APEX – Trade Exit</b>\n"
            f"Closed {qty:.0f} <b>{symbol}</b> @ ${exit_price:.2f}\n"
            f"P&amp;L: {pnl_sign}${net_pnl:.2f} | Reason: {exit_reason.replace('_',' ')}\n"
            f"Strategy v{strategy_version}"
        )
        self._send(ALERT_TRADE, msg)

    def send_loss_limit_warning(self, daily_pnl: float, limit_pct: float,
                                  capital: float) -> None:
        limit_dollars = capital * limit_pct / 100
        msg = (
            f"<b>⚠️ APEX – Loss Limit Warning</b>\n\n"
            f"Daily P&amp;L: ${daily_pnl:.2f}\n"
            f"Daily loss limit: ${limit_dollars:.2f} ({limit_pct}%)\n"
            f"Approaching limit — monitor closely."
        )
        self._send(ALERT_WARNING, msg)

    def send_kill_switch_alert(self, trigger_source: str, orders_cancelled: int,
                                positions_closed: int) -> None:
        msg = (
            f"<b>🛑 APEX – KILL SWITCH ACTIVATED</b>\n\n"
            f"Triggered by: {trigger_source}\n"
            f"Time: {datetime.utcnow().strftime('%H:%M UTC')}\n"
            f"Open orders cancelled: {orders_cancelled}\n"
            f"Positions closed: {positions_closed}\n\n"
            f"<b>Status: ALL TRADING HALTED</b>"
        )
        self._send(ALERT_KILL, msg)

    def send_eod_report(self, total_trades: int, win_rate: float, net_pnl: float,
                         max_drawdown: float, strategy_updates: int) -> None:
        pnl_sign = "+" if net_pnl >= 0 else ""
        now_str = datetime.utcnow().strftime("%a %d %b %Y")
        msg = (
            f"<b>📊 APEX – End of Day | {now_str}</b>\n\n"
            f"Trades: {total_trades} | Win Rate: {win_rate:.1f}%\n"
            f"Net P&amp;L: {pnl_sign}${net_pnl:.2f}\n"
            f"Max Drawdown: {max_drawdown:.2f}%\n"
            f"Strategy Updates: {strategy_updates}\n\n"
            f"<i>System entering sleep until 6:00am EST.</i>"
        )
        self._send(ALERT_EOD, msg)

    def send_error_alert(self, error_message: str) -> None:
        msg = (
            f"<b>❗ APEX – System Error</b>\n\n"
            f"{error_message}\n\n"
            f"<i>Time: {datetime.utcnow().strftime('%H:%M UTC')}</i>"
        )
        self._send(ALERT_ERROR, msg)

    def send_system_startup(self, mode: str) -> None:
        msg = (
            f"<b>🟢 APEX – System Started</b>\n\n"
            f"Mode: <b>{mode.upper()}</b>\n"
            f"Time: {datetime.utcnow().strftime('%H:%M UTC')}\n"
            f"Pre-market strategy generation at 6:00am EST."
        )
        self._send(ALERT_MORNING_STRATEGY, msg)

    # ─── Internal Methods ─────────────────────────────────────────────────────

    def _send(self, alert_type: str, content: str, channel: str = "telegram") -> bool:
        """Send alert with retry logic. Always logs to DB."""
        log_id = self._log_alert(alert_type, channel, content, status="pending")

        for attempt in range(3):
            try:
                if self._send_telegram(content):
                    self._update_log(log_id, "sent")
                    return True
            except requests.exceptions.RequestException as exc:
                logger.warning(f"Alert attempt {attempt + 1}/3 failed: {exc}")
                if attempt < 2:
                    time.sleep(2 ** attempt)  # 1s, 2s backoff
            except Exception as exc:
                logger.error(f"Unexpected alert error: {exc}")
                break

        self._update_log(log_id, "failed", error=str("Delivery failed after 3 attempts"))
        logger.error(f"Alert delivery failed [{alert_type}]: {content[:80]}...")
        return False

    def _send_telegram(self, message: str) -> bool:
        """Send message via Telegram Bot API."""
        if not self.config.telegram_bot_token or not self.config.telegram_chat_id:
            logger.debug("Telegram not configured — alert skipped.")
            return True  # Don't block system if Telegram not set up

        url = f"{self._base_url}/sendMessage"
        payload = {
            "chat_id": self.config.telegram_chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return response.json().get("ok", False)

    def _log_alert(self, alert_type: str, channel: str, content: str,
                    status: str = "pending") -> Optional[int]:
        try:
            with get_db_session() as session:
                log = AlertLog(
                    sent_at=datetime.utcnow(),
                    alert_type=alert_type,
                    channel=channel,
                    content=content,
                    status=status,
                )
                session.add(log)
                session.flush()
                return log.id
        except Exception as exc:
            logger.error(f"Failed to log alert to DB: {exc}")
            return None

    def _update_log(self, log_id: Optional[int], status: str,
                     error: Optional[str] = None) -> None:
        if log_id is None:
            return
        try:
            with get_db_session() as session:
                log = session.query(AlertLog).filter_by(id=log_id).first()
                if log:
                    log.status = status
                    log.error_message = error
        except Exception as exc:
            logger.error(f"Failed to update alert log {log_id}: {exc}")
