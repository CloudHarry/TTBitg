"""
trading_bot.py
==============
Class TradingBot: scanning multi-koin futures USDT-M di Bitget,
menghitung 35 indikator teknikal, threshold & win probability,
lalu entry order otomatis pada koin dengan sinyal terkuat.

CATATAN PENTING:
- "win_probability" di sini BUKAN probabilitas statistik yang divalidasi
  (bukan hasil backtest). Ini adalah rasio sederhana sinyal bullish vs
  bearish dari 35 indikator pada candle terakhir. Gunakan sebagai salah
  satu filter, bukan jaminan profit.
- Selalu test dengan dry_run=True / testnet dulu sebelum live trading.
"""

import logging
import time
from datetime import datetime

import numpy as np
import pandas as pd
import pandas_ta as ta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("TradingBot")


class TradingBot:
    def __init__(self, client, symbol_list=None, timeframe="1H", limit=100, dry_run=True):
        """
        client       : instance ccxt.bitget(...) yang sudah di-load market-nya
        symbol_list  : list simbol manual (opsional). Kalau None, akan di-fetch otomatis.
        timeframe    : timeframe candlestick, default '1H'
        limit        : jumlah candle yang diambil per fetch
        dry_run      : jika True, entry_order() hanya simulasi (tidak kirim order asli)
        """
        self.client = client
        self.symbol_list = symbol_list
        self.timeframe = timeframe
        self.limit = limit
        self.dry_run = dry_run

        if self.symbol_list is None:
            self.symbol_list = self.fetch_all_symbols()

        # Hasil scan terakhir (semua koin yang berhasil dievaluasi, bukan cuma
        # yang lolos filter). Dipakai untuk tabel scan di dashboard.
        # Format tiap item: dict dengan symbol, action, threshold, win_probability,
        # bullish, total_valid, price_change_pct
        self.last_scan_results = []

    # ------------------------------------------------------------------
    # SYMBOL & DATA FETCHING
    # ------------------------------------------------------------------
    def fetch_all_symbols(self):
        """Ambil semua simbol futures USDT-M (swap/perpetual) dari exchange."""
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

        logger.info(f"Ditemukan {len(symbols)} simbol futures USDT-M.")
        return symbols

    def fetch_klines(self, symbol):
        """Ambil candlestick OHLCV untuk sebuah simbol, kembalikan DataFrame."""
        try:
            ohlcv = self.client.fetch_ohlcv(symbol, timeframe=self.timeframe, limit=self.limit)
        except Exception as e:
            logger.warning(f"Gagal fetch_klines untuk {symbol}: {e}")
            return None

        if not ohlcv or len(ohlcv) < 50:
            return None

        df = pd.DataFrame(
            ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)
        return df

    # ------------------------------------------------------------------
    # 35 INDIKATOR TEKNIKAL
    # ------------------------------------------------------------------
    def compute_indicators(self, df):
        """
        Hitung 35 indikator teknikal dari DataFrame OHLCV.
        Return dict {nama_indikator: True/False/None}
        True  = sinyal bullish
        False = sinyal bearish
        None  = tidak cukup data / tidak valid
        """
        if df is None or len(df) < 60:
            return {}

        signals = {}
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        def last_valid(series):
            s = series.dropna()
            return s.iloc[-1] if len(s) else None

        def safe(fn, name):
            try:
                return fn()
            except Exception as e:
                logger.debug(f"Indikator {name} gagal: {e}")
                return None

        # 1. SMA20
        def sma20():
            sma = ta.sma(close, length=20)
            v = last_valid(sma)
            return None if v is None else bool(close.iloc[-1] > v)
        signals["SMA20"] = safe(sma20, "SMA20")

        # 2. SMA50
        def sma50():
            sma = ta.sma(close, length=50)
            v = last_valid(sma)
            return None if v is None else bool(close.iloc[-1] > v)
        signals["SMA50"] = safe(sma50, "SMA50")

        # 3. EMA12_26_cross
        def ema_cross():
            ema12 = ta.ema(close, length=12)
            ema26 = ta.ema(close, length=26)
            v12, v26 = last_valid(ema12), last_valid(ema26)
            return None if (v12 is None or v26 is None) else bool(v12 > v26)
        signals["EMA12_26_cross"] = safe(ema_cross, "EMA12_26_cross")

        # 4. RSI14
        def rsi14():
            rsi = ta.rsi(close, length=14)
            v = last_valid(rsi)
            return None if v is None else bool(v > 50)
        signals["RSI14"] = safe(rsi14, "RSI14")

        # 5. BollingerBands (harga vs mid band)
        def bbands():
            bb = ta.bbands(close, length=20, std=2)
            mid_col = [c for c in bb.columns if c.startswith("BBM")][0]
            v = last_valid(bb[mid_col])
            return None if v is None else bool(close.iloc[-1] > v)
        signals["BollingerBands"] = safe(bbands, "BollingerBands")

        # 6. MACD
        def macd():
            m = ta.macd(close)
            macd_col = [c for c in m.columns if c.startswith("MACD_")][0]
            sig_col = [c for c in m.columns if c.startswith("MACDs_")][0]
            v_macd, v_sig = last_valid(m[macd_col]), last_valid(m[sig_col])
            return None if (v_macd is None or v_sig is None) else bool(v_macd > v_sig)
        signals["MACD"] = safe(macd, "MACD")

        # 7. Stochastic
        def stoch():
            st = ta.stoch(high, low, close)
            k_col = [c for c in st.columns if c.startswith("STOCHk")][0]
            d_col = [c for c in st.columns if c.startswith("STOCHd")][0]
            vk, vd = last_valid(st[k_col]), last_valid(st[d_col])
            return None if (vk is None or vd is None) else bool(vk > vd)
        signals["Stochastic"] = safe(stoch, "Stochastic")

        # 8. ATR (naik = volatilitas naik -> dianggap bullish jika harga juga naik)
        def atr_signal():
            atr = ta.atr(high, low, close, length=14)
            v = last_valid(atr)
            v_prev = atr.dropna().iloc[-2] if len(atr.dropna()) > 1 else None
            if v is None or v_prev is None:
                return None
            return bool(v > v_prev and close.iloc[-1] > close.iloc[-2])
        signals["ATR"] = safe(atr_signal, "ATR")

        # 9. ADX (trend strength, bullish jika +DI > -DI)
        def adx_signal():
            adx = ta.adx(high, low, close, length=14)
            dip_col = [c for c in adx.columns if c.startswith("DMP")][0]
            dim_col = [c for c in adx.columns if c.startswith("DMN")][0]
            vp, vm = last_valid(adx[dip_col]), last_valid(adx[dim_col])
            return None if (vp is None or vm is None) else bool(vp > vm)
        signals["ADX"] = safe(adx_signal, "ADX")

        # 10. CCI
        def cci():
            c = ta.cci(high, low, close, length=20)
            v = last_valid(c)
            return None if v is None else bool(v > 0)
        signals["CCI"] = safe(cci, "CCI")

        # 11. Williams %R
        def willr():
            w = ta.willr(high, low, close, length=14)
            v = last_valid(w)
            return None if v is None else bool(v > -50)
        signals["Williams%R"] = safe(willr, "Williams%R")

        # 12. OBV
        def obv():
            o = ta.obv(close, volume)
            vo = o.dropna()
            if len(vo) < 2:
                return None
            return bool(vo.iloc[-1] > vo.iloc[-2])
        signals["OBV"] = safe(obv, "OBV")

        # 13. MFI
        def mfi():
            m = ta.mfi(high, low, close, volume, length=14)
            v = last_valid(m)
            return None if v is None else bool(v > 50)
        signals["MFI"] = safe(mfi, "MFI")

        # 14. ROC
        def roc():
            r = ta.roc(close, length=10)
            v = last_valid(r)
            return None if v is None else bool(v > 0)
        signals["ROC"] = safe(roc, "ROC")

        # 15. PPO
        def ppo():
            p = ta.ppo(close)
            col = [c for c in p.columns if c.startswith("PPO_")][0]
            v = last_valid(p[col])
            return None if v is None else bool(v > 0)
        signals["PPO"] = safe(ppo, "PPO")

        # 16. PVO
        def pvo():
            p = ta.pvo(volume)
            col = [c for c in p.columns if c.startswith("PVO_")][0]
            v = last_valid(p[col])
            return None if v is None else bool(v > 0)
        signals["PVO"] = safe(pvo, "PVO")

        # 17. KAMA
        def kama():
            k = ta.kama(close, length=10)
            v = last_valid(k)
            return None if v is None else bool(close.iloc[-1] > v)
        signals["KAMA"] = safe(kama, "KAMA")

        # 18. T3
        def t3():
            t = ta.t3(close, length=10)
            v = last_valid(t)
            return None if v is None else bool(close.iloc[-1] > v)
        signals["T3"] = safe(t3, "T3")

        # 19. Ichimoku (harga vs Senkou Span A)
        def ichimoku():
            ich, _ = ta.ichimoku(high, low, close)
            spanA_col = [c for c in ich.columns if c.startswith("ISA_")][0]
            v = last_valid(ich[spanA_col])
            return None if v is None else bool(close.iloc[-1] > v)
        signals["Ichimoku"] = safe(ichimoku, "Ichimoku")

        # 20. Parabolic SAR
        def psar():
            p = ta.psar(high, low, close)
            long_col = [c for c in p.columns if c.startswith("PSARl")][0]
            v = last_valid(p[long_col])
            return None if v is None else True if not pd.isna(v) else False
        signals["ParabolicSAR"] = safe(psar, "ParabolicSAR")

        # 21. Ultimate Oscillator
        def uo():
            u = ta.uo(high, low, close)
            v = last_valid(u)
            return None if v is None else bool(v > 50)
        signals["UltimateOscillator"] = safe(uo, "UltimateOscillator")

        # 22. TRIX
        def trix():
            t = ta.trix(close, length=15)
            col = [c for c in t.columns if c.startswith("TRIX_")][0]
            v = last_valid(t[col])
            return None if v is None else bool(v > 0)
        signals["TRIX"] = safe(trix, "TRIX")

        # 23. Vortex
        def vortex():
            v = ta.vortex(high, low, close, length=14)
            vip_col = [c for c in v.columns if c.startswith("VTXP")][0]
            vim_col = [c for c in v.columns if c.startswith("VTXM")][0]
            vp, vm = last_valid(v[vip_col]), last_valid(v[vim_col])
            return None if (vp is None or vm is None) else bool(vp > vm)
        signals["Vortex"] = safe(vortex, "Vortex")

        # 24. EFI (Elder Force Index)
        def efi():
            e = ta.efi(close, volume, length=13)
            v = last_valid(e)
            return None if v is None else bool(v > 0)
        signals["EFI"] = safe(efi, "EFI")

        # 25. CMO
        def cmo():
            c = ta.cmo(close, length=14)
            v = last_valid(c)
            return None if v is None else bool(v > 0)
        signals["CMO"] = safe(cmo, "CMO")

        # 26. Coppock Curve
        def coppock():
            c = ta.coppock(close)
            v = last_valid(c)
            return None if v is None else bool(v > 0)
        signals["CoppockCurve"] = safe(coppock, "CoppockCurve")

        # 27. ZLEMA
        def zlema():
            z = ta.zlma(close, length=20, mamode="ema")
            v = last_valid(z)
            return None if v is None else bool(close.iloc[-1] > v)
        signals["ZLEMA"] = safe(zlema, "ZLEMA")

        # 28. HMA
        def hma():
            h = ta.hma(close, length=20)
            v = last_valid(h)
            return None if v is None else bool(close.iloc[-1] > v)
        signals["HMA"] = safe(hma, "HMA")

        # 29. ALMA
        def alma():
            a = ta.alma(close, length=20)
            v = last_valid(a)
            return None if v is None else bool(close.iloc[-1] > v)
        signals["ALMA"] = safe(alma, "ALMA")

        # 30. SuperTrend
        def supertrend():
            st = ta.supertrend(high, low, close, length=10, multiplier=3.0)
            dir_col = [c for c in st.columns if c.startswith("SUPERTd")][0]
            v = last_valid(st[dir_col])
            return None if v is None else bool(v == 1)
        signals["SuperTrend"] = safe(supertrend, "SuperTrend")

        # 31. BB_Width (bullish jika band melebar & harga di atas mid)
        def bb_width():
            bb = ta.bbands(close, length=20, std=2)
            width_col = [c for c in bb.columns if c.startswith("BBB")][0]
            w = bb[width_col].dropna()
            if len(w) < 2:
                return None
            mid_col = [c for c in bb.columns if c.startswith("BBM")][0]
            mid = last_valid(bb[mid_col])
            return bool(w.iloc[-1] > w.iloc[-2] and close.iloc[-1] > mid)
        signals["BB_Width"] = safe(bb_width, "BB_Width")

        # 32. Fisher Transform
        def fisher():
            f = ta.fisher(high, low, length=9)
            col = [c for c in f.columns if c.startswith("FISHERT_")][0]
            v = last_valid(f[col])
            return None if v is None else bool(v > 0)
        signals["FisherTransform"] = safe(fisher, "FisherTransform")

        # 33. Keltner Channel
        def keltner():
            kc = ta.kc(high, low, close, length=20)
            mid_col = [c for c in kc.columns if c.startswith("KCB")][0]
            v = last_valid(kc[mid_col])
            return None if v is None else bool(close.iloc[-1] > v)
        signals["KeltnerChannel"] = safe(keltner, "KeltnerChannel")

        # 34. Donchian Channel
        def donchian():
            dc = ta.donchian(high, low, lower_length=20, upper_length=20)
            mid_col = [c for c in dc.columns if c.startswith("DCM")][0]
            v = last_valid(dc[mid_col])
            return None if v is None else bool(close.iloc[-1] > v)
        signals["DonchianChannel"] = safe(donchian, "DonchianChannel")

        # 35a. Quant (harga vs quantile 50 dari N candle terakhir)
        def quant():
            window = close.tail(50)
            median = window.quantile(0.5)
            return bool(close.iloc[-1] > median)
        signals["Quant"] = safe(quant, "Quant")

        # 35b. Regime (ADX tinggi + harga di atas SMA200 = uptrend regime)
        def regime():
            if len(close) < 200:
                return None
            sma200 = ta.sma(close, length=200)
            v200 = last_valid(sma200)
            adx = ta.adx(high, low, close, length=14)
            adx_col = [c for c in adx.columns if c.startswith("ADX_")][0]
            vadx = last_valid(adx[adx_col])
            if v200 is None or vadx is None:
                return None
            return bool(vadx > 20 and close.iloc[-1] > v200)
        signals["Regime"] = safe(regime, "Regime")

        return signals

    # ------------------------------------------------------------------
    # THRESHOLD & WIN PROBABILITY
    # ------------------------------------------------------------------
    def calculate_threshold_and_win_probability(self, signals):
        """
        signals: dict {nama_indikator: True/False/None}
        threshold       = (bullish / total_valid) * 100
        win_probability = (bullish / (bullish + bearish)) * 100
        (Catatan: threshold dan win_probability rumusnya identik karena
        None dikeluarkan dari total_valid di kedua kasus. Ini murni rasio
        sinyal, BUKAN probabilitas statistik tervalidasi.)
        """
        valid_signals = [v for v in signals.values() if v is not None]
        total_valid = len(valid_signals)

        if total_valid == 0:
            return 0.0, 0.0

        bullish = sum(1 for v in valid_signals if v is True)
        bearish = sum(1 for v in valid_signals if v is False)

        threshold = (bullish / total_valid) * 100

        if (bullish + bearish) == 0:
            win_probability = 0.0
        else:
            win_probability = (bullish / (bullish + bearish)) * 100

        return round(threshold, 2), round(win_probability, 2)

    # ------------------------------------------------------------------
    # SCAN & FILTER
    # ------------------------------------------------------------------
    def scan_and_filter(self, threshold_min=70, win_prob_min=80, top_n_display=5):
        """
        Scan semua koin di symbol_list, hitung threshold & win_probability,
        filter yang >= ambang batas, urutkan dari terbaik, kembalikan top1.

        Selain itu, simpan top_n_display hasil scan (lolos filter atau tidak)
        ke self.last_scan_results untuk ditampilkan di dashboard/tabel.

        Return: tuple (symbol, threshold, win_probability) atau None jika
        tidak ada yang lolos filter.
        """
        results = []
        all_evaluated = []  # semua koin yang berhasil dievaluasi, untuk tabel display

        for symbol in self.symbol_list:
            try:
                df = self.fetch_klines(symbol)
                if df is None:
                    continue

                signals = self.compute_indicators(df)
                if not signals:
                    continue

                threshold, win_probability = self.calculate_threshold_and_win_probability(signals)

                valid_signals = [v for v in signals.values() if v is not None]
                bullish = sum(1 for v in valid_signals if v is True)
                total_valid = len(valid_signals)
                action = "BUY" if threshold >= 50 else "SELL"

                # persentase perubahan harga terkini vs beberapa candle lalu (faktual, bukan prediksi)
                try:
                    lookback = min(10, len(df) - 1)
                    price_change_pct = round(
                        float((df["close"].iloc[-1] - df["close"].iloc[-1 - lookback]) / df["close"].iloc[-1 - lookback] * 100),
                        2,
                    )
                except Exception:
                    price_change_pct = None

                all_evaluated.append({
                    "symbol": symbol,
                    "action": action,
                    "threshold": threshold,
                    "win_probability": win_probability,
                    "bullish": bullish,
                    "total_valid": total_valid,
                    "price_change_pct": price_change_pct,
                })

                if threshold >= threshold_min and win_probability >= win_prob_min:
                    results.append((symbol, threshold, win_probability))
                    logger.info(
                        f"[LOLOS FILTER] {symbol} | threshold={threshold}% | win_prob={win_probability}%"
                    )
                else:
                    logger.debug(
                        f"{symbol} tidak lolos | threshold={threshold}% | win_prob={win_probability}%"
                    )

                # hindari rate limit
                time.sleep(getattr(self.client, "rateLimit", 200) / 1000)

            except Exception as e:
                logger.warning(f"Error saat scan {symbol}: {e}")
                continue

        # simpan top-N (urut berdasarkan win_probability) untuk dashboard, terlepas lolos filter atau tidak
        all_evaluated.sort(key=lambda x: (x["win_probability"], x["threshold"]), reverse=True)
        self.last_scan_results = all_evaluated[:top_n_display]

        if not results:
            logger.info("Tidak ada simbol yang lolos filter threshold/win_probability.")
            return None

        # urutkan: prioritas win_probability, lalu threshold
        results.sort(key=lambda x: (x[2], x[1]), reverse=True)
        top1 = results[0]
        logger.info(f"TOP1 hasil scan: {top1}")
        return top1

    # ------------------------------------------------------------------
    # ENTRY ORDER
    # ------------------------------------------------------------------
    def entry_order(self, symbol, usdt_amount=50, leverage=5):
        """
        Hitung size posisi dari usdt_amount x leverage, lalu buka posisi
        long (market order).

        PENTING: order asli hanya dikirim jika self.dry_run == False.
        """
        try:
            ticker = self.client.fetch_ticker(symbol)
            last_price = ticker.get("last") or ticker.get("close")
            if not last_price:
                logger.error(f"Tidak bisa ambil harga terakhir untuk {symbol}.")
                return None

            position_usdt = usdt_amount * leverage
            size = position_usdt / last_price

            # bulatkan size sesuai precision market (kalau tersedia)
            try:
                size = float(self.client.amount_to_precision(symbol, size))
            except Exception:
                size = round(size, 6)

            logger.info(
                f"Entry {symbol}: harga={last_price}, margin={usdt_amount} USDT, "
                f"leverage={leverage}x, size={size}"
            )

            if self.dry_run:
                logger.info(f"[DRY RUN] Order TIDAK dikirim. Simulasi entry_order {symbol} size={size}")
                return {
                    "symbol": symbol,
                    "side": "long",
                    "size": size,
                    "price": last_price,
                    "dry_run": True,
                    "timestamp": datetime.utcnow().isoformat(),
                }

            # set leverage dulu (method bisa berbeda tergantung versi ccxt/bitget)
            try:
                self.client.set_leverage(leverage, symbol)
            except Exception as e:
                logger.warning(f"Gagal set_leverage untuk {symbol}: {e}")

            order = self.client.create_order(
                symbol=symbol,
                type="market",
                side="buy",
                amount=size,
                params={"reduceOnly": False},
            )

            logger.info(f"Order berhasil dikirim: {order.get('id', order)}")
            return order

        except Exception as e:
            logger.error(f"Gagal entry_order untuk {symbol}: {e}")
            return None
