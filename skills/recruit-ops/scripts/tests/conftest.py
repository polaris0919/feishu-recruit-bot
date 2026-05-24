#!/usr/bin/env python3
"""scripts/tests/conftest.py —— pytest 测试基建入口 (C3, v3.8.7)

为什么需要这个文件:

  历史上所有测试都靠 `from tests import helpers` 引发 helpers.py 的导入副作用 (注入
  mem_tdb / 强制 RECRUIT_DISABLE_SIDE_EFFECTS=1 / 在 import 期 pop 主开关
  RECRUIT_DRY_RUN)。但 5 个测试文件 (test_auto_attachments / test_candidate_aliases /
  test_candidate_storage / test_email_templates / test_recruit_paths_env) 没导 helpers,
  靠 pytest collection 顺序运气 -- 如果它们在带 helpers import 的文件之后被收集, 环境
  恰好已经被 helpers 改好; 但 pytest 重排 collection 顺序 (比如加了 -k / -p no:cacheprovider /
  按 fixture 反向调度) 时会暴露。

  pytest 把 conftest.py 当 "test package 的 __init__"  在 collection 之前一定加载,
  因此把 helpers 的导入锚定在这里, 让那 5 个文件不再依赖加载顺序。

提供的能力:
  1. 把 `from tests import helpers` 提到 conftest 顶部, 让 mem_tdb / env 设定先到位。
  2. session-scoped autouse fixture: pre-flight 断言, 防止外层 shell 把
     RECRUIT_DISABLE_SIDE_EFFECTS=0 漏进来 (历史事故复盘见 docs/AGENT_RULES.md §8)。
  3. 命名 fixture `mem_tdb`: 给 pytest 函数风格新测试直接拿到内存 DB 实例,
     替代手写 `from tests.helpers import mem_tdb`。
  4. 命名 fixture `clean_mem_tdb`: 自动调 wipe_state(), 等价于 setUp 里的清场。
  5. 命名 fixture `tmp_data_root`: 临时 RECRUIT_DATA_ROOT, yield 完自动 pop + 删目录。

为什么不加 autouse function-scoped 自动 reset:
  现有测试全是 unittest.TestCase 风格, 每个用例 setUp 都自己调 mem_tdb.reset(),
  conftest 再插一手会双重 reset + 跟测试自己 mock 的 talent_db 局部替换打架。
  约定: 旧测试继续 setUp/tearDown 手写, 新写的 pytest 函数风格测试请求
  `clean_mem_tdb` fixture 即可。
"""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest

from tests import helpers as _helpers


@pytest.fixture(scope="session", autouse=True)
def _enforce_side_effect_guard():
    """Session 起步时校验测试隔离环境正确, 否则直接 fail-fast。

    避免外层 shell `RECRUIT_DISABLE_SIDE_EFFECTS=0 pytest` 把 75 个孤儿目录
    一夜堆出来 (历史复盘事故见 docs/AGENT_RULES.md §8)。
    helpers.py import 时已强制设 1 并 pop 主开关, 这里只是再断言一遍兜底,
    以防有人在 helpers 之后又改回去。
    """
    if os.environ.get("RECRUIT_DISABLE_SIDE_EFFECTS") != "1":
        raise RuntimeError(
            "tests 必须在 RECRUIT_DISABLE_SIDE_EFFECTS=1 下运行 (conftest 兜底失败)"
        )
    yield


@pytest.fixture
def mem_tdb():
    """暴露 helpers 注入的内存 DB 实例。

    用法 (pytest 函数风格):
        def test_something(mem_tdb):
            mem_tdb.upsert_one("t_xxxxxx", {...})
            ...
    """
    return _helpers.mem_tdb


@pytest.fixture
def clean_mem_tdb(mem_tdb):
    """每个用例前自动清空内存 DB, 等价于 unittest 风格的 setUp 调 wipe_state。

    用法 (pytest 函数风格, 与 mem_tdb 互斥地用其一即可):
        def test_isolated(clean_mem_tdb):
            clean_mem_tdb.upsert_one(...)   # 拿到的就是已清空的 mem_tdb
    """
    _helpers.wipe_state()
    return mem_tdb


@pytest.fixture
def tmp_data_root(tmp_path):
    """给 candidate_storage / cv_dir / email_dir 一个临时 RECRUIT_DATA_ROOT。

    跟现有 test_candidate_storage / test_candidate_aliases 里手写的 tempfile +
    setUpModule/tearDownModule 等价, 但用 fixture 形式封装, 让 yield 后自动 pop env
    并清目录 (异常退出也保证清掉)。

    用法:
        def test_writes_pdf(tmp_data_root, clean_mem_tdb):
            # tmp_data_root 已经在 RECRUIT_DATA_ROOT 里
            ...
    """
    prev = os.environ.get("RECRUIT_DATA_ROOT")
    root = str(tmp_path / "recruit_data_root")
    os.makedirs(root, exist_ok=True)
    os.environ["RECRUIT_DATA_ROOT"] = root
    try:
        yield root
    finally:
        if prev is None:
            os.environ.pop("RECRUIT_DATA_ROOT", None)
        else:
            os.environ["RECRUIT_DATA_ROOT"] = prev
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def call_main():
    """暴露 helpers.call_main, 让 pytest 风格测试不用再 import 一遍。"""
    return _helpers.call_main


@pytest.fixture
def new_candidate(clean_mem_tdb):
    """暴露 helpers.new_candidate, 隐式依赖清空过的内存 DB。"""
    return _helpers.new_candidate
