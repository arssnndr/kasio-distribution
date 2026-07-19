"""Tool handlers + schemas for kasio-notion plugin.

10 tools total. Each has:
  - SCHEMA (JSON schema for LLM)
  - handler (sync function returning string result)

Handlers are sync because they're stateless; Notion API calls are blocking.
"""

from __future__ import annotations
import json
import base64
from typing import Any
from .client import NotionClient
from .vision import VisionAPI
from . import parsers, constants

# Lazy clients (avoid init if env not ready)
_notion: NotionClient | None = None
_vision: VisionAPI | None = None


def get_notion() -> NotionClient:
    global _notion
    if _notion is None:
        _notion = NotionClient()
    return _notion


def get_vision() -> VisionAPI | None:
    """Vision is optional — return None if MINIMAX_API_KEY not set."""
    global _vision
    if _vision is None:
        try:
            _vision = VisionAPI()
        except RuntimeError:
            return None
    return _vision


def _to_json(obj: Any) -> str:
    """JSON-serialize result for LLM consumption."""
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


# ============================================================================
# Tool 1: kasio_list_transactions
# ============================================================================

SCHEMA_LIST_TRANSACTIONS = {
    "type": "function",
    "function": {
        "name": "kasio_list_transactions",
        "description": "List transaksi dari Notion DB. Bisa filter by date range, rekening, kategori, transfer group.",
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date YYYY-MM-DD (inclusive)"},
                "end_date": {"type": "string", "description": "End date YYYY-MM-DD (inclusive)"},
                "rekening_id": {"type": "string", "description": "Filter by Notion page ID rekening"},
                "kategori": {"type": "string", "description": "Filter by kategori"},
                "transfer_group": {"type": "string", "description": "Filter by Transfer Group ID (untuk lihat 2 row transfer)"},
                "include_archived": {"type": "boolean", "description": "Include soft-deleted (default false)", "default": False},
                "limit": {"type": "integer", "description": "Max rows (default 100, max 100 per page)", "default": 100},
            },
        },
    },
}


def handle_list_transactions(args: dict) -> str:
    try:
        notion = get_notion()
        accounts = notion.list_accounts()
        account_map = {a["id"]: a for a in accounts}
        txs = notion.list_transactions(
            start_date=args.get("start_date"),
            end_date=args.get("end_date"),
            rekening_id=args.get("rekening_id"),
            kategori=args.get("kategori"),
            transfer_group=args.get("transfer_group"),
            include_archived=args.get("include_archived", False),
            limit=args.get("limit", 100),
        )
        # Hydrate with rekening info
        for t in txs:
            if t.get("rekening_id") and t["rekening_id"] in account_map:
                acct = account_map[t["rekening_id"]]
                t["rekening"] = {"id": t["rekening_id"], "nama": acct["nama"], "icon": acct.get("ikon", "")}
        return _to_json({"transactions": txs, "count": len(txs)})
    except Exception as e:
        return _err(f"List transactions failed: {type(e).__name__}: {e}")


# ============================================================================
# Tool 2: kasio_save_transaction
# ============================================================================

SCHEMA_SAVE_TRANSACTION = {
    "type": "function",
    "function": {
        "name": "kasio_save_transaction",
        "description": "Simpan transaksi baru ke Notion DB. Untuk transfer, panggil 2x dengan transfer_group UUID sama.",
        "parameters": {
            "type": "object",
            "properties": {
                "nama": {"type": "string", "description": "Nama transaksi (misal: 'Makan siang')"},
                "jumlah": {"type": "number", "description": "Nominal dalam Rupiah (integer)"},
                "tipe": {"type": "string", "enum": ["Pemasukan", "Pengeluaran"], "description": "Tipe transaksi"},
                "kategori": {"type": "string", "description": "Salah satu dari 8 kategori"},
                "tanggal": {"type": "string", "description": "YYYY-MM-DD (default: hari ini)"},
                "catatan": {"type": "string", "description": "Catatan opsional"},
                "rekening_id": {"type": "string", "description": "Notion page ID rekening"},
                "transfer_group": {"type": "string", "description": "UUID linking 2 row transfer"},
            },
            "required": ["nama", "jumlah", "tipe", "kategori"],
        },
    },
}


