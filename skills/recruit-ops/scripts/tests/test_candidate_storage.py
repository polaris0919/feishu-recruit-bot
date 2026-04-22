#!/usr/bin/env python3
"""tests/test_candidate_storage.py —— v3.5.8 候选人统一目录单测。

【覆盖】
  1. 路径计算：candidate_dir / cv_dir / exam_answer_dir / email_dir
  2. attachment_dir 路由：context='exam' → exam_answer/，其他 → email/
  3. ensure_candidate_dirs：幂等 / 失败兜成 error 字段（不抛）/ dry-run 不动盘
  4. import_cv：move / copy / 重名加 (2) / 已在目标目录 no-op / 幂等同文件
  5. _validate_talent_id：拒空 / 路径分隔符 / .开头
  6. RECRUIT_DATA_ROOT env 隔离：测试不污染 <RECRUIT_WORKSPACE>/data
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# 先注入 RECRUIT_DATA_ROOT，再 import 模块（其实 candidate_storage 每次都重读
# env，但这样写更明确）
_TMP_ROOT = None


def setUpModule():
    global _TMP_ROOT
    _TMP_ROOT = tempfile.mkdtemp(prefix="cs_test_root_")
    os.environ["RECRUIT_DATA_ROOT"] = _TMP_ROOT
    # 确保 side_effects_disabled() 关闭，否则 ensure_candidate_dirs 全 dry-run
    os.environ.pop("RECRUIT_DISABLE_SIDE_EFFECTS", None)


def tearDownModule():
    global _TMP_ROOT
    if _TMP_ROOT and os.path.isdir(_TMP_ROOT):
        shutil.rmtree(_TMP_ROOT, ignore_errors=True)
    os.environ.pop("RECRUIT_DATA_ROOT", None)


from lib import candidate_storage as cs  # noqa: E402


class TestPathCalc(unittest.TestCase):
    """纯函数路径计算：不动盘也要正确。"""

    def test_data_root_reads_env(self):
        self.assertEqual(str(cs.data_root()), _TMP_ROOT)

    def test_candidates_root_under_data_root(self):
        self.assertEqual(cs.candidates_root(),
                         Path(_TMP_ROOT) / "candidates")

    def test_candidate_dir_uses_talent_id_as_is(self):
        """直接用 talent_id 当目录名（不再叠 t_ 前缀，修 v3.5.6 bug）。"""
        self.assertEqual(cs.candidate_dir("t_abc"),
                         Path(_TMP_ROOT) / "candidates" / "t_abc")

    def test_three_subdirs(self):
        for sub in ("cv", "exam_answer", "email"):
            getter = getattr(cs, "{}_dir".format(sub) if sub != "cv" else "cv_dir")
            self.assertEqual(getter("t_abc"),
                             cs.candidate_dir("t_abc") / sub)

    def test_list_known_subdirs(self):
        self.assertEqual(cs.list_known_subdirs(), ["cv", "exam_answer", "email"])


class TestAttachmentDirRouting(unittest.TestCase):
    """email_attachments.extract_and_save 的核心：context 决定走哪个子树。"""

    def test_exam_context_routes_to_exam_answer(self):
        p = cs.attachment_dir("t_abc", context="exam", email_id="em_xyz")
        self.assertEqual(p,
                         cs.exam_answer_dir("t_abc") / "em_em_xyz")

    def test_exam_context_case_insensitive(self):
        # context 大小写 / 前后空格不应影响路由（HR / OpenClaw 偶尔写 'EXAM'）
        for ctx in ("EXAM", " exam ", "Exam"):
            p = cs.attachment_dir("t_abc", context=ctx, email_id="em_y")
            self.assertEqual(p, cs.exam_answer_dir("t_abc") / "em_em_y")

    def test_non_exam_context_routes_to_email(self):
        for ctx in ("intake", "followup", "round1", "post_offer", None, "", "  "):
            p = cs.attachment_dir("t_abc", context=ctx, email_id="em_y")
            self.assertEqual(p, cs.email_dir("t_abc") / "em_em_y",
                             "context={!r} should go to email/".format(ctx))

    def test_attachment_dir_rejects_empty_email_id(self):
        with self.assertRaises(ValueError):
            cs.attachment_dir("t_abc", context="exam", email_id="")
        with self.assertRaises(ValueError):
            cs.attachment_dir("t_abc", context="exam", email_id=None)


class TestEnsureCandidateDirs(unittest.TestCase):

    def setUp(self):
        # 每个 case 用独立 talent_id，避免相互污染
        self.tid = "t_ensure_{}".format(self._testMethodName[-8:])
        # 清掉可能残留
        cdir = cs.candidate_dir(self.tid)
        if cdir.exists():
            shutil.rmtree(cdir, ignore_errors=True)

    def test_first_call_creates_all_three(self):
        result = cs.ensure_candidate_dirs(self.tid)
        self.assertIsNone(result["error"])
        self.assertFalse(result["dry_run"])
        self.assertEqual(sorted(result["created"]),
                         ["cv", "email", "exam_answer"])
        self.assertEqual(result["already_existed"], [])
        # 实际目录就位
        for sub in ("cv", "exam_answer", "email"):
            self.assertTrue((cs.candidate_dir(self.tid) / sub).is_dir())

    def test_idempotent_second_call_no_op(self):
        cs.ensure_candidate_dirs(self.tid)
        result = cs.ensure_candidate_dirs(self.tid)
        self.assertIsNone(result["error"])
        self.assertEqual(result["created"], [])
        self.assertEqual(sorted(result["already_existed"]),
                         ["cv", "email", "exam_answer"])

    def test_partial_existing_only_creates_missing(self):
        """先建 cv/，再调 ensure → 应只新建 exam_answer / email。"""
        (cs.candidate_dir(self.tid) / "cv").mkdir(parents=True, mode=0o700)
        result = cs.ensure_candidate_dirs(self.tid)
        self.assertIsNone(result["error"])
        self.assertEqual(sorted(result["created"]), ["email", "exam_answer"])
        self.assertEqual(result["already_existed"], ["cv"])

    def test_dir_mode_0700(self):
        cs.ensure_candidate_dirs(self.tid)
        for sub in cs.list_known_subdirs():
            mode = (cs.candidate_dir(self.tid) / sub).stat().st_mode & 0o777
            self.assertEqual(mode, 0o700,
                             "目录 {} 权限应为 0700，实际 {:o}".format(sub, mode))

    def test_dry_run_does_not_touch_disk(self):
        os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = "1"
        try:
            result = cs.ensure_candidate_dirs(self.tid)
        finally:
            os.environ.pop("RECRUIT_DISABLE_SIDE_EFFECTS", None)
        self.assertTrue(result["dry_run"])
        self.assertFalse(cs.candidate_dir(self.tid).exists(),
                         "dry-run 不应创建任何目录")

    def test_mkdir_failure_returns_error_does_not_raise(self):
        """mkdir 失败（mock OSError）→ ensure 不抛，error 字段非空。"""
        with mock.patch("pathlib.Path.mkdir",
                        side_effect=OSError(28, "No space left on device")):
            result = cs.ensure_candidate_dirs(self.tid)
        self.assertIsNotNone(result["error"])
        self.assertIn("ENOSPC", result["error"])

    def test_validate_talent_id_rejects_path_traversal(self):
        for bad in ("", "   ", "../etc/passwd", "t/abc", "t\\abc", ".hidden"):
            with self.assertRaises(ValueError, msg="should reject {!r}".format(bad)):
                cs.ensure_candidate_dirs(bad)


class TestImportCv(unittest.TestCase):

    def setUp(self):
        self.tid = "t_cv_{}".format(self._testMethodName[-12:])
        # 清掉残留
        cdir = cs.candidate_dir(self.tid)
        if cdir.exists():
            shutil.rmtree(cdir, ignore_errors=True)
        # 准备一个 src 目录（不在 candidates 下，模拟 OpenClaw inbound）
        self.src_dir = Path(tempfile.mkdtemp(prefix="cv_src_"))
        self.src = self.src_dir / "张三-CV.pdf"
        self.src.write_bytes(b"%PDF-1.4 fake cv content for test")

    def tearDown(self):
        shutil.rmtree(self.src_dir, ignore_errors=True)
        cdir = cs.candidate_dir(self.tid)
        if cdir.exists():
            shutil.rmtree(cdir, ignore_errors=True)

    def test_move_relocates_file_and_removes_src(self):
        new_path = cs.import_cv(self.tid, str(self.src), mode="move")
        self.assertTrue(new_path.is_file())
        self.assertEqual(new_path.parent, cs.cv_dir(self.tid))
        self.assertFalse(self.src.exists(), "move 模式应删除原文件")
        # 文件权限应被收紧到 0600
        mode = new_path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_copy_keeps_src(self):
        new_path = cs.import_cv(self.tid, str(self.src), mode="copy")
        self.assertTrue(new_path.is_file())
        self.assertTrue(self.src.exists(), "copy 模式应保留原文件")

    def test_already_in_target_dir_is_noop(self):
        """src 已经在 cv_dir 下 → 直接返回 src，不动文件。"""
        cs.ensure_candidate_dirs(self.tid)
        in_place = cs.cv_dir(self.tid) / "in_place.pdf"
        in_place.write_bytes(b"already here")
        result = cs.import_cv(self.tid, str(in_place), mode="move")
        self.assertEqual(result, in_place)
        self.assertTrue(in_place.exists())

    def test_collision_appends_suffix(self):
        """目标目录已有同名 + 内容不同 → 自动加 (2) 后缀。"""
        cs.ensure_candidate_dirs(self.tid)
        existing = cs.cv_dir(self.tid) / self.src.name
        existing.write_bytes(b"different content")
        new_path = cs.import_cv(self.tid, str(self.src), mode="move")
        self.assertNotEqual(new_path, existing)
        self.assertIn("(2)", new_path.name)
        self.assertTrue(existing.exists())  # 老文件没被覆盖

    def test_idempotent_when_same_size_and_mtime(self):
        """如果目标目录已有同名 + 同 size + 同 mtime → 视为重复跑，不动。"""
        cs.ensure_candidate_dirs(self.tid)
        # 先 import 一次
        first = cs.import_cv(self.tid, str(self.src), mode="copy")
        # 再 import 一次同文件
        second = cs.import_cv(self.tid, str(self.src), mode="copy")
        # 第二次应走幂等路径，返回 src（caller 当 no-op）
        self.assertEqual(second, Path(str(self.src)).expanduser().resolve())
        # cv 目录里应该还是只有一个文件，没有 (2)
        cv_files = list(cs.cv_dir(self.tid).glob("*.pdf"))
        self.assertEqual(len(cv_files), 1)
        self.assertEqual(cv_files[0], first)

    def test_missing_src_raises(self):
        with self.assertRaises(FileNotFoundError):
            cs.import_cv(self.tid, "/tmp/this_does_not_exist_xyz.pdf")

    def test_invalid_mode_raises(self):
        with self.assertRaises(ValueError):
            cs.import_cv(self.tid, str(self.src), mode="symlink")

    def test_dry_run_returns_target_path_without_writing(self):
        os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = "1"
        try:
            new_path = cs.import_cv(self.tid, str(self.src), mode="move")
        finally:
            os.environ.pop("RECRUIT_DISABLE_SIDE_EFFECTS", None)
        # 路径算对，但盘上啥都没动
        self.assertEqual(new_path, cs.cv_dir(self.tid) / self.src.name)
        self.assertTrue(self.src.exists())  # 没被搬走
        self.assertFalse(new_path.exists())  # 也没创建

    def test_feishu_doc_prefix_is_stripped_on_import(self):
        """飞书 Gateway 的 doc_<hex>_ 前缀在落盘时应被剥掉（v3.5.10）。"""
        # 模拟飞书拖进来的源文件
        src = self.src_dir / "doc_0123456789ab_候选人BCV.pdf"
        src.write_bytes(b"%PDF-1.4 feishu attachment")
        new_path = cs.import_cv(self.tid, str(src), mode="move")
        # 目标文件名应不带 doc_ 前缀
        self.assertEqual(new_path.name, "候选人BCV.pdf")
        self.assertTrue(new_path.is_file())
        self.assertFalse(src.exists())

    def test_non_feishu_filename_kept_as_is(self):
        """普通文件名不应被误剥（doc_ 但 hex 段太短或不是 hex）。"""
        for name in ("doc_short_x.pdf", "doc_中文_x.pdf", "regular.pdf",
                     "DOC_abcdef12_x.pdf"):  # 大写不剥
            src = self.src_dir / name
            src.write_bytes(b"x")
            tid_local = "t_keep_{}".format(abs(hash(name)) % 10000)
            try:
                new_path = cs.import_cv(tid_local, str(src), mode="copy")
                self.assertEqual(new_path.name, name,
                                 "{!r} 不应被改名".format(name))
            finally:
                src.unlink(missing_ok=True)
                shutil.rmtree(cs.candidate_dir(tid_local), ignore_errors=True)

    def test_dry_run_with_feishu_prefix_returns_clean_target(self):
        """dry-run 也应把目标路径展示为剥掉前缀后的样子，便于 chain echo。"""
        src = self.src_dir / "doc_abcdef123456_X.pdf"
        src.write_bytes(b"y")
        os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = "1"
        try:
            new_path = cs.import_cv(self.tid, str(src), mode="copy")
        finally:
            os.environ.pop("RECRUIT_DISABLE_SIDE_EFFECTS", None)
        self.assertEqual(new_path.name, "X.pdf")


class TestStripFeishuPrefix(unittest.TestCase):
    """覆盖 strip_feishu_prefix 的边界情况，是 import_cv / normalize CLI 的核心。"""

    def test_strips_standard_12hex_prefix(self):
        self.assertEqual(
            cs.strip_feishu_prefix("doc_0123456789ab_张三.pdf"),
            "张三.pdf")

    def test_strips_8_to_32_hex(self):
        for hex_id in ("a" * 8, "a" * 16, "a" * 32, "0123456789abcdef"):
            name = "doc_{}_x.pdf".format(hex_id)
            self.assertEqual(cs.strip_feishu_prefix(name), "x.pdf",
                             "应剥 {!r}".format(name))

    def test_does_not_strip_too_short_or_non_hex(self):
        for name in ("doc_short_x.pdf",       # < 8 char
                     "doc_zzzzzzzz_x.pdf",    # 非 hex
                     "doc_中文hash_x.pdf"):  # 中文
            self.assertEqual(cs.strip_feishu_prefix(name), name,
                             "不应剥 {!r}".format(name))

    def test_does_not_strip_uppercase(self):
        # 飞书 Gateway 一直是小写 hex；大写时保留以防误伤
        self.assertEqual(
            cs.strip_feishu_prefix("DOC_ABCDEF12_x.pdf"),
            "DOC_ABCDEF12_x.pdf")

    def test_handles_empty_and_none_safely(self):
        self.assertEqual(cs.strip_feishu_prefix(""), "")
        self.assertIsNone(cs.strip_feishu_prefix(None))

    def test_strips_only_first_occurrence(self):
        """只剥最前面的一段，文件名中间的 doc_xxx_ 不动。"""
        self.assertEqual(
            cs.strip_feishu_prefix("doc_abcdef12_doc_other_x.pdf"),
            "doc_other_x.pdf")

    def test_idempotent(self):
        clean = "张三 简历.pdf"
        self.assertEqual(cs.strip_feishu_prefix(clean), clean)
        self.assertEqual(
            cs.strip_feishu_prefix(cs.strip_feishu_prefix("doc_abcdef12_" + clean)),
            clean)


if __name__ == "__main__":
    unittest.main()
