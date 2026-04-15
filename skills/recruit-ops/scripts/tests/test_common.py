#!/usr/bin/env python3
"""公共跨阶段操作测试：改期请求 / 改期报告扫描。"""
import io
import os
import subprocess
import sys
import types
import unittest
import datetime as dt
from unittest import mock

from tests.helpers import call_main, new_candidate, wipe_state
from core_state import load_candidate
import talent_db
from tests.scenario_helpers import ScenarioRunner, make_reply_email, subprocess_result_from_call_main

_SCRIPTS = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


def _assert_cli_ok(module_name, argv):
    out, err, rc = call_main(module_name, argv)
    if rc != 0:
        raise AssertionError("{} 应成功 out={} err={}".format(module_name, out, err))
    if "EXCEPTION:" in err:
        raise AssertionError("{} 不应异常失败 err={}".format(module_name, err))
    return out, err


def _setup_confirmed_r2(calendar_event_id=None):
    """创建一个二面已确认的候选人（走真实命令流）。"""
    tid = new_candidate(name="改期测试人", email="resched@example.com")
    _assert_cli_ok("interview.cmd_result", [
        "--talent-id", tid, "--result", "pass", "--email", "resched@example.com",
        "--round", "1",
    ])
    _assert_cli_ok("cmd_exam_result", [
        "--talent-id", tid, "--result", "pass",
        "--round2-time", "2026-04-15 15:00",
    ])
    import interview.cmd_confirm as cmd_confirm
    with mock.patch.object(cmd_confirm, "_spawn_calendar_bg", return_value=2468):
        _assert_cli_ok("interview.cmd_confirm", ["--talent-id", tid, "--round", "2"])
    if calendar_event_id:
        talent_db.update_calendar_event_id(tid, 2, calendar_event_id)
    return tid


def _setup_confirmed_r1(calendar_event_id=None):
    """创建一个一面已确认的候选人（走真实命令流）。"""
    tid = new_candidate(name="一面改期人", email="r1resched@example.com")
    _assert_cli_ok("cmd_round1_schedule", [
        "--talent-id", tid, "--time", "2026-04-10 10:00",
    ])
    import interview.cmd_confirm as cmd_confirm
    with mock.patch.object(cmd_confirm, "_spawn_calendar_bg", return_value=1357):
        _assert_cli_ok("interview.cmd_confirm", ["--talent-id", tid, "--round", "1"])
    if calendar_event_id:
        talent_db.update_calendar_event_id(tid, 1, calendar_event_id)
    return tid


