"""The pairs DTW would let back in (m2's rescued_by_dtw), made readable for a human check: names, industry,
the day's change of each, the excess R and the peak gap that threw them out. → out/m2_rescued.md"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import common as C  # noqa: E402


def main() -> None:
    s = C.store()
    data = json.loads(Path(__file__).with_name("out").joinpath("m2.json").read_text(encoding="utf-8"))
    rows = data["rescued_by_dtw"]
    by_day = {}
    lines = ["# DTW 救回的配對（只因高點差被刷掉、形狀和 N 組合一樣像）", "",
             f"共 {data['rescued_count']} 對，這裡列前 {len(rows)} 對。看看是不是你認得的「分批推進」組合。", "",
             "| 日期 | 股票 A | 股票 B | 同產業 | A 漲跌 | B 漲跌 | 扣指數 R | 高點差（分） |", "|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: (x["date"], -(x["excess_r"] or 0))):
        d = date.fromisoformat(r["date"])
        if d not in by_day:
            by_day[d] = {x["code"]: x for x in s.day_rows(d)}
        a, b = (by_day[d].get(c, {}) for c in r["pair"])
        same = "是" if a.get("industry") and a.get("industry") == b.get("industry") else "否"
        pct = lambda x: f"{x['change_pct']:+.2f}%" if x.get("change_pct") is not None else "—"  # noqa: E731
        lines.append(f"| {r['date']} | {a.get('name', r['pair'][0])} {r['pair'][0]} | {b.get('name', r['pair'][1])} {r['pair'][1]} | {same} | {pct(a)} | {pct(b)} | {r['excess_r']:.2f} | {r['peak_gap']} |")
    Path(__file__).with_name("out").joinpath("m2_rescued.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:16]))


if __name__ == "__main__":
    main()
