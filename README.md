# Delta Theory Taiwan Dashboard

Static GitHub Pages deployment for the Delta Theory Taiwan stock dashboard.

Open the dashboard at the repository GitHub Pages URL. The app loads:

- `index.html`
- `delta_market_data.js`
- `delta_market_bars/*.js`
- `delta_calibrations.js`
- `scripts/delta_market_export.py`
- `scripts/delta_calibrate.py`

The market data index is sharded by stock symbol so GitHub Pages does not need
to serve one very large JavaScript bundle.

`delta_calibrations.js` is generated output. The calibration/template search
logic lives in `scripts/delta_calibrate.py`; see
`scripts/README_delta_pipeline.md` for source and output paths.
