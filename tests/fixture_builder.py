#!/usr/bin/env python3
"""测试 fixture 生成器：动态建一个仿真 Gadgetbridge 导出的 SQLite。

设计（4 问评审 ③）：
- 数据动态生成到 tests/fixtures/ 下，跑完由调用方清理，不入库；
- 同时被 unittest（setUpClass/tearDownClass）与 pytest（conftest.py）复用。

所有时间戳用 datetime(...).timestamp() 生成（本地时区），
与 miband-mcp 代码侧的 fromtimestamp / strptime 口径一致，保证测试自洽。
"""
import os
import sqlite3
from datetime import datetime

import db as dbmod  # 复用表名常量，保证 fixture 与代码映射一致

# ---------------------------------------------------------------------------
# 固定的参考时间线（本地时区）
# ---------------------------------------------------------------------------
DAY_STR = "2026-08-18"            # get_sleep / get_daily 的查询日
PREV_DAY_STR = "2026-08-17"       # 跨天睡眠的入睡日

SLEEP_START_DT = datetime(2026, 8, 17, 23, 40)   # 入睡：前一天 23:40
SLEEP_END_DT = datetime(2026, 8, 18, 7, 20)      # 醒来：查询日 07:20
SLEEP_START_TS = int(SLEEP_START_DT.timestamp())
SLEEP_END_TS = int(SLEEP_END_DT.timestamp())

# 每日汇总（查询日）
DAILY_STEPS = 8234
DAILY_CALORIES = 3200

# 查询日(08-18)内的活动采样：(hour, minute, heart_rate, steps, raw_kind)
# 其中 12:00 的 heart_rate=255 是 Gadgetbridge 的"未测到"哨兵值，应被过滤。
ACTIVITY_ROWS = [
    (8, 30, 72, 120, 1),
    (9, 0, 80, 200, 1),
    (12, 0, 255, 50, 0),   # 无效心率哨兵
    (18, 0, 95, 300, 1),
]

# get_hr 专用：时间线上最靠后的几条（放在查询日"次日"08-19，
# 保证它们是全局"最近"样本，同时落在 get_daily(08-18) 的时间窗之外）。
RECENT_HR_ROWS = [
    (20, 0, 70, 10, 0),
    (20, 10, 71, 12, 0),
    (20, 20, 72, 15, 0),
]

# 睡眠分期采样：(相对入睡的分钟偏移, sleep_stage)
# 0=awake 1=light 2=deep 3=rem（与 db.SLEEP_STAGE_MAP 对齐）
SLEEP_STAGE_ROWS = [
    (30, 1),   # light
    (60, 2),   # deep
    (90, 3),   # rem
    (120, 1),  # light
    (150, 0),  # awake（不计入 light/deep/rem）
    (180, 2),  # deep
]


def _ts_on(date_str, hour, minute):
    y, m, d = (int(x) for x in date_str.split("-"))
    return int(datetime(y, m, d, hour, minute).timestamp())


def build_db(path):
    """在 path 处创建 fixture 数据库；父目录自动创建。返回 path。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        os.remove(path)

    conn = sqlite3.connect(path)
    cur = conn.cursor()

    # 心率/活动采样（真机列名：TIMESTAMP/HEART_RATE/STEPS/RAW_KIND）
    cur.execute(f"""
        CREATE TABLE {dbmod.TABLE_ACTIVITY} (
            TIMESTAMP INTEGER, HEART_RATE INTEGER, STEPS INTEGER,
            RAW_INTENSITY INTEGER, RAW_KIND INTEGER
        )
    """)
    for (h, m, hr, steps, kind) in ACTIVITY_ROWS:
        cur.execute(
            f"INSERT INTO {dbmod.TABLE_ACTIVITY} VALUES (?,?,?,?,?)",
            (_ts_on(DAY_STR, h, m), hr, steps, 0, kind),
        )
    for (h, m, hr, steps, kind) in RECENT_HR_ROWS:
        cur.execute(
            f"INSERT INTO {dbmod.TABLE_ACTIVITY} VALUES (?,?,?,?,?)",
            (_ts_on("2026-08-19", h, m), hr, steps, 0, kind),
        )

    # 睡眠分期（真机列名：TIMESTAMP/STAGE）
    cur.execute(f"""
        CREATE TABLE {dbmod.TABLE_SLEEP_STAGE} (
            TIMESTAMP INTEGER, STAGE INTEGER
        )
    """)
    for (offset_min, stage) in SLEEP_STAGE_ROWS:
        cur.execute(
            f"INSERT INTO {dbmod.TABLE_SLEEP_STAGE} VALUES (?,?)",
            (SLEEP_START_TS + offset_min * 60, stage),
        )

    # 入睡/醒来时间段（真机列名：TIMESTAMP/WAKEUP_TIME）
    cur.execute(f"""
        CREATE TABLE {dbmod.TABLE_SLEEP_TIME} (
            TIMESTAMP INTEGER, WAKEUP_TIME INTEGER
        )
    """)
    cur.execute(
        f"INSERT INTO {dbmod.TABLE_SLEEP_TIME} VALUES (?,?)",
        (SLEEP_START_TS, SLEEP_END_TS),
    )

    # 每日汇总（真机列名：TIMESTAMP 秒级时间戳/STEPS）
    cur.execute(f"""
        CREATE TABLE {dbmod.TABLE_DAILY} (
            TIMESTAMP INTEGER, STEPS INTEGER, CALORIES INTEGER
        )
    """)
    cur.execute(
        f"INSERT INTO {dbmod.TABLE_DAILY} VALUES (?,?,?)",
        (_ts_on(DAY_STR, 0, 0), DAILY_STEPS, DAILY_CALORIES),
    )

    conn.commit()
    conn.close()
    return path


def build_empty_db(path):
    """建一个没有任何业务表的空库，用于测试 TableMissingError。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE dummy (x INTEGER)")
    conn.commit()
    conn.close()
    return path


def remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
