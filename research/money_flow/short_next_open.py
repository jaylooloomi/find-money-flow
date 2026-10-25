"""高同步 + 高當沖比 → 隔天開盤放空, on the system's own stored N groups:

For each stock-day in an N group: short at the next trading day's open, cover at that day's close
(先賣後買的當沖). Return = (open − close) ÷ open. Cells by the group's flags on the signal day:
  同步強 (linkage.is_tight_sync), 🔥 當沖比高 (daytrade.hot, members' average ≥ 50%),
  突然湧入 (當沖比 ≥ SURGE_X × the members' usual).
Benchmark: the same short on every common stock that next day (the market's own open→close).
Costs: 當沖證交稅 0.15% + 手續費 0.1425% × 2 × 0.28 (a 2.8 折 discount) ≈ 0.23% a round trip (an assumption).
A stock in several groups the same day counts once. Split by date into halves.

Needs the app running on :8765. Run: .venv/Scripts/python research/money_flow/short_next_open.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from openmarket.history.linkage import SURGE_X, is_tight_sync  # noqa: E402
from openmarket.history.store import HistoryStore  # noqa: E402

API = "http://127.0.0.1:8765"
COST = 0.15 + 0.1425 * 2 * 0.28  # percent, a round trip


def surge(dt: dict | None) -> float | None:
    xs = [v / dt["base"][c] for c, v in (dt or {}).get("by_code", {}).items() if (dt.get("base") or {}).get(c)]
    return sum(xs) / len(xs) if xs else None


def stats(xs: list[float], days: int) -> str:
    if not xs:
        return "—"
    s = sorted(xs)
    med = s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2
    net = [x - COST for x in xs]
    return (f"n={len(xs):4d}（{days:2d} 天） 平均 {sum(xs) / len(xs):+.2f}%  中位數 {med:+.2f}%  "
            f"勝率 {100 * sum(x > 0 for x in xs) / len(xs):3.0f}%  扣成本後平均 {sum(net) / len(net):+.2f}%、勝率 {100 * sum(x > 0 for x in net) / len(net):3.0f}%")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    s = HistoryStore(ROOT / "data" / "history.sqlite")
    days = sorted(s.trading_dates(limit=400))
    nxt = {d: days[i + 1] for i, d in enumerate(days[:-1])}
    picks: dict[tuple[date, str], dict] = {}  # (signal day, code) → flags (the strongest group it was in)
    for d in days[-90:]:
        for m in ("known", "new"):
            j = httpx.get(f"{API}/api/history/finder", params={"date": d.isoformat(), "method": m, "stored_only": "1"}, timeout=120).json()
            for g in j.get("groups", []):
                dt = g.get("daytrade") or {}
                f = {"sync": is_tight_sync(g), "hot": bool(dt.get("hot")), "surge": bool(dt.get("hot")) and (surge(dt) or 0) >= SURGE_X}
                for mem in g["members"]:
                    old = picks.get((d, mem["code"]), {})
                    picks[(d, mem["code"])] = {k: f[k] or old.get(k, False) for k in f}
    sig_days = sorted({d for d, _ in picks if d in nxt})
    # the next day's open and close: the picks, and every common stock (the benchmark)
    ret: dict[tuple[date, str], float] = {}
    bench: dict[date, float] = {}
    for d in sig_days:
        rows = [r for r in s.day_rows(nxt[d]) if r.get("kind") == "stock" and r.get("open") and r.get("close")]
        if rows:
            bench[d] = sum((r["open"] - r["close"]) / r["open"] * 100 for r in rows) / len(rows)
        by = {r["code"]: r for r in rows}
        for (pd, code) in [k for k in picks if k[0] == d]:
            r = by.get(code)
            if r:
                ret[(pd, code)] = (r["open"] - r["close"]) / r["open"] * 100
    print(f"訊號日 {len(sig_days)} 天（{sig_days[0]}～{sig_days[-1]}）；成本假設 {COST:.2f}%（當沖稅 0.15% + 手續費 2.8 折）")
    print(f"基準：隔天每一檔普通股開盤放空、收盤回補的平均 {sum(bench.values()) / len(bench):+.2f}%（{len(bench)} 天平均）\n")
    cells = [
        ("所有 N 成員", lambda f: True),
        ("同步強", lambda f: f["sync"]),
        ("🔥 當沖比高", lambda f: f["hot"]),
        ("🔥 突然湧入（≥ 1.5 倍）", lambda f: f["surge"]),
        ("同步強 + 🔥", lambda f: f["sync"] and f["hot"]),
        ("同步強 + 突然湧入", lambda f: f["sync"] and f["surge"]),
        ("🔥 但不是同步強", lambda f: f["hot"] and not f["sync"]),
    ]
    half = sig_days[len(sig_days) // 2]
    for name, ok in cells:
        print(f"## {name}")
        for label, inside in (("全部", lambda d: True), ("前半", lambda d: d < half), ("後半", lambda d: d >= half)):
            keys = [k for k, f in picks.items() if ok(f) and k in ret and inside(k[0])]
            xs = [ret[k] for k in keys]
            ex = [ret[k] - bench[k[0]] for k in keys if k[0] in bench]
            by_day: dict[date, list[float]] = {}
            for k in keys:
                by_day.setdefault(k[0], []).append(ret[k] - COST)
            dwin = sum(sum(v) / len(v) > 0 for v in by_day.values())
            print(f"  {label}  {stats(xs, len(by_day))}  扣基準 {sum(ex) / len(ex):+.2f}%  以日計（當天各檔平均、扣成本）賺的日子 {dwin}/{len(by_day)}" if ex else f"  {label}  —")
        print()


if __name__ == "__main__":
    main()
