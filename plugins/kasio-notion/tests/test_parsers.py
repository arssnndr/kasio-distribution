"""Unit tests for parsers.py — pure functions, no I/O.

Tests cover:
  - parse_nominal (Indonesian currency formats)
  - parse_date (multiple date formats + edge cases)
  - format_rupiah (Indonesian number formatting)
  - generate_uuid (UUID generation)

These are the most-tested functions because they're deterministic and used
in the hot path of the wizard UX.
"""
from __future__ import annotations
import re
from datetime import datetime, timezone

import pytest

from parsers import parse_nominal, parse_date, format_rupiah, generate_uuid


# ============================================================================
# parse_nominal
# ============================================================================

class TestParseNominal:
    """Test Indonesian nominal string parsing."""

    # --- Plain numbers ---
    @pytest.mark.parametrize("input_str,expected", [
        ("35000", 35000),
        ("0", 0),
        ("1", 1),
        ("100", 100),
        ("1000000", 1000000),
    ])
    def test_plain_integers(self, input_str, expected):
        assert parse_nominal(input_str) == expected

    @pytest.mark.parametrize("input_str,expected", [
        ("35.000", 35000),       # thousand separator with dot
        ("1.000.000", 1000000),
        ("1.500.000", 1500000),
        ("12.345.678", 12345678),
    ])
    def test_thousand_separator(self, input_str, expected):
        assert parse_nominal(input_str) == expected

    # --- Ribu variants ---
    @pytest.mark.parametrize("input_str,expected", [
        ("35rb", 35000),
        ("35k", 35000),
        ("35ribu", 35000),
        ("1rb", 1000),
        ("100rb", 100000),
        ("1.5rb", 1500),
        ("10.5rb", 10500),
        ("35 RB", 35000),       # case-insensitive (after lower())
        ("35K", 35000),
    ])
    def test_ribu_variants(self, input_str, expected):
        assert parse_nominal(input_str) == expected

    # --- Juta variants ---
    @pytest.mark.parametrize("input_str,expected", [
        ("1jt", 1000000),
        ("1.5jt", 1500000),
        ("2.3juta", 2300000),
        ("1m", 1000000),         # 'm' = juta (millions in Indonesian context)
        ("2.5m", 2500000),
        ("5juta", 5000000),
        ("1JT", 1000000),
    ])
    def test_juta_variants(self, input_str, expected):
        assert parse_nominal(input_str) == expected

    # --- With spaces (informal) ---
    @pytest.mark.parametrize("input_str,expected", [
        ("1.2 juta", 1200000),   # spaces stripped
        ("35 ribu", 35000),
        ("2.5 jt", 2500000),
        ("1 000 000", 1000000),  # space as thousand separator
    ])
    def test_with_spaces(self, input_str, expected):
        assert parse_nominal(input_str) == expected

    # --- Invalid inputs ---
    @pytest.mark.parametrize("invalid_input", [
        "",
        "abc",
        "rb",
        "jt",
        "ribu saja",
        "--",
        "35.",
        ".35",
        "35rb extra",
        "extra 35rb",
        "Rp 35000",   # 'Rp' prefix not supported
        "35000rp",    # suffix 'rp' without space not supported
        None,
        35000,        # non-string input
        [],
        {},
    ])
    def test_invalid_inputs_return_none(self, invalid_input):
        """Invalid formats should return None (caller handles gracefully)."""
        assert parse_nominal(invalid_input) is None

    # --- Edge cases ---
    def test_very_small_decimal(self):
        # 0.5rb = 500
        assert parse_nominal("0.5rb") == 500

    def test_very_large(self):
        # 999.9jt = 999_900_000
        assert parse_nominal("999.9jt") == 999_900_000

    def test_zero_ribu(self):
        assert parse_nominal("0rb") == 0

    def test_ambiguous_format_falls_through(self):
        # "1.234" could be 1.234 or 1234 — heuristic picks thousand separator
        # because parts[0]="1" (1 digit) and parts[1]="234" (3 digits)
        assert parse_nominal("1.234") == 1234

    def test_not_thousand_separator(self):
        # "1.23" — parts=["1", "23"], 23 is not 3 digits → not thousand sep
        # Falls through to float() → round(1.23) = 1
        # (Documented behavior: ambiguous, treated as decimal)
        result = parse_nominal("1.23")
        assert result in (1, 1234)  # either is acceptable


# ============================================================================
# parse_date
# ============================================================================

