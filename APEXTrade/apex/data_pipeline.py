"""
APEX Data Pipeline
- Fetches 5-minute OHLCV bars from Alpaca every 5 minutes
- Computes technical indicators inline (RSI, MACD, EMA, ATR, volume ratio)
- Builds market context JSON fed to Claude for strategy generation
- Historical backfill via yfinance
- Market calendar check via Alpaca
"""
import logging
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Any

import pytz
import pandas as pd
import requests

from apex.database import get_db_session, OHLCVBar, Strategy, SystemState

logger = logging.getLogger(__name__)

EST = pytz.timezone("US/Eastern")


class DataPipeline:
    def __init__(self, config):
        self.config = config
        self._trading_client = None
        self._data_client = None
        self._init_alpaca()

    def _init_alpaca(self) -> None:
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.data.historical import StockHistoricalDataClient
            self._trading_client = TradingClient(
                self.config.alpaca_api_key,
                self.config.alpaca_api_secret,
                paper=self.config.alpaca_paper,
            )
            self._data_client = StockHistoricalDataClient(
                self.config.alpaca_api_key,
                self.config.alpaca_api_secret,
            )
            logger.info("Alpaca clients initialised.")
        except Exception as exc:
            logger.error(f"Alpaca init failed: {exc}")

    # ─── Market Calendar ─────────────────────────────────────────────────────

    def is_market_day(self) -> bool:
        """Return True if today is a NYSE trading day."""
        try:
            from alpaca.trading.requests import GetCalendarRequest
            today = date.today()
            cal = self._trading_client.get_calendar(
                GetCalendarRequest(start=today, end=today)
            )
            return len(cal) > 0
        except Exception as exc:
            logger.warning(f"Calendar check failed, assuming market day: {exc}")
            return True  # Fail-open: assume trading day if calendar unavailable

    def get_account_value(self) -> float:
        """Fetch current portfolio value from Alpaca."""
        try:
            account = self._trading_client.get_account()
            return float(account.portfolio_value)
        except Exception as exc:
            logger.error(f"Failed to fetch account value: {exc}")
            return 100_000.0  # Safe fallback

    # ─── OHLCV Fetching and Indicator Computation ─────────────────────────────

    def fetch_and_store_bars(self) -> int:
        """
        Fetch latest 5-min OHLCV bars for all configured tickers,
        compute indicators, and upsert into ohlcv_bars table.
        Returns the number of bars stored.
        """
        if self._data_client is None:
            logger.error("Data client not initialised. Cannot fetch bars.")
            return 0

        tickers = self.config.default_tickers
        end = datetime.now(pytz.UTC)
        start = end - timedelta(hours=2)  # 2h window to compute indicators

        stored = 0
        for symbol in tickers:
            try:
                df = self._fetch_bars_df(symbol, start, end, timeframe_minutes=5)
                if df.empty:
                    continue
                df = self._compute_indicators(df)
                stored += self._upsert_bars(symbol, df)
            except Exception as exc:
                logger.error(f"Error fetching bars for {symbol}: {exc}")

        logger.debug(f"Data pipeline: {stored} bars stored for {tickers}")
        return stored

    def _fetch_bars_df(self, symbol: str, start: datetime, end: datetime,
                        timeframe_minutes: int = 5) -> pd.DataFrame:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        request = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame(timeframe_minutes, TimeFrameUnit.Minute),
            start=start,
            end=end,
            limit=500,
        )
        bars = self._data_client.get_stock_bars(request)
        df = bars.df
        if df.empty:
            return pd.DataFrame()

        # Flatten MultiIndex if present
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level="symbol")

        df.index = pd.to_datetime(df.index, utc=True)
        df.sort_index(inplace=True)
        return df

    def _compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute all technical indicators using pandas-ta."""
        try:
            import pandas_ta as ta
        except ImportError:
            logger.error("pandas-ta not installed. Run: pip install pandas-ta")
            return df

        # RSI (14)
        df["rsi_14"] = ta.rsi(df["close"], length=14)

        # EMA (9, 21, 50)
        df["ema_9"] = ta.ema(df["close"], length=9)
        df["ema_21"] = ta.ema(df["close"], length=21)
        df["ema_50"] = ta.ema(df["close"], length=50)

        # MACD (12, 26, 9)
        macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
        if macd is not None and not macd.empty:
            df["macd"] = macd.get("MACD_12_26_9")
            df["macd_signal"] = macd.get("MACDs_12_26_9")

        # ATR (14)
        df["atr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)

        # Volume ratio: current volume / 20-bar average volume
        vol_avg = df["volume"].rolling(window=20).mean()
        df["volume_ratio"] = df["volume"] / vol_avg.replace(0, float("nan"))

        return df

    def _upsert_bars(self, symbol: str, df: pd.DataFrame) -> int:
        """Insert or update OHLCV bars (skips duplicates by symbol+timestamp)."""
        stored = 0
        with get_db_session() as session:
            for ts, row in df.iterrows():
                ts_utc = ts.to_pydatetime().replace(tzinfo=None)  # Store as naive UTC
                existing = session.query(OHLCVBar).filter_by(
                    symbol=symbol, timestamp=ts_utc
                ).first()
                if existing:
                    # Update indicators on existing bar
                    _update_bar_indicators(existing, row)
                else:
                    bar = OHLCVBar(
                        symbol=symbol,
                        timestamp=ts_utc,
                        open=float(row.get("open", 0)),
                        high=float(row.get("high", 0)),
                        low=float(row.get("low", 0)),
                        close=float(row.get("close", 0)),
                        volume=int(row.get("volume", 0)),
                        vwap=_safe_float(row.get("vwap")),
                        rsi_14=_safe_float(row.get("rsi_14")),
                        ema_9=_safe_float(row.get("ema_9")),
                        ema_21=_safe_float(row.get("ema_21")),
                        ema_50=_safe_float(row.get("ema_50")),
                        macd=_safe_float(row.get("macd")),
                        macd_signal=_safe_float(row.get("macd_signal")),
                        atr_14=_safe_float(row.get("atr_14")),
                        volume_ratio=_safe_float(row.get("volume_ratio")),
                    )
                    session.add(bar)
                    stored += 1
        return stored

    def get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        """Get the most recent bar for a symbol."""
        with get_db_session() as session:
            return (
                session.query(OHLCVBar)
                .filter_by(symbol=symbol)
                .order_by(OHLCVBar.timestamp.desc())
                .first()
            )

    def get_previous_bar(self, symbol: str, before_bar: OHLCVBar) -> Optional[OHLCVBar]:
        """Get the bar immediately before the given bar (for crossover detection)."""
        with get_db_session() as session:
            return (
                session.query(OHLCVBar)
                .filter(
                    OHLCVBar.symbol == symbol,
                    OHLCVBar.timestamp < before_bar.timestamp,
                )
                .order_by(OHLCVBar.timestamp.desc())
                .first()
            )

    # ─── Market Context Builder ───────────────────────────────────────────────

    def build_market_context(self) -> Dict[str, Any]:
        """
        Assemble the structured market context JSON injected into Claude.
        Includes: price data, indicators, VIX, news, last strategy performance.
        """
        context: Dict[str, Any] = {
            "date": date.today().isoformat(),
            "time_utc": datetime.utcnow().strftime("%H:%M"),
        }

        # Price data per ticker
        context["tickers"] = {}
        for symbol in self.config.default_tickers:
            try:
                latest = self.get_latest_bar(symbol)
                if latest:
                    context["tickers"][symbol] = {
                        "price": latest.close,
                        "rsi_14": _safe_float(latest.rsi_14),
                        "ema_9": _safe_float(latest.ema_9),
                        "ema_21": _safe_float(latest.ema_21),
                        "ema_50": _safe_float(latest.ema_50),
                        "macd": _safe_float(latest.macd),
                        "macd_signal": _safe_float(latest.macd_signal),
                        "atr_14": _safe_float(latest.atr_14),
                        "volume_ratio": _safe_float(latest.volume_ratio),
                    }
            except Exception as exc:
                logger.warning(f"Context: failed to get indicator data for {symbol}: {exc}")

        # VIX level (via yfinance as fallback)
        context["vix"] = self._fetch_vix()

        # News headlines (Alpha Vantage)
        context["news"] = self._fetch_news(self.config.default_tickers)

        # Market regime classification
        context["regime"] = self._classify_regime(context)

        # Last strategy performance
        context["last_strategy"] = self._get_last_strategy_performance()

        return context

    def _fetch_vix(self) -> Dict[str, Any]:
        """Fetch VIX level via yfinance."""
        try:
            import yfinance as yf
            vix = yf.Ticker("^VIX")
            hist = vix.history(period="5d", interval="1d")
            if not hist.empty:
                current = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[0])
                change_5d = ((current - prev) / prev * 100) if prev else 0.0
                return {"level": round(current, 2), "change_5d": round(change_5d, 2)}
        except Exception as exc:
            logger.warning(f"VIX fetch failed: {exc}")
        return {"level": None, "change_5d": None}

    def _fetch_news(self, tickers: List[str]) -> List[Dict]:
        """Fetch news headlines from Alpha Vantage (free tier: 25 req/day)."""
        headlines = []
        if not self.config.alpha_vantage_key or self.config.alpha_vantage_key == "demo":
            return headlines
        try:
            ticker_str = ",".join(tickers[:3])  # Limit to conserve API quota
            url = (
                "https://www.alphavantage.co/query"
                f"?function=NEWS_SENTIMENT&tickers={ticker_str}"
                f"&limit=5&apikey={self.config.alpha_vantage_key}"
            )
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            for item in data.get("feed", [])[:5]:
                headlines.append({
                    "headline": item.get("title", ""),
                    "source": item.get("source", ""),
                    "sentiment": item.get("overall_sentiment_label", ""),
                })
        except Exception as exc:
            logger.warning(f"News fetch failed: {exc}")
        return headlines

    def _classify_regime(self, context: Dict) -> str:
        """Simple regime classification based on VIX and EMA alignment."""
        vix = context.get("vix", {}).get("level")
        spy_data = context.get("tickers", {}).get("SPY", {})
        ema_9 = spy_data.get("ema_9")
        ema_21 = spy_data.get("ema_21")
        ema_50 = spy_data.get("ema_50")

        if vix and vix > 25:
            return "high_volatility"
        if vix and vix < 15:
            if ema_9 and ema_21 and ema_50:
                if ema_9 > ema_21 > ema_50:
                    return "trending_up"
                if ema_9 < ema_21 < ema_50:
                    return "trending_down"
            return "low_volatility"
        return "ranging"

    def _get_last_strategy_performance(self) -> Dict:
        """Summarise the active strategy's performance for Claude context."""
        try:
            with get_db_session() as session:
                state = session.query(SystemState).filter_by(id=1).first()
                if not state or not state.active_strategy_id:
                    return {}
                strategy = session.query(Strategy).filter_by(
                    id=state.active_strategy_id
                ).first()
                if not strategy:
                    return {}

                from apex.database import Trade
                trades = (
                    session.query(Trade)
                    .filter(Trade.strategy_id == strategy.id, Trade.status == "closed")
                    .all()
                )
                if not trades:
                    return {
                        "strategy_id": strategy.id,
                        "strategy_type": strategy.strategy_type,
                        "trades": 0,
                        "win_rate": None,
                        "net_pnl": 0.0,
                    }

                wins = sum(1 for t in trades if t.net_pnl and t.net_pnl > 0)
                net_pnl = sum(t.net_pnl or 0 for t in trades)
                return {
                    "strategy_id": strategy.id,
                    "version": strategy.version,
                    "strategy_type": strategy.strategy_type,
                    "trades": len(trades),
                    "win_rate": round(wins / len(trades) * 100, 1),
                    "net_pnl": round(net_pnl, 2),
                    "trigger_reason": strategy.trigger_reason,
                }
        except Exception as exc:
            logger.error(f"Error building last strategy performance: {exc}")
            return {}

    # ─── Historical Backfill ─────────────────────────────────────────────────

    def historical_backfill(self, tickers: List[str], years: int = 2) -> None:
        """Pull N years of daily OHLCV bars via yfinance for backtesting."""
        try:
            import yfinance as yf
        except ImportError:
            logger.error("yfinance not installed. Run: pip install yfinance")
            return

        end_date = date.today()
        start_date = date(end_date.year - years, end_date.month, end_date.day)

        for symbol in tickers:
            logger.info(f"Backfilling {symbol}: {start_date} → {end_date}")
            try:
                ticker = yf.Ticker(symbol)
                df = ticker.history(start=str(start_date), end=str(end_date), interval="1d")
                if df.empty:
                    logger.warning(f"No historical data for {symbol}")
                    continue
                df.columns = [c.lower() for c in df.columns]
                df = self._compute_indicators(df)
                count = self._upsert_bars(symbol, df)
                logger.info(f"  {symbol}: {count} bars stored")
            except Exception as exc:
                logger.error(f"Backfill failed for {symbol}: {exc}")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _safe_float(val) -> Optional[float]:
    """Convert to float, return None on NaN/None."""
    try:
        f = float(val)
        import math
        return None if math.isnan(f) else round(f, 6)
    except (TypeError, ValueError):
        return None


def _update_bar_indicators(bar: OHLCVBar, row: "pd.Series") -> None:
    bar.rsi_14 = _safe_float(row.get("rsi_14"))
    bar.ema_9 = _safe_float(row.get("ema_9"))
    bar.ema_21 = _safe_float(row.get("ema_21"))
    bar.ema_50 = _safe_float(row.get("ema_50"))
    bar.macd = _safe_float(row.get("macd"))
    bar.macd_signal = _safe_float(row.get("macd_signal"))
    bar.atr_14 = _safe_float(row.get("atr_14"))
    bar.volume_ratio = _safe_float(row.get("volume_ratio"))
