#!/usr/bin/env python3
"""db.py 数据层测试（标准库 unittest，零第三方依赖）。

覆盖：打开/缺失数据库、最近采样、时间窗查询、表缺失异常、时间戳单位开关。
fixture 由 fixture_builder 动态生成，跑完自清（4 问评审 ③）。
"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
for _p in (PROJECT_ROOT, TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db
import fixture_builder as fb

FIXTURES_DIR = os.path.join(TESTS_DIR, "fixtures")
DB_PATH = os.path.join(FIXTURES_DIR, "miband_test.sqlite")
EMPTY_DB_PATH = os.path.join(FIXTURES_DIR, "miband_empty.sqlite")


class TestDb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fb.build_db(DB_PATH)
        fb.build_empty_db(EMPTY_DB_PATH)

    @classmethod
    def tearDownClass(cls):
        fb.remove(DB_PATH)
        fb.remove(EMPTY_DB_PATH)

    # -- 连接与异常 ----------------------------------------------------------
    def test_open_db_missing_raises(self):
        with self.assertRaises(db.DatabaseNotFoundError) as ctx:
            db.open_db(os.path.join(FIXTURES_DIR, "no_such.sqlite"))
        self.assertIn("database not found", str(ctx.exception))

    def test_open_db_ok(self):
        conn = db.open_db(DB_PATH)
        self.assertTrue(db.table_exists(conn, db.TABLE_ACTIVITY))
        conn.close()

    def test_missing_table_raises(self):
        conn = db.open_db(EMPTY_DB_PATH)
        try:
            with self.assertRaises(db.TableMissingError):
                db.fetch_latest_activity_samples(conn, 5)
        finally:
            conn.close()

    # -- 查询 ----------------------------------------------------------------
    def test_fetch_latest_activity_samples_order_and_count(self):
        conn = db.open_db(DB_PATH)
        try:
            rows = db.fetch_latest_activity_samples(conn, 3)
        finally:
            conn.close()
        self.assertEqual(len(rows), 3)
        ts_list = [db.normalize_ts(r["TIMESTAMP"]) for r in rows]
        self.assertEqual(ts_list, sorted(ts_list, reverse=True))
        # 最近的 3 条应来自 08-19（fixture 中全局最新样本）
        self.assertEqual(rows[0]["HEART_RATE"], 72)

    def test_fetch_activity_samples_between_window(self):
        start = fb._ts_on(fb.DAY_STR, 0, 0)
        end = start + 86400
        conn = db.open_db(DB_PATH)
        try:
            rows = db.fetch_activity_samples_between(conn, start, end)
        finally:
            conn.close()
        # 只应包含查询日内的 4 条（08-19 的 3 条被排除）
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["TIMESTAMP"], rows[0]["TIMESTAMP"])  # 正序由 SQL 保证
        ts_list = [db.normalize_ts(r["TIMESTAMP"]) for r in rows]
        self.assertEqual(ts_list, sorted(ts_list))

    def test_fetch_sleep_stage_samples_between(self):
        conn = db.open_db(DB_PATH)
        try:
            rows = db.fetch_sleep_stage_samples_between(
                conn, fb.SLEEP_START_TS, fb.SLEEP_END_TS)
        finally:
            conn.close()
        self.assertEqual(len(rows), len(fb.SLEEP_STAGE_ROWS))

    def test_fetch_sleep_time(self):
        conn = db.open_db(DB_PATH)
        try:
            rows = db.fetch_sleep_time(conn)
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(db.normalize_ts(rows[0]["TIMESTAMP"]), fb.SLEEP_START_TS)
        self.assertEqual(db.normalize_ts(rows[0]["WAKEUP_TIME"]), fb.SLEEP_END_TS)

    def test_fetch_daily_summaries(self):
        conn = db.open_db(DB_PATH)
        try:
            rows = db.fetch_daily_summaries(conn)
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["TIMESTAMP"] is not None, True)
        self.assertEqual(rows[0]["STEPS"], fb.DAILY_STEPS)

    # -- 时间戳单位开关（评审 ②）----------------------------------------------
    def test_normalize_ts_seconds_default(self):
        self.assertEqual(db.TS_UNIT, "s")
        self.assertEqual(db.normalize_ts(1_755_000_000), 1_755_000_000)

    def test_normalize_ts_milliseconds_switch(self):
        original = db.TS_UNIT
        try:
            db.TS_UNIT = "ms"
            self.assertEqual(db.normalize_ts(1_755_000_000_000), 1_755_000_000)
        finally:
            db.TS_UNIT = original

    def test_normalize_ts_none(self):
        self.assertIsNone(db.normalize_ts(None))


if __name__ == "__main__":
    unittest.main()
