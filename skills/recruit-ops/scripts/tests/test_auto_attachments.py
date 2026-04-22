#!/usr/bin/env python3
"""tests/test_auto_attachments.py —— v3.5.10 模板默认附件 + cmd_send 自动附加。

【覆盖】
  1. auto_attachments_for(template_name)：
     - 注册了的模板 → 返回绝对路径列表
     - 未注册的模板 → 返回 []
     - 注册了但文件缺失 → 抛 RuntimeError（fail-fast）
  2. cmd_send --template onboarding_offer dry-run：
     - 自动追加注册附件
     - JSON 输出 attachments[] 标 auto=true
     - --attach 手传同一个文件不会重复
  3. cmd_send --template round1_invite dry-run：
     - 没注册 → 无自动附件，不报错
  4. 真实仓库里 onboarding_offer 注册的两个文件确实存在（防止有人删了）
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

from email_templates import auto_attachments as aa


REAL_DATA_ROOT = "<RECRUIT_WORKSPACE>/data"


class TestAutoAttachmentsRegistry(unittest.TestCase):

    def test_registered_templates_listed(self):
        # 注册表至少包含 onboarding_offer，未来加 entry 不应该意外删它
        regs = aa.list_registered_templates()
        self.assertIn("onboarding_offer", regs)

    def test_unregistered_returns_empty(self):
        self.assertEqual(aa.auto_attachments_for("round1_invite"), [])
        self.assertEqual(aa.auto_attachments_for("nonexistent_template"), [])

    def test_onboarding_offer_real_files_exist(self):
        """v3.5.10 上线必备：合同 + 登记表 docx 真的躺在 data/onoffer_data/。
        任何人删 / 改名这两个文件都会让该测试红，强制走 PR review。"""
        for rel in [
            "onoffer_data/模板-示例科技实习协议-2026年4月版.docx",
            "onoffer_data/示例科技-实习生入职信息登记表-2026年版.docx",
        ]:
            p = Path(REAL_DATA_ROOT) / rel
            self.assertTrue(p.is_file(), "缺失默认附件: {}".format(p))

    def test_missing_file_raises_runtime_error(self):
        """注册了但文件没了 → fail-fast，不能静默发漏附件。"""
        with tempfile.TemporaryDirectory(prefix="aa_test_") as tmp:
            os.environ["RECRUIT_DATA_ROOT"] = tmp
            try:
                with self.assertRaises(RuntimeError) as ctx:
                    aa.auto_attachments_for("onboarding_offer")
                self.assertIn("默认附件文件缺失", str(ctx.exception))
            finally:
                os.environ.pop("RECRUIT_DATA_ROOT", None)

    def test_returns_absolute_paths(self):
        # 真实仓库下应该返回 2 个绝对路径
        paths = aa.auto_attachments_for("onboarding_offer")
        self.assertEqual(len(paths), 2)
        for p in paths:
            self.assertTrue(p.is_absolute())
            self.assertTrue(p.is_file())


class TestCmdSendAutoAttach(unittest.TestCase):
    """端到端：cmd_send dry-run 应自动追加 onboarding_offer 默认附件。"""

    def setUp(self):
        # 虽然 dry-run 不真发邮件，但 _resolve_recipient 会查 DB；
        # mock 掉避免对真库的依赖
        from outbound import cmd_send as cs
        self.cs = cs

        self._patch_resolve = mock.patch.object(
            cs, "_resolve_recipient",
            return_value=("offer_test@example.com", "李四", "POST_OFFER_FOLLOWUP"),
        )
        self._patch_resolve.start()

    def tearDown(self):
        self._patch_resolve.stop()

    def _run(self, argv):
        """跑 cmd_send.main 并捕获 stdout / stderr / 返回码。

        UserInputError 在生产里由 cli_wrapper.run_with_self_verify 兜成 rc=2；
        测试里直接 main()，所以这里手动转。"""
        from lib.cli_wrapper import UserInputError
        out = StringIO()
        err = StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            try:
                rc = self.cs.main(argv)
            except UserInputError as e:
                err.write(str(e))
                rc = 2
        return rc, out.getvalue(), err.getvalue()

    _BASE_OFFER_VARS = [
        "position_title=量化研究员",
        "interview_feedback=表现不错",
        "daily_rate=350",
        "onboard_date=2026-05-06",
        "location=上海·南京西路",
        "evaluation_criteria=试用期 1 个月",
    ]

    def test_onboarding_offer_dry_run_auto_attaches_two_files(self):
        rc, out, err = self._run([
            "--talent-id", "t_offer_test",
            "--template", "onboarding_offer",
            "--vars", *self._BASE_OFFER_VARS,
            "--dry-run",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr={}".format(err))
        result = json.loads(out.strip().splitlines()[-1])
        names = [a["name"] for a in result["attachments"]]
        self.assertIn("模板-示例科技实习协议-2026年4月版.docx", names)
        self.assertIn("示例科技-实习生入职信息登记表-2026年版.docx", names)
        # 两个都应该是 auto=True
        for a in result["attachments"]:
            self.assertTrue(a.get("auto"),
                            "{} 应标 auto=True".format(a["name"]))

    def test_round1_invite_no_auto_attach(self):
        """没注册的模板不应自动带任何附件。"""
        rc, out, err = self._run([
            "--talent-id", "t_test",
            "--template", "round1_invite",
            "--vars",
            "round1_time=2026-05-06 14:00",
            "position=量化研究员",
            "position_suffix=（实习）",
            "location=上海·南京西路",
            "--dry-run",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr={}".format(err))
        result = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(result["attachments"], [])

    def test_manual_attach_dedup(self):
        """agent 手动 --attach 了同一份合同 → 不会重复追加。"""
        agreement = (Path(REAL_DATA_ROOT) /
                     "onoffer_data/模板-示例科技实习协议-2026年4月版.docx")
        rc, out, err = self._run([
            "--talent-id", "t_offer_test",
            "--template", "onboarding_offer",
            "--vars", *self._BASE_OFFER_VARS,
            "--attach", str(agreement),
            "--dry-run",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr={}".format(err))
        result = json.loads(out.strip().splitlines()[-1])
        names = [a["name"] for a in result["attachments"]]
        # 只能出现一次
        self.assertEqual(names.count("模板-示例科技实习协议-2026年4月版.docx"), 1)
        # 登记表仍应被自动追加
        self.assertIn("示例科技-实习生入职信息登记表-2026年版.docx", names)
        # 手动那份 auto=False，自动那份 auto=True
        agreement_meta = next(
            a for a in result["attachments"]
            if a["name"] == "模板-示例科技实习协议-2026年4月版.docx"
        )
        form_meta = next(
            a for a in result["attachments"]
            if a["name"] == "示例科技-实习生入职信息登记表-2026年版.docx"
        )
        self.assertFalse(agreement_meta.get("auto"))
        self.assertTrue(form_meta.get("auto"))

    def test_onboarding_offer_fails_when_default_attachment_missing(self):
        """合同被人挪走 → cmd_send 应 fail-fast 拒绝发送。"""
        with tempfile.TemporaryDirectory(prefix="aa_send_") as tmp:
            os.environ["RECRUIT_DATA_ROOT"] = tmp
            try:
                rc, out, err = self._run([
                    "--talent-id", "t_offer_test",
                    "--template", "onboarding_offer",
                    "--vars", *self._BASE_OFFER_VARS,
                    "--dry-run",
                ])
                self.assertNotEqual(rc, 0,
                                    "应 fail-fast：默认附件缺失不能继续")
                self.assertIn("默认附件文件缺失", err)
            finally:
                os.environ.pop("RECRUIT_DATA_ROOT", None)


if __name__ == "__main__":
    unittest.main()
