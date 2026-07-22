#!/usr/bin/env python3
"""Refresh the Notion 'KASIO Ringkasan Harian' page with current DB state.

Called by the kasio-notion plugin after every successful transaction mutation
(save/update/archive) when KASIO_NOTION_SUMMARY_PAGE_ID is set in env.

Idempotent: archives existing page children, then re-creates them with
fresh data + (optional) charts.

Can also be run by hand:
    python scripts/refresh_summary.py

Exit codes:
    0  success
    1  missing required env
    2  Notion API error
"""
from __future__ import annotations

import os
import sys
import json
import uuid
import urllib.request
import urllib.error
from datetime import datetime
from collections import defaultdict


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "plugins", "kasio-notion"))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

try:
    import client as _client_mod  # noqa: E402
except ImportError as e:
    print(f"FATAL: cannot import kasio_notion.client: {e}", file=sys.stderr)
    print(f"       Looked in: {PLUGIN_DIR}", file=sys.stderr)
    sys.exit(1)


REQUIRED_ENV = (
    "NOTION_API_KEY",
    "KASIO_TRANSACTIONS_DS_ID",
    "KASIO_ACCOUNTS_DS_ID",
    "KASIO_NOTION_SUMMARY_PAGE_ID",
)


def _notion(method: str, path: str, body=None, content_type: str = "application/json"):
    headers = {
        "Authorization": f"Bearer {os.environ['NOTION_API_KEY']}",
        "Notion-Version": "2025-09-03",
        "Content-Type": content_type,
    }
    req = urllib.request.Request(
        f"https://api.notion.com/v1{path}",
        method=method, headers=headers, data=body,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _require_env() -> dict:
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"ERROR: missing env vars: {missing}", file=sys.stderr)
        sys.exit(1)
    return {k: os.environ[k] for k in REQUIRED_ENV}


def _archive_all(page_id: str) -> int:
    """Archive (soft-delete) all current children of the page. Returns count."""
    s, resp = _notion("GET", f"/blocks/{page_id}/children")
    if s != 200:
        return 0
    n = 0
    for blk in resp.get("results", []):
        bs, _ = _notion("PATCH", f"/blocks/{blk['id']}",
                        json.dumps({"archived": True}).encode())
        if bs == 200:
            n += 1
    return n


