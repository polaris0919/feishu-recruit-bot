#!/usr/bin/env python3
"""lib.cli_subprocess.run_module() 单元测试。

覆盖契约（见 plan §设计原则）：
  - 子进程 env 注入：PYTHONPATH=<scripts_dir>:existing 与 RECRUIT_WORKSPACE_ROOT
  - parse_json=True 的反向扫描语义（末尾 JSON / 末尾混 debug 行 / 完全没 JSON）
  - 非零退出 → ok=False
  - timeout → returncode=-1 + stderr 含 timeout 标识
  - 哑执行器：不读 RECRUIT_DISABLE_SIDE_EFFECTS,不识别 dry_run

所有 case 都通过真起子进程的 fixture（tests.fixtures.echo_env）验证,
不 mock subprocess.run,确保 env / argv / 解析整条链都被覆盖。
"""
from __future__ import print_function

import json
import os
import unittest

import tests.helpers as helpers  # noqa: F401  side-effect: 装内存 talent_db / env 隔离

from lib import cli_subprocess
from lib.recruit_paths import scripts_dir, workspace_path


FIXTURE_MODULE = "tests.fixtures.echo_env"


class TestRunModuleEnv(unittest.TestCase):
    """env 注入：PYTHONPATH 与 RECRUIT_WORKSPACE_ROOT。"""

    def test_pythonpath_contains_scripts_dir(self):
        res = cli_subprocess.run_module(FIXTURE_MODULE, [], parse_json=True)
        self.assertTrue(res["ok"], "stderr=" + res["stderr"])
        env = res["json"]
        self.assertIsNotNone(env, "fixture 应输出 env JSON")
        pp = env.get("PYTHONPATH") or ""
        # 子进程的 PYTHONPATH 至少要含 scripts_dir,以便它能 `import lib.xxx`
        self.assertIn(scripts_dir(), pp.split(os.pathsep))

    def test_recruit_workspace_root_injected(self):
        res = cli_subprocess.run_module(FIXTURE_MODULE, [], parse_json=True)
        self.assertTrue(res["ok"], "stderr=" + res["stderr"])
        # 注入值应等于 workspace_path() 的字符串形式
        self.assertEqual(res["json"]["RECRUIT_WORKSPACE_ROOT"], str(workspace_path()))

    def test_existing_pythonpath_preserved(self):
        """父进程 PYTHONPATH 已有值时,子进程应在前面拼上 scripts_dir,而不是覆盖。"""
        sentinel = "/tmp/__sentinel_pp_path__"
        old = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = sentinel + (
            os.pathsep + old if old else "")
        try:
            res = cli_subprocess.run_module(FIXTURE_MODULE, [], parse_json=True)
        finally:
            if old is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = old
        self.assertTrue(res["ok"], "stderr=" + res["stderr"])
        pp_parts = (res["json"]["PYTHONPATH"] or "").split(os.pathsep)
        self.assertIn(scripts_dir(), pp_parts)
        self.assertIn(sentinel, pp_parts)


