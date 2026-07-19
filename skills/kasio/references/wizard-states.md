# Wizard State Machine

Diagram state transitions untuk wizard KASIO.

## /catat (6 step)

```
[IDLE]
  │ user: "catat" / "tambah transaksi"
  ▼
[CATAT_STEP_1: Nama]
  │ input: text (non-empty)
  ▼
[CATAT_STEP_2: Nominal]
  │ kasio_parse_nominal → valid integer
  ▼
[CATAT_STEP_3: Rekening]
  │ user pilih dari kasio_list_accounts
  ▼
[CATAT_STEP_4: Tipe]
  │ user pilih: Pemasukan / Pengeluaran
  ▼
[CATAT_STEP_5: Kategori]
  │ user pilih dari 8 fixed kategori
  ▼
[CATAT_STEP_6: Catatan]
  │ skip / tambah (text)
  ▼
[CATAT_PREVIEW]
  │ user: Simpan → save_transaction → IDLE
  │ user: Ubah → kembali ke step 1
  │ user: Batal → IDLE (no save)
```

## /transfer (5 step)

```
[IDLE]
  │ user: "transfer" / "kirim uang"
  ▼
[TRANSFER_STEP_1: Dari]
  │ user pilih rekening A (exclude none)
  ▼
[TRANSFER_STEP_2: Ke]
  │ user pilih rekening B (exclude A)
  ▼
[TRANSFER_STEP_3: Nominal]
  │ kasio_parse_nominal
  ▼
[TRANSFER_STEP_4: Catatan]
  │ skip / tambah (default: "Transfer")
  ▼
[TRANSFER_PREVIEW]
  │ user: Simpan
  │    → generate uuid (transfer_group)
  │    → save_transaction Row A (Pengeluaran, rekening A)
  │    → save_transaction Row B (Pemasukan, rekening B)
  │    → save both IDs ke scratch memory
  │ user: Ubah / Batal → IDLE
```

## Undo

```
[Any]
  │ user: "undo" / "batalkan tadi"
  ▼
[CHECK_LAST_SAVED]
  │ lookup (chat_id, last_saved_transaction) di scratch
  ▼
  ├─ not found → "❌ Tidak ada transaksi yang bisa di-undo."
  │
  ├─ now - saved_at > 30s → "❌ Sudah lebih dari 30 detik, undo tidak berlaku."
  │
  └─ now - saved_at ≤ 30s
     │
     ├─ type='single' → archive_transaction(id) → "✅ Dibatalkan."
     │
     └─ type='transfer_pair' → archive_transaction(id_a) + archive_transaction(id_b) → "✅ Transfer dibatalkan."
```

## Error States

```
[Any State]
  │ tool returns error
  ▼
[ERROR_DISPLAY]
  │ show error message
  │ preserve draft state
  │ user can /batal to reset
```
