"""
dashboard.py
============
Tampilan dashboard terminal (mirip screenshot referensi) memakai library
`rich`. Menampilkan: status bot, posisi aktif, TripWire (risk management),
tabel hasil scan, dan log terkini -- semua auto-refresh.

CATATAN DESAIN:
- Dashboard ini FULL-SCREEN (rich.live dengan screen=True), mirip TUI di
  gambar referensi.
- Auto-refresh dijeda sesaat ketika user sedang mengetik command, supaya
  ketikan tidak "diganggu" oleh redraw. Setelah command dieksekusi (Enter),
  auto-refresh jalan lagi.
- Energy bar bersifat INFORMATIF saja (estimasi sisa waktu tahan posisi
  vs batas maksimum yang kamu set) -- TIDAK otomatis memaksa close posisi.
  Penutupan posisi tetap lewat trailing stop (Monitor) atau command manual.
"""

import threading
import time
from collections import deque
from datetime import datetime

from rich.console import Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

MAX_LOG_LINES = 6


class DashboardLogHandler:
    """Handler logging kecil yang menampung baris log terbaru untuk ditampilkan di panel LOG."""

    def __init__(self, maxlen=MAX_LOG_LINES):
        self.lines = deque(maxlen=maxlen)

    def add(self, message, level="INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        color = {
            "INFO": "cyan",
            "WARNING": "yellow",
            "ERROR": "red",
            "SUCCESS": "green",
        }.get(level, "white")
        self.lines.append((ts, message, color))


