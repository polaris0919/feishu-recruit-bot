#!/usr/bin/env python3
"""二面相关测试：cmd_round2_result / cmd_round2_reschedule / cmd_round2_confirm / cmd_round2_defer。"""
import sys
import types
import unittest
from unittest import mock

from tests.helpers import call_main, new_candidate, wipe_state
from core_state import load_candidate


def _setup_r2():
    """候选人走完一面 + 笔试，进入 ROUND2_SCHEDULING。"""
    tid = new_candidate()
    call_main("cmd_round1_result", [
        "--talent-id", tid, "--result", "pass", "--email", "x@x.com",
    ])
    call_main("cmd_exam_result", [
        "--talent-id", tid, "--result", "pass",
        "--round2-time", "2026-04-01 14:00",
    ])
    return tid


def _setup_confirmed_r2():
    """候选人走完二面确认链路，进入 ROUND2_SCHEDULED。"""
    tid = _setup_r2()
    from interview import cmd_confirm as _confirm_mod
    with mock.patch.object(_confirm_mod, "_spawn_calendar_bg", return_value=2468):
        out, err, rc = call_main("cmd_round2_confirm", ["--talent-id", tid])
    if rc != 0:
        raise AssertionError("cmd_round2_confirm 应成功 out={} err={}".format(out, err))
    return tid


class TestRound2Result(unittest.TestCase):

    def setUp(self):
        wipe_state()

    def test_round2_pending(self):
        tid = _setup_confirmed_r2()
        out, _, rc = call_main("cmd_round2_result", [
            "--talent-id", tid, "--result", "pending",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("ROUND2_DONE_PENDING", out)

    def test_round2_pass(self):
        tid = _setup_confirmed_r2()
        out, err, rc = call_main("cmd_round2_result", [
            "--talent-id", tid, "--result", "pass",
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("OFFER_HANDOFF", out)

    def test_round2_reject_keep(self):
        tid = _setup_confirmed_r2()
        out, _, rc = call_main("cmd_round2_result", [
            "--talent-id", tid, "--result", "reject_keep",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("保留人才库", out)

    def test_round2_reject_delete(self):
        tid = _setup_confirmed_r2()
        out, _, rc = call_main("cmd_round2_result", [
            "--talent-id", tid, "--result", "reject_delete",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("彻底删除", out)

    def test_round2_wrong_stage_fails(self):
        tid = new_candidate()  # 还在 NEW
        _, _, rc = call_main("cmd_round2_result", [
            "--talent-id", tid, "--result", "pass",
        ])
        self.assertNotEqual(rc, 0)


class TestRound2SchedulingFlow(unittest.TestCase):

    def setUp(self):
        wipe_state()

    def test_round2_reschedule_default_confirms_and_creates_calendar(self):
        """改期默认自动确认 + 创建日历（双方已达成一致）"""
        tid = _setup_r2()
        from interview import cmd_reschedule as _mod
        with mock.patch.object(_mod, "_send_reschedule_email", return_value=4321) as email_mock, \
             mock.patch.object(_mod, "spawn_calendar", return_value=8765) as cal_mock:
            out, err, rc = call_main("cmd_round2_reschedule", [
                "--talent-id", tid, "--time", "2026-04-02 15:00",
            ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("已直接确认", out)
        email_mock.assert_called_once()
        cal_mock.assert_called_once()

    def test_round2_reschedule_no_confirm_defers_calendar(self):
        """--no-confirm 时等候选人确认，不建日历"""
        tid = _setup_r2()
        from interview import cmd_reschedule as _mod
        with mock.patch.object(_mod, "_send_reschedule_email", return_value=4321) as email_mock, \
             mock.patch.object(_mod, "spawn_calendar", return_value=8765) as cal_mock:
            out, err, rc = call_main("cmd_round2_reschedule", [
                "--talent-id", tid, "--time", "2026-04-02 15:00", "--no-confirm",
            ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("等待候选人确认", out)
        email_mock.assert_called_once()

    def test_round2_confirm_creates_boss_calendar(self):
        from interview import cmd_confirm as _confirm_mod

        _demo_cand = {
            "talent_id": "t_demo",
            "stage": "ROUND2_SCHEDULING",
            "candidate_name": "测试人",
            "candidate_email": "demo@test.com",
            "round2_time": "2026-04-01 14:00",
            "round2_confirm_status": "PENDING",
        }
        fake_tdb = types.SimpleNamespace()
        fake_tdb._is_enabled = lambda: True
        fake_tdb.get_one = lambda tid: _demo_cand if tid == "t_demo" else None
        fake_tdb.mark_confirmed = mock.Mock()

        with mock.patch.dict(sys.modules, {"talent_db": fake_tdb}), \
             mock.patch.object(_confirm_mod, "_spawn_calendar_bg", return_value=2468) as cal_mock:
            out, err, rc = call_main("cmd_round2_confirm", ["--talent-id", "t_demo"])

        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("二面时间已确认", out)
        self.assertIn("飞书日历: 创建中", out)
        fake_tdb.mark_confirmed.assert_called_once_with("t_demo", 2, auto=False)
        cal_mock.assert_called_once()

    def test_round2_confirm_calendar_uses_offline_defaults(self):
        from interview import cmd_confirm as _confirm_mod

        _demo_cand = {
            "talent_id": "t_demo",
            "stage": "ROUND2_SCHEDULING",
            "candidate_name": "测试人",
            "candidate_email": "demo@test.com",
            "round2_time": "2026-04-01 14:00",
            "round2_confirm_status": "PENDING",
        }
        fake_tdb = types.SimpleNamespace()
        fake_tdb._is_enabled = lambda: True
        fake_tdb.get_one = lambda tid: _demo_cand if tid == "t_demo" else None
        fake_tdb.mark_confirmed = mock.Mock()

        with mock.patch.dict(sys.modules, {"talent_db": fake_tdb}), \
             mock.patch.object(_confirm_mod, "_spawn_calendar_bg", return_value=2468) as cal_mock:
            out, err, rc = call_main("cmd_round2_confirm", ["--talent-id", "t_demo"])

        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("线下面试", out)
        cal_mock.assert_called_once_with(
            "t_demo",
            "2026-04-01 14:00",
            2,
            "demo@test.com",
            "测试人",
        )

    def test_round2_switch_mode_is_deprecated_stub(self):
        out, err, rc = call_main("cmd_round2_switch_mode", ["--talent-id", "t_x"])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("已废弃", out)

    def test_round2_defer_enters_wait_return_and_sends_email(self):
        tid = _setup_r2()
        import cmd_round2_defer

        with mock.patch.object(cmd_round2_defer, "_send_defer_email", return_value=3456) as email_mock:
            out, err, rc = call_main("cmd_round2_defer", [
                "--talent-id", tid,
                "--reason", "候选人暂时不在上海，之后再约",
            ])

        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("暂缓安排", out)
        self.assertIn("WAIT_RETURN", out)
        email_mock.assert_called_once()
        cand = load_candidate(tid)
        self.assertEqual(cand["stage"], "WAIT_RETURN")
        self.assertEqual(cand["wait_return_round"], 2)
        st_out, _, st_rc = call_main("cmd_status", ["--talent-id", tid])
        self.assertEqual(st_rc, 0)
        self.assertIn("WAIT_RETURN", st_out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
