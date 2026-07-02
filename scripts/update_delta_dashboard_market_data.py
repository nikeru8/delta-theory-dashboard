#!/usr/bin/env python3
"""Export Hermes market bars to the Delta dashboard and publish GitHub Pages."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path("/Users/nikeru8/排程claude")
HERMES_DB = Path("/Users/nikeru8/hermes-trader/data/market.sqlite")
GH_PAGES = Path("/tmp/delta-gh-pages")
STATE_PATH = ROOT / "scripts" / "delta_dashboard_update_state.json"
LOCK_PATH = Path("/tmp/delta_dashboard_market_update.lock")
LOG_PREFIX = "[delta-dashboard-update]"
DEFAULT_MIN_SYMBOLS = 1900
PUBLIC_BASE = "https://nikeru8.github.io/delta-theory-dashboard"


def log(message: str) -> None:
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {LOG_PREFIX} {message}", flush=True)


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    log("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc


def today_taipei() -> str:
    return datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")


def load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def already_done_for_date(state: dict, date_text: str) -> bool:
    return state.get("done_date") == date_text


def has_complete_today_bars(today_symbol_count: int, min_symbols: int = DEFAULT_MIN_SYMBOLS) -> bool:
    return today_symbol_count >= min_symbols


def is_allowed_dirty_path(path: str) -> bool:
    return path == "delta_market_data.js" or path.startswith("delta_market_bars/")


def shard_text_has_latest_date(text: str, date_text: str) -> bool:
    rows = re.findall(r'\["(\d{4}-\d{2}-\d{2})",([^\]]+)\]', text)
    return bool(rows and rows[-1][0] == date_text)


def local_shard_has_date(repo: Path, symbol: str, date_text: str) -> bool:
    path = repo / "delta_market_bars" / f"{symbol}.js"
    if not path.exists():
        return False
    return shard_text_has_latest_date(path.read_text(encoding="utf-8"), date_text)


def dirty_paths(repo: Path) -> list[str]:
    proc = run(["git", "status", "--short"], cwd=repo, check=True)
    paths = []
    for line in proc.stdout.splitlines():
        if not line:
            continue
        paths.append(line[3:].strip())
    return paths


def assert_no_unrelated_dirty_paths(repo: Path) -> None:
    unrelated = [path for path in dirty_paths(repo) if not is_allowed_dirty_path(path)]
    if unrelated:
        raise RuntimeError(f"gh-pages has unrelated dirty paths: {', '.join(unrelated)}")


def count_today_symbols(db_path: Path, date_text: str) -> int:
    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT COUNT(DISTINCT symbol) FROM daily_bars_adjusted WHERE date = ?",
            (date_text,),
        ).fetchone()
    return int(row[0] or 0)


def latest_symbol_row(db_path: Path, symbol: str = "6446") -> tuple | None:
    with sqlite3.connect(db_path) as con:
        return con.execute(
            """
            SELECT symbol, date, open, high, low, close, volume
            FROM daily_bars_adjusted
            WHERE symbol = ?
            ORDER BY date DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()


