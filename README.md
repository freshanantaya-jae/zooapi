# Zoo Visitors API (ปีงบประมาณ 2569)

FastAPI จากข้อมูล Data.go.th: ข้อมูลรายงานสถิติจำนวนผู้เข้าชมสวนสัตว์ ปีงบประมาณ 2569
(Dataset `zoo-visitors2569`, Resource ID `0ecc96cf-d914-42a1-b184-dbbd013c3c9c`)

## รัน

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
fastapi dev main.py                # หรือ uvicorn main:app --host 0.0.0.0 --port 8000
```

- API:  http://127.0.0.1:8000/api/zoo-visitors
- Docs: http://127.0.0.1:8000/docs

## ที่มาของข้อมูล (ไม่สร้างข้อมูลสมมติ)

1. เรียก CKAN `datastore_search` ด้วย Resource ID
2. ถ้าไม่ได้ → หา URL ไฟล์ CSV/XLSX/JSON จาก `resource_show` / `package_show` แล้วอ่านไฟล์
3. ถ้าเครือข่ายเข้าไม่ได้ → ดาวน์โหลดไฟล์จากหน้า dataset เอง แล้วตั้ง `DATA_FILE`

```bash
DATA_FILE=data/zoo-visitors2569.csv fastapi dev main.py     # Windows PowerShell: $env:DATA_FILE="data\zoo-visitors2569.csv"
```

ถ้าดึงไม่ได้ทุกวิธี API ตอบ `502` พร้อมสาเหตุ

## Query parameters

`limit` (1-1000, ค่าเริ่มต้น 100), `offset`, `refresh=true` (ข้าม cache)

## ตัวแปรสภาพแวดล้อม

`DATASET_ID`, `RESOURCE_ID`, `FISCAL_YEAR`, `DATA_FILE`, `DATA_GO_TH_BASE`, `CACHE_TTL`