def _make_charts(accounts, transactions, today):
    """Generate 3 PNG charts to temp dir. Returns list of (filepath, filename).
    Skipped silently if matplotlib is not available."""
    if os.environ.get("KASIO_INCLUDE_CHARTS") == "0":
        return []
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("WARN: matplotlib not installed, skipping charts", file=sys.stderr)
        return []

    import tempfile
    tmp = tempfile.mkdtemp(prefix="kasio_summary_")
    ICON_R = {"BCA": "🏦", "Cash": "💵", "Seabank": "🏦", "Gopay": "💚",
              "Bank Jago": "🦊", "Jago → Gopay": "🔄"}

    def _saldo_live(a):
        s = a["saldo_awal"]
        for t in transactions:
            if t.get("rekening_id") == a["id"] and not t.get("archived"):
                if t["tipe"] == "Pemasukan":
                    s += t["jumlah"]
                elif t["tipe"] == "Pengeluaran":
                    s -= t["jumlah"]
        return s

    # Chart 1: pie + cashflow
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5),
                             gridspec_kw={"width_ratios": [1, 1.2]})
    fig.patch.set_facecolor("#F5F7FA")
    plt.suptitle(f"KASIO — Ringkasan {today}",
                 fontsize=16, fontweight="bold", color="#1A1A2E", y=1.02)
    by_cat = defaultdict(int)
    for t in transactions:
        if t["tipe"] == "Pengeluaran":
            by_cat[t["kategori"]] += t["jumlah"]
    cat_out = dict(sorted(by_cat.items(), key=lambda x: -x[1]))
    total_out = sum(cat_out.values()) or 1
    colors = ["#22C55E", "#3B82F6", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899", "#14B8A6", "#6B7280"]
    wedges, texts, atxts = axes[0].pie(
        cat_out.values(), labels=cat_out.keys(),
        autopct=lambda p: f"{p:.1f}%\nRp {int(p*total_out/100):,}".replace(",", "."),
        colors=colors[:len(cat_out)], startangle=90,
        wedgeprops={"edgecolor": "white", "linewidth": 2},
        textprops={"fontsize": 9, "color": "#1A1A2E"})
    for t in atxts:
        t.set_fontweight("bold"); t.set_fontsize(8)
    axes[0].set_title(f"Pengeluaran per Kategori (Rp {total_out:,})".replace(",", "."),
                      fontsize=12, fontweight="bold", color="#1A1A2E", pad=12)

    by_date = defaultdict(lambda: {"in": 0, "out": 0})
    for t in transactions:
        k = "in" if t["tipe"] == "Pemasukan" else "out"
        by_date[t["tanggal"]][k] += t["jumlah"]
    dates = sorted(by_date.keys())
    in_vals = [by_date[d]["in"] for d in dates]
    out_vals = [by_date[d]["out"] for d in dates]
    x = list(range(len(dates)))
    axes[1].bar([i-0.2 for i in x], in_vals, width=0.4, color="#22C55E", label="Pemasukan", zorder=3)
    axes[1].bar([i+0.2 for i in x], out_vals, width=0.4, color="#EF4444", label="Pengeluaran", zorder=3)
    axes[1].set_xticks(x)
    axes[1].set_xticklabels([datetime.strptime(d, "%Y-%m-%d").strftime("%d %b") for d in dates], fontsize=10)
    axes[1].set_title("Cash Flow Harian", fontsize=12, fontweight="bold",
                       color="#1A1A2E", pad=12)
    axes[1].legend(loc="upper right", frameon=False)
    axes[1].grid(axis="y", alpha=0.3, linestyle="--", zorder=0)
    axes[1].spines[["top", "right"]].set_visible(False)
    axes[1].set_facecolor("#FFFFFF")
    axes[1].yaxis.set_major_formatter(plt.FuncFormatter(
        lambda v, p: f"{v/1000:.0f}rb" if v < 1e6 else f"{v/1e6:.1f}jt"))
    plt.tight_layout()
    p1 = os.path.join(tmp, "chart1.png")
    plt.savefig(p1, dpi=140, bbox_inches="tight", facecolor="#F5F7FA")
    plt.close()

    # Chart 2: saldo per rekening
    fig, ax = plt.subplots(figsize=(10, 4))
    fig.patch.set_facecolor("#F5F7FA")
    saldos = [(a["nama"], _saldo_live(a)) for a in accounts if not a["archived"]]
    saldos.sort(key=lambda x: -x[1])
    names = [x[0] for x in saldos]
    vals = [x[1] for x in saldos]
    color_bar = ["#22C55E", "#3B82F6", "#F59E0B", "#8B5CF6", "#EC4899"][:len(names)]
    bars = ax.barh(names[::-1], vals[::-1], color=color_bar[::-1], zorder=3)
    ax.set_xlabel("Saldo (Rp)", fontsize=10)
    ax.set_title("Saldo Live per Rekening", fontsize=13, fontweight="bold",
                 color="#1A1A2E", pad=12)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(
        lambda v, p: f"{v:,.0f}".replace(",", ".")))
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_facecolor("#FFFFFF")
    ax.grid(axis="x", alpha=0.3, linestyle="--", zorder=0)
    for bar, val in zip(bars, vals[::-1]):
        ax.text(val + max(vals) * 0.012, bar.get_y() + bar.get_height() / 2,
                f"Rp {val:,}".replace(",", "."), va="center", fontsize=10,
                fontweight="bold", color="#1A1A2E")
    plt.tight_layout()
    p2 = os.path.join(tmp, "chart2.png")
    plt.savefig(p2, dpi=140, bbox_inches="tight", facecolor="#F5F7FA")
    plt.close()

    # Chart 3: daily cash position
    fig, ax = plt.subplots(figsize=(10, 4))
    fig.patch.set_facecolor("#F5F7FA")
    daily = defaultdict(int)
    for t in transactions:
        if t.get("rekening_id"):
            daily[t["tanggal"]] += (t["jumlah"] if t["tipe"] == "Pemasukan" else -t["jumlah"])
    running = 0
    cum = []
    days = ["sebelum"] + sorted(daily.keys())
    for d in days[1:]:
        running += daily[d]
        cum.append(running)
    ax.plot(days[1:], cum, marker="o", linewidth=2.5, color="#22C55E",
            markersize=10, markerfacecolor="white", markeredgewidth=2.5,
            markeredgecolor="#22C55E", zorder=3)
    ax.fill_between(range(len(cum)), cum, alpha=0.15, color="#22C55E", zorder=2)
    for i, v in enumerate(cum):
        ax.annotate(f"Rp {v:,}".replace(",", "."), (i, v),
                    textcoords="offset points", xytext=(0, 12), ha="center",
                    fontsize=10, fontweight="bold", color="#1A1A2E")
    ax.set_title("Kumulatif Cash Flow", fontsize=13, fontweight="bold",
                 color="#1A1A2E", pad=12)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_facecolor("#FFFFFF")
    ax.grid(axis="y", alpha=0.3, linestyle="--", zorder=0)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(
        lambda v, p: f"{v/1000:.0f}rb".replace(",", ".")))
    plt.tight_layout()
    p3 = os.path.join(tmp, "chart3.png")
    plt.savefig(p3, dpi=140, bbox_inches="tight", facecolor="#F5F7FA")
    plt.close()

    return [(p1, "summary-pengeluaran-cashflow.png"),
            (p2, "summary-saldo-rekening.png"),
            (p3, "summary-cashflow-kumulatif.png")]


