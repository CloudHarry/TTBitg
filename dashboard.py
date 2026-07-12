"""
dashboard.py (V2)
================
Kerangka TUI Dashboard lengkap menggunakan Rich Engine.
Mendukung visualisasi data posisi dinamis berbasis ATR dari mesin V2.
"""

import threading
from datetime import datetime
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from rich.table import Table

class LogBuffer:
    """Menampung log aktivitas sistem agar muncul live di TUI."""
    def __init__(self):
        self.logs = []
    
    def add(self, msg, level="INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        color = "white"
        if level == "SUCCESS": color = "green"
        elif level == "WARNING": color = "yellow"
        elif level == "ERROR": color = "red"
        
        self.logs.append(f"[{timestamp}] [{color}][{level}][/{color}] {msg}")
        if len(self.logs) > 30:
            self.logs.pop(0)

class BotState:
    """Global in-memory state tracker untuk bot."""
    def __init__(self):
        self.running = True
        self.mode = "DRY RUN"
        self.threshold_min = 70.0
        self.win_prob_min = 80.0
        self.symbol = "-"
        self.side = "-"
        self.leverage = 5
        self.entry_price = 0.0
        self.current_price = 0.0
        self.stop_loss = 0.0
        self.take_profit = 0.0
        self.size = 0.0
        self.upnl = 0.0
        self.balance_usdt = 0.0
        self.has_position = False
        self.scan_results = []
        self.last_scan_at = None
        self.active_trailing_pct = 1.5  # Variabel V2 untuk pelacakan ATR dinamis
        self.position_opened_at = None
        self.lock = threading.Lock()
        self.log = LogBuffer()

    def hold_duration_str(self):
        if not self.position_opened_at:
            return "00:00:00"
        diff = datetime.now() - self.position_opened_at
        total_seconds = int(diff.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def energy_percent(self):
        return 100 if self.has_position else 0

def _bar(percent):
    """Membuat visual progress bar sederhana."""
    slots = int(percent / 10)
    return "[" + "█" * slots + "░" * (10 - slots) + "]"

def render_dashboard(state, tripwire, cfg=None, last_command=""):
    """Fungsi utama merender seluruh elemen layout dashboard ke terminal."""
    layout = Layout()
    
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=3)
    )
    
    layout["body"].split_row(
        Layout(name="left", ratio=1),
        Layout(name="right", ratio=1)
    )
    
    layout["left"].split_column(
        Layout(name="position", ratio=1),
        Layout(name="tripwire", ratio=1)
    )
    
    layout["right"].split_column(
        Layout(name="scanner", ratio=5),
        Layout(name="logs", ratio=4)
    )
    
    # 1. HEADER
    status_text = "[bold green]RUNNING[/bold green]" if state.running else "[bold yellow]PAUSED[/bold yellow]"
    header_panel = Panel(
        Text.from_markup(f"🤖 [bold cyan]AUTOTRADE CORE ENGINE V2[/bold cyan] | Mode: [yellow]{state.mode}[/] | Status: {status_text} | Saldo: [green]${state.balance_usdt:,.2f} USDT[/]"),
        style="bold white on blue"
    )
    layout["header"].update(header_panel)
    
    # 2. PANEL POSISI ACTIVE
    if state.has_position:
        pnl_style = "green" if (state.upnl or 0) >= 0 else "red"
        active_trail = getattr(state, "active_trailing_pct", 1.0)
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
                f"Energy:{state.energy_percent()}/100  " + _bar(state.energy_percent()) + f"  [magenta]ATR-Trail: {active_trail}%[/]"
            ),
        ]
    else:
        pos_lines = [Text("Tidak ada posisi aktif saat ini.", style="yellow")]
        
    layout["position"].update(Panel(Text.join(Text("\n"), pos_lines), title="📊 ACTIVE POSITION"))
    
    # 3. PANEL RISK CONTROL (TRIPWIRE)
    tw_allowed, tw_reason = tripwire.can_trade()
    tw_style = "green" if tw_allowed else "bold red"
    tw_status = "READY" if tw_allowed else f"LOCKED ({tw_reason})"
    
    tw_lines = [
        Text.from_markup(f"Core Status: [{tw_style}]{tw_status}[/{tw_style}]"),
        Text.from_markup(f"Current Loss Streak: [yellow]{getattr(tripwire, 'loss_streak', 0)}x[/] berturut-turut"),
        Text.from_markup(f"API Health: [green]STABLE[/green]")
    ]
    layout["tripwire"].update(Panel(Text.join(Text("\n"), tw_lines), title="🛑 RISK MANAGEMENT (TRIPWIRE)"))
    
    # 4. PANEL MARKET SCANNER
    scan_time = state.last_scan_at.strftime('%H:%M:%S') if state.last_scan_at else "-"
    table = Table(title=f"Top Evaluated (Last Scan: {scan_time})", expand=True)
    table.add_column("Symbol", justify="left")
    table.add_column("Action", justify="center")
    table.add_column("Win Prob", justify="right")
    table.add_column("Chg %", justify="right")
    
    for res in state.scan_results:
        action_style = "green" if res.get("action") == "BUY" else "red"
        table.add_row(
            res.get("symbol", "-"),
            f"[{action_style}]{res.get('action', '-')}[/{action_style}]",
            f"{res.get('win_probability', 0)}%",
            f"{res.get('price_change_pct', 0):+.2f}%"
        )
    layout["scanner"].update(Panel(table, title="🔍 MARKET SCANNER"))
    
    # 5. LIVE LOGS
    log_lines = [Text.from_markup(log) for log in state.log.logs[-8:]]
    layout["logs"].update(Panel(Text.join(Text("\n"), log_lines), title="📜 LIVE SYSTEM LOGS"))
    
    # 6. COMMAND INPUT PANEL
    layout["footer"].update(Panel(Text(f"cmd> {last_command}", style="bold green"), title="⌨️ COMMAND INPUT"))
    
    return layout
