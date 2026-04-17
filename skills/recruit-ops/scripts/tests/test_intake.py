#!/usr/bin/env python3
"""候选人 CV 导入测试：cmd_ingest_cv。"""
import os
import unittest
import zipfile
from unittest import mock

from tests.helpers import call_main, wipe_state


class TestIngestCv(unittest.TestCase):
    _SAMPLE_PDF_PATH = "/tmp/recruit_test_resume.pdf"

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

    _SAMPLE_PDF_TEXT = """测试同学
邮箱: \tsample.quant@example.com \t电话: \t+86 \t13900000000
求职意向：算法，量化，数据分析
教育背景
示例大学 \t2025.09 \t– \t至今
数学科学学院应用统计硕士
示例大学 \t2019.09 \t- \t2023.06
数学系信息与计算科学专业学士
全国大学生数学建模竞赛 \tA \t类一等奖
研究/实习经历
关于 \tA \t股动量策略的风险平价增强研究
感知算法实习生
"""

    def test_ingest_cv_supports_docx_new_candidate_preview(self):
        import cmd_ingest_cv

        docx_path = "/tmp/recruit_test_resume.docx"
        self._make_docx(docx_path, [
            "测试候选人",
            "candidate@example.com",
            "示例大学",
            "量化研究实习生",
        ])

        with mock.patch.object(cmd_ingest_cv, "_lookup_existing", return_value=None), \
             mock.patch.object(cmd_ingest_cv._parse_mod, "_llm_parse_cv_fields", return_value={
                 "name": "测试候选人",
                 "email": "candidate@example.com",
                 "education": "博士",
                 "school": "示例大学",
                 "position": "量化研究实习生",
                 "resume_summary": "测试摘要",
             }):
            out, err, rc = call_main("cmd_ingest_cv", [
                "--file-path", docx_path,
                "--filename", "测试候选人简历.docx",
            ])

        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("【新候选人 - 待确认】", out)
        self.assertIn("测试候选人", out)
        self.assertIn("candidate@example.com", out)
        self.assertIn("已读取本地DOCX", err)

    def test_ingest_cv_supports_real_pdf_preview(self):
        import cmd_ingest_cv

        pdf_path = self._SAMPLE_PDF_PATH
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n% public sample fixture\n")
        self.assertTrue(os.path.isfile(pdf_path), "测试 PDF 不存在")

        def _fake_llm(cv_text, filename="", pdf_title="", pdf_author=""):
            self.assertIn("测试同学", cv_text)
            self.assertIn("sample.quant@example.com", cv_text)
            self.assertIn("全国大学生数学建模竞赛", cv_text)
            self.assertIn("示例大学", cv_text)
            self.assertTrue(filename.endswith(".pdf"))
            return {
                "name": "测试同学",
                "email": "sample.quant@example.com",
                "phone": "13900000000",
                "wechat": None,
                "position": "股票量化研究员",
                "education": "硕士",
                "school": "示例大学",
                "work_years": 0,
                "source": None,
                "resume_summary": "示例大学应用统计硕士，具备量化研究与感知算法实习经历，获得全国大学生数学建模竞赛A类一等奖。",
            }

        with mock.patch.object(cmd_ingest_cv, "_lookup_existing", return_value=None), \
             mock.patch.object(cmd_ingest_cv._parse_mod, "_extract_text_from_pdf", return_value=self._SAMPLE_PDF_TEXT), \
             mock.patch.object(cmd_ingest_cv._parse_mod, "_llm_parse_cv_fields", side_effect=_fake_llm):
            out, err, rc = call_main("cmd_ingest_cv", [
                "--file-path", pdf_path,
                "--filename", "sample_resume.pdf",
            ])

        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("【新候选人 - 待确认】", out)
        self.assertIn("测试同学", out)
        self.assertIn("sample.quant@example.com", out)
        self.assertIn("股票量化研究员", out)
        self.assertIn("示例大学", out)
        self.assertIn("已读取本地PDF", err)
        self.assertIn("提取PDF正文", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
