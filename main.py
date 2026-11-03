"""
FastAPI: สถิติจำนวนผู้เข้าชมสวนสัตว์ ปีงบประมาณ 2569
ข้อมูลจาก Data.go.th (องค์การสวนสัตว์แห่งประเทศไทย ในพระบรมราชูปถัมภ์)

ลำดับการดึงข้อมูล (ไม่สร้างข้อมูลสมมติ):
  1) CKAN datastore_search ด้วย Resource ID
  2) resource_show / package_show เพื่อหา URL ไฟล์ CSV/XLSX/JSON แล้วดาวน์โหลดมาอ่าน
  3) ไฟล์ที่ดาวน์โหลดไว้ในเครื่อง (ตัวแปร DATA_FILE) กรณีเครือข่ายเข้า Data.go.th ไม่ได้
ถ้าทุกวิธีล้มเหลว จะตอบ HTTP 502 พร้อมสาเหตุ แทนการส่งข้อมูลปลอม
"""
import csv
import io
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query

BASE_URL = os.getenv("DATA_GO_TH_BASE", "https://data.go.th").rstrip("/")
DATASET_ID = os.getenv("DATASET_ID", "zoo-visitors2569")
# Resource ID ตามโจทย์เดิมเป็นตารางคำอธิบายประเภทผู้เข้าชม (10 รายการ)
ASSIGNMENT_RESOURCE_ID = "0ecc96cf-d914-42a1-b184-dbbd013c3c9c"

# Resource ID ของไฟล์สถิติผู้เข้าชมรายเดือน (CSV) เดือนสิงหาคม 2569
# มีข้อมูลจำนวนผู้เข้าชมจริงแยกตามสวนสัตว์และประเภทผู้เข้าชม
RESOURCE_ID = os.getenv(
    "RESOURCE_ID",
    "57a5cad9-d533-4bd1-b3a5-e310bc13eeaa"
)
FISCAL_YEAR = int(os.getenv("FISCAL_YEAR", "2569"))
DATA_FILE = os.getenv("DATA_FILE", "")  # เช่น data/zoo-visitors2569.csv
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))  # วินาที

DATASET_PAGE = f"{BASE_URL}/dataset/{DATASET_ID}"
HEADERS = {"User-Agent": "zoo-visitors-api/1.0 (student project)"}

app = FastAPI(
    title="Zoo Visitors API (FY2569)",
    description="RESTful API จากข้อมูลรายงานสถิติจำนวนผู้เข้าชมสวนสัตว์ ปีงบประมาณ 2569 "
    "ขององค์การสวนสัตว์แห่งประเทศไทย ผ่าน Data.go.th",
    version="1.0.0",
)

_cache: dict[str, Any] = {"at": 0.0, "payload": None}


# ---------- ตัวอ่านไฟล์ ----------
def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp874", "tis-620"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_csv(raw: bytes) -> list[dict]:
    text = _decode(raw)
    return [dict(r) for r in csv.DictReader(io.StringIO(text))]


def _parse_xlsx(raw: bytes) -> list[dict]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(h).strip() if h is not None else f"col_{i}" for i, h in enumerate(rows[0])]
    return [dict(zip(header, r)) for r in rows[1:] if any(c is not None for c in r)]


