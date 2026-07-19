"""Vision API client for kasio-notion plugin.

Port dari KASIO v2 src/minimax.js. Hanya untuk baca struk / screenshot bank.
Confidence threshold 0.8 — kalau di bawah, minta konfirmasi manual user.
"""

from __future__ import annotations
import os
import re
import json
import httpx
from .constants import VISION_API_BASE, VISION_MODEL

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
        return json.loads(match.group(0))

    def read_receipt(self, image_b64: str, mime_type: str = "image/jpeg") -> dict:
        """Read shopping receipt (Indomaret, Resto, etc). Returns structured fields."""
        from datetime import datetime, timezone
        data = self._call_vision(PROMPT_STRUK, image_b64, mime_type)
        return {
            "jenis": "struk_belanja",
            "nama": data.get("merchant") or "Belanja",
            "jumlah": float(data.get("total") or 0),
            "tipe": "Pengeluaran",
            "kategori": data.get("kategori") or "Lainnya",
            "tanggal": data.get("tanggal") or datetime.now(timezone.utc).date().isoformat(),
            "catatan": "",
            "confidence": float(data.get("confidence") or 0),
        }

    def read_screenshot(self, image_b64: str, mime_type: str = "image/jpeg") -> dict:
        """Read bank/ewallet screenshot. Returns transfer/payment details."""
        from datetime import datetime, timezone
        data = self._call_vision(PROMPT_SCREENSHOT, image_b64, mime_type)
        return {
            "jenis": data.get("jenis"),
            "sumber": data.get("sumber"),
            "tujuan": data.get("tujuan"),
            "nominal": float(data.get("nominal") or 0),
            "tanggal": data.get("tanggal") or datetime.now(timezone.utc).date().isoformat(),
            "keterangan": data.get("keterangan") or "",
            "confidence": float(data.get("confidence") or 0),
        }
