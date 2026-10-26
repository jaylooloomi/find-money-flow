"""🔥 突然湧入 with more samples: the next day of 🔥 groups (members' mean 當沖比 ≥ 50%) by how far above
their own usual (the 20 trading days before) they day-traded — ≥ 1.3×, 1.5×, 2×, 3× — against the 🔥
groups below 1.3× and the rest. 60 stored days, default 條件, both methods, one entry per member set a day.
Next day: 開盤跳空, 盤中 (close − open), 收盤 less 大盤, 開高走低 share."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("D:/git/open-market")
sys.path.insert(0, str(ROOT / "src"))

from openmarket.history.finder import FinderParams  # noqa: E402
from openmarket.history.linkage import DAYTRADE_HOT, daytrade_ratios  # noqa: E402
from openmarket.history.store import HistoryStore  # noqa: E402


def main() -> None:
    s = HistoryStore(ROOT / "data/history.sqlite")
    p = FinderParams()
    days = sorted(s.trading_dates(limit=400))
    pos = {d: i for i, d in enumerate(days)}
    key = p.key() + "|" + ",".join(sorted(p.groupings))
    groups = set()
    for m in ("known", "new"):
        for d, res in s.finder_days(m, key, any_version=True, min_version=9, groupings=p.groupings):
            for g in res.get("groups", []):
                groups.add((d, tuple(sorted(x["code"] for x in g["members"]))))
    codes = sorted({c for _, ms in groups for c in ms})
    need = sorted({x for d, _ in groups if d in pos for x in days[max(0, pos[d] - 20):pos[d] + 2]})
    ratios = daytrade_ratios(s, need, codes)
    quotes = s.quotes(need, codes)
    index = s.index_rows()

    def mk(d):
        r = index.get(d.isoformat(), {}).get("上市") or {}
        return r["change"] / (r["close"] - r["change"]) * 100 if r.get("change") is not None and r.get("close") else None

    recs = []
    for d, ms in groups:
        if d not in pos or pos[d] + 1 >= len(days):
            continue
        nd = days[pos[d] + 1]
        today = {c: ratios.get(c, {}).get(d) for c in ms}
        known = {c: v for c, v in today.items() if v is not None}
        if not known:
            continue
        avg = round(sum(known.values()) / len(known), 1)
        before = days[max(0, pos[d] - 20):pos[d]]
        xs = []
        for c, v in known.items():
            hist = [r for x in before if (r := ratios.get(c, {}).get(x)) is not None]
            if hist and sum(hist) > 0:
                xs.append(v / (sum(hist) / len(hist)))
        mult = sum(xs) / len(xs) if xs else None
        m = mk(nd)
        for c in ms:
            q = quotes.get(nd, {}).get(c)
            if not q or q.get("change_pct") is None or not q.get("open") or not q.get("prev_close"):
                continue
            recs.append({"hot": avg >= DAYTRADE_HOT, "mult": mult, "gap": (q["open"] / q["prev_close"] - 1) * 100,
                         "intra": (q["close"] / q["open"] - 1) * 100, "excess": q["change_pct"] - m if m is not None else None})

    def line(label, sel):
        if not sel:
            print(f"{label:30s} —")
            return
        n = len(sel)
        mean = lambda k: sum(r[k] for r in sel if r[k] is not None) / max(1, sum(1 for r in sel if r[k] is not None))  # noqa: E731
        fade = sum(1 for r in sel if r["intra"] < 0) / n * 100
        print(f"{label:30s} n={n:4d}  開盤 {mean('gap'):+.2f}%  盤中 {mean('intra'):+.2f}%  收盤扣大盤 {mean('excess'):+.2f}%  開高走低 {fade:.0f}%")

    hot = [r for r in recs if r["hot"]]
    for t in (1.3, 1.5, 2.0, 3.0):
        line(f"🔥 當沖比 ≥ 平常 {t}×", [r for r in hot if r["mult"] is not None and r["mult"] >= t])
    line("🔥 當沖比 < 平常 1.3×", [r for r in hot if r["mult"] is not None and r["mult"] < 1.3])
    line("其他（非 🔥）", [r for r in recs if not r["hot"]])


if __name__ == "__main__":
    main()
