"""
monitor.py
==========
Class Monitor: memantau posisi terbuka dan menjalankan trailing stop.

CATATAN:
- Trailing stop butuh referensi "harga tertinggi sejak entry" (untuk long).
  Karena itu Monitor menyimpan state internal (self.trailing_data) per simbol.
  Kalau bot/app di-restart, referensi ini akan reset -> pertimbangkan simpan
  ke file/DB kalau butuh persistensi lintas restart.
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("Monitor")


class Monitor:
    def __init__(self, client, dry_run=False):
        self.client = client
        self.dry_run = dry_run
        # state internal: {symbol: {"highest_price": float}}
        self.trailing_data = {}

    # ------------------------------------------------------------------
    def get_open_positions(self):
        """
        Ambil semua posisi terbuka. Mencoba client.get_open_positions()
        dulu (sesuai spesifikasi), fallback ke client.fetch_positions()
        kalau client-nya ccxt standar dan tidak punya method custom itu.
        """
        try:
            positions = self.client.get_open_positions()
        except AttributeError:
            logger.debug("client.get_open_positions() tidak ada, fallback ke fetch_positions().")
            try:
                positions = self.client.fetch_positions()
            except Exception as e:
                logger.error(f"Gagal ambil posisi terbuka: {e}")
                return []
        except Exception as e:
            logger.error(f"Gagal ambil posisi terbuka: {e}")
            return []

        # filter hanya posisi dengan size/contracts > 0
        open_positions = []
        for p in positions or []:
            try:
                size = float(p.get("contracts") or p.get("size") or p.get("amount") or 0)
                if size != 0:
                    open_positions.append(p)
            except (TypeError, ValueError):
                continue

        return open_positions

    # ------------------------------------------------------------------
    def trail_stop(self, symbol, trailing_percent=1.0, take_profit=None):
        """
        Jalankan trailing stop (dan cek take profit opsional) untuk posisi LONG di `symbol`.

        Logika:
        1. Ambil harga terakhir.
        2. Kalau take_profit diisi dan harga sekarang >= take_profit -> close (TP tersentuh).
        3. Update highest_price kalau harga sekarang bikin rekor baru.
        4. Hitung stop_price = highest_price * (1 - trailing_percent/100).
        5. Kalau harga sekarang <= stop_price -> close posisi (market sell, reduceOnly).

        Return: None kalau posisi belum ditutup, atau string alasan penutupan
        ("TAKE_PROFIT" / "TRAILING_STOP") kalau ditutup. String truthy jadi
        tetap kompatibel dengan `if trail_stop(...):` di kode lama.
        """
        try:
            ticker = self.client.fetch_ticker(symbol)
            current_price = ticker.get("last") or ticker.get("close")
            if not current_price:
                logger.warning(f"Tidak bisa ambil harga terakhir untuk {symbol}, skip trail_stop.")
                return None
        except Exception as e:
            logger.error(f"Gagal fetch_ticker {symbol}: {e}")
            return None

        # cek take profit dulu (kalau diisi)
        if take_profit is not None and current_price >= take_profit:
            logger.info(f"{symbol}: harga menyentuh take profit ({current_price} >= {take_profit}). Menutup posisi...")
            closed = self._close_position(symbol)
            if closed:
                self.trailing_data.pop(symbol, None)
                return "TAKE_PROFIT"
            return None

        # inisialisasi state kalau simbol ini baru dipantau
        if symbol not in self.trailing_data:
            self.trailing_data[symbol] = {"highest_price": current_price}
            logger.info(f"Mulai pantau trailing stop {symbol}, harga awal={current_price}")

        state = self.trailing_data[symbol]

        # update rekor harga tertinggi (untuk posisi long)
        if current_price > state["highest_price"]:
            state["highest_price"] = current_price
            logger.info(f"{symbol}: harga tertinggi baru = {current_price}")

        stop_price = state["highest_price"] * (1 - trailing_percent / 100)
        logger.info(
            f"{symbol}: harga sekarang={current_price}, highest={state['highest_price']}, "
            f"stop_price={round(stop_price, 6)}"
        )

        if current_price <= stop_price:
            logger.info(f"{symbol}: harga menyentuh stop ({current_price} <= {stop_price}). Menutup posisi...")
            closed = self._close_position(symbol)
            if closed:
                # bersihkan state setelah posisi ditutup

                del self.trailing_data[symbol]
                return "TRAILING_STOP"
            return None

        return None

    # ------------------------------------------------------------------
    def _close_position(self, symbol):
        """Tutup posisi long dengan market sell reduceOnly. Kalau dry_run, cuma simulasi tanpa cek posisi asli di exchange."""
        if self.dry_run:
            logger.info(f"[DRY RUN] {symbol}: simulasi close posisi (tidak ada order asli dikirim).")
            return True

        try:
            positions = self.get_open_positions()
            pos = next((p for p in positions if p.get("symbol") == symbol), None)

            if pos is None:
                logger.warning(f"Tidak ditemukan posisi terbuka untuk {symbol} saat mau close.")
                return False

            size = float(pos.get("contracts") or pos.get("size") or pos.get("amount") or 0)
            if size <= 0:
                logger.warning(f"Size posisi {symbol} tidak valid: {size}")
                return False

            order = self.client.create_order(
                symbol=symbol,
                type="market",
                side="sell",
                amount=size,
                params={"reduceOnly": True},
            )
            logger.info(f"Posisi {symbol} berhasil ditutup. Order: {order.get('id', order)}")
            return True

        except Exception as e:
            logger.error(f"Gagal menutup posisi {symbol}: {e}")
            return False
