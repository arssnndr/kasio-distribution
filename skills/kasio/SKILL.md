---
name: kasio
description: Financial tracker — catat transaksi, cek saldo, transfer antar rekening, baca struk/screenshot bank via vision, undo 30 detik. Pakai plugin `kasio-notion` (10 tools) sebagai backend.
platforms: [linux, macos, windows]
---

# KASIO Workflow

KASIO = pencatat keuangan pribadi via Notion DB. Plugin `kasio-notion` menyediakan 10 atomic tools (`kasio_*`). Skill ini orchestrate tools tsb untuk UX wizard.

## Konsep Dasar

- **Backend**: Plugin `kasio-notion` (10 tools registered di toolset `kasio`).
- **DB schema**: Notion (transactions + accounts). 8 kategori tetap, 2 tipe, 2 status rekening.
- **Multi-user**: Shared account (1 DB dipakai rame-rame). Tidak ada `user_id`. Semua transaksi campur.
- **Transfer**: 2 row di transactions DB, linked via `transfer_group` UUID. Row A = Pengeluaran (rekening asal), Row B = Pemasukan (rekening tujuan).
- **Soft-delete**: Archive (bukan delete). Visible kalau `include_archived=true`. Undo window 30 detik per transaksi terakhir.
- **Currency**: Rupiah. Format `formatRupiah(n)` → "Rp 35.000".

## Enums (harus match Notion DB schema)

### Tipe (2)
- `Pemasukan` 🟢
- `Pengeluaran` 🔴

### Kategori (8)
- `Makanan & Minuman` 🍽️
- `Transportasi` 🚌
- `Belanja` 🛒
- `Tagihan` 💡
- `Kesehatan` 💊
- `Hiburan` 🎮
- `Pendapatan` 💰
- `Lainnya` 📦

### Status Rekening (2)
- `Aktif`
- `Diarsipkan`

## 10 Tools Reference (pakai persis nama ini)

| Tool | Purpose | Key params |
|---|---|---|
| `kasio_list_transactions` | Query transactions | start_date?, end_date?, rekening_id?, kategori?, transfer_group?, limit? |
| `kasio_save_transaction` | Create transaction | nama, jumlah, tipe, kategori (+ tanggal, catatan, rekening_id, transfer_group optional) |
| `kasio_update_transaction` | Edit fields | page_id, updates: {nama?, jumlah?, tipe?, ...} |
| `kasio_archive_transaction` | Soft-delete | page_id |
| `kasio_list_accounts` | List rekening | include_archived? |
| `kasio_save_account` | Tambah rekening | nama (+ saldo_awal, urutan, ikon optional) |
| `kasio_update_account` | Edit rekening | page_id, updates: {...} |
| `kasio_archive_account` | Soft-delete rekening | page_id |
| `kasio_parse_nominal` | Parse string nominal | input |
| `kasio_read_receipt` | Vision: struk | image_b64, mime_type? |
| `kasio_read_screenshot` | Vision: mutasi bank | image_b64, mime_type? |

## State Management (Wizard)

Wizard butuh multi-step state per chat. Simpan state di conversation memory (in-session scratch).

```
kasio_draft = {
  mode: 'catat' | 'transfer' | 'edit' | 'rekening_tambah',
  step: 1..6,
  data: {
    nama?, jumlah?, tipe?, kategori?, tanggal?, catatan?,
    rekening_id?, rekening_nama?, rekening_icon?,
    dari_rekening?, ke_rekening?,
  },
  created_at: <iso timestamp>,
  last_saved_transaction: { id, saved_at, type: 'single'|'transfer_pair', ids: [id_a, id_b] },
}
```

**Key**: state per `chat_id`. Reset kalau user `/batal` atau step idle >5 menit.

## Command → Action Mapping

| User input | Action |
|---|---|
| `catat`, `/catat`, "tambah transaksi", "expense" | Wizard catat (6 step) |
| `transfer`, `/transfer`, "kirim uang" | Wizard transfer (5 step) |
| `saldo`, `/saldo`, "cek saldo", "balance" | List rekening + saldo komputasi |
| `laporan [periode]`, "pengeluaran bulan ini" | Aggregasi per kategori/tipe |
| `cari [keyword]` | Filter transactions by nama match |
| `rekening`, `/rekening` | List + manage rekening |
| `undo`, "batalkan tadi" | Archive transaksi terakhir (<30s) |
| `/batal`, `batal`, "reset" | Reset wizard state |
| Free text nominal `35rb`, `1.5jt`, dst | Auto-parse via `kasio_parse_nominal` |
| Photo (struk / mutasi bank) | Vision flow |

