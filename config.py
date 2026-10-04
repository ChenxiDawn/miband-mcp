#!/usr/bin/env python3
"""miband-mcp 配置模块。

只做一件事：解析"数据库路径 + 默认参数"。
优先级：环境变量 MIBAND_DB > 命令行参数 --db > 默认相对路径。

零第三方依赖，纯标准库。
"""
import os
import sys

# ---------------------------------------------------------------------------
# 默认路径（当环境变量与命令行都未提供时的兜底）
# ---------------------------------------------------------------------------
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "data", "gadgetbridge.db")   # 2026-10-04 修正：原来写成 "Gadgetbridge"（少了 .db），是死路

# 环境变量名（与 Reasonix 侧配置对齐）
ENV_DB_KEY = "MIBAND_DB"

# ---------------------------------------------------------------------------
# 工具默认参数（与规格书 3 节签名一致）
# ---------------------------------------------------------------------------
HR_DEFAULT_COUNT = 10      # get_hr 默认条数
HR_MAX_COUNT = 100         # get_hr 上限，防拉爆


def resolve_db_path(argv=None):
    """按优先级解析数据库文件路径。

    返回 str（可能指向不存在的文件，由 db 层负责报错）。
    """
    if argv is None:
        argv = sys.argv[1:]

    # 1) 命令行参数 --db <path>
    for i, arg in enumerate(argv):
        if arg == "--db" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--db="):
            return arg.split("=", 1)[1]

    # 2) 环境变量
    env = os.environ.get(ENV_DB_KEY)
    if env:
        return env

    # 3) 默认路径
    return DEFAULT_DB_PATH
