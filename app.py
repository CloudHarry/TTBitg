"""
app.py
======
Entry point bot dengan dashboard TUI interaktif.

Arsitektur:
- Thread "job"   -> jalankan scan/entry/trailing-stop tiap INTERVAL_MENIT,
                    mengikuti logika trading seperti biasa.
- Thread "render"-> auto-refresh tampilan dashboard tiap 1 detik.
- Thread utama   -> baca command dari keyboard (start/stop/scan/analyze/
                    reset/close/q).

Command yang tersedia:
  start          -> lanjutkan job otomatis (kalau sebelumnya di-stop)
  stop           -> jeda job otomatis (posisi yang sudah terbuka tetap dipantau trailing stop)
  scan           -> paksa scan manual sekarang juga
  analyze <coin> -> analisa satu simbol spesifik (contoh: analyze BTC/USDT:USDT)
  reset          -> reset TripWire manual (buka kunci kalau ke-lock)
  close          -> tutup posisi aktif sekarang juga (market order)
  q              -> keluar dari program

Jalankan:
    python app.py
"""

import logging
import os
import threading
import time
from datetime import datetime

import ccxt
from dotenv import load_dotenv
from rich.console import Console
from rich.live import Live

from trading_bot import TradingBot
from monitor import Monitor
from risk_manager import TripWire
from trade_journal import TradeJournal
from dashboard import BotState, render_dashboard

# ----------------------------------------------------------------------
# LOGGING -> diarahkan ke panel LOG dashboard, bukan ke stdout langsung
# ----------------------------------------------------------------------
logging.basicConfig(level=logging.WARNING)  # library lain tetap diam, dashboard yang tampilkan info penting
logger = logging.getLogger("App")

console = Console()

# ----------------------------------------------------------------------
# LOAD .env
# ----------------------------------------------------------------------
load_dotenv()

API_KEY = os.getenv("BITGET_API_KEY")
API_SECRET = os.getenv("BITGET_API_SECRET")
API_PASSWORD = os.getenv("BITGET_API_PASSWORD")

