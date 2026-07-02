import importlib.util
import pathlib
import unittest


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "delta_calibrate.py"


def load_module():
    spec = importlib.util.spec_from_file_location("delta_calibrate", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = load_module()


def synthetic_bars(start_date, days, spikes):
    """Flat 100 bars with isolated swing spikes: spikes = {day_offset: 'H'|'L'}."""
    bars = []
    t0 = M.parse_date_ms(start_date)
    for i in range(days):
        t = t0 + i * M.MS_DAY
        o = h = l = c = 100.0
        role = spikes.get(i)
        if role == "H":
            h = 110.0
        elif role == "L":
            l = 90.0
        bars.append({
            "symbol": "TEST", "date": M.date_from_ms(t), "time": t,
            "open": o, "high": h, "low": l, "close": c, "volume": 0,
        })
    return bars


def make_events(cycle_starts, events_per_cycle=4, spacing_days=25):
    """Synthetic ITD-like events: cycle k starts at day k*120, alternating roles."""
    events = []
    for k, start in enumerate(cycle_starts):
        base = k * 120 * M.MS_DAY
        for j in range(events_per_cycle):
            events.append({
                "time": base + j * spacing_days * M.MS_DAY,
                "tf": "ITD", "p": j + 1, "color": "R",
                "role": "H" if j % 2 == 0 else "L",
                "cycleStart": start, "windowDays": 3.0,
            })
    return sorted(events, key=lambda e: e["time"])


def swings_for_events(events, flip_cycles=(), offset_ms=0):
    swings = []
    for e in events:
        role = e["role"]
        if e["cycleStart"] in flip_cycles:
            role = M.opposite_role(role)
        swings.append({"time": e["time"] + offset_ms, "role": role, "price": 100.0})
    return swings


class WalkForwardInversionTests(unittest.TestCase):
    def test_flip_detected_one_cycle_later(self):
        starts = [0, 4, 8, 12]
        events = make_events(starts)
        # cycles 0/1 realize normal roles; cycles 2/3 realize inverted roles
        swings = swings_for_events(events, flip_cycles=(8, 12))
        idx = M.SwingIndex(swings)
        pol_map, hist, cur = M.walk_forward_polarity(events, idx, 3.0, 0.10)
        self.assertEqual(pol_map[0], 0)
        self.assertEqual(pol_map[4], 0)
        # cycle 8 polarity decided from cycle 4 (normal) -> still 0
        self.assertEqual(pol_map[8], 0)
        # cycle 12 polarity decided from cycle 8 (realized inverted) -> flips
        self.assertEqual(pol_map[12], 1)
        self.assertEqual(cur, 1)
        self.assertEqual(hist[0], [0, 0])
        self.assertEqual(hist[-1], [12, 1])

    def test_polarity_is_causal(self):
        starts = [0, 4, 8, 12]
        events = make_events(starts)
        swings = swings_for_events(events, flip_cycles=(8, 12))
        base_map, _, _ = M.walk_forward_polarity(events, M.SwingIndex(swings), 3.0, 0.10)
        # mutate everything at/after cycle 12's first event time
        cutoff = min(e["time"] for e in events if e["cycleStart"] == 12)
        mutated = [s for s in swings if s["time"] < cutoff]
        mutated += [{"time": cutoff + i * M.MS_DAY, "role": "H", "price": 1.0} for i in range(8)]
        new_map, _, _ = M.walk_forward_polarity(events, M.SwingIndex(mutated), 3.0, 0.10)
        for start in starts:
            self.assertEqual(base_map[start], new_map[start], f"cycle {start} polarity changed by future data")

    def test_no_flip_without_margin(self):
        starts = [0, 4, 8, 12]
        events = make_events(starts)
        swings = swings_for_events(events)  # all cycles normal
        pol_map, hist, cur = M.walk_forward_polarity(events, M.SwingIndex(swings), 3.0, 0.10)
        self.assertTrue(all(pol == 0 for pol in pol_map.values()))
        self.assertEqual(cur, 0)
        self.assertEqual(len(hist), 1)


class StatusGateTests(unittest.TestCase):
    def test_calibrated_requires_min_valid_matches(self):
        train = {"score": 0.30, "events": 60, "matched": 40, "oppositeMatched": 3}
        valid_low = {"score": 0.30, "events": 30, "matched": 9, "oppositeMatched": 2}
        valid_ok = {"score": 0.30, "events": 30, "matched": 10, "oppositeMatched": 2}
        self.assertEqual(M.status_for("MTD", 2000, train, valid_low, 10), "weak")
        self.assertEqual(M.status_for("MTD", 2000, train, valid_ok, 10), "calibrated")

    def test_insufficient_paths_unchanged(self):
        train = {"score": 0.30, "events": 1, "matched": 1, "oppositeMatched": 0}
        valid = {"score": 0.30, "events": 0, "matched": 0, "oppositeMatched": 0}
        self.assertEqual(M.status_for("ITD", 2000, train, valid, 10), "insufficient")
        self.assertEqual(M.status_for("ITD", 10, train, valid, 10), "insufficient")


class TemplateTests(unittest.TestCase):
    def test_build_template_carries_cycle_steps(self):
        tpl = M.build_template("ITD", "R", 0, "H")
        self.assertEqual([p["cyc"] for p in tpl], [0, 0, 0, 1, 1, 2, 2, 2, 3, 3])
        self.assertTrue(all(p["source"] == "calibrated" for p in tpl))

    def test_generate_events_cycle_start(self):
        tpl = M.build_template("ITD", "R", 0, "H")
        start = M.parse_date_ms("2020-01-01")
        end = M.parse_date_ms("2021-01-01")
        events = M.generate_events("ITD", start, end, tpl)
        self.assertTrue(events)
        self.assertTrue(all(e["cycleStart"] is not None for e in events))
        # P1 events sit on their own cycle-start line
        p1 = [e for e in events if e["p"] == 1]
        self.assertTrue(p1)
        for e in p1:
            group = [x for x in events if x["cycleStart"] == e["cycleStart"]]
            self.assertLessEqual(len({x["p"] for x in group}), 10)


class RefinementTests(unittest.TestCase):
    def _bars_with_pattern(self):
        """Spikes at ITD(R,0,H) event times, even points +2d, odd points -1d."""
        days = 720
        start_date = "2020-01-06"
        t0 = M.parse_date_ms(start_date)
        tpl = M.build_template("ITD", "R", 0, "H")
        events = M.generate_events("ITD", t0, t0 + days * M.MS_DAY, tpl)
        spikes = {}
        for e in events:
            shift = 2 if e["p"] % 2 == 1 else -1
            day = round((e["time"] - t0) / M.MS_DAY) + shift
            if 6 <= day < days - 6:
                spikes.setdefault(day, e["role"])
        return synthetic_bars(start_date, days, spikes)

    def test_refined_offsets_stay_bounded(self):
        bars = self._bars_with_pattern()
        opts = {**M.DEFAULT_OPTS, "refine": True}
        result = M.calibrate_timeframe("TEST", bars, "ITD", opts)
        uniform = M.build_template(
            "ITD", result["anchorColor"], result["phaseShiftDays"], result["firstRole"])
        max_dev = max(abs(d) for d in M.TF_CONFIG["ITD"]["refine_devs"])
        for got, base in zip(result["template"], uniform):
            self.assertLessEqual(abs(got["offsetDays"] - base["offsetDays"]), max_dev + 1e-9)
            self.assertEqual(got["cyc"], base["cyc"])

    def test_no_refine_keeps_uniform_offsets(self):
        bars = self._bars_with_pattern()
        opts = {**M.DEFAULT_OPTS, "refine": False}
        result = M.calibrate_timeframe("TEST", bars, "ITD", opts)
        uniform = M.build_template(
            "ITD", result["anchorColor"], result["phaseShiftDays"], result["firstRole"])
        self.assertEqual(
            [p["offsetDays"] for p in result["template"]],
            [p["offsetDays"] for p in uniform])
        self.assertFalse(result["refined"])

    def test_refine_beats_or_matches_uniform_on_train(self):
        bars = self._bars_with_pattern()
        refined = M.calibrate_timeframe("TEST", bars, "ITD", {**M.DEFAULT_OPTS, "refine": True})
        uniform = M.calibrate_timeframe("TEST", bars, "ITD", {**M.DEFAULT_OPTS, "refine": False})
        self.assertGreaterEqual(refined["trainScore"], uniform["trainScore"])


class CompactSolutionTests(unittest.TestCase):
    def test_compact_includes_inversion_and_refine_flags(self):
        bars = synthetic_bars("2020-01-06", 400, {30: "H", 45: "L", 80: "H", 120: "L", 200: "H", 260: "L", 320: "H"})
        solution = M.calibrate_symbol("TEST", bars)
        compact = M.compact_solution(solution)
        for tf in ("ITD", "MTD"):
            self.assertIn("rf", compact["t"][tf])
            self.assertIn("tpl", compact["t"][tf])
            for point in compact["t"][tf]["tpl"]:
                self.assertIn("cyc", point)


if __name__ == "__main__":
    unittest.main()
