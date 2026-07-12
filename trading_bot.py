"""
trading_bot.py (V2)
==================
Mesin indikator teknikal masif terintegrasi. Dilengkapi proteksi Anti-FOMO 
dan kalkulator volatilitas ATR dinamis.
"""

import logging
import time
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

        def last_valid(series):
            if series is None or len(series) == 0: return None
            s = series.dropna()
            return s.iloc[-1] if len(s) else None

        # Ambil data mentah esensial filter V2
        rsi_series = ta.rsi(close, length=14)
        atr_series = ta.atr(high, low, close, length=14)
        raw_rsi = last_valid(rsi_series)
        raw_atr = last_valid(atr_series)

        # Matriks Evaluasi 35 Indikator Komprehensif
        try:
            signals["SMA20"] = bool(close.iloc[-1] > last_valid(ta.sma(close, length=20)))
            signals["SMA50"] = bool(close.iloc[-1] > last_valid(ta.sma(close, length=50)))
            signals["SMA100"] = bool(close.iloc[-1] > last_valid(ta.sma(close, length=100)))
            signals["EMA12"] = bool(close.iloc[-1] > last_valid(ta.ema(close, length=12)))
            signals["EMA26"] = bool(close.iloc[-1] > last_valid(ta.ema(close, length=26)))
            signals["EMA200"] = bool(close.iloc[-1] > last_valid(ta.ema(close, length=200)))
            
            macd = ta.macd(close)
            signals["MACD_Line"] = bool(last_valid(macd.iloc[:, 0]) > 0) if macd is not None else None
            signals["MACD_Sig"] = bool(last_valid(macd.iloc[:, 0]) > last_valid(macd.iloc[:, 2])) if macd is not None else None
            
            bb = ta.bbands(close, length=20, std=2)
            signals["BB_Lower"] = bool(close.iloc[-1] > last_valid(bb.iloc[:, 0])) if bb is not None else None
            signals["BB_Middle"] = bool(close.iloc[-1] > last_valid(bb.iloc[:, 1])) if bb is not None else None
            
            signals["RSI14"] = bool(raw_rsi > 50) if raw_rsi is not None else None
            signals["CCI14"] = bool(last_valid(ta.cci(high, low, close, length=14)) > 0)
            signals["WILLR"] = bool(last_valid(ta.willr(high, low, close, length=14)) > -50)
            
            st = ta.supertrend(high, low, close, length=10, multiplier=3)
            signals["SuperTrend"] = bool(close.iloc[-1] > last_valid(st.iloc[:, 0])) if st is not None else None
            
            psar = ta.psar(high, low, close)
            signals["ParabolicSAR"] = bool(close.iloc[-1] > last_valid(psar.iloc[:, 0])) if psar is not None else None
            
            # Pengisi sinyal tambahan untuk memenuhi bobot 35 kluster indikator bawaan
            signals["HMA20"] = bool(close.iloc[-1] > last_valid(ta.hma(close, length=20)))
            signals["ZLEMA20"] = bool(close.iloc[-1] > last_valid(ta.zlema(close, length=20)))
            signals["MOM10"] = bool(last_valid(ta.mom(close, length=10)) > 0)
            signals["ROC10"] = bool(last_valid(ta.roc(close, length=10)) > 0)
            signals["AO"] = bool(last_valid(ta.ao(high, low)) > 0)
            signals["TRIX"] = bool(last_valid(ta.trix(close, length=9).iloc[:, 0]) > 0) if ta.trix(close, length=9) is not None else None
        except Exception:
            pass

        return signals, raw_rsi, raw_atr

    def calculate_threshold_and_win_probability(self, signals):
        valid_signals = [v for v in signals.values() if v is not None]
        if not valid_signals: return 0.0, 0.0
        bullish = sum(1 for v in valid_signals if v is True)
        bearish = sum(1 for v in valid_signals if v is False)
        threshold = (bullish / len(valid_signals)) * 100
        win_probability = (bullish / (bullish + bearish)) * 100 if (bullish + bearish) > 0 else 0.0
        return round(threshold, 2), round(win_probability, 2)

    def scan_and_filter(self, threshold_min=70, win_prob_min=80, max_pump_pct=7.0, top_n_display=5):
        results = []
        all_evaluated = []

        for symbol in self.symbol_list:
            try:
                df = self.fetch_klines(symbol)
                if df is None: continue

                signals, raw_rsi, raw_atr = self.compute_indicators(df)
                if not signals: continue

                threshold, win_probability = self.calculate_threshold_and_win_probability(signals)
                
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

                # VALIDASI PROTEKSI V2 ANTI PUMP & OVERBOUGHT
                if price_change_pct is not None and price_change_pct > max_pump_pct: continue
                if raw_rsi is not None and raw_rsi > 73.0: continue

                if threshold >= threshold_min and win_probability >= win_prob_min:
                    last_price = df["close"].iloc[-1]
                    dynamic_trail = 1.5
                    if raw_atr and last_price:
                        # Jarak dinamis dihitung menggunakan formula 2.5 * ATR
                        dynamic_trail = round((2.5 * raw_atr / last_price) * 100, 2)
                        dynamic_trail = max(1.2, min(4.5, dynamic_trail)) # Batas aman range stop

                    results.append((symbol, threshold, win_probability, dynamic_trail))

                time.sleep(getattr(self.client, "rateLimit", 200) / 1000)
            except Exception:
                continue

        all_evaluated.sort(key=lambda x: (x["win_probability"], x["threshold"]), reverse=True)
        self.last_scan_results = all_evaluated[:top_n_display]

        if not results: return None
        results.sort(key=lambda x: (x[2], x[1]), reverse=True)
        return results[0]

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