def _parse_json(raw: bytes) -> list[dict]:
    data = json.loads(_decode(raw))
    if isinstance(data, dict):
        for key in ("records", "data", "result", "items"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return data


def _parse_by_hint(raw: bytes, hint: str) -> list[dict]:
    hint = hint.lower()
    if hint.endswith(".xlsx") or hint == "xlsx":
        return _parse_xlsx(raw)
    if hint.endswith(".json") or hint == "json":
        return _parse_json(raw)
    return _parse_csv(raw)


# ---------- แหล่งข้อมูล ----------
def _from_local_file() -> tuple[list[dict], dict]:
    path = Path(DATA_FILE)
    records = _parse_by_hint(path.read_bytes(), path.suffix)
    return records, {"method": "local_file", "file": str(path)}


def _from_datastore(client: httpx.Client) -> tuple[list[dict], dict]:
    url = f"{BASE_URL}/api/3/action/datastore_search"
    records: list[dict] = []
    offset, page = 0, 1000
    while True:
        r = client.get(url, params={"resource_id": RESOURCE_ID, "limit": page, "offset": offset})
        r.raise_for_status()
        body = r.json()
        if not body.get("success"):
            raise RuntimeError(f"datastore_search ไม่สำเร็จ: {body.get('error')}")
        batch = body["result"].get("records", [])
        records.extend(batch)
        total = body["result"].get("total", len(records))
        offset += len(batch)
        if not batch or offset >= total:
            break
    # ตัดคอลัมน์ภายในของ CKAN ออก
    records = [{k: v for k, v in rec.items() if k not in ("_id", "_full_text")} for rec in records]
    return records, {"method": "ckan_datastore_search", "api_url": r.url.__str__().split("?")[0]}


def _from_resource_file(client: httpx.Client) -> tuple[list[dict], dict]:
    resource: dict | None = None
    try:
        r = client.get(f"{BASE_URL}/api/3/action/resource_show", params={"id": RESOURCE_ID})
        r.raise_for_status()
        resource = r.json()["result"]
    except Exception:
        r = client.get(f"{BASE_URL}/api/3/action/package_show", params={"id": DATASET_ID})
        r.raise_for_status()
        resources = r.json()["result"].get("resources", [])
        resource = next((x for x in resources if x.get("id") == RESOURCE_ID), None)
        if resource is None:
            # ไม่พบ ID ตามโจทย์ → ใช้ resource แรกที่เป็น CSV/XLSX/JSON ของ dataset นี้
            order = {"CSV": 0, "XLSX": 1, "JSON": 2}
            usable = [x for x in resources if str(x.get("format", "")).upper() in order]
            usable.sort(key=lambda x: order[str(x["format"]).upper()])
            resource = usable[0] if usable else None
    if not resource or not resource.get("url"):
        raise RuntimeError("ไม่พบ URL ไฟล์ทรัพยากรของชุดข้อมูลนี้")
    file_url = resource["url"]
    fmt = str(resource.get("format") or file_url.rsplit(".", 1)[-1])
    raw = client.get(file_url, follow_redirects=True).content
    return _parse_by_hint(raw, fmt), {
        "method": "resource_file",
        "resource_id": resource.get("id"),
        "resource_name": resource.get("name"),
        "format": fmt,
        "file_url": file_url,
    }


def load_data(force: bool = False) -> dict:
    if not force and _cache["payload"] and time.time() - _cache["at"] < CACHE_TTL:
        return _cache["payload"]

    errors: list[str] = []
    records, info = None, {}

    if DATA_FILE:
        try:
            records, info = _from_local_file()
        except Exception as e:  # noqa: BLE001
            errors.append(f"local_file: {e}")

    if records is None:
        with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
            for fn in (_from_datastore, _from_resource_file):
                try:
                    records, info = fn(client)
                    break
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{fn.__name__}: {e}")

    if records is None:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "ดึงข้อมูลจาก Data.go.th ไม่สำเร็จ (ไม่ได้สร้างข้อมูลสมมติแทน)",
                "errors": errors,
                "hint": "ดาวน์โหลดไฟล์ CSV/XLSX จาก " + DATASET_PAGE + " แล้วตั้งค่า DATA_FILE",
            },
        )

    payload = {
        "source": {
            "title": "ข้อมูลรายงานสถิติจำนวนผู้เข้าชมสวนสัตว์ ปีงบประมาณ 2569",
            "publisher": "องค์การสวนสัตว์แห่งประเทศไทย ในพระบรมราชูปถัมภ์",
            "portal": "Data.go.th",
            "dataset_id": DATASET_ID,
            "dataset_url": DATASET_PAGE,
            "resource_id_assignment": ASSIGNMENT_RESOURCE_ID,
            "resource_id_used": RESOURCE_ID,
            **info,
        },
        "fiscal_year": FISCAL_YEAR,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "columns": list(records[0].keys()) if records else [],
        "total": len(records),
        "records": records,
    }
    _cache.update(at=time.time(), payload=payload)
    return payload


# ---------- Endpoints ----------
@app.get("/", include_in_schema=False)
def root():
    return {"message": "Zoo Visitors API FY2569", "docs": "/docs", "endpoint": "/api/zoo-visitors"}


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}


@app.get("/api/zoo-visitors", tags=["zoo"])
def zoo_visitors(
    limit: int = Query(100, ge=1, le=1000, description="จำนวนรายการสูงสุดที่ส่งกลับ"),
    offset: int = Query(0, ge=0, description="ข้ามรายการแรก N รายการ"),
    refresh: bool = Query(False, description="true = ข้าม cache แล้วดึงใหม่"),
):
    """คืนสถิติผู้เข้าชมสวนสัตว์ ปีงบประมาณ 2569 พร้อมข้อมูลแหล่งที่มา"""
    data = load_data(force=refresh)
    page = data["records"][offset : offset + limit]
    return {**data, "returned": len(page), "limit": limit, "offset": offset, "records": page}
