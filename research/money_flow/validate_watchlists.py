"""Re-checks the two report features that came from research/exp8, on the system's own output:

- 回測 10 日線 (linkage.pullbacks): the 10-day return, less 大盤, of each stock listed — against buying the
  same tight-sync members on the sync day (exp8: +4.31% vs +1.63%).
- ⚠ 帶量跟漲 (finder.flag_chasers): the next-day and 5-day return, less 大盤, of the flagged laggards —
  against the unflagged ones (exp8 C4: next day −1.63%, 22% up).

Days are split by date: --cut YYYY-MM-DD (default 2026-10-22, the last day exp8 saw) gives the in-sample days
up to it and the out-of-sample days after it; the in-sample days are also split into halves. Run again as new
days are stored: the out-of-sample rows are the real test.

Needs the app running on :8765 (N results stored for those days).
Run: .venv/Scripts/python research/money_flow/validate_watchlists.py [--cut 2026-10-22]
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from openmarket.history.linkage import PULLBACK_DAYS, is_tight_sync, pullbacks  # noqa: E402
from openmarket.history.store import HistoryStore  # noqa: E402

API = "http://127.0.0.1:8765"


def summarize(xs: list[float]) -> str:
    xs = [x for x in xs if x is not None]
    if not xs:
        return "—"
    xs.sort()
    med = xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2
    return f"n={len(xs)} 平均 {sum(xs) / len(xs):+.2f}% 中位數 {med:+.2f}% 勝率 {100 * sum(x > 0 for x in xs) / len(xs):.0f}%"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    cut = date.fromisoformat(sys.argv[sys.argv.index("--cut") + 1]) if "--cut" in sys.argv else date(2026, 10, 22)
    s = HistoryStore(ROOT / "data" / "history.sqlite")
    days = sorted(s.trading_dates(limit=400))
    pos = {d: i for i, d in enumerate(days)}
    index = s.index_rows()
    idx = {d: (index.get(d.isoformat(), {}).get("上市") or {}).get("close") for d in days}
    closes: dict[str, dict[date, float]] = {}
    for d in days[-120:]:
        for r in s.day_rows(d):
            if r.get("close") and r.get("kind") == "stock":
                closes.setdefault(r["code"], {})[d] = r["close"]

    def fwd(code: str, d: date, h: int) -> float | None:
        i = pos.get(d)
        if i is None or i + h >= len(days):
            return None
        d1 = days[i + h]
        a, b, ia, ib = closes.get(code, {}).get(d), closes.get(code, {}).get(d1), idx.get(d), idx.get(d1)
        return (b / a - 1) * 100 - (ib / ia - 1) * 100 if a and b and ia and ib else None

    # every stored day's N groups (both methods), as the report sees them
    per_day: dict[date, list[dict]] = {}
    for d in days[-90:]:
        gs = []
        for m in ("known", "new"):
            j = httpx.get(f"{API}/api/history/finder", params={"date": d.isoformat(), "method": m, "stored_only": "1"}, timeout=120).json()
            gs += j.get("groups", [])
        if gs:
            per_day[d] = gs
    stored = sorted(per_day)
    print(f"有 N 結果的日子：{len(stored)}（{stored[0]}～{stored[-1]}）；切點 {cut}")
    synced = [(d, g["members"]) for d in stored for g in per_day[d] if is_tight_sync(g)]

    rows = []  # (day, kind, code, r1, r5, r10)
    for d in stored:
        for x in pullbacks(d, days, synced, closes):
            rows.append((d, "pull", x["code"], None, fwd(x["code"], d, 5), fwd(x["code"], d, 10)))
        for g in per_day[d]:
            if is_tight_sync(g):
                for m in g["members"]:
                    rows.append((d, "sync", m["code"], None, fwd(m["code"], d, 5), fwd(m["code"], d, 10)))
            for st in (g.get("laggards") or {}).get("stocks") or []:
                kind = "chase" if st.get("chase") else "lag"
                rows.append((d, kind, st["code"], fwd(st["code"], d, 1), fwd(st["code"], d, 5), None))

    ins = [d for d in stored if d <= cut]
    half = ins[len(ins) // 2] if ins else cut
    parts = [("樣本內 前半", lambda d: d < half), ("樣本內 後半", lambda d: half <= d <= cut), ("樣本外（切點之後）", lambda d: d > cut)]
    for name, ok in parts:
        sub = [r for r in rows if ok(r[0])]
        print(f"\n## {name}")
        print("  回測 10 日線（10 日）：", summarize([r[5] for r in sub if r[1] == "pull"]))
        print("  同步當天就買（10 日）：", summarize([r[5] for r in sub if r[1] == "sync"]))
        print("  ⚠ 帶量跟漲（隔天）　：", summarize([r[3] for r in sub if r[1] == "chase"]))
        print("  其他未跟上（隔天）　：", summarize([r[3] for r in sub if r[1] == "lag"]))
        print("  ⚠ 帶量跟漲（5 日）　：", summarize([r[4] for r in sub if r[1] == "chase"]))
    print(f"\n（回測名單的同步窗口 {PULLBACK_DAYS} 天；報酬都扣大盤；同一檔在不同天出現會重複計）")


if __name__ == "__main__":
    main()