def handle_save_transaction(args: dict) -> str:
    try:
        # Validate tipe & kategori
        if not constants.is_valid_tipe(args.get("tipe", "")):
            return _err(f"Invalid tipe: {args.get('tipe')}. Must be one of {constants.VALID_TIPE}")
        if not constants.is_valid_kategori(args.get("kategori", "")):
            return _err(f"Invalid kategori: {args.get('kategori')}. Must be one of {constants.KATEGORI_LIST}")
        notion = get_notion()
        tx = notion.save_transaction(
            nama=args["nama"],
            jumlah=args["jumlah"],
            tipe=args["tipe"],
            kategori=args["kategori"],
            tanggal=args.get("tanggal"),
            catatan=args.get("catatan", ""),
            rekening_id=args.get("rekening_id"),
            transfer_group=args.get("transfer_group"),
        )
        return _to_json({"saved": True, "transaction": tx})
    except Exception as e:
        return _err(f"Save transaction failed: {type(e).__name__}: {e}")


# ============================================================================
# Tool 3: kasio_update_transaction
# ============================================================================

SCHEMA_UPDATE_TRANSACTION = {
    "type": "function",
    "function": {
        "name": "kasio_update_transaction",
        "description": "Update field transaksi yang sudah ada. Hanya field yang disebut di updates yang diubah.",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Notion page ID transaksi"},
                "updates": {
                    "type": "object",
                    "description": "Field yang mau di-update. Keys: nama, jumlah, tipe, kategori, tanggal, catatan, rekening_id, transfer_group",
                    "properties": {
                        "nama": {"type": "string"},
                        "jumlah": {"type": "number"},
                        "tipe": {"type": "string", "enum": ["Pemasukan", "Pengeluaran"]},
                        "kategori": {"type": "string"},
                        "tanggal": {"type": "string"},
                        "catatan": {"type": "string"},
                        "rekening_id": {"type": "string"},
                        "transfer_group": {"type": "string"},
                    },
                },
            },
            "required": ["page_id", "updates"],
        },
    },
}


def handle_update_transaction(args: dict) -> str:
    try:
        notion = get_notion()
        tx = notion.update_transaction(args["page_id"], args["updates"])
        return _to_json({"updated": True, "transaction": tx})
    except Exception as e:
        return _err(f"Update transaction failed: {type(e).__name__}: {e}")


# ============================================================================
# Tool 4: kasio_archive_transaction
# ============================================================================

SCHEMA_ARCHIVE_TRANSACTION = {
    "type": "function",
    "function": {
        "name": "kasio_archive_transaction",
        "description": "Soft-delete transaksi (archive). Bisa di-restore manual dari Notion UI.",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Notion page ID transaksi"},
            },
            "required": ["page_id"],
        },
    },
}


def handle_archive_transaction(args: dict) -> str:
    try:
        notion = get_notion()
        tx = notion.archive_transaction(args["page_id"])
        return _to_json({"archived": True, "id": tx["id"]})
    except Exception as e:
        return _err(f"Archive transaction failed: {type(e).__name__}: {e}")


# ============================================================================
# Tool 5: kasio_list_accounts
# ============================================================================

SCHEMA_LIST_ACCOUNTS = {
    "type": "function",
    "function": {
        "name": "kasio_list_accounts",
        "description": "List rekening dari Notion DB. Default exclude archived.",
        "parameters": {
            "type": "object",
            "properties": {
                "include_archived": {"type": "boolean", "default": False},
            },
        },
    },
}


