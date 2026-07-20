"""Unit tests for vision.py — validation layer for LLM output.

The vision module uses MiniMax M3 LLM to extract structured data from images.
LLM outputs can be unpredictable (hallucinations, invalid values), so we have
a validation layer that:
  - Sanitizes confidence to [0, 1] range
  - Sanitizes nominal (positive, capped at MAX_NOMINAL)
  - Sanitizes date to YYYY-MM-DD
  - Validates jenis enum for screenshots
  - Validates kategori enum for receipts
  - Auto-derives tipe from jenis for screenshots
  - Falls back to safe defaults on invalid fields

These tests focus on the validation layer (no actual API calls).
"""
from __future__ import annotations
import pytest

from vision import (
    _clamp_confidence,
    _sanitize_nominal,
    _sanitize_string,
    _sanitize_date,
    _validate_screenshot_result,
    _validate_receipt_result,
    VALID_JENIS,
    VALID_KATEGORI,
    JENIS_TO_TIPE,
    MAX_NOMINAL,
)


# ============================================================================
# _clamp_confidence
# ============================================================================

class TestClampConfidence:
    """Test confidence value sanitization."""

    @pytest.mark.parametrize("value,expected", [
        (0.0, 0.0),
        (0.5, 0.5),
        (0.8, 0.8),
        (1.0, 1.0),
        (0.95, 0.95),
    ])
    def test_valid_values_passed_through(self, value, expected):
        assert _clamp_confidence(value) == expected

    def test_negative_clamped_to_zero(self):
        assert _clamp_confidence(-0.5) == 0.0
        assert _clamp_confidence(-1.0) == 0.0

    def test_above_one_clamped_to_one(self):
        assert _clamp_confidence(1.5) == 1.0
        assert _clamp_confidence(2.0) == 1.0
        assert _clamp_confidence(100.0) == 1.0

    @pytest.mark.parametrize("invalid", [None, "abc", [], {}])
    def test_invalid_returns_default(self, invalid):
        """Non-numeric input → default."""
        assert _clamp_confidence(invalid, default=0.0) == 0.0

    def test_nan_returns_default(self):
        """NaN is invalid (LLM may return NaN) — return default."""
        assert _clamp_confidence(float('nan'), default=0.0) == 0.0

    def test_numeric_string_accepted(self):
        """Numeric strings like '0.5' are accepted (lenient parsing)."""
        assert _clamp_confidence("0.5") == 0.5
        assert _clamp_confidence("0.95") == 0.95

    def test_custom_default(self):
        assert _clamp_confidence(None, default=0.5) == 0.5


# ============================================================================
# _sanitize_nominal
# ============================================================================

class TestSanitizeNominal:
    """Test nominal value sanitization."""

    @pytest.mark.parametrize("value,expected", [
        (0, 0.0),
        (1000, 1000.0),
        (87500.50, 87500.50),
        (1500000, 1500000.0),
    ])
    def test_valid_positive_values(self, value, expected):
        assert _sanitize_nominal(value) == expected

    @pytest.mark.parametrize("value,expected", [
        (-1000, 1000.0),     # Negative → absolute value
        (-87500.50, 87500.50),
    ])
    def test_negative_becomes_positive(self, value, expected):
        """Sometimes LLM returns negative for debits/credits — take abs."""
        assert _sanitize_nominal(value) == expected

    def test_above_max_clamped(self):
        """Nominal > MAX_NOMINAL is capped (avoid hallucinated extreme values)."""
        assert _sanitize_nominal(100_000_000_000) == MAX_NOMINAL
        assert _sanitize_nominal(MAX_NOMINAL * 100) == MAX_NOMINAL

    def test_at_max_not_clamped(self):
        """Exactly at MAX_NOMINAL passes through."""
        assert _sanitize_nominal(MAX_NOMINAL) == MAX_NOMINAL

    @pytest.mark.parametrize("invalid", [None, "abc", [], {}])
    def test_invalid_returns_default(self, invalid):
        """Invalid → 0.0 (caller must validate before save)."""
        assert _sanitize_nominal(invalid, default=0.0) == 0.0


# ============================================================================
# _sanitize_string
# ============================================================================

class TestSanitizeString:
    """Test string field sanitization."""

    def test_valid_string_passed_through(self):
        assert _sanitize_string("Indomaret") == "Indomaret"

    def test_whitespace_stripped(self):
        assert _sanitize_string("  Indomaret  ") == "Indomaret"

    def test_empty_returns_default(self):
        assert _sanitize_string("", default="Unknown") == "Unknown"
        assert _sanitize_string("   ", default="Unknown") == "Unknown"

    def test_oversized_truncated(self):
        """Notion rich_text limit is 2000 chars — truncate beyond that."""
        long_str = "x" * 3000
        result = _sanitize_string(long_str)
        assert len(result) == 2000

    @pytest.mark.parametrize("invalid", [None, 123, [], {}])
    def test_non_string_returns_default(self, invalid):
        assert _sanitize_string(invalid, default="Unknown") == "Unknown"


