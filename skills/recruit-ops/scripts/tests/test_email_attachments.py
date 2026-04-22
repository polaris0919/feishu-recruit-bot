#!/usr/bin/env python3
"""tests/test_email_attachments.py —— v3.5.6
lib.email_attachments 的纯单元测试（不依赖 IMAP / DB）。

覆盖：
  - _safe_name：路径穿越 / 控制字符 / Windows 禁用字符 / 中文 / 长名截断
  - extract_metadata：能识别 attachment / inline 附件，跳过正文
  - extract_and_save：
      * 正常落盘到指定目录，文件权限 0o600，目录 0o700
      * 同名附件不覆盖（自动加 (2) 后缀）
      * winmail.dat / 空 / 超大 → saved=false 元数据
      * MAX_FILES_PER_EMAIL → 截断 + 多写一行 saved=false 标记
      * RECRUIT_DISABLE_SIDE_EFFECTS=1 时**绝不写盘**（dry-run）
      * mkdir 失败时所有附件 saved=false
"""
from __future__ import print_function

import email as email_lib
import os
import shutil
import tempfile
import unittest
from email.message import EmailMessage

import tests.helpers  # noqa: F401  side-effect: 装好 sys.modules

from lib import email_attachments


def _build_msg_with_attachments(parts, body_text="hello world"):
    """parts: list of (filename, mime, payload_bytes, disposition)
    disposition ∈ {'attachment', 'inline', None(=纯附件: get_filename only)}"""
    msg = EmailMessage()
    msg["From"] = "candidate@example.com"
    msg["To"] = "recruiter@example.com"
    msg["Subject"] = "应聘材料"
    msg.set_content(body_text)
    for filename, mime, payload, disposition in parts:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(payload, maintype=maintype, subtype=subtype,
                           filename=filename, disposition=disposition)
    raw = msg.as_bytes()
    return email_lib.message_from_bytes(raw)


class TestSafeName(unittest.TestCase):
    def test_basic_passthrough(self):
        self.assertEqual(email_attachments._safe_name("简历.pdf"), "简历.pdf")

    def test_strips_path_traversal(self):
        # Linux 上 basename 会把 / 切掉
        self.assertEqual(
            email_attachments._safe_name("../../etc/passwd"), "passwd")
        # Windows 风格反斜杠：basename 不识别，但 .replace 会把 \ → _，
        # 关键不变量是结果不包含路径分隔符、不以 .. 开头
        out = email_attachments._safe_name("..\\..\\windows\\sys.ini")
        self.assertNotIn("/", out)
        self.assertNotIn("\\", out)
        self.assertFalse(out.startswith(".."))
        self.assertTrue(out.endswith("sys.ini"))

    def test_strips_control_chars(self):
        out = email_attachments._safe_name("foo\x00\x01.pdf")
        self.assertEqual(out, "foo__.pdf")

    def test_strips_windows_unsafe(self):
        out = email_attachments._safe_name("a<b>c:d|e?f.pdf")
        self.assertNotRegex(out, r"[<>:|?]")
        self.assertTrue(out.endswith(".pdf"))

    def test_empty_to_unnamed(self):
        self.assertEqual(email_attachments._safe_name(""), "unnamed.bin")
        self.assertEqual(email_attachments._safe_name("..."), "unnamed.bin")

    def test_truncates_long_name(self):
        long_stem = "x" * 500
        out = email_attachments._safe_name(long_stem + ".pdf")
        self.assertLessEqual(len(out.encode("utf-8")), 200)
        self.assertTrue(out.endswith(".pdf"))


class TestExtractMetadata(unittest.TestCase):
    def test_no_attachments(self):
        msg = _build_msg_with_attachments([])
        self.assertEqual(email_attachments.extract_metadata(msg), [])

    def test_one_pdf_attachment(self):
        msg = _build_msg_with_attachments([
            ("简历.pdf", "application/pdf", b"%PDF-1.4 fake", "attachment"),
        ])
        meta = email_attachments.extract_metadata(msg)
        self.assertEqual(len(meta), 1)
        self.assertEqual(meta[0]["name"], "简历.pdf")
        self.assertEqual(meta[0]["mime"], "application/pdf")
        self.assertEqual(meta[0]["size"], len(b"%PDF-1.4 fake"))
        self.assertFalse(meta[0]["saved"])
        self.assertIsNone(meta[0]["path"])

    def test_inline_image_counted(self):
        msg = _build_msg_with_attachments([
            ("logo.png", "image/png", b"\x89PNGfake", "inline"),
        ])
        meta = email_attachments.extract_metadata(msg)
        self.assertEqual(len(meta), 1)
        self.assertEqual(meta[0]["mime"], "image/png")


