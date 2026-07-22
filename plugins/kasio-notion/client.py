"""Notion HTTP client for kasio-notion plugin.

Port dari KASIO v2 src/notion.js + src/notion-accounts.js. Bypass @notionhq/client
SDK (yang v2.x belum support data_sources endpoint di Notion API 2025-09-03).
Pakai httpx langsung + Notion-Version header.

Features:
  - Auto-retry dengan exponential backoff untuk 429 (rate limit) dan 5xx (server errors)
  - Honoring Retry-After header dari Notion API
  - Jitter untuk mencegah thundering herd
"""

from __future__ import annotations
import os
import time
import random
import logging
from collections import defaultdict
import httpx
import subprocess
import sys

# Support both package mode (production: from .constants import ...) and
# top-level mode (testing: from constants import ...).
try:
    from .constants import NOTION_API_BASE, NOTION_VERSION, TX_PROP, ACCT_PROP
except ImportError:
    from constants import NOTION_API_BASE, NOTION_VERSION, TX_PROP, ACCT_PROP

# Logger for retry events (visible kalau plugin host configure logging)
logger = logging.getLogger("kasio.notion")

# Retry configuration
MAX_RETRIES = int(os.environ.get("KASIO_MAX_RETRIES", "3"))
BASE_DELAY_SEC = float(os.environ.get("KASIO_RETRY_BASE_DELAY", "1.0"))
MAX_DELAY_SEC = float(os.environ.get("KASIO_RETRY_MAX_DELAY", "30.0"))
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _calculate_backoff(attempt: int, retry_after: str | None = None) -> float:
    """Calculate backoff delay with optional jitter.

    Args:
        attempt: 0-based retry attempt number (0 = first retry)
        retry_after: Server-provided Retry-After header value (seconds)

    Returns:
        Delay in seconds (includes jitter)
    """
    if retry_after:
        try:
            return float(retry_after)
        except (ValueError, TypeError):
            pass  # Fall through to exponential backoff
    # Exponential backoff: 1, 2, 4, 8, ... up to MAX_DELAY_SEC
    delay = min(BASE_DELAY_SEC * (2 ** attempt), MAX_DELAY_SEC)
    # Add jitter (±25%) to prevent thundering herd
    jitter = delay * 0.25 * (2 * random.random() - 1)
    return max(0.1, delay + jitter)




def _maybe_refresh_summary_page() -> None:
    """Re-render the Notion 'KASIO Ringkasan Harian' page after a transaction
    mutation, so users see fresh saldo + cashflow without manual refresh.

    Opt-in via KASIO_NOTION_SUMMARY_PAGE_ID env var. The refresh is best-effort:
    failures here NEVER fail the originating transaction (which already
    succeeded). Errors are logged to stderr for debugging.

    Uses scripts/refresh_summary.py from the kasio-distribution repo. We
    locate it by walking up from this file's directory until we find a
    'scripts/refresh_summary.py' sibling, so the plugin works whether
    installed as part of the kasio-distribution package or as a standalone
    Hermes plugin (in which case summary auto-refresh is simply skipped).
    """
    page_id = os.environ.get("KASIO_NOTION_SUMMARY_PAGE_ID")
    if not page_id:
        return  # user opted out — feature is opt-in
    # Auto-refresh in pytest would hit real Notion API and slow tests by
    # ~3s each (3 charts to upload). Skip during test runs unless explicitly
    # enabled via KASIO_NOTION_TEST_REFRESH=1.
    if "pytest" in sys.modules and not os.environ.get("KASIO_NOTION_TEST_REFRESH"):
        return
    # Locate scripts/refresh_summary.py
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "..", "..", "scripts", "refresh_summary.py"),  # plugin source layout
        os.path.join(here, "..", "scripts", "refresh_summary.py"),       # plugin installed layout
    ]
    script = next((p for p in candidates if os.path.exists(p)), None)
    if not script:
        print(
            f"[kasio] KASIO_NOTION_SUMMARY_PAGE_ID set but scripts/refresh_summary.py "
            f"not found. Looked in: {candidates}",
            file=sys.stderr,
        )
        return
    try:
        # Use the same Python interpreter that's running the plugin. Inherit
        # env so NOTION_API_KEY, KASIO_*_DS_ID, KASIO_NOTION_SUMMARY_PAGE_ID
        # all reach the child process.
        subprocess.run(
            [sys.executable, script],
            check=False,         # don't propagate failure to caller
            timeout=30,          # cap so a Notion outage can't hang us
            capture_output=True, # suppress child stdout (too noisy for every tx)
        )
    except subprocess.TimeoutExpired:
        print(f"[kasio] refresh_summary.py timed out after 30s", file=sys.stderr)
    except Exception as e:
        print(f"[kasio] refresh_summary.py failed: {type(e).__name__}: {e}",
              file=sys.stderr)



