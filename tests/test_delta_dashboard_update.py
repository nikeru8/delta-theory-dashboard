import importlib.util
import pathlib
import unittest


SCRIPT_PATH = pathlib.Path("/Users/nikeru8/排程claude/scripts/update_delta_dashboard_market_data.py")


def load_module():
    spec = importlib.util.spec_from_file_location("delta_dashboard_update", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DeltaDashboardUpdateTests(unittest.TestCase):
    def test_complete_market_requires_minimum_today_symbols(self):
        module = load_module()

        self.assertTrue(module.has_complete_today_bars(1922, 1900))
        self.assertTrue(module.has_complete_today_bars(1900, 1900))
        self.assertFalse(module.has_complete_today_bars(1899, 1900))

    def test_done_state_skips_later_same_day_runs(self):
        module = load_module()

        self.assertTrue(module.already_done_for_date({"done_date": "2026-07-01"}, "2026-07-01"))
        self.assertFalse(module.already_done_for_date({"done_date": "2026-06-30"}, "2026-07-01"))
        self.assertFalse(module.already_done_for_date({}, "2026-07-01"))

    def test_only_market_data_paths_are_allowed_to_be_dirty(self):
        module = load_module()

        self.assertTrue(module.is_allowed_dirty_path("delta_market_data.js"))
        self.assertTrue(module.is_allowed_dirty_path("delta_market_bars/6446.js"))
        self.assertFalse(module.is_allowed_dirty_path("index.html"))
        self.assertFalse(module.is_allowed_dirty_path("delta_calibrations.js"))

    def test_shard_text_detects_latest_date(self):
        module = load_module()
        text = 'window.HERMES_MARKET_BARS["6446"]=[["2026-06-30",1,2,3,4,5],["2026-07-01",1,2,3,4,5]];'

        self.assertTrue(module.shard_text_has_latest_date(text, "2026-07-01"))
        self.assertFalse(module.shard_text_has_latest_date(text, "2026-07-02"))


if __name__ == "__main__":
    unittest.main()
