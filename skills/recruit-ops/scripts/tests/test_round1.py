#!/usr/bin/env python3
"""一面相关测试：统一结果命令 + round1 调度 / defer / 兼容别名。"""
import unittest
from unittest import mock

from tests.helpers import call_main, new_candidate, wipe_state
from core_state import load_candidate


class TestRound1Result(unittest.TestCase):

    def setUp(self):
        wipe_state()

    def test_exam_attachments_prefer_shared_tar(self):
        from interview import cmd_result as _result_mod
        from recruit_paths import exam_archive_dir

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

    def test_round1_reject_keep(self):
        tid = new_candidate()
        out, _, rc = call_main("interview.cmd_result", [
            "--talent-id", tid, "--result", "reject_keep",
            "--round", "1",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("保留人才库", out)
        st_out, _, _ = call_main("cmd_status", ["--talent-id", tid])
        self.assertIn("ROUND1_DONE_REJECT_KEEP", st_out)

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

    def test_round1_wrong_stage_fails(self):
        tid = new_candidate()
        call_main("interview.cmd_result", [
            "--talent-id", tid, "--result", "reject_keep",
            "--round", "1",
        ])
        # 已是 REJECT 状态，再执行 pass 应失败
        _, _, rc = call_main("interview.cmd_result", [
            "--talent-id", tid, "--result", "pass", "--email", "x@x.com",
            "--round", "1",
        ])
        self.assertNotEqual(rc, 0)

    def test_round1_result_wrapper_still_forwards(self):
        tid = new_candidate()
        out, err, rc = call_main("cmd_round1_result", [
            "--talent-id", tid, "--result", "pass", "--email", "x@x.com",
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("一面通过", out)


class TestRound1SchedulingFlow(unittest.TestCase):

    def setUp(self):
        wipe_state()

    def test_round1_defer_enters_wait_return_and_sends_email(self):
        tid = new_candidate("一面暂缓人", "r1defer@example.com")
        out, err, rc = call_main("cmd_round1_schedule", [
            "--talent-id", tid, "--time", "2026-04-10 10:00",
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))

        import cmd_round1_defer
        with mock.patch.object(cmd_round1_defer, "_send_defer_email", return_value=1357) as email_mock:
            out, err, rc = call_main("cmd_round1_defer", [
                "--talent-id", tid,
                "--reason", "候选人暂时不在国内，之后再约",
            ])

        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("WAIT_RETURN", out)
        email_mock.assert_called_once()
        cand = load_candidate(tid)
        self.assertEqual(cand["stage"], "WAIT_RETURN")
        self.assertEqual(cand["wait_return_round"], 1)

    def test_round1_confirm_wrapper_still_forwards(self):
        tid = new_candidate("一面确认人", "r1confirm@example.com")
        out, err, rc = call_main("cmd_round1_schedule", [
            "--talent-id", tid, "--time", "2026-04-10 10:00",
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))

        from interview import cmd_confirm as _confirm_mod
        with mock.patch.object(_confirm_mod, "_spawn_calendar_bg", return_value=2468):
            out, err, rc = call_main("cmd_round1_confirm", ["--talent-id", tid])

        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("一面时间已确认", out)
        cand = load_candidate(tid)
        self.assertEqual(cand["stage"], "ROUND1_SCHEDULED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