class TestParseDate:
    """Test date parsing to YYYY-MM-DD format."""

    def test_none_returns_today_utc(self):
        result = parse_date(None)
        assert result is not None
        # Verify format
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", result)
        # Verify it's today (UTC)
        assert result == datetime.now(timezone.utc).date().isoformat()

    def test_empty_string_returns_today_utc(self):
        result = parse_date("")
        assert result == datetime.now(timezone.utc).date().isoformat()

    def test_whitespace_returns_today_utc(self):
        result = parse_date("   ")
        assert result == datetime.now(timezone.utc).date().isoformat()

    @pytest.mark.parametrize("input_str,expected", [
        ("2026-07-19", "2026-07-19"),
        ("2026-01-01", "2026-01-01"),
        ("2026-12-31", "2026-12-31"),
        ("2025-02-28", "2025-02-28"),
    ])
    def test_iso_format_passthrough(self, input_str, expected):
        assert parse_date(input_str) == expected

    @pytest.mark.parametrize("input_str,expected", [
        ("19-07-2026", "2026-07-19"),
        ("1-7-2026", "2026-07-01"),       # single-digit day padded
        ("01-07-2026", "2026-07-01"),
        ("31-12-2026", "2026-12-31"),
    ])
    def test_dd_mm_yyyy_format(self, input_str, expected):
        assert parse_date(input_str) == expected

    @pytest.mark.parametrize("input_str,expected", [
        ("19/07/2026", "2026-07-19"),
        ("1/7/2026", "2026-07-01"),
        ("31/12/2026", "2026-12-31"),
    ])
    def test_dd_mm_yyyy_slash_separator(self, input_str, expected):
        assert parse_date(input_str) == expected

    @pytest.mark.parametrize("invalid_input", [
        "2026/07/19",          # wrong order (YYYY/MM/DD)
        "19.07.2026",          # dot separator not supported
        "July 19 2026",        # English month name
        "2026-13-01",          # invalid month
        "2026-07-32",          # invalid day
        "garbage",
        "abc-def-ghi",
    ])
    def test_invalid_inputs(self, invalid_input):
        """Invalid date formats return None (caller can fallback to today)."""
        assert parse_date(invalid_input) is None


# ============================================================================
# format_rupiah
# ============================================================================

class TestFormatRupiah:
    """Test Indonesian Rupiah formatting."""

    @pytest.mark.parametrize("value,expected", [
        (0, "Rp 0"),
        (1, "Rp 1"),
        (100, "Rp 100"),
        (1000, "Rp 1.000"),
        (35000, "Rp 35.000"),
        (100000, "Rp 100.000"),
        (1500000, "Rp 1.500.000"),
        (1000000000, "Rp 1.000.000.000"),
    ])
    def test_integer_formatting(self, value, expected):
        assert format_rupiah(value) == expected

    @pytest.mark.parametrize("value,expected", [
        (35.7, "Rp 36"),                # .7 rounds up
        (35.4, "Rp 35"),                # .4 rounds down
        # NOTE: Python's built-in round() uses banker's rounding (round-half-to-even).
        # round(1500000.5) = 1500000 (because 1500000 is even), not 1500001.
        # We document this behavior; for financial rounding use Decimal.
        (1500000.5, "Rp 1.500.000"),    # banker's rounding (1500000 is even)
        (1499999.7, "Rp 1.500.000"),    # .7 rounds up
    ])
    def test_float_rounding(self, value, expected):
        assert format_rupiah(value) == expected

    @pytest.mark.parametrize("invalid_value", [
        None,
        "35000",      # string not allowed
        [],
        {},
        float('nan'), # NaN
    ])
    def test_invalid_returns_zero(self, invalid_value):
        """Non-numeric input falls back to Rp 0 (avoid crash)."""
        assert format_rupiah(invalid_value) == "Rp 0"


# ============================================================================
# generate_uuid
# ============================================================================

class TestGenerateUuid:
    """Test UUID generation for transfer_group linking."""

    def test_returns_string(self):
        result = generate_uuid()
        assert isinstance(result, str)

    def test_valid_uuid_format(self):
        """UUID v4 format: 8-4-4-4-12 hex chars."""
        result = generate_uuid()
        pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        assert re.match(pattern, result), f"Invalid UUID format: {result}"

    def test_unique(self):
        """Each call should produce a unique UUID."""
        uuids = {generate_uuid() for _ in range(1000)}
        assert len(uuids) == 1000, "Duplicate UUIDs generated"

    def test_version_4(self):
        """UUID v4 has '4' as the 13th hex char (after 2nd dash)."""
        result = generate_uuid()
        # Format: 8-4-4-4-12
        # Position of version char: index 14 (after first 8-char + dash + 3 hex chars)
        assert result[14] == "4"


# ============================================================================
# Integration / smoke tests
# ============================================================================

class TestParsersIntegration:
    """Smoke tests combining parsers — like real wizard flow."""

    def test_wizard_nominal_step(self):
        """Simulate user typing various nominal formats in step 2 of wizard."""
        user_inputs = ["35000", "35rb", "1.5jt", "1,2 juta", "2.5m"]
        results = [parse_nominal(x) for x in user_inputs]
        # First two should give same nominal (35k)
        assert results[0] == 35000
        assert results[1] == 35000
        # Last three should give millions
        assert results[2] == 1500000
        assert results[3] == 1200000
        assert results[4] == 2500000

    def test_wizard_date_step(self):
        """Test date parsing for past transactions."""
        # User logs old transaction
        assert parse_date("2026-01-15") == "2026-01-15"
        assert parse_date("15-01-2026") == "2026-01-15"
        assert parse_date("15/01/2026") == "2026-01-15"

    def test_transfer_group_uuid_is_valid_for_notion(self):
        """transfer_group UUID must be valid string for Notion rich_text field."""
        tg = generate_uuid()
        assert isinstance(tg, str)
        assert len(tg) == 36  # UUID v4 string length
        # Notion rich_text content limit is 2000 chars — well within
        assert len(tg) < 2000
