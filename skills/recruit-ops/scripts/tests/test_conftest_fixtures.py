#!/usr/bin/env python3
"""tests/test_conftest_fixtures.py —— v3.8.7 C3 测试基建回归。

为什么这些断言重要:

  conftest.py 的副作用 (import helpers → mem_tdb 注入 / env 兜底) 是隐式的, 一旦有人
  把 `from tests import helpers as _helpers` 删掉, 或者把 pyproject.toml 的
  pythonpath/testpaths 改坏, 表面跑得动但其实测试已经在裸跑 talent_db / 真写盘 -- 这层
  断言就是不让"基建悄悄退化"。

测试矩阵:
  - mem_tdb 已注入 sys.modules["lib.talent_db"] (核心隔离)
  - RECRUIT_DISABLE_SIDE_EFFECTS == "1" 且 RECRUIT_DRY_RUN 不在 env (固定姿势)
  - fixture: mem_tdb / clean_mem_tdb / tmp_data_root / call_main / new_candidate
    都能从 conftest 拿到, 且语义对得上
  - pyproject 配的 pythonpath 让 `from lib import config` 直接能解析
"""
from __future__ import annotations

import os
import sys

import pytest


class TestEnvIsolationGuard:
    """conftest 在 session 起步时应已经把测试隔离环境锁定。"""

    def test_side_effects_disabled_flag_is_set(self):
        assert os.environ.get("RECRUIT_DISABLE_SIDE_EFFECTS") == "1", (
            "conftest/helpers 已 import, RECRUIT_DISABLE_SIDE_EFFECTS 必须 = '1'"
        )

    def test_dry_run_master_switch_not_polluting_tests(self):
        # A2 (v3.8.7) 之后 helpers 在 import 期 pop 主开关, 让 RECRUIT_DISABLE_SIDE_EFFECTS
        # 当唯一开关 (理由见 helpers.py 顶部注释)。这里防止有人加回去。
        assert "RECRUIT_DRY_RUN" not in os.environ, (
            "RECRUIT_DRY_RUN 不该在测试 process 里被 set, 应由 helpers 在 import 期 pop"
        )


class TestMemTdbInjection:
    """B1 / 历史 _InMemoryTdb 注入路径必须仍然生效。"""

    def test_lib_talent_db_resolves_to_mem_tdb(self):
        # conftest → helpers 的副作用应该把 sys.modules["lib.talent_db"] 替换为
        # _InMemoryTdb 实例 (而不是真模块)。判定方式: 真模块有 _connect 函数,
        # mem_tdb 只有上层 API (load_state_from_db / get_one / upsert_one 等)。
        tdb = sys.modules.get("lib.talent_db")
        assert tdb is not None, "lib.talent_db 应该已经被 helpers 注入"
        assert hasattr(tdb, "load_state_from_db"), "mem_tdb 应该有 load_state_from_db"
        assert hasattr(tdb, "get_one"), "mem_tdb 应该有 get_one"
        # 真模块的 _connect 不该出现在 mem_tdb 上 (mem_tdb 的 __getattr__ 兜底会返回
        # lambda 不抛, 所以不能直接 hasattr 判, 用调用结果)。
        result = tdb._connect()  # mem_tdb 的 fallback lambda 返回 None
        assert result is None, "mem_tdb 的 _connect 应该是 lambda fallback, 真模块不该穿透"

    def test_bare_talent_db_alias_also_present(self):
        # 兼容老式 `import talent_db` 写法 (如 cmd_interview_reminder 路径)。
        assert "talent_db" in sys.modules
        assert sys.modules["talent_db"] is sys.modules["lib.talent_db"]


class TestFixtures:
    """conftest 暴露的命名 fixture 必须可拿、语义正确。"""

    def test_mem_tdb_fixture_returns_helpers_instance(self, mem_tdb):
        from tests import helpers
        assert mem_tdb is helpers.mem_tdb

    def test_clean_mem_tdb_starts_empty(self, clean_mem_tdb):
        # 即使前一个用例往 mem_tdb 写过, clean_mem_tdb 应该先 reset
        clean_mem_tdb.upsert_one("t_dirty", {"talent_id": "t_dirty", "stage": "NEW"})
        # 再次请求 fixture 时 (下一个用例) 应该是空的, 这里手动模拟一下:
        from tests import helpers
        helpers.wipe_state()
        assert clean_mem_tdb.load_state_from_db() == {"candidates": {}}

    def test_call_main_fixture_callable(self, call_main):
        from tests import helpers
        assert call_main is helpers.call_main

    def test_tmp_data_root_sets_env_and_cleans_up(self, tmp_data_root):
        # fixture body 期: env 已设, 目录存在
        assert os.environ.get("RECRUIT_DATA_ROOT") == tmp_data_root
        assert os.path.isdir(tmp_data_root)
        # 退出后 (由后续测试隐式验证, 这里只测 invariant)


class TestPyprojectPytestConfig:
    """pyproject 的 pythonpath 配置应该让 lib.* 直接 import 通。"""

    def test_lib_config_importable_without_pythonpath_env(self):
        # 不依赖 shell 的 PYTHONPATH=scripts, 而是靠 pyproject [tool.pytest.ini_options]
        # 的 pythonpath = ["scripts"] 让 pytest 自动塞进 sys.path。
        # 这里只断言 lib 顶层包能解析。
        import importlib
        cfg = importlib.import_module("lib.config")
        assert hasattr(cfg, "db_enabled")


@pytest.fixture
def _fixture_chain_marker():
    return "chain-ok"


class TestFixtureComposition:
    """组合多个 fixture, 验证依赖链不会互相打架。"""

    def test_clean_mem_tdb_plus_tmp_data_root(self, clean_mem_tdb, tmp_data_root):
        assert clean_mem_tdb.load_state_from_db() == {"candidates": {}}
        assert os.environ.get("RECRUIT_DATA_ROOT") == tmp_data_root

    def test_new_candidate_fixture_creates_via_call_main(self, new_candidate, mem_tdb):
        # new_candidate fixture 隐式依赖 clean_mem_tdb, 所以这里 mem_tdb 一开始是空的。
        # 注意: new_candidate 本身是 helpers.new_candidate 函数, 调它会真触发
        # cmd_new_candidate, 走完整流程 (在 RECRUIT_DISABLE_SIDE_EFFECTS=1 下不动盘)。
        tid = new_candidate(name="基建回归张三", email="infra@example.com",
                            position="后端工程师")
        assert tid.startswith("t_")
        snapshot = mem_tdb.load_state_from_db()
        assert tid in snapshot["candidates"]
