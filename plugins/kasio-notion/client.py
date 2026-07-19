"""Notion HTTP client for kasio-notion plugin.

Port dari KASIO v2 src/notion.js + src/notion-accounts.js. Bypass @notionhq/client
SDK (yang v2.x belum support data_sources endpoint di Notion API 2025-09-03).
Pakai httpx langsung + Notion-Version header.
"""

from __future__ import annotations
import os
import httpx
from .constants import NOTION_API_BASE, NOTION_VERSION, TX_PROP, ACCT_PROP


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

    def _post(self, path: str, body: dict) -> dict:
        resp = self._client.post(path, json=body)
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path: str, body: dict) -> dict:
        resp = self._client.patch(path, json=body)
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str) -> dict:
        resp = self._client.get(path)
        resp.raise_for_status()
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
        from .parsers import parse_date
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
        return self.parse_transaction(data)

    def update_transaction(self, page_id: str, updates: dict) -> dict:
        """Update fields on existing transaction. updates keys: nama, jumlah, tipe, kategori, tanggal, catatan, rekening_id, transfer_group."""
        from .parsers import parse_date
        properties = {}
        for key, value in updates.items():
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
            elif key == "rekening_id":
                properties[notion_field] = {"relation": [{"id": value}] if value else []}
            elif key == "transfer_group":
                properties[notion_field] = {"rich_text": [{"text": {"content": value or ""}}]}
        data = self._patch(f"/pages/{page_id}", {"properties": properties})
        return self.parse_transaction(data)

    def archive_transaction(self, page_id: str) -> dict:
        """Soft-delete (archive) transaction."""
        data = self._patch(f"/pages/{page_id}", {"archived": True})
        return self.parse_transaction(data)

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
        return accounts

    def save_account(
        self,
        nama: str,
        saldo_awal: float = 0,
        urutan: int | None = None,
        ikon: str = "",
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
        data = self._patch(f"/pages/{page_id}", {"properties": properties})
        return self.parse_account(data)

    def archive_account(self, page_id: str) -> dict:
        """Soft-delete (archive) account. Sets status to Diarsipkan + archives."""
        self.update_account(page_id, {"status": "Diarsipkan"})
        data = self._patch(f"/pages/{page_id}", {"archived": True})
        return self.parse_account(data)
