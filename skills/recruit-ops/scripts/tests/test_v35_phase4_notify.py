#!/usr/bin/env python3
"""tests/test_v35_phase4_notify.py —— v3.5 Phase 4 测试。

【验证目标】
v3.5 Phase 4 的核心改造：
  1. ops/cmd_push_alert.py 已下线，唯一的飞书消息推送 atomic CLI 是
     feishu/cmd_notify.py（与 feishu/cmd_calendar_create / cmd_calendar_delete
     并列在飞书 sink 目录下）。
  2. cmd_notify 是真正的 atomic：不写 DB / 不调 LLM / 不联动状态机；
     仅把一段文本通过 lib.feishu.send_text 或 send_text_to_hr 推出去。
  3. dry-run 不产生真实推送，且 --json 输出可被 agent 解析。
  4. stdin 模式可读长文本（agent 串 daily summary 的常规用法）。
  5. 旧入口 ops.cmd_push_alert 必须真的不可 import，避免 agent 误用。
"""
from __future__ import annotations

import io
import json
import os
import unittest
from unittest import mock

import tests.helpers as helpers  # noqa: F401  保证 _InMemoryTdb 注入

os.environ["RECRUIT_SUPPRESS_SELF_VERIFY_ALERT"] = "1"

from feishu import cmd_notify  # noqa: E402


# ════════════════════════════════════════════════════════════════════════════
# 1. cmd_notify --to boss
# ════════════════════════════════════════════════════════════════════════════

