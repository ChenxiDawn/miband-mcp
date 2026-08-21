#!/usr/bin/env python3
"""miband-mcp: 小米手环 9 健康数据 MCP Server

零第三方依赖（纯标准库），MCP stdio 传输，手写 JSON-RPC 2.0 循环。
仅实现 MCP 必需方法：initialize / tools/list / tools/call，
另兼容 notifications/initialized 与 ping。

数据源：Gadgetbridge 导出的 SQLite（路径由环境变量 MIBAND_DB 或 --db 传入）。
工具：get_hr / get_sleep / get_daily（签名见规格书 §3）。

所有工具只读、幂等；日志一律走 stderr，不污染 stdout 协议流。
"""
import json
import sys

try:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import aggregator
import config
import db

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "miband-mcp"
SERVER_VERSION = "0.1.0"

# 数据库路径：环境变量 MIBAND_DB > 命令行 --db > 默认路径（见 config.py）
DB_PATH = config.resolve_db_path()


def log(msg):
    print(f"[{SERVER_NAME}] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 三个 MCP 工具的实现（签名写死，见规格书 §3）
# ---------------------------------------------------------------------------
def tool_get_hr(arguments):
    count = arguments.get("count", config.HR_DEFAULT_COUNT)
    try:
        count = int(count)
    except (TypeError, ValueError):
        raise ValueError("count 必须是整数")
    count = max(1, min(count, config.HR_MAX_COUNT))

    conn = db.open_db(DB_PATH)
    try:
        rows = db.fetch_latest_activity_samples(conn, count)
    finally:
        conn.close()
    return aggregator.aggregate_hr(rows)


def tool_get_sleep(arguments):
    date_str = arguments.get("date") or aggregator.today_str()
    aggregator.valid_date_str(date_str)

    conn = db.open_db(DB_PATH)
    try:
        sleep_time_rows = db.fetch_sleep_time(conn)
        interval = aggregator.pick_sleep_interval(sleep_time_rows, date_str)
        if interval is None:
            return aggregator.aggregate_sleep(date_str, None, [])
        start_ts, end_ts = interval
        stage_rows = db.fetch_sleep_stage_samples_between(conn, start_ts, end_ts)
    finally:
        conn.close()
    return aggregator.aggregate_sleep(date_str, interval, stage_rows)


def tool_get_daily(arguments):
    date_str = arguments.get("date") or aggregator.today_str()
    aggregator.valid_date_str(date_str)

    start_ts, end_ts = aggregator.date_window(date_str)
    conn = db.open_db(DB_PATH)
    try:
        daily_rows = db.fetch_daily_summaries(conn)
        sample_rows = db.fetch_activity_samples_between(conn, start_ts, end_ts)
    finally:
        conn.close()
    return aggregator.aggregate_daily(date_str, daily_rows, sample_rows)


# ---------------------------------------------------------------------------
# 工具注册表（tools/list 的输入 schema 与 tools/call 分发共用）
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "get_hr",
        "description": (
            "读取小米手环最近 count 条心率采样（数据来自 Gadgetbridge 同步，"
            "可能滞后于真实时间）。返回 samples 时间戳数组、来源标注与 stale 标记。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": f"条数，默认 {config.HR_DEFAULT_COUNT}，最大 {config.HR_MAX_COUNT}",
                }
            },
        },
    },
    {
        "name": "get_sleep",
        "description": (
            "查询指定日期（默认今天）的睡眠情况：入睡/醒来时间、总时长、"
            "分期分钟数。跨天睡眠归属于'醒来那天'。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "查询日期，格式 YYYY-MM-DD，默认今天",
                }
            },
        },
    },
    {
        "name": "get_daily",
        "description": (
            "查询指定日期（默认今天）的每日活动摘要：步数、活动分钟、"
            "心率均值/最低/最高。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "查询日期，格式 YYYY-MM-DD，默认今天",
                }
            },
        },
    },
]

TOOL_IMPLS = {
    "get_hr": tool_get_hr,
    "get_sleep": tool_get_sleep,
    "get_daily": tool_get_daily,
}


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 基础
# ---------------------------------------------------------------------------
def make_result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def make_error(req_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def text_result(payload):
    """MCP tools/call 的成功结果：结构化数据放 content[0].text（JSON 字符串）。"""
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
        ],
        "isError": False,
    }


def error_result(message):
    """MCP tools/call 的业务失败：走正常 result，isError=true，不炸协议。"""
    return {
        "content": [
            {"type": "text",
             "text": json.dumps({"error": message}, ensure_ascii=False)}
        ],
        "isError": True,
    }


# ---------------------------------------------------------------------------
# 请求分发
# ---------------------------------------------------------------------------
def handle_request(msg):
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    # 通知类（无 id）：initialized / cancelled 等，直接忽略
    if req_id is None:
        return None

    if method == "initialize":
        return make_result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method == "ping":
        return make_result(req_id, {})

    if method == "tools/list":
        return make_result(req_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        impl = TOOL_IMPLS.get(name)
        if impl is None:
            return make_result(req_id, error_result(f"unknown tool: {name}"))
        try:
            payload = impl(arguments)
            return make_result(req_id, text_result(payload))
        except db.DbError as e:
            return make_result(req_id, error_result(str(e)))
        except ValueError as e:
            return make_result(req_id, error_result(f"invalid argument: {e}"))
        except Exception as e:  # 兜底：工具内部任何异常都不崩服务
            log(f"tool {name} crashed: {e!r}")
            return make_result(req_id, error_result(f"internal error: {e}"))

    return make_error(req_id, -32601, f"method not found: {method}")


def main():
    # banner 格式按规格书 §5：miband-mcp started, db=<path>
    print(f"{SERVER_NAME} started, db={DB_PATH}", file=sys.stderr)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            send(make_error(None, -32700, f"parse error: {e}"))
            continue
        try:
            resp = handle_request(msg)
        except Exception as e:  # 分发层兜底，保证进程不退出
            log(f"dispatch crashed: {e!r}")
            resp = make_error(msg.get("id"), -32603, f"internal error: {e}")
        if resp is not None:
            send(resp)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
