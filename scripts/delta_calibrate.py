#!/usr/bin/env python3
import argparse
import datetime as dt
import importlib.util
import json
import math
import sqlite3
from pathlib import Path


def load_market_export_module():
    path = Path(__file__).with_name("delta_market_export.py")
    spec = importlib.util.spec_from_file_location("delta_market_export", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


load_merged_market_data = load_market_export_module().load_merged_market_data


MS_DAY = 86_400_000
WORKTREE_ROOT = Path(__file__).resolve().parents[1]
COLORS = ["R", "B", "O", "G"]
SYNODIC_MS = 29.530588853 * MS_DAY
FULL_MOON_EPOCH_UTC = int(dt.datetime(2000, 1, 21, 4, 40, tzinfo=dt.timezone.utc).timestamp() * 1000)
STD_ANCHOR_MS = int(dt.datetime(1991, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
MOON_ANCHOR_MS = FULL_MOON_EPOCH_UTC

TF_CONFIG = {
    "STD": {"spacing": 1.0, "window": 1.0, "swing_radius": 2, "min_bars": 80, "min_train_events": 10, "min_valid_events": 4, "shifts": [-1, -0.5, 0, 0.5, 1]},
    "ITD": {"spacing": 29.530588853, "window": 3.0, "swing_radius": 5, "min_bars": 120, "min_train_events": 4, "min_valid_events": 2, "shifts": [-6, -4, -2, 0, 2, 4, 6]},
    "MTD": {"spacing": 29.530588853 * 3, "window": 10.0, "swing_radius": 10, "min_bars": 240, "min_train_events": 2, "min_valid_events": 1, "shifts": [-15, -10, -5, 0, 5, 10, 15]},
}


def mod(n, m):
    return ((n % m) + m) % m


def utc_day(ms):
    d = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000)


def parse_date_ms(value):
    y, m, d = [int(part) for part in value.split("-")]
    return int(dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp() * 1000)


def date_from_ms(ms):
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")


def color_index(color):
    return COLORS.index(color)


def std_color_for_date(ms):
    days = math.floor((utc_day(ms) - utc_day(STD_ANCHOR_MS)) / MS_DAY)
    return COLORS[mod(days, 4)]


def color_for_moon_index(moon_index):
    anchor_idx = round((MOON_ANCHOR_MS - FULL_MOON_EPOCH_UTC) / SYNODIC_MS)
    return COLORS[mod(moon_index - anchor_idx, 4)]


def color_for_mtd_line(moon_index):
    anchor_idx = round((MOON_ANCHOR_MS - FULL_MOON_EPOCH_UTC) / SYNODIC_MS)
    step = round((moon_index - anchor_idx) / 3)
    return COLORS[mod(step, 4)]


def generate_full_moons(start_ms, end_ms):
    moons = []
    n = math.floor((start_ms - FULL_MOON_EPOCH_UTC) / SYNODIC_MS) - 3
    while True:
        t = FULL_MOON_EPOCH_UTC + n * SYNODIC_MS
        if t > end_ms + SYNODIC_MS * 3:
            break
        if t >= start_ms - SYNODIC_MS * 3:
            moons.append({"time": int(round(t)), "moonIndex": n})
        n += 1
    return moons


def generate_grid(tf, start_ms, end_ms):
    grids = []
    if tf == "STD":
        t = utc_day(start_ms - 5 * MS_DAY)
        while t <= end_ms + 5 * MS_DAY:
            grids.append({"time": t, "color": std_color_for_date(t), "tf": "STD"})
            t += MS_DAY
    elif tf == "ITD":
        for moon in generate_full_moons(start_ms, end_ms):
            grids.append({"time": moon["time"], "color": color_for_moon_index(moon["moonIndex"]), "tf": "ITD"})
    elif tf == "MTD":
        anchor_idx = round((MOON_ANCHOR_MS - FULL_MOON_EPOCH_UTC) / SYNODIC_MS)
        for moon in generate_full_moons(start_ms, end_ms):
            if mod(moon["moonIndex"] - anchor_idx, 3) == 0:
                grids.append({"time": moon["time"], "color": color_for_mtd_line(moon["moonIndex"]), "tf": "MTD"})
    return sorted(grids, key=lambda g: g["time"])


def opposite_role(role):
    return "L" if role == "H" else "H"


def build_template(tf, p1_color, phase_shift_days, first_role, n_points=10):
    spacing = TF_CONFIG[tf]["spacing"]
    p1_idx = color_index(p1_color)
    out = []
    for i in range(n_points):
        phase = i / n_points * 4
        color_idx = (p1_idx + math.floor(phase)) % 4
        offset = (phase - math.floor(phase)) * spacing + phase_shift_days
        role = first_role if i % 2 == 0 else opposite_role(first_role)
        out.append({
            "p": i + 1,
            "color": COLORS[color_idx],
            "offsetDays": round(offset, 3),
            "role": role,
            "source": "calibrated",
        })
    return out


def template_with_source(template, source):
    return [{**point, "source": source} for point in template]


def generate_events(tf, start_ms, end_ms, template):
    grids = generate_grid(tf, start_ms - 120 * MS_DAY, end_ms + 120 * MS_DAY)
    events = []
    seen = set()
    for grid in grids:
        for point in template:
            if point["color"] != grid["color"]:
                continue
            t = int(round(grid["time"] + float(point.get("offsetDays", 0)) * MS_DAY))
            if t < start_ms - 30 * MS_DAY or t > end_ms + 30 * MS_DAY:
                continue
            key = (tf, point["p"], round(t / 60_000))
            if key in seen:
                continue
            seen.add(key)
            events.append({
                "time": t,
                "tf": tf,
                "p": point["p"],
                "color": point["color"],
                "role": point.get("role") or ("L" if point["p"] % 2 == 0 else "H"),
                "windowDays": TF_CONFIG[tf]["window"],
            })
    return sorted(events, key=lambda e: (e["time"], e["p"]))


def detect_swings(bars, radius=5):
    swings = []
    if len(bars) < radius * 2 + 1:
        return swings
    for i in range(radius, len(bars) - radius):
        window = bars[i - radius:i + radius + 1]
        high = bars[i]["high"]
        low = bars[i]["low"]
        if high == max(x["high"] for x in window) and high > max(x["high"] for j, x in enumerate(window) if j != radius):
            swings.append({"index": i, "time": bars[i]["time"], "date": bars[i]["date"], "role": "H", "price": high})
        if low == min(x["low"] for x in window) and low < min(x["low"] for j, x in enumerate(window) if j != radius):
            swings.append({"index": i, "time": bars[i]["time"], "date": bars[i]["date"], "role": "L", "price": low})
    return sorted(swings, key=lambda s: s["time"])


def nearest_swing_score(event, swings, role, window_ms):
    best = None
    for swing in swings:
        if swing["role"] != role:
            continue
        dist = abs(swing["time"] - event["time"])
        if dist <= window_ms and (best is None or dist < best):
            best = dist
    if best is None:
        return 0.0
    return max(0.0, 1.0 - best / window_ms)


def score_events(events, swings, period_start, period_end, window_days):
    scoped = [event for event in events if period_start <= event["time"] <= period_end]
    if not scoped:
        return {"score": 0.0, "events": 0, "matched": 0, "oppositeMatched": 0}
    total = 0.0
    matched = 0
    opposite = 0
    window_ms = window_days * MS_DAY
    for event in scoped:
        same_score = nearest_swing_score(event, swings, event["role"], window_ms)
        opposite_score = nearest_swing_score(event, swings, opposite_role(event["role"]), window_ms)
        if same_score > 0:
            matched += 1
        if opposite_score > 0:
            opposite += 1
        total += same_score - 0.6 * opposite_score
    return {
        "score": round(total / len(scoped), 4),
        "events": len(scoped),
        "matched": matched,
        "oppositeMatched": opposite,
    }


def candidate_templates(tf):
    for color in COLORS:
        for shift in TF_CONFIG[tf]["shifts"]:
            for first_role in ("H", "L"):
                yield {
                    "anchorColor": color,
                    "phaseShiftDays": shift,
                    "firstRole": first_role,
                    "template": build_template(tf, color, shift, first_role),
                }


def status_for(tf, bars_count, train_stats, valid_stats):
    cfg = TF_CONFIG[tf]
    if bars_count < cfg["min_bars"] or train_stats["events"] < cfg["min_train_events"] or valid_stats["events"] < cfg["min_valid_events"]:
        return "insufficient"
    if valid_stats["score"] >= 0.22 and train_stats["score"] >= 0.18:
        return "calibrated"
    return "weak"


def calibrate_timeframe(symbol, bars, tf):
    if len(bars) < 2:
        template = template_with_source(build_template(tf, "R", 0, "H"), "insufficient-fallback")
        empty = {"score": 0.0, "events": 0, "matched": 0, "oppositeMatched": 0}
        return {
            "status": "insufficient",
            "anchorColor": "R",
            "phaseShiftDays": 0,
            "firstRole": "H",
            "trainScore": 0.0,
            "validationScore": 0.0,
            "eventsTrain": 0,
            "eventsValidation": 0,
            "matchedTrain": 0,
            "matchedValidation": 0,
            "template": template,
            "train": empty,
            "validation": empty,
        }

    split_idx = max(1, min(len(bars) - 1, int(len(bars) * 0.7)))
    start_ms = bars[0]["time"]
    end_ms = bars[-1]["time"]
    train_start = start_ms
    train_end = bars[split_idx - 1]["time"]
    valid_start = bars[split_idx]["time"]
    valid_end = end_ms
    radius = TF_CONFIG[tf]["swing_radius"]
    train_swings = detect_swings(bars[:split_idx], radius)
    valid_swings = detect_swings(bars[split_idx:], radius)

    best = None
    for candidate in candidate_templates(tf):
        events = generate_events(tf, start_ms, end_ms, candidate["template"])
        train_stats = score_events(events, train_swings, train_start, train_end, TF_CONFIG[tf]["window"])
        key = (train_stats["score"], train_stats["matched"], -train_stats["oppositeMatched"])
        if best is None or key > best["key"]:
            best = {
                "candidate": candidate,
                "events": events,
                "train": train_stats,
                "key": key,
            }

    candidate = best["candidate"]
    valid_stats = score_events(best["events"], valid_swings, valid_start, valid_end, TF_CONFIG[tf]["window"])
    status = status_for(tf, len(bars), best["train"], valid_stats)
    template = candidate["template"] if status != "insufficient" else template_with_source(candidate["template"], "insufficient-fallback")
    return {
        "status": status,
        "anchorColor": candidate["anchorColor"],
        "phaseShiftDays": candidate["phaseShiftDays"],
        "firstRole": candidate["firstRole"],
        "trainScore": best["train"]["score"],
        "validationScore": valid_stats["score"],
        "eventsTrain": best["train"]["events"],
        "eventsValidation": valid_stats["events"],
        "matchedTrain": best["train"]["matched"],
        "matchedValidation": valid_stats["matched"],
        "oppositeTrain": best["train"]["oppositeMatched"],
        "oppositeValidation": valid_stats["oppositeMatched"],
        "template": template,
        "train": best["train"],
        "validation": valid_stats,
    }


def calibrate_symbol(symbol, bars, tfs=("ITD", "MTD")):
    bars = sorted(bars, key=lambda b: b["time"])
    tfs_out = {}
    for tf in tfs:
        tfs_out[tf] = calibrate_timeframe(symbol, bars, tf)
    statuses = [tfs_out[tf]["status"] for tf in tfs_out]
    if any(status == "calibrated" for status in statuses):
        overall = "calibrated"
    elif any(status == "weak" for status in statuses):
        overall = "weak"
    else:
        overall = "insufficient"
    return {
        "symbol": symbol,
        "status": overall,
        "bars": len(bars),
        "minDate": bars[0]["date"] if bars else "",
        "maxDate": bars[-1]["date"] if bars else "",
        "tfs": tfs_out,
    }


def load_bars_by_symbol(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    merged, _meta = load_merged_market_data(con)
    grouped = {}
    for symbol, rows in merged.items():
        grouped[symbol] = [{
            "symbol": symbol,
            "date": row[0],
            "time": parse_date_ms(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": int(row[5] or 0),
        } for row in rows]
    con.close()
    return grouped


def compact_solution(solution):
    out = {
        "s": solution["status"],
        "b": solution["bars"],
        "min": solution["minDate"],
        "max": solution["maxDate"],
        "t": {},
    }
    for tf, item in solution["tfs"].items():
        out["t"][tf] = {
            "s": item["status"],
            "a": item["anchorColor"],
            "d": item["phaseShiftDays"],
            "r": item["firstRole"],
            "ts": item["trainScore"],
            "vs": item["validationScore"],
            "et": item["eventsTrain"],
            "ev": item["eventsValidation"],
            "mt": item["matchedTrain"],
            "mv": item["matchedValidation"],
            "tpl": item["template"],
        }
    return out


def export_js(calibrations, out_path, db_path):
    counts = {"calibrated": 0, "weak": 0, "insufficient": 0}
    for item in calibrations.values():
        counts[item["s"]] = counts.get(item["s"], 0) + 1
    payload = {
        "schemaVersion": 1,
        "source": str(db_path),
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "method": "swing-grid-search-train70-validate30",
        "anchors": {
            "stdAnchorMs": STD_ANCHOR_MS,
            "stdAnchorColor": "R",
            "moonAnchorMs": MOON_ANCHOR_MS,
            "moonAnchorColor": "R",
            "fullMoonEpochUtc": FULL_MOON_EPOCH_UTC,
        },
        "statusCounts": counts,
        "data": calibrations,
    }
    text = "window.HERMES_DELTA_CALIBRATIONS=" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n"
    Path(out_path).write_text(text, encoding="utf-8")
    return counts


def main():
    parser = argparse.ArgumentParser(description="Calibrate per-symbol Delta templates from Hermes market.sqlite")
    parser.add_argument("--db", default="/Users/nikeru8/hermes-trader/data/market.sqlite")
    parser.add_argument("--out", default=str(WORKTREE_ROOT / "delta_calibrations.js"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--symbols", default="")
    args = parser.parse_args()

    grouped = load_bars_by_symbol(args.db)
    symbols = sorted(grouped)
    if args.symbols:
        wanted = {s.strip() for s in args.symbols.split(",") if s.strip()}
        symbols = [s for s in symbols if s in wanted]
    if args.limit:
        symbols = symbols[:args.limit]

    calibrations = {}
    for idx, symbol in enumerate(symbols, start=1):
        solution = calibrate_symbol(symbol, grouped[symbol])
        calibrations[symbol] = compact_solution(solution)
        if idx == 1 or idx % 100 == 0 or idx == len(symbols):
            print(f"calibrated {idx}/{len(symbols)} {symbol} status={solution['status']}")

    counts = export_js(calibrations, args.out, args.db)
    print(f"wrote {args.out}")
    print(json.dumps({"symbols": len(calibrations), "statusCounts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
