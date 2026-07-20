"""Vision API client for kasio-notion plugin.

Port dari KASIO v2 src/minimax.js. Hanya untuk baca struk / screenshot bank.
Confidence threshold 0.8 — kalau di bawah, minta konfirmasi manual user.

Validation layer:
  - Sanity check LLM output (jenis enum, nominal positivity, confidence range)
  - Fallback ke safe defaults kalau LLM hallucinate
  - Clamp confidence ke range [0, 1]
"""

from __future__ import annotations
import os
import re
import json
import logging
import httpx

# Support both package mode (production: from .constants import ...) and
# top-level mode (testing: from constants import ...).
try:
    from .constants import VISION_API_BASE, VISION_MODEL
except ImportError:
    from constants import VISION_API_BASE, VISION_MODEL

logger = logging.getLogger("kasio.vision")

PROMPT_STRUK = """
Baca struk belanja ini. Ekstrak sebagai JSON:
{
  "merchant": "nama toko",
  "total": 87500,
  "tanggal": "YYYY-MM-DD",
  "kategori": "salah satu dari: Makanan & Minuman, Transportasi, Belanja, Tagihan, Kesehatan, Hiburan, Pendapatan, Lainnya",
  "confidence": 0.0-1.0
}
Hanya JSON, tanpa penjelasan.
"""

PROMPT_SCREENSHOT = """
Baca screenshot mutasi bank/ewallet ini. Tentukan JENIS transaksi lalu ekstrak JSON.

Jenis yang mungkin:
- "transfer_out" — user kirim uang ke rekening/ewallet lain
- "transfer_in"  — user terima uang dari rekening/ewallet lain
- "topup_ewallet" — top-up e-wallet dari bank (misal dari BCA ke GoPay)
- "payment_merchant" — bayar merchant via QRIS/payment code (bukan transfer antar orang)

Untuk payment_merchant:
{
  "jenis": "payment_merchant",
  "sumber": "nama bank/ewallet asal (misal 'BCA', 'GoPay')",
  "tujuan": "nama merchant/toko",
  "nominal": 25000,
  "tanggal": "YYYY-MM-DD",
  "keterangan": "deskripsi singkat",
  "confidence": 0.0-1.0
}

Untuk transfer_in/transfer_out/topup_ewallet:
{
  "jenis": "transfer_in" | "transfer_out" | "topup_ewallet",
  "sumber": "nama bank/ewallet asal",
  "tujuan": "nama bank/ewallet tujuan",
  "nominal": 50000,
  "tanggal": "YYYY-MM-DD",
  "keterangan": "nama pengirim/penerima jika ada",
  "confidence": 0.0-1.0
}

PENTING:
- NOMINAL TRANSFER/BAYAR, BUKAN SALDO REKENING
- Kalau tanggal tidak eksplisit, pakai tanggal hari ini: {today}
- Kalau screenshot berisi multiple transactions, ambil yang paling atas/paling baru
- Abaikan banner promo / iklan / menu UI non-transaksi

Panduan confidence:
- 0.9-1.0: semua field jelas terlihat
- 0.7-0.89: ada 1 field ambigu
- 0.5-0.69: ada 2+ field ambigu
- <0.5: tidak yakin (cuma banner / saldo saja)

Hanya JSON, tanpa penjelasan.
"""

# ============================================================================
# Validation constants
# ============================================================================

VALID_JENIS = frozenset({"transfer_in", "transfer_out", "topup_ewallet", "payment_merchant"})
VALID_TIPE = frozenset({"Pemasukan", "Pengeluaran"})
VALID_KATEGORI = frozenset({
    "Makanan & Minuman", "Transportasi", "Belanja", "Tagihan",
    "Kesehatan", "Hiburan", "Pendapatan", "Lainnya",
})

# Mapping: jenis transaksi → tipe (Pemasukan / Pengeluaran)
JENIS_TO_TIPE = {
    "transfer_in": "Pemasukan",
    "transfer_out": "Pengeluaran",
    "topup_ewallet": "Pengeluaran",   # Top-up e-wallet = keluar dari bank
    "payment_merchant": "Pengeluaran",
}

MAX_NOMINAL = 10_000_000_000  # 10 milyar — sanity cap (avoid hallucinated extreme values)


def _clamp_confidence(value, default: float = 0.0) -> float:
    """Clamp confidence value to [0.0, 1.0] range. Handles NaN, None, and invalid types."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    # Reject NaN explicitly (NaN comparisons always False, would propagate)
    if v != v:  # NaN check
        return default
    return max(0.0, min(1.0, v))


def _sanitize_nominal(value, default: float = 0.0) -> float:
    """Sanitize nominal: must be positive number, within MAX_NOMINAL cap."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if n < 0:
        n = abs(n)  # Sometimes LLM returns negative; take absolute value
    if n > MAX_NOMINAL:
        logger.warning("Nominal %s exceeds MAX_NOMINAL cap %s — clamping", n, MAX_NOMINAL)
        n = MAX_NOMINAL
    return n


def _sanitize_string(value, default: str = "") -> str:
    """Sanitize string field: strip whitespace, truncate to safe length."""
    if not isinstance(value, str):
        return default
    s = value.strip()
    # Notion rich_text content limit is 2000 chars
    return s[:2000] if s else default


