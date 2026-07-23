"""Unit tests for client.py — retry/backoff logic with mocked httpx.

Uses unittest.mock to simulate Notion API responses without making real HTTP calls.
Tests cover:
  - Successful first-try request
  - Retry on 429 (rate limit)
  - Retry on 5xx server errors
  - No retry on 4xx client errors (other than 429)
  - Retry-After header honored
  - Exhausted retries raises last exception
  - _calculate_backoff exponential growth
  - Jitter is within expected range
"""
from __future__ import annotations
import os
import sys
import time
from unittest.mock import patch, MagicMock, PropertyMock

import httpx
import pytest

# Ensure env vars are set BEFORE importing client (conftest.py also does this,
# but we set here for safety in case this module is run standalone)
os.environ.setdefault("NOTION_API_KEY", "ntn_test_fake_key_for_unit_tests")
os.environ.setdefault("KASIO_TRANSACTIONS_DS_ID", "11111111-1111-1111-1111-111111111111")
os.environ.setdefault("KASIO_ACCOUNTS_DS_ID", "22222222-2222-2222-2222-222222222222")
# NOTE: We deliberately do NOT override KASIO_RETRY_BASE_DELAY / MAX_DELAY here,
# because _calculate_backoff tests assert against the DEFAULT values (1.0 / 30.0).
# Integration tests that need fast retries use monkeypatch per-test.

# Register `parsers` as an attribute of the `client` module so the relative
# import `from .parsers import parse_date` inside client.py resolves when
# client is imported standalone (not as part of the kasio_notion package).
# Without this, TestUpdateTransaction.test_*_routes_to_* that exercise
# `tanggal` / `jumlah` paths crash with ImportError. Conftest.py already
# adds the plugin dir to sys.path so `import parsers` works as top-level;
# we then expose it as a submodule so the relative-import lookup succeeds.
import parsers as _parsers_module  # noqa: E402
import client as _client_module     # noqa: E402
_client_module.parsers = _parsers_module
sys.modules.setdefault("client.parsers", _parsers_module)

from client import NotionClient, _calculate_backoff, MAX_RETRIES, RETRYABLE_STATUS_CODES


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_httpx_client():
    """Patch httpx.Client used inside NotionClient to return a mock."""
    with patch("client.httpx.Client") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def notion_client(mock_httpx_client):
    """Create NotionClient instance with mocked httpx."""
    return NotionClient()


@pytest.fixture
def reset_refresh_state():
    """Clear the debounce state before each test that uses it.

    The debounce timer and pending count are module-level globals; if a
    prior test left a timer running or a non-zero count, subsequent tests
    would see leftover state and fail.
    """
    import client as client_mod
    with client_mod._refresh_lock:
        if client_mod._refresh_timer is not None:
            client_mod._refresh_timer.cancel()
            client_mod._refresh_timer = None
        client_mod._pending_mutation_count = 0
    yield
    # Cleanup after test
    with client_mod._refresh_lock:
        if client_mod._refresh_timer is not None:
            client_mod._refresh_timer.cancel()
            client_mod._refresh_timer = None
        client_mod._pending_mutation_count = 0


# ============================================================================
# _calculate_backoff
# ============================================================================

class TestCalculateBackoff:
    """Test exponential backoff calculation."""

    def test_first_retry(self):
        """First retry (attempt=0) base delay ~ 1.0s with ±25% jitter."""
        delay = _calculate_backoff(0, retry_after=None)
        # base 1.0, jitter ±25% → range [0.75, 1.25]
        assert 0.75 <= delay <= 1.25

    def test_exponential_growth(self):
        """Subsequent retries roughly double (with jitter)."""
        d0 = _calculate_backoff(0)
        d1 = _calculate_backoff(1)
        d2 = _calculate_backoff(2)
        # Average should double: ~1, ~2, ~4
        assert 0.75 <= d0 <= 1.25
        assert 1.5 <= d1 <= 2.5
        assert 3.0 <= d2 <= 5.0

    def test_max_delay_cap(self):
        """Very high attempt counts are capped at MAX_DELAY_SEC (default 30s)."""
        delay = _calculate_backoff(20)  # 2^20 = ~1M, way above cap
        # MAX_DELAY_SEC default is 30.0, with ±25% jitter → max ~37.5
        assert delay <= 30.0 * 1.25

    def test_retry_after_overrides(self):
        """Server-provided Retry-After should override exponential backoff."""
        delay = _calculate_backoff(0, retry_after="10")
        assert delay == 10.0

    def test_retry_after_with_jitter(self):
        """Wait — Retry-After is server directive, no jitter added. Let me re-check."""
        # Note: current impl uses Retry-After as-is (no jitter). That's safer.
        delay = _calculate_backoff(2, retry_after="5")
        assert delay == 5.0

    def test_invalid_retry_after_falls_back(self):
        """Non-numeric Retry-After falls back to exponential."""
        delay = _calculate_backoff(0, retry_after="not-a-number")
        assert 0.75 <= delay <= 1.25

    def test_minimum_delay(self):
        """Delay never drops below 0.1s (avoid tight retry loops)."""
        delay = _calculate_backoff(0)
        assert delay >= 0.1


# ============================================================================
# Retryable status codes
# ============================================================================

class TestRetryableStatusCodes:
    """Test the set of HTTP status codes that trigger retry."""

    def test_includes_rate_limit(self):
        assert 429 in RETRYABLE_STATUS_CODES

    def test_includes_server_errors(self):
        for code in (500, 502, 503, 504):
            assert code in RETRYABLE_STATUS_CODES

    def test_excludes_client_errors(self):
        """4xx (except 429) should NOT retry — request is bad."""
        assert 400 not in RETRYABLE_STATUS_CODES
        assert 401 not in RETRYABLE_STATUS_CODES
        assert 403 not in RETRYABLE_STATUS_CODES
        assert 404 not in RETRYABLE_STATUS_CODES

    def test_excludes_success(self):
        assert 200 not in RETRYABLE_STATUS_CODES
        assert 201 not in RETRYABLE_STATUS_CODES


# ============================================================================
# NotionClient._request_with_retry — success cases
# ============================================================================

class TestRequestWithRetrySuccess:
    """Tests for successful (no retry) requests."""

    def test_200_returns_response(self, notion_client, mock_httpx_client):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        mock_httpx_client.request.return_value = mock_resp

        result = notion_client._request_with_retry("GET", "/test")

        assert result.status_code == 200
        assert mock_httpx_client.request.call_count == 1

    def test_201_returns_response(self, notion_client, mock_httpx_client):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"created": True}
        mock_httpx_client.request.return_value = mock_resp

        result = notion_client._request_with_retry("POST", "/pages", json={})
        assert result.status_code == 201

    def test_non_retryable_4xx_raises_immediately(self, notion_client, mock_httpx_client):
        """400/401/403/404 should raise without retry."""
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 401
        # Mock raise_for_status to raise HTTPStatusError
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized", request=MagicMock(), response=mock_resp,
        )
        mock_httpx_client.request.return_value = mock_resp

        with pytest.raises(httpx.HTTPStatusError):
            notion_client._request_with_retry("GET", "/test")

        # Only 1 attempt — no retry on 401
        assert mock_httpx_client.request.call_count == 1


