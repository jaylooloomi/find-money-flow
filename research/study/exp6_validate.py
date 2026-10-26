"""Did tonight's two report lists mean anything afterwards?
  補漲候選 (laggards: the same small known group's stocks that lagged) — did they catch up over 1/3/5 days,
      against 大盤 and against the group itself?
  高點錯開但走勢一樣 (shape_pairs) — how did those pairs do, against 大盤 and against N's own groups?
Read through the running server's /api/history/finder (stored results, default 條件, 60 days) so the
lists are exactly what the report shows; returns from the stored closes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path("D:/git/open-market")
sys.path.insert(0, str(ROOT / "src"))

from openmarket.history.store import HistoryStore  # noqa: E402

HORIZONS = (1, 3, 5)


def main() -> None:
    s = HistoryStore(ROOT / "data/history.sqlite")
    days = sorted(s.trading_dates(limit=400))
    pos = {d: i for i, d in enumerate(days)}
    with s._conn() as c:
        cached = sorted({r["date"] for r in c.execute("SELECT date FROM yahoo_5m GROUP BY date HAVING COUNT(*) >= 800")})
    lists = {"laggard": [], "group": [], "shape": []}  # (day, [codes])
    for ds in cached:
        for m in ("known", "new"):
            j = httpx.get("http://127.0.0.1:8765/api/history/finder", params={"date": ds, "method": m, "stored_only": "1"}, timeout=120).json()
            for g in j.get("groups", []):
                lists["group"].append((ds, [x["code"] for x in g["members"]]))
                lag = g.get("laggards")
                if lag and lag.get("stocks"):
                    lists["laggard"].append((ds, [x["code"] for x in lag["stocks"]]))
            for x in j.get("shape_pairs", []) or []:
                lists["shape"].append((ds, [y["code"] for y in x["members"]]))
    codes = sorted({c for v in lists.values() for _, cs in v for c in cs})
    closes = {}
    for d in days:
        for r in s.day_rows(d):
            if r["code"] in codes and r.get("close"):
                closes.setdefault(r["code"], {})[d] = r["close"]
    index = s.index_rows()
    idx = {d: (index.get(d.isoformat(), {}).get("上市") or {}).get("close") for d in days}
    from datetime import date

    def fwd(code, d, h):
        i = pos.get(d)
        if i is None or i + h >= len(days):
            return None
        a, b = closes.get(code, {}).get(d), closes.get(code, {}).get(days[i + h])
        ia, ib = idx.get(d), idx.get(days[i + h])
        return (b / a - 1) * 100 - (ib / ia - 1) * 100 if a and b and ia and ib else None

    out = {}
    for name, items in lists.items():
        seen = set()
        row = {}
        for h in HORIZONS:
            xs = []
            for ds, cs in items:
                d = date.fromisoformat(ds)
                for c in cs:
                    if (d, c, h) in seen:
                        continue
                    seen.add((d, c, h))
                    v = fwd(c, d, h)
                    if v is not None:
                        xs.append(v)
            row[h] = {"n": len(xs), "mean": round(sum(xs) / len(xs), 2) if xs else None, "up": round(sum(1 for x in xs if x > 0) / len(xs) * 100) if xs else None}
        out[name] = row
        print(f"{name:8s} " + " | ".join(f"{h}日 {row[h]['mean']:+.2f}% 跑贏大盤 {row[h]['up']}% (n={row[h]['n']})" for h in HORIZONS if row[h]["mean"] is not None))
    Path(__file__).with_name("out").joinpath("exp6.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
