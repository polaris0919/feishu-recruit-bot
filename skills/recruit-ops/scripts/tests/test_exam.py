#!/usr/bin/env python3
"""笔试相关测试：cmd_exam_result / exam_prereview / daily_exam_review。"""
import os
import sys
import types
import unittest
import datetime as dt
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from email.utils import format_datetime
from unittest import mock

from tests.helpers import call_main, new_candidate, wipe_state
from tests.scenario_helpers import FakeMailbox, ScenarioRunner, make_reply_email


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
        import core_state

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
        import cmd_exam_result
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
        self.assertIn("ROUND1_DONE_REJECT_KEEP", st_out)

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


class TestExamPrereview(unittest.TestCase):

    def setUp(self):
        import exam_prereview
        self.mod = exam_prereview

    def test_analyze_response_time_normal(self):
        result = self.mod.analyze_response_time(
            "2026-03-15 10:00:00", "2026-03-16 08:00:00"
        )
        self.assertTrue(result["available"])
        self.assertIn("正常", result["label"])

    def test_analyze_response_time_too_fast(self):
        result = self.mod.analyze_response_time(
            "2026-03-15 10:00:00", "2026-03-15 10:30:00"
        )
        self.assertTrue(result["available"])
        self.assertIn("极快", result["label"])

    def test_analyze_response_time_overtime(self):
        result = self.mod.analyze_response_time(
            "2026-03-10 10:00:00", "2026-03-15 10:00:00"
        )
        self.assertTrue(result["available"])
        self.assertIn("超时", result["label"])

    def test_analyze_response_time_missing(self):
        result = self.mod.analyze_response_time(None, None)
        self.assertFalse(result["available"])

    def test_code_quality_no_code(self):
        result = self.mod.analyze_code_quality("")
        self.assertFalse(result["has_code"])
        self.assertEqual(result["score"], 0)

    def test_code_quality_good_code(self):
        code = (
            "import pandas as pd\nimport numpy as np\n\n"
            "def clean(df):\n    \"\"\"清洗数据\"\"\"\n    return df.dropna()\n\n"
            "def analyze(df):\n    return df.groupby('x').sum()\n\n"
            "def main():\n    df = pd.read_csv('data.csv')\n    df = clean(df)\n"
            "    result = analyze(df)\n    result.to_csv('out.csv')\n    print(result)\n\n"
            "if __name__ == '__main__':\n    main()\n"
        )
        result = self.mod.analyze_code_quality(code)
        self.assertTrue(result["has_code"])
        self.assertGreater(result["score"], 50)
        self.assertIn("pandas", result["metrics"]["data_libs"])

    def test_code_quality_detects_eval(self):
        code = "x = eval(input())\n"
        result = self.mod.analyze_code_quality(code)
        self.assertTrue(any("eval" in w for w in result["warnings"]))

    def test_code_quality_detects_except_pass(self):
        code = "try:\n    x = 1\nexcept:\n    pass\n"
        result = self.mod.analyze_code_quality(code)
        self.assertTrue(any("except" in w.lower() for w in result["warnings"]))

    def test_completeness_code_and_result(self):
        attachments = [
            {"filename": "solution.py", "size": 1024, "is_text": True},
            {"filename": "output.csv", "size": 512, "is_text": True},
        ]
        result = self.mod.analyze_completeness(
            attachments,
            "您好，我已完成笔试题目，代码在附件中，输出结果也一并附上，请查收。",
        )
        self.assertEqual(result["total_attachments"], 2)
        self.assertIn("solution.py", result["code_files"])
        self.assertIn("output.csv", result["result_files"])
        self.assertTrue(result["has_body_text"])

    def test_completeness_no_files(self):
        result = self.mod.analyze_completeness([], "")
        self.assertEqual(result["total_attachments"], 0)
        self.assertEqual(result["code_files"], [])
        self.assertFalse(result["has_body_text"])

    def test_run_prereview_full(self):
        email_data = {
            "sender": "candidate@example.com",
            "subject": "Re: 【笔试邀请】",
            "date": "2026-03-16 10:00:00",
            "body_text": "您好，已完成笔试，请查收附件。",
            "code_text": (
                "import pandas as pd\ndef analyze(df):\n    return df.dropna()\n"
                "def main():\n    df = pd.read_csv('data.csv')\n"
                "    r = analyze(df)\n    r.to_csv('out.csv')\n    print(r)\n"
                "if __name__ == '__main__':\n    main()\n"
            ),
            "attachment_info_list": [
                {"filename": "solution.py", "size": 500, "is_text": True},
                {"filename": "output.csv", "size": 200, "is_text": True},
            ],
        }
        cand_info = {
            "talent_id": "t_test01",
            "candidate_name": "张三",
            "exam_sent_at": "2026-03-15 10:00:00",
            "exam_id": "exam-t_test01-20260315",
        }
        result = self.mod.run_prereview(email_data, cand_info)
        self.assertIn("score", result)
        self.assertIn("report_text", result)
        self.assertIn("db_summary", result)
        self.assertIn("📋 笔试预审报告", result["report_text"])
        self.assertIn("t_test01", result["report_text"])
        self.assertIn("[自动预审]", result["db_summary"])
        self.assertGreater(result["score"], 0)


