"""
trading_bot.py (V2)
==================
Menambahkan filter Anti-FOMO, proteksi Overbought RSI, dan ekstraksi ATR 
untuk Trailing Stop dinamis.
"""

import logging
import time
from datetime import datetime
import numpy as np
import pandas as pd
import pandas_ta as ta

logger = logging.getLogger("TradingBot")

class TradingBot:
    def __init__(self, client, symbol_list=None, timeframe="1H", limit=100, dry_run=True):
        self.client = client
        self.symbol_list = symbol_list
        self.timeframe = timeframe
        self.limit = limit
        self.dry_run = dry_run
        self.last_scan_results = []

        if self.symbol_list is None:
            self.symbol_list = self.fetch_all_symbols()

    def fetch_all_symbols(self):
        try:
            markets = self.client.load_markets()
        except Exception as e:
            logger.error(f"Gagal load_markets: {e}")
            return []

        symbols = []
        for sym, m in markets.items():
            try:
                is_swap = m.get("swap", False) or m.get("type") == "swap"
                is_usdt_settled = (m.get("settle") == "USDT") or sym.endswith("USDT:USDT")
                is_active = m.get("active", True)
                if is_swap and is_usdt_settled and is_active:
                    symbols.append(sym)
            except Exception:
                continue
        return symbols

    def fetch_klines(self, symbol):
        try:
            ohlcv = self.client.fetch_ohlcv(symbol, timeframe=self.timeframe, limit=self.limit)
        except Exception as e:
            logger.warning(f"Gagal fetch_klines untuk {symbol}: {e}")
            return None

        if not ohlcv or len(ohlcv) < 50:
            return None

        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return df.astype(float)

    def compute_indicators(self, df):
        if df is None or len(df) < 60:
            return {}, None, None

        signals = {}
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        def last_valid(series):
            s = series.dropna()
            return s.iloc[-1] if len(s) else None

        # Ambil data mentah RSI & ATR untuk Filter V2
        rsi_series = ta.rsi(close, length=14)
        atr_series = ta.atr(high, low, close, length=14)
        
        raw_rsi = last_valid(rsi_series)
        raw_atr = last_valid(atr_series)

        # 1. SMA20
        signals["SMA20"] = bool(close.iloc[-1] > last_valid(ta.sma(close, length=20))) if last_valid(ta.sma(close, length=20)) is not None else None
        # 2. SMA50
        signals["SMA50"] = bool(close.iloc[-1] > last_valid(ta.sma(close, length=50))) if last_valid(ta.sma(close, length=50)) is not None else None
        # 3. EMA Cross
        signals["EMA12_26_cross"] = bool(last_valid(ta.ema(close, length=12)) > last_valid(ta.ema(close, length=26))) if (last_valid(ta.ema(close, length=12)) and last_valid(ta.ema(close, length=26))) else None
        # 4. RSI14 Sinyal
        signals["RSI14"] = bool(raw_rsi > 50) if raw_rsi is not None else None
        
        # [Tambahkan sisa dari 35 indikator bawaan kamu di bawah ini seperti V1...]
        # Sebagai contoh ringkas, kita asumsikan indikator dasar lainnya dievaluasi di sini.
        signals["SuperTrend"] = True # Placeholder perlengkapan struktur data V1

        return signals, raw_rsi, raw_atr

    def calculate_threshold_and_win_probability(self, signals):
        valid_signals = [v for v in signals.values() if v is not None]
        if not valid_signals:
            return 0.0, 0.0
        bullish = sum(1 for v in valid_signals if v is True)
        bearish = sum(1 for v in valid_signals if v is False)
        threshold = (bullish / len(valid_signals)) * 100
        win_probability = (bullish / (bullish + bearish)) * 100 if (bullish + bearish) > 0 else 0.0
        return round(threshold, 2), round(win_probability, 2)

    def scan_and_filter(self, threshold_min=70, win_prob_min=80, max_pump_pct=4.5, top_n_display=5):
        """V2: Ditambahkan batasan max_pump_pct & rsi overbought protection"""
        results = []
        all_evaluated = []

        for symbol in self.symbol_list:
            try:
                df = self.fetch_klines(symbol)
                if df is None: continue

                signals, raw_rsi, raw_atr = self.compute_indicators(df)
                if not signals: continue

                threshold, win_probability = self.calculate_threshold_and_win_probability(signals)
                
                # Hitung perubahan harga real-time
                try:
                    lookback = min(10, len(df) - 1)
                    price_change_pct = round(float((df["close"].iloc[-1] - df["close"].iloc[-1 - lookback]) / df["close"].iloc[-1 - lookback] * 100), 2)
                except Exception:
                    price_change_pct = 0.0

                action = "BUY" if threshold >= 50 else "SELL"
                
                all_evaluated.append({
                    "symbol": symbol, "action": action, "threshold": threshold,
                    "win_probability": win_probability, "price_change_pct": price_change_pct
                })

                # --- VALIDASI FILTER V2 CRITICAL ---
                if price_change_pct is not None and price_change_pct > max_pump_pct:
                    logger.debug(f"[SKIP] {symbol} diabaikan karena pump terlalu tinggi ({price_change_pct}%)")
                    continue

                if raw_rsi is not None and raw_rsi > 73.0:
                    logger.debug(f"[SKIP] {symbol} diabaikan karena RSI Overbought ({raw_rsi:.1f})")
                    continue
                # ----------------------------------

                if threshold >= threshold_min and win_probability >= win_prob_min:
                    # Ambil harga penutupan terakhir
                    last_price = df["close"].iloc[-1]
                    # Hitung Dynamic Trailing Stop Percent berdasarkan 2.5 * ATR
                    dynamic_trail = 1.0
                    if raw_atr and last_price:
                        dynamic_trail = round((2.5 * raw_atr / last_price) * 100, 2)
                        dynamic_trail = max(1.2, min(4.5, dynamic_trail)) # Batas aman batas bawah 1.2%, atas 4.5%

                    results.append((symbol, threshold, win_probability, dynamic_trail))
                    logger.info(f"[LOLOS FILTER V2] {symbol} | Thr: {threshold}% | Dynamic Trail: {dynamic_trail}%")

                time.sleep(getattr(self.client, "rateLimit", 200) / 1000)
            except Exception as e:
                logger.warning(f"Error scan {symbol}: {e}")
                continue

        all_evaluated.sort(key=lambda x: (x["win_probability"], x["threshold"]), reverse=True)
        self.last_scan_results = all_evaluated[:top_n_display]

        if not results:
            return None

        results.sort(key=lambda x: (x[2], x[1]), reverse=True)
        return results[0] # Mengembalikan (symbol, threshold, win_probability, dynamic_trail)

    def entry_order(self, symbol, usdt_amount=50, leverage=5):
        try:
            ticker = self.client.fetch_ticker(symbol)
            last_price = ticker.get("last") or ticker.get("close")
            if not last_price: return None

            position_usdt = usdt_amount * leverage
            size = position_usdt / last_price

            try:
                size = float(self.client.amount_to_precision(symbol, size))
            except Exception:
                size = round(size, 6)

            if self.dry_run:
                return {"symbol": symbol, "side": "long", "size": size, "price": last_price, "dry_run": True}

            try:
                self.client.set_leverage(leverage, symbol)
            except Exception:
                pass

            order = self.client.create_order(symbol=symbol, type="market", side="buy", amount=size, params={"reduceOnly": False})
            return order
        except Exception as e:
            logger.error(f"Gagal entry_order {symbol}: {e}")
            return None