# ============================================================================
# _sanitize_date
# ============================================================================

class TestSanitizeDate:
    """Test date sanitization."""

    def test_valid_iso_format(self):
        assert _sanitize_date("2026-07-19", "2026-01-01") == "2026-07-19"

    def test_valid_iso_with_whitespace(self):
        assert _sanitize_date("  2026-07-19  ", "2026-01-01") == "2026-07-19"

    @pytest.mark.parametrize("invalid", [
        "",
        "2026/07/19",         # wrong separator
        "19-07-2026",         # wrong order
        "2026-13-01",         # invalid month
        "July 19 2026",       # English
        "garbage",
    ])
    def test_invalid_returns_default(self, invalid):
        assert _sanitize_date(invalid, "2026-01-01") == "2026-01-01"

    @pytest.mark.parametrize("invalid", [None, 123, [], {}])
    def test_non_string_returns_default(self, invalid):
        assert _sanitize_date(invalid, "2026-01-01") == "2026-01-01"


# ============================================================================
# _validate_screenshot_result
# ============================================================================

class TestValidateScreenshotResult:
    """Test full screenshot result validation."""

    def test_valid_transfer_out(self):
        data = {
            "jenis": "transfer_out",
            "sumber": "BCA",
            "tujuan": "GoPay",
            "nominal": 100000,
            "tanggal": "2026-07-19",
            "keterangan": "Top up GoPay",
            "confidence": 0.95,
        }
        result = _validate_screenshot_result(data, "2026-07-19")

        assert result["jenis"] == "transfer_out"
        assert result["tipe"] == "Pengeluaran"  # auto-derived
        assert result["sumber"] == "BCA"
        assert result["tujuan"] == "GoPay"
        assert result["nominal"] == 100000.0
        assert result["tanggal"] == "2026-07-19"
        assert result["keterangan"] == "Top up GoPay"
        assert result["confidence"] == 0.95

    def test_valid_transfer_in_is_pemasukan(self):
        """transfer_in should auto-map to Pemasukan."""
        data = {
            "jenis": "transfer_in",
            "sumber": "Teman",
            "tujuan": "BCA",
            "nominal": 50000,
            "tanggal": "2026-07-19",
            "confidence": 0.9,
        }
        result = _validate_screenshot_result(data, "2026-07-19")
        assert result["jenis"] == "transfer_in"
        assert result["tipe"] == "Pemasukan"

    @pytest.mark.parametrize("jenis,expected_tipe", [
        ("transfer_out", "Pengeluaran"),
        ("transfer_in", "Pemasukan"),
        ("topup_ewallet", "Pengeluaran"),
        ("payment_merchant", "Pengeluaran"),
    ])
    def test_jenis_to_tipe_mapping(self, jenis, expected_tipe):
        """All valid jenis values map to correct tipe."""
        data = {"jenis": jenis, "nominal": 1000, "confidence": 0.9}
        result = _validate_screenshot_result(data, "2026-07-19")
        assert result["tipe"] == expected_tipe

    def test_invalid_jenis_falls_back_to_payment_merchant(self):
        """LLM hallucination → fall back to safest default."""
        data = {
            "jenis": "withdrawal_atm",  # not in VALID_JENIS
            "nominal": 500000,
            "confidence": 0.7,
        }
        result = _validate_screenshot_result(data, "2026-07-19")
        assert result["jenis"] == "payment_merchant"
        assert result["tipe"] == "Pengeluaran"

    def test_missing_jenis_falls_back(self):
        data = {"nominal": 1000, "confidence": 0.5}  # no jenis
        result = _validate_screenshot_result(data, "2026-07-19")
        assert result["jenis"] == "payment_merchant"

    def test_missing_sumber_uses_unknown(self):
        data = {"jenis": "transfer_out", "nominal": 1000}
        result = _validate_screenshot_result(data, "2026-07-19")
        assert result["sumber"] == "Unknown"

    def test_negative_nominal_becomes_positive(self):
        data = {"jenis": "transfer_out", "nominal": -50000}
        result = _validate_screenshot_result(data, "2026-07-19")
        assert result["nominal"] == 50000.0

    def test_extreme_nominal_capped(self):
        data = {"jenis": "transfer_out", "nominal": 999_999_999_999}
        result = _validate_screenshot_result(data, "2026-07-19")
        assert result["nominal"] == MAX_NOMINAL

    def test_invalid_date_uses_today(self):
        data = {
            "jenis": "transfer_out",
            "nominal": 1000,
            "tanggal": "yesterday",  # invalid format
        }
        result = _validate_screenshot_result(data, "2026-07-19")
        assert result["tanggal"] == "2026-07-19"  # fallback to today_iso

    def test_confidence_clamped(self):
        """Confidence > 1.0 should be clamped to 1.0."""
        data = {
            "jenis": "transfer_out",
            "nominal": 1000,
            "confidence": 1.5,  # invalid (LLM hallucination)
        }
        result = _validate_screenshot_result(data, "2026-07-19")
        assert result["confidence"] == 1.0

    def test_negative_confidence_clamped(self):
        data = {
            "jenis": "transfer_out",
            "nominal": 1000,
            "confidence": -0.3,
        }
        result = _validate_screenshot_result(data, "2026-07-19")
        assert result["confidence"] == 0.0

    def test_all_invalid_returns_safe_defaults(self):
        """Completely garbage input → all safe defaults."""
        data = {
            "jenis": None,
            "nominal": "abc",
            "tanggal": None,
            "confidence": None,
            "sumber": None,
            "tujuan": None,
        }
        result = _validate_screenshot_result(data, "2026-07-19")

        # Should not crash, all fields should have valid defaults
        assert result["jenis"] == "payment_merchant"
        assert result["tipe"] == "Pengeluaran"
        assert result["nominal"] == 0.0
        assert result["tanggal"] == "2026-07-19"
        assert result["confidence"] == 0.0
        assert result["sumber"] == "Unknown"
        assert result["tujuan"] == "Unknown"


