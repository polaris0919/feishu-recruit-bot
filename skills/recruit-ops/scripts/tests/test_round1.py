#!/usr/bin/env python3
"""一面相关测试：统一结果命令 + round1 调度 / defer / 兼容别名。"""
import unittest
from unittest import mock

from tests.helpers import call_main, new_candidate, wipe_state


class TestRound1Result(unittest.TestCase):

    def setUp(self):
        wipe_state()

    def test_exam_attachments_prefer_shared_tar(self):
        """v3.8.4 起 exam 题包探测移到 email_templates.auto_attachments，
        但"多候选挑第一个能用的"语义保持不变 —— 只有 .tar 存在时仍能命中。"""
        from email_templates import auto_attachments as aa
        from lib.recruit_paths import exam_archive_dir

        tar_path = exam_archive_dir() / "笔试题.tar"

        def _fake_is_file(self):
            return str(self) == str(tar_path)

        def _fake_stat(self):
            class S:
                st_size = 1024
            return S()

        with mock.patch("pathlib.Path.is_file", _fake_is_file), \
             mock.patch("pathlib.Path.stat", _fake_stat):
            attachments = aa.auto_attachments_for("exam_invite")

        self.assertEqual([str(p) for p in attachments], [str(tar_path)])

    def test_round1_pass_creates_exam(self):
        tid = new_candidate()
        out, err, rc = call_main("interview.cmd_result", [
            "--talent-id", tid, "--result", "pass", "--email", "x@x.com",
            "--round", "1",
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("一面通过", out)
        self.assertIn("exam-", out)

    def test_round1_reject_keep_no_longer_supported(self):
        """一面 reject_keep 已下线：必须返回错误，提示改用 reject_delete。"""
        tid = new_candidate()
        _, err, rc = call_main("interview.cmd_result", [
            "--talent-id", tid, "--result", "reject_keep",
            "--round", "1",
        ])
        self.assertNotEqual(rc, 0)
        self.assertIn("reject_delete", err)

    def test_round1_reject_delete(self):
        tid = new_candidate()
        from interview import cmd_result
        with mock.patch.object(cmd_result, "_cmd_delete", return_value={
            "ok": True,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "json": {"archive_path": "/tmp/archive.json"},
        }):
            out, _, rc = call_main("interview.cmd_result", [
                "--talent-id", tid, "--result", "reject_delete",
                "--confirm-reject-delete", tid,
                "--round", "1",
            ])
        self.assertEqual(rc, 0)
        self.assertIn("彻底删除", out)

    def test_round1_reject_delete_stops_when_rejection_email_fails(self):
        tid = new_candidate()
        from interview import cmd_result
        with mock.patch.object(cmd_result, "_send_rejection_email",
                               return_value={"ok": False, "error": "smtp down"}):
            _, err, rc = call_main("interview.cmd_result", [
                "--talent-id", tid, "--result", "reject_delete",
                "--confirm-reject-delete", tid,
                "--round", "1",
            ])
        self.assertNotEqual(rc, 0)
        self.assertIn("未执行删档", err)
        st_out, _, _ = call_main("cmd_status", ["--talent-id", tid])
        self.assertIn(tid, st_out)

    def test_round1_pass_email_failure_does_not_advance_stage(self):
        tid = new_candidate()
        from interview import cmd_result
        with mock.patch.object(cmd_result, "_send_exam_email", return_value=None):
            _, err, rc = call_main("interview.cmd_result", [
                "--talent-id", tid, "--result", "pass", "--email", "x@x.com",
                "--round", "1",
            ])
        self.assertNotEqual(rc, 0)
        self.assertIn("仍停留", err)
        st_out, _, _ = call_main("cmd_status", ["--talent-id", tid])
        self.assertIn("NEW", st_out)

    def test_round1_pass_without_email_fails(self):
        tid = new_candidate()
        _, _, rc = call_main("interview.cmd_result", [
            "--talent-id", tid, "--result", "pass",
            "--round", "1",
        ])
        self.assertNotEqual(rc, 0)

    def test_round1_pass_with_invalid_email_fails(self):
        """回归测试：闵思涵案 (04-20) —— 上层误把模板/正文当成 --email 传进来。
        即使语法上通过 argparse，也必须被 cmd_result 的 email 格式校验拦下。"""
        tid = new_candidate()
        bogus_inputs = [
            "笔试邀请邮件内容",  # 中文字面量 — 闵思涵案的真实输入
            "exam invite body",  # 英文 placeholder
            "邮件正文 张三 embedded@example.com",  # 嵌入合法邮箱但仍非法
            "no-at-sign.example.com",  # 没有 @
            "user@",  # 没有 domain
        ]
        for bogus in bogus_inputs:
            _, err, rc = call_main("interview.cmd_result", [
                "--talent-id", tid, "--result", "pass",
                "--email", bogus, "--round", "1",
            ])
            self.assertNotEqual(rc, 0,
                                "bogus email {!r} 不应被接受".format(bogus))
            self.assertIn("不是合法邮箱", err,
                          "bogus={!r} stderr 应给出明确诊断: {}".format(bogus, err))

    def test_round1_wrong_stage_fails(self):
        tid = new_candidate()
        # 走一遍 reject_delete 流程（候选人会被删除）
        from lib import talent_db as _tdb
        _tdb.delete_talent(tid)
        # 候选人已被删，再执行 pass 应失败
        _, _, rc = call_main("interview.cmd_result", [
            "--talent-id", tid, "--result", "pass", "--email", "x@x.com",
            "--round", "1",
        ])
        self.assertNotEqual(rc, 0)

# v3.5: TestRound1SchedulingFlow 已下线。
#  - cmd_round1_schedule wrapper 已删除（agent 用 outbound.cmd_send + talent.cmd_update 自拼）
#  - interview.cmd_{confirm,defer,reschedule} wrapper 已删除（同上）
# 端到端剧本（schedule → confirm / defer）改由 tests/test_agent_chain.py 用 lib.run_chain
# 串 atomic CLI 验证；本文件只保留 interview.cmd_result --round 1 的 atomic 行为测试。


if __name__ == "__main__":
    unittest.main(verbosity=2)
