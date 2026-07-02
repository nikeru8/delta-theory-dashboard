#!/usr/bin/env python3
"""Calibrate per-symbol Delta templates from Hermes market.sqlite.

v2 additions (2026-07-02):
- Walk-forward inversion: each cycle's H/L polarity is decided ONLY from the
  previous cycle's realized swings (Wilder's point-1 inversion, mechanised).
- Bounded per-point offset refinement: after the coarse (color, shift, role)
  grid search, each of the 10 points may deviate from its uniform position by
  at most +/-refine_devs days, optimised on the TRAIN segment only.
- "calibrated" status now additionally requires matchedValidation >= 10
  (min_valid_matches), killing small-sample false positives.
- --workers N multiprocessing, --no-inversion / --no-refine ablation flags.
- Bumps the delta_calibrations.js cache-buster query in the HTML files.
"""
import argparse
import bisect
import copy
import datetime as dt
import importlib.util
import json
import math
import multiprocessing as mp
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote


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

# lines_per_cycle: color lines per full template cycle (P1..P10).
# moon_step: full moons between consecutive grid lines (ITD 1, MTD 3).
TF_CONFIG = {
    "STD": {
        "spacing": 1.0, "window": 1.0, "swing_radius": 2, "min_bars": 80,
        "min_train_events": 10, "min_valid_events": 4, "shifts": [-1, -0.5, 0, 0.5, 1],
        "moon_step": 0, "refine_devs": [],
    },
    "ITD": {
        "spacing": 29.530588853, "window": 3.0, "swing_radius": 5, "min_bars": 120,
        "min_train_events": 4, "min_valid_events": 2, "shifts": [-6, -4, -2, 0, 2, 4, 6],
        "moon_step": 1, "refine_devs": [-3, -2, -1, 1, 2, 3],
    },
    "MTD": {
        "spacing": 29.530588853 * 3, "window": 10.0, "swing_radius": 10, "min_bars": 240,
        "min_train_events": 2, "min_valid_events": 1, "shifts": [-15, -10, -5, 0, 5, 10, 15],
        "moon_step": 3, "refine_devs": [-9, -6, -3, 3, 6, 9],
    },
}

DEFAULT_SWITCH_MARGIN = 0.10
DEFAULT_MIN_VALID_MATCHES = 10
DEFAULT_OPTS = {
    "inversion": True,
    "refine": True,
    "switch_margin": DEFAULT_SWITCH_MARGIN,
    "min_valid_matches": DEFAULT_MIN_VALID_MATCHES,
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


def opposite_role(role):
    return "L" if role == "H" else "H"


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
            grids.append({"time": t, "color": std_color_for_date(t), "tf": "STD", "moonIndex": None})
            t += MS_DAY
    elif tf == "ITD":
        for moon in generate_full_moons(start_ms, end_ms):
            grids.append({"time": moon["time"], "color": color_for_moon_index(moon["moonIndex"]), "tf": "ITD", "moonIndex": moon["moonIndex"]})
    elif tf == "MTD":
        anchor_idx = round((MOON_ANCHOR_MS - FULL_MOON_EPOCH_UTC) / SYNODIC_MS)
        for moon in generate_full_moons(start_ms, end_ms):
            if mod(moon["moonIndex"] - anchor_idx, 3) == 0:
                grids.append({"time": moon["time"], "color": color_for_mtd_line(moon["moonIndex"]), "tf": "MTD", "moonIndex": moon["moonIndex"]})
    return sorted(grids, key=lambda g: g["time"])


def build_template(tf, p1_color, phase_shift_days, first_role, n_points=10):
    """Uniform coarse template. Each point carries `cyc` = how many color lines
    after the P1-color line it sits (0..3), used to map events back to their
    cycle-start moon index for inversion (frontend uses the same field)."""
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
            "cyc": math.floor(phase),
            "source": "calibrated",
        })
    return out


def template_with_source(template, source):
    return [{**point, "source": source} for point in template]