# ============================================================================
# _validate_receipt_result
# ============================================================================

class TestValidateReceiptResult:
    """Test full receipt result validation."""

    def test_valid_receipt(self):
        data = {
            "merchant": "Indomaret",
            "total": 87500,
            "tanggal": "2026-07-19",
            "kategori": "Makanan & Minuman",
            "confidence": 0.95,
        }
        result = _validate_receipt_result(data, "2026-07-19")

        assert result["jenis"] == "struk_belanja"
        assert result["nama"] == "Indomaret"
        assert result["jumlah"] == 87500.0
        assert result["tipe"] == "Pengeluaran"   # Receipts always expense
        assert result["kategori"] == "Makanan & Minuman"
        assert result["tanggal"] == "2026-07-19"
        assert result["confidence"] == 0.95

    @pytest.mark.parametrize("kategori", [
        "Makanan & Minuman", "Transportasi", "Belanja", "Tagihan",
        "Kesehatan", "Hiburan", "Pendapatan", "Lainnya",
    ])
    def test_all_valid_kategori_accepted(self, kategori):
        data = {"merchant": "X", "total": 1000, "kategori": kategori}
        result = _validate_receipt_result(data, "2026-07-19")
        assert result["kategori"] == kategori

    @pytest.mark.parametrize("invalid_kategori", [
        "Food",            # English
        "makanan",         # lowercase
        "Random",          # not in list
        "",                # empty
    ])
    def test_invalid_kategori_falls_back_to_lainnya(self, invalid_kategori):
        data = {"merchant": "X", "total": 1000, "kategori": invalid_kategori}
        result = _validate_receipt_result(data, "2026-07-19")
        assert result["kategori"] == "Lainnya"

    def test_missing_merchant_uses_belanja(self):
        """Default merchant name is 'Belanja' (generic)."""
        data = {"total": 1000}
        result = _validate_receipt_result(data, "2026-07-19")
        assert result["nama"] == "Belanja"

    def test_tipe_always_pengeluaran(self):
        """Receipts are always Pengeluaran (can't have income receipt)."""
        data = {"merchant": "X", "total": 1000}
        result = _validate_receipt_result(data, "2026-07-19")
        assert result["tipe"] == "Pengeluaran"

    def test_extreme_total_capped(self):
        data = {"merchant": "X", "total": 999_999_999_999}
        result = _validate_receipt_result(data, "2026-07-19")
        assert result["jumlah"] == MAX_NOMINAL

    def test_invalid_date_uses_today(self):
        data = {"merchant": "X", "total": 1000, "tanggal": "tomorrow"}
        result = _validate_receipt_result(data, "2026-07-19")
        assert result["tanggal"] == "2026-07-19"


# ============================================================================
# Constants integrity
# ============================================================================

class TestVisionConstants:
    """Test vision module constants."""

    def test_valid_jenis_complete(self):
        """All 4 expected jenis are valid."""
        expected = {"transfer_in", "transfer_out", "topup_ewallet", "payment_merchant"}
        assert VALID_JENIS == expected

    def test_jenis_to_tipe_complete_mapping(self):
        """Every valid jenis has a tipe mapping."""
        for jenis in VALID_JENIS:
            assert jenis in JENIS_TO_TIPE
            assert JENIS_TO_TIPE[jenis] in {"Pemasukan", "Pengeluaran"}

    def test_only_transfer_in_is_pemasukan(self):
        """Only incoming transfer is income; others are expenses."""
        for jenis, expected_tipe in JENIS_TO_TIPE.items():
            if jenis == "transfer_in":
                assert expected_tipe == "Pemasukan"
            else:
                assert expected_tipe == "Pengeluaran"

    def test_valid_kategori_matches_constants(self):
        """VALID_KATEGORI in vision should match KATEGORI_LIST in constants."""
        from constants import KATEGORI_LIST
        assert VALID_KATEGORI == set(KATEGORI_LIST)

    def test_max_nominal_is_reasonable(self):
        """10 milyar IDR is sanity cap (avoid hallucinated extreme values)."""
        assert MAX_NOMINAL == 10_000_000_000