class TestCmdNotifyBoss(unittest.TestCase):

    def test_dry_run_does_not_call_feishu(self):
        """--dry-run 必须既不调 lib.feishu.send_text，也不调 send_text_to_hr。"""
        with mock.patch("lib.feishu.send_text") as mock_boss, \
             mock.patch("lib.feishu.send_text_to_hr") as mock_hr:
            out, err, rc = helpers.call_main("feishu.cmd_notify", [
                "--title", "测试告警",
                "--body", "这是一段告警正文",
                "--severity", "warn",
                "--dry-run",
                "--json",
            ])
        self.assertEqual(rc, 0, "stderr=" + err)
        mock_boss.assert_not_called()
        mock_hr.assert_not_called()
        result = json.loads(out)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["to"], "boss")
        self.assertGreater(result["chars"], 0)
        self.assertIn("测试告警", result["preview"])
        self.assertIn("WARN", result["preview"])

    def test_real_send_routes_to_send_text(self):
        """非 dry-run 默认走 lib.feishu.send_text（boss 通道）。"""
        with mock.patch("lib.feishu.send_text", return_value=True) as mock_boss, \
             mock.patch("lib.feishu.send_text_to_hr") as mock_hr:
            out, err, rc = helpers.call_main("feishu.cmd_notify", [
                "--title", "Cron 失败",
                "--body", "inbox.cmd_scan exit=1",
                "--severity", "error",
                "--source", "cron.cron_runner",
                "--json",
            ])
        self.assertEqual(rc, 0, "stderr=" + err)
        mock_boss.assert_called_once()
        mock_hr.assert_not_called()
        text = mock_boss.call_args[0][0]
        # 关键字段都被拼到推送文本
        self.assertIn("Cron 失败", text)
        self.assertIn("ERROR", text)
        self.assertIn("inbox.cmd_scan exit=1", text)
        self.assertIn("cron.cron_runner", text)
        result = json.loads(out)
        self.assertTrue(result["ok"])

    def test_send_failure_returns_exit_code_2(self):
        """lib.feishu.send_text 返回 False 时，CLI 必须以 rc=2 收尾，
        让 agent / cron 把告警发送失败本身当作可观测信号。"""
        with mock.patch("lib.feishu.send_text", return_value=False):
            out, err, rc = helpers.call_main("feishu.cmd_notify", [
                "--title", "x", "--body", "y", "--json",
            ])
        self.assertEqual(rc, 2)
        result = json.loads(out)
        self.assertFalse(result["ok"])

    def test_empty_title_rejected(self):
        out, err, rc = helpers.call_main("feishu.cmd_notify", [
            "--title", "  ", "--body", "y",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("title", err.lower())


# ════════════════════════════════════════════════════════════════════════════
# 2. cmd_notify --to hr
# ════════════════════════════════════════════════════════════════════════════

class TestCmdNotifyHr(unittest.TestCase):

    def test_to_hr_routes_to_hr_channel(self):
        with mock.patch("lib.feishu.send_text") as mock_boss, \
             mock.patch("lib.feishu.send_text_to_hr", return_value=True) as mock_hr:
            out, err, rc = helpers.call_main("feishu.cmd_notify", [
                "--title", "HR 复核",
                "--body", "候选人 t_xxx 笔试已通过",
                "--to", "hr",
                "--json",
            ])
        self.assertEqual(rc, 0, "stderr=" + err)
        mock_hr.assert_called_once()
        mock_boss.assert_not_called()

    def test_severity_choices_enforced(self):
        out, err, rc = helpers.call_main("feishu.cmd_notify", [
            "--title", "x", "--body", "y", "--severity", "totally-invalid",
        ])
        self.assertEqual(rc, 2)  # argparse 拒非法 choice
        self.assertIn("totally-invalid", err)

    def test_stdin_mode_reads_long_body(self):
        """--stdin 让 agent 把 cron 摘要这种长文本管道进来。"""
        long_body = "\n".join("第{}行".format(i) for i in range(50))
        with mock.patch("sys.stdin", io.StringIO(long_body)), \
             mock.patch("lib.feishu.send_text", return_value=True) as mock_boss:
            out, err, rc = helpers.call_main("feishu.cmd_notify", [
                "--title", "Daily Summary",
                "--stdin",
                "--json",
            ])
        self.assertEqual(rc, 0, "stderr=" + err)
        text = mock_boss.call_args[0][0]
        self.assertIn("第0行", text)
        self.assertIn("第49行", text)


# ════════════════════════════════════════════════════════════════════════════
# 3. 旧 ops.cmd_push_alert 必须下线
# ════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════
# 2.5. cmd_notify --to interviewer-{master,bachelor,cpp}  (v3.5.7 §5.11)
# ════════════════════════════════════════════════════════════════════════════

class TestCmdNotifyInterviewer(unittest.TestCase):
    """三个新 role 必须各自走对应 send_text_to_interviewer_* wrapper，
    且不会误触 boss / hr 通道——agent §5.11 chain 依赖这一隔离。"""

    def _common_args(self, role):
        return [
            "--title", "一面派单",
            "--body", "候选人 t_iv 已被分配给你",
            "--to", role,
            "--json",
        ]

    def test_to_interviewer_master_routes_to_master(self):
        with mock.patch("lib.feishu.send_text_to_interviewer_master",
                        return_value=True) as m_master, \
             mock.patch("lib.feishu.send_text_to_interviewer_bachelor") as m_bach, \
             mock.patch("lib.feishu.send_text_to_interviewer_cpp") as m_cpp, \
             mock.patch("lib.feishu.send_text") as m_boss, \
             mock.patch("lib.feishu.send_text_to_hr") as m_hr:
            out, err, rc = helpers.call_main(
                "feishu.cmd_notify", self._common_args("interviewer-master"))
        self.assertEqual(rc, 0, "stderr=" + err)
        m_master.assert_called_once()
        for unused in (m_bach, m_cpp, m_boss, m_hr):
            unused.assert_not_called()
        result = json.loads(out)
        self.assertEqual(result["to"], "interviewer-master")
        self.assertTrue(result["ok"])
        # 确认 wrapper 收到的就是渲染后的文本
        text = m_master.call_args[0][0]
        self.assertIn("一面派单", text)
        self.assertIn("候选人 t_iv 已被分配给你", text)

    def test_to_interviewer_bachelor_routes_to_bachelor(self):
        with mock.patch("lib.feishu.send_text_to_interviewer_master") as m_master, \
             mock.patch("lib.feishu.send_text_to_interviewer_bachelor",
                        return_value=True) as m_bach, \
             mock.patch("lib.feishu.send_text_to_interviewer_cpp") as m_cpp, \
             mock.patch("lib.feishu.send_text") as m_boss, \
             mock.patch("lib.feishu.send_text_to_hr") as m_hr:
            out, err, rc = helpers.call_main(
                "feishu.cmd_notify", self._common_args("interviewer-bachelor"))
        self.assertEqual(rc, 0, "stderr=" + err)
        m_bach.assert_called_once()
        for unused in (m_master, m_cpp, m_boss, m_hr):
            unused.assert_not_called()

    def test_to_interviewer_cpp_routes_to_cpp(self):
        with mock.patch("lib.feishu.send_text_to_interviewer_master") as m_master, \
             mock.patch("lib.feishu.send_text_to_interviewer_bachelor") as m_bach, \
             mock.patch("lib.feishu.send_text_to_interviewer_cpp",
                        return_value=True) as m_cpp, \
             mock.patch("lib.feishu.send_text") as m_boss, \
             mock.patch("lib.feishu.send_text_to_hr") as m_hr:
            out, err, rc = helpers.call_main(
                "feishu.cmd_notify", self._common_args("interviewer-cpp"))
        self.assertEqual(rc, 0, "stderr=" + err)
        m_cpp.assert_called_once()
        for unused in (m_master, m_bach, m_boss, m_hr):
            unused.assert_not_called()

    def test_dry_run_to_interviewer_does_not_call_anything(self):
        with mock.patch("lib.feishu.send_text_to_interviewer_master") as m_master, \
             mock.patch("lib.feishu.send_text_to_interviewer_bachelor") as m_bach, \
             mock.patch("lib.feishu.send_text_to_interviewer_cpp") as m_cpp:
            out, err, rc = helpers.call_main("feishu.cmd_notify", [
                "--title", "派单",
                "--body", "x",
                "--to", "interviewer-cpp",
                "--dry-run", "--json",
            ])
        self.assertEqual(rc, 0)
        for unused in (m_master, m_bach, m_cpp):
            unused.assert_not_called()
        result = json.loads(out)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["to"], "interviewer-cpp")

    def test_invalid_role_rejected(self):
        out, err, rc = helpers.call_main("feishu.cmd_notify", [
            "--title", "x", "--body", "y",
            "--to", "interviewer-totally-bogus",
        ])
        self.assertEqual(rc, 2)
        self.assertIn("interviewer-totally-bogus", err)


# ════════════════════════════════════════════════════════════════════════════
# 3. 旧 ops.cmd_push_alert 必须下线
# ════════════════════════════════════════════════════════════════════════════

class TestOldPushAlertGone(unittest.TestCase):

    def test_legacy_module_offline(self):
        """v3.5：ops.cmd_push_alert 必须真的不可 import；
        否则 agent 仍可能拿旧入口推消息，与 cmd_notify 不一致。"""
        import importlib
        with self.assertRaises(ImportError):
            importlib.import_module("ops.cmd_push_alert")


if __name__ == "__main__":
    unittest.main()
