"""
app.py
======
Entry point bot. Load .env, inisialisasi client (ccxt Bitget futures),
TradingBot, dan Monitor. Loop utama pakai `schedule` setiap 15 menit:
  - kalau tidak ada posisi aktif -> scan_and_filter() lalu entry_order() ke top1
  - kalau ada posisi aktif       -> trail_stop() untuk cek apakah harus close

Jalankan:
    python app.py

WAJIB baca sebelum live trading:
- Set DRY_RUN=true di .env dulu untuk simulasi (tidak kirim order asli).
- Cek ulang method client (fetch_ohlcv, create_order, set_leverage, dll)
  cocok dengan versi ccxt/Bitget yang Anda pakai.
- Uang yang di-leverage bisa rugi cepat. Gunakan modal yang siap hilang.
"""

import logging
import os
import time

import ccxt
import schedule
from dotenv import load_dotenv

from trading_bot import TradingBot
from monitor import Monitor

# ----------------------------------------------------------------------
# LOGGING
# ----------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("App")

# ----------------------------------------------------------------------
# LOAD .env
# ----------------------------------------------------------------------
load_dotenv()

API_KEY = os.getenv("BITGET_API_KEY")
API_SECRET = os.getenv("BITGET_API_SECRET")
API_PASSWORD = os.getenv("BITGET_API_PASSWORD")  # passphrase, wajib untuk Bitget

TIMEFRAME = os.getenv("TIMEFRAME", "1h")
LIMIT = int(os.getenv("LIMIT", "100"))
USDT_AMOUNT = float(os.getenv("USDT_AMOUNT", "50"))
LEVERAGE = int(os.getenv("LEVERAGE", "5"))
TRAILING_PERCENT = float(os.getenv("TRAILING_PERCENT", "1.0"))
THRESHOLD_MIN = float(os.getenv("THRESHOLD_MIN", "70"))
WIN_PROB_MIN = float(os.getenv("WIN_PROB_MIN", "80"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
INTERVAL_MINUTES = int(os.getenv("INTERVAL_MINUTES", "15"))

if not API_KEY or not API_SECRET or not API_PASSWORD:
    logger.error("BITGET_API_KEY / BITGET_API_SECRET / BITGET_API_PASSWORD belum diset di .env")
    raise SystemExit(1)

if DRY_RUN:
    logger.warning("=== DRY RUN MODE AKTIF: order TIDAK akan dikirim ke exchange. ===")

# ----------------------------------------------------------------------
# INISIALISASI CLIENT
# ----------------------------------------------------------------------
client = ccxt.bitget({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "password": API_PASSWORD,
    "enableRateLimit": True,
    "options": {
        "defaultType": "swap",  # futures perpetual
    },
})

logger.info("Load markets dari Bitget...")
client.load_markets()

# ----------------------------------------------------------------------
# INISIALISASI BOT & MONITOR
# ----------------------------------------------------------------------
bot = TradingBot(client, symbol_list=None, timeframe=TIMEFRAME, limit=LIMIT, dry_run=DRY_RUN)
monitor = Monitor(client, dry_run=DRY_RUN)

# ----------------------------------------------------------------------
# GLOBAL STATE
# ----------------------------------------------------------------------
active_position = False
current_symbol = None

# sinkronisasi state di awal, jaga-jaga kalau app di-restart saat posisi masih terbuka
try:
    _open_positions = monitor.get_open_positions()
    if _open_positions:
        active_position = True
        current_symbol = _open_positions[0].get("symbol")
        logger.info(f"Ditemukan posisi terbuka saat startup: {current_symbol}. State disinkronkan.")
except Exception as e:
    logger.warning(f"Gagal sinkronisasi posisi awal: {e}")


# ----------------------------------------------------------------------
# JOB UTAMA
# ----------------------------------------------------------------------
def job():
    global active_position, current_symbol

    logger.info("=" * 60)
    logger.info(f"Menjalankan job | active_position={active_position} | current_symbol={current_symbol}")

    if not active_position:
        logger.info("Tidak ada posisi aktif. Mulai scan & filter simbol...")
        result = bot.scan_and_filter(threshold_min=THRESHOLD_MIN, win_prob_min=WIN_PROB_MIN)

        if result is None:
            logger.info("Tidak ada simbol yang lolos filter. Tunggu siklus berikutnya.")
            return

        symbol, threshold, win_probability = result
        logger.info(
            f"Kandidat entry: {symbol} | threshold={threshold}% | win_probability={win_probability}%"
        )

        order = bot.entry_order(symbol, usdt_amount=USDT_AMOUNT, leverage=LEVERAGE)

        if order:
            active_position = True
            current_symbol = symbol
            logger.info(f"Entry berhasil: {symbol}. State diupdate -> active_position=True")
        else:
            logger.error(f"Entry gagal untuk {symbol}. State tetap tidak aktif.")

    else:
        logger.info(f"Posisi aktif di {current_symbol}. Cek trailing stop...")
        closed = monitor.trail_stop(current_symbol, trailing_percent=TRAILING_PERCENT)

        if closed:
            logger.info(f"Posisi {current_symbol} ditutup oleh trailing stop. Reset state.")
            active_position = False
            current_symbol = None
        else:
            logger.info(f"Posisi {current_symbol} masih berjalan. Belum menyentuh stop.")


# ----------------------------------------------------------------------
# MAIN LOOP
# ----------------------------------------------------------------------
if __name__ == "__main__":
    logger.info(f"Bot dimulai. Interval job setiap {INTERVAL_MINUTES} menit. DRY_RUN={DRY_RUN}")

    schedule.every(INTERVAL_MINUTES).minutes.do(job)

    # panggil job() pertama kali sebelum masuk loop, biar tidak nunggu 15 menit
    job()

    while True:
        schedule.run_pending()
        time.sleep(1)
