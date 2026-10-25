"""The official 淨值: 公開資訊觀測站 t78sb35 (基金每日淨資產價值), one page per trading day with every ETF's
淨值, 前一日淨值, 當日收盤價 and 折溢價 — the primary 淨值 source (MoneyDJ is only the cross-check).

Saves data/raw/etf/mops/YYYYMMDD.html (the page as served) and .csv (parsed); re-runs skip days on disk.
Trading days come from data/raw/etf/taiex_tr.csv (fetch.py first).

Run: .venv/Scripts/python research/etf_premium/fetch_mops.py            (指數型, from 2020)
     .venv/Scripts/python research/etf_premium/fetch_mops.py --active   (主動式, from 2025-04; into mops_active/)
"""

from __future__ import annotations

import csv
import re
import sys
import time
from pathlib import Path

import httpx

RAW = Path(__file__).resolve().parents[2] / "data" / "raw" / "etf"
URL = "https://mopsov.twse.com.tw/mops/web/ajax_t78sb35"  # 國內成分證券指數股票型基金
URL_ACTIVE = "https://mopsov.twse.com.tw/mops/web/ajax_t78sb39_q1"  # 國內成分主動式 ETF (the menu's labels are the other way round)
ACTIVE_FROM = "2025-04-01"  # the first domestic active ETFs listed in 2025
PAUSE = 3.0  # MOPS blocks bursts
CELL = r"\s*<td[^>]*>\s*(.*?)\s*</td>"
ROW = re.compile(r"<td\s+class='\w+'\s+rowspan='\d+'>\s*(00[0-9A-Z]+)\s*</td>(?:\s*<th[^>]*>.*?</th>|\s*</tr>|\s*<tr[^>]*>)*" + CELL * 8, re.S)


def parse(html: str) -> list[list[str]]:
    """[[code, name, nav_date, nav, prev_nav, nav_chg, nav_chg_pct, close, premium_pct]] — the fund row under each code."""
    return [[m[0], m[1], m[2].replace("/", ""), *(x.replace(",", "") for x in m[3:])] for m in ROW.findall(html)]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    active = "--active" in sys.argv
    url, out = (URL_ACTIVE, RAW / "mops_active") if active else (URL, RAW / "mops")
    out.mkdir(parents=True, exist_ok=True)
    with (RAW / "taiex_tr.csv").open(encoding="utf-8") as f:
        days = [r["date"] for r in csv.DictReader(f) if not active or r["date"] >= ACTIVE_FROM]
    for i, d in enumerate(days):
        path = out / f"{d.replace('-', '')}.csv"
        if path.exists():
            continue
        y, m, dd = d.split("-")
        for attempt in range(3):
            try:
                r = httpx.post(url, data={"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1", "TYPEK": "all",
                                          "year": str(int(y) - 1911), "month": m, "day": dd}, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
                r.raise_for_status()
                if "安全性考量" in r.text:
                    raise RuntimeError("blocked")
                break
            except Exception as e:  # noqa: BLE001
                print(d, "retry", e, flush=True)
                time.sleep(60 * (attempt + 1))
        else:
            print(d, "FAILED", flush=True)
            continue
        (out / f"{d.replace('-', '')}.html").write_text(r.text, encoding="utf-8")
        rows = parse(r.text)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["code", "name", "nav_date", "nav", "prev_nav", "nav_chg", "nav_chg_pct", "close", "premium_pct"])
            w.writerows(rows)
        print(f"{i + 1}/{len(days)} {d} {len(rows)}", flush=True)
        time.sleep(PAUSE)


if __name__ == "__main__":
    main()
