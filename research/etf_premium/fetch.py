"""Step 1 of the ETF 溢價 study: fetch the raw data into data/raw/etf/ (gitignored) and report the coverage.

Sources (each verified on 0050 before the batch):
- universe: TWSE OpenAPI t187ap47_L (the ETFs listed today, with 基金類型)
- 淨值: MoneyDJ Basic0003BCD (date, 淨值, 市價 — both split-adjusted)
- 市價／成交量: FinMind TaiwanStockPrice (raw, not adjusted; same numbers as TWSE STOCK_DAY)
- 配息: FinMind TaiwanStockDividend (CashExDividendTradingDate, CashEarningsDistribution)
- 加權報酬指數: TWSE MFI94U (monthly pages)

Run: .venv/Scripts/python research/etf_premium/fetch.py   (re-runs skip files already on disk)
"""

from __future__ import annotations

import csv
import json
import sys
import time
from datetime import date
from pathlib import Path

import httpx

RAW = Path(__file__).resolve().parents[2] / "data" / "raw" / "etf"
START, END = date(2020, 1, 1), date.today()
PRICE_START = date(2019, 9, 1)  # the 60-day 均量 on the first days of 2020 needs the autumn before
H = {"User-Agent": "Mozilla/5.0"}
DOMESTIC_TYPES = {"國內成分證券指數股票型基金", "國內成分證券主動式交易所交易基金(股票)"}
PAUSE = 1.5  # seconds between requests to the same site


def get(url: str, **kw) -> httpx.Response:
    for i in range(3):
        try:
            r = httpx.get(url, headers={**H, **kw.pop("headers", {})}, timeout=60, follow_redirects=True, **kw)
            r.raise_for_status()
            return r
        except httpx.HTTPError:
            if i == 2:
                raise
            time.sleep(5 * (i + 1))
    raise RuntimeError


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def universe() -> list[dict]:
    path = RAW / "universe.json"
    if not path.exists():
        r = get("https://openapi.twse.com.tw/v1/opendata/t187ap47_L")
        r.encoding = "utf-8"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(r.json(), ensure_ascii=False, indent=1), encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8"))


def nav(code: str) -> Path:
    path = RAW / "nav" / f"{code}.csv"
    if not path.exists():
        url = f"https://www.moneydj.com/ETF/X/xdjbcd/Basic0003BCD.xdjbcd?etfid={code}.TW&b={START:%Y%m%d}&c={END:%Y%m%d}"
        t = get(url, headers={"Referer": f"https://www.moneydj.com/ETF/X/Basic/Basic0003.xdjhtm?etfid={code}.TW"}).text.strip()
        cols = [p.split(",") for p in t.split(" ")] if t else []
        rows = list(zip(*cols)) if len(cols) == 3 and len({len(c) for c in cols}) == 1 else []
        write_csv(path, ["date", "nav_adj", "price_adj"], rows)
        time.sleep(PAUSE)
    return path


def finmind(dataset: str, code: str, sub: str) -> Path:
    path = RAW / sub / f"{code}.csv"
    if not path.exists():
        j = get("https://api.finmindtrade.com/api/v4/data", params={"dataset": dataset, "data_id": code, "start_date": (PRICE_START if sub == "price" else START).isoformat()}).json()
        if j.get("status") != 200:
            raise RuntimeError(f"FinMind {dataset} {code}: {j.get('msg')}")
        data = j.get("data", [])
        header = list(data[0]) if data else ["date"]
        write_csv(path, header, [[x.get(k) for k in header] for x in data])
        time.sleep(PAUSE)
    return path


def tr_index() -> Path:
    path = RAW / "taiex_tr.csv"
    if not path.exists():
        rows, y, m = [], START.year, START.month
        while (y, m) <= (END.year, END.month):
            j = get(f"https://www.twse.com.tw/rwd/zh/TAIEX/MFI94U?date={y}{m:02d}01&response=json").json()
            for d, v in j.get("data", []):
                ry, rm, rd = (int(x) for x in d.split("/"))
                rows.append([date(ry + 1911, rm, rd).isoformat(), v.replace(",", "")])
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
            time.sleep(3)  # TWSE blocks bursts
        write_csv(path, ["date", "tr_index"], rows)
    return path


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    etfs = [x for x in universe() if x["基金類型"] in DOMESTIC_TYPES]
    print(f"候選 {len(etfs)} 檔")
    tr_index()
    for i, x in enumerate(etfs):
        code = x["基金代號"]
        for fn in (lambda: nav(code), lambda: finmind("TaiwanStockPrice", code, "price"), lambda: finmind("TaiwanStockDividend", code, "dividend")):
            try:
                fn()
            except Exception as e:  # noqa: BLE001 — reported, never filled in
                print(code, "ERR", e)
        print(f"{i + 1}/{len(etfs)} {code}", flush=True)


if __name__ == "__main__":
    main()
