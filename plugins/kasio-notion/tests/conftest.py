"""Pytest fixtures for kasio-notion tests.

Adds parent directory to sys.path so we can import kasio_notion modules
without needing the plugin to be installed as a package.
"""
import sys
import os

# Add parent directory (kasio-notion/) to sys.path so we can import
# parsers, client, vision, constants as top-level modules.
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

# Set fake env vars BEFORE importing client module (it reads at import time)
os.environ.setdefault("NOTION_API_KEY", "ntn_test_fake_key_for_unit_tests")
os.environ.setdefault("KASIO_TRANSACTIONS_DS_ID", "11111111-1111-1111-1111-111111111111")
os.environ.setdefault("KASIO_ACCOUNTS_DS_ID", "22222222-2222-2222-2222-222222222222")
# Use DEFAULT retry delays (1.0s base, 30s max) — tests rely on these.
# If you want fast retries for a specific test, override per-test with monkeypatch.
