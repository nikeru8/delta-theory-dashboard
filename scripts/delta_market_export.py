#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote


DEFAULT_DB = Path("/Users/nikeru8/hermes-trader/data/market.sqlite")
WORKTREE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = WORKTREE_ROOT / "delta_market_data.js"
BAR_COLUMNS = ("date", "open", "high", "low", "close", "volume")


def row_to_bar(row):
    return [
        row["date"],
        round(float(row["open"]), 4),
        round(float(row["high"]), 4),
        round(float(row["low"]), 4),
        round(float(row["close"]), 4),
        int(row["volume"] or 0),
    ]


def load_instruments(con):
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT symbol, name, market, exchange, industry
        FROM instruments
        WHERE is_active = 1 AND is_common_stock = 1
        ORDER BY symbol
        """
    ).fetchall()
    return {str(row["symbol"]): row for row in rows}


def load_bars(con, table, symbols):
    grouped = {symbol: {} for symbol in symbols}
    placeholders = ",".join("?" for _ in symbols)
    rows = con.execute(
        f"""
        SELECT symbol, date, open, high, low, close, volume
        FROM {table}
        WHERE symbol IN ({placeholders})
          AND open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL
        ORDER BY symbol, date
        """,
        list(symbols),
    )
    for row in rows:
        symbol = str(row["symbol"])
        grouped.setdefault(symbol, {})[row["date"]] = row_to_bar(row)
    return grouped


def merge_symbol_bars(yahoo_rows, adjusted_rows):
    merged = {}
    merged.update(yahoo_rows or {})
    merged.update(adjusted_rows or {})
    return [merged[date] for date in sorted(merged)]


def load_merged_market_data(con):
    instruments = load_instruments(con)
    symbols = sorted(instruments)
    yahoo = load_bars(con, "daily_bars_yahoo", symbols)
    adjusted = load_bars(con, "daily_bars_adjusted", symbols)
    merged = {}
    meta = {}

    for symbol in symbols:
        rows = merge_symbol_bars(yahoo.get(symbol, {}), adjusted.get(symbol, {}))
        if not rows:
            continue
        inst = instruments[symbol]
        merged[symbol] = rows
        meta[symbol] = {
            "symbol": symbol,
            "name": inst["name"] or symbol,
            "market": inst["market"] or inst["exchange"] or "TW",
            "industry": inst["industry"] or "",
            "minDate": rows[0][0],
            "maxDate": rows[-1][0],
            "rows": len(rows),
        }

    return merged, meta


def write_shards(bars, out_path, bars_dir_name="delta_market_bars"):
    out_path = Path(out_path)
    bars_dir = out_path.parent / bars_dir_name
    bars_dir.mkdir(parents=True, exist_ok=True)

    for stale in bars_dir.glob("*.js"):
        stale.unlink()

    for symbol, rows in bars.items():
        shard_name = quote(str(symbol), safe="") + ".js"
        shard_path = bars_dir / shard_name
        text = (
            "window.HERMES_MARKET_BARS=window.HERMES_MARKET_BARS||{};"
            f"window.HERMES_MARKET_BARS[{json.dumps(str(symbol), ensure_ascii=False)}]="
            + json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
            + ";\n"
        )
        shard_path.write_text(text, encoding="utf-8")

    return f"./{bars_dir_name}/"


def export_market_data(db_path=DEFAULT_DB, out_path=DEFAULT_OUT, shard=True):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        bars, meta_by_symbol = load_merged_market_data(con)
    finally:
        con.close()
    meta = [
        [
            item["symbol"],
            item["name"],
            item["market"],
            item["industry"],
            item["minDate"],
            item["maxDate"],
            item["rows"],
        ]
        for item in meta_by_symbol.values()
    ]
    payload = {
        "schemaVersion": 2,
        "source": Path(db_path).name,
        "table": "daily_bars_yahoo+daily_bars_adjusted",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "columns": list(BAR_COLUMNS),
        "mergePolicy": "daily_bars_yahoo base, daily_bars_adjusted overrides same date and extends recent data",
        "meta": meta,
        "sharded": bool(shard),
        "barsPath": "./delta_market_bars/" if shard else "",
        "bars": {} if shard else bars,
    }
    if shard:
        payload["barsPath"] = write_shards(bars, out_path)
        payload["shardCount"] = len(bars)
    text = "window.HERMES_MARKET_DATA=" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n"
    Path(out_path).write_text(text, encoding="utf-8")
    return payload


def main():
    parser = argparse.ArgumentParser(description="Export merged Hermes market data for the Delta dashboard")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--monolithic", action="store_true", help="Write all K bars into the index file instead of per-symbol shards")
    args = parser.parse_args()
    payload = export_market_data(Path(args.db), Path(args.out), shard=not args.monolithic)
    print(json.dumps({
        "symbols": len(payload["meta"]),
        "rows": sum(item[6] for item in payload["meta"]),
        "table": payload["table"],
        "sharded": payload["sharded"],
        "shards": payload.get("shardCount", 0),
        "out": args.out,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
