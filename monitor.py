"""
monitor.py (V2)
==============
Mendukung kalkulasi trailing stop adaptif dinamis per posisi.
"""

import logging
logger = logging.getLogger("Monitor")

class Monitor:
    def __init__(self, client, dry_run=False):
        self.client = client
        self.dry_run = dry_run
        self.trailing_data = {}

    def get_open_positions(self):
        try:
            positions = self.client.get_open_positions()
        except AttributeError:
            try:
                positions = self.client.fetch_positions()
            except Exception:
                return []
        except Exception:
            return []

        open_positions = []
        for p in positions or []:
            try:
                size = float(p.get("contracts") or p.get("size") or p.get("amount") or 0)
                if size != 0: open_positions.append(p)
            except (TypeError, ValueError):
                continue
        return open_positions

    def trail_stop(self, symbol, trailing_percent=1.5, take_profit=None):
        try:
            ticker = self.client.fetch_ticker(symbol)
            current_price = ticker.get("last") or ticker.get("close")
            if not current_price: return None
        except Exception as e:
            logger.error(f"Gagal fetch_ticker {symbol}: {e}")
            return None

        if take_profit is not None and current_price >= take_profit:
            if self._close_position(symbol):
                self.trailing_data.pop(symbol, None)
                return "TAKE_PROFIT"
            return None

        if symbol not in self.trailing_data:
            self.trailing_data[symbol] = {"highest_price": current_price}
            logger.info(f"[MONITOR] Mengunci Jarak Trailing Dinamis V2: {trailing_percent}%")

        state = self.trailing_data[symbol]
        if current_price > state["highest_price"]:
            state["highest_price"] = current_price

        stop_price = state["highest_price"] * (1 - trailing_percent / 100)

        if current_price <= stop_price:
            if self._close_position(symbol):
                self.trailing_data.pop(symbol, None)
                return "TRAILING_STOP"

        return None

    def _close_position(self, symbol):
        if self.dry_run:
            return True
        try:
            positions = self.get_open_positions()
            pos = next((p for p in positions if p.get("symbol") == symbol), None)
            if pos is None: return False

            size = float(pos.get("contracts") or pos.get("size") or pos.get("amount") or 0)
            if size <= 0: return False

            self.client.create_order(symbol=symbol, type="market", side="sell", amount=size, params={"reduceOnly": True})
            return True
        except Exception as e:
            logger.error(f"Gagal close posisi {symbol}: {e}")
            return False