class TestDailyExamReview(unittest.TestCase):

    def setUp(self):
        wipe_state()

    @staticmethod
    def _make_exam_mail(from_addr, subject, body, message_id, sent_at, attachments):
        msg = MIMEMultipart()
        msg["From"] = from_addr
        msg["To"] = "recruit@test.com"
        msg["Subject"] = subject
        msg["Message-ID"] = message_id
        msg["Date"] = format_datetime(sent_at)
        msg.attach(MIMEText(body, _charset="utf-8"))
        for filename, content, mime in attachments:
            maintype, subtype = mime.split("/", 1)
            part = MIMEBase(maintype, subtype)
            part.set_payload(content.encode("utf-8"))
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(part)
        return msg.as_bytes()

    def test_scan_no_imap_config(self):
        """无 IMAP 配置时应静默返回空列表。"""
        import daily_exam_review
        old_host = os.environ.pop("RECRUIT_EXAM_IMAP_HOST", None)
        try:
            results = daily_exam_review.scan_new_replies(auto_mode=True)
            self.assertIsInstance(results, list)
        finally:
            if old_host:
                os.environ["RECRUIT_EXAM_IMAP_HOST"] = old_host

    def test_scan_new_replies_skips_old_exam_email_before_exam_sent_at(self):
        import daily_exam_review

        tid = new_candidate(name="笔试旧邮件过滤人", email="examfilter@example.com")
        out, err, rc = call_main("interview.cmd_result", [
            "--talent-id", tid,
            "--result", "pass",
            "--email", "examfilter@example.com",
            "--skip-email",
            "--round", "1",
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))

        from core_state import load_candidate
        cand = load_candidate(tid)
        exam_id = cand["exam_id"]
        exam_sent_at = dt.datetime.fromisoformat(cand["exam_sent_at"])

        mailbox = FakeMailbox()
        mailbox.deliver_now(self._make_exam_mail(
            "examfilter@example.com",
            "Re: {} submission".format(exam_id),
            "这是旧邮件\n\n{}".format(exam_id),
            "<exam-old@test>",
            exam_sent_at - dt.timedelta(hours=2),
            [
                ("answer.py", "print('old')\n", "text/plain"),
                ("result.csv", "x\n1\n", "text/csv"),
            ],
        ))
        mailbox.deliver_now(self._make_exam_mail(
            "examfilter@example.com",
            "Re: {} submission".format(exam_id),
            "这是新邮件\n\n{}".format(exam_id),
            "<exam-real@test>",
            exam_sent_at + dt.timedelta(hours=3),
            [
                ("answer.py", "import pandas as pd\nprint('real')\n", "text/plain"),
                ("result.csv", "x\n2\n", "text/csv"),
            ],
        ))

        with mock.patch.object(daily_exam_review, "connect_imap", side_effect=mailbox.connect), \
             mock.patch.object(daily_exam_review, "_lookup_exam_sent_at_from_sent", return_value=None):
            results = daily_exam_review.scan_new_replies(auto_mode=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["message_id"], "<exam-real@test>")

        cand = load_candidate(tid)
        self.assertEqual(cand["exam_last_email_id"], "<exam-real@test>")
        self.assertEqual(cand["stage"], "EXAM_REVIEWED")
        reviewed = [ev for ev in cand["audit"] if ev.get("action") == "exam_reviewed_auto"]
        self.assertEqual(len(reviewed), 1)

    def test_format_report_uses_prereview(self):
        """format_report 应优先使用 prereview.report_text。"""
        import daily_exam_review
        fake_result = {
            "sender": "test@example.com",
            "subject": "Re: 笔试",
            "date": "2026-03-16 10:00:00",
            "exam_id": "exam-t_001-20260315",
            "prereview": {
                "report_text": "📋 笔试预审报告 | 候选人 t_001",
                "score": 75,
                "db_summary": "[自动预审] 用时正常 | 预审分:75",
            },
        }
        report = daily_exam_review.format_report(fake_result)
        self.assertIn("📋 笔试预审报告", report)

    def test_format_report_fallback(self):
        """无预审结果时 format_report 降级到简单格式。"""
        import daily_exam_review
        fake_result = {
            "sender": "x@example.com",
            "subject": "笔试回复",
            "date": "2026-03-16 10:00:00",
            "exam_id": None,
            "prereview": None,
        }
        report = daily_exam_review.format_report(fake_result)
        self.assertIn("新笔试回复", report)

    def test_request_online_report_guides_boss_to_switch_mode(self):
        import daily_exam_review

        report = daily_exam_review.format_interview_confirmation_report({
            "round": 2,
            "talent_id": "t_demo",
            "candidate_name": "测试人",
            "interview_time": "2026-04-01 14:00",
            "intent": "request_online",
            "summary": "候选人表示人在海外，希望改为线上面试",
        })
        self.assertIn("希望改为线上面试", report)
        self.assertIn("interview/cmd_reschedule.py", report)

    def test_defer_report_guides_boss_to_wait_return(self):
        import daily_exam_review

        report = daily_exam_review.format_interview_confirmation_report({
            "round": 2,
            "talent_id": "t_demo",
            "candidate_name": "测试人",
            "interview_time": "2026-04-01 14:00",
            "intent": "defer_until_shanghai",
            "summary": "候选人暂时不在上海，之后再约",
        })
        self.assertIn("之后再约", report)
        self.assertIn("cmd_round2_defer.py", report)
        self.assertIn("WAIT_RETURN", report)

    def test_main_auto_defers_round2_candidate(self):
        import daily_exam_review

        item = {
            "round": 2,
            "talent_id": "t_demo",
            "candidate_name": "测试人",
            "interview_time": "2026-04-01 14:00",
            "intent": "defer_until_shanghai",
            "summary": "候选人暂时不在上海，之后再约",
        }
        fake_proc = types.SimpleNamespace(
            stdout="[二面暂缓安排]\n- 当前阶段: WAIT_RETURN".encode("utf-8"),
            stderr=b"",
        )
        with mock.patch.object(daily_exam_review, "scan_round1_confirmations", return_value=[]), \
             mock.patch.object(daily_exam_review, "scan_round2_confirmations", return_value=[item]), \
             mock.patch("subprocess.run", return_value=fake_proc) as run_mock, \
             mock.patch.dict(sys.modules, {"feishu": types.SimpleNamespace(send_text=lambda text: True)}):
            rc = daily_exam_review.main(["--auto", "--interview-confirm-only"])

        self.assertEqual(rc, 0)
        called = run_mock.call_args[0][0]
        self.assertIn("cmd_round2_defer.py", called[1])
        self.assertIn("--talent-id", called)
        self.assertIn("t_demo", called)

    @mock.patch.dict(os.environ, {
        "RECRUIT_EXAM_IMAP_HOST": "imap.test.com",
        "RECRUIT_EXAM_IMAP_USER": "test@test.com",
        "RECRUIT_EXAM_IMAP_PASS": "pass",
    })
    def test_scan_interview_confirmations_local_from_matching(self):
        """扫描逻辑应通过本地 From 头精确匹配，不误归因其他候选人的邮件。"""
        from email.mime.text import MIMEText
        import daily_exam_review

        target_email = "candidate_a@example.com"
        other_email = "candidate_b@example.com"

        def _make_email(from_addr, subject, body, message_id):
            msg = MIMEText(body)
            msg["From"] = from_addr
            msg["Subject"] = subject
            msg["Message-ID"] = message_id
            return msg.as_bytes()

        email_from_other = _make_email(other_email, "Re: 面试", "我确认", "<other@test>")
        email_from_target = _make_email(target_email, "Re: 面试", "好的确认时间", "<target@test>")

        fake_pending = [{
            "talent_id": "t_test_a",
            "candidate_email": target_email,
            "candidate_name": "候选人A",
            "round2_time": "2026-04-01 14:00",
            "round2_invite_sent_at": None,
            "round2_confirm_status": "PENDING",
            "round2_calendar_event_id": None,
            "round2_last_email_id": None,
        }]

        class FakeIMAP:
            def select(self, folder):
                return ("OK", [b"1"])
            def search(self, charset, criterion):
                return ("OK", [b"1 2"])
            def fetch(self, mid, parts):
                if mid == b"2":
                    return ("OK", [(b"1 (RFC822 {100})", email_from_target)])
                return ("OK", [(b"1 (RFC822 {100})", email_from_other)])
            def logout(self):
                pass

        with mock.patch.object(daily_exam_review, "connect_imap", return_value=FakeIMAP()), \
             mock.patch("talent_db.get_pending_confirmations", return_value=fake_pending), \
             mock.patch("talent_db._is_enabled", return_value=True), \
             mock.patch("talent_db.update_last_email_id"), \
             mock.patch.object(daily_exam_review, "_llm_analyze_reply",
                               return_value={"intent": "confirm", "new_time": None,
                                             "summary": "确认参加"}):
            results = daily_exam_review._scan_interview_confirmations(round_num=2, auto_mode=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["talent_id"], "t_test_a")
        self.assertEqual(results[0]["candidate_email"], target_email)
        self.assertEqual(results[0]["message_id"], "<target@test>")
        self.assertEqual(results[0]["intent"], "confirm")

    def test_scan_interview_confirmations_delayed_reply_on_second_scan(self):
        import daily_exam_review

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid = scenario.create_round2_pending_candidate(
            name="延迟确认人", email="delay@example.com", round2_time="2026-04-20 14:00"
        )
        scenario.set_invite_sent_at(tid, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_on_scan(2, make_reply_email(
            "delay@example.com",
            "Re: 二面安排",
            "好的，我确认参加。",
            "<delay-confirm@test>",
            sent_at=now,
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{"intent": "confirm", "new_time": None, "summary": "确认参加"}],
        ):
            first = daily_exam_review._scan_interview_confirmations(round_num=2, auto_mode=True)
            second = daily_exam_review._scan_interview_confirmations(round_num=2, auto_mode=True)

        self.assertEqual(first, [])
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0]["talent_id"], tid)
        self.assertEqual(second[0]["intent"], "confirm")
        scenario.assert_last_email_id_updated(tid, "round2", "<delay-confirm@test>")

    def test_scan_reschedule_requests_delayed_reply_on_second_scan(self):
        import daily_exam_review

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid = scenario.create_confirmed_round2_candidate(
            name="延迟改期人", email="rescan@example.com", round2_time="2026-04-20 14:00"
        )
        scenario.set_invite_sent_at(tid, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_on_scan(2, make_reply_email(
            "rescan@example.com",
            "Re: 二面安排",
            "我4月20日不方便，能否改到4月22日下午两点？",
            "<delay-resched@test>",
            sent_at=now,
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{"intent": "reschedule", "new_time": "2026-04-22 14:00", "summary": "希望改期"}],
        ):
            first = daily_exam_review.scan_round2_reschedule_requests(auto_mode=True)
            second = daily_exam_review.scan_round2_reschedule_requests(auto_mode=True)

        self.assertEqual(first, [])
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0]["talent_id"], tid)
        self.assertEqual(second[0]["new_time"], "2026-04-22 14:00")
        scenario.assert_last_email_id_updated(tid, "round2", "<delay-resched@test>")

    def test_scan_reschedule_requests_matches_multiple_candidates(self):
        import daily_exam_review

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid_a = scenario.create_confirmed_round2_candidate(
            name="候选人甲", email="multi-a@example.com", round2_time="2026-04-20 14:00"
        )
        tid_b = scenario.create_confirmed_round2_candidate(
            name="候选人乙", email="multi-b@example.com", round2_time="2026-04-21 15:00"
        )
        scenario.set_invite_sent_at(tid_a, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.set_invite_sent_at(tid_b, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_now(make_reply_email(
            "multi-a@example.com", "Re: 二面安排", "我想改到22号下午两点", "<multi-a@test>", sent_at=now
        ))
        scenario.mailbox.deliver_now(make_reply_email(
            "multi-b@example.com", "Re: 二面安排", "我想改到23号上午十点", "<multi-b@test>", sent_at=now
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[
                {"intent": "reschedule", "new_time": "2026-04-22 14:00", "summary": "甲改期"},
                {"intent": "reschedule", "new_time": "2026-04-23 10:00", "summary": "乙改期"},
            ],
        ):
            results = daily_exam_review.scan_round2_reschedule_requests(auto_mode=True)

        self.assertEqual(len(results), 2)
        by_tid = {item["talent_id"]: item for item in results}
        self.assertEqual(by_tid[tid_a]["candidate_email"], "multi-a@example.com")
        self.assertEqual(by_tid[tid_a]["new_time"], "2026-04-22 14:00")
        self.assertEqual(by_tid[tid_b]["candidate_email"], "multi-b@example.com")
        self.assertEqual(by_tid[tid_b]["new_time"], "2026-04-23 10:00")
        scenario.assert_last_email_id_updated(tid_a, "round2", "<multi-a@test>")
        scenario.assert_last_email_id_updated(tid_b, "round2", "<multi-b@test>")

    def test_scan_interview_confirmations_dedup_by_message_id(self):
        import daily_exam_review

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid = scenario.create_round2_pending_candidate(email="dedup@example.com")
        scenario.set_invite_sent_at(tid, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_now(make_reply_email(
            "dedup@example.com",
            "Re: 二面安排",
            "确认参加",
            "<dedup@test>",
            sent_at=now,
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{"intent": "confirm", "new_time": None, "summary": "确认参加"}],
        ):
            first = daily_exam_review._scan_interview_confirmations(round_num=2, auto_mode=True)
            second = daily_exam_review._scan_interview_confirmations(round_num=2, auto_mode=True)

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        scenario.assert_last_email_id_updated(tid, "round2", "<dedup@test>")

    def test_scan_interview_confirmations_skips_old_email_before_invite(self):
        import daily_exam_review

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid = scenario.create_round2_pending_candidate(email="oldmail@example.com")
        scenario.set_invite_sent_at(tid, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_now(make_reply_email(
            "oldmail@example.com",
            "Re: 二面安排",
            "确认参加",
            "<oldmail@test>",
            sent_at=now - dt.timedelta(hours=2),
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{"intent": "confirm", "new_time": None, "summary": "确认参加"}],
        ):
            results = daily_exam_review._scan_interview_confirmations(round_num=2, auto_mode=True)

        self.assertEqual(results, [])
        cand = scenario.candidate(tid)
        self.assertIsNone(cand.get("round2_last_email_id"))

    def test_scan_interview_confirmations_timeout_without_new_email(self):
        import daily_exam_review

        scenario = ScenarioRunner()
        tid = scenario.create_round2_pending_candidate(email="timeout@example.com")
        scenario.set_invite_sent_at(
            tid, 2, (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=72)).isoformat()
        )

        with scenario.patch_daily_exam_review(daily_exam_review):
            results = daily_exam_review._scan_interview_confirmations(round_num=2, auto_mode=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["talent_id"], tid)
        self.assertEqual(results[0]["intent"], "timeout")

    def test_scan_interview_confirmations_skips_auto_reply_and_uses_real_mail(self):
        import daily_exam_review

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid = scenario.create_round2_pending_candidate(email="autoreply@example.com")
        scenario.set_invite_sent_at(tid, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_now(make_reply_email(
            "autoreply@example.com",
            "Re: 二面安排",
            "好的确认参加",
            "<real-confirm@test>",
            sent_at=now - dt.timedelta(minutes=5),
        ))
        scenario.mailbox.deliver_now(make_reply_email(
            "autoreply@example.com",
            "Auto-reply: Re: 二面安排",
            "我现在无法及时回复",
            "<auto-reply@test>",
            sent_at=now,
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{"intent": "confirm", "new_time": None, "summary": "确认参加"}],
        ):
            results = daily_exam_review._scan_interview_confirmations(round_num=2, auto_mode=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["talent_id"], tid)
        self.assertEqual(results[0]["message_id"], "<real-confirm@test>")
        scenario.assert_last_email_id_updated(tid, "round2", "<real-confirm@test>")

    def test_scan_interview_confirmations_latest_valid_email_wins(self):
        import daily_exam_review

        scenario = ScenarioRunner()
        now = dt.datetime.now(dt.timezone.utc)
        tid = scenario.create_round2_pending_candidate(email="latest-win@example.com")
        scenario.set_invite_sent_at(tid, 2, (now - dt.timedelta(hours=1)).isoformat())
        scenario.mailbox.deliver_now(make_reply_email(
            "latest-win@example.com",
            "Re: 二面安排",
            "好的，我可以参加。",
            "<older-confirm@test>",
            sent_at=now - dt.timedelta(minutes=10),
        ))
        scenario.mailbox.deliver_now(make_reply_email(
            "latest-win@example.com",
            "Re: 二面安排",
            "我想改到4月24日下午三点。",
            "<newer-reschedule@test>",
            sent_at=now,
        ))

        with scenario.patch_daily_exam_review(
            daily_exam_review,
            llm_side_effect=[{"intent": "reschedule", "new_time": "2026-04-24 15:00", "summary": "最新邮件要求改期"}],
        ):
            results = daily_exam_review._scan_interview_confirmations(round_num=2, auto_mode=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["talent_id"], tid)
        self.assertEqual(results[0]["intent"], "reschedule")
        self.assertEqual(results[0]["new_time"], "2026-04-24 15:00")
        self.assertEqual(results[0]["message_id"], "<newer-reschedule@test>")
        scenario.assert_last_email_id_updated(tid, "round2", "<newer-reschedule@test>")


if __name__ == "__main__":
    unittest.main(verbosity=2)