# ============================================================================
# NotionClient._request_with_retry — retry cases
# ============================================================================

class TestRequestWithRetryOnRetryable:
    """Tests for retry behavior on 429 and 5xx."""

    def test_429_retries_then_succeeds(self, notion_client, mock_httpx_client):
        """First attempt 429, second attempt 200."""
        # First call: 429
        rate_limited = MagicMock(spec=httpx.Response)
        rate_limited.status_code = 429
        rate_limited.reason_phrase = "Too Many Requests"
        rate_limited.headers = {}

        # Second call: success
        success = MagicMock(spec=httpx.Response)
        success.status_code = 200
        success.json.return_value = {"ok": True}

        mock_httpx_client.request.side_effect = [rate_limited, success]

        result = notion_client._request_with_retry("GET", "/test")

        assert result.status_code == 200
        assert mock_httpx_client.request.call_count == 2

    def test_500_retries_then_succeeds(self, notion_client, mock_httpx_client):
        """First attempt 500, second attempt 200."""
        server_error = MagicMock(spec=httpx.Response)
        server_error.status_code = 500
        server_error.reason_phrase = "Internal Server Error"
        server_error.headers = {}

        success = MagicMock(spec=httpx.Response)
        success.status_code = 200

        mock_httpx_client.request.side_effect = [server_error, success]

        result = notion_client._request_with_retry("GET", "/test")
        assert result.status_code == 200
        assert mock_httpx_client.request.call_count == 2

    @pytest.mark.parametrize("status_code", [500, 502, 503, 504])
    def test_all_5xx_codes_retry(self, notion_client, mock_httpx_client, status_code):
        """All common 5xx codes should trigger retry."""
        error_resp = MagicMock(spec=httpx.Response)
        error_resp.status_code = status_code
        error_resp.reason_phrase = "Error"
        error_resp.headers = {}

        success = MagicMock(spec=httpx.Response)
        success.status_code = 200

        mock_httpx_client.request.side_effect = [error_resp, success]

        notion_client._request_with_retry("GET", "/test")
        assert mock_httpx_client.request.call_count == 2

    def test_exhausted_retries_raises(self, notion_client, mock_httpx_client):
        """All MAX_RETRIES + 1 attempts fail with 429 → raise last error."""
        rate_limited = MagicMock(spec=httpx.Response)
        rate_limited.status_code = 429
        rate_limited.reason_phrase = "Too Many Requests"
        rate_limited.headers = {}
        # Configure raise_for_status to raise HTTPStatusError on 429
        rate_limited.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429 Too Many Requests", request=MagicMock(), response=rate_limited,
        )

        mock_httpx_client.request.return_value = rate_limited

        with pytest.raises(httpx.HTTPStatusError):
            notion_client._request_with_retry("GET", "/test")

        # MAX_RETRIES + 1 total attempts
        assert mock_httpx_client.request.call_count == MAX_RETRIES + 1

    def test_retry_after_header_honored(self, notion_client, mock_httpx_client):
        """Retry-After header value should be used as delay."""
        rate_limited = MagicMock(spec=httpx.Response)
        rate_limited.status_code = 429
        rate_limited.reason_phrase = "Too Many Requests"
        rate_limited.headers = {"Retry-After": "0.05"}

        success = MagicMock(spec=httpx.Response)
        success.status_code = 200

        mock_httpx_client.request.side_effect = [rate_limited, success]

        start = time.monotonic()
        notion_client._request_with_retry("GET", "/test")
        elapsed = time.monotonic() - start

        # Should wait ~0.05s for Retry-After (not exponential backoff ~0.01)
        # Allow generous range for timing variance in tests
        assert 0.04 <= elapsed <= 0.5

    def test_succeeds_after_multiple_retries(self, notion_client, mock_httpx_client):
        """Should keep retrying until success, even if takes all retries."""
        # First 3 fail, 4th succeeds
        failures = [
            MagicMock(spec=httpx.Response) for _ in range(MAX_RETRIES)
        ]
        for f in failures:
            f.status_code = 429
            f.reason_phrase = "Too Many Requests"
            f.headers = {}

        success = MagicMock(spec=httpx.Response)
        success.status_code = 200

        mock_httpx_client.request.side_effect = failures + [success]

        result = notion_client._request_with_retry("GET", "/test")
        assert result.status_code == 200
        assert mock_httpx_client.request.call_count == MAX_RETRIES + 1


# ============================================================================
# NotionClient._post / _patch / _get
# ============================================================================

class TestClientMethods:
    """Test the HTTP method wrappers."""

    def test_post_returns_json(self, notion_client, mock_httpx_client):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "page-123"}
        mock_httpx_client.request.return_value = mock_resp

        result = notion_client._post("/pages", {"foo": "bar"})

        assert result == {"id": "page-123"}
        # Verify method/args
        call = mock_httpx_client.request.call_args
        assert call[0][0] == "POST"
        assert call[0][1] == "/pages"
        assert call[1]["json"] == {"foo": "bar"}

    def test_patch_returns_json(self, notion_client, mock_httpx_client):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"updated": True}
        mock_httpx_client.request.return_value = mock_resp

        result = notion_client._patch("/pages/abc", {"archived": True})

        assert result == {"updated": True}
        call = mock_httpx_client.request.call_args
        assert call[0][0] == "PATCH"

    def test_get_returns_json(self, notion_client, mock_httpx_client):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": []}
        mock_httpx_client.request.return_value = mock_resp

        result = notion_client._get("/users/me")

        assert result == {"results": []}
        call = mock_httpx_client.request.call_args
        assert call[0][0] == "GET"


# ============================================================================
# Initialization
# ============================================================================