TIMEFRAME = os.getenv("TIMEFRAME", "1h")
LIMIT = int(os.getenv("LIMIT", "100"))
USDT_AMOUNT = float(os.getenv("USDT_AMOUNT", "50"))
LEVERAGE = int(os.getenv("LEVERAGE", "5"))
TRAILING_PERCENT = float(os.getenv("TRAILING_PERCENT", "1.0"))
TP_PERCENT = float(os.getenv("TP_PERCENT", "2.0"))
THRESHOLD_MIN = float(os.getenv("THRESHOLD_MIN", "70"))
WIN_PROB_MIN = float(os.getenv("WIN_PROB_MIN", "80"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
INTERVAL_MINUTES = int(os.getenv("INTERVAL_MINUTES", "15"))
POSITION_CHECK_SECONDS = int(os.getenv("POSITION_CHECK_SECONDS", "15"))
MAX_HOLD_MINUTES = int(os.getenv("MAX_HOLD_MINUTES", "240"))
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT_USDT", "-20"))
JOURNAL_PATH = os.getenv("JOURNAL_PATH", "trades_journal.csv")

if not API_KEY or not API_SECRET or not API_PASSWORD:
    console.print("[bold red]BITGET_API_KEY / BITGET_API_SECRET / BITGET_API_PASSWORD belum diset di .env[/bold red]")
    raise SystemExit(1)

# ----------------------------------------------------------------------
# INISIALISASI CLIENT, BOT, MONITOR, TRIPWIRE, STATE
# ----------------------------------------------------------------------
client = ccxt.bitget({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "password": API_PASSWORD,
    "enableRateLimit": True,
    "options": {"defaultType": "swap"},
})

state = BotState(max_hold_minutes=MAX_HOLD_MINUTES)
state.mode = "DRY RUN" if DRY_RUN else "LIVE"
state.threshold_min = THRESHOLD_MIN
state.win_prob_min = WIN_PROB_MIN
state.log.add("Menghubungkan ke Bitget...", "INFO")

try:
    client.load_markets()
    state.log.add("Berhasil load markets dari Bitget.", "SUCCESS")
except Exception as e:
    state.log.add(f"Gagal load_markets: {e}", "ERROR")

bot = TradingBot(client, symbol_list=None, timeframe=TIMEFRAME, limit=LIMIT, dry_run=DRY_RUN)
monitor = Monitor(client, dry_run=DRY_RUN)
tripwire = TripWire(daily_loss_limit_usdt=DAILY_LOSS_LIMIT)
journal = TradeJournal(filepath=JOURNAL_PATH)
state.log.add(f"Trade journal aktif: {JOURNAL_PATH}", "INFO")

active_position = False
current_symbol = None

# sinkronisasi state awal kalau ada posisi terbuka dari sebelumnya
try:
    open_positions = monitor.get_open_positions()
    if open_positions:
        p = open_positions[0]
        active_position = True
        current_symbol = p.get("symbol")
        state.has_position = True
        state.symbol = current_symbol
        state.side = "long"
        state.log.add(f"Posisi terbuka terdeteksi saat startup: {current_symbol}", "WARNING")
except Exception as e:
    state.log.add(f"Gagal sinkronisasi posisi awal: {e}", "WARNING")

_stop_event = threading.Event()
_last_command = ""


# ----------------------------------------------------------------------
# JOB UTAMA (dipanggil otomatis tiap INTERVAL_MINUTES, atau manual via 'scan')
# ----------------------------------------------------------------------
def check_entry(manual=False):
    """Scan koin & entry kalau belum ada posisi. Dipanggil tiap INTERVAL_MINUTES (berat, scan banyak koin)."""
    global active_position, current_symbol

    if active_position:
        return  # sudah ada posisi, tidak perlu scan lagi

    if not state.running and not manual:
        return

    allowed, reason = tripwire.can_trade()
    if not allowed:
        state.log.add(f"TripWire mengunci entry baru: {reason}", "WARNING")
        return

    state.log.add("Scanning simbol...", "INFO")
    try:
        result = bot.scan_and_filter(threshold_min=state.threshold_min, win_prob_min=state.win_prob_min)
        tripwire.record_api_call(True)
    except Exception as e:
        tripwire.record_api_call(False)
        state.log.add(f"Scan gagal: {e}", "ERROR")
        result = None

    with state.lock:
        state.scan_results = bot.last_scan_results
        state.last_scan_at = datetime.now()

    if result is None:
        state.log.add("Tidak ada koin lolos filter.", "INFO")
        return

    symbol, threshold, win_probability = result
    state.log.add(f"Kandidat: {symbol} thr={threshold}% wp={win_probability}%", "INFO")

    order = bot.entry_order(symbol, usdt_amount=USDT_AMOUNT, leverage=LEVERAGE)
    if order:
        active_position = True
        current_symbol = symbol
        tripwire.record_trade_open()
        with state.lock:
            state.has_position = True
            state.symbol = symbol
            state.side = "long"
            state.leverage = LEVERAGE
            state.entry_price = order.get("price") or order.get("average")
            state.stop_loss = round(state.entry_price * (1 - TRAILING_PERCENT / 100), 6) if state.entry_price else None
            state.take_profit = round(state.entry_price * (1 + TP_PERCENT / 100), 6) if state.entry_price else None
            state.size = order.get("size") or order.get("amount")
            state.position_opened_at = datetime.now()
        state.log.add(f"Entry berhasil: {symbol}", "SUCCESS")
        journal.log_entry(
            mode=state.mode,
            symbol=symbol,
            side="long",
            price=state.entry_price,
            size=state.size,
            usdt_amount=USDT_AMOUNT,
            leverage=LEVERAGE,
            threshold_pct=threshold,
            win_probability_pct=win_probability,
            balance_after=state.balance_usdt,
        )
    else:
        state.log.add(f"Entry gagal untuk {symbol}", "ERROR")


def check_position():
    """Cek trailing stop, take profit, update harga & uPnL. Dipanggil sering (ringan, cuma 1 ticker)."""
    global active_position, current_symbol

    if not active_position or not current_symbol:
        return

    try:
        closed = monitor.trail_stop(current_symbol, trailing_percent=TRAILING_PERCENT, take_profit=state.take_profit)
        tripwire.record_api_call(True)
    except Exception as e:
        tripwire.record_api_call(False)
        state.log.add(f"Trail stop error: {e}", "ERROR")
        closed = False

    # update harga & uPnL terkini untuk tampilan, terlepas closed atau tidak
    try:
        ticker = client.fetch_ticker(current_symbol)
        price = ticker.get("last") or ticker.get("close")
        with state.lock:
            state.current_price = price
            if state.entry_price and state.size:
                state.upnl = round((price - state.entry_price) * state.size, 4)
            sym_state = monitor.trailing_data.get(current_symbol)
            if sym_state:
                state.stop_loss = round(sym_state["highest_price"] * (1 - TRAILING_PERCENT / 100), 6)
    except Exception:
        pass

    if closed:
        reason_label = "take profit" if closed == "TAKE_PROFIT" else "trailing stop"
        state.log.add(f"Posisi {current_symbol} ditutup oleh {reason_label}.", "SUCCESS")
        journal.log_close(
            mode=state.mode,
            symbol=current_symbol,
            side=state.side,
            price=state.current_price,
            size=state.size,
            close_reason=closed,
            pnl_usdt=state.upnl,
            balance_after=state.balance_usdt,
        )
        tripwire.record_trade_close(state.upnl or 0)
        active_position = False
        current_symbol = None
        with state.lock:
            state.has_position = False
            state.symbol = "-"
            state.upnl = 0.0
            state.position_opened_at = None
            state.take_profit = None
            state.stop_loss = None


def job(manual=False):
    """Backward-compat wrapper: jalankan cek posisi (kalau ada) atau entry (kalau tidak ada)."""
    if active_position:
        check_position()
    else:
        check_entry(manual=manual)


def refresh_balance():
    """Fetch saldo USDT terkini dari exchange, update state.balance_usdt."""
    try:
        balance = client.fetch_balance(params={"type": "swap"})
        usdt = balance.get("USDT", {}) or balance.get("total", {}).get("USDT")
        if isinstance(usdt, dict):
            free = usdt.get("free")
        else:
            free = usdt
        if free is None:
            free = balance.get("free", {}).get("USDT")
        with state.lock:
            state.balance_usdt = float(free) if free is not None else state.balance_usdt
        tripwire.record_api_call(True)
    except Exception as e:
        tripwire.record_api_call(False)
        state.log.add(f"Gagal ambil saldo: {e}", "WARNING")


def balance_loop():
    """Thread terpisah, refresh saldo tiap 60 detik (tidak perlu nunggu siklus 15 menit)."""
    while not _stop_event.is_set():
        refresh_balance()
        time.sleep(60)


def manual_close():
    """Command 'close': tutup posisi aktif sekarang juga."""
    global active_position, current_symbol
    if not active_position or not current_symbol:
        state.log.add("Tidak ada posisi untuk ditutup.", "WARNING")
        return
    closed = monitor._close_position(current_symbol)
    if closed:
        state.log.add(f"Posisi {current_symbol} ditutup manual.", "SUCCESS")
        journal.log_close(
            mode=state.mode,
            symbol=current_symbol,
            side=state.side,
            price=state.current_price,
            size=state.size,
            close_reason="MANUAL",
            pnl_usdt=state.upnl,
            balance_after=state.balance_usdt,
        )
        tripwire.record_trade_close(state.upnl or 0)
        active_position = False
        current_symbol = None
        with state.lock:
            state.has_position = False
            state.symbol = "-"
            state.upnl = 0.0
    else:
        state.log.add(f"Gagal menutup posisi {current_symbol}.", "ERROR")


def manual_analyze(symbol):
    """Command 'analyze <coin>': analisa satu simbol spesifik."""
    if not symbol:
        state.log.add("Format: analyze <SYMBOL> contoh: analyze BTC/USDT:USDT", "WARNING")
        return
    df = bot.fetch_klines(symbol)
    if df is None:
        state.log.add(f"Gagal ambil data untuk {symbol}.", "ERROR")
        return
    signals = bot.compute_indicators(df)
    threshold, win_probability = bot.calculate_threshold_and_win_probability(signals)
    state.log.add(f"{symbol}: threshold={threshold}% win_prob={win_probability}%", "INFO")


# ----------------------------------------------------------------------
# THREAD: JOB SCHEDULER (interval otomatis)
# ----------------------------------------------------------------------
def entry_scan_loop():
    """Loop scan koin baru & entry -- tiap INTERVAL_MINUTES, cuma jalan kalau belum ada posisi."""
    check_entry(manual=True)  # panggil sekali di awal
    last_run = time.time()
    while not _stop_event.is_set():
        if time.time() - last_run >= INTERVAL_MINUTES * 60:
            check_entry()
            last_run = time.time()
        time.sleep(1)


def position_monitor_loop():
    """Loop cek posisi aktif (trailing stop, TP, update harga) -- tiap POSITION_CHECK_SECONDS, jauh lebih sering dari entry scan."""
    while not _stop_event.is_set():
        check_position()
        time.sleep(POSITION_CHECK_SECONDS)


# ----------------------------------------------------------------------
# THREAD: COMMAND INPUT
# ----------------------------------------------------------------------
def command_loop(live: Live):
    global _last_command
    while not _stop_event.is_set():
        try:
            cmd = input()
        except EOFError:
            break

        cmd = cmd.strip()
        if not cmd:
            continue

        _last_command = cmd
        parts = cmd.split(maxsplit=1)
        action = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        if action == "q":
            state.log.add("Keluar dari program...", "WARNING")
            _stop_event.set()
            break
        elif action == "start":
            state.running = True
            state.log.add("Job otomatis dilanjutkan.", "SUCCESS")
        elif action == "stop":
            state.running = False
            state.log.add("Job otomatis dijeda (posisi aktif tetap dipantau).", "WARNING")
        elif action == "scan":
            state.log.add("Scan manual dipicu...", "INFO")
            threading.Thread(target=check_entry, kwargs={"manual": True}, daemon=True).start()
        elif action == "analyze":
            threading.Thread(target=manual_analyze, args=(arg,), daemon=True).start()
        elif action == "reset":
            tripwire.reset()
            state.log.add("TripWire di-reset.", "SUCCESS")
        elif action == "close":
            threading.Thread(target=manual_close, daemon=True).start()
        else:
            state.log.add(f"Command tidak dikenal: {cmd}", "WARNING")


# ----------------------------------------------------------------------
# THREAD: RENDER LOOP
# ----------------------------------------------------------------------
def render_loop(live: Live):
    while not _stop_event.is_set():
        try:
            live.update(render_dashboard(state, tripwire, cfg=None, last_command=_last_command))
        except Exception:
            pass
        time.sleep(1)


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
if __name__ == "__main__":
    with Live(console=console, screen=True, refresh_per_second=1) as live:
        t_entry = threading.Thread(target=entry_scan_loop, daemon=True)
        t_position = threading.Thread(target=position_monitor_loop, daemon=True)
        t_render = threading.Thread(target=render_loop, args=(live,), daemon=True)
        t_balance = threading.Thread(target=balance_loop, daemon=True)
        t_entry.start()
        t_position.start()
        t_render.start()
        t_balance.start()

        try:
            command_loop(live)
        except KeyboardInterrupt:
            _stop_event.set()

    console.print("[bold]Bot dihentikan.[/bold]")
