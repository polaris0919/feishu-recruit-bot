#!/usr/bin/env python3
"""tests/test_candidate_aliases.py —— v3.5.9 by_name 软链层单测。

【覆盖】
  1. sanitized_name：空 / 特殊字符 / 路径分隔符 / 长度截断
  2. alias_name_for / alias_dir_for：路径计算
  3. rebuild_alias_for：
     - 首次新建（symlink + 指向正确）
     - 已存在且正确 → already_ok（不动盘）
     - 已存在但姓名变了 → 删旧建新
     - 真目录不存在 → error 字段，不抛
     - dry-run（RECRUIT_DISABLE_SIDE_EFFECTS=1）→ 不动盘
  4. remove_alias_for：把指向某 tid 的所有 alias 一并 unlink
  5. rebuild_all_aliases：清理 dangling alias / 错误汇总
  6. 拒绝 unlink 真实目录（HR 万一手动 mkdir 了同名目录）
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

_TMP_ROOT = None


def setUpModule():
    global _TMP_ROOT
    _TMP_ROOT = tempfile.mkdtemp(prefix="ca_test_root_")
    os.environ["RECRUIT_DATA_ROOT"] = _TMP_ROOT
    os.environ.pop("RECRUIT_DISABLE_SIDE_EFFECTS", None)


def tearDownModule():
    global _TMP_ROOT
    if _TMP_ROOT and os.path.isdir(_TMP_ROOT):
        shutil.rmtree(_TMP_ROOT, ignore_errors=True)
    os.environ.pop("RECRUIT_DATA_ROOT", None)


from lib import candidate_aliases as ca  # noqa: E402
from lib import candidate_storage as cs  # noqa: E402


def _fresh_root():
    """每个 test 跑前清空 candidates_root，避免互相污染。"""
    root = cs.candidates_root()
    if root.exists():
        shutil.rmtree(str(root), ignore_errors=True)


def _make_real_dir(tid):
    """造一个真实的 candidate_dir，便于 alias 指过去。"""
    cs.ensure_candidate_dirs(tid)
    return cs.candidate_dir(tid)


# ─── 纯函数 ──────────────────────────────────────────────────────────────────

class TestSanitize(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(ca.sanitized_name(None), "未命名")
        self.assertEqual(ca.sanitized_name(""), "未命名")
        self.assertEqual(ca.sanitized_name("   "), "未命名")

    def test_strip_path_seps(self):
        self.assertEqual(ca.sanitized_name("a/b\\c"), "a_b_c")

    def test_strip_control_chars(self):
        self.assertEqual(ca.sanitized_name("张三\n李四"), "张三_李四")

    def test_collapse_whitespace(self):
        self.assertEqual(ca.sanitized_name("张  三   "), "张 三")

    def test_strip_leading_dot(self):
        # 防隐藏文件
        self.assertEqual(ca.sanitized_name("..hidden"), "hidden")

    def test_truncate_80(self):
        long_name = "x" * 200
        self.assertEqual(len(ca.sanitized_name(long_name)), 80)

    def test_alias_name_basename(self):
        self.assertEqual(ca.alias_name_for("张三", "t_abc"), "张三__t_abc")

    def test_alias_name_requires_tid(self):
        with self.assertRaises(ValueError):
            ca.alias_name_for("张三", "")

    def test_alias_dir_under_by_name(self):
        p = ca.alias_dir_for("张三", "t_abc")
        self.assertEqual(p, cs.candidates_root() / "by_name" / "张三__t_abc")


# ─── rebuild_alias_for ───────────────────────────────────────────────────────

class TestRebuildAlias(unittest.TestCase):

    def setUp(self):
        _fresh_root()

    def test_first_time_create(self):
        _make_real_dir("t_aaa")
        r = ca.rebuild_alias_for("t_aaa", "张三")
        self.assertIsNone(r["error"])
        self.assertTrue(r["created"])
        self.assertFalse(r["already_ok"])

        alias = Path(r["alias_path"])
        self.assertTrue(alias.is_symlink())
        # symlink 解析后等于真目录
        self.assertEqual(alias.resolve(), cs.candidate_dir("t_aaa").resolve())

    def test_idempotent_second_call(self):
        _make_real_dir("t_aaa")
        ca.rebuild_alias_for("t_aaa", "张三")
        # 第二次调：应识别为已正确
        r2 = ca.rebuild_alias_for("t_aaa", "张三")
        self.assertTrue(r2["already_ok"])
        self.assertFalse(r2["created"])
        self.assertEqual(r2["removed_stale"], [])

    def test_rename_replaces_old_alias(self):
        _make_real_dir("t_aaa")
        ca.rebuild_alias_for("t_aaa", "张三")
        old_alias = ca.alias_dir_for("张三", "t_aaa")
        self.assertTrue(old_alias.is_symlink())

        # 改名：旧 alias 被 unlink，新 alias 建出来
        r = ca.rebuild_alias_for("t_aaa", "李四")
        self.assertTrue(r["created"])
        self.assertIn("张三__t_aaa", r["removed_stale"])
        self.assertFalse(old_alias.exists())
        new_alias = ca.alias_dir_for("李四", "t_aaa")
        self.assertTrue(new_alias.is_symlink())
        self.assertEqual(new_alias.resolve(), cs.candidate_dir("t_aaa").resolve())

    def test_target_missing_returns_error(self):
        # 真目录都没建，alias 没意义
        r = ca.rebuild_alias_for("t_nope", "张三")
        self.assertIsNotNone(r["error"])
        self.assertIn("不存在", r["error"])
        self.assertFalse(ca.alias_dir_for("张三", "t_nope").exists())

    def test_dry_run_noop(self):
        _make_real_dir("t_aaa")
        os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = "1"
        try:
            r = ca.rebuild_alias_for("t_aaa", "张三")
            self.assertTrue(r["dry_run"])
            self.assertFalse(ca.alias_dir_for("张三", "t_aaa").exists())
        finally:
            os.environ.pop("RECRUIT_DISABLE_SIDE_EFFECTS", None)

    def test_refuses_to_unlink_real_dir(self):
        """HR 手贱在 by_name/ 下 mkdir 了同名真目录 → 我们不能 unlink。"""
        _make_real_dir("t_aaa")
        ca.by_name_root().mkdir(parents=True, exist_ok=True)
        bogus = ca.by_name_root() / "张三__t_aaa"
        bogus.mkdir()  # 真目录不是 symlink
        try:
            r = ca.rebuild_alias_for("t_aaa", "张三")
            # 真目录还在
            self.assertTrue(bogus.is_dir())
            self.assertFalse(bogus.is_symlink())
            # 没 already_ok，也没 created，error 应该提示
            self.assertFalse(r["created"])
            self.assertIsNotNone(r["error"])
        finally:
            shutil.rmtree(str(bogus), ignore_errors=True)

    def test_empty_name_falls_back_to_placeholder(self):
        _make_real_dir("t_aaa")
        r = ca.rebuild_alias_for("t_aaa", "")
        self.assertIsNone(r["error"])
        self.assertTrue(r["created"])
        self.assertTrue(Path(r["alias_path"]).name.startswith("未命名__"))


# ─── remove_alias_for ────────────────────────────────────────────────────────

class TestRemoveAlias(unittest.TestCase):

    def setUp(self):
        _fresh_root()

    def test_remove_all_aliases_for_tid(self):
        _make_real_dir("t_aaa")
        ca.rebuild_alias_for("t_aaa", "张三")
        # 模拟历史残留：旧名 alias 还在
        ca.by_name_root().mkdir(parents=True, exist_ok=True)
        legacy = ca.by_name_root() / "李四__t_aaa"
        os.symlink("../t_aaa", str(legacy))

        r = ca.remove_alias_for("t_aaa")
        names = set(r["removed"])
        self.assertIn("张三__t_aaa", names)
        self.assertIn("李四__t_aaa", names)
        self.assertFalse((ca.by_name_root() / "张三__t_aaa").exists())
        self.assertFalse((ca.by_name_root() / "李四__t_aaa").exists())

    def test_remove_when_by_name_missing(self):
        # 没建过 by_name/ 也不应抛
        r = ca.remove_alias_for("t_aaa")
        self.assertEqual(r["removed"], [])


# ─── rebuild_all_aliases ─────────────────────────────────────────────────────

class TestRebuildAll(unittest.TestCase):

    def setUp(self):
        _fresh_root()

    def test_full_rebuild_and_dangling_cleanup(self):
        _make_real_dir("t_a")
        _make_real_dir("t_b")
        # 先建一个孤儿 alias，rebuild_all 应该清理它
        ca.by_name_root().mkdir(parents=True, exist_ok=True)
        orphan = ca.by_name_root() / "外星人__t_zzz"
        os.symlink("../t_zzz", str(orphan))

        summary = ca.rebuild_all_aliases([("t_a", "张三"), ("t_b", "李四")])
        self.assertEqual(summary["errors"], [])
        # 两条新建
        self.assertEqual(len(summary["built"]), 2)
        # 孤儿被清理
        self.assertIn("外星人__t_zzz", summary["removed_dangling"])
        self.assertFalse(orphan.exists())
        # 正经 alias 都在
        self.assertTrue((ca.by_name_root() / "张三__t_a").is_symlink())
        self.assertTrue((ca.by_name_root() / "李四__t_b").is_symlink())

    def test_full_rebuild_dry_run(self):
        _make_real_dir("t_a")
        os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = "1"
        try:
            summary = ca.rebuild_all_aliases([("t_a", "张三")])
            self.assertTrue(summary["dry_run"])
            self.assertFalse(ca.by_name_root().exists())
        finally:
            os.environ.pop("RECRUIT_DISABLE_SIDE_EFFECTS", None)

    def test_errors_dont_abort_others(self):
        _make_real_dir("t_a")
        # t_missing 真目录不存在 → 该条出 error，但 t_a 仍要成功
        summary = ca.rebuild_all_aliases([
            ("t_a", "张三"),
            ("t_missing", "李四"),
        ])
        self.assertEqual(len(summary["built"]), 1)
        self.assertEqual(len(summary["errors"]), 1)
        self.assertEqual(summary["errors"][0]["talent_id"], "t_missing")


if __name__ == "__main__":
    unittest.main()