class TestRunModuleJsonParsing(unittest.TestCase):
    """parse_json=True 的反向扫描语义。"""

    def test_json_at_tail_parsed(self):
        res = cli_subprocess.run_module(FIXTURE_MODULE, [], parse_json=True)
        self.assertTrue(res["ok"])
        self.assertIsInstance(res["json"], dict)
        self.assertIn("RECRUIT_WORKSPACE_ROOT", res["json"])

    def test_json_with_trailing_debug_line_still_parsed(self):
        """JSON 行后还有 debug 行时,反向扫描应仍能解析到 JSON。

        这是反向扫描相对于 splitlines()[-1] 的关键好处。如果有人把
        run_module 简化成"取最后一行"会让本测试红。
        """
        res = cli_subprocess.run_module(
            FIXTURE_MODULE, ["--tail-debug"], parse_json=True)
        self.assertTrue(res["ok"], "stderr=" + res["stderr"])
        self.assertIsInstance(res["json"], dict)
        self.assertIn("PYTHONPATH", res["json"])
        # stdout 里确实有 debug 行
        self.assertIn("post-json debug line", res["stdout"])

    def test_no_json_returns_none_without_raising(self):
        """stdout 完全没有 JSON 行 → json=None,不抛异常(哑执行器契约)。"""
        res = cli_subprocess.run_module(
            FIXTURE_MODULE, ["--no-json"], parse_json=True)
        self.assertTrue(res["ok"], "stderr=" + res["stderr"])
        self.assertIsNone(res["json"])
        # stdout 仍能拿到原始文本,业务层可自行兜底
        self.assertIn("plain text", res["stdout"])

    def test_parse_json_false_never_parses(self):
        """parse_json=False 时无论 stdout 是不是 JSON,json 字段都是 None。"""
        res = cli_subprocess.run_module(FIXTURE_MODULE, [], parse_json=False)
        self.assertTrue(res["ok"])
        self.assertIsNone(res["json"])
        # 但 stdout 里还是有 JSON 文本,调用方可自己解析
        self.assertIn("RECRUIT_WORKSPACE_ROOT", res["stdout"])


class TestRunModuleReturnCodes(unittest.TestCase):
    """非零退出 / timeout / 异常的统一返回结构。"""

    def test_nonzero_exit_marks_not_ok(self):
        res = cli_subprocess.run_module(
            FIXTURE_MODULE, ["--exit", "3", "--stderr", "boom"], parse_json=True)
        self.assertFalse(res["ok"])
        self.assertEqual(res["returncode"], 3)
        self.assertIn("boom", res["stderr"])

    def test_timeout_returns_minus_one_with_marker_in_stderr(self):
        res = cli_subprocess.run_module(
            FIXTURE_MODULE, ["--sleep", "5"], timeout=1)
        self.assertFalse(res["ok"])
        self.assertEqual(res["returncode"], -1)
        self.assertIn("timeout", res["stderr"])

    def test_unknown_module_does_not_raise(self):
        """启动一个根本不存在的模块,run_module 不应抛 Python 异常,
        而应返回 ok=False(可能 returncode=1 或 -1,具体看 python 报错)。"""
        res = cli_subprocess.run_module(
            "tests.fixtures._does_not_exist_xyz", [], parse_json=True)
        self.assertFalse(res["ok"])
        # python -m 找不到 module 时一般 rc=1 + stderr 含 "No module named"
        self.assertIn("No module named", res["stderr"])


class TestRunModuleStructure(unittest.TestCase):
    """返回结构的字段契约。"""

    def test_return_dict_has_all_required_keys(self):
        res = cli_subprocess.run_module(FIXTURE_MODULE, [], parse_json=True)
        for key in ("ok", "returncode", "stdout", "stderr", "cmd", "json"):
            self.assertIn(key, res)
        # cmd 必须包含 sys.executable + -m + module
        self.assertIn("-m", res["cmd"])
        self.assertIn(FIXTURE_MODULE, res["cmd"])


class TestRunModuleIsDumb(unittest.TestCase):
    """哑执行器契约：side_effects_disabled / dry_run 不影响 run_module 行为。"""

    def test_side_effects_disabled_does_not_short_circuit(self):
        """RECRUIT_DISABLE_SIDE_EFFECTS=1 不应让 run_module 返回 fake 成功;
        run_module 是哑执行器,所有 fake / 短路必须由调用方决定。"""
        old = os.environ.get("RECRUIT_DISABLE_SIDE_EFFECTS")
        os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = "1"
        try:
            res = cli_subprocess.run_module(
                FIXTURE_MODULE, ["--exit", "7"], parse_json=True)
        finally:
            if old is None:
                os.environ.pop("RECRUIT_DISABLE_SIDE_EFFECTS", None)
            else:
                os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = old
        # 哑执行器：side_effects_disabled 时,exit 7 仍真传到 returncode
        self.assertFalse(res["ok"])
        self.assertEqual(res["returncode"], 7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
