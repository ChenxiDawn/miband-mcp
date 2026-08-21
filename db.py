#!/usr/bin/env python3
"""miband-mcp 数据层：读取 Gadgetbridge 导出的 SQLite。

职责边界（规格书 §4 原则）：
- 只负责"查出来"，返回 dict/list 原始数据；
- 不做任何聚合/摘要，聚合全部在 aggregator.py；
- 数据库缺失、表缺失时抛出明确异常，由上层转为错误 JSON。

表名与列名来自规格书 §4，均为社区逆向 + 教程口径，
**待真机导出后逐列验证**（见 总结文档-待办）。
"""
import os
import sqlite3

# ---------------------------------------------------------------------------
# 时间戳单位开关（4 问评审 ②：默认按 Unix 秒级处理）
# 真机验证若发现 Gadgetbridge 导出的 ts 是毫秒级，把 TS_UNIT 改成 "ms" 即可。
# ---------------------------------------------------------------------------
TS_UNIT = "s"   # "s" | "ms"

# ---------------------------------------------------------------------------
# 表名映射（待真机验证）
# ---------------------------------------------------------------------------
TABLE_ACTIVITY = "XIAOMI_ACTIVITY_SAMPLE"        # 心率/活动采样（待真机验证）
TABLE_SLEEP_STAGE = "XIAOMI_SLEEP_STAGE_SAMPLE"  # 睡眠分期（待真机验证）
TABLE_DAILY = "XIAOMI_DAILY_SUMMARY_SAMPLE"      # 每日汇总（待真机验证）
TABLE_SLEEP_TIME = "XIAOMI_SLEEP_TIME_SAMPLE"    # 入睡/醒来时间段（待真机验证）

# 睡眠分期编码（规格书 §4：0=awake 1=light 2=deep 3=rem）（待真机验证）
SLEEP_STAGE_MAP = {0: "awake", 1: "light", 2: "deep", 3: "rem"}

# 活动类型编码：raw_kind 中视为"活动状态"的值，用于统计 active_min
# Gadgetbridge ActivityKind 惯例：1=activity（待真机验证）
ACTIVE_RAW_KINDS = {1}


class DbError(Exception):
    """数据层统一异常基类。"""


class DatabaseNotFoundError(DbError):
    def __init__(self, path):
        super().__init__(f"database not found: {path}")
        self.path = path


class TableMissingError(DbError):
    def __init__(self, table):
        super().__init__(f"table missing in database: {table}")
        self.table = table


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def normalize_ts(ts):
    """把库内时间戳统一为 Unix 秒。"""
    if ts is None:
        return None
    ts = int(ts)
    if TS_UNIT == "ms":
        ts = ts // 1000
    return ts


def open_db(path):
    """打开数据库；文件不存在时抛 DatabaseNotFoundError。"""
    if not path or not os.path.isfile(path):
        raise DatabaseNotFoundError(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _require_table(conn, name):
    if not table_exists(conn, name):
        raise TableMissingError(name)


def _rows_to_dicts(rows):
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 查询函数（均返回 list[dict] / dict，不做聚合）
# ---------------------------------------------------------------------------
def fetch_latest_activity_samples(conn, count):
    """最近 count 条心率/活动采样，按时间倒序返回。"""
    _require_table(conn, TABLE_ACTIVITY)
    rows = conn.execute(
        f"SELECT * FROM {TABLE_ACTIVITY} ORDER BY TIMESTAMP DESC LIMIT ?",
        (count,),
    ).fetchall()
    return _rows_to_dicts(rows)


def fetch_activity_samples_between(conn, start_ts, end_ts):
    """指定时间窗 [start_ts, end_ts) 内的活动采样，按时间正序。"""
    _require_table(conn, TABLE_ACTIVITY)
    rows = conn.execute(
        f"SELECT * FROM {TABLE_ACTIVITY} WHERE TIMESTAMP >= ? AND TIMESTAMP < ? ORDER BY TIMESTAMP ASC",
        (start_ts, end_ts),
    ).fetchall()
    return _rows_to_dicts(rows)


def fetch_sleep_stage_samples_between(conn, start_ts, end_ts):
    """指定时间窗内的睡眠分期采样，按时间正序。"""
    _require_table(conn, TABLE_SLEEP_STAGE)
    rows = conn.execute(
        f"SELECT * FROM {TABLE_SLEEP_STAGE} WHERE TIMESTAMP >= ? AND TIMESTAMP < ? ORDER BY TIMESTAMP ASC",
        (start_ts, end_ts),
    ).fetchall()
    return _rows_to_dicts(rows)


def fetch_sleep_time(conn):
    """全部入睡/醒来时间段，按开始时间倒序。列名 TIMESTAMP/WAKEUP_TIME（真机验证）。"""
    _require_table(conn, TABLE_SLEEP_TIME)
    rows = conn.execute(
        f"SELECT * FROM {TABLE_SLEEP_TIME} ORDER BY TIMESTAMP DESC"
    ).fetchall()
    return _rows_to_dicts(rows)


def fetch_daily_summaries(conn):
    """全部每日汇总行（一天一行，量小，整表取回由上层过滤）。

    date 列格式：真机为 TIMESTAMP（秒级时间戳）。
    """
    _require_table(conn, TABLE_DAILY)
    rows = conn.execute(
        f"SELECT * FROM {TABLE_DAILY} ORDER BY TIMESTAMP ASC"
    ).fetchall()
    return _rows_to_dicts(rows)
