# Bitget Autotrade Bot

Bot trading otomatis untuk **Bitget Futures (USDT-M)** yang men-scan seluruh koin, menghitung sinyal dari 35 indikator teknikal, lalu entry ke koin dengan sinyal terkuat. Dilengkapi trailing stop, sistem risk management (TripWire), dan dashboard terminal interaktif.

> ⚠️ **PERINGATAN PENTING**
> Bot ini trading **futures dengan leverage** menggunakan uang sungguhan. Leverage memperbesar untung *dan* rugi. `win_probability` yang dihitung bot **bukan probabilitas statistik tervalidasi** — itu cuma rasio sinyal bullish vs bearish dari 35 indikator pada satu momen, bukan hasil backtest. Selalu mulai dengan `DRY_RUN=true` dan modal kecil. Kamu bertanggung jawab penuh atas keputusan trading dan risiko kerugian.

---

## Daftar Isi

- [Fitur](#fitur)
- [Struktur Proyek](#struktur-proyek)
- [Instalasi](#instalasi)
- [Konfigurasi (.env)](#konfigurasi-env)
- [Cara Menjalankan](#cara-menjalankan)
- [Command Dashboard](#command-dashboard)
- [Cara Kerja Bot](#cara-kerja-bot)
- [35 Indikator Teknikal](#35-indikator-teknikal)
- [Risk Management (TripWire)](#risk-management-tripwire)
- [Troubleshooting](#troubleshooting)
- [Batasan & Catatan Jujur](#batasan--catatan-jujur)

---

## Fitur

- 🔍 Scan seluruh simbol futures USDT-M di Bitget secara otomatis
- 📊 35 indikator teknikal dihitung per koin (SMA, EMA, RSI, MACD, Ichimoku, SuperTrend, dll)
- 🎯 Filter otomatis: hanya entry ke koin dengan `threshold ≥ 70%` dan `win_probability ≥ 80%` (bisa diubah)
- 📈 Trailing stop otomatis untuk posisi terbuka
- 🛡️ TripWire — kill-switch otomatis: batas rugi harian, batas jumlah trade, batas loss streak, monitoring kesehatan API
- 🖥️ Dashboard terminal live (posisi, sinyal, log, status risk management)
- ⌨️ Command interaktif: `start`, `stop`, `scan`, `analyze`, `reset`, `close`, `q`
- 🧪 Mode `DRY_RUN` untuk simulasi tanpa kirim order asli

---

## Struktur Proyek

```
bitget-bot/
├── app.py                  # Entry point — dashboard interaktif (JALANKAN INI)
├── app_simple_backup.py    # Versi lama tanpa dashboard (loop + log biasa)
├── trading_bot.py          # Class TradingBot — scan, indikator, entry order
├── monitor.py               # Class Monitor — pantau posisi, trailing stop
├── risk_manager.py          # Class TripWire — risk management / kill-switch
├── dashboard.py              # Rendering tampilan TUI (rich)
├── requirements.txt          # Daftar dependency Python
├── .env.example               # Template konfigurasi (copy jadi .env)
└── README.md                  # Dokumentasi ini
```

---

## Instalasi

### 1. Clone / taruh semua file di satu folder

```bash
mkdir -p ~/bitget-bot
cd ~/bitget-bot
# taruh semua file .py, requirements.txt, .env.example di sini
```

### 2. (Opsional tapi disarankan) Buat virtual environment

```bash
python3 -m venv venv
source venv/bin/activate        # Linux/Mac
# atau
source venv/Scripts/activate    # Windows Git Bash
```

### 3. Install dependency

```bash
pip install -r requirements.txt
```

Dependency yang dipakai: `ccxt` (koneksi exchange), `pandas` + `pandas-ta` + `numpy` (perhitungan indikator), `python-dotenv` (load `.env`), `rich` (dashboard), `schedule` (dipakai `app_simple_backup.py`).

---

## Konfigurasi (.env)

Copy `.env.example` jadi `.env`:

```bash
cp .env.example .env
```

Lalu isi:

```bash
# Kredensial API Bitget
BITGET_API_KEY=isi_api_key_kamu
BITGET_API_SECRET=isi_api_secret_kamu
BITGET_API_PASSWORD=isi_passphrase_kamu

# Strategi
TIMEFRAME=1h
LIMIT=100
USDT_AMOUNT=50            # margin per entry (USDT)
LEVERAGE=5                # kali leverage
TRAILING_PERCENT=1.0      # trailing stop %
THRESHOLD_MIN=70          # minimal threshold buat entry
WIN_PROB_MIN=80           # minimal win_probability buat entry
INTERVAL_MINUTES=15       # interval siklus job otomatis

# Risk management
MAX_HOLD_MINUTES=240      # dipakai buat energy bar (informatif, tidak force-close)
DAILY_LOSS_LIMIT_USDT=-20 # TripWire lock kalau rugi harian tembus ini

# WAJIB true dulu sampai yakin bot berjalan benar
DRY_RUN=true
```

### Cara bikin API key Bitget

1. Login ke Bitget → **API Management**
2. Buat API key baru, isi **Passphrase** sendiri (dicatat, cuma muncul sekali)
3. Centang izin **Read** + **Futures Trade** — **JANGAN centang Withdraw**
4. (Opsional) set IP whitelist untuk keamanan ekstra
5. Copy API Key, Secret Key, Passphrase ke `.env`

---

## Cara Menjalankan

```bash
python app.py
```

Ini akan membuka dashboard full-screen di terminal, langsung mulai scan siklus pertama, lalu berjalan otomatis setiap `INTERVAL_MINUTES`.

Untuk keluar: ketik `q` lalu Enter (bukan `Ctrl+C` langsung, supaya thread berhenti dengan bersih).

### Menjalankan versi tanpa dashboard (log biasa)

Kalau dashboard bermasalah di environment kamu (misal terminal tidak mendukung), pakai versi simpel:

```bash
python app_simple_backup.py
```

Versi ini fungsinya sama persis, cuma tampilannya log biasa (bukan TUI), dan tidak ada command interaktif — murni jalan otomatis tiap 15 menit.

---

## Command Dashboard

Ketik di dashboard, lalu tekan Enter:

| Command | Fungsi |
|---|---|
| `start` | Lanjutkan job otomatis (kalau sebelumnya di-`stop`) |
| `stop` | Jeda job otomatis. Posisi yang sudah terbuka **tetap dipantau** trailing stop |
| `scan` | Paksa scan manual sekarang juga, tidak perlu tunggu interval |
| `analyze <symbol>` | Analisa 1 simbol spesifik, contoh: `analyze BTC/USDT:USDT` |
| `reset` | Reset TripWire manual (buka kunci kalau ke-lock oleh daily loss/loss streak) |
| `close` | Tutup posisi aktif sekarang juga (market order) |
| `q` | Keluar dari program dengan bersih |

---

## Cara Kerja Bot

1. **Scan**: `fetch_all_symbols()` ambil semua simbol futures USDT-M aktif di Bitget
2. **Ambil data**: `fetch_klines(symbol)` ambil candlestick per simbol sesuai `TIMEFRAME` dan `LIMIT`
3. **Hitung sinyal**: `compute_indicators(df)` jalankan 35 indikator, tiap indikator hasilkan `True` (bullish), `False` (bearish), atau `None` (data tidak cukup)
4. **Hitung skor**:
   - `threshold = (jumlah sinyal bullish / total sinyal valid) × 100`
   - `win_probability = (bullish / (bullish + bearish)) × 100`
5. **Filter & urutkan**: `scan_and_filter()` ambil semua koin dengan `threshold ≥ THRESHOLD_MIN` dan `win_probability ≥ WIN_PROB_MIN`, urutkan dari terbaik, ambil top-1 buat entry — sekaligus simpan top-5 buat ditampilkan di tabel dashboard
6. **Entry**: `entry_order()` hitung size dari `USDT_AMOUNT × LEVERAGE ÷ harga_terakhir`, kirim market order long (atau simulasi kalau `DRY_RUN=true`)
7. **Pantau**: `Monitor.trail_stop()` jalan tiap siklus selama ada posisi — hitung stop price dari harga tertinggi sejak entry, close otomatis kalau harga turun menyentuh stop
8. **Ulangi** tiap `INTERVAL_MINUTES`, atau manual lewat command `scan`

---

## 35 Indikator Teknikal

SMA20, SMA50, EMA12/26 Cross, RSI14, Bollinger Bands, MACD, Stochastic, ATR, ADX, CCI, Williams %R, OBV, MFI, ROC, PPO, PVO, KAMA, T3, Ichimoku, Parabolic SAR, Ultimate Oscillator, TRIX, Vortex, EFI, CMO, Coppock Curve, ZLEMA, HMA, ALMA, SuperTrend, BB Width, Fisher Transform, Keltner Channel, Donchian Channel, Quant (harga vs quantile 50), Regime (ADX + harga vs SMA200).

Tiap indikator independen — kalau data tidak cukup (misal candle kurang dari periode yang dibutuhkan), hasilnya `None` dan tidak ikut dihitung di `total_valid`.

---

## Risk Management (TripWire)

`risk_manager.py` — kill-switch berbasis aturan, **bukan jaminan keamanan mutlak**:

| Aturan | Default | Efek kalau tersentuh |
|---|---|---|
| Daily loss limit | `-20 USDT` | Entry baru dikunci sampai hari berganti atau `reset` manual |
| Loss streak | 3x rugi berturut-turut | Entry baru dikunci |
| Max trade per hari | 20 | Entry baru ditolak sampai besok |
| Max trade per jam | 6 | Entry baru ditolak sampai jam berikutnya |
| API health | 5 gagal berturut-turut | Entry baru dihentikan sementara |

Status TripWire selalu tampil di dashboard: `✅ ALL CLEAR` atau `⛔ <alasan>`. Reset manual lewat command `reset`.

---

## Troubleshooting

### `ConnectTimeout` / `Request timed out` ke `api.bitget.com`

Biasanya **bukan masalah kode**, tapi jaringan/ISP memblokir domain exchange. Cek:
```bash
ping api.bitget.com
curl -I https://api.bitget.com
```
Kalau timeout total, coba jaringan lain (hotspot HP) atau aktifkan VPN. Solusi paling stabil jangka panjang: jalankan bot di **VPS luar negeri** (misal Singapore) supaya tidak kena blokir ISP lokal.

### `python3: command not found` (Windows)

Windows pakai `python`, bukan `python3`. Pastikan juga saat install Python dari python.org, centang **"Add python.exe to PATH"**.

### Termux gagal install bootstrap

Uninstall total, download ulang APK dari [github.com/termux/termux-app/releases](https://github.com/termux/termux-app/releases), install ulang. Kalau masih gagal berulang kali, pertimbangkan pakai VPS + SSH client (Termius/JuiceSSH) sebagai alternatif yang lebih stabil.

### `pandas`/`numpy` gagal install di Termux

Install lewat `pkg` dulu (`pkg install python-numpy python-pandas`), baru sisanya lewat `pip`.

---

## Batasan & Catatan Jujur

- **`win_probability` bukan probabilitas statistik tervalidasi** — murni rasio sinyal historis sesaat, bukan hasil backtest. Jangan anggap ini jaminan profit.
- **Energy bar bersifat informatif saja** — menunjukkan estimasi sisa waktu tahan posisi vs `MAX_HOLD_MINUTES`, tidak otomatis memaksa close posisi.
- **Trailing stop reference reset kalau `app.py` di-restart** — state "harga tertinggi sejak entry" disimpan di memori, bukan persisten ke file/database.
- **Command input di dashboard tidak 100% seamless** — auto-refresh tetap jalan di background saat kamu mengetik, jadi tampilannya tidak se-presisi TUI yang pakai raw-terminal library khusus.
- Method exchange (`create_order`, `set_leverage`, `fetch_positions`, dll) mengikuti konvensi `ccxt` standar — **selalu cek ulang** kompatibilitasnya dengan versi `ccxt` dan Bitget API terbaru sebelum live trading, terutama setelah update dependency.

---

## Lisensi

Proyek personal — silakan dimodifikasi sesuai kebutuhan kamu sendiri.