class NotionClient:
    """HTTP client for Notion API. Lazy-loaded via plugin_utils.lazy_singleton."""

    def __init__(self):
        api_key = os.environ.get("NOTION_API_KEY", "").strip()
        if not api_key or api_key.startswith("your_"):
            raise RuntimeError(
                "NOTION_API_KEY not set or placeholder. "
                "Set it di ~/.hermes/.env. Get key di https://www.notion.so/my-integrations"
            )
        self.api_key = api_key
        self.transactions_ds_id = os.environ.get("KASIO_TRANSACTIONS_DS_ID", "").strip()
        self.accounts_ds_id = os.environ.get("KASIO_ACCOUNTS_DS_ID", "").strip()
        if not self.transactions_ds_id or not self.accounts_ds_id:
            raise RuntimeError(
                "KASIO_TRANSACTIONS_DS_ID dan KASIO_ACCOUNTS_DS_ID harus di-set di .env. "
                "Lihat README plugin untuk cara dapetin DS ID."
            )
        self._client = httpx.Client(
            base_url=NOTION_API_BASE,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request_with_retry(self, method: str, path: str, **kwargs) -> httpx.Response:
        """HTTP request with retry/backoff on transient errors.

        Retries on:
          - 429 (Too Many Requests / rate limit)
          - 500 (Internal Server Error)
          - 502 (Bad Gateway)
          - 503 (Service Unavailable)
          - 504 (Gateway Timeout)

        Does NOT retry on:
          - 4xx other than 429 (client errors — request is bad, retry won't help)
          - Network errors (handled by httpx timeout — caller can retry)

        Honors Retry-After header if present (Notion includes it on 429).
        """
        last_exception: httpx.HTTPStatusError | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._client.request(method, path, **kwargs)
            except httpx.TimeoutException:
                # Network timeout — don't retry automatically, let caller decide
                # (retries on network errors can mask real connectivity issues)
                raise
            except httpx.NetworkError:
                # Network error (DNS, connection refused, etc.) — also don't retry
                raise

            if resp.status_code in RETRYABLE_STATUS_CODES:
                last_exception = httpx.HTTPStatusError(
                    f"{resp.status_code} {resp.reason_phrase}",
                    request=resp.request,
                    response=resp,
                )
                if attempt < MAX_RETRIES:
                    retry_after = resp.headers.get("Retry-After")
                    delay = _calculate_backoff(attempt, retry_after)
                    logger.warning(
                        "Notion API %s %s -> %d (attempt %d/%d). Retrying in %.2fs",
                        method, path, resp.status_code, attempt + 1, MAX_RETRIES + 1, delay,
                    )
                    time.sleep(delay)
                    continue

            # Either non-retryable status, or out of retries — raise if error
            resp.raise_for_status()
            return resp

        # Exhausted all retries on retryable status code
        assert last_exception is not None
        raise last_exception

    def _post(self, path: str, body: dict) -> dict:
        resp = self._request_with_retry("POST", path, json=body)
        return resp.json()

    def _patch(self, path: str, body: dict) -> dict:
        resp = self._request_with_retry("PATCH", path, json=body)
        return resp.json()

    def _get(self, path: str) -> dict:
        resp = self._request_with_retry("GET", path)
        return resp.json()

    @staticmethod
    def _extract_rich_text(prop) -> str:
        if not prop or prop.get("type") != "rich_text":
            return ""
        return "".join(rt.get("plain_text", "") for rt in prop.get("rich_text", []))

    @staticmethod
    def _extract_relation_id(prop) -> str | None:
        if not prop or prop.get("type") != "relation":
            return None
        rels = prop.get("relation", [])
        return rels[0].get("id") if rels else None

    @staticmethod
    def _extract_title(prop) -> str:
        if not prop or prop.get("type") != "title":
            return ""
        return "".join(t.get("plain_text", "") for t in prop.get("title", []))

    # ------------------------------------------------------------------
    # Transactions
    # ------------------------------------------------------------------

    def parse_transaction(self, page: dict, account_map: dict | None = None) -> dict:
        """Parse Notion page -> transaction dict."""
        p = page.get("properties", {})
        rekening_id = self._extract_relation_id(p.get(TX_PROP["rekening"]))
        result = {
            "id": page.get("id"),
            "nama": self._extract_title(p.get(TX_PROP["nama"])),
            "jumlah": p.get(TX_PROP["jumlah"], {}).get("number") or 0,
            "tipe": (p.get(TX_PROP["tipe"], {}).get("select") or {}).get("name", ""),
            "kategori": (p.get(TX_PROP["kategori"], {}).get("select") or {}).get("name", ""),
            "tanggal": (p.get(TX_PROP["tanggal"], {}).get("date") or {}).get("start", ""),
            "catatan": self._extract_rich_text(p.get(TX_PROP["catatan"])),
            "rekening_id": rekening_id,
            "transfer_group": self._extract_rich_text(p.get(TX_PROP["transfer_group"])),
            "archived": bool(page.get("archived")),
        }
        if rekening_id and account_map and rekening_id in account_map:
            acct = account_map[rekening_id]
            result["rekening"] = {
                "id": rekening_id,
                "nama": acct.get("nama", ""),
                "icon": acct.get("ikon", ""),
            }
        return result

    def list_transactions(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        rekening_id: str | None = None,
        kategori: str | None = None,
        transfer_group: str | None = None,
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """Query transactions from Notion DB with optional filters."""
        body: dict = {"page_size": min(max(limit, 1), 100), "sorts": [{"property": TX_PROP["tanggal"], "direction": "descending"}]}
        filters = []
        if start_date or end_date:
            date_filter: dict = {"property": TX_PROP["tanggal"], "date": {}}
            if start_date:
                date_filter["date"]["on_or_after"] = start_date
            if end_date:
                date_filter["date"]["on_or_before"] = end_date
            filters.append(date_filter)
        if rekening_id:
            filters.append({
                "property": TX_PROP["rekening"],
                "relation": {"contains": rekening_id},
            })
        if kategori:
            filters.append({
                "property": TX_PROP["kategori"],
                "select": {"equals": kategori},
            })
        if transfer_group:
            filters.append({
                "property": TX_PROP["transfer_group"],
                "rich_text": {"equals": transfer_group},
            })
        if filters:
            body["filter"] = {"and": filters}

        all_results = []
        cursor = None
        while True:
            if cursor:
                body["start_cursor"] = cursor
            data = self._post(f"/data_sources/{self.transactions_ds_id}/query", body)
            all_results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        txs = [self.parse_transaction(p) for p in all_results]
        if not include_archived:
            txs = [t for t in txs if not t["archived"]]
        return txs

    def save_transaction(
        self,
        nama: str,
        jumlah: int | float,
        tipe: str,
        kategori: str,
        tanggal: str | None = None,
        catatan: str = "",
        rekening_id: str | None = None,
        transfer_group: str | None = None,
    ) -> dict:
        """Create new transaction page. Returns parsed transaction."""
        # Lazy import to avoid circular dependency; support both package and
        # top-level test modes.
        try:
            from .parsers import parse_date
        except ImportError:
            from parsers import parse_date
        date_val = parse_date(tanggal) if tanggal else parse_date("")
        properties = {
            TX_PROP["nama"]: {"title": [{"text": {"content": nama}}]},
            TX_PROP["jumlah"]: {"number": float(jumlah)},
            TX_PROP["tipe"]: {"select": {"name": tipe}},
            TX_PROP["kategori"]: {"select": {"name": kategori}},
            TX_PROP["tanggal"]: {"date": {"start": date_val}},
            TX_PROP["catatan"]: {"rich_text": [{"text": {"content": catatan or ""}}]},
        }
        if rekening_id:
            properties[TX_PROP["rekening"]] = {"relation": [{"id": rekening_id}]}
        if transfer_group:
            properties[TX_PROP["transfer_group"]] = {"rich_text": [{"text": {"content": transfer_group}}]}

        # Notion 2025-09-03: parent must use data_source_id, not database_id
        data = self._post("/pages", {"parent": {"type": "data_source_id", "data_source_id": self.transactions_ds_id}, "properties": properties})
        result = self.parse_transaction(data)
        # Auto-refresh Notion summary page (best-effort, opt-in). See
        # _maybe_refresh_summary_page for details.
        _maybe_refresh_summary_page()
        return result

    def update_transaction(self, page_id: str, updates: dict) -> dict:
        """Update fields on existing transaction. updates keys: nama, jumlah, tipe, kategori, tanggal, catatan, rekening_id, transfer_group."""
        # Lazy import to avoid circular dependency; support both package and
        # top-level test modes.
        try:
            from .parsers import parse_date
        except ImportError:
            from parsers import parse_date
        properties = {}
        for key, value in updates.items():
            # Special-case: rekening_id / rekening both route to the Notion
            # relation field whose TX_PROP key is "rekening". The TX_PROP
            # lookup alone would miss these aliases and silently drop the
            # field. See commit 037e61a for the bug history.
            if key in ("rekening", "rekening_id"):
                properties[TX_PROP["rekening"]] = {
                    "relation": [{"id": value}] if value else []
                }
                continue
            notion_field = TX_PROP.get(key)
            if not notion_field:
                continue
            if key == "jumlah":
                properties[notion_field] = {"number": float(value)}
            elif key in ("tipe", "kategori"):
                properties[notion_field] = {"select": {"name": value}}
            elif key == "tanggal":
                properties[notion_field] = {"date": {"start": parse_date(value)}}
            elif key == "nama":
                properties[notion_field] = {"title": [{"text": {"content": value}}]}
            elif key == "catatan":
                properties[notion_field] = {"rich_text": [{"text": {"content": value or ""}}]}
            elif key == "transfer_group":
                properties[notion_field] = {"rich_text": [{"text": {"content": value or ""}}]}
        data = self._patch(f"/pages/{page_id}", {"properties": properties})
        result = self.parse_transaction(data)
        _maybe_refresh_summary_page()
        return result

    def archive_transaction(self, page_id: str) -> dict:
        """Soft-delete (archive) transaction."""
        data = self._patch(f"/pages/{page_id}", {"archived": True})
        result = self.parse_transaction(data)
        _maybe_refresh_summary_page()
        return result

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------

    def parse_account(self, page: dict) -> dict:
        """Parse Notion page -> account dict."""
        p = page.get("properties", {})
        return {
            "id": page.get("id"),
            "nama": self._extract_title(p.get(ACCT_PROP["nama"])),
            "saldo_awal": p.get(ACCT_PROP["saldo_awal"], {}).get("number") or 0,
            "status": (p.get(ACCT_PROP["status"], {}).get("select") or {}).get("name", "Aktif"),
            "urutan": p.get(ACCT_PROP["urutan"], {}).get("number"),
            "ikon": self._extract_rich_text(p.get(ACCT_PROP["ikon"])),
            "nomor_rekening": self._extract_rich_text(p.get(ACCT_PROP["nomor_rekening"])),
            "archived": bool(page.get("archived")),
        }

    def list_accounts(self, include_archived: bool = False, status: str | None = None) -> list[dict]:
        """Query accounts from Notion DB."""
        body: dict = {
            "page_size": 100,
            "sorts": [
                {"property": ACCT_PROP["urutan"], "direction": "ascending"},
                {"property": ACCT_PROP["nama"], "direction": "ascending"},
            ],
        }
        if status:
            body["filter"] = {"property": ACCT_PROP["status"], "select": {"equals": status}}

        all_results = []
        cursor = None
        while True:
            if cursor:
                body["start_cursor"] = cursor
            data = self._post(f"/data_sources/{self.accounts_ds_id}/query", body)
            all_results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        accounts = [self.parse_account(p) for p in all_results]
        if not include_archived:
            accounts = [a for a in accounts if not a["archived"] and a["status"] != "Diarsipkan"]
        # Compute saldo_live per account: saldo_awal + sum(non-archived
        # transactions tied to this account). Single source of truth = tx DB.
        accounts = self._attach_saldo_live(accounts)
        return accounts

    def _attach_saldo_live(self, accounts: list[dict]) -> list[dict]:
        """Compute and attach `saldo_live` to each account dict.

        saldo_live = saldo_awal + Σ(Pemasukan) − Σ(Pengeluaran)
        over all non-archived transactions tied to this account. The
        computation lives here (not on Notion) so the value is always
        fresh — there is no chance of drift between stored saldo_live and
        the underlying transactions.
        """
        if not accounts:
            return accounts
        account_ids = {a["id"] for a in accounts if a.get("id")}
        # Fetch all transactions in one query and bucket by rekening_id.
        # Cap at 1000 to bound memory; users with more rows can paginate
        # explicitly via list_transactions().
        txs = self.list_transactions(limit=1000)
        saldo_delta: dict[str, int] = defaultdict(int)
        for t in txs:
            if t.get("archived"):
                continue
            rid = t.get("rekening_id")
            if rid not in account_ids:
                continue
            if t.get("tipe") == "Pemasukan":
                saldo_delta[rid] += t["jumlah"]
            elif t.get("tipe") == "Pengeluaran":
                saldo_delta[rid] -= t["jumlah"]
        for a in accounts:
            a["saldo_live"] = a["saldo_awal"] + saldo_delta.get(a["id"], 0)
        return accounts

    def save_account(
        self,
        nama: str,
        saldo_awal: float = 0,
        urutan: int | None = None,
        ikon: str = "",
        nomor_rekening: str = "",
        status: str = "Aktif",
    ) -> dict:
        """Create new account."""
        properties = {
            ACCT_PROP["nama"]: {"title": [{"text": {"content": nama}}]},
            ACCT_PROP["saldo_awal"]: {"number": float(saldo_awal)},
            ACCT_PROP["status"]: {"select": {"name": status}},
        }
        if urutan is not None:
            properties[ACCT_PROP["urutan"]] = {"number": urutan}
        if ikon:
            properties[ACCT_PROP["ikon"]] = {"rich_text": [{"text": {"content": ikon}}]}
        if nomor_rekening:
            properties[ACCT_PROP["nomor_rekening"]] = {"rich_text": [{"text": {"content": nomor_rekening}}]}
        # Notion 2025-09-03: parent must use data_source_id, not database_id
        data = self._post("/pages", {"parent": {"type": "data_source_id", "data_source_id": self.accounts_ds_id}, "properties": properties})
        return self.parse_account(data)

    def update_account(self, page_id: str, updates: dict) -> dict:
        """Update fields on existing account."""
        properties = {}
        for key, value in updates.items():
            notion_field = ACCT_PROP.get(key)
            if not notion_field:
                continue
            if key in ("saldo_awal", "urutan"):
                properties[notion_field] = {"number": float(value) if value is not None else None}
            elif key == "nama":
                properties[notion_field] = {"title": [{"text": {"content": value}}]}
            elif key == "status":
                properties[notion_field] = {"select": {"name": value}}
            elif key == "ikon":
                properties[notion_field] = {"rich_text": [{"text": {"content": value or ""}}]}
            elif key == "nomor_rekening":
                properties[notion_field] = {"rich_text": [{"text": {"content": value or ""}}]}
        data = self._patch(f"/pages/{page_id}", {"properties": properties})
        return self.parse_account(data)

    def archive_account(self, page_id: str) -> dict:
        """Soft-delete (archive) account. Sets status to Diarsipkan + archives."""
        self.update_account(page_id, {"status": "Diarsipkan"})
        data = self._patch(f"/pages/{page_id}", {"archived": True})
        return self.parse_account(data)
