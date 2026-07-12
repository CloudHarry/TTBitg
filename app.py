"""
app.py (V2)
==========
Orkestrator Utama yang menangkap parameter ATR dinamis 
dan menyuntikkannya ke modul Monitor Posisi.
"""

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

load_dotenv()
console = Console()

# Ambil Konfigurasi Dasar .env
API_KEY = os.getenv("BITGET_API_KEY")
API_SECRET = os.getenv("BITGET_API_SECRET")
API_PASSWORD = os.getenv("BITGET_API_PASSWORD")
TIMEFRAME = os.getenv("TIMEFRAME", "1h")
LIMIT = int(os.getenv("LIMIT", "100"))
USDT_AMOUNT = float(os.getenv("USDT_AMOUNT", "50"))
LEVERAGE = int(os.getenv("LEVERAGE", "5"))
TP_PERCENT = float(os.getenv("TP_PERCENT", "2.0"))
THRESHOLD_MIN = float(os.getenv("THRESHOLD_MIN", "70"))
WIN_PROB_MIN = float(os.getenv("WIN_PROB_MIN", "80"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
INTERVAL_MINUTES = int(os.getenv("INTERVAL_MINUTES", "15"))
POSITION_CHECK_SECONDS = int(os.getenv("POSITION_CHECK_SECONDS", "15"))

client = ccxt.bitget({
    "apiKey": API_KEY, "secret": API_SECRET, "password": API_PASSWORD,
    "enableRateLimit": True, "options": {"defaultType": "swap"},
})

state = BotState()
state.mode = "DRY RUN" if DRY_RUN else "LIVE"
state.threshold_min = THRESHOLD_MIN
state.win_prob_min = WIN_PROB_MIN

# Tambah variabel penampung di state untuk menahan data dynamic trail
state.active_trailing_pct = 1.5 

client.load_markets()
bot = TradingBot(client, symbol_list=None, timeframe=TIMEFRAME, limit=LIMIT, dry_run=DRY_RUN)
monitor = Monitor(client, dry_run=DRY_RUN)
tripwire = TripWire()
journal = TradeJournal()

active_position = False
current_symbol = None
_stop_event = threading.Event()
_last_command = ""

def check_entry(manual=False):
    global active_position, current_symbol
    if active_position: return
    if not state.running and not manual: return

    allowed, reason = tripwire.can_trade()
    if not allowed: return

    state.log.add("Scanning simbol (Menggunakan Proteksi V2)...", "INFO")
    result = bot.scan_and_filter(threshold_min=state.threshold_min, win_prob_min=state.win_prob_min, max_pump_pct=4.5)

    with state.lock:
        state.scan_results = bot.last_scan_results
        state.last_scan_at = datetime.now()

    if result is None:
        state.log.add("Tidak ada koin aman yang lolos filter V2.", "INFO")
        return

    # Destrukturisasi 4 komponen baru V2 (termasuk dynamic_trail)
    symbol, threshold, win_probability, dynamic_trail = result
    state.log.add(f"Kandidat Terpilih: {symbol} | Target Trail Stop: {dynamic_trail}%", "INFO")

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
            state.active_trailing_pct = dynamic_trail # Simpan ke state agar bisa dirender
            state.entry_price = order.get("price") or order.get("average")
            state.stop_loss = round(state.entry_price * (1 - dynamic_trail / 100), 6) if state.entry_price else None
            state.take_profit = round(state.entry_price * (1 + TP_PERCENT / 100), 6) if state.entry_price else None
            state.size = order.get("size") or order.get("amount")
            state.position_opened_at = datetime.now()
        
        state.log.add(f"Entry Sukses V2: {symbol}!", "SUCCESS")
        journal.log_entry(state.mode, symbol, "long", state.entry_price, state.size, USDT_AMOUNT, LEVERAGE, threshold, win_probability, state.balance_usdt)

def check_position():
    global active_position, current_symbol
    if not active_position or not current_symbol: return

    # Ambil nilai trailing stop dinamis yang disimpan sewaktu entry tadi
    current_trail = getattr(state, "active_trailing_pct", 1.5)

    try:
        closed = monitor.trail_stop(current_symbol, trailing_percent=current_trail, take_profit=state.take_profit)
    except Exception as e:
        closed = False

    try:
        ticker = client.fetch_ticker(current_symbol)
        price = ticker.get("last") or ticker.get("close")
        with state.lock:
            state.current_price = price
            if state.entry_price and state.size:
                state.upnl = round((price - state.entry_price) * state.size, 4)
            sym_state = monitor.trailing_data.get(current_symbol)
            if sym_state:
                state.stop_loss = round(sym_state["highest_price"] * (1 - current_trail / 100), 6)
    except Exception:
        pass

    if closed:
        state.log.add(f"Posisi {current_symbol} Berhasil Keluar via {closed}.", "SUCCESS")
        journal.log_close(state.mode, current_symbol, state.side, state.current_price, state.size, closed, state.upnl, state.balance_usdt)
        tripwire.record_trade_close(state.upnl or 0)
        active_position = False
        current_symbol = None
        with state.lock:
            state.has_position = False
            state.symbol = "-"

def entry_scan_loop():
    check_entry(manual=True)
    last_run = time.time()
    while not _stop_event.is_set():
        if time.time() - last_run >= INTERVAL_MINUTES * 60:
            check_entry()
            last_run = time.time()
        time.sleep(1)

def position_monitor_loop():
    while not _stop_event.is_set():
        check_position()
        time.sleep(POSITION_CHECK_SECONDS)

# [Gunakan sisa thread render_loop, balance_loop, dan main block bawaan V1 kamu...]

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
    """Thread terpisah, refresh saldo tiap 60 detik."""
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
            state.active_trailing_pct = 1.5  # Reset ke default safety value
    else:
        state.log.add(f"Gagal menutup posisi {current_symbol}.", "ERROR")


def manual_analyze(symbol):
    """Command 'analyze <coin>': analisa satu simbol spesifik dengan kalkulasi ATR V2."""
    if not symbol:
        state.log.add("Format: analyze <SYMBOL> contoh: analyze BTC/USDT:USDT", "WARNING")
        return
    df = bot.fetch_klines(symbol)
    if df is None:
        state.log.add(f"Gagal ambil data untuk {symbol}.", "ERROR")
        return
    signals, raw_rsi, raw_atr = bot.compute_indicators(df)
    threshold, win_probability = bot.calculate_threshold_and_win_probability(signals)
    
    # Hitung estimasi dynamic trail jika di-analyze saat ini
    est_trail = 1.5
    if raw_atr and df["close"].iloc[-1]:
        est_trail = round((2.5 * raw_atr / df["close"].iloc[-1]) * 100, 2)
        est_trail = max(1.2, min(4.5, est_trail))

    state.log.add(f"{symbol}: Thr={threshold}% | WP={win_probability}% | RSI={raw_rsi:.1f} | Est-Trail={est_trail}%", "INFO")


# ----------------------------------------------------------------------
# THREAD: COMMAND INPUT (Menerima input keyboard dari user)
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
            state.log.add("Scan manual V2 dipicu...", "INFO")
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
# THREAD: RENDER LOOP (Auto-refresh layar TUI)
# ----------------------------------------------------------------------
def render_loop(live: Live):
    while not _stop_event.is_set():
        try:
            live.update(render_dashboard(state, tripwire, cfg=None, last_command=_last_command))
        except Exception:
            pass
        time.sleep(1)


# ----------------------------------------------------------------------
# MAIN BLOCK (Titik masuk utama program)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Sinkronisasi awal saldo sebelum merender layar utama
    refresh_balance()
    
    with Live(console=console, screen=True, refresh_per_second=1) as live:
        t_entry = threading.Thread(target=entry_scan_loop, daemon=True)
        t_position = threading.Thread(target=position_monitor_loop, daemon=True)
        t_render = threading.Thread(target=render_loop, args=(live,), daemon=True)
        t_balance = threading.Thread(target=balance_loop, daemon=True)
        
        # Nyalakan seluruh engine thread background
        t_entry.start()
        t_position.start()
        t_render.start()
        t_balance.start()

        try:
            command_loop(live)
        except KeyboardInterrupt:
            _stop_event.set()

    console.print("[bold green]Bot Autotrade V2 berhasil dihentikan dengan aman.[/bold green]")
