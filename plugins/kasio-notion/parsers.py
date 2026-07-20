"""Parsers for kasio-notion plugin.

Regex-based nominal + date parsers. No LLM dependency (deterministic).
"""

from __future__ import annotations
import re
import uuid
from datetime import datetime, timezone


def parse_nominal(input_str) -> int | None:
    """Parse Indonesian nominal strings.

    Supported formats:
        "35000"        -> 35000
        "35rb"         -> 35000
        "35 ribu"      -> 35000
        "1.5jt"        -> 1500000
        "1,2 juta"     -> 1200000  (koma sebagai desimal separator — Indonesian style)
        "2.3 juta"     -> 2300000
        "1m"           -> 1000000
        "1.000.000"    -> 1000000 (thousand separator)
    Returns: int nominal, or None if unparseable.
    """
    if not isinstance(input_str, str):
        return None
    cleaned = input_str.lower().replace(" ", "")

    # ribu variants: rb, ribu, k
    # Accept both . and , as decimal separator (1.5rb or 1,5rb)
    m = re.match(r"^([\d]+(?:[.,]\d+)?)(rb|ribu|k)$", cleaned)
    if m:
        return round(float(m.group(1).replace(",", ".")) * 1000)

    # juta variants: jt, juta, m
    m = re.match(r"^([\d]+(?:[.,]\d+)?)(jt|juta|m)$", cleaned)
    if m:
        return round(float(m.group(1).replace(",", ".")) * 1_000_000)

    # plain number, optionally with thousand separator dots
    m = re.match(r"^[\d]+(?:\.\d+)*$", cleaned)
    if m:
        raw = m.group(0)
        if "." in raw:
            parts = raw.split(".")
            # Looks like thousand separator: parts[0] 1-3 digits, rest exactly 3
            looks_like_thousand_sep = (
                len(parts) >= 2
                and 1 <= len(parts[0]) <= 3
                and all(len(p) == 3 for p in parts[1:])
            )
            if looks_like_thousand_sep:
                return int("".join(parts))
        return round(float(raw))

    return None


def parse_date(input_str: str) -> str | None:
    """Parse date string to YYYY-MM-DD format.

    Supported:
        "" or None         -> today
        "2026-07-19"       -> 2026-07-19 (ISO)
        "19-07-2026"       -> 2026-07-19 (DD-MM-YYYY)
        "19/07/2026"       -> 2026-07-19
    Returns: YYYY-MM-DD string, or None if unparseable or invalid (e.g. month > 12).
    """
    if not input_str or not input_str.strip():
        return datetime.now(timezone.utc).date().isoformat()
    cleaned = input_str.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", cleaned):
        # Validate month 1-12 and day 1-31 (light check; doesn't catch Feb 30 etc.)
        try:
            year, month, day = int(cleaned[:4]), int(cleaned[5:7]), int(cleaned[8:10])
            if not (1 <= month <= 12 and 1 <= day <= 31):
                return None
            return cleaned
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$", cleaned)
    if m:
        try:
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if not (1 <= month <= 12 and 1 <= day <= 31):
                return None
            return f"{year}-{month:02d}-{day:02d}"
        except ValueError:
            return None
    return None


def format_rupiah(n) -> str:
    """Format number to Indonesian Rupiah display: 35000 -> 'Rp 35.000'"""
    if not isinstance(n, (int, float)) or n != n:  # NaN check
        return "Rp 0"
    return "Rp " + round(n).__format__(",d").replace(",", ".")


def generate_uuid() -> str:
    """Generate UUID v4 string."""
    return str(uuid.uuid4())