class BotState:
    """State bersama antara job trading (thread background) dan dashboard (thread render)."""

    def __init__(self, symbol_placeholder="-", max_hold_minutes=240):
        self.lock = threading.RLock()

        self.mode = "DRY RUN"  # atau "LIVE"
        self.running = True  # bisa di-pause via command 'stop'
        self.balance_usdt = 0.0

        # posisi aktif
        self.has_position = False
        self.symbol = symbol_placeholder
        self.side = None
        self.leverage = 1
        self.entry_price = None
        self.current_price = None
        self.stop_loss = None
        self.take_profit = None
        self.size = None
        self.upnl = 0.0
        self.position_opened_at = None
        self.max_hold_minutes = max_hold_minutes

        self.last_scan_at = None
        self.scan_results = []  # list of dict dari TradingBot.last_scan_results
        self.threshold_min = 70
        self.win_prob_min = 80

        self.log = DashboardLogHandler()

    def energy_percent(self):
        if not self.has_position or self.position_opened_at is None:
            return 100
        elapsed_min = (datetime.now() - self.position_opened_at).total_seconds() / 60
        pct = max(0, 100 - (elapsed_min / max(self.max_hold_minutes, 1)) * 100)
        return round(pct)

    def hold_duration_str(self):
        if not self.has_position or self.position_opened_at is None:
            return "-"
        elapsed = datetime.now() - self.position_opened_at
        h = int(elapsed.total_seconds() // 3600)
        m = int((elapsed.total_seconds() % 3600) // 60)
        return f"{h}h{m}m" if h else f"{m}min"


# --------------------------------------------------------------------------
# RENDERING
# --------------------------------------------------------------------------
def _bar(pct, width=20, color_ok="green", color_warn="yellow", color_bad="red"):
    pct = max(0, min(100, pct))
    filled = int(width * pct / 100)
    color = color_ok if pct > 50 else (color_warn if pct > 20 else color_bad)
    bar = f"[{color}]{'█' * filled}[/{color}]{'░' * (width - filled)}"
    return bar


def render_dashboard(state: BotState, tripwire, cfg, last_command=""):
    with state.lock:
        header = Text()
        header.append(" BITGET AUTOTRADE BOT ", style="bold white on blue")
        header.append(f"  [{datetime.now().strftime('%H:%M:%S')}]", style="dim")
        header_sub = Text()
        header_sub.append(f"{state.symbol}  ", style="bold")
        mode_style = "bold green" if state.mode == "LIVE" else "bold yellow"
        header_sub.append(f"{state.mode}  ", style=mode_style)
        run_style = "bold green" if state.running else "bold red"
        header_sub.append("RUNNING" if state.running else "PAUSED", style=run_style)

        balance_line = Text(f" $ {state.balance_usdt:.2f} USDT", style="bold yellow")

        # ---------------- PANEL POSISI ----------------
        if state.has_position:
            pnl_style = "green" if (state.upnl or 0) >= 0 else "red"
            pos_lines = [
                Text.from_markup(
                    f"[bold]POSISI:[/bold] [{'green' if state.side == 'long' else 'red'}]{state.side.upper() if state.side else '-'} "
                    f"x{state.leverage}[/] ({state.hold_duration_str()})  "
                    f"uPnL [{pnl_style}]{state.upnl:+.3f}[/{pnl_style}]"
                ),
                Text.from_markup(
                    f"E:{state.entry_price}  SL:{state.stop_loss}  TP:{state.take_profit}  sz:{state.size}"
                ),
                Text.from_markup(
                    f"Energy:{state.energy_percent()}/100  " + _bar(state.energy_percent())
                ),
            ]
        else:
            pos_lines = [Text("Tidak ada posisi aktif.", style="dim")]

        position_panel = Panel(Group(*pos_lines), title="POSISI", border_style="cyan")

        # ---------------- PANEL TRIPWIRE ----------------
        label, is_clear = tripwire.status_label()
        summary = tripwire.summary()
        tw_style = "bold green" if is_clear else "bold red"
        tw_lines = [
            Text.from_markup(f"[{tw_style}]{'✅ ' if is_clear else '⛔ '}{label}[/{tw_style}]"),
            Text(
                f"Daily PnL: {summary['daily_pnl']:+.2f} USDT   "
                f"Trades: {summary['trades_today']}/{summary['max_trades_per_day']}d "
                f"{summary['trades_this_hour']}/{summary['max_trades_per_hour']}h"
            ),
            Text(
                f"Win streak:{summary['win_streak']}  Loss streak:{summary['loss_streak']}  "
                f"API health:{summary['api_health']}"
            ),
        ]
        tripwire_panel = Panel(Group(*tw_lines), title="TRIPWIRE", border_style="magenta")

        # ---------------- TABEL SCAN ----------------
        scan_table = Table(show_header=True, header_style="bold", expand=True, box=None)
        scan_table.add_column("#", width=3)
        scan_table.add_column("Coin")
        scan_table.add_column("Sig")
        scan_table.add_column("Thr%", justify="right")
        scan_table.add_column("WP%", justify="right")
        scan_table.add_column("Chg%", justify="right")

        if state.scan_results:
            for i, r in enumerate(state.scan_results, start=1):
                sig_style = "bold green" if r["action"] == "BUY" else "bold red"
                chg = r.get("price_change_pct")
                chg_style = "green" if (chg or 0) >= 0 else "red"
                chg_str = f"{chg:+.2f}" if chg is not None else "-"
                star = "★" if i == 1 else str(i)
                scan_table.add_row(
                    star,
                    r["symbol"].split("/")[0],
                    Text(r["action"], style=sig_style),
                    f"{r['threshold']:.0f}",
                    f"{r['win_probability']:.0f}",
                    Text(chg_str, style=chg_style),
                )
        else:
            scan_table.add_row("-", "belum ada data scan", "", "", "", "")

        last_scan_label = (
            state.last_scan_at.strftime("%H:%M:%S") if state.last_scan_at else "belum pernah"
        )
        scan_panel = Panel(
            scan_table,
            title=f"SCAN (terakhir: {last_scan_label})  thr≥{state.threshold_min}%  wp≥{state.win_prob_min}%",
            border_style="blue",
        )

        # ---------------- PANEL LOG ----------------
        log_lines = []
        if state.log.lines:
            for ts, msg, color in state.log.lines:
                log_lines.append(Text.from_markup(f"[dim]{ts}[/dim] [{color}]{msg}[/{color}]"))
        else:
            log_lines = [Text("Belum ada aktivitas.", style="dim")]
        log_panel = Panel(Group(*log_lines), title="LOG", border_style="white")

        # ---------------- FOOTER / COMMAND ----------------
        footer = Text.from_markup(
            "[dim]perintah: start stop scan analyze <coin> reset close q[/dim]"
        )
        cmd_line = Text.from_markup(f"[bold]cmd>[/bold] {last_command}")

        body = Group(
            header,
            header_sub,
            balance_line,
            Text(""),
            position_panel,
            tripwire_panel,
            scan_panel,
            log_panel,
            Text(""),
            footer,
            cmd_line,
        )
        return Panel(body, border_style="bright_black")
