"""The user's ideas (2026-10-25) and a better 補漲, tested on the 60 stored days (default 條件):

A. 同步強的族群回測均線再買: a member of a tightly synced group (≥ 4 of the server's 5 checks, or the
   group's excess R ≥ 0.6) — buy not on the day, but on the first day within the next 10 whose close is
   at or under its 10-day average (the pullback); returns 5 and 10 trading days after that buy, less 大盤.
   Against: buying on the day itself, and any stock touching its 10-day average on the same days.
B. 交集股票: a stock in 2+ of the day's groups (both methods merged) against a stock in just one.
C. 補漲, other definitions than tonight's (a laggard of the group's small known home):
   C0 tonight's list
   C1 … that still rose today with 量比 ≥ 1.5 (money arriving, just less of it)
   C2 … whose daily R with the group's members over 20 days is ≥ 0.6 (they usually move together)
   C3 … of a group seen on 2+ of the last 5 days (a run, not a one-day burst)
   C4 C1 and C2 together
All returns: the stock's close-to-close less 大盤's over the same span; 1/3/5-day horizons (A: 5/10).
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

import httpx

ROOT = Path("D:/git/open-market")
sys.path.insert(0, str(ROOT / "src"))

from openmarket.history.finder import daily_series, volume_ratio  # noqa: E402
from openmarket.history.linkage import evidence_yes  # noqa: E402
from openmarket.history.store import HistoryStore  # noqa: E402


def pearson(x, y):
    pts = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    if len(pts) < 10:
        return None
    mx = sum(a for a, _ in pts) / len(pts)
    my = sum(b for _, b in pts) / len(pts)
    sxy = sum((a - mx) * (b - my) for a, b in pts)
    sxx = sum((a - mx) ** 2 for a, _ in pts)
    syy = sum((b - my) ** 2 for _, b in pts)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def main() -> None:
    s = HistoryStore(ROOT / "data/history.sqlite")
    days = sorted(s.trading_dates(limit=400))
    pos = {d: i for i, d in enumerate(days)}
    with s._conn() as c:
        cached = sorted({r["date"] for r in c.execute("SELECT date FROM yahoo_5m GROUP BY date HAVING COUNT(*) >= 800")})
    closes: dict[str, dict[date, float]] = {}
    for d in days:
        for r in s.day_rows(d):
            if r.get("close") and r.get("kind") == "stock":
                closes.setdefault(r["code"], {})[d] = r["close"]
    index = s.index_rows()
    idx = {d: (index.get(d.isoformat(), {}).get("上市") or {}).get("close") for d in days}

    def ret(code, d0, d1):
        a, b = closes.get(code, {}).get(d0), closes.get(code, {}).get(d1)
        ia, ib = idx.get(d0), idx.get(d1)
        return (b / a - 1) * 100 - (ib / ia - 1) * 100 if a and b and ia and ib else None

    def fwd(code, d, h):
        i = pos.get(d)
        return ret(code, d, days[i + h]) if i is not None and i + h < len(days) else None

    def ma(code, d, n=10):
        i = pos[d]
        xs = [closes.get(code, {}).get(x) for x in days[max(0, i - n + 1):i + 1]]
        xs = [x for x in xs if x]
        return sum(xs) / len(xs) if len(xs) == n else None

    per_day = {}
    for ds in cached:
        d = date.fromisoformat(ds)
        gs = []
        for m in ("known", "new"):
            j = httpx.get("http://127.0.0.1:8765/api/history/finder", params={"date": ds, "method": m, "stored_only": "1"}, timeout=120).json()
            gs += j.get("groups", [])
        per_day[d] = gs

    def summarize(xs):
        xs = [x for x in xs if x is not None]
        if not xs:
            return None
        return {"n": len(xs), "mean": round(sum(xs) / len(xs), 2), "win": round(sum(1 for x in xs if x > 0) / len(xs) * 100)}

    out = {}
    # ---- A: the pullback buy
    synced, buy_now, buy_pull, base_touch = set(), {5: [], 10: []}, {5: [], 10: []}, {5: [], 10: []}
    for d, gs in per_day.items():
        for g in gs:
            if evidence_yes(g) >= 4 or (g.get("excess_r") or 0) >= 0.6:
                for m in g["members"]:
                    synced.add((d, m["code"]))
    pulled = []
    for d, code in synced:
        i = pos.get(d)
        if i is None:
            continue
        for h in (5, 10):
            buy_now[h].append(fwd(code, d, h))
        for k in range(1, 11):
            if i + k >= len(days):
                break
            dk = days[i + k]
            mk, ck = ma(code, dk), closes.get(code, {}).get(dk)
            if mk and ck and ck <= mk:
                pulled.append((dk, code))
                for h in (5, 10):
                    buy_pull[h].append(fwd(code, dk, h))
                break
    touch_days = {dk for dk, _ in pulled}
    for dk in touch_days:  # the baseline: any common stock touching its 10-day average that day
        for code in list(closes)[::7]:
            mk, ck = ma(code, dk), closes.get(code, {}).get(dk)
            if mk and ck and ck <= mk:
                for h in (5, 10):
                    base_touch[h].append(fwd(code, dk, h))
    out["A_pullback"] = {"synced_stock_days": len(synced), "pullbacks_within_10_days": len(pulled),
                         "buy_on_the_day": {h: summarize(v) for h, v in buy_now.items()},
                         "buy_on_the_pullback": {h: summarize(v) for h, v in buy_pull.items()},
                         "any_stock_at_its_10MA": {h: summarize(v) for h, v in base_touch.items()}}
    # ---- B: 交集股票
    multi, single = {1: [], 3: [], 5: []}, {1: [], 3: [], 5: []}
    for d, gs in per_day.items():
        count = {}
        for g in gs:
            for m in {x["code"] for x in g["members"]}:
                count[m] = count.get(m, 0) + 1
        for code, n in count.items():
            for h in (1, 3, 5):
                (multi if n >= 2 else single)[h].append(fwd(code, d, h))
    out["B_intersection"] = {"in_2plus_groups": {h: summarize(v) for h, v in multi.items()}, "in_1_group": {h: summarize(v) for h, v in single.items()}}
    # ---- C: 補漲 definitions
    variants = {k: {1: [], 3: [], 5: []} for k in ("C0", "C1", "C2", "C3", "C4")}
    for d, gs in per_day.items():
        vr = volume_ratio(s, d, 5)
        series = daily_series(s, d, 20)
        rows = {r["code"]: r for r in s.day_rows(d)}
        for g in gs:
            lag = (g.get("laggards") or {}).get("stocks") or []
            members = [m["code"] for m in g["members"]]
            seen = len(g.get("seen_days") or [])
            for x in lag:
                c = x["code"]
                up_money = (rows.get(c, {}).get("change_pct") or 0) > 0 and (vr.get(c) or 0) >= 1.5
                rs = [r for m in members if (r := pearson(series.get(c, []), series.get(m, []))) is not None]
                moves_with = bool(rs) and sum(rs) / len(rs) >= 0.6
                for k, ok in (("C0", True), ("C1", up_money), ("C2", moves_with), ("C3", seen >= 1), ("C4", up_money and moves_with)):
                    if ok:
                        for h in (1, 3, 5):
                            variants[k][h].append(fwd(c, d, h))
    out["C_laggards"] = {k: {h: summarize(v) for h, v in hs.items()} for k, hs in variants.items()}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    Path(__file__).with_name("out").joinpath("exp8.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
