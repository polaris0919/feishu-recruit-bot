#!/usr/bin/env python3
"""一面相关测试：统一结果命令 + round1 调度 / defer / 兼容别名。"""
import unittest
from unittest import mock

from tests.helpers import call_main, new_candidate, wipe_state


class TestRound1Result(unittest.TestCase):

    def setUp(self):
        wipe_state()

    def test_exam_attachments_prefer_shared_tar(self):
        from interview import cmd_result as _result_mod
        from lib.recruit_paths import exam_archive_dir

        tar_path = str(exam_archive_dir() / "笔试题.tar")
        with mock.patch("os.path.isfile", side_effect=lambda p: p == tar_path), \
             mock.patch("os.path.isdir", return_value=False):
            attachments = _result_mod._get_exam_attachments()

        self.assertEqual(attachments, [tar_path])

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
        out, _, rc = call_main("interview.cmd_result", [
            "--talent-id", tid, "--result", "reject_delete",
            "--round", "1",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("彻底删除", out)

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
            "邮件正文 张三 sample.user@example.com",  # 嵌入合法邮箱但仍非法
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
        call_main("interview.cmd_result", [
            "--talent-id", tid, "--result", "reject_delete",
            "--round", "1",
        ])
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
