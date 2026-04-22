#!/usr/bin/env python3
"""二面 atomic CLI 测试（v3.5 大幅瘦身）。

【v3.5 变更】
  - TestRound2SchedulingFlow 整个下线：interview.cmd_{confirm,defer,reschedule}
    wrapper 已删除，端到端剧本（schedule → confirm / reschedule / defer）改由
    tests/test_agent_chain.py 用 lib.run_chain 串 atomic CLI 验证。
  - TestRound2Result 保留：interview.cmd_result --round 2 仍是 atomic CLI。
    setUp 直接用 talent.cmd_update 推到 ROUND2_SCHEDULED，不走已删的 cmd_confirm。
"""
import unittest

from tests.helpers import call_main, new_candidate, wipe_state


def _setup_r2_scheduled():
    """候选人走完一面 + 笔试，并直接置为 ROUND2_SCHEDULED（替代旧 cmd_confirm 路径）。"""
    tid = new_candidate()
    call_main("interview.cmd_result", [
        "--talent-id", tid, "--result", "pass", "--email", "x@x.com",
        "--round", "1",
    ])
    call_main("cmd_exam_result", [
        "--talent-id", tid, "--result", "pass",
        "--round2-time", "2026-04-01 14:00",
    ])
    out, err, rc = call_main("talent.cmd_update", [
        "--talent-id", tid,
        "--stage", "ROUND2_SCHEDULED",
        "--set", "round2_confirm_status=CONFIRMED",
        "--force",
    ])
    if rc != 0:
        raise AssertionError("talent.cmd_update 应成功 out={} err={}".format(out, err))
    return tid


class TestRound2Result(unittest.TestCase):

    def setUp(self):
        wipe_state()

    def test_round2_pending_no_longer_supported(self):
        """二面 pending 已下线：argparse 直接拒绝。"""
        tid = _setup_r2_scheduled()
        _, err, rc = call_main("interview.cmd_result", [
            "--talent-id", tid, "--result", "pending",
            "--round", "2",
        ])
        self.assertNotEqual(rc, 0)
        self.assertIn("pending", err)

    def test_round2_pass(self):
        tid = _setup_r2_scheduled()
        out, err, rc = call_main("interview.cmd_result", [
            "--talent-id", tid, "--result", "pass",
            "--round", "2",
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        # v3.6: OFFER_HANDOFF 已下线；round2 pass 一步推到 POST_OFFER_FOLLOWUP。
        self.assertIn("POST_OFFER_FOLLOWUP", out)
        self.assertNotIn("OFFER_HANDOFF", out)

    def test_round2_reject_keep(self):
        tid = _setup_r2_scheduled()
        out, _, rc = call_main("interview.cmd_result", [
            "--talent-id", tid, "--result", "reject_keep",
            "--round", "2",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("保留人才库", out)

    def test_round2_reject_delete(self):
        tid = _setup_r2_scheduled()
        out, _, rc = call_main("interview.cmd_result", [
            "--talent-id", tid, "--result", "reject_delete",
            "--round", "2",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("彻底删除", out)

    def test_round2_wrong_stage_fails(self):
        tid = new_candidate()  # 还在 NEW
        _, _, rc = call_main("interview.cmd_result", [
            "--talent-id", tid, "--result", "pass",
            "--round", "2",
        ])
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
