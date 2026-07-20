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

    def test_valid_env_initializes(self, mock_httpx_client):
        client = NotionClient()
        assert client.api_key == "ntn_test_fake_key_for_unit_tests"
        assert client.transactions_ds_id is not None
        assert client.accounts_ds_id is not None
