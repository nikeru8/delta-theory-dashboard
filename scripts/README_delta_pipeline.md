# Delta Dashboard 資料與校準來源

這個資料夾保留 Delta Theory 台股 Dashboard 的產生器腳本，方便 code review 不用再跨到 `Downloads` 找來源。

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
- 頁面：`/Users/nikeru8/排程claude/index.html`（`delta_theory_dashboard_tw.html` 為同內容副本）
- GitHub Pages 工作樹：`/tmp/delta-gh-pages`

## 產生器腳本

- `scripts/delta_market_export.py`
  - 從 Hermes SQLite 匯出市場資料（yahoo 為底、adjusted 覆蓋）。
  - 預設輸出到工作樹上一層的 `delta_market_data.js` + `delta_market_bars/`。
- `scripts/delta_calibrate.py` — **v2（2026-07-02）**
  - `method` 是 `swing-grid-search-train70-validate30+wf-inversion+bounded-point-refine`。
  - 流程（每檔、每週期 ITD/MTD）：
    1. 粗搜尋：4 色 × 相位偏移（ITD ±6、MTD ±15）× 首點角色，train 70% 評分取最佳。
    2. 受限逐點精修：10 個點各自允許偏離等間距位置（ITD ±3 天、MTD ±9 天），
       coordinate descent 兩輪，只看 train 分數（validation 不參與選擇）。
    3. Walk-forward 反轉（inversion）：每輪開始時，只用「上一輪已實現的轉折」
       重評正常 vs 反轉極性，反轉方勝出超過 switch margin（0.10）才翻轉；
       第 0 輪極性 = 模板擬合角色。逐輪極性同時用於 train/validation 評分，
       validation 期的極性決策只依賴該輪之前的資料，無前視。
       已知近似：swing 需要 radius 根 K 才確認，實盤極性會晚 ~radius 天穩定。
    4. Validation 30% 樣本外評分。
  - **已校準門檻**：`vs >= 0.22` 且 `ts >= 0.18` 且 **樣本外命中 `mv >= 10`**（擋小樣本假陽性）。
  - 輸出 schemaVersion 2：每週期新增 `rf`（是否精修）、`inv = {cur, hist:[[cycleStartMoonIndex, polarity], ...]}`
    （RLE 只記變化點）；模板點新增 `cyc`（距 P1 色線的線數 0..3，前端用它把事件對回所屬輪）。
  - 完成後自動改寫兩份 HTML 的 `delta_calibrations.js?v=` cache key（`--no-bump-html` 可關）。
  - CLI：`--workers N`（多進程，全市場 8 workers 約 3–5 分鐘）、
    `--no-inversion` / `--no-refine`（消融）、`--switch-margin`、`--min-valid-matches`、
    `--symbols 2330,6446` / `--limit N`。
  - 回溯相容性已驗證：`--no-inversion --no-refine --min-valid-matches 0`
    對同一份輸入可逐位元重現 v1 的 a/d/r/ts/vs/et/ev/mt/mv/s 與模板偏移。

## 前端（index.html）對應

- `cloneCalibratedTemplate` 保留 `cyc`；`generateGrid` 帶出 `moonIndex`；
  `generateEvents` 以 `cycleStart = moonIndex - cyc * (MTD?3:1)` 查 `inv.hist`（RLE）決定該輪是否高低點互換。
- 反轉狀態顯示於：週期卡 badge（本輪正常/反轉｜歷史反轉次數）、股票資訊列、事件表「（反轉輪）」標記、tooltip。
- 套用自訂模板時反轉判定停用；舊 schemaVersion 1 校準檔（無 cyc/inv）自動退化為不反轉。
- 回測費用預設單邊 30 bps（雙邊 ~0.6%，接近台股手續費＋證交稅）。

## 測試

```bash
python3 -m unittest tests.test_delta_calibrate -v      # 反轉因果性/精修邊界/門檻/匯出欄位
node --test tests/delta_dashboard_regression.test.mjs  # 前端 wiring 與文案回歸
```

## 重新校準與發布

```bash
python3 scripts/delta_calibrate.py --workers 8                      # 產生 delta_calibrations.js + bump HTML cache key
python3 -m unittest tests.test_delta_calibrate && node --test tests/delta_dashboard_regression.test.mjs
cp delta_calibrations.js index.html delta_theory_dashboard_tw.html /tmp/delta-gh-pages/
cp scripts/delta_calibrate.py scripts/delta_market_export.py /tmp/delta-gh-pages/scripts/
cd /tmp/delta-gh-pages && git add -A && git commit -m "Recalibrate with wf-inversion + point refine" && git push origin gh-pages
```

注意：`delta_calibrations.js` 是輸出檔，不是校準邏輯本體。模板、候選參數、反轉與 train/validation 評分邏輯都在 `scripts/delta_calibrate.py`。
