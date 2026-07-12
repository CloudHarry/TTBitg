"""
trade_journal.py
=================
Mencatat setiap kejadian trading (entry & close) ke file CSV, supaya
histori tetap ada meski dashboard/log di layar sudah bergulir atau bot
di-restart. Bisa dibuka langsung pakai Excel / Google Sheets.

Format 1 baris = 1 kejadian (ENTRY atau CLOSE). Untuk lihat hasil 1 trade
utuh, cocokkan baris ENTRY dan CLOSE dengan symbol dan waktu yang berdekatan.
"""

import csv
import os
from datetime import datetime

FIELDNAMES = [
    "timestamp",
    "event",          # ENTRY atau CLOSE
    "mode",            # DRY RUN atau LIVE
    "symbol",
    "side",
    "price",
    "size",
    "usdt_amount",     # margin yang dipakai (cuma diisi saat ENTRY)
    "leverage",
    "threshold_pct",   # skor threshold saat entry (cuma diisi saat ENTRY)
    "win_probability_pct",  # skor win_probability saat entry (cuma diisi saat ENTRY)
    "close_reason",    # TAKE_PROFIT / TRAILING_STOP / MANUAL (cuma diisi saat CLOSE)
    "pnl_usdt",        # cuma diisi saat CLOSE
    "balance_after",
]


class TradeJournal:
    def __init__(self, filepath="trades_journal.csv"):
        self.filepath = filepath
        self._ensure_file()

    def _ensure_file(self):
        """Buat file dengan header kalau belum ada."""
        if not os.path.exists(self.filepath):
            with open(self.filepath, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
                writer.writeheader()

    def _write_row(self, row: dict):
        full_row = {key: row.get(key, "") for key in FIELDNAMES}
        full_row["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.filepath, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writerow(full_row)

    def log_entry(self, mode, symbol, side, price, size, usdt_amount, leverage,
                  threshold_pct=None, win_probability_pct=None, balance_after=None):
        self._write_row({
            "event": "ENTRY",
            "mode": mode,
            "symbol": symbol,
            "side": side,
            "price": price,
            "size": size,
            "usdt_amount": usdt_amount,
            "leverage": leverage,
            "threshold_pct": threshold_pct,
            "win_probability_pct": win_probability_pct,
            "balance_after": balance_after,
        })

    def log_close(self, mode, symbol, side, price, size, close_reason, pnl_usdt, balance_after=None):
        self._write_row({
            "event": "CLOSE",
            "mode": mode,
            "symbol": symbol,
            "side": side,
            "price": price,
            "size": size,
            "close_reason": close_reason,
            "pnl_usdt": pnl_usdt,
            "balance_after": balance_after,
        })
