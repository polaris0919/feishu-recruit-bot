#!/usr/bin/env python3
"""笔试相关测试（v3.5 大幅瘦身）。

【v3.5 变更】
  - 删 TestExamPrereview：exam/exam_prereview.py 整个下架（cmd_exam_ai_review 不依赖它）。
  - 删 TestDailyExamReview：exam/daily_exam_review.py 整个下架；其 5 个 scan_*
    + format_*report + main 全部由 inbox.cmd_scan + inbox.cmd_analyze + agent
    按 docs/AGENT_RULES.md 编排。剧本级测试搬到 tests/test_agent_chain.py。
  - 保留 TestExamResult：cmd_exam_result 仍是 v3.5 的 atomic CLI。
"""
import unittest
from unittest import mock

from tests.helpers import call_main, new_candidate, wipe_state


def _setup_exam():
    """公共前置：候选人过一面，进入 EXAM_SENT。"""
    tid = new_candidate()
    call_main("interview.cmd_result", [
        "--talent-id", tid, "--result", "pass", "--email", "x@x.com",
        "--round", "1",
    ])
    return tid


class TestExamResult(unittest.TestCase):

    def setUp(self):
        wipe_state()

    def test_exam_pass_transitions(self):
        tid = _setup_exam()
        out, err, rc = call_main("cmd_exam_result", [
            "--talent-id", tid, "--result", "pass",
            "--round2-time", "2026-04-01 14:00",
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("ROUND2_SCHEDULING", out)

    def test_exam_pass_requires_round2_time_and_does_not_reuse_old_time(self):
        from lib import core_state

        tid = _setup_exam()
        state = core_state.load_state()
        state["candidates"][tid]["round2_time"] = "2026-04-01 14:00"
        core_state.save_state(state)

        out, err, rc = call_main("cmd_exam_result", [
            "--talent-id", tid, "--result", "pass",
        ])
        self.assertNotEqual(rc, 0)
        self.assertIn("round2-time", err)

        state = core_state.load_state()
        cand = state["candidates"][tid]
        self.assertEqual(cand.get("stage"), "EXAM_SENT")
        self.assertEqual(cand.get("round2_time"), "2026-04-01 14:00")

    def test_exam_pass_defers_boss_calendar_until_confirmed(self):
        tid = _setup_exam()
        from exam import cmd_exam_result
        with mock.patch.object(cmd_exam_result, "send_round2_notification", return_value=1234) as email_mock:
            out, err, rc = call_main("cmd_exam_result", [
                "--talent-id", tid, "--result", "pass",
                "--round2-time", "2026-04-01 14:00",
            ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("ROUND2_SCHEDULING", out)
        self.assertIn("候选人确认后", out)
        self.assertIn("线下面试", out)
        email_mock.assert_called_once()

    def test_exam_reject_keep(self):
        tid = _setup_exam()
        out, _, rc = call_main("cmd_exam_result", [
            "--talent-id", tid, "--result", "reject_keep",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("保留人才库", out)
        st_out, _, _ = call_main("cmd_status", ["--talent-id", tid])
        self.assertIn("EXAM_REJECT_KEEP", st_out)

    def test_exam_reject_delete(self):
        tid = _setup_exam()
        out, _, rc = call_main("cmd_exam_result", [
            "--talent-id", tid, "--result", "reject_delete",
        ])
        self.assertEqual(rc, 0)

    def test_exam_wrong_stage_fails(self):
        tid = new_candidate()  # 还在 NEW，没过一面
        _, _, rc = call_main("cmd_exam_result", [
            "--talent-id", tid, "--result", "pass",
            "--round2-time", "2026-04-01 14:00",
        ])
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
