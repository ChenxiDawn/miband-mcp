#!/usr/bin/env python3
"""pytest 入口配置（同时兼容 python -m unittest 直接跑）。

作用：
1. 把项目根目录与 tests/ 目录加入 sys.path，
   保证 `import db / config / aggregator / fixture_builder` 在任意工作目录下可用；
2. 提供 pytest 风格的共享 fixture（动态建库、跑完自清，见 4 问评审 ③）。

注意：本项目零第三方依赖，测试本体用标准库 unittest 编写，
pytest 未安装时可用 `python -m unittest discover tests` 运行。
"""
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)

for p in (PROJECT_ROOT, TESTS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import fixture_builder  # noqa: E402

try:
    import pytest
except ImportError:  # 未装 pytest 时本文件仅作路径修正
    pytest = None

if pytest is not None:
    FIXTURES_DIR = os.path.join(TESTS_DIR, "fixtures")

    @pytest.fixture(scope="module")
    def db_path():
        """动态生成一个含完整仿真数据的 SQLite，测试结束自动删除。"""
        path = os.path.join(FIXTURES_DIR, "miband_test.sqlite")
        fixture_builder.build_db(path)
        yield path
        fixture_builder.remove(path)

    @pytest.fixture(scope="module")
    def empty_db_path():
        """动态生成一个没有任何业务表的空库，测试结束自动删除。"""
        path = os.path.join(FIXTURES_DIR, "miband_empty.sqlite")
        fixture_builder.build_empty_db(path)
        yield path
        fixture_builder.remove(path)
