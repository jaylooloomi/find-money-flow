"""延續性: which features of an N group tell that it keeps going (3 / 5 days after, less 大盤)? Kept apart from
證據強 ("the same money"), which exp5 found to say nothing about follow-through.

Unit: a group-day (one per member set), its members' average return less 大盤.
1. Each feature alone: the top third against the bottom third, in each half of the days.
2. A score from the features chosen on the first half only (the sign each had there), tested on the second half.

Needs the app running on :8765. Run: .venv/Scripts/python research/money_flow/persistence.py
"""

from __future__ import annotations

import random
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from openmarket.history.linkage import evidence_yes  # noqa: E402
from openmarket.history.store import HistoryStore  # noqa: E402

API = "http://127.0.0.1:8765"


def minutes(t: str | None) -> float | None:
    if not t:
        return None
    h, m = t.split(":")
    return int(h) * 60 + int(m) - 9 * 60


def surge(dt: dict | None) -> float | None:
    xs = [v / dt["base"][c] for c, v in (dt or {}).get("by_code", {}).items() if (dt.get("base") or {}).get(c)]
    return sum(xs) / len(xs) if xs else None


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


FEATURES = {
    "連續出現天數": lambda g: len(g.get("seen_days") or []),
    "今天平均漲幅": lambda g: g.get("avg_pct"),
    "扣指數 R": lambda g: g.get("excess_r"),
    "證據 ✓ 數": lambda g: evidence_yes(g),
    "當沖比": lambda g: (g.get("daytrade") or {}).get("avg"),
    "當沖比倍數": lambda g: surge(g.get("daytrade")),
    "法人買超（億）": lambda g: (g.get("inst") or {}).get("amount_100m"),
    "投信買超（張）": lambda g: (g.get("inst_split") or {}).get("trust"),
    "融資增減（張）": lambda g: (g.get("margin") or {}).get("margin_chg"),
    "週轉占比": lambda g: g.get("turnover_share"),
    "高點時間（分）": lambda g: mean([minutes(m.get("peak")) for m in g["members"]]),
    "起漲時間（分）": lambda g: mean([minutes(m.get("start")) for m in g["members"]]),
    "鎖漲停占比": lambda g: mean([1.0 if m.get("locked_at") else 0.0 for m in g["members"]]),
}


def tercile_gap(rows: list[tuple[float, float]]) -> tuple[float | None, int]:
    """mean(return) of the top third by feature − the bottom third; n per third."""
    rng = random.Random(3)  # ties (a discrete feature) in a random order — never by the return itself
    rows = [(f, r) for f, _, r in sorted((f, rng.random(), r) for f, r in rows if f is not None and r is not None)]
    k = len(rows) // 3
    if k < 5:
        return None, k
    lo, hi = [r for _, r in rows[:k]], [r for _, r in rows[-k:]]
    return sum(hi) / k - sum(lo) / k, k


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

    groups: list[tuple[date, dict]] = []
    for d in days[-90:]:
        seen = set()
        for m in ("known", "new"):
            for g in httpx.get(f"{API}/api/history/finder", params={"date": d.isoformat(), "method": m, "stored_only": "1"}, timeout=120).json().get("groups", []):
                key = tuple(sorted(x["code"] for x in g["members"]))
                if key not in seen:
                    seen.add(key)
                    groups.append((d, g))
    gdays = sorted({d for d, _ in groups})
    half = gdays[len(gdays) // 2]
    print(f"組合 {len(groups)} 個，{len(gdays)} 天（{gdays[0]}～{gdays[-1]}）；前半 < {half}；報酬扣大盤，成員平均\n")
    ret = {h: [mean([fwd(m["code"], d, h) for m in g["members"]]) for d, g in groups] for h in (3, 5)}
    chosen: dict[int, dict[str, int]] = {3: {}, 5: {}}
    for h in (3, 5):
        print(f"## {h} 日後：前三分之一 − 後三分之一（平均報酬差，%）")
        for name, fn in FEATURES.items():
            vals = [fn(g) for _, g in groups]
            cells = []
            signs = []
            for lab, ok in (("前半", lambda d: d < half), ("後半", lambda d: d >= half)):
                gap, k = tercile_gap([(v, r) for (d, _), v, r in zip(groups, vals, ret[h]) if ok(d)])
                cells.append(f"{lab} {gap:+.2f}（每組 {k}）" if gap is not None else f"{lab} —")
                signs.append(gap)
            same = signs[0] is not None and signs[1] is not None and (signs[0] > 0) == (signs[1] > 0)
            print(f"  {name:10s}  " + "  ".join(cells) + ("  ← 前後一致" if same and min(abs(signs[0]), abs(signs[1])) >= 0.5 else ""))
            if signs[0] is not None and abs(signs[0]) >= 1.0:  # chosen on the first half alone
                chosen[h][name] = 1 if signs[0] > 0 else -1
        print()
    # the score: chosen on the first half, judged on the second
    for h in (3, 5):
        feats = chosen[h]
        print(f"## {h} 日分數（前半挑的特徵：{', '.join(f'{n}{"+" if s > 0 else "−"}' for n, s in feats.items()) or '無'}）")
        if not feats:
            continue
        # each feature as its rank within the first half (no peeking at the second half's spread)
        first = [(d, g) for d, g in groups if d < half]
        cuts = {n: sorted(v for v in (FEATURES[n](g) for _, g in first) if v is not None) for n in feats}

        def score(g):
            tot = 0.0
            for n, sgn in feats.items():
                v, c = FEATURES[n](g), cuts[n]
                if v is None or not c:
                    continue
                tot += sgn * (sum(x <= v for x in c) / len(c) - 0.5)
            return tot

        for lab, ok in (("前半（挑特徵用，會偏好看）", lambda d: d < half), ("後半（真正的檢驗）", lambda d: d >= half)):
            pts = [(score(g), r) for (d, g), r in zip(groups, ret[h]) if ok(d) and r is not None]
            gap, k = tercile_gap(pts)
            hi = sorted(pts, key=lambda x: x[0])[-k:] if k else []
            print(f"  {lab}：高分三分之一 − 低分三分之一 = {gap:+.2f}%（每組 {k}）；高分組勝率 {100 * sum(r > 0 for _, r in hi) / len(hi):.0f}%" if gap is not None else f"  {lab}：—")
        print()


if __name__ == "__main__":
    main()
