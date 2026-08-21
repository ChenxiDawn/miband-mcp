#!/usr/bin/env python3
"""aggregator.py 聚合层测试（标准库 unittest，零第三方依赖）。

覆盖：get_hr / get_sleep / get_daily 三个工具的聚合逻辑，
含空数据 stale 标记、跨天睡眠归属、无效心率过滤、total_min 补丁（评审 ④）。
fixture 由 fixture_builder 动态生成，跑完自清。
"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
for _p in (PROJECT_ROOT, TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aggregator
import db
import fixture_builder as fb

FIXTURES_DIR = os.path.join(TESTS_DIR, "fixtures")
DB_PATH = os.path.join(FIXTURES_DIR, "miband_test.sqlite")
EMPTY_DB_PATH = os.path.join(FIXTURES_DIR, "miband_empty.sqlite")


class TestAggregator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fb.build_db(DB_PATH)
        fb.build_empty_db(EMPTY_DB_PATH)

    @classmethod
    def tearDownClass(cls):
        fb.remove(DB_PATH)
        fb.remove(EMPTY_DB_PATH)

    # -- 基础工具 --------------------------------------------------------------
    def test_valid_bpm_filters_sentinel(self):
        self.assertTrue(aggregator.valid_bpm(72))
        self.assertFalse(aggregator.valid_bpm(255))   # 哨兵值
        self.assertFalse(aggregator.valid_bpm(0))
        self.assertFalse(aggregator.valid_bpm(None))
        self.assertFalse(aggregator.valid_bpm("x"))

    def test_valid_date_str(self):
        self.assertEqual(aggregator.valid_date_str("2026-08-18"), "2026-08-18")
        with self.assertRaises(ValueError):
            aggregator.valid_date_str("2026/08/18")

    # -- get_hr ----------------------------------------------------------------
    def test_aggregate_hr_filters_and_sorts(self):
        conn = db.open_db(DB_PATH)
        try:
            rows = db.fetch_latest_activity_samples(conn, 7)
        finally:
            conn.close()
        result = aggregator.aggregate_hr(rows)
        self.assertEqual(result["source"], "gadgetbridge")
        self.assertFalse(result["stale"])
        bpms = [s["bpm"] for s in result["samples"]]
        self.assertNotIn(255, bpms)                     # 哨兵被过滤
        ts = [s["ts"] for s in result["samples"]]
        self.assertEqual(ts, sorted(ts))                # 正序

    def test_aggregate_hr_empty_marks_stale(self):
        result = aggregator.aggregate_hr([])
        self.assertEqual(result["samples"], [])
        self.assertTrue(result["stale"])

    # -- get_sleep -------------------------------------------------------------
    def test_pick_sleep_interval_matches_wake_date(self):
        conn = db.open_db(DB_PATH)
        try:
            rows = db.fetch_sleep_time(conn)
        finally:
            conn.close()
        interval = aggregator.pick_sleep_interval(rows, fb.DAY_STR)
        self.assertIsNotNone(interval)
        self.assertEqual(interval, (fb.SLEEP_START_TS, fb.SLEEP_END_TS))

    def test_pick_sleep_interval_none_when_no_match(self):
        conn = db.open_db(DB_PATH)
        try:
            rows = db.fetch_sleep_time(conn)
        finally:
            conn.close()
        interval = aggregator.pick_sleep_interval(rows, "2026-01-01")
        self.assertIsNone(interval)

    def test_aggregate_sleep_full(self):
        conn = db.open_db(DB_PATH)
        try:
            st_rows = db.fetch_sleep_time(conn)
            interval = aggregator.pick_sleep_interval(st_rows, fb.DAY_STR)
            stage_rows = db.fetch_sleep_stage_samples_between(
                conn, interval[0], interval[1])
        finally:
            conn.close()
        result = aggregator.aggregate_sleep(fb.DAY_STR, interval, stage_rows)

        self.assertEqual(result["date"], fb.DAY_STR)
        self.assertEqual(result["fell_asleep"], "23:40")
        self.assertEqual(result["fell_asleep_date"], fb.PREV_DAY_STR)  # 跨天归属
        self.assertEqual(result["woke_up"], "07:20")
        # total_min 必须存在且正确（评审 ④ 补丁）
        expected_min = (fb.SLEEP_END_TS - fb.SLEEP_START_TS) // 60
        self.assertEqual(result["total_min"], expected_min)
        # 分期：light 2 条、deep 2 条、rem 1 条，awake 不计入
        self.assertEqual(result["stages"]["light"], 2 * aggregator.SLEEP_STAGE_MIN)
        self.assertEqual(result["stages"]["deep"], 2 * aggregator.SLEEP_STAGE_MIN)
        self.assertEqual(result["stages"]["rem"], 1 * aggregator.SLEEP_STAGE_MIN)
        self.assertFalse(result["stale"])

    def test_aggregate_sleep_empty_marks_stale(self):
        result = aggregator.aggregate_sleep(fb.DAY_STR, None, [])
        self.assertTrue(result["stale"])
        self.assertEqual(result["total_min"], 0)
        self.assertIsNone(result["fell_asleep"])

    # -- get_daily -------------------------------------------------------------
    def test_aggregate_daily(self):
        start = fb._ts_on(fb.DAY_STR, 0, 0)
        end = start + 86400
        conn = db.open_db(DB_PATH)
        try:
            daily_rows = db.fetch_daily_summaries(conn)
            sample_rows = db.fetch_activity_samples_between(conn, start, end)
        finally:
            conn.close()
        result = aggregator.aggregate_daily(fb.DAY_STR, daily_rows, sample_rows)

        self.assertEqual(result["date"], fb.DAY_STR)
        self.assertEqual(result["steps"], fb.DAILY_STEPS)   # 取每日汇总优先
        # 有效心率：72, 80, 95（255 被过滤）
        self.assertEqual(result["hr_min"], 72)
        self.assertEqual(result["hr_max"], 95)
        self.assertEqual(result["hr_avg"], int((72 + 80 + 95) / 3))
        # active_min：raw_kind==1 的有 3 条
        self.assertEqual(result["active_min"], 3 * aggregator.ACTIVITY_SAMPLE_MIN)
        self.assertFalse(result["stale"])

    def test_aggregate_daily_empty_marks_stale(self):
        result = aggregator.aggregate_daily("2026-01-01", [], [])
        self.assertEqual(result["steps"], 0)
        self.assertTrue(result["stale"])


if __name__ == "__main__":
    unittest.main()
