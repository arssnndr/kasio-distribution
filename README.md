# KASIO Distribution for Hermes

Pencatat keuangan pribadi via Hermes Agent + Notion DB + AI vision. Bundle plugin + skill untuk install plug-and-play.

## Quick Install

**Cara paling simple — one-shot script:**

```bash
# macOS/Linux/Git Bash:
curl -fsSL https://raw.githubusercontent.com/arissunandar/kasio-distribution/main/install-kasio.sh | bash

# Windows PowerShell:
irm https://raw.githubusercontent.com/arissunandar/kasio-distribution/main/install-kasio.ps1 | iex
```

Script akan:
1. Verify Hermes ter-install
2. Install distribution via `hermes profile install`
3. Prompt for 3 env values (NOTION_API_KEY, 2 DS IDs)
4. Activate profile `kasio`
5. Verify plugin + skill loaded

**Cara manual — 2 commands:**

```bash
# 1. Install
hermes profile install github.com/arissunandar/kasio-distribution

# 2. Activate & setup
hermes profile activate kasio
nano ~/.hermes/profiles/kasio/.env  # Isi 4 values
```

**Total: ~2 menit** (setelah Hermes core ter-install di mesin tsb).

## Yang Di-install

Distribution ini bundle:
- **Plugin**: `~/.hermes/plugins/kasio-notion/` (11 atomic tools)
- **Skill**: `~/.hermes/skills/kasio/` (workflow doc)
- **Profile**: `~/.hermes/profiles/kasio/` (env + config)

Setelah install, plugin dan skill langsung aktif di profile `kasio`.

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

## Usage

Di chat Hermes (Telegram / CLI / Desktop):

```
catat makan siang 35rb          → wizard 6-step catat transaksi
/saldo                          → cek saldo semua rekening
transfer 100rb dari BCA ke Cash → wizard 5-step transfer
[kirim foto struk]              → vision auto-extract
undo                            → batalkan transaksi terakhir (30s window)
```

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
│       ├── __init__.py
│       ├── plugin.yaml
│       ├── client.py                # Notion HTTP client
│       ├── tools.py                 # 11 tool handlers
│       ├── parsers.py               # parse_nominal, parse_date
│       ├── vision.py                # receipt + screenshot reader
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

## Update

```bash
hermes profile update kasio
```

Akan re-pull dari GitHub, replace `distribution_owned` files (plugins + skills + manifest). User-owned (memories, sessions, auth.json) **tidak disentuh**.

## Uninstall

```bash
hermes profile remove kasio
```

## License

MIT
