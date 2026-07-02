import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const files = [
  'delta_theory_dashboard_tw.html',
  'index.html',
];

for (const file of files) {
  const html = readFileSync(new URL(`../${file}`, import.meta.url), 'utf8');

  test(`${file} exposes extended lookback choices`, () => {
    assert.match(html, /<option value="1825">5 年<\/option>/);
    assert.match(html, /<option value="3650">10 年<\/option>/);
    assert.match(html, /<option value="all"(?:\s+selected)?>全部<\/option>/);
  });

  test(`${file} has no two-year hard cap in data loading or CSV import`, () => {
    assert.doesNotMatch(html, /Math\.min\(\s*730\b/);
    assert.doesNotMatch(html, /730\s*\*\s*MS_DAY/);
    assert.doesNotMatch(html, /slice\([^)]*730/);
  });

  test(`${file} displays the loaded data period`, () => {
    assert.match(html, /id="dataPeriod"/);
    assert.match(html, /資料期間/);
  });

  test(`${file} does not cap historical Delta markers to the latest 1000`, () => {
    assert.doesNotMatch(html, /\.slice\(\s*-1000\s*\)/);
  });

  test(`${file} preserves the visible chart range when Delta overlay controls change`, () => {
    assert.match(html, /function renderAll\(\{\s*preserveRange\s*=\s*false\s*\}\s*=\s*\{\}\)/);
    assert.match(html, /renderChart\(\{\s*preserveRange\s*\}\)/);
    assert.match(html, /const previousRange = preserveRange[\s\S]*getVisibleRange\(\)/);
    assert.match(html, /previousRange[\s\S]*setVisibleRange\(previousRange\)[\s\S]*fitContent\(\)/);
    assert.match(html, /\['showITD','showMTD','showGrid','showWindow','moonAnchor','moonAnchorColor','p2Role','flipRole'\][\s\S]*renderAll\(\{\s*preserveRange:\s*true\s*\}\)/);
  });

  test(`${file} supports single-position backtests that skip overlapping entries`, () => {
    assert.match(html, /<select id="positionMode">/);
    assert.match(html, /<option value="overlap"(?:\s+selected)?>允許重疊<\/option>/);
    assert.match(html, /<option value="single"(?:\s+selected)?>單一持倉<\/option>/);
    assert.match(html, /const positionMode = el\('positionMode'\)\.value/);
    assert.match(html, /let lastExitIdx = -1/);
    assert.match(html, /if \(positionMode === 'single' && entryIdx <= lastExitIdx\) continue;/);
    assert.match(html, /if \(positionMode === 'single'\) lastExitIdx = exitIdx;/);
  });

  test(`${file} defaults backtest controls to the feasible 6446 ITD long model`, () => {
    assert.match(html, /<input id="confirmBars" type="number" min="1" max="20" value="2" \/>/);
    assert.match(html, /<input id="feeBps" type="number" min="0" max="100" value="30" \/>/);
    assert.match(html, /<option value="both">多空都做<\/option>/);
    assert.match(html, /<option value="long" selected>只做多<\/option>/);
    assert.match(html, /<option value="reverse">下一個反向 Delta 點<\/option>/);
    assert.match(html, /<option value="trailing" selected>移動停利<\/option>/);
    assert.match(html, /<input id="trailPct" type="number" min="0.5" max="50" step="0.5" value="34.5" \/>/);
    assert.match(html, /<option value="overlap">允許重疊<\/option>/);
    assert.match(html, /<option value="single" selected>單一持倉<\/option>/);
  });

  test(`${file} separates theoretical Delta events from confirmation anchor candles`, () => {
    assert.match(html, /<th>理論時間<\/th>/);
    assert.match(html, /<th>轉折窗<\/th>/);
    assert.match(html, /<th>確認錨點<\/th>/);
    assert.match(html, /function anchorInfoForEvent\(ev\)/);
    assert.match(html, /const idx = firstCandleIdxAtOrAfter\(ev\.time\)/);
    assert.match(html, /尚未形成/);
    assert.match(html, /收盤 &gt;/);
    assert.match(html, /收盤 &lt;/);
  });

  test(`${file} does not snap future Delta events onto the latest loaded candle`, () => {
    assert.match(html, /function isWithinLoadedCandleRange\(timeMs\)/);
    assert.match(html, /if \(!isWithinLoadedCandleRange\(timeMs\)\) return null;/);
    assert.match(html, /\.filter\(ev => ev\.time >= start - 10 \* MS_DAY && ev\.time <= end\)/);
    assert.doesNotMatch(html, /\.filter\(ev => ev\.time >= start - 10 \* MS_DAY && ev\.time <= end \+ 10 \* MS_DAY\)/);
  });

  test(`${file} clips Delta window bands to loaded candle data`, () => {
    assert.match(html, /const clippedStart = Math\.max\(ev\.windowStart, loadedStart\)/);
    assert.match(html, /const clippedEnd = Math\.min\(ev\.windowEnd, loadedEnd\)/);
    assert.match(html, /if \(clippedEnd < clippedStart\) return;/);
  });

  test(`${file} applies walk-forward inversion from calibrations`, () => {
    assert.match(html, /function activeInversion\(tf\)/);
    assert.match(html, /function invPolarityFor\(inv, cycleStart\)/);
    assert.match(html, /cyc: Number\.isFinite\(Number\(pt\.cyc\)\) \? Number\(pt\.cyc\) : null/);
    assert.match(html, /moonIndex: m\.moonIndex/);
    assert.match(html, /const cycleStart = g\.moonIndex - Number\(pt\.cyc\) \* moonStep/);
    assert.match(html, /if \(invPolarityFor\(inv, cycleStart\) === 1\)/);
    assert.match(html, /反轉輪/);
    // custom templates must disable inversion
    assert.match(html, /if \(state\.customTemplates\) return null;/);
  });

  test(`${file} documents cycle scope, inversion and calibration gate`, () => {
    assert.match(html, /反轉（Inversion）/);
    assert.match(html, /walk-forward/);
    assert.match(html, /LTD（4 年）與 SLTD（19 年）/);
    assert.match(html, /樣本外命中 ≥10/);
    assert.match(html, /ITD 每點最多 ±3 天、MTD ±9 天/);
  });

  test(`${file} uses realistic Taiwan round-trip cost defaults`, () => {
    assert.match(html, /單邊成本預設 30 bps/);
    assert.doesNotMatch(html, /<input id="feeBps"[^>]*value="3" \/>/);
  });
}
