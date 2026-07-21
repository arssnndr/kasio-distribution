"""Pytest fixtures for kasio-notion tests.

Adds parent directory to sys.path so we can import kasio_notion modules
without needing the plugin to be installed as a package.
"""
import sys
import os
import types

# Add parent directory (kasio-notion/) to sys.path so we can import
# parsers, client, vision, constants as top-level modules.
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

# Pre-register a stub `client.parsers` so relative imports inside
# client.py (e.g. `from .parsers import parse_date`) resolve when client is
# imported standalone via sys.path injection (the typical test setup).
# We attach the top-level `parsers` module under both `client.parsers` and
# `client.vision` namespaces so any `from .X import Y` in client.py works.
try:
    import parsers as _parsers_top
    import vision as _vision_top
    import client as _client_mod  # noqa: F401 — ensure `client` is loaded
    _client_mod.parsers = _parsers_top
    _client_mod.vision = _vision_top
    sys.modules.setdefault("client.parsers", _parsers_top)
    sys.modules.setdefault("client.vision", _vision_top)
except Exception:
    # If client/parsers/vision can't be imported yet (collection ordering),
    # the relative-import will still fail — tests that hit those branches
    # will report the ImportError clearly instead of crashing the session.
    pass

# Set fake env vars BEFORE importing client module (it reads at import time)
os.environ.setdefault("NOTION_API_KEY", "ntn_test_fake_key_for_unit_tests")
os.environ.setdefault("KASIO_TRANSACTIONS_DS_ID", "11111111-1111-1111-1111-111111111111")
os.environ.setdefault("KASIO_ACCOUNTS_DS_ID", "22222222-2222-2222-2222-222222222222")
# Use DEFAULT retry delays (1.0s base, 30s max) — tests rely on these.
# If you want fast retries for a specific test, override per-test with monkeypatch.