## Wizard /catat (6 Step)

Step dipandu inline. Tiap step: prompt → input user → validasi → next step → save ke `kasio_draft`.

```
Step 1: Nama (text input)
  → "📝 Step 1/6 — Nama transaksi. Ketik nama (misal: 'Makan siang')"

Step 2: Nominal (text input)
  → "💰 Step 2/6 — Nominal. Format: 35000, 35rb, 1.5jt"
  → Panggil kasio_parse_nominal untuk validasi
  → Kalau invalid, tanya ulang

Step 3: Rekening (inline keyboard)
  → Ambil dari kasio_list_accounts
  → Tampilkan sebagai opsi dengan icon + nama
  → User pilih atau "Tambah rekening baru"

Step 4: Tipe
  → "🟢 Pemasukan / 🔴 Pengeluaran"

Step 5: Kategori
  → 8 opsi (Lainnya sebagai default aman)

Step 6: Catatan (opsional)
  → "Skip / Tambah"
  → Default kosong kalau skip

Preview + Konfirmasi
  → Format dengan formatRupiah + icon
  → Tampilkan saldo sebelum transaksi kalau bisa
  → "✅ Simpan / ✏️ Ubah / ❌ Batal"
```

### Save flow
On confirm:
1. Generate timestamp = `now()`
2. Panggil `kasio_save_transaction(...)` dengan field dari `kasio_draft.data`
3. Save returned `{id, saved_at}` ke `kasio_draft.last_saved_transaction` untuk undo
4. Tampilkan konfirmasi: "✅ Tersimpan. ID: <id>. Bisa di-undo dalam 30 detik dengan bilang 'undo'."
5. Reset `kasio_draft.mode='catat'`, kosongkan data

## Wizard /transfer (5 Step)

```
Step 1: Dari rekening
  → Inline keyboard, exclude rekening yg sama (safety)

Step 2: Ke rekening
  → Sama, exclude rekening asal

Step 3: Nominal
  → Parse via kasio_parse_nominal

Step 4: Catatan (optional, default "Transfer")

Preview + Konfirmasi
  → "📤 Dari: BCA → 📥 Ke: Cash | 💰 Rp 50.000 | 📌 Transfer"
  → "Akan dibuat 2 row linked via transfer_group"
```

### Save flow
On confirm:
1. Generate `tg_uuid = kasio.parsers.generate_uuid()` (uuid4)
2. **Parallel save** 2 rows dengan transfer_group sama:
   - Row A: tipe=Pengeluaran, rekening_id=<dari>, transfer_group=tg_uuid
   - Row B: tipe=Pemasukan, rekening_id=<ke>, transfer_group=tg_uuid
3. Save kedua ID ke `last_saved_transaction` (type='transfer_pair', ids=[id_a, id_b])
4. Tampilkan konfirmasi + warning tentang undo: "Bisa undo dalam 30 detik."

## Undo Pattern

Setiap `kasio_save_transaction` success → catat `(chat_id, transaction_id, saved_at_iso)` ke in-session scratch memory.

User bilang "undo" atau "batalkan tadi":
1. Cari transaction_id terakhir untuk chat_id di scratch memory.
2. Compute `delta = now - saved_at`.
3. **Kalau delta > 30 detik**: tolak dengan pesan "❌ Sudah lebih dari 30 detik, undo tidak berlaku. Hapus manual di Notion kalau perlu."
4. **Kalau delta ≤ 30 detik**:
   - Kalau `type='single'`: panggil `kasio_archive_transaction(id)`.
   - Kalau `type='transfer_pair'`: panggil `kasio_archive_transaction(id_a)` + `kasio_archive_transaction(id_b)` (parallel).
5. Konfirmasi: "✅ Transaksi dibatalkan." atau "✅ Transfer dibatalkan (2 row archived)."
6. Hapus entry dari scratch memory.

## Saldo Komputasi

Saldo per rekening = `saldo_awal + Σ(pemasukan ke rekening) - Σ(pengeluaran dari rekening)` dari semua transaksi non-archived.

**Computed on-the-fly**, tidak disimpan sebagai field. Single source of truth = transactions DB.