class TestExtractAndSave(unittest.TestCase):
    """v3.5.8：落盘路径走 lib.candidate_storage，按 context 分流。"""

    def setUp(self):
        # 用 RECRUIT_DATA_ROOT 隔离测试，不再 monkey-patch 模块级常量
        self.tmp_root = tempfile.mkdtemp(prefix="ea_test_")
        self._orig_root = os.environ.get("RECRUIT_DATA_ROOT")
        os.environ["RECRUIT_DATA_ROOT"] = self.tmp_root
        # 确保不是 dry-run（helpers.py 默认 RECRUIT_DISABLE_SIDE_EFFECTS=1）
        self._orig_env = os.environ.pop(
            "RECRUIT_DISABLE_SIDE_EFFECTS", None)

    def tearDown(self):
        if self._orig_root is None:
            os.environ.pop("RECRUIT_DATA_ROOT", None)
        else:
            os.environ["RECRUIT_DATA_ROOT"] = self._orig_root
        if self._orig_env is not None:
            os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = self._orig_env
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_basic_save_non_exam_routes_to_email(self):
        msg = _build_msg_with_attachments([
            ("简历.pdf", "application/pdf", b"%PDF-1.4 fake content", "attachment"),
        ])
        meta = email_attachments.extract_and_save(
            msg, talent_id="t_unit1", email_id="eid-unit-1", context="intake")
        self.assertEqual(len(meta), 1)
        self.assertTrue(meta[0]["saved"])
        self.assertEqual(meta[0]["name"], "简历.pdf")
        # v3.5.8 新路径：candidates/<tid>/email/em_<eid>/<file>，不再有 t_t_ bug
        self.assertEqual(
            meta[0]["path"],
            "candidates/t_unit1/email/em_eid-unit-1/简历.pdf")

        full = os.path.join(self.tmp_root, meta[0]["path"])
        self.assertTrue(os.path.isfile(full))
        with open(full, "rb") as f:
            self.assertEqual(f.read(), b"%PDF-1.4 fake content")

        # 文件 0o600，目录 0o700
        st = os.stat(full)
        self.assertEqual(st.st_mode & 0o777, 0o600)
        parent_st = os.stat(os.path.dirname(full))
        self.assertEqual(parent_st.st_mode & 0o777, 0o700)

    def test_exam_context_routes_to_exam_answer(self):
        """笔试附件应落到 exam_answer/，不再混进通用 email/。"""
        msg = _build_msg_with_attachments([
            ("笔试题答案.zip", "application/zip", b"PKzipfake", "attachment"),
        ])
        meta = email_attachments.extract_and_save(
            msg, talent_id="t_exam", email_id="eid-exam-1", context="exam")
        self.assertTrue(meta[0]["saved"])
        self.assertEqual(
            meta[0]["path"],
            "candidates/t_exam/exam_answer/em_eid-exam-1/笔试题答案.zip")
        full = os.path.join(self.tmp_root, meta[0]["path"])
        self.assertTrue(os.path.isfile(full))

    def test_none_context_routes_to_email(self):
        """没传 context（向后兼容）默认走 email/。"""
        msg = _build_msg_with_attachments([
            ("file.bin", "application/octet-stream", b"DATA", "attachment"),
        ])
        meta = email_attachments.extract_and_save(
            msg, talent_id="t_none", email_id="eid-none-1")
        self.assertTrue(meta[0]["saved"])
        self.assertIn("/email/em_eid-none-1/", meta[0]["path"])
        self.assertNotIn("/exam_answer/", meta[0]["path"])

    def test_duplicate_name_gets_suffix(self):
        msg = _build_msg_with_attachments([
            ("简历.pdf", "application/pdf", b"FIRST", "attachment"),
            ("简历.pdf", "application/pdf", b"SECOND", "attachment"),
        ])
        meta = email_attachments.extract_and_save(
            msg, talent_id="t_dup", email_id="eid-dup-1", context="intake")
        self.assertEqual(len(meta), 2)
        self.assertTrue(all(m["saved"] for m in meta))
        names = sorted(m["name"] for m in meta)
        self.assertEqual(names[0], "简历(2).pdf")
        self.assertEqual(names[1], "简历.pdf")

    def test_winmail_dat_skipped(self):
        msg = _build_msg_with_attachments([
            ("winmail.dat", "application/ms-tnef",
             b"some-tnef-bytes", "attachment"),
        ])
        meta = email_attachments.extract_and_save(
            msg, talent_id="t_w", email_id="eid-w-1", context="intake")
        self.assertEqual(len(meta), 1)
        self.assertFalse(meta[0]["saved"])
        self.assertIn("blacklist", meta[0]["note"])

    def test_oversize_skipped(self):
        orig = email_attachments.MAX_FILE_BYTES
        email_attachments.MAX_FILE_BYTES = 100
        try:
            msg = _build_msg_with_attachments([
                ("big.pdf", "application/pdf", b"x" * 500, "attachment"),
            ])
            meta = email_attachments.extract_and_save(
                msg, talent_id="t_big", email_id="eid-big-1", context="intake")
            self.assertEqual(len(meta), 1)
            self.assertFalse(meta[0]["saved"])
            self.assertIn("oversize", meta[0]["note"])
        finally:
            email_attachments.MAX_FILE_BYTES = orig

    def test_max_files_per_email(self):
        orig = email_attachments.MAX_FILES_PER_EMAIL
        email_attachments.MAX_FILES_PER_EMAIL = 2
        try:
            msg = _build_msg_with_attachments([
                ("a.txt", "text/plain", b"AAA", "attachment"),
                ("b.txt", "text/plain", b"BBB", "attachment"),
                ("c.txt", "text/plain", b"CCC", "attachment"),
            ])
            meta = email_attachments.extract_and_save(
                msg, talent_id="t_many", email_id="eid-many-1", context="intake")
            saved = [m for m in meta if m["saved"]]
            skipped = [m for m in meta if not m["saved"]]
            self.assertEqual(len(saved), 2)
            self.assertEqual(len(skipped), 1)
            self.assertIn("MAX_FILES_PER_EMAIL", skipped[0]["note"])
        finally:
            email_attachments.MAX_FILES_PER_EMAIL = orig

    def test_dry_run_writes_nothing(self):
        os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = "1"
        msg = _build_msg_with_attachments([
            ("简历.pdf", "application/pdf", b"PDFFAKE", "attachment"),
        ])
        meta = email_attachments.extract_and_save(
            msg, talent_id="t_dry", email_id="eid-dry-1", context="exam")
        self.assertEqual(len(meta), 1)
        self.assertFalse(meta[0]["saved"])
        self.assertIn("dry-run", meta[0]["note"])
        # path 字段还要算对，方便 echo / 审计
        self.assertEqual(
            meta[0]["path"],
            "candidates/t_dry/exam_answer/em_eid-dry-1/简历.pdf")
        # 关键：不能创建任何目录
        target_dir = os.path.join(
            self.tmp_root, "candidates", "t_dry", "exam_answer")
        self.assertFalse(
            os.path.exists(target_dir),
            "dry-run 不能在磁盘上留任何 trace, but {} exists".format(target_dir))


class TestExtractAndSaveValidation(unittest.TestCase):
    def test_missing_talent_id_raises(self):
        msg = _build_msg_with_attachments([])
        with self.assertRaises(ValueError):
            email_attachments.extract_and_save(
                msg, talent_id="", email_id="eid", context="intake")

    def test_missing_email_id_raises(self):
        msg = _build_msg_with_attachments([])
        with self.assertRaises(ValueError):
            email_attachments.extract_and_save(
                msg, talent_id="t_x", email_id="", context="intake")


if __name__ == "__main__":
    unittest.main(verbosity=2)
