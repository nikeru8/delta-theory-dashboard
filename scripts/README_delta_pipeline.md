# Delta Dashboard 資料與校準來源

這個資料夾現在保留 Delta Theory 台股 Dashboard 的產生器腳本，方便 code review 不用再跨到 `Downloads` 找來源。

## 市場資料來源

- SQLite DB: `/Users/nikeru8/hermes-trader/data/market.sqlite`
- 主要讀取表：
  - `daily_bars_adjusted`
  - `daily_bars_yahoo`
  - `instruments`

## 網站靜態資料

- 索引檔：`/Users/nikeru8/排程claude/delta_market_data.js`
- 分片 K 線：`/Users/nikeru8/排程claude/delta_market_bars/*.js`
- 校準輸出：`/Users/nikeru8/排程claude/delta_calibrations.js`
- 頁面：`/Users/nikeru8/排程claude/index.html`
- GitHub Pages 工作樹：`/tmp/delta-gh-pages`

## 產生器腳本

- `scripts/delta_market_export.py`
  - 從 Hermes SQLite 匯出市場資料。
  - 預設輸出到腳本所在工作樹上一層的 `delta_market_data.js`。
  - 這是原本 `/Users/nikeru8/Downloads/delta_market_export.py` 的工作樹副本。
- `scripts/delta_calibrate.py`
  - 從 Hermes SQLite 讀取 K 線並產生 `delta_calibrations.js`。
  - `method` 是 `swing-grid-search-train70-validate30`。
  - 預設輸出到腳本所在工作樹上一層的 `delta_calibrations.js`。
  - 這是原本 `/Users/nikeru8/Downloads/delta_calibrate.py` 的工作樹副本。

注意：`delta_calibrations.js` 是輸出檔，不是校準邏輯本體。模板、候選參數與 train/validation 評分邏輯在 `scripts/delta_calibrate.py`。