def handle_list_accounts(args: dict) -> str:
    try:
        notion = get_notion()
        accounts = notion.list_accounts(include_archived=args.get("include_archived", False))
        return _to_json({"accounts": accounts, "count": len(accounts)})
    except Exception as e:
        return _err(f"List accounts failed: {type(e).__name__}: {e}")


# ============================================================================
# Tool 6: kasio_save_account
# ============================================================================

SCHEMA_SAVE_ACCOUNT = {
    "type": "function",
    "function": {
        "name": "kasio_save_account",
        "description": "Tambah rekening baru.",
        "parameters": {
            "type": "object",
            "properties": {
                "nama": {"type": "string", "description": "Nama rekening (misal: 'BCA', 'GoPay')"},
                "saldo_awal": {"type": "number", "description": "Saldo awal dalam Rupiah", "default": 0},
                "urutan": {"type": "integer", "description": "Sort order (lower = top)"},
                "ikon": {"type": "string", "description": "Emoji icon (misal: '🏦', '💳')"},
            },
            "required": ["nama"],
        },
    },
}


def handle_save_account(args: dict) -> str:
    try:
        notion = get_notion()
        acct = notion.save_account(
            nama=args["nama"],
            saldo_awal=args.get("saldo_awal", 0),
            urutan=args.get("urutan"),
            ikon=args.get("ikon", ""),
        )
        return _to_json({"saved": True, "account": acct})
    except Exception as e:
        return _err(f"Save account failed: {type(e).__name__}: {e}")


# ============================================================================
# Tool 7: kasio_update_account
# ============================================================================

SCHEMA_UPDATE_ACCOUNT = {
    "type": "function",
    "function": {
        "name": "kasio_update_account",
        "description": "Update rekening existing.",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string"},
                "updates": {
                    "type": "object",
                    "properties": {
                        "nama": {"type": "string"},
                        "saldo_awal": {"type": "number"},
                        "status": {"type": "string", "enum": ["Aktif", "Diarsipkan"]},
                        "urutan": {"type": "integer"},
                        "ikon": {"type": "string"},
                    },
                },
            },
            "required": ["page_id", "updates"],
        },
    },
}


def handle_update_account(args: dict) -> str:
    try:
        notion = get_notion()
        acct = notion.update_account(args["page_id"], args["updates"])
        return _to_json({"updated": True, "account": acct})
    except Exception as e:
        return _err(f"Update account failed: {type(e).__name__}: {e}")


# ============================================================================
# Tool 8: kasio_archive_account
# ============================================================================

SCHEMA_ARCHIVE_ACCOUNT = {
    "type": "function",
    "function": {
        "name": "kasio_archive_account",
        "description": "Soft-delete rekening.",
        "parameters": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string"},
            },
            "required": ["page_id"],
        },
    },
}


def handle_archive_account(args: dict) -> str:
    try:
        notion = get_notion()
        acct = notion.archive_account(args["page_id"])
        return _to_json({"archived": True, "id": acct["id"]})
    except Exception as e:
        return _err(f"Archive account failed: {type(e).__name__}: {e}")


# ============================================================================
# Tool 9: kasio_parse_nominal
# ============================================================================

SCHEMA_PARSE_NOMINAL = {
    "type": "function",
    "function": {
        "name": "kasio_parse_nominal",
        "description": "Parse string nominal Indonesia ke integer. Support: 35000, 35rb, 1.5jt, 1m, 1.000.000.",
        "parameters": {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "Input string nominal"},
            },
            "required": ["input"],
        },
    },
}


def handle_parse_nominal(args: dict) -> str:
    try:
        result = parsers.parse_nominal(args.get("input", ""))
        return _to_json({"input": args.get("input", ""), "parsed": result, "valid": result is not None})
    except Exception as e:
        return _err(f"Parse nominal failed: {type(e).__name__}: {e}")


# ============================================================================
# Tool 10: kasio_read_receipt
# ============================================================================