```python
def compute_saldo(accounts, transactions):
    result = []
    for acc in accounts:
        saldo = acc.saldo_awal
        for tx in transactions:
            if tx.rekening_id == acc.id:
                if tx.tipe == "Pemasukan":
                    saldo += tx.jumlah
                elif tx.tipe == "Pengeluaran":
                    saldo -= tx.jumlah
        result.append({acc.nama, acc.saldo, acc.icon})
    return result
```

Untuk response, format:
```
💼 Saldo semua rekening:

🏦 BCA        Rp 33.456
💳 Cash       Rp 187.000
🏦 Seabank    Rp 236.952
💰 Gopay      Rp 427

Total: Rp 457.835
```

## Vision Flow (Foto Struk / Screenshot Bank)

User kirim foto:
1. Detect image attachment (Telegram/WhatsApp auto, CLI/Desktop manual upload).
2. Encode ke base64.
3. **Decision: struk atau mutasi bank?**
   - Default heuristic: kalau ada kata "mutasi" / "transaksi" / "bank" / nama bank di caption → screenshot.
   - Atau tanya user "📸 Struk belanja atau screenshot mutasi bank?"
4. Panggil tool yang sesuai:
   - `kasio_read_receipt(image_b64, mime_type)` untuk struk
   - `kasio_read_screenshot(image_b64, mime_type)` untuk mutasi
5. Confidence gate:
   - **≥ 0.8**: tampilkan preview extracted fields, minta konfirmasi → save via wizard mini (skip langkah yang sudah ada di hasil vision).
   - **< 0.8**: warning "⚠️ AI kurang yakin (confidence: 0.6). Mohon cek manual sebelum simpan." Tampilkan field apa yang ambigu. Tetap bisa confirm.

## Response Formatting

Gunakan helper `formatRupiah(n)` (di-handle internal, jangan expose ke user sebagai math):
- `35000` → "Rp 35.000"
- `1500000` → "Rp 1.500.000"
- `0` → "Rp 0"

Format row transaksi:
```
🔴 Makan siang
   💼 BCA | 💰 Rp 35.000 | 🍽️ Makanan & Minuman
   📅 2026-07-19
   📝 Meeting dengan client
```

Format konfirmasi preview (sebelum save):
```
📋 Konfirmasi Transaksi

📝 Nama: Makan siang
💰 Jumlah: Rp 35.000
💼 Rekening: 🏦 BCA
📂 Tipe: 🔴 Pengeluaran
🏷️ Kategori: 🍽️ Makanan & Minuman
📅 Tanggal: 2026-07-19
📌 Catatan: Meeting dengan client
```

## Boundaries / Out of Scope

- **Multi-user isolation**: TIDAK. Shared account by design.
- **Free-text LLM query**: TIDAK. Query via command eksplisit atau wizard.
- **Auto-suggest kategori dari nama**: TIDAK. User harus pilih manual (strict & guided design).
- **Auto-categorize recurring**: future enhancement.
- **Edit transaksi lama via wizard**: pakai command eksplisit, tidak auto-suggest.
- **Export CSV**: out of scope, bisa pakai Notion UI langsung.
- **Multi-currency**: TIDAK. Rupiah only.
- **Investment tracking**: TIDAK. Hanya income/expense/transfer.

## Error Handling

Tiap tool call bisa return error. Pattern:
```json
{"error": "Save transaction failed: HTTPStatusError: 400 ..."}
```

Kalau dapat error:
- Notion 401/403 → "❌ Auth Notion gagal. Cek NOTION_API_KEY di .env."
- Notion 404 → "❌ Page/DB tidak ditemukan. Schema mungkin berubah?"
- Validation error → "❌ <field>: <reason>. Coba lagi."
- Timeout → "❌ Request timeout. Notion mungkin sibuk, coba lagi."

Network error → jangan kill wizard state. User bisa coba lagi.

## Catatan Penting

- Selalu panggil `kasio_list_accounts` dulu sebelum wizard step 3 (rekening), supaya list fresh.
- Selalu panggil `kasio_list_transactions` dengan filter archived=false (default) untuk saldo & laporan.
- Backup rutin ke CSV masih bisa pakai Notion UI (sama seperti sebelumnya).
- Plugin path: `~/.hermes/plugins/kasio-notion/`. Update via edit file + restart Hermes session.
