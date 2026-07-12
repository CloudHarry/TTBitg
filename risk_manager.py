"""
risk_manager.py
================
TripWire: kill-switch berbasis aturan untuk mengurangi risiko kerugian
beruntun atau bug runaway. BUKAN jaminan keamanan mutlak -- ini hanya
pemutus otomatis berdasarkan batas yang kamu tentukan sendiri:
  - Batas rugi harian (daily loss limit)
  - Batas jumlah trade per hari & per jam
  - Batas kekalahan beruntun (loss streak)
  - Kesehatan koneksi API (kalau API sering gagal, bot berhenti sementara)
"""

import logging
from collections import deque
from datetime import date, datetime

logger = logging.getLogger("TripWire")


class TripWire:
    def __init__(
        self,
        daily_loss_limit_usdt=-20.0,
        max_trades_per_day=20,
        max_trades_per_hour=6,
        max_loss_streak=3,
        api_health_window=5,
    ):
        self.daily_loss_limit_usdt = daily_loss_limit_usdt
        self.max_trades_per_day = max_trades_per_day
        self.max_trades_per_hour = max_trades_per_hour
        self.max_loss_streak = max_loss_streak
        self.api_health_window = api_health_window

        self._today = date.today()
        self.daily_pnl = 0.0
        self.trades_today = 0
        self._trade_timestamps_hour = deque()
        self.win_streak = 0
        self.loss_streak = 0
        self._api_call_results = deque(maxlen=api_health_window)

        self._locked = False
        self._lock_reason = None

    # ------------------------------------------------------------------
    def _roll_day_if_needed(self):
        if date.today() != self._today:
            logger.info("Hari baru terdeteksi. Reset counter harian TripWire.")
            self._today = date.today()
            self.daily_pnl = 0.0
            self.trades_today = 0
            self._locked = False
            self._lock_reason = None

    def _prune_hourly(self):
        cutoff = datetime.utcnow().timestamp() - 3600
        while self._trade_timestamps_hour and self._trade_timestamps_hour[0] < cutoff:
            self._trade_timestamps_hour.popleft()

    # ------------------------------------------------------------------
    def record_api_call(self, success: bool):
        """Panggil ini setiap kali habis melakukan call API (sukses/gagal)."""
        self._api_call_results.append(success)

    def api_failure_count(self):
        return sum(1 for r in self._api_call_results if not r)

    def api_health_label(self):
        return f"{self.api_failure_count()}/{self.api_health_window}"

    # ------------------------------------------------------------------
    def record_trade_open(self):
        self._roll_day_if_needed()
        self.trades_today += 1
        self._trade_timestamps_hour.append(datetime.utcnow().timestamp())
        self._prune_hourly()

    def trades_this_hour(self):
        self._prune_hourly()
        return len(self._trade_timestamps_hour)

    def record_trade_close(self, pnl_usdt):
        self._roll_day_if_needed()
        self.daily_pnl += pnl_usdt

        if pnl_usdt > 0:
            self.win_streak += 1
            self.loss_streak = 0
        elif pnl_usdt < 0:
            self.loss_streak += 1
            self.win_streak = 0

        if self.daily_pnl <= self.daily_loss_limit_usdt:
            self._locked = True
            self._lock_reason = f"Batas rugi harian tersentuh ({self.daily_pnl:.2f} USDT)"
            logger.warning(f"TRIPWIRE AKTIF: {self._lock_reason}")

        if self.loss_streak >= self.max_loss_streak:
            self._locked = True
            self._lock_reason = f"Loss streak {self.loss_streak}x berturut-turut"
            logger.warning(f"TRIPWIRE AKTIF: {self._lock_reason}")

    # ------------------------------------------------------------------
    def can_trade(self):
        """Return (allowed: bool, reason: str|None)."""
        self._roll_day_if_needed()

        if self._locked:
            return False, self._lock_reason

        if self.trades_today >= self.max_trades_per_day:
            return False, f"Limit trade harian tercapai ({self.trades_today}/{self.max_trades_per_day})"

        if self.trades_this_hour() >= self.max_trades_per_hour:
            return False, f"Limit trade per jam tercapai ({self.trades_this_hour()}/{self.max_trades_per_hour})"

        if self.api_failure_count() >= self.api_health_window:
            return False, "API gagal terus-menerus, bot dihentikan sementara demi keamanan"

        return True, None

    def reset(self):
        """Reset manual, dipanggil lewat command 'reset' di dashboard."""
        self._locked = False
        self._lock_reason = None
        self.loss_streak = 0
        logger.info("TripWire di-reset manual oleh user.")

    def status_label(self):
        """Return (label: str, is_clear: bool) untuk ditampilkan di dashboard."""
        allowed, reason = self.can_trade()
        if allowed:
            return "ALL CLEAR", True
        return reason or "LOCKED", False

    def summary(self):
        return {
            "daily_pnl": round(self.daily_pnl, 2),
            "trades_today": self.trades_today,
            "max_trades_per_day": self.max_trades_per_day,
            "trades_this_hour": self.trades_this_hour(),
            "max_trades_per_hour": self.max_trades_per_hour,
            "win_streak": self.win_streak,
            "loss_streak": self.loss_streak,
            "api_health": self.api_health_label(),
        }