SCHEMA_READ_RECEIPT = {
    "type": "function",
    "function": {
        "name": "kasio_read_receipt",
        "description": "Baca struk belanja via MiniMax M3 vision. Returns nama, jumlah, tipe, kategori, tanggal, confidence.",
        "parameters": {
            "type": "object",
            "properties": {
                "image_b64": {"type": "string", "description": "Base64-encoded image data"},
                "mime_type": {"type": "string", "default": "image/jpeg"},
            },
            "required": ["image_b64"],
        },
    },
}


def handle_read_receipt(args: dict) -> str:
    try:
        vision = get_vision()
        if vision is None:
            return _err("MINIMAX_API_KEY not set — vision reading unavailable")
        result = vision.read_receipt(args["image_b64"], args.get("mime_type", "image/jpeg"))
        # Validate extracted fields
        if not constants.is_valid_kategori(result.get("kategori", "")):
            result["kategori"] = "Lainnya"
            result["confidence"] = min(result.get("confidence", 0), 0.7)
        return _to_json(result)
    except Exception as e:
        return _err(f"Read receipt failed: {type(e).__name__}: {e}")


# ============================================================================
# Tool 11: kasio_read_screenshot
# ============================================================================

SCHEMA_READ_SCREENSHOT = {
    "type": "function",
    "function": {
        "name": "kasio_read_screenshot",
        "description": "Baca screenshot mutasi bank/ewallet. Returns jenis, sumber, tujuan, nominal, confidence.",
        "parameters": {
            "type": "object",
            "properties": {
                "image_b64": {"type": "string"},
                "mime_type": {"type": "string", "default": "image/jpeg"},
            },
            "required": ["image_b64"],
        },
    },
}


def handle_read_screenshot(args: dict) -> str:
    try:
        vision = get_vision()
        if vision is None:
            return _err("MINIMAX_API_KEY not set — vision reading unavailable")
        result = vision.read_screenshot(args["image_b64"], args.get("mime_type", "image/jpeg"))
        return _to_json(result)
    except Exception as e:
        return _err(f"Read screenshot failed: {type(e).__name__}: {e}")


# ============================================================================
# Tool registration
# ============================================================================

ALL_TOOLS = [
    ("kasio_list_transactions", SCHEMA_LIST_TRANSACTIONS, handle_list_transactions, "📋"),
    ("kasio_save_transaction", SCHEMA_SAVE_TRANSACTION, handle_save_transaction, "💾"),
    ("kasio_update_transaction", SCHEMA_UPDATE_TRANSACTION, handle_update_transaction, "✏️"),
    ("kasio_archive_transaction", SCHEMA_ARCHIVE_TRANSACTION, handle_archive_transaction, "🗑️"),
    ("kasio_list_accounts", SCHEMA_LIST_ACCOUNTS, handle_list_accounts, "💼"),
    ("kasio_save_account", SCHEMA_SAVE_ACCOUNT, handle_save_account, "➕"),
    ("kasio_update_account", SCHEMA_UPDATE_ACCOUNT, handle_update_account, "✏️"),
    ("kasio_archive_account", SCHEMA_ARCHIVE_ACCOUNT, handle_archive_account, "🗑️"),
    ("kasio_parse_nominal", SCHEMA_PARSE_NOMINAL, handle_parse_nominal, "🔢"),
    ("kasio_read_receipt", SCHEMA_READ_RECEIPT, handle_read_receipt, "🧾"),
    ("kasio_read_screenshot", SCHEMA_READ_SCREENSHOT, handle_read_screenshot, "📸"),
]


def _check_kasio_available() -> bool:
    """Runtime gate — only enable tools if NOTION_API_KEY + DS IDs are set."""
    import os
    api_key = os.environ.get("NOTION_API_KEY", "").strip()
    tx_ds = os.environ.get("KASIO_TRANSACTIONS_DS_ID", "").strip()
    acct_ds = os.environ.get("KASIO_ACCOUNTS_DS_ID", "").strip()
    return bool(api_key and not api_key.startswith("your_") and tx_ds and acct_ds)
