"""交集股票, redefined (exp8 B did not hold: "in 2+ groups the same day" is mostly one set of stocks under two
labels). A stock is a 交集 when that day it is in 2+ N groups whose OTHER members do not overlap at all — it
moves with two different sets of stocks — and (strict) those groups' best-matching classifications differ.

Returns, less 大盤, 1/3/5/10 days after the day, against the stocks in just one group; split by date into halves.
Also: does a 交集 member of a tight-sync group make a better 回測 10 日線 pick?

Needs the app running on :8765. Run: .venv/Scripts/python research/money_flow/intersection.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from openmarket.history.linkage import is_tight_sync, pullbacks  # noqa: E402
from openmarket.history.store import HistoryStore  # noqa: E402

API = "http://127.0.0.1:8765"
HORIZONS = (1, 3, 5, 10)


def label(g: dict) -> str:
    m = max(g.get("matches") or [{}], key=lambda x: x.get("overlap", 0))
    return f"{m.get('grouping')}:{m.get('group')}" if m else "?"


def summ(xs: list[float]) -> str:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return "—"
    med = xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2
    return f"n={len(xs):4d} 平均 {sum(xs) / len(xs):+.2f}% 中位數 {med:+.2f}% 勝率 {100 * sum(x > 0 for x in xs) / len(xs):3.0f}%"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    s = HistoryStore(ROOT / "data" / "history.sqlite")
    days = sorted(s.trading_dates(limit=400))
    pos = {d: i for i, d in enumerate(days)}
    idx = {d: (s.index_rows().get(d.isoformat(), {}).get("上市") or {}).get("close") for d in days}
    closes: dict[str, dict[date, float]] = defaultdict(dict)
    for d in days[-120:]:
        for r in s.day_rows(d):
            if r.get("close") and r.get("kind") == "stock":
                closes[r["code"]][d] = r["close"]

    def fwd(code, d, h):
        i = pos.get(d)
        if i is None or i + h >= len(days):
            return None
        d1 = days[i + h]
        a, b, ia, ib = closes[code].get(d), closes[code].get(d1), idx.get(d), idx.get(d1)
        return (b / a - 1) * 100 - (ib / ia - 1) * 100 if a and b and ia and ib else None

    per_day: dict[date, list[dict]] = {}
    for d in days[-90:]:
        gs, seen = [], set()
        for m in ("known", "new"):
            for g in httpx.get(f"{API}/api/history/finder", params={"date": d.isoformat(), "method": m, "stored_only": "1"}, timeout=120).json().get("groups", []):
                key = tuple(sorted(x["code"] for x in g["members"]))
                if key not in seen:  # the same member set found by both methods: once
                    seen.add(key)
                    gs.append(g)
        if gs:
            per_day[d] = gs
    stored = sorted(per_day)
    half = stored[len(stored) // 2]
    kinds: dict[tuple[date, str], str] = {}  # (day, code) → single / loose (2+ groups) / cross / strict
    for d, gs in per_day.items():
        by: dict[str, list[dict]] = defaultdict(list)
        for g in gs:
            for m in g["members"]:
                by[m["code"]].append(g)
        for code, mine in by.items():
            if len(mine) == 1:
                kinds[(d, code)] = "single"
                continue
            others = [{x["code"] for x in g["members"]} - {code} for g in mine]
            disjoint = any(not (others[i] & others[j]) for i in range(len(mine)) for j in range(i + 1, len(mine)))
            strict = disjoint and any(not (others[i] & others[j]) and label(mine[i]) != label(mine[j]) for i in range(len(mine)) for j in range(i + 1, len(mine)))
            kinds[(d, code)] = "strict" if strict else "cross" if disjoint else "loose"
    names = {"single": "只在 1 組", "loose": "2+ 組但夥伴重疊（舊定義的大多數）", "cross": "跨組交集（夥伴不重疊）", "strict": "跨組交集＋不同分類"}
    print(f"N 結果 {len(stored)} 天（{stored[0]}～{stored[-1]}）；報酬扣大盤")
    for k in ("single", "loose", "cross", "strict"):
        print(f"\n## {names[k]}")
        for h in HORIZONS:
            row = []
            for lab, ok in (("全部", lambda d: True), ("前半", lambda d: d < half), ("後半", lambda d: d >= half)):
                row.append(f"{lab} {summ([fwd(c, d, h) for (d, c), v in kinds.items() if v == k and ok(d)])}")
            print(f"  {h:2d} 日  " + "  |  ".join(row))
    # 回測 10 日線, split by whether the stock was a 交集 on its sync day
    synced = [(d, g["members"]) for d in stored for g in per_day[d] if is_tight_sync(g)]
    cross_days = {(d, c) for (d, c), v in kinds.items() if v in ("cross", "strict")}
    picks = []  # (day, code, sync day, is a 交集 that sync day, 10-day return)
    for d in stored:
        for x in pullbacks(d, days, synced, closes):
            since = date.fromisoformat(x["since"])
            picks.append((d, x["code"], since, (since, x["code"]) in cross_days, fwd(x["code"], d, 10)))
    print("\n## 回測 10 日線（10 日後）")
    for lab, ok in (("全部", lambda d: True), ("前半", lambda d: d < half), ("後半", lambda d: d >= half)):
        for is_x, name in ((True, "同步那天是跨組交集"), (False, "其他")):
            sub = [p for p in picks if p[3] == is_x and ok(p[0]) and p[4] is not None]
            print(f"  {lab} {name}：{summ([p[4] for p in sub])}（{len({p[1] for p in sub})} 檔、{len({p[0] for p in sub})} 個買點日、{len({p[2] for p in sub})} 個同步日）")
    for p in sorted(x for x in picks if x[3] and x[4] is not None):
        print("   ", p[0], p[1], "同步", p[2], f"{p[4]:+.1f}%")


if __name__ == "__main__":
    main()
