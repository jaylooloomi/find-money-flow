# 台股 ETF「下跌中出現溢價」事件研究

研究問題：一檔台股 ETF 在下跌過程中出現明顯溢價時，之後 5～240 個交易日的含息報酬，是否比同樣下跌但沒有溢價、或出現折價的對照組差？

結果在 `results/`，App 的歷史頁「ETF 溢價研究」分頁會顯示。

## 重跑

```bash
.venv/Scripts/python research/etf_premium/fetch.py      # 抓原始資料到 data/raw/etf/（不進版控；已有的檔案會跳過）
.venv/Scripts/python research/etf_premium/validate.py   # 交叉驗證：市價對證交所、配息對淨值跌幅
.venv/Scripts/python research/etf_premium/coverage.py   # 各檔涵蓋率 → results/coverage.csv
.venv/Scripts/python research/etf_premium/fetch_mops.py           # 官方淨值（公開資訊觀測站，約 3 小時）
.venv/Scripts/python research/etf_premium/fetch_mops.py --active  # 主動式的官方淨值（2025-04 起）
.venv/Scripts/python research/etf_premium/validate.py --mops      # 官方淨值 vs MoneyDJ、官方收盤 vs FinMind
.venv/Scripts/python research/etf_premium/study.py                # 主分析 → results/
.venv/Scripts/python research/etf_premium/fetch.py --ext          # 附錄資料（2003 起，data/raw/etf_ext/）
.venv/Scripts/python research/etf_premium/study.py --ext --nav moneydj   # 附錄 → results/ext/
```

結論是手寫的（`results/conclusion.md`），`study.py` 會把它和各個表格組成 `summary.md`。數字變了，就要重新讀表格、重寫結論。

如果要重抓某一類資料，先刪掉 `data/raw/etf/` 底下對應的資料夾。

## 進度

- [x] 第一步：資料可行性（`results/feasibility.md`）
- [x] 事件、報酬、檢定、穩健性、圖（`results/summary.md`、`robustness.md`、`main_table.csv`、`events.csv`、`median_returns.svg`）
- [x] 附錄：2003–2019 年（`results/ext/`）

結論：資料不支持假說（詳見 `results/summary.md`）。
