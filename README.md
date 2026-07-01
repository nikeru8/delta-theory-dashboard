# Delta Theory Taiwan Dashboard

Static GitHub Pages deployment for the Delta Theory Taiwan stock dashboard.

Open the dashboard at the repository GitHub Pages URL. The app loads:

- `index.html`
- `delta_market_data.js`
- `delta_market_bars/*.js`
- `delta_calibrations.js`

The market data index is sharded by stock symbol so GitHub Pages does not need
to serve one very large JavaScript bundle.