def export_market_data(db_path: Path, root: Path) -> tuple[int, list | None]:
    bars_dir = root / "delta_market_bars"
    index_path = root / "delta_market_data.js"
    bars_dir.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        instruments = con.execute(
            """
            SELECT symbol, name, market, industry
            FROM instruments
            WHERE is_common_stock = 1 AND is_active = 1
            ORDER BY symbol
            """
        ).fetchall()
        meta = []
        sample_6446 = None
        written = 0
        for inst in instruments:
            symbol = inst["symbol"]
            rows = con.execute(
                """
                WITH merged AS (
                  SELECT symbol, date, open, high, low, close, volume, 0 AS priority
                  FROM daily_bars_yahoo
                  WHERE symbol = ?
                  UNION ALL
                  SELECT symbol, date, open, high, low, close, volume, 1 AS priority
                  FROM daily_bars_adjusted
                  WHERE symbol = ?
                ), ranked AS (
                  SELECT date, open, high, low, close, volume,
                         ROW_NUMBER() OVER (PARTITION BY date ORDER BY priority DESC) AS rn
                  FROM merged
                  WHERE date IS NOT NULL
                    AND open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL
                )
                SELECT date, open, high, low, close, volume
                FROM ranked
                WHERE rn = 1
                ORDER BY date
                """,
                (symbol, symbol),
            ).fetchall()
            if not rows:
                continue
            bars = [
                [
                    row["date"],
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    int(row["volume"] or 0),
                ]
                for row in rows
            ]
            stock_meta = [
                symbol,
                inst["name"],
                inst["market"],
                inst["industry"],
                bars[0][0],
                bars[-1][0],
                len(bars),
            ]
            meta.append(stock_meta)
            if symbol == "6446":
                sample_6446 = stock_meta
            payload = json.dumps(bars, ensure_ascii=False, separators=(",", ":"))
            (bars_dir / f"{symbol}.js").write_text(
                "window.HERMES_MARKET_BARS=window.HERMES_MARKET_BARS||{};"
                f"window.HERMES_MARKET_BARS[{json.dumps(symbol, ensure_ascii=False)}]={payload};\n",
                encoding="utf-8",
            )
            written += 1

    index_payload = {
        "schemaVersion": 2,
        "source": "market.sqlite",
        "table": "daily_bars_yahoo+daily_bars_adjusted",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "columns": ["date", "open", "high", "low", "close", "volume"],
        "mergePolicy": "daily_bars_yahoo base, daily_bars_adjusted overrides same date and extends recent data",
        "meta": meta,
        "sharded": True,
        "barsPath": "./delta_market_bars/",
        "bars": {},
        "shardCount": written,
    }
    index_path.write_text(
        "window.HERMES_MARKET_DATA="
        + json.dumps(index_payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    return written, sample_6446


def sync_to_gh_pages(root: Path, gh_pages: Path) -> None:
    shutil.copy2(root / "delta_market_data.js", gh_pages / "delta_market_data.js")
    run(
        [
            "rsync",
            "-a",
            str(root / "delta_market_bars") + "/",
            str(gh_pages / "delta_market_bars") + "/",
        ]
    )


def git_has_changes(repo: Path) -> bool:
    proc = run(["git", "status", "--short"], cwd=repo, check=True)
    return bool(proc.stdout.strip())


def publish(repo: Path, date_text: str) -> str | None:
    if not git_has_changes(repo):
        log("gh-pages already has current market data; no commit needed")
        return None
    run(["git", "add", "delta_market_data.js", "delta_market_bars"], cwd=repo)
    run(["git", "commit", "-m", f"Update market bars through {date_text}"], cwd=repo)
    run(["git", "push", "origin", "gh-pages"], cwd=repo)
    proc = run(["git", "rev-parse", "--short", "HEAD"], cwd=repo)
    return proc.stdout.strip()


def public_6446_has_date(date_text: str) -> bool:
    url = f"{PUBLIC_BASE}/delta_market_bars/6446.js?check={int(datetime.now().timestamp())}"
    req = urllib.request.Request(
        url,
        headers={
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "delta-dashboard-update",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    return shard_text_has_latest_date(body, date_text)


def raw_6446_has_date(date_text: str) -> bool:
    url = "https://raw.githubusercontent.com/nikeru8/delta-theory-dashboard/gh-pages/delta_market_bars/6446.js"
    with urllib.request.urlopen(url, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    return shard_text_has_latest_date(body, date_text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=today_taipei())
    parser.add_argument("--min-symbols", type=int, default=int(os.getenv("DELTA_DASHBOARD_MIN_SYMBOLS", DEFAULT_MIN_SYMBOLS)))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with LOCK_PATH.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("another update is running; skip")
            return 0

        state = load_state()
        if already_done_for_date(state, args.date):
            log(f"{args.date} already published; skip")
            return 0

        today_count = count_today_symbols(HERMES_DB, args.date)
        latest_6446 = latest_symbol_row(HERMES_DB, "6446")
        log(f"{args.date} daily_bars_adjusted symbols={today_count}, min={args.min_symbols}, latest_6446={latest_6446}")
        if not has_complete_today_bars(today_count, args.min_symbols):
            log(f"{args.date} market bars are not complete enough yet; wait for later trigger")
            return 0

        if args.dry_run:
            log("dry run complete; no files written")
            return 0

        assert_no_unrelated_dirty_paths(GH_PAGES)
        if local_shard_has_date(GH_PAGES, "6446", args.date) and raw_6446_has_date(args.date):
            log(f"gh-pages already has {args.date}; mark state done without another commit")
            save_state(
                {
                    "done_date": args.date,
                    "symbols": today_count,
                    "commit": None,
                    "completed_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
                    "note": "already deployed before scheduler state was written",
                }
            )
            return 0

        written, sample_6446 = export_market_data(HERMES_DB, ROOT)
        log(f"exported shards={written}, 6446_meta={sample_6446}")
        sync_to_gh_pages(ROOT, GH_PAGES)
        assert_no_unrelated_dirty_paths(GH_PAGES)

        run(["node", "--test", str(ROOT / "tests" / "delta_dashboard_regression.test.mjs")])
        commit = publish(GH_PAGES, args.date)
        if commit:
            log(f"pushed gh-pages commit {commit}")

        if not raw_6446_has_date(args.date):
            raise RuntimeError("raw GitHub branch does not show the target date after publish")

        try:
            if public_6446_has_date(args.date):
                log("public GitHub Pages already serves the new 6446 shard")
            else:
                log("public GitHub Pages still serves cached JS; raw branch is updated, CDN should refresh shortly")
        except Exception as exc:
            log(f"public GitHub Pages verification warning: {type(exc).__name__}: {exc}")

        save_state(
            {
                "done_date": args.date,
                "symbols": today_count,
                "commit": commit,
                "completed_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
            }
        )
        log(f"{args.date} update marked done")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
