#!/usr/bin/env python3
"""miband-mcp 聚合层：原始样本 → 摘要 JSON。

职责边界（规格书 §4 原则）：
- db.py 只吐原始 dict/list，本层负责全部聚合、过滤、格式化；
- 输出结构严格对齐规格书 §3 的三个工具返回签名（含 4 问评审补丁：
  get_sleep 必含 total_min 与 fell_asleep_date）。

所有时间均按**本地时区**解释（评审 ④ 结论）。
"""
from datetime import datetime

import db

# ---------------------------------------------------------------------------
# 采样间隔常量（骨架假设，均待真机验证）
# ---------------------------------------------------------------------------
SLEEP_STAGE_MIN = 5     # 每条睡眠分期采样代表的分钟数（Gadgetbridge 惯例约 5 分钟）
ACTIVITY_SAMPLE_MIN = 1  # 每条活动采样代表的分钟数（用于折算 active_min）

# 数据新鲜度来源标注（规格书 §3.1 返回字段 source）
SOURCE_TAG = "gadgetbridge"

STAGE_NAMES = ("light", "deep", "rem")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def valid_bpm(value):
    """Gadgetbridge 用 255 表示'未测到心率'；一并排除非正数与 None。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return False
    return 0 < v < 250


def iso_local(ts):
    """Unix 秒 → 本地时区 ISO 字符串，如 2026-08-19T08:30:00。"""
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def valid_date_str(s):
    """校验 YYYY-MM-DD；合法返回原串，非法抛 ValueError。"""
    datetime.strptime(s, "%Y-%m-%d")
    return s


def date_window(date_str):
    """返回某天本地时区的 [start_ts, end_ts) Unix 秒窗口。"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    start = int(d.timestamp())
    return start, start + 86400


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# get_hr 聚合
# ---------------------------------------------------------------------------
def aggregate_hr(rows):
    """rows: db.fetch_latest_activity_samples 的原始行（时间倒序）。

    输出按时间**正序**（旧→新），便于 AI 阅读；过滤无效心率。
    """
    samples = []
    for row in rows:
        ts = db.normalize_ts(row.get("TIMESTAMP"))
        bpm = row.get("HEART_RATE")
        if ts is None or not valid_bpm(bpm):
            continue
        samples.append({"ts": iso_local(ts), "bpm": int(bpm)})
    samples.sort(key=lambda s: s["ts"])
    return {
        "samples": samples,
        "source": SOURCE_TAG,
        "stale": len(samples) == 0,
    }


# ---------------------------------------------------------------------------
# get_sleep 聚合
# ---------------------------------------------------------------------------
def pick_sleep_interval(sleep_time_rows, date_str):
    """从全部睡眠时间段中挑出属于 date_str 的那一段。

    规则：优先按"醒来日期 == date_str"匹配（跨天睡眠归属于醒来的那天）；
    其次按"入睡日期 == date_str"兜底。返回 (start_ts, end_ts) 或 None。
    """
    fallback = None
    for row in sleep_time_rows:
        s = db.normalize_ts(row.get("TIMESTAMP"))
        e = db.normalize_ts(row.get("WAKEUP_TIME"))
        if s is None or e is None or e <= s:
            continue
        wake_date = datetime.fromtimestamp(e).date().isoformat()
        if wake_date == date_str:
            return (s, e)
        start_date = datetime.fromtimestamp(s).date().isoformat()
        if start_date == date_str and fallback is None:
            fallback = (s, e)
    return fallback


def aggregate_sleep(date_str, interval, stage_rows):
    """interval: (start_ts, end_ts) 或 None；stage_rows: 该区间内的分期采样。"""
    empty = {
        "date": date_str,
        "fell_asleep": None,
        "fell_asleep_date": None,
        "woke_up": None,
        "total_min": 0,
        "stages": {name: 0 for name in STAGE_NAMES},
        "quality": "unknown",
        "stale": True,
    }
    if interval is None:
        return empty

    s, e = interval
    stages = {name: 0 for name in STAGE_NAMES}
    for row in stage_rows:
        name = db.SLEEP_STAGE_MAP.get(row.get("STAGE"))
        if name in stages:
            stages[name] += SLEEP_STAGE_MIN

    total_min = int((e - s) // 60)
    if total_min <= 0:
        total_min = sum(stages.values())

    return {
        "date": date_str,
        "fell_asleep": datetime.fromtimestamp(s).strftime("%H:%M"),
        "fell_asleep_date": datetime.fromtimestamp(s).date().isoformat(),
        "woke_up": datetime.fromtimestamp(e).strftime("%H:%M"),
        "total_min": total_min,
        "stages": stages,
        # quality 目前为占位启发式：有完整睡眠段即 normal。
        # 真实质量评分规则待真机数据接入后补充（见 总结文档-待办）。
        "quality": "normal" if total_min > 0 else "unknown",
        "stale": False,
    }


# ---------------------------------------------------------------------------
# get_daily 聚合
# ---------------------------------------------------------------------------
def _daily_row_date(row):
    """解析每日汇总行的日期。真机为 TIMESTAMP 秒级时间戳。"""
    v = row.get("TIMESTAMP")
    if v is None:
        return None
    if isinstance(v, str):
        return v[:10]
    try:
        return datetime.fromtimestamp(db.normalize_ts(int(v))).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def aggregate_daily(date_str, daily_rows, sample_rows):
    """daily_rows: 每日汇总全表；sample_rows: 当天时间窗内的活动采样。"""
    steps = None
    for row in daily_rows:
        if _daily_row_date(row) == date_str:
            try:
                steps = int(row.get("STEPS"))
            except (TypeError, ValueError):
                steps = None
            break

    if steps is None:
        # 兜底：用当天活动采样的 steps 累加（口径待真机验证）
        steps = sum(int(row.get("STEPS") or 0) for row in sample_rows)

    bpms = [int(r.get("HEART_RATE")) for r in sample_rows
            if valid_bpm(r.get("HEART_RATE"))]
    active_samples = [r for r in sample_rows
                      if r.get("RAW_KIND") in db.ACTIVE_RAW_KINDS]

    no_data = steps == 0 and not bpms and not active_samples
    return {
        "date": date_str,
        "steps": steps,
        "active_min": len(active_samples) * ACTIVITY_SAMPLE_MIN,
        "hr_avg": int(sum(bpms) / len(bpms)) if bpms else 0,
        "hr_min": min(bpms) if bpms else 0,
        "hr_max": max(bpms) if bpms else 0,
        "stale": no_data,
    }
