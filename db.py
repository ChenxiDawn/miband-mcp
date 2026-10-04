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
# 时间戳单位（2026-10-04 实测修正：**各表单位不一致！**）
#   XIAOMI_ACTIVITY_SAMPLE      -> 秒
#   XIAOMI_SLEEP_STAGE_SAMPLE   -> 毫秒
#   XIAOMI_SLEEP_TIME_SAMPLE    -> 毫秒
#   XIAOMI_DAILY_SUMMARY_SAMPLE -> 毫秒
# 旧代码用全局 TS_UNIT="s" 统一按秒读 → 睡眠表一读就崩（OSError Errno 22）。
# 现在改为「建表时换算 + normalize_ts 按量级兑底」。
# ---------------------------------------------------------------------------
TS_UNIT_BY_TABLE = {
    "XIAOMI_ACTIVITY_SAMPLE": "s",
    "XIAOMI_SLEEP_STAGE_SAMPLE": "ms",
    "XIAOMI_SLEEP_TIME_SAMPLE": "ms",
    "XIAOMI_DAILY_SUMMARY_SAMPLE": "ms",
}

# ---------------------------------------------------------------------------
# 表名映射（待真机验证）
# ---------------------------------------------------------------------------
TABLE_ACTIVITY = "XIAOMI_ACTIVITY_SAMPLE"        # 心率/活动采样（待真机验证）
TABLE_SLEEP_STAGE = "XIAOMI_SLEEP_STAGE_SAMPLE"  # 睡眠分期（待真机验证）
TABLE_DAILY = "XIAOMI_DAILY_SUMMARY_SAMPLE"      # 每日汇总（待真机验证）
TABLE_SLEEP_TIME = "XIAOMI_SLEEP_TIME_SAMPLE"    # 入睡/醒来时间段（待真机验证）

# 睡眠分期编码（2026-10-04 修正，来源：Gadgetbridge XiaomiSampleProvider.java）
#   2=deep 深睡 / 3=light 浅睡 / 4=rem / 5=awake（夜间醒） / 0=final awake（结束时醒）
# 旧表 {0:awake,1:light,2:deep,3:rem} 整张写错，已废弃。
SLEEP_STAGE_MAP = {0: "awake", 2: "deep", 3: "light", 4: "rem", 5: "awake"}

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
    """把库内时间戳统一为 Unix 秒（按量级自动判定：> 1e11 视为毫秒）。"""
    if ts is None:
        return None
    ts = int(ts)
    if ts > 10 ** 11:      # 毫秒（1e11 秒 ≈ 公元 5138 年，不会误伤）
        ts //= 1000
    return ts


def to_table_ts(table, ts_seconds):
    """把 Unix 秒换算成该表自己的时间戳单位（用于 SQL 里的比较）。"""
    if TS_UNIT_BY_TABLE.get(table) == "ms":
        return int(ts_seconds) * 1000
    return int(ts_seconds)


def ts_floor(days, now_ts=None):
    """“只看最近 days 天”的起始 Unix 秒（days<=0 表示不限制）。"""
    import time
    if not days or days <= 0:
        return None
    now_ts = int(now_ts if now_ts is not None else time.time())
    return now_ts - int(days) * 86400


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
def fetch_latest_activity_samples(conn, count, since_ts=None, heart_rate_only=False):
    """最近 count 条心率/活动采样，按时间倒序。

    since_ts：只看最近 N 天（Unix 秒）。
    heart_rate_only：只要“有心率”的行 —— 否则取回的多是只有步数的采样，
    get_hr 会显得很稀疏（2026-10-04 实测：最近 10 行里仅 1 行带心率）。
    """
    _require_table(conn, TABLE_ACTIVITY)
    cond, params = [], []
    if since_ts:
        cond.append("TIMESTAMP >= ?")
        params.append(to_table_ts(TABLE_ACTIVITY, since_ts))
    if heart_rate_only:
        cond.append("HEART_RATE IS NOT NULL AND HEART_RATE > 0 AND HEART_RATE < 250")
    sql = f"SELECT * FROM {TABLE_ACTIVITY}"
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY TIMESTAMP DESC LIMIT ?"
    params.append(count)
    rows = conn.execute(sql, tuple(params)).fetchall()
    return _rows_to_dicts(rows)


def fetch_activity_samples_between(conn, start_ts, end_ts):
    """指定时间窗 [start_ts, end_ts) 内的活动采样，按时间正序。"""
    _require_table(conn, TABLE_ACTIVITY)
    rows = conn.execute(
        f"SELECT * FROM {TABLE_ACTIVITY} WHERE TIMESTAMP >= ? AND TIMESTAMP < ? ORDER BY TIMESTAMP ASC",
        (to_table_ts(TABLE_ACTIVITY, start_ts), to_table_ts(TABLE_ACTIVITY, end_ts)),
    ).fetchall()
    return _rows_to_dicts(rows)


def fetch_sleep_stage_samples_between(conn, start_ts, end_ts):
    """指定时间窗内的睡眠分期采样，按时间正序。

    ⚠️ 睡眠表是**毫秒**，必须过 to_table_ts 换算，否则一条都匹配不到。
    """
    _require_table(conn, TABLE_SLEEP_STAGE)
    rows = conn.execute(
        f"SELECT * FROM {TABLE_SLEEP_STAGE} WHERE TIMESTAMP >= ? AND TIMESTAMP < ? ORDER BY TIMESTAMP ASC",
        (to_table_ts(TABLE_SLEEP_STAGE, start_ts), to_table_ts(TABLE_SLEEP_STAGE, end_ts)),
    ).fetchall()
    return _rows_to_dicts(rows)


def fetch_sleep_time(conn, since_ts=None):
    """入睡/醒来时间段，按开始时间倒序。可只取最近 since_ts 之后的。"""
    _require_table(conn, TABLE_SLEEP_TIME)
    if since_ts:
        rows = conn.execute(
            f"SELECT * FROM {TABLE_SLEEP_TIME} WHERE TIMESTAMP >= ? ORDER BY TIMESTAMP DESC",
            (to_table_ts(TABLE_SLEEP_TIME, since_ts),),
        ).fetchall()
        return _rows_to_dicts(rows)
    rows = conn.execute(
        f"SELECT * FROM {TABLE_SLEEP_TIME} ORDER BY TIMESTAMP DESC"
    ).fetchall()
    return _rows_to_dicts(rows)


def fetch_daily_summaries(conn, since_ts=None):
    """每日汇总行（一天一行，量小）。可只取最近 since_ts 之后的。

    date 列格式：真机为 TIMESTAMP（**毫秒**，2026-10-04 实测）。
    """
    _require_table(conn, TABLE_DAILY)
    if since_ts:
        rows = conn.execute(
            f"SELECT * FROM {TABLE_DAILY} WHERE TIMESTAMP >= ? ORDER BY TIMESTAMP ASC",
            (to_table_ts(TABLE_DAILY, since_ts),),
        ).fetchall()
        return _rows_to_dicts(rows)
    rows = conn.execute(
        f"SELECT * FROM {TABLE_DAILY} ORDER BY TIMESTAMP ASC"
    ).fetchall()
    return _rows_to_dicts(rows)