def _sanitize_date(value, default: str) -> str:
    """Sanitize date: must be valid YYYY-MM-DD format, else use default (today)."""
    if not isinstance(value, str):
        return default
    cleaned = value.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", cleaned):
        # Validate month 1-12 and day 1-31
        try:
            month, day = int(cleaned[5:7]), int(cleaned[8:10])
            if 1 <= month <= 12 and 1 <= day <= 31:
                return cleaned
        except ValueError:
            pass
    return default


def _validate_screenshot_result(data: dict, today_iso: str) -> dict:
    """Validate & sanitize LLM output for screenshot reading.

    Returns a sanitized dict with safe defaults. Logs warnings for invalid fields
    so user can debug LLM hallucination.
    """
    raw_jenis = data.get("jenis", "")
    if raw_jenis not in VALID_JENIS:
        logger.warning(
            "Invalid 'jenis' from vision LLM: %r (expected one of %s). Falling back to 'payment_merchant'.",
            raw_jenis, sorted(VALID_JENIS),
        )
        jenis = "payment_merchant"  # safest default — most common case
    else:
        jenis = raw_jenis

    return {
        "jenis": jenis,
        "tipe": JENIS_TO_TIPE.get(jenis, "Pengeluaran"),
        "sumber": _sanitize_string(data.get("sumber"), default="Unknown"),
        "tujuan": _sanitize_string(data.get("tujuan"), default="Unknown"),
        "nominal": _sanitize_nominal(data.get("nominal")),
        "tanggal": _sanitize_date(data.get("tanggal"), default=today_iso),
        "keterangan": _sanitize_string(data.get("keterangan")),
        "confidence": _clamp_confidence(data.get("confidence"), default=0.0),
    }


def _validate_receipt_result(data: dict, today_iso: str) -> dict:
    """Validate & sanitize LLM output for receipt reading."""
    kategori = _sanitize_string(data.get("kategori"), default="Lainnya")
    if kategori not in VALID_KATEGORI:
        logger.warning(
            "Invalid 'kategori' from vision LLM: %r. Falling back to 'Lainnya'.",
            kategori,
        )
        kategori = "Lainnya"

    merchant = _sanitize_string(data.get("merchant"), default="Belanja")

    return {
        "jenis": "struk_belanja",
        "nama": merchant,
        "jumlah": _sanitize_nominal(data.get("total")),
        "tipe": "Pengeluaran",   # Receipts are always expenses
        "kategori": kategori,
        "tanggal": _sanitize_date(data.get("tanggal"), default=today_iso),
        "catatan": "",
        "confidence": _clamp_confidence(data.get("confidence"), default=0.0),
    }


class VisionAPI:
    """MiniMax M3 vision client."""

    def __init__(self):
        api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
        if not api_key or api_key.startswith("your_"):
            raise RuntimeError(
                "MINIMAX_API_KEY not set. Vision reading tidak akan jalan tanpa ini."
            )
        self.api_key = api_key
        self._client = httpx.Client(
            base_url=VISION_API_BASE,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

    def _call_vision(self, prompt: str, image_b64: str, mime_type: str = "image/jpeg") -> dict:
        from datetime import datetime, timezone
        prompt_filled = prompt.format(today=datetime.now(timezone.utc).date().isoformat())
        body = {
            "model": VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_filled},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                    ],
                }
            ],
            "temperature": 0.1,
        }
        resp = self._client.post("/text/chatcompletion_v2", json=body)
        resp.raise_for_status()
        content = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content", "")
        match = re.search(r"\{[\s\S]*\}", content)
        if not match:
            raise RuntimeError(f"Gagal parse response LLM: tidak ada JSON. Response: {content[:200]}")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Gagal parse JSON dari vision LLM: {e}. Raw: {match.group(0)[:200]}")

    def read_receipt(self, image_b64: str, mime_type: str = "image/jpeg") -> dict:
        """Read shopping receipt (Indomaret, Resto, etc). Returns structured fields.

        Output includes validated & sanitized fields:
          - nama, jumlah, tipe, kategori, tanggal, confidence, catatan
        """
        from datetime import datetime, timezone
        today_iso = datetime.now(timezone.utc).date().isoformat()
        raw = self._call_vision(PROMPT_STRUK, image_b64, mime_type)
        return _validate_receipt_result(raw, today_iso)

    def read_screenshot(self, image_b64: str, mime_type: str = "image/jpeg") -> dict:
        """Read bank/ewallet screenshot. Returns transfer/payment details.

        Output includes validated & sanitized fields:
          - jenis (one of transfer_in, transfer_out, topup_ewallet, payment_merchant)
          - tipe (auto-derived: transfer_in → Pemasukan, others → Pengeluaran)
          - sumber, tujuan, nominal, tanggal, keterangan, confidence
        """
        from datetime import datetime, timezone
        today_iso = datetime.now(timezone.utc).date().isoformat()
        raw = self._call_vision(PROMPT_SCREENSHOT, image_b64, mime_type)
        return _validate_screenshot_result(raw, today_iso)
