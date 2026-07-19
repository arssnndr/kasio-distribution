# Notion Schema Reference untuk KASIO Plugin

Dokumentasi schema DB yang di-assume plugin `kasio-notion`. **WAJIB match** dengan DB Notion yang dituju.

## Transactions DB

| Property | Type | Required | Notes |
|---|---|---|---|
| `Nama` | title | ✅ | Nama transaksi |
| `Angka` | number | ✅ | Nominal Rupiah (integer) |
| `Tipe` | select | ✅ | `Pemasukan` / `Pengeluaran` |
| `Kategori` | select | ✅ | 8 nilai tetap |
| `Tanggal` | date | ✅ | YYYY-MM-DD |
| `Catatan` | rich_text | ❌ | Optional |
| `Rekening` | relation | ❌ | → Accounts DB |
| `Transfer Group ID` | rich_text | ❌ | UUID untuk linking transfer pair |

## Accounts DB

| Property | Type | Required | Notes |
|---|---|---|---|
| `Nama` | title | ✅ | Nama rekening |
| `Saldo Awal` | number | ✅ | **Note: ada spasi!** |
| `Status` | select | ✅ | `Aktif` / `Diarsipkan` |
| `Urutan` | number | ❌ | Sort order (lower = top) |
| `Ikon` | rich_text | ❌ | Emoji icon |

## Environment Variables

| Var | Required | Source |
|---|---|---|
| `NOTION_API_KEY` | ✅ | https://www.notion.so/my-integrations |
| `KASIO_TRANSACTIONS_DS_ID` | ✅ | DS ID dari URL Notion DB |
| `KASIO_ACCOUNTS_DS_ID` | ✅ | DS ID dari URL Notion DB |
| `MINIMAX_API_KEY` | ❌ | Optional — untuk vision reading |

## API Quirks (Notion 2025-09-03)

- **Query DB**: pakai `POST /v1/data_sources/{DS_ID}/query` (BUKAN `/databases/{ID}/query` lagi)
- **Create page**: parent harus `{"type": "data_source_id", "data_source_id": "..."}` (BUKAN `{"database_id": "..."}`)
- **Headers wajib**: `Notion-Version: 2025-09-03`
- SDK `@notionhq/client` v2.x belum support — plugin pakai httpx bypass

## Setting Up DB Baru

Untuk user baru yang mau setup DB dari nol:

1. Create 2 Notion DB di workspace
2. Add properties di atas (case-sensitive!)
3. Invite integration "Hermes Api Key" ke kedua DB
4. Ambil Data Source ID dari URL DB
5. Set env vars di `~/.hermes/.env`
6. Test plugin load: `hermes plugins list | grep kasio`
