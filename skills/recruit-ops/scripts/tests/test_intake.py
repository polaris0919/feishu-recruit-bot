#!/usr/bin/env python3
"""候选人 CV 导入测试：cmd_ingest_cv。"""
import os
import unittest
import zipfile
from unittest import mock

from tests.helpers import call_main, wipe_state


class TestIngestCv(unittest.TestCase):

    def setUp(self):
        wipe_state()

    def _make_docx(self, path, paragraphs):
        body = "".join(
            "<w:p><w:r><w:t>{}</w:t></w:r></w:p>".format(p) for p in paragraphs
        )
        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>{}</w:body></w:document>".format(body)
        )
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>"
        )
        rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>'
        )
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("[Content_Types].xml", content_types)
            zf.writestr("_rels/.rels", rels)
            zf.writestr("word/document.xml", document_xml)

    # 公开仓 fixtures：所有候选人字段全部用中性占位，避免任何真实 PII 入库。
    _REAL_PDF_TEXT = """候选人C
邮箱: \tcandidate-c@example.com \t电话: \t+86 \t13800000000
求职意向：算法，量化，数据分析
教育背景
示例大学 \t2025.09 \t– \t至今
数学科学学院应用统计硕士
另一所示例大学 \t2019.09 \t- \t2023.06
数学系信息与计算科学专业学士
某全国数学竞赛 \tA \t类一等奖
研究/实习经历
关于 \tA \t股动量策略的风险平价增强研究
感知算法实习生
"""

    def test_ingest_cv_supports_docx_new_candidate_preview(self):
        from intake import cmd_ingest_cv

        docx_path = "/tmp/recruit_test_resume.docx"
        self._make_docx(docx_path, [
            "候选人K",
            "candidate-k@example.com",
            "示例大学",
            "量化研究实习生",
        ])

        with mock.patch.object(cmd_ingest_cv, "_lookup_existing", return_value=None), \
             mock.patch.object(cmd_ingest_cv._parse_mod, "_llm_parse_cv_fields", return_value={
                 "name": "候选人K",
                 "email": "candidate-k@example.com",
                 "education": "博士",
                 "school": "示例大学",
                 "position": "量化研究实习生",
                 "resume_summary": "测试摘要",
             }):
            out, err, rc = call_main("cmd_ingest_cv", [
                "--file-path", docx_path,
                "--filename", "候选人K简历.docx",
            ])

        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("【新候选人 - 待确认】", out)
        self.assertIn("候选人K", out)
        self.assertIn("candidate-k@example.com", out)
        self.assertIn("已读取本地DOCX", err)

    def test_ingest_cv_supports_real_pdf_preview(self):
        from intake import cmd_ingest_cv

        # 公开仓不携带任何真实 PDF；除非用户自己放一个 fixture 才会跑该用例。
        pdf_path = os.environ.get(
            "RECRUIT_TEST_PDF_FIXTURE",
            "<RECRUIT_WORKSPACE>/data/media/inbound/示例候选人简历.pdf",
        )
        if not os.path.isfile(pdf_path):
            self.skipTest(
                "公开仓默认无 PDF fixture；如需运行，把脱敏后的简历 PDF 放到 "
                "`data/media/inbound/示例候选人简历.pdf` 或设 `RECRUIT_TEST_PDF_FIXTURE` 环境变量")

        def _fake_llm(cv_text, filename="", pdf_title="", pdf_author=""):
            self.assertIn("候选人C", cv_text)
            self.assertIn("candidate-c@example.com", cv_text)
            self.assertIn("某全国数学竞赛", cv_text)
            self.assertIn("示例大学", cv_text)
            self.assertTrue(filename.endswith(".pdf"))
            return {
                "name": "候选人C",
                "email": "candidate-c@example.com",
                "phone": "13800000000",
                "wechat": None,
                "position": "股票量化研究员",
                "education": "硕士",
                "school": "示例大学",
                "work_years": 0,
                "source": None,
                "resume_summary": "示例大学应用统计硕士，具备量化研究与感知算法实习经历，获得某全国数学竞赛A类一等奖。",
            }

        with mock.patch.object(cmd_ingest_cv, "_lookup_existing", return_value=None), \
             mock.patch.object(cmd_ingest_cv._parse_mod, "_extract_text_from_pdf", return_value=self._REAL_PDF_TEXT), \
             mock.patch.object(cmd_ingest_cv._parse_mod, "_llm_parse_cv_fields", side_effect=_fake_llm):
            out, err, rc = call_main("cmd_ingest_cv", [
                "--file-path", pdf_path,
                "--filename", os.path.basename(pdf_path),
            ])

        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("【新候选人 - 待确认】", out)
        self.assertIn("候选人C", out)
        self.assertIn("candidate-c@example.com", out)
        self.assertIn("股票量化研究员", out)
        self.assertIn("示例大学", out)
        self.assertIn("已读取本地PDF", err)
        self.assertIn("提取PDF正文", err)


