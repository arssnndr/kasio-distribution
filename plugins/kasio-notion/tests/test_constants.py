"""Unit tests for constants.py — schema maps and validation functions.

These tests ensure that the property name mappings match Notion DB schema
exactly (case-sensitive!), and that validation functions correctly accept/reject
the right enum values.
"""
from __future__ import annotations

import pytest

from constants import (
    TX_PROP, ACCT_PROP,
    TIPE_PEMASUKAN, TIPE_PENGELUARAN, VALID_TIPE,
    KATEGORI_LIST, KATEGORI_ICON, TIPE_ICON,
    REKENING_STATUS_AKTIF, REKENING_STATUS_DIARSIPKAN,
    is_valid_tipe, is_valid_kategori,
    NOTION_API_BASE, NOTION_VERSION,
    VISION_API_BASE, VISION_MODEL,
    VISION_CONFIDENCE_THRESHOLD, UNDO_WINDOW_SECONDS,
)


# ============================================================================
# Property maps — must match Notion DB schema EXACTLY (case-sensitive!)
# ============================================================================

class TestTransactionPropertyMap:
    """TX_PROP must match Notion DB column names exactly."""

    def test_all_required_keys_present(self):
        required = {"nama", "jumlah", "tipe", "kategori", "tanggal",
                    "catatan", "rekening", "transfer_group"}
        assert required.issubset(TX_PROP.keys())

    def test_title_column_is_nama(self):
        """Notion title property is conventionally 'Nama' for transactions."""
        assert TX_PROP["nama"] == "Nama"

    def test_amount_column_is_angka(self):
        """Notion number property uses 'Angka' (Indonesian for number)."""
        assert TX_PROP["jumlah"] == "Angka"

    def test_type_column_is_tipe(self):
        assert TX_PROP["tipe"] == "Tipe"

    def test_category_column_is_kategori(self):
        assert TX_PROP["kategori"] == "Kategori"

    def test_date_column_is_tanggal(self):
        assert TX_PROP["tanggal"] == "Tanggal"

    def test_notes_column_is_catatan(self):
        assert TX_PROP["catatan"] == "Catatan"

    def test_account_relation_column_is_rekening(self):
        assert TX_PROP["rekening"] == "Rekening"

    def test_transfer_group_column_has_id_suffix(self):
        """Important: column has 'ID' suffix — case-sensitive in Notion."""
        assert TX_PROP["transfer_group"] == "Transfer Group ID"

    def test_no_extra_keys(self):
        """If schema changes, this test fails — update both code AND Notion DB."""
        expected = {"nama", "jumlah", "tipe", "kategori", "tanggal",
                    "catatan", "rekening", "transfer_group"}
        assert set(TX_PROP.keys()) == expected


class TestAccountPropertyMap:
    """ACCT_PROP must match Notion DB column names exactly."""

    def test_all_required_keys_present(self):
        required = {"nama", "saldo_awal", "status", "urutan", "ikon"}
        assert required.issubset(ACCT_PROP.keys())

    def test_title_column_is_nama(self):
        assert ACCT_PROP["nama"] == "Nama"

    def test_initial_balance_has_space_in_name(self):
        """CRITICAL: 'Saldo Awal' has a SPACE — Notion is case/space sensitive."""
        assert ACCT_PROP["saldo_awal"] == "Saldo Awal"

    def test_status_column(self):
        assert ACCT_PROP["status"] == "Status"

    def test_urutan_column(self):
        assert ACCT_PROP["urutan"] == "Urutan"

    def test_icon_column(self):
        assert ACCT_PROP["ikon"] == "Ikon"

    def test_no_extra_keys(self):
        expected = {"nama", "saldo_awal", "status", "urutan", "ikon"}
        assert set(ACCT_PROP.keys()) == expected


# ============================================================================
# Enums
# ============================================================================