def _upload_charts(charts):
    """Upload chart PNGs to Notion. Returns list of file_upload IDs."""
    ids = []
    for fpath, fname in charts:
        s, resp = _notion(
            "POST", "/file_uploads",
            json.dumps({"filename": fname, "content_type": "image/png"}).encode())
        if s != 200:
            print(f"WARN: failed to create upload for {fname}: {s}", file=sys.stderr)
            continue
        upload_id = resp["id"]
        with open(fpath, "rb") as f:
            data = f.read()
        boundary = f"----NotionBoundary{uuid.uuid4().hex[:8]}"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        s, _ = _notion("POST", f"/file_uploads/{upload_id}/send",
                       body, content_type=f"multipart/form-data; boundary={boundary}")
        if s == 200:
            ids.append(upload_id)
    return ids


def _build_blocks(accounts, transactions, today, chart_ids):
    """Build the new page content as a list of Notion block dicts."""
    # Quick stats
    in_total = sum(t["jumlah"] for t in transactions
                   if t["tipe"] == "Pemasukan" and not t.get("archived"))
    out_total = sum(t["jumlah"] for t in transactions
                    if t["tipe"] == "Pengeluaran" and not t.get("archived"))
    net = in_total - out_total
    active = [a for a in accounts if not a["archived"]]
    total_portfolio = sum(a["saldo_awal"] for a in active)

    # Compute live saldo per account
    live_by_id = {}
    for a in accounts:
        s = a["saldo_awal"]
        for t in transactions:
            if t.get("rekening_id") == a["id"] and not t.get("archived"):
                if t["tipe"] == "Pemasukan":
                    s += t["jumlah"]
                elif t["tipe"] == "Pengeluaran":
                    s -= t["jumlah"]
        live_by_id[a["id"]] = s
    live_total = sum(live_by_id[a["id"]] for a in active)

    # Build summary rows
    ICON = {"BCA": "🏦", "Cash": "💵", "Seabank": "🏦",
            "Gopay": "💚", "Bank Jago": "🦊", "Jago → Gopay": "🔄"}

    def cell(text, bold=False):
        """Return a single rich_text run dict.

        Use directly for callout/heading_2 rich_text arrays.
        For table_row.cells, wrap each cell in a list: [cell(...)].
        """
        run = {"type": "text", "text": {"content": text}}
        if bold:
            run["annotations"] = {"bold": True}
        return run

    def cell_for_table(text, bold=False):
        """Return list-of-runs for a single table_row cell.

        Notion table_row.cells expects List[List[rich_text_run]].
        """
        return [cell(text, bold=bold)]

    # Header row + data rows for saldo table
    rows = [
        [cell_for_table("Rekening", bold=True), cell_for_table("Saldo Awal", bold=True),
         cell_for_table("Saldo Live", bold=True), cell_for_table("No. Rekening", bold=True)],
    ]
    for a in sorted(active, key=lambda x: (x.get("urutan") or 999, x["nama"])):
        norek = a.get("nomor_rekening") or "-"
        rows.append([
            cell_for_table(f"{ICON.get(a['nama'], '💼')} {a['nama']}"),
            cell_for_table(f"Rp {a['saldo_awal']:,}".replace(",", ".")),
            cell_for_table(f"Rp {live_by_id[a['id']]:,}".replace(",", ".")),
            cell_for_table(norek),
        ])
    rows.append([
        cell_for_table("TOTAL", bold=True),
        cell_for_table(f"Rp {total_portfolio:,}".replace(",", "."), bold=True),
        cell_for_table(f"Rp {live_total:,}".replace(",", "."), bold=True),
        cell_for_table(f"{len(active)} rekening aktif", bold=True),
    ])

    # Transaction detail rows
    tx_rows = [
        [cell_for_table("Tanggal", bold=True), cell_for_table("Tipe", bold=True),
         cell_for_table("Nama", bold=True), cell_for_table("Kategori", bold=True),
         cell_for_table("Jumlah", bold=True)],
    ]
    today_txs = sorted(
        [t for t in transactions if t["tanggal"] == today and not t.get("archived")],
        key=lambda x: (x["tipe"], -x["jumlah"]),
    )
    for t in today_txs:
        sign = "+" if t["tipe"] == "Pemasukan" else "−"
        icon = "🟢" if t["tipe"] == "Pemasukan" else "🔴"
        tx_rows.append([
            cell_for_table(t["tanggal"]),
            cell_for_table(f"{icon} {t['tipe']}"),
            cell_for_table(t["nama"]),
            cell_for_table(t.get("kategori") or "-"),
            cell_for_table(f"{sign}Rp {t['jumlah']:,}".replace(",", ".")),
        ])

    blocks = [
        {"object": "block", "type": "callout", "callout": {
            "icon": {"type": "emoji", "emoji": "💰"},
            "color": "green_background",
            "rich_text": [cell(
                f"Auto-update: {datetime.now().strftime('%Y-%m-%d %H:%M WIB')} | "
                f"Pemasukan Rp {in_total:,} | Pengeluaran Rp {out_total:,} | "
                f"Net {'+' if net >= 0 else ''}Rp {net:,} | Total {len(active)} rekening aktif | "
                f"Saldo Live Rp {live_total:,}".replace(",", "."), bold=True)],
        }},
        {"object": "block", "type": "heading_2", "heading_2": {
            "rich_text": [cell("📊 Saldo Live (per Rekening)")]}},
        {"object": "block", "type": "table", "table": {
            "table_width": 4, "has_column_header": True, "has_row_header": False,
            "children": [{"type": "table_row", "table_row": {"cells": r}} for r in rows],
        }},
    ]

    # Charts
    if chart_ids:
        blocks.extend([
            {"object": "block", "type": "heading_2", "heading_2": {
                "rich_text": [cell("📈 Visualisasi")]}},
        ])
        captions = [
            "Chart 1: Pengeluaran per kategori + cash flow harian",
            "Chart 2: Saldo live per rekening",
            "Chart 3: Kumulatif cash flow",
        ]
        for cid, cap in zip(chart_ids, captions):
            blocks.append({"object": "block", "type": "image", "image": {
                "type": "file_upload", "file_upload": {"id": cid},
                "caption": [cell(cap)]}})

    # Transaction detail
    if tx_rows and len(tx_rows) > 1:
        blocks.extend([
            {"object": "block", "type": "heading_2", "heading_2": {
                "rich_text": [cell(f"📋 Transaksi {today}")]}},
            {"object": "block", "type": "table", "table": {
                "table_width": 5, "has_column_header": True, "has_row_header": False,
                "children": [{"type": "table_row", "table_row": {"cells": r}}
                             for r in tx_rows],
            }},
        ])

    blocks.append({"object": "block", "type": "callout", "callout": {
        "icon": {"type": "emoji", "emoji": "📌"},
        "color": "gray_background",
        "rich_text": [cell(
            f"Auto-refreshed setiap kali ada transaksi baru (via scripts/refresh_summary.py). "
            f"Page ini di-generate dari KASIO Notion DB. Set KASIO_INCLUDE_CHARTS=0 untuk skip chart.")],
    }})
    return blocks


