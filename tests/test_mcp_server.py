#!/usr/bin/env python3
"""miband_mcp.py 协议层测试（标准库 unittest，零第三方依赖）。

两类测试：
1. 进程内：直接调 handle_request，验证 initialize / tools/list / tools/call；
2. 端到端：subprocess 启动真实进程走 stdio JSON-RPC，
   验证验收清单第 1、2 条（stderr banner + initialize 握手）。

fixture 由 fixture_builder 动态生成，跑完自清。
"""
import json
import os
import subprocess
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
for _p in (PROJECT_ROOT, TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fixture_builder as fb
import miband_mcp

FIXTURES_DIR = os.path.join(TESTS_DIR, "fixtures")
DB_PATH = os.path.join(FIXTURES_DIR, "miband_test.sqlite")
MISSING_DB_PATH = os.path.join(FIXTURES_DIR, "no_such.sqlite")
SERVER_PATH = os.path.join(PROJECT_ROOT, "miband_mcp.py")


def call(msg):
    """进程内调用 handle_request，返回响应 dict。"""
    return miband_mcp.handle_request(msg)


def tool_payload(resp):
    """从 tools/call 响应里解出 content[0].text 的 JSON。"""
    return json.loads(resp["result"]["content"][0]["text"])


class TestProtocolInProcess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fb.build_db(DB_PATH)
        cls._old_db_path = miband_mcp.DB_PATH
        miband_mcp.DB_PATH = DB_PATH

    @classmethod
    def tearDownClass(cls):
        miband_mcp.DB_PATH = cls._old_db_path
        fb.remove(DB_PATH)

    # -- initialize / tools/list ----------------------------------------------
    def test_initialize_handshake(self):
        resp = call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2024-11-05",
                                "capabilities": {},
                                "clientInfo": {"name": "test", "version": "0"}}})
        self.assertEqual(resp["id"], 1)
        self.assertIn("protocolVersion", resp["result"])
        self.assertEqual(resp["result"]["serverInfo"]["name"], "miband-mcp")

    def test_notification_returns_none(self):
        self.assertIsNone(call({"jsonrpc": "2.0",
                                "method": "notifications/initialized"}))

    def test_tools_list(self):
        resp = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertEqual(names, ["get_hr", "get_sleep", "get_daily"])
        for t in resp["result"]["tools"]:
            self.assertIn("inputSchema", t)
            self.assertIn("description", t)

    def test_unknown_method(self):
        resp = call({"jsonrpc": "2.0", "id": 3, "method": "nope"})
        self.assertEqual(resp["error"]["code"], -32601)

    # -- tools/call：get_hr ----------------------------------------------------
    def test_call_get_hr(self):
        resp = call({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                     "params": {"name": "get_hr", "arguments": {"count": 3}}})
        payload = tool_payload(resp)
        self.assertFalse(payload["stale"])
        self.assertEqual(len(payload["samples"]), 3)
        self.assertEqual(payload["source"], "gadgetbridge")

    def test_call_get_hr_clamps_count(self):
        resp = call({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                     "params": {"name": "get_hr", "arguments": {"count": 9999}}})
        payload = tool_payload(resp)
        # fixture 库内共 7 条采样，其中 1 条心率=255（无效哨兵）被过滤 → 6 条
        self.assertEqual(len(payload["samples"]), 6)

    # -- tools/call：get_sleep --------------------------------------------------
    def test_call_get_sleep(self):
        resp = call({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                     "params": {"name": "get_sleep",
                                "arguments": {"date": fb.DAY_STR}}})
        payload = tool_payload(resp)
        self.assertEqual(payload["fell_asleep"], "23:40")
        self.assertEqual(payload["fell_asleep_date"], fb.PREV_DAY_STR)
        self.assertEqual(payload["woke_up"], "07:20")
        self.assertGreater(payload["total_min"], 0)

    def test_call_get_sleep_missing_date_stale(self):
        resp = call({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                     "params": {"name": "get_sleep",
                                "arguments": {"date": "2026-01-01"}}})
        payload = tool_payload(resp)
        self.assertTrue(payload["stale"])

    # -- tools/call：get_daily --------------------------------------------------
    def test_call_get_daily(self):
        resp = call({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                     "params": {"name": "get_daily",
                                "arguments": {"date": fb.DAY_STR}}})
        payload = tool_payload(resp)
        self.assertEqual(payload["steps"], fb.DAILY_STEPS)
        self.assertEqual(payload["hr_min"], 72)
        self.assertEqual(payload["hr_max"], 95)

    # -- 错误路径 ----------------------------------------------------------------
    def test_call_unknown_tool(self):
        resp = call({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                     "params": {"name": "no_such_tool", "arguments": {}}})
        self.assertTrue(resp["result"]["isError"])

    def test_call_db_missing_returns_error_result(self):
        miband_mcp.DB_PATH = MISSING_DB_PATH
        try:
            resp = call({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                         "params": {"name": "get_hr", "arguments": {}}})
        finally:
            miband_mcp.DB_PATH = DB_PATH
        self.assertTrue(resp["result"]["isError"])
        payload = tool_payload(resp)
        self.assertIn("database not found", payload["error"])

    def test_call_invalid_date_returns_error_result(self):
        resp = call({"jsonrpc": "2.0", "id": 11, "method": "tools/call",
                     "params": {"name": "get_daily",
                                "arguments": {"date": "not-a-date"}}})
        self.assertTrue(resp["result"]["isError"])
        payload = tool_payload(resp)
        self.assertIn("invalid argument", payload["error"])


class TestEndToEndStdio(unittest.TestCase):
    """subprocess 启动真实进程，验证 banner + initialize 握手（验收清单 1、2）。"""

    @classmethod
    def setUpClass(cls):
        fb.build_db(DB_PATH)

    @classmethod
    def tearDownClass(cls):
        fb.remove(DB_PATH)

    def test_startup_banner_and_handshake(self):
        session = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "e2e", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "get_hr", "arguments": {"count": 2}}},
        ]
        stdin_data = "\n".join(json.dumps(m) for m in session) + "\n"

        proc = subprocess.run(
            [sys.executable, SERVER_PATH, "--db", DB_PATH],
            input=stdin_data, capture_output=True, text=True,
            encoding="utf-8", timeout=30,
        )
        # stderr 必须有 banner（验收清单第 1 条）
        self.assertIn("miband-mcp started", proc.stderr)
        self.assertIn("db=", proc.stderr)

        # stdout 是干净的 JSON-RPC 响应流（验收清单第 2 条）
        lines = [l for l in proc.stdout.splitlines() if l.strip()]
        self.assertEqual(len(lines), 2)  # initialize + tools/call 各一条
        init_resp = json.loads(lines[0])
        self.assertEqual(init_resp["result"]["serverInfo"]["name"], "miband-mcp")
        hr_resp = json.loads(lines[1])
        payload = json.loads(hr_resp["result"]["content"][0]["text"])
        self.assertEqual(len(payload["samples"]), 2)


if __name__ == "__main__":
    unittest.main()
