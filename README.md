# KASIO Distribution for Hermes

Pencatat keuangan pribadi via **Hermes Agent** + **Notion DB** + **AI vision (MiniMax M3)**. Bundle plugin + skill untuk install plug-and-play.

> **Migrasi dari KASIO v2 (Telegram bot Grammy/Node)** ke **KASIO v3 (Hermes native)** — selesai 19 Juli 2026. Plan lengkap: [Notion page](https://app.notion.com/p/KASIO-Migration-Plan-to-Hermes-3a2ac5534df081638559f699f3c57c45).

> ⚠️ **Penting untuk user Telegram:** Hermes Telegram gateway saat ini **tidak handle slash command** (`/saldo`, `/catat`, dll). Pakai **natural language** aja — lihat [Usage](#usage). Slash command hanya jalan di CLI/Desktop.

## Quick Install

**Cara paling simple — one-shot script:**

```bash
# macOS / Linux / Git Bash:
curl -fsSL https://raw.githubusercontent.com/arssnndr/kasio-distribution/main/install-kasio.sh | bash

# Windows PowerShell:
irm https://raw.githubusercontent.com/arssnndr/kasio-distribution/main/install-kasio.ps1 | iex
```

Script akan:
1. Verify Hermes ter-install
2. Install distribution via `hermes profile install`
3. Prompt for 3 env values (`NOTION_API_KEY`, `KASIO_TRANSACTIONS_DS_ID`, `KASIO_ACCOUNTS_DS_ID`) + 1 optional (`MINIMAX_API_KEY`)
4. Activate profile `kasio`
5. Verify plugin + skill loaded

**Cara manual — 2 commands:**

```bash
# 1. Install distribution via git protocol (works untuk public + private repo)
hermes profile install github.com/arssnndr/kasio-distribution

# 2. Activate profile & setup env
hermes profile activate kasio
nano ~/.hermes/profiles/kasio/.env  # Isi 4 values
```

**Total: ~2 menit** (setelah Hermes core ter-install di mesin tsb).

## Yang Di-install

Distribution ini bundle:

| Komponen | Path | Isi |
|---|---|---|
| **Plugin** | `~/.hermes/plugins/kasio-notion/` | 11 atomic tools (Notion CRUD + parser + vision) |
| **Skill** | `~/.hermes/skills/kasio/` | Workflow doc (wizard 6-step catat, 5-step transfer, undo 30s) |
| **Profile** | `~/.hermes/profiles/kasio/` | Env + config |

Setelah install, plugin dan skill langsung aktif di profile `kasio`.

## Usage

KASIO v3 (Hermes native) merespon **natural language** melalui AI agent — bukan slash command.

**Cara paling natural (recommended):**

```
catat makan siang 35rb
transfer 100rb dari BCA ke Cash
saldo
laporan minggu ini
cari kopi
undo
batal
```

Bebas bahasa informal: `gw sarpan 30k cash`, `cek duit`, `gaji masuk 5jt bca`, dll — AI akan parse.

### Contoh lengkap wizard catat (6-step)

```
You:  catat sarapan 30000
Bot:  📝 Step 1/6 — Nama: Sarapan           ✓
      💰 Step 2/6 — Nominal: Rp 30.000      ✓
      💼 Step 3/6 — Pilih rekening:
          1. 🏦 BCA (Rp 33.456)
          2. 💵 Cash (Rp 187.000)
          3. 📱 Gopay (Rp 427)
          4. 🏦 Seabank (Rp 236.952)
You:  2
Bot:  🔴 Step 4/6 — Tipe: Pengeluaran      ✓
      🍽️ Step 5/6 — Kategori: ? (1-8)
You:  1
Bot:  📌 Step 6/6 — Catatan (skip dengan "skip"):
You:  skip
Bot:  ✅ Tersimpan. ID: xxx. Bisa di-undo dalam 30s.
```

### Kenapa bukan `/saldo`?

Di Telegram, slash command (`/saldo`, `/catat`) **tidak diproses** oleh Hermes gateway — user akan dapat error:

> `/saldo: Unknown command. Type /commands to see what's available, or resend without the leading slash to send as a regular message.`

Slash command masih jalan kalau kamu pakai:
- **CLI**: `hermes chat "saldo"` atau langsung ketik `saldo` di interactive mode
- **Desktop app**: tinggal chat biasa tanpa `/`

Solusinya cukup **ketik tanpa leading slash** — AI agent akan tangkap sebagai natural language dan trigger tool yang sesuai.

### Command reference (natural language)

| Intent | Contoh | Tool yang dipanggil |
|---|---|---|
| Catat transaksi | `catat kopi 25rb` / `beli bakso 15rb cash` | `kasio_save_transaction` (wizard 6-step) |
| Transfer | `kirim 100rb dari BCA ke Cash` | 2x `kasio_save_transaction` (linked via transfer_group) |
| Cek saldo | `saldo` / `cek duit` / `balance` | `kasio_list_accounts` + `kasio_list_transactions` |
| Laporan | `laporan` / `pengeluaran minggu ini` / `laporan juni` | aggregasi dari `kasio_list_transactions` |
| Cari transaksi | `cari kopi` / `history grab` | `kasio_list_transactions` filter by nama |
| Undo | `undo` / `batalkan tadi` | `kasio_archive_transaction` (≤30s) |
| Batal wizard | `batal` / `reset` | reset wizard state |
| Vision (foto struk) | kirim foto struk | `kasio_read_receipt` → preview → konfirmasi |
| Vision (mutasi bank) | kirim foto mutasi + caption "mutasi" | `kasio_read_screenshot` → preview → konfirmasi |

### Nominal parser

Plugin `kasio_parse_nominal` support format:
- `35000` → 35.000
- `35rb` / `35k` → 35.000
- `1.5jt` / `1.5juta` → 1.500.000
- `1,2 juta` → 1.200.000
- `2.5m` → 2.500.000

### Undo

Setiap `kasio_save_transaction` otomatis dicatat di session memory. Bilang `undo` dalam **30 detik** setelah save → row di-archive (soft-delete). Lebih dari 30s: tolak, harus hapus manual di Notion.

### Transfer link

Transfer = 2 row di Notion DB, linked via UUID `transfer_group`. `undo` bakal archive 2 row sekaligus (atomic).

## ⏰ Cron Reminder (Auto)

Plugin ini include 2 cron jobs (set setelah install pertama):

| Cron | Schedule | Isi |
|---|---|---|
| `kasio-daily-reminder` | Setiap hari jam 21:00 WIB | Recap pengeluaran hari ini |
| `kasio-weekly-summary` | Setiap Minggu jam 20:00 WIB | Breakdown per kategori + net income |

Cek: `hermes cron list | grep kasio`

## Setup Notion DB

Plugin ini assume kamu punya 2 Notion DB dengan schema tertentu. 2 cara:

### Cara 1: Pakai Template (recommended, coming soon)
Duplicate template Notion publik (akan tersedia) → punya DB dengan schema benar → ambil DS ID → set env.

### Cara 2: Manual Setup
Buat 2 Notion DB dengan properties berikut (case-sensitive!):

**Transactions DB:**
- `Nama` (Title)
- `Angka` (Number)
- `Tipe` (Select: Pemasukan, Pengeluaran)
- `Kategori` (Select: 8 nilai — lihat di bawah)
- `Tanggal` (Date)
- `Catatan` (Rich text)
- `Rekening` (Relation → Accounts DB)
- `Transfer Group ID` (Rich text)

**Accounts DB:**
- `Nama` (Title)
- `Saldo Awal` (Number, **note: ada spasi!**)
- `Status` (Select: Aktif, Diarsipkan)
- `Urutan` (Number)
- `Ikon` (Rich text)

### Kategori values (untuk Transactions DB)
```
Makanan & Minuman
Transportasi
Belanja
Tagihan
Kesehatan
Hiburan
Pendapatan
Lainnya
```

## Cara Ambil Data Source ID

1. Buka DB di Notion
2. Klik "..." → "Connections" → tambah integration "Hermes Api Key" (atau nama integration kamu)
3. Klik DB → "..." → "View data source"
4. Copy ID dari URL (32 char hex dengan dash): `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`

> ⚠️ **Catatan:** Untuk Notion API version `2025-09-03`, butuh **data source ID** (bukan database ID). Cara ambilnya via "View data source" di step 3.

## Architecture

```
kasio-distribution/                  ← This repo
├── distribution.yaml                ← Profile manifest
├── .env.template                    ← Env template
├── install-kasio.sh                 ← One-shot installer (Unix)
├── install-kasio.ps1                ← One-shot installer (Windows)
├── README.md                        ← This file
│
├── plugins/
│   └── kasio-notion/                ← Plugin code
│       ├── __init__.py              # register(ctx) — masukin 11 tools
│       ├── plugin.yaml              # manifest (name, version, requires_env)
│       ├── client.py                # Notion HTTP client (httpx)
│       ├── tools.py                 # 11 tool handlers + JSON schemas
│       ├── parsers.py               # parse_nominal, parse_date, format_rupiah
│       ├── vision.py                # read_receipt, read_screenshot
│       ├── constants.py             # PROP_MAP + ENUMS
│       └── README.md
│
└── skills/
    └── kasio/                       ← Skill workflow doc
        ├── SKILL.md                 # Main workflow
        └── references/
            ├── notion-schema.md     # Notion DB reference
            └── wizard-states.md     # State machine diagrams
```

## 11 Tools Reference

| Tool | Purpose |
|---|---|
| `kasio_list_transactions` | Query transactions (filter by date, rekening, kategori, transfer_group) |
| `kasio_save_transaction` | Create transaction |
| `kasio_update_transaction` | Edit fields |
| `kasio_archive_transaction` | Soft-delete (untuk undo) |
| `kasio_list_accounts` | List rekening |
| `kasio_save_account` | Add rekening baru |
| `kasio_update_account` | Edit rekening |
| `kasio_archive_account` | Soft-delete rekening |
| `kasio_parse_nominal` | Parse "35rb", "1.5jt", "1.2 juta" → integer |
| `kasio_read_receipt` | Vision: struk belanja → merchant, total, kategori |
| `kasio_read_screenshot` | Vision: mutasi bank → transfer/payment details |

## Notion API Quirks

Plugin pakai httpx bypass karena `@notionhq/client` SDK v2.x belum support:

- **Header wajib**: `Notion-Version: 2025-09-03`
- **Query DB**: `POST /v1/data_sources/{DS_ID}/query` (bukan `/databases/{ID}/query`)
- **Create page**: parent `{"type": "data_source_id", "data_source_id": "..."}` (bukan `database_id`)

## Update

```bash
hermes profile update kasio
```

Akan re-pull dari GitHub, replace `distribution_owned` files (plugins + skills + manifest). User-owned (memories, sessions, auth.json) **tidak disentuh**.

## Uninstall

```bash
hermes profile remove kasio
```

## Migrasi dari KASIO v2 (Telegram bot)

Kalau sebelumnya pakai KASIO v2 (Grammy/Node bot), sekarang KASIO v3 (Hermes native):

- **DB schema**: Sama persis. Data di Notion tidak perlu migrasi.
- **Bot lama**: Di-archive di `kasio-bot.deprecated-20260719\` (backup 30 hari).
- **Perbedaan UX**: Tidak perlu buka Telegram @KasioRissBot — sekarang langsung chat di Telegram/CLI/Desktop apapun yang Hermes konek.
- **Undo**: Workflow sama (30s window), pakai command `undo` bukan tombol inline.

Plan migrasi lengkap: [Notion page](https://app.notion.com/p/KASIO-Migration-Plan-to-Hermes-3a2ac5534df081638559f699f3c57c45).

## License

MIT
