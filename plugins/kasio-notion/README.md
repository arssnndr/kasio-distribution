# KASIO — Notion-backed Financial Tracker for Hermes

Plugin ini bagian dari distribusi **KASIO v2** — pencatat keuangan pribadi via Hermes Agent + Notion DB + AI vision (MiniMax M3).

## Apa ini?

Plugin Python untuk Hermes Agent yang menyediakan **11 atomic tools** untuk:
- Notion DB CRUD (transactions + accounts)
- Indonesian nominal parsing (regex, no LLM)
- Receipt/screenshot vision reading (via MiniMax M3)

Backend DB: Notion API 2025-09-03. Tidak ada local DB, tidak ada sync logic — single source of truth = Notion.

## Requirements

- **Hermes Agent** 0.12+
- **Python** 3.11+
- **httpx** (already in Hermes deps)
- **Notion** integration with API key

## Environment Variables

Tambah ini di `~/.hermes/.env`:

```bash
# Required
NOTION_API_KEY=ntn_xxxxxxxxxxxxxxxxxxxxx
KASIO_TRANSACTIONS_DS_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
KASIO_ACCOUNTS_DS_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# Optional (untuk vision reading)
MINIMAX_API_KEY=sk-cp-xxxxxxxxxxxxxxxxxxxxx
```

### Cara Mendapatkan Notion API Key
1. Buka https://www.notion.so/my-integrations
2. Create new integration → copy "Internal Integration Token"
3. Paste ke `NOTION_API_KEY`

### Cara Mendapatkan Data Source ID
1. Buka DB di Notion
2. Klik "..." → "Connections" → invite integration
3. Lihat URL: `https://www.notion.so/workspace/<DB-ID>?v=...`
4. Untuk API 2025-09-03, butuh **Data Source ID** (bukan Database ID)
5. Klik DB → "..." → "View data source" → copy ID dari URL

## Notion DB Schema (WAJIB match)

### Transactions DB
- `Nama` (title)
- `Angka` (number)
- `Tipe` (select: Pemasukan / Pengeluaran)
- `Kategori` (select: 8 nilai)
- `Tanggal` (date)
- `Catatan` (rich_text)
- `Rekening` (relation → Accounts DB)
- `Transfer Group ID` (rich_text)

### Accounts DB
- `Nama` (title)
- `Saldo Awal` (number, **note: ada spasi**)
- `Status` (select: Aktif / Diarsipkan)
- `Urutan` (number)
- `Ikon` (rich_text)

## 11 Tools

| Tool | Purpose |
|---|---|
| `kasio_list_transactions` | Query transactions |
| `kasio_save_transaction` | Create transaction |
| `kasio_update_transaction` | Edit transaction |
| `kasio_archive_transaction` | Soft-delete |
| `kasio_list_accounts` | List rekening |
| `kasio_save_account` | Add rekening |
| `kasio_update_account` | Edit rekening |
| `kasio_archive_account` | Soft-delete rekening |
| `kasio_parse_nominal` | Parse "35rb", "1.5jt", etc |
| `kasio_read_receipt` | Vision: struk belanja |
| `kasio_read_screenshot` | Vision: mutasi bank/ewallet |

## Usage via Skill

Plugin ini dipakai via skill `kasio` di `~/.hermes/skills/kasio/`. Lihat `SKILL.md` skill untuk workflow lengkap.

Quick examples:
- "catat makan siang 35rb" → wizard 6-step
- "/saldo" → cek saldo semua rekening
- "transfer 100rb dari BCA ke GoPay" → wizard 5-step
- [kirim foto struk] → vision → confirm → save

## Architecture

```
kasio-notion/
├── __init__.py       # register(ctx)
├── plugin.yaml       # manifest
├── client.py         # Notion HTTP client (httpx)
├── tools.py          # 11 tool handlers + schemas
├── parsers.py        # parse_nominal, parse_date, format_rupiah
├── vision.py         # baca_struk, baca_screenshot
└── constants.py      # PROP_MAP, KATEGORI, ENUMS
```

## Notion API Quirks

- API version: `2025-09-03` (di-header `Notion-Version`)
- Query DB: `POST /v1/data_sources/{DS_ID}/query` (bukan `/databases/{ID}/query`)
- Create page: parent `{"type": "data_source_id", "data_source_id": "..."}` (bukan `database_id`)
- SDK `@notionhq/client` v2.x belum support — plugin pakai httpx bypass

## Testing

Plugin di-test via direct Python import (Hermes sandbox pakai importlib manual). Smoke tests:
- ✅ 11 tools registered
- ✅ Parser regex: 35000, 35rb, 1.5jt, 1m, 1.000.000 → correct int
- ✅ List accounts: return 4 rekening (BCA, Cash, Seabank, Gopay)
- ✅ Save + archive roundtrip
- ✅ Transfer: 2 rows linked via transfer_group UUID

## License

MIT