def generate_events(tf, start_ms, end_ms, template):
    """Events carry `cycleStart` = moon index of their cycle's P1-color line
    (None for STD or template points without `cyc`)."""
    moon_step = TF_CONFIG[tf]["moon_step"]
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
            cycle_start = None
            if grid["moonIndex"] is not None and point.get("cyc") is not None and moon_step:
                cycle_start = grid["moonIndex"] - int(point["cyc"]) * moon_step
            events.append({
                "time": t,
                "tf": tf,
                "p": point["p"],
                "color": point["color"],
                "role": point.get("role") or ("L" if point["p"] % 2 == 0 else "H"),
                "cycleStart": cycle_start,
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


class SwingIndex:
    """Per-role sorted swing times with bisect lookups."""

    def __init__(self, swings):
        self.by_role = {"H": [], "L": []}
        for swing in sorted(swings, key=lambda s: s["time"]):
            self.by_role[swing["role"]].append(swing["time"])

    def nearest_dist(self, role, t, before_time=None):
        arr = self.by_role.get(role) or []
        hi = len(arr) if before_time is None else bisect.bisect_left(arr, before_time)
        if hi == 0:
            return None
        i = bisect.bisect_left(arr, t, 0, hi)
        best = None
        for j in (i - 1, i):
            if 0 <= j < hi:
                d = abs(arr[j] - t)
                if best is None or d < best:
                    best = d
        return best


def kernel_score(dist, window_ms):
    if dist is None or dist > window_ms:
        return 0.0
    return max(0.0, 1.0 - dist / window_ms)


def event_value(event, role, swing_index, window_ms, before_time=None):
    """same-role kernel minus 0.6 * opposite-role kernel; also returns hit flags."""
    same = kernel_score(swing_index.nearest_dist(role, event["time"], before_time), window_ms)
    opp = kernel_score(swing_index.nearest_dist(opposite_role(role), event["time"], before_time), window_ms)
    return same - 0.6 * opp, same > 0, opp > 0


def effective_role(event, pol_map):
    role = event["role"]
    if pol_map:
        pol = pol_map.get(event.get("cycleStart"), 0) if event.get("cycleStart") is not None else 0
        if pol:
            role = opposite_role(role)
    return role


def walk_forward_polarity(events, swing_index, window_days, switch_margin):
    """Strictly walk-forward per-cycle polarity.

    Cycle k's polarity is decided at cycle k's first event time, using only
    swings strictly BEFORE that time, by re-scoring cycle k-1's events under
    kept vs flipped polarity. Flip only if the flipped reading beats the kept
    reading by `switch_margin` (mean per-event score). Cycle 0 keeps the
    template's fitted roles (pol=0).

    Known approximation: a swing needs `radius` future bars to be confirmed,
    so live polarity stabilises ~radius bars into a cycle; here past swings
    are treated as known once their bar time has passed.

    Returns (pol_map {cycleStart: 0|1}, rle_hist [[cycleStart, pol]...], cur_pol).
    """
    starts = sorted({e["cycleStart"] for e in events if e.get("cycleStart") is not None})
    if not starts:
        return {}, [], 0
    by_cycle = {s: [] for s in starts}
    for e in events:
        if e.get("cycleStart") is not None:
            by_cycle[e["cycleStart"]].append(e)
    window_ms = window_days * MS_DAY
    pol = 0
    pol_map = {}
    hist = []
    for idx, start in enumerate(starts):
        if idx > 0:
            prev_events = by_cycle[starts[idx - 1]]
            decision_time = min(e["time"] for e in by_cycle[start])
            if prev_events:
                keep_total = 0.0
                flip_total = 0.0
                for e in prev_events:
                    keep_role = opposite_role(e["role"]) if pol else e["role"]
                    kv, _, _ = event_value(e, keep_role, swing_index, window_ms, before_time=decision_time)
                    fv, _, _ = event_value(e, opposite_role(keep_role), swing_index, window_ms, before_time=decision_time)
                    keep_total += kv
                    flip_total += fv
                n = len(prev_events)
                if flip_total / n > keep_total / n + switch_margin:
                    pol = 1 - pol
        pol_map[start] = pol
        if not hist or hist[-1][1] != pol:
            hist.append([start, pol])
    return pol_map, hist, pol


def score_events(events, swing_index, period_start, period_end, window_days, pol_map=None):
    scoped = [event for event in events if period_start <= event["time"] <= period_end]
    if not scoped:
        return {"score": 0.0, "events": 0, "matched": 0, "oppositeMatched": 0}
    total = 0.0
    matched = 0
    opposite = 0
    window_ms = window_days * MS_DAY
    for event in scoped:
        role = effective_role(event, pol_map)
        value, same_hit, opp_hit = event_value(event, role, swing_index, window_ms)
        if same_hit:
            matched += 1
        if opp_hit:
            opposite += 1
        total += value
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


def status_for(tf, bars_count, train_stats, valid_stats, min_valid_matches=DEFAULT_MIN_VALID_MATCHES):
    cfg = TF_CONFIG[tf]
    if bars_count < cfg["min_bars"] or train_stats["events"] < cfg["min_train_events"] or valid_stats["events"] < cfg["min_valid_events"]:
        return "insufficient"
    if (
        valid_stats["score"] >= 0.22
        and train_stats["score"] >= 0.18
        and valid_stats["matched"] >= min_valid_matches
    ):
        return "calibrated"
    return "weak"


def evaluate_template(tf, template, start_ms, end_ms, train_index, all_index,
                      train_start, train_end, window_days, opts):
    """Generate events, run walk-forward inversion, score the train segment."""
    events = generate_events(tf, start_ms, end_ms, template)
    if opts["inversion"]:
        pol_map, hist, cur = walk_forward_polarity(events, all_index, window_days, opts["switch_margin"])
    else:
        pol_map, hist, cur = {}, [], 0
    train_stats = score_events(events, train_index, train_start, train_end, window_days, pol_map)
    return events, pol_map, hist, cur, train_stats


def refine_template(tf, base, start_ms, end_ms, train_index, all_index,
                    train_start, train_end, window_days, opts):
    """Bounded coordinate descent on per-point offsets (train segment only).

    Each point may deviate from its coarse uniform offset by at most
    max(|refine_devs|) days; a move is kept only if the train score improves
    by > 1e-4. Two passes. Returns (template, refined_flag, eval_count)."""
    devs = TF_CONFIG[tf]["refine_devs"]
    if not devs or not opts["refine"]:
        return base, False, 0
    template = copy.deepcopy(base)
    base_offsets = [point["offsetDays"] for point in base]
    _, _, _, _, best_stats = evaluate_template(
        tf, template, start_ms, end_ms, train_index, all_index,
        train_start, train_end, window_days, opts)
    best_score = best_stats["score"]
    refined = False
    evals = 0
    for _pass in range(2):
        for i in range(len(template)):
            current = template[i]["offsetDays"]
            for dev in devs:
                cand_offset = round(base_offsets[i] + dev, 3)
                if cand_offset == current:
                    continue
                template[i]["offsetDays"] = cand_offset
                _, _, _, _, stats = evaluate_template(
                    tf, template, start_ms, end_ms, train_index, all_index,
                    train_start, train_end, window_days, opts)
                evals += 1
                if stats["score"] > best_score + 1e-4:
                    best_score = stats["score"]
                    current = cand_offset
                    refined = True
                else:
                    template[i]["offsetDays"] = current
    return template, refined, evals


def empty_stats():
    return {"score": 0.0, "events": 0, "matched": 0, "oppositeMatched": 0}


def calibrate_timeframe(symbol, bars, tf, opts=DEFAULT_OPTS):
    if len(bars) < 2:
        template = template_with_source(build_template(tf, "R", 0, "H"), "insufficient-fallback")
        empty = empty_stats()
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
            "inversionHist": [],
            "inversionCur": 0,
            "refined": False,
        }

    split_idx = max(1, min(len(bars) - 1, int(len(bars) * 0.7)))
    start_ms = bars[0]["time"]
    end_ms = bars[-1]["time"]
    train_start = start_ms
    train_end = bars[split_idx - 1]["time"]
    valid_start = bars[split_idx]["time"]
    valid_end = end_ms
    window_days = TF_CONFIG[tf]["window"]
    radius = TF_CONFIG[tf]["swing_radius"]
    train_swings = detect_swings(bars[:split_idx], radius)
    valid_swings = detect_swings(bars[split_idx:], radius)
    train_index = SwingIndex(train_swings)
    valid_index = SwingIndex(valid_swings)
    all_index = SwingIndex(train_swings + valid_swings)

    best = None
    for candidate in candidate_templates(tf):
        events, pol_map, hist, cur, train_stats = evaluate_template(
            tf, candidate["template"], start_ms, end_ms, train_index, all_index,
            train_start, train_end, window_days, opts)
        key = (train_stats["score"], train_stats["matched"], -train_stats["oppositeMatched"])
        if best is None or key > best["key"]:
            best = {
                "candidate": candidate,
                "events": events,
                "pol_map": pol_map,
                "hist": hist,
                "cur": cur,
                "train": train_stats,
                "key": key,
            }

    candidate = best["candidate"]
    template = candidate["template"]
    if best["train"]["events"] >= TF_CONFIG[tf]["min_train_events"]:
        template, refined, _ = refine_template(
            tf, template, start_ms, end_ms, train_index, all_index,
            train_start, train_end, window_days, opts)
        if refined:
            events, pol_map, hist, cur, train_stats = evaluate_template(
                tf, template, start_ms, end_ms, train_index, all_index,
                train_start, train_end, window_days, opts)
            best.update({"events": events, "pol_map": pol_map, "hist": hist, "cur": cur, "train": train_stats})
    else:
        refined = False

    valid_stats = score_events(best["events"], valid_index, valid_start, valid_end, window_days, best["pol_map"])
    status = status_for(tf, len(bars), best["train"], valid_stats, opts["min_valid_matches"])
    out_template = template if status != "insufficient" else template_with_source(template, "insufficient-fallback")
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
        "template": out_template,
        "train": best["train"],
        "validation": valid_stats,
        "inversionHist": best["hist"],
        "inversionCur": best["cur"],
        "refined": refined,
    }


