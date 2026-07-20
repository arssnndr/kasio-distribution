# KASIO Distribution for Hermes

Pencatat keuangan pribadi via **Hermes Agent** + **Notion DB** + **AI vision (MiniMax M3)**. Bundle plugin + skill untuk install plug-and-play.

> **Migrasi dari KASIO v2 (Telegram bot Grammy/Node)** ke **KASIO v3 (Hermes native)** — selesai 19 Juli 2026. Plan lengkap: [Notion page](https://app.notion.com/p/KASIO-Migration-Plan-to-Hermes-3a2ac5534df081638559f699f3c57c45).

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

Di chat Hermes (Telegram / CLI / Desktop), bilang:

```
catat makan siang 35rb            → wizard 6-step catat transaksi
/saldo                            → cek saldo semua rekening
transfer 100rb dari BCA ke Cash   → wizard 5-step transfer
laporan                           → aggregation by kategori
cari kopi                         → search by keyword
[kirim foto struk/screenshot]     → vision auto-extract
undo                              → archive transaksi terakhir (30s window)
```

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