class TestRescheduleRequest(unittest.TestCase):

    def setUp(self):
        wipe_state()

    def assert_cli_business_fail(self, module_name, argv, expected_text=None):
        out, err, rc = call_main(module_name, argv)
        combined = "\n".join(part for part in (out, err) if part)
        self.assertNotEqual(rc, 0, "命令 {} 应失败".format(module_name))
        self.assertNotIn("EXCEPTION:", combined, "命令 {} 不应异常失败".format(module_name))
        if expected_text:
            self.assertIn(expected_text, combined)
        return out, err

    def test_reschedule_request_revokes_r2_and_sends_email(self):
        tid = _setup_confirmed_r2(calendar_event_id="evt_round2_old")
        import cmd_reschedule_request

        with mock.patch.object(cmd_reschedule_request, "_send_ack_email", return_value=5555) as email_mock, \
             mock.patch.object(cmd_reschedule_request, "_spawn_calendar_delete_bg", return_value=6666) as delete_mock:
            out, err, rc = call_main("cmd_reschedule_request", [
                "--talent-id", tid,
                "--round", "2",
                "--reason", "4月15日有其他安排",
                "--new-time", "2026-04-18 14:00",
            ])

        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("改期请求已处理", out)
        self.assertIn("确认状态: 已撤销", out)
        self.assertIn("候选人建议新时间: 2026-04-18 14:00", out)
        email_mock.assert_called_once_with("resched@example.com", tid, 2, candidate_name="改期测试人")
        delete_mock.assert_called_once_with("evt_round2_old")

        cand = load_candidate(tid)
        self.assertEqual(cand["stage"], "ROUND2_SCHEDULING")
        self.assertEqual(cand["round2_confirm_status"], "PENDING")
        self.assertEqual(cand["round2_time"], "2026-04-15 15:00")
        self.assertIsNone(cand["round2_calendar_event_id"])
        self.assertEqual(cand["audit"][-1]["action"], "round2_reschedule_requested")
        self.assertEqual(cand["audit"][-1]["payload"]["reason"], "4月15日有其他安排")
        self.assertEqual(cand["audit"][-1]["payload"]["new_time_proposed"], "2026-04-18 14:00")
        self.assertEqual(cand["audit"][-1]["payload"]["old_time"], "2026-04-15 15:00")
        self.assertEqual(cand["audit"][-1]["payload"]["old_calendar_event_id"], "evt_round2_old")

        st_out, _, st_rc = call_main("cmd_status", ["--talent-id", tid])
        self.assertEqual(st_rc, 0)
        self.assertIn("改期申请", st_out)
        self.assertIn("二面时间: 2026-04-15 15:00", st_out)

    def test_reschedule_request_revokes_r1_and_sends_email(self):
        tid = _setup_confirmed_r1(calendar_event_id="evt_round1_old")
        import cmd_reschedule_request

        with mock.patch.object(cmd_reschedule_request, "_send_ack_email", return_value=7777) as email_mock, \
             mock.patch.object(cmd_reschedule_request, "_spawn_calendar_delete_bg", return_value=8888) as delete_mock:
            out, err, rc = call_main("cmd_reschedule_request", [
                "--talent-id", tid,
                "--round", "1",
                "--reason", "临时有事需要改期",
            ])

        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("改期请求已处理", out)
        email_mock.assert_called_once_with("r1resched@example.com", tid, 1, candidate_name="一面改期人")
        delete_mock.assert_called_once_with("evt_round1_old")

        cand = load_candidate(tid)
        self.assertEqual(cand["stage"], "ROUND1_SCHEDULING")
        self.assertEqual(cand["round1_confirm_status"], "PENDING")
        self.assertEqual(cand["round1_time"], "2026-04-10 10:00")
        self.assertIsNone(cand["round1_calendar_event_id"])
        self.assertEqual(cand["audit"][-1]["action"], "round1_reschedule_requested")
        self.assertEqual(cand["audit"][-1]["payload"]["reason"], "临时有事需要改期")
        self.assertIsNone(cand["audit"][-1]["payload"]["new_time_proposed"])
        self.assertEqual(cand["audit"][-1]["payload"]["old_time"], "2026-04-10 10:00")
        self.assertEqual(cand["audit"][-1]["payload"]["old_calendar_event_id"], "evt_round1_old")

    def test_reschedule_request_wrong_stage_fails(self):
        tid = new_candidate()
        self.assert_cli_business_fail(
            "cmd_reschedule_request",
            ["--talent-id", tid, "--round", "2"],
            "有效改期阶段内",
        )

    def test_reschedule_report_with_new_time(self):
        import daily_exam_review

        report = daily_exam_review.format_reschedule_request_report({
            "round": 2,
            "talent_id": "t_demo",
            "candidate_name": "张三",
            "interview_time": "2026-04-15 15:00",
            "intent": "reschedule",
            "new_time": "2026-04-18 14:00",
            "summary": "4月15日有其他安排，希望改到18日",
        })
        self.assertIn("改期请求", report)
        self.assertIn("t_demo", report)
        self.assertIn("2026-04-18 14:00", report)
        self.assertIn("interview/cmd_reschedule.py", report)

    def test_reschedule_report_without_new_time(self):
        import daily_exam_review

        report = daily_exam_review.format_reschedule_request_report({
            "round": 1,
            "talent_id": "t_abc",
            "candidate_name": "李四",
            "interview_time": "2026-04-10 10:00",
            "intent": "reschedule",
            "new_time": None,
            "summary": "有事需要改期",
        })
        self.assertIn("改期请求", report)
        self.assertIn("interview/cmd_reschedule.py", report)
        self.assertIn("YYYY-MM-DD HH:MM", report)

    def test_reschedule_report_defer_until_shanghai(self):
        import daily_exam_review

        report = daily_exam_review.format_reschedule_request_report({
            "round": 2,
            "talent_id": "t_wait",
            "candidate_name": "王五",
            "interview_time": "2026-04-15 15:00",
            "intent": "defer_until_shanghai",
            "summary": "候选人暂时不在上海，之后再约",
        })
        self.assertIn("暂缓请求", report)
        self.assertIn("interview/cmd_defer.py", report)
        self.assertIn("WAIT_RETURN", report)

    def test_reschedule_report_request_online(self):
        import daily_exam_review

        report = daily_exam_review.format_reschedule_request_report({
            "round": 2,
            "talent_id": "t_online",
            "candidate_name": "赵六",
            "interview_time": "2026-04-15 15:00",
            "intent": "request_online",
            "summary": "候选人希望改为线上面试",
        })
        self.assertIn("线上面试请求", report)
        self.assertIn("interview/cmd_reschedule.py", report)
        self.assertIn("线上面试", report)

    def test_main_auto_handles_reschedule_scan(self):
        import daily_exam_review

        item = {
            "round": 2,
            "talent_id": "t_resched",
            "candidate_name": "改期人",
            "interview_time": "2026-04-15 15:00",
            "intent": "reschedule",
            "new_time": "2026-04-20 14:00",
            "summary": "希望改到20号",
        }
        fake_proc = types.SimpleNamespace(
            stdout="[二面改期请求已处理]\n- 确认状态: 已撤销".encode("utf-8"),
            stderr=b"",
        )
        with mock.patch.object(daily_exam_review, "scan_round1_reschedule_requests", return_value=[]), \
             mock.patch.object(daily_exam_review, "scan_round2_reschedule_requests", return_value=[item]), \
             mock.patch("subprocess.run", return_value=fake_proc) as run_mock, \
             mock.patch.dict(sys.modules, {"feishu": types.SimpleNamespace(send_text=lambda text: True)}):
            rc = daily_exam_review.main(["--auto", "--reschedule-scan-only"])

        self.assertEqual(rc, 0)
        called = run_mock.call_args[0][0]
        self.assertIn("cmd_reschedule_request.py", called[1])
        self.assertIn("--talent-id", called)
        self.assertIn("t_resched", called)
        self.assertIn("--round", called)
        self.assertIn("2", called)
        self.assertIn("--reason", called)
        self.assertIn("希望改到20号", called)
        self.assertIn("--new-time", called)
        self.assertIn("2026-04-20 14:00", called)

    def test_main_interview_scan_sets_boss_pending_after_delayed_confirm(self):
        import daily_exam_review

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid = scenario.create_round2_pending_candidate(
            name="主流程确认人", email="bosspending@example.com", round2_time="2026-04-20 14:00"
        )
        scenario.set_invite_sent_at(tid, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_on_scan(2, make_reply_email(
            "bosspending@example.com",
            "Re: 二面安排",
            "好的，我可以参加。",
            "<boss-pending@test>",
            sent_at=now,
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{"intent": "confirm", "new_time": None, "summary": "确认参加"}],
        ):
            rc1 = daily_exam_review.main(["--auto", "--interview-confirm-only"])
            rc2 = daily_exam_review.main(["--auto", "--interview-confirm-only"])

        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        scenario.assert_boss_pending(tid, 2, "2026-04-20 14:00")
        self.assertTrue(scenario.sent_reports)


class TestRegressionFixes(unittest.TestCase):

    def setUp(self):
        wipe_state()

    def test_finalize_script_direct_run_imports_lib_modules_first(self):
        script = os.path.join(_SCRIPTS, "common", "cmd_finalize_interview_time.py")
        env = os.environ.copy()
        env["RECRUIT_DISABLE_DB"] = "1"
        env.pop("TALENT_DB_PASSWORD", None)

        proc = subprocess.run(
            ["python3", script, "--talent-id", "t_demo", "--round", "1"],
            cwd=_SCRIPTS,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        combined = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("SyntaxError", combined)
        self.assertNotIn("invalid syntax", combined)
        self.assertIn("DB 未配置", combined)

    def test_calendar_cli_can_run_as_standalone_script(self):
        script = os.path.join(_SCRIPTS, "lib", "feishu", "calendar_cli.py")
        env = os.environ.copy()
        env["RECRUIT_DISABLE_SIDE_EFFECTS"] = "1"

        proc = subprocess.run(
            ["python3", script, "--talent-id", "t_demo", "--round2-time", "2026-04-20 14:00"],
            cwd=_SCRIPTS,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        combined = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
        self.assertEqual(proc.returncode, 0, combined)
        self.assertIn("测试模式：已跳过日历操作", proc.stdout)

    def test_defer_main_without_argv_uses_sys_argv(self):
        import interview.cmd_defer as mod

        buf_out, buf_err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["cmd_defer.py", "--talent-id", "t_demo", "--round", "1"]):
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = buf_out, buf_err
            try:
                rc = mod.main()
            finally:
                sys.stdout, sys.stderr = old_out, old_err

        combined = "\n".join(part for part in (buf_out.getvalue(), buf_err.getvalue()) if part)
        self.assertEqual(rc, 1)
        self.assertNotIn("NameError", combined)
        self.assertNotIn("sys is not defined", combined)
        self.assertIn("未找到候选人", combined)

    def test_reschedule_main_without_argv_uses_sys_argv(self):
        import interview.cmd_reschedule as mod

        buf_out, buf_err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", [
            "cmd_reschedule.py", "--talent-id", "t_demo", "--round", "1", "--time", "2026-04-20 14:00",
        ]):
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = buf_out, buf_err
            try:
                rc = mod.main()
            finally:
                sys.stdout, sys.stderr = old_out, old_err

        combined = "\n".join(part for part in (buf_out.getvalue(), buf_err.getvalue()) if part)
        self.assertEqual(rc, 1)
        self.assertNotIn("NameError", combined)
        self.assertNotIn("sys is not defined", combined)
        self.assertIn("未找到候选人", combined)

    def test_normalize_new_time_anchors_year_to_current_interview(self):
        import daily_exam_review

        normalized = daily_exam_review._normalize_new_time(
            "2024-05-12 10:00",
            "5月12日上午10:00可以吗？",
            "2026-05-10 14:00",
        )
        self.assertEqual(normalized, "2026-05-12 10:00")

    def test_main_interview_scan_confirm_with_new_time_updates_pending_time(self):
        import daily_exam_review

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid = scenario.create_round2_pending_candidate(
            name="确认新时间人", email="confirm-new-time@example.com", round2_time="2026-04-20 14:00"
        )
        scenario.set_invite_sent_at(tid, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_now(make_reply_email(
            "confirm-new-time@example.com",
            "Re: 二面安排",
            "4月24日下午三点可以，我确认这个时间。",
            "<confirm-new-time@test>",
            sent_at=now,
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{
                "intent": "confirm",
                "new_time": "2024-04-24 15:00",
                "summary": "确认参加4月24日下午三点面试",
            }],
        ):
            rc = daily_exam_review.main(["--auto", "--interview-confirm-only"])

        self.assertEqual(rc, 0)
        scenario.assert_boss_pending(tid, 2, "2026-04-24 15:00")
        cand = scenario.candidate(tid)
        self.assertEqual(cand["round2_last_email_id"], "<confirm-new-time@test>")

    def test_scan_interview_confirmations_does_not_reprocess_older_mail_after_cursor(self):
        import daily_exam_review

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid = scenario.create_round2_pending_candidate(
            name="游标稳定人", email="cursor-stable@example.com", round2_time="2026-04-20 14:00"
        )
        scenario.set_invite_sent_at(tid, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_now(make_reply_email(
            "cursor-stable@example.com",
            "Re: 二面安排",
            "我可以参加。",
            "<older-confirm@test>",
            sent_at=now - dt.timedelta(minutes=10),
        ))
        scenario.mailbox.deliver_now(make_reply_email(
            "cursor-stable@example.com",
            "Re: 二面安排",
            "我确认最新这封邮件里的时间。",
            "<newer-confirm@test>",
            sent_at=now,
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{"intent": "confirm", "new_time": None, "summary": "确认参加"}],
        ):
            first_results = daily_exam_review._scan_interview_confirmations(round_num=2, auto_mode=True)

        self.assertEqual(len(first_results), 1)
        self.assertEqual(first_results[0]["message_id"], "<newer-confirm@test>")
        self.assertEqual(scenario.candidate(tid)["round2_last_email_id"], "<newer-confirm@test>")

        with scenario.patch_daily_exam_review(daily_exam_review), \
             mock.patch.object(
                 daily_exam_review,
                 "_llm_analyze_reply",
                 side_effect=AssertionError("should not reprocess older mail"),
             ):
            second_results = daily_exam_review._scan_interview_confirmations(round_num=2, auto_mode=True)

        self.assertEqual(second_results, [])

    def test_main_reschedule_scan_uses_real_scan_and_calls_reschedule_request(self):
        import daily_exam_review

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid = scenario.create_confirmed_round2_candidate(
            name="真实改期扫描人", email="realresched@example.com", round2_time="2026-04-20 14:00"
        )
        scenario.set_invite_sent_at(tid, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_on_scan(2, make_reply_email(
            "realresched@example.com",
            "Re: 二面安排",
            "能否改到4月22日下午两点？",
            "<real-resched@test>",
            sent_at=now,
        ))
        fake_proc = types.SimpleNamespace(stdout=b"[ok]", stderr=b"")

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{"intent": "reschedule", "new_time": "2026-04-22 14:00", "summary": "希望改到22号"}],
        ), mock.patch("subprocess.run", return_value=fake_proc) as run_mock:
            rc1 = daily_exam_review.main(["--auto", "--reschedule-scan-only"])
            rc2 = daily_exam_review.main(["--auto", "--reschedule-scan-only"])

        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        called = run_mock.call_args[0][0]
        self.assertIn("cmd_reschedule_request.py", called[1])
        self.assertIn(tid, called)
        self.assertIn("2026-04-22 14:00", called)
        scenario.assert_last_email_id_updated(tid, "round2", "<real-resched@test>")

    def test_main_reschedule_scan_rolls_back_only_target_candidate_and_keeps_time(self):
        import daily_exam_review
        import cmd_reschedule_request

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid_a = scenario.create_confirmed_round2_candidate(
            name="改期甲", email="rollback-a@example.com", round2_time="2026-04-20 14:00"
        )
        tid_b = scenario.create_confirmed_round2_candidate(
            name="改期乙", email="rollback-b@example.com", round2_time="2026-04-21 15:00"
        )
        scenario.set_invite_sent_at(tid_a, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.set_invite_sent_at(tid_b, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_now(make_reply_email(
            "rollback-a@example.com", "Re: 二面安排", "想改到22号下午两点", "<rollback-a@test>", sent_at=now
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{"intent": "reschedule", "new_time": "2026-04-22 14:00", "summary": "甲改期"}],
        ), mock.patch.object(cmd_reschedule_request, "_send_ack_email", return_value=5555), \
             mock.patch.object(cmd_reschedule_request, "_spawn_calendar_delete_bg", return_value=6666), \
             mock.patch("subprocess.run", side_effect=subprocess_result_from_call_main):
            rc = daily_exam_review.main(["--auto", "--reschedule-scan-only"])

        self.assertEqual(rc, 0)
        cand_a = scenario.candidate(tid_a)
        cand_b = scenario.candidate(tid_b)
        self.assertEqual(cand_a["stage"], "ROUND2_SCHEDULING")
        self.assertEqual(cand_a["round2_time"], "2026-04-20 14:00")
        self.assertEqual(cand_a["round2_confirm_status"], "PENDING")
        self.assertEqual(cand_b["stage"], "ROUND2_SCHEDULED")
        self.assertEqual(cand_b["round2_time"], "2026-04-21 15:00")
        self.assertEqual(cand_b["round2_confirm_status"], "CONFIRMED")

    def test_main_reschedule_scan_matches_multiple_candidates_and_updates_each_correctly(self):
        import daily_exam_review
        import cmd_reschedule_request

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid_a = scenario.create_confirmed_round2_candidate(
            name="多候选甲", email="many-a@example.com", round2_time="2026-04-20 14:00"
        )
        tid_b = scenario.create_confirmed_round2_candidate(
            name="多候选乙", email="many-b@example.com", round2_time="2026-04-21 15:00"
        )
        scenario.set_invite_sent_at(tid_a, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.set_invite_sent_at(tid_b, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_now(make_reply_email(
            "many-a@example.com", "Re: 二面安排", "想改到22号下午两点", "<many-a@test>", sent_at=now
        ))
        scenario.mailbox.deliver_now(make_reply_email(
            "many-b@example.com", "Re: 二面安排", "想改到23号上午十点", "<many-b@test>", sent_at=now
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[
                {"intent": "reschedule", "new_time": "2026-04-22 14:00", "summary": "甲改期"},
                {"intent": "reschedule", "new_time": "2026-04-23 10:00", "summary": "乙改期"},
            ],
        ), mock.patch.object(cmd_reschedule_request, "_send_ack_email", return_value=5555), \
             mock.patch.object(cmd_reschedule_request, "_spawn_calendar_delete_bg", return_value=6666), \
             mock.patch("subprocess.run", side_effect=subprocess_result_from_call_main):
            rc = daily_exam_review.main(["--auto", "--reschedule-scan-only"])

        self.assertEqual(rc, 0)
        cand_a = scenario.candidate(tid_a)
        cand_b = scenario.candidate(tid_b)
        self.assertEqual(cand_a["stage"], "ROUND2_SCHEDULING")
        self.assertEqual(cand_b["stage"], "ROUND2_SCHEDULING")
        self.assertEqual(cand_a["round2_time"], "2026-04-20 14:00")
        self.assertEqual(cand_b["round2_time"], "2026-04-21 15:00")
        self.assertEqual(cand_a["audit"][-1]["payload"]["new_time_proposed"], "2026-04-22 14:00")
        self.assertEqual(cand_b["audit"][-1]["payload"]["new_time_proposed"], "2026-04-23 10:00")

    def test_main_reschedule_scan_defer_moves_correct_candidate_to_wait_return(self):
        import daily_exam_review
        import interview.cmd_defer as cmd_defer

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid_a = scenario.create_confirmed_round2_candidate(
            name="暂缓甲", email="defer-a@example.com", round2_time="2026-04-20 14:00"
        )
        tid_b = scenario.create_confirmed_round2_candidate(
            name="暂缓乙", email="defer-b@example.com", round2_time="2026-04-21 15:00"
        )
        scenario.set_invite_sent_at(tid_a, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.set_invite_sent_at(tid_b, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_now(make_reply_email(
            "defer-a@example.com", "Re: 二面安排", "我人在美国，回国后再约", "<defer-a@test>", sent_at=now
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{"intent": "defer_until_shanghai", "new_time": None, "summary": "人在美国"}],
        ), mock.patch.object(cmd_defer, "_send_defer_email", return_value=1234), \
             mock.patch.object(cmd_defer, "_spawn_calendar_delete_bg", return_value=2345), \
             mock.patch("subprocess.run", side_effect=subprocess_result_from_call_main):
            rc = daily_exam_review.main(["--auto", "--reschedule-scan-only"])

        self.assertEqual(rc, 0)
        cand_a = scenario.candidate(tid_a)
        cand_b = scenario.candidate(tid_b)
        self.assertEqual(cand_a["stage"], "WAIT_RETURN")
        self.assertEqual(cand_a["wait_return_round"], 2)
        self.assertIsNone(cand_a["round2_time"])
        self.assertEqual(cand_b["stage"], "ROUND2_SCHEDULED")
        self.assertEqual(cand_b["wait_return_round"], None)

    def test_main_reschedule_scan_defer_matches_multiple_candidates_across_rounds(self):
        import daily_exam_review
        import interview.cmd_defer as cmd_defer

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid_r1 = scenario.create_confirmed_round1_candidate(
            name="一面暂缓人", email="defer-r1@example.com", round1_time="2026-04-20 10:00"
        )
        tid_r2 = scenario.create_confirmed_round2_candidate(
            name="二面暂缓人", email="defer-r2@example.com", round2_time="2026-04-21 15:00"
        )
        scenario.set_invite_sent_at(tid_r1, 1, (now - dt.timedelta(hours=1)).isoformat())
        scenario.set_invite_sent_at(tid_r2, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_now(make_reply_email(
            "defer-r1@example.com", "Re: 一面安排", "我不在国内，回来后再约", "<defer-r1@test>", sent_at=now
        ))
        scenario.mailbox.deliver_now(make_reply_email(
            "defer-r2@example.com", "Re: 二面安排", "我不在国内，回来后再约", "<defer-r2@test>", sent_at=now
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[
                {"intent": "defer_until_shanghai", "new_time": None, "summary": "一面暂缓"},
                {"intent": "defer_until_shanghai", "new_time": None, "summary": "二面暂缓"},
            ],
        ), mock.patch.object(cmd_defer, "_send_defer_email", side_effect=[1234, 3456]), \
             mock.patch.object(cmd_defer, "_spawn_calendar_delete_bg", side_effect=[2345, 4567]), \
             mock.patch("subprocess.run", side_effect=subprocess_result_from_call_main):
            rc = daily_exam_review.main(["--auto", "--reschedule-scan-only"])

        self.assertEqual(rc, 0)
        cand_r1 = scenario.candidate(tid_r1)
        cand_r2 = scenario.candidate(tid_r2)
        self.assertEqual(cand_r1["stage"], "WAIT_RETURN")
        self.assertEqual(cand_r1["wait_return_round"], 1)
        self.assertEqual(cand_r2["stage"], "WAIT_RETURN")
        self.assertEqual(cand_r2["wait_return_round"], 2)

    def test_multi_round_negotiation_with_interleaved_candidates(self):
        import daily_exam_review
        import interview.cmd_confirm as cmd_confirm
        import cmd_reschedule_request

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid_a = scenario.create_round2_pending_candidate(
            name="协商甲", email="negotiate-a@example.com", round2_time="2026-04-20 14:00"
        )
        tid_b = scenario.create_round2_pending_candidate(
            name="协商乙", email="negotiate-b@example.com", round2_time="2026-04-21 15:00"
        )
        scenario.set_invite_sent_at(tid_a, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.set_invite_sent_at(tid_b, 2, (now - dt.timedelta(hours=1)).isoformat())

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[
                {"intent": "confirm", "new_time": None, "summary": "甲确认"},
                {"intent": "reschedule", "new_time": "2026-04-23 16:00", "summary": "乙改期"},
            ],
        ):
            first = daily_exam_review.main(["--auto", "--interview-confirm-only"])
            scenario.mailbox.deliver_now(make_reply_email(
                "negotiate-a@example.com", "Re: 二面安排", "我可以参加", "<negotiate-a-confirm@test>", sent_at=now
            ))
            scenario.mailbox.deliver_now(make_reply_email(
                "negotiate-b@example.com", "Re: 二面安排", "我想改到23号下午四点", "<negotiate-b-resched@test>", sent_at=now
            ))
            second = daily_exam_review.main(["--auto", "--interview-confirm-only"])

        self.assertEqual(first, 0)
        self.assertEqual(second, 0)
        scenario.assert_boss_pending(tid_a, 2, "2026-04-20 14:00")
        scenario.assert_boss_pending(tid_b, 2, "2026-04-23 16:00")

        with mock.patch.object(cmd_confirm, "_spawn_calendar_bg", return_value=2468):
            out, err, rc = call_main("interview.cmd_confirm", ["--talent-id", tid_a, "--round", "2"])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))

        out, err, rc = call_main("interview.cmd_reschedule", [
            "--talent-id", tid_b, "--round", "2", "--time", "2026-04-23 16:00", "--no-confirm",
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))

        later = now + dt.timedelta(minutes=10)
        scenario.mailbox.deliver_now(make_reply_email(
            "negotiate-a@example.com", "Re: 二面安排", "我临时有事，想改到24号下午三点", "<negotiate-a-resched@test>", sent_at=later
        ))
        scenario.mailbox.deliver_now(make_reply_email(
            "negotiate-b@example.com", "Re: 二面安排", "23号下午四点可以", "<negotiate-b-confirm@test>", sent_at=later
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{"intent": "confirm", "new_time": None, "summary": "乙确认"}],
        ):
            rc = daily_exam_review.main(["--auto", "--interview-confirm-only"])
        self.assertEqual(rc, 0)
        scenario.assert_boss_pending(tid_b, 2, "2026-04-23 16:00")

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{"intent": "reschedule", "new_time": "2026-04-24 15:00", "summary": "甲再次改期"}],
        ), mock.patch.object(cmd_reschedule_request, "_send_ack_email", return_value=5555), \
             mock.patch.object(cmd_reschedule_request, "_spawn_calendar_delete_bg", return_value=6666), \
             mock.patch("subprocess.run", side_effect=subprocess_result_from_call_main):
            rc = daily_exam_review.main(["--auto", "--reschedule-scan-only"])
        self.assertEqual(rc, 0)

        cand_a = scenario.candidate(tid_a)
        cand_b = scenario.candidate(tid_b)
        self.assertEqual(cand_a["stage"], "ROUND2_SCHEDULING")
        self.assertEqual(cand_a["round2_time"], "2026-04-20 14:00")
        self.assertEqual(cand_b["stage"], "ROUND2_SCHEDULING")
        self.assertEqual(cand_b["round2_time"], "2026-04-23 16:00")

    def test_wait_return_resume_then_candidate_can_continue_confirmation_flow(self):
        import daily_exam_review
        import cmd_round2_defer

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid = scenario.create_confirmed_round2_candidate(
            name="恢复后继续人", email="resume-flow@example.com", round2_time="2026-04-20 14:00"
        )

        with mock.patch.object(cmd_round2_defer, "_send_defer_email", return_value=7777), \
             mock.patch.object(cmd_round2_defer, "_spawn_calendar_delete_bg", return_value=8888):
            out, err, rc = call_main("cmd_round2_defer", ["--talent-id", tid, "--reason", "人在美国"])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))

        out, err, rc = call_main("cmd_wait_return_resume", ["--talent-id", tid])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        out, err, rc = call_main("interview.cmd_reschedule", [
            "--talent-id", tid, "--round", "2", "--time", "2026-04-25 11:00", "--no-confirm",
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))

        scenario.set_invite_sent_at(tid, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_now(make_reply_email(
            "resume-flow@example.com", "Re: 二面安排", "25号11点可以", "<resume-flow-confirm@test>", sent_at=now
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{"intent": "confirm", "new_time": None, "summary": "恢复后确认"}],
        ):
            rc = daily_exam_review.main(["--auto", "--interview-confirm-only"])
        self.assertEqual(rc, 0)
        scenario.assert_boss_pending(tid, 2, "2026-04-25 11:00")

    def test_main_reschedule_scan_latest_valid_email_wins_for_confirmed_candidate(self):
        import daily_exam_review
        import interview.cmd_defer as cmd_defer

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid = scenario.create_confirmed_round2_candidate(
            name="最新邮件优先人", email="latest-final@example.com", round2_time="2026-04-20 14:00"
        )
        scenario.set_invite_sent_at(tid, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_now(make_reply_email(
            "latest-final@example.com",
            "Re: 二面安排",
            "我想改到4月24日下午三点。",
            "<older-resched@test>",
            sent_at=now - dt.timedelta(minutes=10),
        ))
        scenario.mailbox.deliver_now(make_reply_email(
            "latest-final@example.com",
            "Re: 二面安排",
            "我人在美国，回国后再约。",
            "<newer-defer@test>",
            sent_at=now,
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{"intent": "defer_until_shanghai", "new_time": None, "summary": "最新邮件要求暂缓"}],
        ), mock.patch.object(cmd_defer, "_send_defer_email", return_value=7777), \
             mock.patch.object(cmd_defer, "_spawn_calendar_delete_bg", return_value=8888), \
             mock.patch("subprocess.run", side_effect=subprocess_result_from_call_main):
            rc = daily_exam_review.main(["--auto", "--reschedule-scan-only"])

        self.assertEqual(rc, 0)
        cand = scenario.candidate(tid)
        self.assertEqual(cand["stage"], "WAIT_RETURN")
        self.assertEqual(cand["wait_return_round"], 2)
        self.assertIsNone(cand["round2_time"])
        self.assertEqual(cand["audit"][-1]["action"], "round2_deferred_until_return")

    def test_main_reschedule_scan_request_online_does_not_rollback_state(self):
        import daily_exam_review

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid = scenario.create_confirmed_round2_candidate(
            name="线上请求人", email="online-only@example.com", round2_time="2026-04-20 14:00"
        )
        scenario.set_invite_sent_at(tid, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_now(make_reply_email(
            "online-only@example.com",
            "Re: 二面安排",
            "我人在外地，希望改为线上面试。",
            "<online-only@test>",
            sent_at=now,
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{"intent": "request_online", "new_time": None, "summary": "希望线上面试"}],
        ):
            rc = daily_exam_review.main(["--auto", "--reschedule-scan-only"])

        self.assertEqual(rc, 0)
        cand = scenario.candidate(tid)
        self.assertEqual(cand["stage"], "ROUND2_SCHEDULED")
        self.assertEqual(cand["round2_confirm_status"], "CONFIRMED")
        self.assertEqual(cand["round2_time"], "2026-04-20 14:00")
        scenario.assert_last_email_id_updated(tid, "round2", "<online-only@test>")

    def test_main_reschedule_scan_same_time_mail_does_not_rollback_state(self):
        import daily_exam_review

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid = scenario.create_confirmed_round2_candidate(
            name="同时间干扰人", email="same-time@example.com", round2_time="2026-04-20 14:00"
        )
        scenario.set_invite_sent_at(tid, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_now(make_reply_email(
            "same-time@example.com",
            "Re: 二面安排",
            "这边时间没变化，还是 2026-04-20 14:00。",
            "<same-time@test>",
            sent_at=now,
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{
                "intent": "reschedule",
                "new_time": "2026-04-20 14:00",
                "summary": "希望维持 2026-04-20 14:00",
            }],
        ), mock.patch("subprocess.run") as run_mock:
            rc = daily_exam_review.main(["--auto", "--reschedule-scan-only"])

        self.assertEqual(rc, 0)
        run_mock.assert_not_called()
        cand = scenario.candidate(tid)
        self.assertEqual(cand["stage"], "ROUND2_SCHEDULED")
        self.assertEqual(cand["round2_confirm_status"], "CONFIRMED")
        self.assertEqual(cand["round2_time"], "2026-04-20 14:00")
        scenario.assert_last_email_id_updated(tid, "round2", "<same-time@test>")
        self.assertTrue(scenario.sent_reports)
        self.assertIn("未自动撤销确认", scenario.sent_reports[-1])

    def test_wait_return_resume_round1(self):
        tid = _setup_confirmed_r1()
        import cmd_round1_defer

        with mock.patch.object(cmd_round1_defer, "_send_defer_email", return_value=7777):
            out, err, rc = call_main("cmd_round1_defer", [
                "--talent-id", tid,
                "--reason", "候选人暂时在美国",
            ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))

        out, err, rc = call_main("cmd_wait_return_resume", ["--talent-id", tid])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        cand = load_candidate(tid)
        self.assertEqual(cand["stage"], "ROUND1_SCHEDULING")
        self.assertIsNone(cand["wait_return_round"])
        self.assertEqual(cand["audit"][-1]["action"], "wait_return_resumed")
        self.assertEqual(cand["audit"][-1]["payload"]["round"], 1)

    def test_wait_return_resume_round2(self):
        tid = _setup_confirmed_r2()
        import cmd_round2_defer

        with mock.patch.object(cmd_round2_defer, "_send_defer_email", return_value=7777):
            out, err, rc = call_main("cmd_round2_defer", [
                "--talent-id", tid,
                "--reason", "候选人暂时在美国",
            ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))

        out, err, rc = call_main("cmd_wait_return_resume", ["--talent-id", tid])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        cand = load_candidate(tid)
        self.assertEqual(cand["stage"], "ROUND2_SCHEDULING")
        self.assertIsNone(cand["wait_return_round"])
        self.assertEqual(cand["audit"][-1]["action"], "wait_return_resumed")
        self.assertEqual(cand["audit"][-1]["payload"]["round"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