def main() -> int:
    env = _require_env()
    page_id = env["KASIO_NOTION_SUMMARY_PAGE_ID"]

    nc = _client_mod.NotionClient()
    accounts = nc.list_accounts()
    transactions = nc.list_transactions(limit=1000)
    today = datetime.now().strftime("%Y-%m-%d")

    print(f"📊 Refreshing Notion page {page_id} with {len(accounts)} accounts, "
          f"{len(transactions)} transactions…")
    archived = _archive_all(page_id)
    print(f"   archived {archived} old blocks")

    charts = _make_charts(accounts, transactions, today)
    chart_ids = _upload_charts(charts) if charts else []
    if charts:
        print(f"   uploaded {len(chart_ids)} charts")

    blocks = _build_blocks(accounts, transactions, today, chart_ids)

    # Notion limit per request: 100 blocks. Batch safely at 90.
    BATCH = 90
    appended = 0
    for i in range(0, len(blocks), BATCH):
        batch = blocks[i:i + BATCH]
        s, resp = _notion(
            "PATCH", f"/blocks/{page_id}/children",
            json.dumps({"children": batch}).encode())
        if s in (200, 201):
            appended += len(batch)
        else:
            print(f"ERROR: failed at block {i}: HTTP {s} — {resp}", file=sys.stderr)
            return 2

    print(f"✅ OK: {archived} archived, {appended} new blocks, {len(chart_ids)} charts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