def calibrate_symbol(symbol, bars, tfs=("ITD", "MTD"), opts=DEFAULT_OPTS):
    bars = sorted(bars, key=lambda b: b["time"])
    tfs_out = {}
    for tf in tfs:
        tfs_out[tf] = calibrate_timeframe(symbol, bars, tf, opts)
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
        entry = {
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
            "rf": 1 if item.get("refined") else 0,
            "tpl": item["template"],
        }
        hist = item.get("inversionHist") or []
        if hist:
            entry["inv"] = {"cur": item.get("inversionCur", 0), "hist": hist}
        out["t"][tf] = entry
    return out


def export_js(calibrations, out_path, db_path, opts=DEFAULT_OPTS):
    counts = {"calibrated": 0, "weak": 0, "insufficient": 0}
    for item in calibrations.values():
        counts[item["s"]] = counts.get(item["s"], 0) + 1
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    payload = {
        "schemaVersion": 2,
        "source": str(db_path),
        "generatedAt": generated_at,
        "method": "swing-grid-search-train70-validate30+wf-inversion+bounded-point-refine",
        "params": {
            "inversion": bool(opts["inversion"]),
            "refine": bool(opts["refine"]),
            "switchMargin": opts["switch_margin"],
            "minValidMatches": opts["min_valid_matches"],
            "refineDevs": {tf: TF_CONFIG[tf]["refine_devs"] for tf in ("ITD", "MTD")},
        },
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
    return counts, generated_at


def bump_html_cache_key(generated_at, root=WORKTREE_ROOT):
    """Point the <script src="./delta_calibrations.js?v=..."> at the new build."""
    version = quote(generated_at, safe="")
    changed = []
    for name in ("index.html", "delta_theory_dashboard_tw.html"):
        path = root / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        new_text = re.sub(r'(\./delta_calibrations\.js\?v=)[^"]+', lambda m: m.group(1) + version, text)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            changed.append(name)
    return changed


def _calibrate_job(job):
    symbol, bars, opts = job
    solution = calibrate_symbol(symbol, bars, opts=opts)
    return symbol, compact_solution(solution), solution["status"]


def main():
    parser = argparse.ArgumentParser(description="Calibrate per-symbol Delta templates from Hermes market.sqlite")
    parser.add_argument("--db", default="/Users/nikeru8/hermes-trader/data/market.sqlite")
    parser.add_argument("--out", default=str(WORKTREE_ROOT / "delta_calibrations.js"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--workers", type=int, default=1, help="multiprocessing workers (e.g. 8)")
    parser.add_argument("--no-inversion", action="store_true", help="disable walk-forward inversion (ablation)")
    parser.add_argument("--no-refine", action="store_true", help="disable bounded per-point refinement (ablation)")
    parser.add_argument("--switch-margin", type=float, default=DEFAULT_SWITCH_MARGIN)
    parser.add_argument("--min-valid-matches", type=int, default=DEFAULT_MIN_VALID_MATCHES)
    parser.add_argument("--no-bump-html", action="store_true", help="do not rewrite the HTML cache-buster query")
    args = parser.parse_args()

    opts = {
        "inversion": not args.no_inversion,
        "refine": not args.no_refine,
        "switch_margin": args.switch_margin,
        "min_valid_matches": args.min_valid_matches,
    }

    grouped = load_bars_by_symbol(args.db)
    symbols = sorted(grouped)
    if args.symbols:
        wanted = {s.strip() for s in args.symbols.split(",") if s.strip()}
        symbols = [s for s in symbols if s in wanted]
    if args.limit:
        symbols = symbols[:args.limit]

    calibrations = {}
    jobs = [(symbol, grouped[symbol], opts) for symbol in symbols]
    done = 0

    def note(symbol, status):
        nonlocal done
        done += 1
        if done == 1 or done % 100 == 0 or done == len(jobs):
            print(f"calibrated {done}/{len(jobs)} {symbol} status={status}", flush=True)

    if args.workers > 1 and len(jobs) > 1:
        with mp.Pool(args.workers) as pool:
            for symbol, compact, status in pool.imap_unordered(_calibrate_job, jobs, chunksize=4):
                calibrations[symbol] = compact
                note(symbol, status)
    else:
        for job in jobs:
            symbol, compact, status = _calibrate_job(job)
            calibrations[symbol] = compact
            note(symbol, status)

    calibrations = {symbol: calibrations[symbol] for symbol in sorted(calibrations)}
    counts, generated_at = export_js(calibrations, args.out, args.db, opts)
    print(f"wrote {args.out}")
    if not args.no_bump_html:
        changed = bump_html_cache_key(generated_at)
        if changed:
            print(f"bumped calibrations cache key in: {', '.join(changed)}")
    print(json.dumps({"symbols": len(calibrations), "statusCounts": counts, "params": opts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