class TestNotionClientInit:
    """Test NotionClient initialization and env validation."""

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("NOTION_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="NOTION_API_KEY"):
            NotionClient()

    def test_placeholder_api_key_raises(self, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", "your_key_here")
        with pytest.raises(RuntimeError, match="NOTION_API_KEY"):
            NotionClient()

    def test_missing_transactions_ds_id_raises(self, monkeypatch):
        monkeypatch.setenv("KASIO_TRANSACTIONS_DS_ID", "")
        with pytest.raises(RuntimeError, match="KASIO_TRANSACTIONS_DS_ID"):
            NotionClient()

    def test_missing_accounts_ds_id_raises(self, monkeypatch):
        monkeypatch.setenv("KASIO_ACCOUNTS_DS_ID", "")
        with pytest.raises(RuntimeError, match="KASIO_ACCOUNTS_DS_ID"):
            NotionClient()

    def test_valid_env_initializes(self, mock_httpx_client, monkeypatch):
        # Drop any pre-set env vars from the host shell (real ~/.hermes/.env
        # leaks into pytest via parent env); then re-set our test fakes
        # through monkeypatch so they auto-revert on teardown.
        for var in ("NOTION_API_KEY", "KASIO_TRANSACTIONS_DS_ID",
                    "KASIO_ACCOUNTS_DS_ID"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("NOTION_API_KEY", "ntn_test_fake_key_for_unit_tests")
        monkeypatch.setenv("KASIO_TRANSACTIONS_DS_ID", "11111111-1111-1111-1111-111111111111")
        monkeypatch.setenv("KASIO_ACCOUNTS_DS_ID", "22222222-2222-2222-2222-222222222222")
        # Re-import client so module-level os.environ reads pick up the
        # patched values. Patch sys.modules then re-import the module to
        # re-execute the module-level env reads.
        import importlib
        import client as _client_mod
        importlib.reload(_client_mod)
        client = _client_mod.NotionClient()
        assert client.api_key == "ntn_test_fake_key_for_unit_tests"
        assert client.transactions_ds_id == "11111111-1111-1111-1111-111111111111"
        assert client.accounts_ds_id == "22222222-2222-2222-2222-222222222222"


# ============================================================================
# NotionClient.update_transaction — field routing (regression tests)
# ============================================================================
#
# These tests guard against the bug where `rekening_id` was silently dropped
# by update_transaction because TX_PROP uses "rekening" as the key mapped to
# the Notion relation field. Reproducer: passing
#   update_transaction(tx_id, {"rekening_id": "..."})
# left Notion DB unchanged and the returned parse_transaction still showed
# the old rekening_id. Fix accepts both "rekening" and "rekening_id" and
# routes to TX_PROP["rekening"]. See commit 037e61a.


def _mock_notion_page_response(page_id: str, **props) -> dict:
        """Build a minimal Notion page response with the given property values.

        Only the properties the code under test reads are populated; everything
        else uses Notion-style empty containers so parse_transaction doesn't
        blow up.
        """
        def _title(content: str) -> list:
            return [{"type": "text", "text": {"content": content},
                     "annotations": {"bold": False, "italic": False, "strikethrough": False,
                                    "underline": False, "code": False, "color": "default"},
                     "plain_text": content, "href": None}]

        def _rich_text(content: str) -> list:
            return [{"type": "text", "text": {"content": content}, "plain_text": content,
                     "annotations": {"bold": False, "italic": False, "strikethrough": False,
                                    "underline": False, "code": False, "color": "default"},
                     "href": None}]

        def _date(content: str) -> dict:
            return {"date": {"start": content, "end": None, "time_zone": None}}

        def _select(name: str) -> dict:
            return {"select": {"id": f"sel-{name}", "name": name, "color": "default"}}

        def _number(n) -> dict:
            return {"number": n}

        def _relation(*ids: str) -> dict:
            return {"relation": [{"id": i} for i in ids]}

        properties = {
            "Nama": {"id": "title", "type": "title", "title": _title(props.get("nama", ""))},
            "Angka": {"id": "angka", "type": "number", "number": props.get("jumlah")},
            "Tipe": {"id": "tipe", "type": "select", **_select(props.get("tipe", ""))},
            "Kategori": {"id": "kategori", "type": "select", **_select(props.get("kategori", ""))},
            "Tanggal": {"id": "tanggal", "type": "date", **_date(props.get("tanggal", "1970-01-01"))},
            "Catatan": {"id": "catatan", "type": "rich_text", "rich_text": _rich_text(props.get("catatan", ""))},
            "Rekening": {"id": "rekening", "type": "relation", **_relation(*props.get("rekening_ids", []))},
            "Transfer Group ID": {"id": "tg", "type": "rich_text", "rich_text": _rich_text(props.get("transfer_group", ""))},
        }
        return {"object": "page", "id": page_id, "properties": properties, "archived": False}


class TestUpdateTransaction:
    """Regression tests for update_transaction() field routing."""

    TX_PAGE_ID = "3a4ac553-4df0-8153-a88e-f56e01d21353"
    NEW_ACC_ID = "3a2ac553-4df0-8186-8d06-cd0d5d044248"  # Gopay

    def _patch_with_response(self, notion_client, mock_httpx_client, response_page):
        """Set up mock to return a PATCH response with the given page dict."""
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = response_page
        mock_resp.raise_for_status.return_value = None
        mock_httpx_client.request.return_value = mock_resp

    def test_rekening_id_routes_to_relation_field(self, notion_client, mock_httpx_client):
        """regression: rekening_id input must reach Notion as the 'Rekening' relation.

        Bug pre-fix: properties dict had no Rekening entry because
        TX_PROP.get('rekening_id') returned None and the field was dropped.
        """
        page_response = _mock_notion_page_response(
            self.TX_PAGE_ID,
            nama="Token PLN",
            jumlah=51900,
            tipe="Pengeluaran",
            kategori="Tagihan",
            tanggal="2026-07-21",
            rekening_ids=[self.NEW_ACC_ID],
        )
        self._patch_with_response(notion_client, mock_httpx_client, page_response)

        notion_client.update_transaction(
            self.TX_PAGE_ID, {"rekening_id": self.NEW_ACC_ID}
        )

        # Inspect the actual PATCH body sent to Notion.
        mock_httpx_client.request.assert_called_once()
        kwargs = mock_httpx_client.request.call_args.kwargs
        sent_json = kwargs.get("json", {})
        sent_props = sent_json.get("properties", {})
        assert "Rekening" in sent_props, (
            "BUG: rekening_id input was silently dropped — 'Rekening' "
            "not present in PATCH body sent to Notion"
        )
        assert sent_props["Rekening"] == {"relation": [{"id": self.NEW_ACC_ID}]}

    def test_rekening_alias_also_routes(self, notion_client, mock_httpx_client):
        """The 'rekening' alias (TX_PROP key) must work too."""
        page_response = _mock_notion_page_response(
            self.TX_PAGE_ID, rekening_ids=[self.NEW_ACC_ID]
        )
        self._patch_with_response(notion_client, mock_httpx_client, page_response)

        notion_client.update_transaction(
            self.TX_PAGE_ID, {"rekening": self.NEW_ACC_ID}
        )

        sent_props = mock_httpx_client.request.call_args.kwargs["json"]["properties"]
        assert sent_props["Rekening"] == {"relation": [{"id": self.NEW_ACC_ID}]}

    def test_catatan_routes_to_rich_text(self, notion_client, mock_httpx_client):
        """Catatan is rich_text — verify correct Notion property format."""
        page_response = _mock_notion_page_response(self.TX_PAGE_ID, catatan="hello")
        self._patch_with_response(notion_client, mock_httpx_client, page_response)

        notion_client.update_transaction(
            self.TX_PAGE_ID, {"catatan": "hello world"}
        )
        sent_props = mock_httpx_client.request.call_args.kwargs["json"]["properties"]
        assert sent_props["Catatan"] == {"rich_text": [{"text": {"content": "hello world"}}]}

    def test_jumlah_routes_to_number(self, notion_client, mock_httpx_client):
        """Jumlah is a Number — verify float conversion."""
        page_response = _mock_notion_page_response(self.TX_PAGE_ID, jumlah=12345)
        self._patch_with_response(notion_client, mock_httpx_client, page_response)

        notion_client.update_transaction(self.TX_PAGE_ID, {"jumlah": 12345})
        sent_props = mock_httpx_client.request.call_args.kwargs["json"]["properties"]
        assert sent_props["Angka"] == {"number": 12345.0}

    def test_tipe_routes_to_select(self, notion_client, mock_httpx_client):
        """Tipe is a Select — verify wrapped in select.name."""
        page_response = _mock_notion_page_response(self.TX_PAGE_ID, tipe="Pemasukan")
        self._patch_with_response(notion_client, mock_httpx_client, page_response)

        notion_client.update_transaction(self.TX_PAGE_ID, {"tipe": "Pemasukan"})
        sent_props = mock_httpx_client.request.call_args.kwargs["json"]["properties"]
        assert sent_props["Tipe"] == {"select": {"name": "Pemasukan"}}

    def test_kategori_routes_to_select(self, notion_client, mock_httpx_client):
        """Kategori is a Select — same pattern as Tipe."""
        page_response = _mock_notion_page_response(self.TX_PAGE_ID, kategori="Makanan & Minuman")
        self._patch_with_response(notion_client, mock_httpx_client, page_response)

        notion_client.update_transaction(self.TX_PAGE_ID, {"kategori": "Makanan & Minuman"})
        sent_props = mock_httpx_client.request.call_args.kwargs["json"]["properties"]
        assert sent_props["Kategori"] == {"select": {"name": "Makanan & Minuman"}}

    def test_tanggal_routes_to_date(self, notion_client, mock_httpx_client):
        """Tanggal is a Date — verify wrapped in date.start."""
        page_response = _mock_notion_page_response(self.TX_PAGE_ID, tanggal="2026-07-21")
        self._patch_with_response(notion_client, mock_httpx_client, page_response)

        notion_client.update_transaction(self.TX_PAGE_ID, {"tanggal": "2026-07-21"})
        sent_props = mock_httpx_client.request.call_args.kwargs["json"]["properties"]
        assert sent_props["Tanggal"] == {"date": {"start": "2026-07-21"}}

    def test_nama_routes_to_title(self, notion_client, mock_httpx_client):
        """Nama is the Title property — verify title array format."""
        page_response = _mock_notion_page_response(self.TX_PAGE_ID, nama="Sarapan")
        self._patch_with_response(notion_client, mock_httpx_client, page_response)

        notion_client.update_transaction(self.TX_PAGE_ID, {"nama": "Sarapan"})
        sent_props = mock_httpx_client.request.call_args.kwargs["json"]["properties"]
        assert sent_props["Nama"] == {"title": [{"text": {"content": "Sarapan"}}]}

    def test_transfer_group_routes_to_rich_text(self, notion_client, mock_httpx_client):
        """Transfer Group ID is a rich_text field."""
        page_response = _mock_notion_page_response(self.TX_PAGE_ID, transfer_group="abc-123")
        self._patch_with_response(notion_client, mock_httpx_client, page_response)

        notion_client.update_transaction(
            self.TX_PAGE_ID, {"transfer_group": "abc-123"}
        )
        sent_props = mock_httpx_client.request.call_args.kwargs["json"]["properties"]
        assert sent_props["Transfer Group ID"] == {"rich_text": [{"text": {"content": "abc-123"}}]}

    def test_unknown_key_is_silently_dropped(self, notion_client, mock_httpx_client):
        """Keys not in TX_PROP should not produce any Notion property entry."""
        page_response = _mock_notion_page_response(self.TX_PAGE_ID, catatan="x")
        self._patch_with_response(notion_client, mock_httpx_client, page_response)

        notion_client.update_transaction(
            self.TX_PAGE_ID,
            {"unknown_field": "value", "another": 42},
        )
        sent_props = mock_httpx_client.request.call_args.kwargs["json"]["properties"]
        # Only unknown keys should be dropped; if everything is dropped the
        # body is still valid (empty properties) — Notion accepts that.
        assert "unknown_field" not in sent_props
        assert "another" not in sent_props
        assert sent_props == {}

    def test_multiple_fields_in_one_call(self, notion_client, mock_httpx_client):
        """Verify multiple updates in one call all reach the PATCH body."""
        page_response = _mock_notion_page_response(
            self.TX_PAGE_ID,
            nama="Updated",
            catatan="multi",
            rekening_ids=[self.NEW_ACC_ID],
        )
        self._patch_with_response(notion_client, mock_httpx_client, page_response)

        notion_client.update_transaction(self.TX_PAGE_ID, {
            "nama": "Updated",
            "catatan": "multi",
            "rekening_id": self.NEW_ACC_ID,
        })

        sent_props = mock_httpx_client.request.call_args.kwargs["json"]["properties"]
        assert sent_props["Nama"] == {"title": [{"text": {"content": "Updated"}}]}
        assert sent_props["Catatan"] == {"rich_text": [{"text": {"content": "multi"}}]}
        assert sent_props["Rekening"] == {"relation": [{"id": self.NEW_ACC_ID}]}

    def test_parse_transaction_returns_routed_rekening(self, notion_client, mock_httpx_client):
        """After update with rekening_id, the returned parse_transaction shows the new id."""
        page_response = _mock_notion_page_response(
            self.TX_PAGE_ID,
            nama="Token PLN",
            rekening_ids=[self.NEW_ACC_ID],
        )
        self._patch_with_response(notion_client, mock_httpx_client, page_response)

        result = notion_client.update_transaction(
            self.TX_PAGE_ID, {"rekening_id": self.NEW_ACC_ID}
        )

        # Without the fix, result would have rekening_id == None or stale value
        # because parse_transaction re-reads the (unchanged) Notion page.
        assert result.get("rekening_id") == self.NEW_ACC_ID


class TestListAccountsSaldoLive:
    """Tests for list_accounts() attaching computed `saldo_live` to each
    account dict. saldo_live = saldo_awal + Σ(Pemasukan) − Σ(Pengeluaran).
    See client._attach_saldo_live."""

    def _mock_account_page(self, page_id, nama, saldo_awal, status="Aktif", archived=False, urutan=None):
        return {
            "object": "page", "id": page_id, "archived": archived,
            "properties": {
                "Nama": {"type": "title", "title": [{"type": "text", "text": {"content": nama}, "plain_text": nama}]},
                "Saldo Awal": {"type": "number", "number": saldo_awal},
                "Status": {"type": "select", "select": {"name": status}},
                "Urutan": {"type": "number", "number": urutan},
                "Ikon": {"type": "rich_text", "rich_text": []},
                "Nomor Rekening": {"type": "rich_text", "rich_text": []},
            },
        }

    def _mock_tx_page(self, page_id, rekening_id, tipe, jumlah, archived=False):
        rel_field = {"relation": [{"id": rekening_id}]} if rekening_id else {"relation": []}
        return {
            "object": "page", "id": page_id, "archived": archived,
            "properties": {
                "Nama": {"type": "title", "title": []},
                "Angka": {"type": "number", "number": jumlah},
                "Tipe": {"type": "select", "select": {"name": tipe}},
                "Kategori": {"type": "select", "select": {"name": "Lainnya"}},
                "Tanggal": {"type": "date", "date": {"start": "2026-07-21"}},
                "Catatan": {"type": "rich_text", "rich_text": []},
                "Rekening": {"type": "relation", **rel_field},
                "Transfer Group ID": {"type": "rich_text", "rich_text": []},
            },
        }

    def _setup_paginated_query(self, mock_httpx_client, account_pages, tx_pages):
        """Mock Notion to return given pages from /query endpoint based on path."""
        def fake_post(url, *args, **kwargs):
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 200
            if "/data_sources/" in url and "/query" in url:
                # Return accounts or transactions based on body property names
                body = kwargs.get("json", {})
                if "Nama" in str(body) or body == {}:
                    pass  # default
                # Heuristic: check if request body has 'Nama' style props
                # Easier: check URL contains accounts or transactions DS
                # We rely on the fact that data source IDs are different.
                # The mock fixture uses accounts_ds_id; tx uses transactions_ds_id.
                if "accounts" in url.lower() or (account_pages and "/query" in url):
                    pass
                return resp
            resp.raise_for_status = lambda: None
            return resp
        # Simpler: just return based on DS in URL
        def route_based_post(url, *args, **kwargs):
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 200
            resp.raise_for_status = lambda: None
            from urllib.parse import urlparse
            parsed = urlparse(url)
            path = parsed.path
            if self.TX_DS_ID in path:
                resp.json.return_value = {"results": tx_pages, "has_more": False}
            else:
                resp.json.return_value = {"results": account_pages, "has_more": False}
            return resp
        # We need to make mock_httpx_client.request return based on URL
        # Create separate response objects and use side_effect
        accounts_resp = MagicMock(spec=httpx.Response)
        accounts_resp.status_code = 200
        accounts_resp.json.return_value = {"results": account_pages, "has_more": False}
        accounts_resp.raise_for_status = lambda: None

        tx_resp = MagicMock(spec=httpx.Response)
        tx_resp.status_code = 200
        tx_resp.json.return_value = {"results": tx_pages, "has_more": False}
        tx_resp.raise_for_status = lambda: None

        # mock_httpx_client.request returns one for each call
        responses = [accounts_resp, tx_resp]
        mock_httpx_client.request.side_effect = responses

    TX_DS_ID = "11111111-1111-1111-1111-111111111111"
    ACC_DS_ID = "22222222-2222-2222-2222-222222222222"

    def test_saldo_live_reflects_saldo_awal_when_no_transactions(
        self, notion_client, mock_httpx_client
    ):
        """Empty transactions DB → saldo_live == saldo_awal for all accounts."""
        cash_page = self._mock_account_page("acc-cash", "Cash", 194000, urutan=3)
        bc_page = self._mock_account_page("acc-bca", "BCA", 33456, urutan=2)
        self._setup_paginated_query(mock_httpx_client, [bc_page, cash_page], [])

        result = notion_client.list_accounts()

        by_name = {a["nama"]: a for a in result}
        assert by_name["Cash"]["saldo_awal"] == 194000
        assert by_name["Cash"]["saldo_live"] == 194000
        assert by_name["BCA"]["saldo_awal"] == 33456
        assert by_name["BCA"]["saldo_live"] == 33456

    def test_saldo_live_subtracts_pengeluaran(
        self, notion_client, mock_httpx_client
    ):
        """saldo_live = saldo_awal − sum(Pengeluaran) for an account."""
        cash_page = self._mock_account_page("acc-cash", "Cash", 194000, urutan=1)
        tx1 = self._mock_tx_page("tx-1", "acc-cash", "Pengeluaran", 50000)
        tx2 = self._mock_tx_page("tx-2", "acc-cash", "Pengeluaran", 22000)
        self._setup_paginated_query(mock_httpx_client, [cash_page], [tx1, tx2])

        result = notion_client.list_accounts()
        cash = next(a for a in result if a["nama"] == "Cash")
        assert cash["saldo_awal"] == 194000
        assert cash["saldo_live"] == 194000 - 50000 - 22000  # 122000

    def test_saldo_live_adds_pemasukan(
        self, notion_client, mock_httpx_client
    ):
        """saldo_live includes +Σ(Pemasukan)."""
        cash_page = self._mock_account_page("acc-cash", "Cash", 100000, urutan=1)
        tx1 = self._mock_tx_page("tx-1", "acc-cash", "Pemasukan", 50000)
        tx2 = self._mock_tx_page("tx-2", "acc-cash", "Pengeluaran", 30000)
        self._setup_paginated_query(mock_httpx_client, [cash_page], [tx1, tx2])

        result = notion_client.list_accounts()
        cash = next(a for a in result if a["nama"] == "Cash")
        # 100000 + 50000 - 30000 = 120000
        assert cash["saldo_live"] == 120000

    def test_saldo_live_per_account_isolation(
        self, notion_client, mock_httpx_client
    ):
        """Each account's saldo_live is independent — tx on Cash doesn't
        affect BCA saldo_live."""
        cash_page = self._mock_account_page("acc-cash", "Cash", 100000, urutan=2)
        bc_page = self._mock_account_page("acc-bca", "BCA", 33456, urutan=1)
        tx_cash = self._mock_tx_page("tx-1", "acc-cash", "Pengeluaran", 70000)
        self._setup_paginated_query(mock_httpx_client, [bc_page, cash_page], [tx_cash])

        result = notion_client.list_accounts()
        cash = next(a for a in result if a["nama"] == "Cash")
        bca = next(a for a in result if a["nama"] == "BCA")
        assert cash["saldo_live"] == 30000
        assert bca["saldo_live"] == 33456  # unaffected

    def test_saldo_live_ignores_archived_transactions(
        self, notion_client, mock_httpx_client
    ):
        """Archived (soft-deleted) transactions are excluded from saldo_live."""
        cash_page = self._mock_account_page("acc-cash", "Cash", 100000, urutan=1)
        tx_live = self._mock_tx_page("tx-1", "acc-cash", "Pengeluaran", 30000, archived=False)
        tx_archived = self._mock_tx_page("tx-2", "acc-cash", "Pengeluaran", 40000, archived=True)
        self._setup_paginated_query(mock_httpx_client, [cash_page], [tx_live, tx_archived])

        result = notion_client.list_accounts()
        cash = next(a for a in result if a["nama"] == "Cash")
        # 100000 - 30000 = 70000 (the archived 40000 should be ignored)
        assert cash["saldo_live"] == 70000

    def test_saldo_live_ignores_transactions_to_other_accounts(
        self, notion_client, mock_httpx_client
    ):
        """Transactions tied to other accounts must not affect saldo_live."""
        cash_page = self._mock_account_page("acc-cash", "Cash", 100000, urutan=1)
        bc_page = self._mock_account_page("acc-bca", "BCA", 50000, urutan=2)
        # Tx to BCA, not Cash
        tx_bc = self._mock_tx_page("tx-1", "acc-bca", "Pengeluaran", 20000)
        self._setup_paginated_query(mock_httpx_client, [bc_page, cash_page], [tx_bc])

        result = notion_client.list_accounts()
        cash = next(a for a in result if a["nama"] == "Cash")
        bca = next(a for a in result if a["nama"] == "BCA")
        assert cash["saldo_live"] == 100000  # unaffected
        assert bca["saldo_live"] == 30000

    def test_saldo_live_ignores_transactions_with_no_rekening(
        self, notion_client, mock_httpx_client
    ):
        """Edge case: tx with rekening_id missing or empty should not crash
        and should not contribute to any account."""
        cash_page = self._mock_account_page("acc-cash", "Cash", 50000, urutan=1)
        tx_no_rekening = self._mock_tx_page("tx-orphan", None, "Pengeluaran", 99999)
        self._setup_paginated_query(mock_httpx_client, [cash_page], [tx_no_rekening])

        result = notion_client.list_accounts()
        cash = next(a for a in result if a["nama"] == "Cash")
        assert cash["saldo_live"] == 50000

    def test_saldo_live_for_archived_accounts_excluded(
        self, notion_client, mock_httpx_client
    ):
        """When include_archived=False (default), archived accounts are
        filtered out before saldo_live is attached. The remaining accounts
        still get saldo_live correctly."""
        cash_page = self._mock_account_page("acc-cash", "Cash", 100000, urutan=1, archived=False)
        old_page = self._mock_account_page("acc-old", "Lama", 50000, urutan=99, archived=True)
        tx = self._mock_tx_page("tx-1", "acc-cash", "Pengeluaran", 20000)
        self._setup_paginated_query(mock_httpx_client, [old_page, cash_page], [tx])

        result = notion_client.list_accounts(include_archived=False)
        assert all(a["nama"] != "Lama" for a in result)
        cash = next(a for a in result if a["nama"] == "Cash")
        assert cash["saldo_live"] == 80000

    def test_saldo_live_with_zero_transactions(
        self, notion_client, mock_httpx_client
    ):
        """Brand new account, no transactions yet → saldo_live == saldo_awal."""
        new_page = self._mock_account_page("acc-new", "BankBaru", 25000, urutan=10)
        self._setup_paginated_query(mock_httpx_client, [new_page], [])

        result = notion_client.list_accounts()
        new = next(a for a in result if a["nama"] == "BankBaru")
        assert new["saldo_live"] == 25000

    def test_saldo_live_with_negative_balance(
        self, notion_client, mock_httpx_client
    ):
        """saldo_live can go negative (overdraft scenario)."""
        cash_page = self._mock_account_page("acc-cash", "Cash", 50000, urutan=1)
        tx_big = self._mock_tx_page("tx-1", "acc-cash", "Pengeluaran", 75000)
        self._setup_paginated_query(mock_httpx_client, [cash_page], [tx_big])

        result = notion_client.list_accounts()
        cash = next(a for a in result if a["nama"] == "Cash")
        assert cash["saldo_live"] == -25000  # overdraft OK


class TestSaveTransaction:
    """Regression tests for save_transaction().

    Ensures that save_transaction correctly serializes the rekening_id
    parameter into the Notion 'Rekening' relation field. A previous
    session saw a stale-pycache incident where the runtime plugin
    returned `rekening_id: null` even though the relation was set in
    source code — these tests pin that path so any future regression
    shows up immediately.

    Also covers: jumlah coercion, tipe/kategori select mapping, catatan
    rich_text, transfer_group, parent shape, and missing rekening_id.
    """

    NEW_TX_PAGE_ID = "new-tx-page-001"
    ACC_ID = "3a2ac553-4df0-81a7-b276-d6e93ae3a777"

    def _post_with_response(self, mock_httpx_client, response_page):
        """Configure mock_httpx_client so the next POST returns response_page.

        Note: no `spec=httpx.Response` here. parse_transaction calls
        `page.get(...)` (dict access), so the mock must accept .get().
        Using spec=httpx.Response would lock the mock to httpx.Response's
        actual API and reject dict-like access.
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = response_page
        mock_resp.raise_for_status.return_value = None
        mock_httpx_client.request.return_value = mock_resp

    def test_rekening_id_serializes_to_relation_field(
        self, notion_client, mock_httpx_client
    ):
        """REGRESSION: rekening_id must become Rekening.relation in POST body.

        This is the exact bug we caught in the live session (22 Jul 2026):
        save_transaction returned rekening_id=null even though source code
        clearly sets the relation field. Pin this path so any regression
        (stale pycache, monkey-patch, schema drift) fails loudly.
        """
        response_page = _mock_notion_page_response(
            self.NEW_TX_PAGE_ID,
            nama="Regression: rekening_id",
            jumlah=10000,
            tipe="Pengeluaran",
            kategori="Lainnya",
            tanggal="2026-07-22",
            rekening_ids=[self.ACC_ID],
        )
        self._post_with_response(mock_httpx_client, response_page)

        captured_body = {}

        def capture_post(self, path, body=None):
            captured_body["body"] = body
            # Return response_page directly (already JSON-parsed dict), not
            # the mock response object — save_transaction calls
            # parse_transaction() which uses .get() on the result.
            return response_page

        notion_client._post = lambda path, body=None: capture_post(
            notion_client, path, body
        )

        result = notion_client.save_transaction(
            nama="Regression: rekening_id",
            jumlah=10000,
            tipe="Pengeluaran",
            kategori="Lainnya",
            tanggal="2026-07-22",
            rekening_id=self.ACC_ID,
        )

        # Body MUST carry the relation field
        body = captured_body["body"]
        assert body is not None, "POST /pages was not called"
        assert "Rekening" in body["properties"], (
            f"Rekening missing from body. Properties sent: {list(body['properties'].keys())}"
        )
        assert body["properties"]["Rekening"] == {
            "relation": [{"id": self.ACC_ID}]
        }, f"Rekening relation malformed: {body['properties']['Rekening']}"

        # Returned tx should also reflect the relation
        assert result["rekening_id"] == self.ACC_ID

    def test_missing_rekening_id_omits_relation(
        self, notion_client, mock_httpx_client
    ):
        """If rekening_id is None, body MUST NOT include Rekening at all
        (Notion rejects empty relation lists on creation)."""
        response_page = _mock_notion_page_response(
            self.NEW_TX_PAGE_ID, nama="No rekening",
        )
        self._post_with_response(mock_httpx_client, response_page)

        captured_body = {}

        def capture_post(self, path, body=None):
            captured_body["body"] = body
            # Return response_page directly (already JSON-parsed dict), not
            # the mock response object — save_transaction calls
            # parse_transaction() which uses .get() on the result.
            return response_page

        notion_client._post = lambda path, body=None: capture_post(
            notion_client, path, body
        )

        notion_client.save_transaction(
            nama="No rekening",
            jumlah=5000,
            tipe="Pengeluaran",
            kategori="Lainnya",
            tanggal="2026-07-22",
        )

        body = captured_body["body"]
        assert "Rekening" not in body["properties"], (
            "Empty Rekening relation must be omitted, not sent as empty list"
        )

    def test_jumlah_is_float_in_payload(
        self, notion_client, mock_httpx_client
    ):
        """jumlah (int) must serialize as float — Notion API accepts both
        but tests pin the current behavior."""
        response_page = _mock_notion_page_response(
            self.NEW_TX_PAGE_ID, nama="Int vs float", jumlah=15000,
        )
        self._post_with_response(mock_httpx_client, response_page)

        captured_body = {}

        def capture_post(self, path, body=None):
            captured_body["body"] = body
            # Return response_page directly (already JSON-parsed dict), not
            # the mock response object — save_transaction calls
            # parse_transaction() which uses .get() on the result.
            return response_page

        notion_client._post = lambda path, body=None: capture_post(
            notion_client, path, body
        )

        notion_client.save_transaction(
            nama="Int vs float",
            jumlah=15000,
            tipe="Pengeluaran",
            kategori="Lainnya",
            tanggal="2026-07-22",
            rekening_id="acc-x",
        )

        body = captured_body["body"]
        assert body["properties"]["Angka"] == {"number": 15000.0}

    def test_tipe_and_kategori_use_select_wrapper(
        self, notion_client, mock_httpx_client
    ):
        """Tipe/Kategori fields must use Notion select envelope."""
        response_page = _mock_notion_page_response(
            self.NEW_TX_PAGE_ID, nama="Select fields",
            tipe="Pemasukan", kategori="Pendapatan",
        )
        self._post_with_response(mock_httpx_client, response_page)

        captured_body = {}

        def capture_post(self, path, body=None):
            captured_body["body"] = body
            # Return response_page directly (already JSON-parsed dict), not
            # the mock response object — save_transaction calls
            # parse_transaction() which uses .get() on the result.
            return response_page

        notion_client._post = lambda path, body=None: capture_post(
            notion_client, path, body
        )

        notion_client.save_transaction(
            nama="Select fields",
            jumlah=1000,
            tipe="Pemasukan",
            kategori="Pendapatan",
            tanggal="2026-07-22",
        )

        body = captured_body["body"]
        assert body["properties"]["Tipe"] == {"select": {"name": "Pemasukan"}}
        assert body["properties"]["Kategori"] == {"select": {"name": "Pendapatan"}}

    def test_catatan_uses_rich_text_wrapper(
        self, notion_client, mock_httpx_client
    ):
        """Catatan must use rich_text envelope (not plain string)."""
        response_page = _mock_notion_page_response(
            self.NEW_TX_PAGE_ID, nama="With note", catatan="SeaBank …4309 via GoPay",
        )
        self._post_with_response(mock_httpx_client, response_page)

        captured_body = {}

        def capture_post(self, path, body=None):
            captured_body["body"] = body
            # Return response_page directly (already JSON-parsed dict), not
            # the mock response object — save_transaction calls
            # parse_transaction() which uses .get() on the result.
            return response_page

        notion_client._post = lambda path, body=None: capture_post(
            notion_client, path, body
        )

        notion_client.save_transaction(
            nama="With note",
            jumlah=2500,
            tipe="Pengeluaran",
            kategori="Lainnya",
            tanggal="2026-07-22",
            catatan="SeaBank …4309 via GoPay",
        )

        body = captured_body["body"]
        assert body["properties"]["Catatan"] == {
            "rich_text": [{"text": {"content": "SeaBank …4309 via GoPay"}}]
        }

    def test_catatan_empty_string_still_rich_text(
        self, notion_client, mock_httpx_client
    ):
        """Catatan with empty string must still produce rich_text array."""
        response_page = _mock_notion_page_response(
            self.NEW_TX_PAGE_ID, nama="No note", catatan="",
        )
        self._post_with_response(mock_httpx_client, response_page)

        captured_body = {}

        def capture_post(self, path, body=None):
            captured_body["body"] = body
            # Return response_page directly (already JSON-parsed dict), not
            # the mock response object — save_transaction calls
            # parse_transaction() which uses .get() on the result.
            return response_page

        notion_client._post = lambda path, body=None: capture_post(
            notion_client, path, body
        )

        notion_client.save_transaction(
            nama="No note",
            jumlah=100,
            tipe="Pengeluaran",
            kategori="Lainnya",
            tanggal="2026-07-22",
            catatan="",
        )

        body = captured_body["body"]
        assert body["properties"]["Catatan"] == {
            "rich_text": [{"text": {"content": ""}}]
        }

    def test_transfer_group_sets_rich_text(
        self, notion_client, mock_httpx_client
    ):
        """transfer_group (UUID for paired transactions) → rich_text."""
        response_page = _mock_notion_page_response(
            self.NEW_TX_PAGE_ID, nama="Top-up out", transfer_group="abc-123-uuid",
        )
        self._post_with_response(mock_httpx_client, response_page)

        captured_body = {}

        def capture_post(self, path, body=None):
            captured_body["body"] = body
            # Return response_page directly (already JSON-parsed dict), not
            # the mock response object — save_transaction calls
            # parse_transaction() which uses .get() on the result.
            return response_page

        notion_client._post = lambda path, body=None: capture_post(
            notion_client, path, body
        )

        notion_client.save_transaction(
            nama="Top-up out",
            jumlah=52000,
            tipe="Pengeluaran",
            kategori="Lainnya",
            tanggal="2026-07-21",
            rekening_id="acc-sea",
            transfer_group="abc-123-uuid",
        )

        body = captured_body["body"]
        assert body["properties"]["Transfer Group ID"] == {
            "rich_text": [{"text": {"content": "abc-123-uuid"}}]
        }

    def test_no_transfer_group_omits_field(
        self, notion_client, mock_httpx_client
    ):
        """Without transfer_group, that property should not be in the body."""
        response_page = _mock_notion_page_response(
            self.NEW_TX_PAGE_ID, nama="No transfer group",
        )
        self._post_with_response(mock_httpx_client, response_page)

        captured_body = {}

        def capture_post(self, path, body=None):
            captured_body["body"] = body
            # Return response_page directly (already JSON-parsed dict), not
            # the mock response object — save_transaction calls
            # parse_transaction() which uses .get() on the result.
            return response_page

        notion_client._post = lambda path, body=None: capture_post(
            notion_client, path, body
        )

        notion_client.save_transaction(
            nama="No transfer group",
            jumlah=100,
            tipe="Pengeluaran",
            kategori="Lainnya",
            tanggal="2026-07-22",
        )

        body = captured_body["body"]
        assert "Transfer Group ID" not in body["properties"]

    def test_parent_uses_data_source_id(
        self, notion_client, mock_httpx_client
    ):
        """Notion API 2025-09-03: parent must use data_source_id, not
        database_id. Pin this so a future migration doesn't break."""
        response_page = _mock_notion_page_response(
            self.NEW_TX_PAGE_ID, nama="Parent shape",
        )
        self._post_with_response(mock_httpx_client, response_page)

        captured_body = {}

        def capture_post(self, path, body=None):
            captured_body["body"] = body
            # Return response_page directly (already JSON-parsed dict), not
            # the mock response object — save_transaction calls
            # parse_transaction() which uses .get() on the result.
            return response_page

        notion_client._post = lambda path, body=None: capture_post(
            notion_client, path, body
        )

        notion_client.save_transaction(
            nama="Parent shape",
            jumlah=100,
            tipe="Pengeluaran",
            kategori="Lainnya",
            tanggal="2026-07-22",
        )

        body = captured_body["body"]
        assert body["parent"]["type"] == "data_source_id"
        assert body["parent"]["data_source_id"] == notion_client.transactions_ds_id
        # Belt-and-braces: database_id key MUST NOT be present
        assert "database_id" not in body["parent"]

    def test_tanggal_parsed_and_serialized(
        self, notion_client, mock_httpx_client
    ):
        """Tanggal string → parsers.parse_date → ISO date in body."""
        response_page = _mock_notion_page_response(
            self.NEW_TX_PAGE_ID, nama="Date parsing", tanggal="2026-07-21",
        )
        self._post_with_response(mock_httpx_client, response_page)

        captured_body = {}

        def capture_post(self, path, body=None):
            captured_body["body"] = body
            # Return response_page directly (already JSON-parsed dict), not
            # the mock response object — save_transaction calls
            # parse_transaction() which uses .get() on the result.
            return response_page

        notion_client._post = lambda path, body=None: capture_post(
            notion_client, path, body
        )

        notion_client.save_transaction(
            nama="Date parsing",
            jumlah=100,
            tipe="Pengeluaran",
            kategori="Lainnya",
            tanggal="21/07/2026",  # DD/MM/YYYY — supported by parse_date
        )

        body = captured_body["body"]
        # parse_date turns "21/07/2026" into ISO "2026-07-21"
        assert body["properties"]["Tanggal"]["date"]["start"] == "2026-07-21"

    def test_full_jagocoffee_repro(
        self, notion_client, mock_httpx_client
    ):
        """Full reproduction of the Jagocoffee tx from the live session
        (22 Jul 2026, Rp 10.000, SeaBank, via GoPay). This is the exact
        payload that exposed the stale-pycache bug. If this fails, the
        runtime plugin almost certainly has a stale .pyc cache.
        """
        response_page = _mock_notion_page_response(
            self.NEW_TX_PAGE_ID,
            nama="Jagocoffee",
            jumlah=10000,
            tipe="Pengeluaran",
            kategori="Makanan & Minuman",
            tanggal="2026-07-22",
            rekening_ids=[self.ACC_ID],
            catatan=(
                "SeaBank …4309 → jagocoffee.com/ (Jakbar) via GoPay | "
                "No. 2026072243507487745487900 | Ref 011100077J3Q"
            ),
        )
        self._post_with_response(mock_httpx_client, response_page)

        captured_body = {}

        def capture_post(self, path, body=None):
            captured_body["body"] = body
            # Return response_page directly (already JSON-parsed dict), not
            # the mock response object — save_transaction calls
            # parse_transaction() which uses .get() on the result.
            return response_page

        notion_client._post = lambda path, body=None: capture_post(
            notion_client, path, body
        )

        result = notion_client.save_transaction(
            nama="Jagocoffee",
            jumlah=10000,
            tipe="Pengeluaran",
            kategori="Makanan & Minuman",
            tanggal="2026-07-22",
            rekening_id=self.ACC_ID,
            catatan=(
                "SeaBank …4309 → jagocoffee.com/ (Jakbar) via GoPay | "
                "No. 2026072243507487745487900 | Ref 011100077J3Q"
            ),
        )

        body = captured_body["body"]

        # The exact assertion that would have caught the bug:
        assert body["properties"]["Rekening"] == {
            "relation": [{"id": self.ACC_ID}]
        }, (
            "REGRESSION: Rekening relation is missing or malformed in save_transaction. "
            "Check that client.py save_transaction (around line 285) is in sync with "
            "the runtime plugin and that __pycache__ has been cleared."
        )
        assert result["rekening_id"] == self.ACC_ID
        # Verify other fields
        assert body["properties"]["Catatan"]["rich_text"][0]["text"]["content"].startswith(
            "SeaBank"
        )

    def test_save_transaction_schedules_debounced_refresh(
        self, notion_client, mock_httpx_client, monkeypatch, reset_refresh_state
    ):
        """REGRESSION: save_transaction must trigger an auto-refresh of the
        Notion summary page so users see fresh saldo after every transaction.

        Bug pre-fix: hook was only in handle_save_transaction (tools.py),
        so direct client.save_transaction() calls (used by all internal
        code paths and tests) bypassed the refresh — page only updated
        via Telegram gateway path.

        After debounce refactor (commit 037e61a follow-up): the call is
        debounced via _schedule_refresh() so a batch of N mutations fires
        1 refresh instead of N. We assert the SCHEDULER is called, not the
        underlying hook (which would fire on a timer).
        """
        response_page = _mock_notion_page_response(
            self.NEW_TX_PAGE_ID,
            nama="Hook fires",
            rekening_ids=[self.ACC_ID],
        )
        self._post_with_response(mock_httpx_client, response_page)

        # Spy on _schedule_refresh (the debounced entry point).
        import client as client_mod
        scheduled = {"count": 0}
        def spy_schedule():
            scheduled["count"] += 1
        monkeypatch.setattr(client_mod, "_schedule_refresh", spy_schedule)

        # And spy on _maybe_refresh_summary_page — it must NOT be called
        # directly by save_transaction (it would defeat the debouncer).
        fired = {"count": 0}
        def spy_hook():
            fired["count"] += 1
        monkeypatch.setattr(client_mod, "_maybe_refresh_summary_page", spy_hook)

        notion_client.save_transaction(
            nama="Hook fires",
            jumlah=100,
            tipe="Pengeluaran",
            kategori="Lainnya",
            tanggal="2026-07-22",
            rekening_id=self.ACC_ID,
        )

        assert scheduled["count"] == 1, (
            f"_schedule_refresh called {scheduled['count']} times, expected 1. "
            "save_transaction must call _schedule_refresh once per mutation."
        )
        assert fired["count"] == 0, (
            f"_maybe_refresh_summary_page called {fired['count']} times "
            "directly from save_transaction. That bypasses the debouncer — "
            "save_transaction should call _schedule_refresh only."
        )

    def test_flush_pending_refresh_fires_hook_immediately(
        self, notion_client, mock_httpx_client, monkeypatch, reset_refresh_state
    ):
        """flush_pending_refresh() must cancel the debounce timer and run
        the hook synchronously, so callers (e.g. batch scripts, gateway
        shutdown) can deterministically wait for the page to refresh.
        """
        import client as client_mod
        fired = {"count": 0}
        def spy_hook():
            fired["count"] += 1
        monkeypatch.setattr(client_mod, "_maybe_refresh_summary_page", spy_hook)

        # Schedule a refresh, then flush it
        client_mod._schedule_refresh()
        assert client_mod.get_pending_mutation_count() == 1
        client_mod.flush_pending_refresh()
        assert fired["count"] == 1, (
            "flush_pending_refresh did not invoke the hook"
        )
        assert client_mod.get_pending_mutation_count() == 0
        assert client_mod._refresh_timer is None, (
            "flush_pending_refresh left a dangling timer"
        )

    def test_debounce_collapse_rapid_mutations(
        self, notion_client, mock_httpx_client, monkeypatch, reset_refresh_state
    ):
        """Five rapid save_transaction calls should fire the hook exactly
        ONCE (collapse), not five times.

        We force debounce=2s here so the test doesn't take 30s, and wait
        ~3s for the timer to fire.
        """
        import client as client_mod
        monkeypatch.setenv("KASIO_REFRESH_DEBOUNCE_SEC", "2")
        # Re-import the module-level constant (read at import time)
        import importlib
        importlib.reload(client_mod)
        # Restore the new function reference (reload creates new module)
        import sys
        # Actually, simpler: directly monkey-patch the debounce window
        client_mod._REFRESH_DEBOUNCE_SEC = 2.0

        fired = {"count": 0}
        def spy_hook():
            fired["count"] += 1
        monkeypatch.setattr(client_mod, "_maybe_refresh_summary_page", spy_hook)

        # Schedule 5 refreshes in quick succession (collapse pattern)
        for _ in range(5):
            client_mod._schedule_refresh()
        assert client_mod.get_pending_mutation_count() == 5
        # Hook must NOT have fired yet (window is 2s)
        assert fired["count"] == 0, (
            f"Hook fired {fired['count']} times during debounce window"
        )

        # Wait for window to expire
        import time
        time.sleep(2.5)

        # Now hook should have fired exactly once
        assert fired["count"] == 1, (
            f"Expected 1 hook fire after debounce window, got {fired['count']}"
        )
        assert client_mod.get_pending_mutation_count() == 0

