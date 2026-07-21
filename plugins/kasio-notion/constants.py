"""Constants for kasio-notion plugin.

Hardcoded schema mapping + enums. MUST match Notion DB schema exactly.
"""

# ============================================================================
# Notion Property Maps
# ============================================================================

# Transactions DB property names (case-sensitive, matching Notion schema)
TX_PROP = {
    "nama": "Nama",                # title
    "jumlah": "Angka",              # number
    "tipe": "Tipe",                 # select
    "kategori": "Kategori",         # select
    "tanggal": "Tanggal",           # date
    "catatan": "Catatan",           # rich_text
    "rekening": "Rekening",         # relation -> Accounts DB
    "transfer_group": "Transfer Group ID",  # rich_text
}

# Accounts DB property names
ACCT_PROP = {
    "nama": "Nama",            # title
    "saldo_awal": "Saldo Awal",  # number (note: spasi)
    "status": "Status",        # select
    "urutan": "Urutan",        # number
    "ikon": "Ikon",            # rich_text
    "nomor_rekening": "Nomor Rekening",  # rich_text
}

# ============================================================================
# Enums
# ============================================================================

TIPE_PEMASUKAN = "Pemasukan"
TIPE_PENGELUARAN = "Pengeluaran"
VALID_TIPE = {TIPE_PEMASUKAN, TIPE_PENGELUARAN}

KATEGORI_LIST = [
    "Makanan & Minuman",
    "Transportasi",
    "Belanja",
    "Tagihan",
    "Kesehatan",
    "Hiburan",
    "Pendapatan",
    "Lainnya",
]

KATEGORI_ICON = {
    "Makanan & Minuman": "🍽️",
    "Transportasi": "🚌",
    "Belanja": "🛒",
    "Tagihan": "💡",
    "Kesehatan": "💊",
    "Hiburan": "🎮",
    "Pendapatan": "💰",
    "Lainnya": "📦",
}

TIPE_ICON = {
    TIPE_PEMASUKAN: "🟢",
    TIPE_PENGELUARAN: "🔴",
}

REKENING_STATUS_AKTIF = "Aktif"
REKENING_STATUS_DIARSIPKAN = "Diarsipkan"


def is_valid_kategori(s: str) -> bool:
    """Check if string is a valid kategori. Returns False for unhashable types."""
    try:
        return s in KATEGORI_LIST
    except TypeError:
        return False


def is_valid_tipe(s: str) -> bool:
    """Check if string is a valid tipe. Returns False for unhashable types."""
    try:
        return s in VALID_TIPE
    except TypeError:
        return False

# ============================================================================
# API Configuration
# ============================================================================

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
VISION_API_BASE = "https://api.minimax.io/v1"
VISION_MODEL = "MiniMax-M3"

# Vision confidence threshold — below this, ask user to confirm manually
VISION_CONFIDENCE_THRESHOLD = 0.8

# Undo window (seconds) — after this, can't undo
UNDO_WINDOW_SECONDS = 30
