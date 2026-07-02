# Delta Theory Taiwan Dashboard

Static GitHub Pages deployment for the Delta Theory Taiwan stock dashboard.

Open the dashboard at the repository GitHub Pages URL. The app loads:

- `index.html`
- `delta_market_data.js`
- `delta_market_bars/*.js`
- `delta_calibrations.js`
- `delta_pipeline_manifest.txt`

The market data index is sharded by stock symbol so GitHub Pages does not need
to serve one very large JavaScript bundle.

The repository also includes source and regression artifacts for review:

- `scripts/delta_market_export.py`
- `scripts/delta_calibrate.py`
- `scripts/update_delta_dashboard_market_data.py`
- `tests/*.py`
- `tests/*.mjs`

`delta_calibrations.js` is generated output. The calibration/template search
logic lives in `scripts/delta_calibrate.py`; see
`delta_pipeline_manifest.txt` for source and output paths.