class TestTipeEnum:
    """Transaction type enum (Pemasukan / Pengeluaran)."""

    def test_valid_tipe_set(self):
        assert VALID_TIPE == {TIPE_PEMASUKAN, TIPE_PENGELUARAN}

    def test_pemasukan_value(self):
        assert TIPE_PEMASUKAN == "Pemasukan"

    def test_pengeluaran_value(self):
        assert TIPE_PENGELUARAN == "Pengeluaran"

    @pytest.mark.parametrize("value", ["Pemasukan", "Pengeluaran"])
    def test_valid_tipe_accepted(self, value):
        assert is_valid_tipe(value) is True

    @pytest.mark.parametrize("value", [
        "", "pemasukan", "pengeluaran",   # case-sensitive!
        "income", "expense",              # English
        "PEMASUKAN",                      # uppercase
        None, 0, [],
    ])
    def test_invalid_tipe_rejected(self, value):
        """is_valid_tipe must be strict — case-sensitive, exact match."""
        assert is_valid_tipe(value) is False


class TestKategoriEnum:
    """Category enum — 8 fixed values."""

    def test_exactly_8_kategori(self):
        assert len(KATEGORI_LIST) == 8

    def test_all_kategori_values(self):
        expected = {
            "Makanan & Minuman",
            "Transportasi",
            "Belanja",
            "Tagihan",
            "Kesehatan",
            "Hiburan",
            "Pendapatan",
            "Lainnya",
        }
        assert set(KATEGORI_LIST) == expected

    @pytest.mark.parametrize("value", KATEGORI_LIST)
    def test_valid_kategori_accepted(self, value):
        assert is_valid_kategori(value) is True

    @pytest.mark.parametrize("value", [
        "", "makanan", "Makanan",          # partial/wrong case
        "Food", "Food & Drink",            # English
        "Random",
        None, 0, [],
    ])
    def test_invalid_kategori_rejected(self, value):
        assert is_valid_kategori(value) is False

    def test_every_kategori_has_icon(self):
        """KATEGORI_ICON must have entry for every kategori (for display)."""
        for k in KATEGORI_LIST:
            assert k in KATEGORI_ICON, f"Missing icon for kategori: {k}"

    def test_icon_is_emoji(self):
        """Icons should be non-empty emoji strings."""
        for k, icon in KATEGORI_ICON.items():
            assert isinstance(icon, str)
            assert len(icon) >= 1


class TestTipeIcon:
    """Tipe enum has display icons."""

    def test_pemasukan_has_icon(self):
        assert TIPE_PEMASUKAN in TIPE_ICON

    def test_pengeluaran_has_icon(self):
        assert TIPE_PENGELUARAN in TIPE_ICON

    def test_icons_are_distinct(self):
        """Pemasukan (income) and Pengeluaran (expense) should have different colors."""
        assert TIPE_ICON[TIPE_PEMASUKAN] != TIPE_ICON[TIPE_PENGELUARAN]


class TestRekeningStatus:
    """Account status enum."""

    def test_status_values(self):
        assert REKENING_STATUS_AKTIF == "Aktif"
        assert REKENING_STATUS_DIARSIPKAN == "Diarsipkan"

    def test_status_values_distinct(self):
        assert REKENING_STATUS_AKTIF != REKENING_STATUS_DIARSIPKAN


# ============================================================================
# API Configuration
# ============================================================================

class TestAPIConfiguration:
    """API endpoints and config values."""

    def test_notion_api_base(self):
        assert NOTION_API_BASE == "https://api.notion.com/v1"

    def test_notion_version_is_2025_09_03(self):
        """Notion API version 2025-09-03 introduced data_sources endpoint."""
        assert NOTION_VERSION == "2025-09-03"

    def test_vision_api_base(self):
        assert VISION_API_BASE == "https://api.minimax.io/v1"

    def test_vision_model_is_minimax_m3(self):
        """Model name should match MiniMax M3 documentation."""
        assert VISION_MODEL == "MiniMax-M3"

    def test_vision_threshold_is_reasonable(self):
        """Confidence threshold 0.8 means we trust high-confidence extractions."""
        assert 0.5 <= VISION_CONFIDENCE_THRESHOLD <= 1.0
        assert VISION_CONFIDENCE_THRESHOLD == 0.8

    def test_undo_window_is_30_seconds(self):
        """Undo window of 30s is documented in README."""
        assert UNDO_WINDOW_SECONDS == 30