# ════════════════════════════════════════════════════════════════════════════
# v3.5.8: cmd_attach_cv 把传入的 CV 自动搬进 candidates/<tid>/cv/
# ════════════════════════════════════════════════════════════════════════════

class TestAttachCvImportsToCandidateDir(unittest.TestCase):

    def setUp(self):
        wipe_state()
        import tempfile, shutil
        from lib import candidate_storage as _cs
        from tests import helpers
        # 注入独立 data root，避免污染 <RECRUIT_WORKSPACE>/data
        self._tmp_root = tempfile.mkdtemp(prefix="attach_cv_test_")
        self._prev_root = os.environ.get("RECRUIT_DATA_ROOT")
        self._prev_off = os.environ.get("RECRUIT_DISABLE_SIDE_EFFECTS")
        os.environ["RECRUIT_DATA_ROOT"] = self._tmp_root
        os.environ.pop("RECRUIT_DISABLE_SIDE_EFFECTS", None)
        # 准备一个候选人
        self.tid = helpers.new_candidate("张三", "zhang@test.com")
        # 准备一个 OpenClaw inbound 风格的 CV 源文件
        self._src_dir = tempfile.mkdtemp(prefix="cv_src_inbound_")
        self.src_pdf = os.path.join(self._src_dir, "张三-CV.pdf")
        with open(self.src_pdf, "wb") as f:
            f.write(b"%PDF-1.4 fake cv for cmd_attach_cv test")
        self._shutil = shutil
        self._cs = _cs

    def tearDown(self):
        self._shutil.rmtree(self._tmp_root, ignore_errors=True)
        self._shutil.rmtree(self._src_dir, ignore_errors=True)
        if self._prev_root is None:
            os.environ.pop("RECRUIT_DATA_ROOT", None)
        else:
            os.environ["RECRUIT_DATA_ROOT"] = self._prev_root
        if self._prev_off is None:
            os.environ.pop("RECRUIT_DISABLE_SIDE_EFFECTS", None)
        else:
            os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = self._prev_off

    def test_attach_cv_moves_file_into_candidate_dir(self):
        from lib.core_state import load_candidate
        out, err, rc = call_main("cmd_attach_cv", [
            "--talent-id", self.tid,
            "--cv-path", self.src_pdf,
            "--confirm",
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        # 文件已搬到 cv_dir
        new_path = self._cs.cv_dir(self.tid) / "张三-CV.pdf"
        self.assertTrue(new_path.is_file(),
                        "CV 应被搬到 {}".format(new_path))
        self.assertFalse(os.path.exists(self.src_pdf),
                         "默认 mode=move 应删除原文件")
        # 入库 cv_path 是新路径
        cand = load_candidate(self.tid)
        self.assertEqual(cand["cv_path"], str(new_path))
        # echo 含「已自动搬至候选人资料目录」提示
        self.assertIn("候选人资料目录", out)

    def test_attach_cv_copy_mode_via_env(self):
        """RECRUIT_CV_IMPORT_MODE=copy → 原文件保留。"""
        os.environ["RECRUIT_CV_IMPORT_MODE"] = "copy"
        try:
            out, err, rc = call_main("cmd_attach_cv", [
                "--talent-id", self.tid,
                "--cv-path", self.src_pdf,
                "--confirm",
            ])
        finally:
            os.environ.pop("RECRUIT_CV_IMPORT_MODE", None)
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertTrue(os.path.exists(self.src_pdf),
                        "copy 模式应保留原文件")
        self.assertTrue((self._cs.cv_dir(self.tid) / "张三-CV.pdf").is_file())

    def test_attach_cv_idempotent_when_already_in_dir(self):
        """src 已经在 cv_dir 下 → no-op（不抛异常，cv_path 仍指向同一文件）。"""
        # 先搬一次
        call_main("cmd_attach_cv", [
            "--talent-id", self.tid,
            "--cv-path", self.src_pdf,
            "--confirm",
        ])
        new_path = self._cs.cv_dir(self.tid) / "张三-CV.pdf"
        self.assertTrue(new_path.is_file())
        # 再用新路径调一次（caller 提供的路径已经在 cv_dir 下）
        out, err, rc = call_main("cmd_attach_cv", [
            "--talent-id", self.tid,
            "--cv-path", str(new_path),
            "--confirm",
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertTrue(new_path.is_file())

    def test_attach_cv_missing_source_returns_error(self):
        out, _, rc = call_main("cmd_attach_cv", [
            "--talent-id", self.tid,
            "--cv-path", "/tmp/this_does_not_exist.pdf",
            "--confirm",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("写入失败", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
