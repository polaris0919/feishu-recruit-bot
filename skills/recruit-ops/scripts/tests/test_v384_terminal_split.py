#!/usr/bin/env python3
"""tests/test_v384_terminal_split.py —— v3.8.4 分权事件回归测试。

【场景 8 (cmd_analyze)】候选人 confirm_interview 邮件不再自动驱动建日历——
  inbox.cmd_analyze 内对 (intent=confirm_interview, stage∈{ROUND1_SCHEDULING,
  ROUND2_SCHEDULING}) 强制 override need_boss_action=True,让推飞书 warn 卡。
  agent 看到卡后等老板飞书命令"OK 建日历"再走 §4.2 chain。

【场景 12 (cmd_scan)】两个终态 ONBOARDED / OFFER_DECLINED_KEEP 的候选人不再
  被 inbox.cmd_scan 拉邮件——_SKIP_STAGES 在 _process_candidate 入口直接 return。
  其他叶子态(EXAM_REJECT_KEEP / ROUND2_DONE_REJECT_KEEP / WAIT_RETURN)仍扫。

不要把这些测试合并到 test_v35_phase1_inbox_general（那里关心 v3.5 prompt 契约,
本文件关心 v3.8.4 决策反转,语义上不重叠）。
"""
from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta
from unittest import mock

import tests.helpers as helpers  # noqa: F401

os.environ["RECRUIT_SUPPRESS_SELF_VERIFY_ALERT"] = "1"


# ════════════════════════════════════════════════════════════════════════════
# 场景 8：cmd_analyze stage-aware need_boss_action override
# ════════════════════════════════════════════════════════════════════════════

class TestConfirmInterviewStageOverride(unittest.TestCase):
    """v3.8.4 场景 8：SCHEDULING 阶段的 confirm_interview 强制推飞书 warn 卡。

    本测试 mock 掉 analyzer.analyze() 直接控制 LLM 输出,只验
    `_analyze_one` 内的 stage-aware override 逻辑。
    """

    def _fake_email_row(self, stage):
        return {
            "email_id": "e_test",
            "talent_id": "t_test",
            "message_id": "<m@local>",
            "subject": "可以的",
            "sender": "candidate@example.com",
            "sent_at": None,
            "context": "interview",
            "stage_at_receipt": stage,
            "body_full": "好的,X 时间可以。",
            "body_excerpt": "好的",
            "candidate_name": "测试候选人",
            "current_stage": stage,
            "attachments": [],
        }

    def _fake_analyzer_result(self, intent="confirm_interview",
                              need_boss=False):
        """模拟 LLM 输出：默认 confirm_interview + need_boss_action=False
        (因为 analyzer.py 的 _NEED_BOSS_INTENTS 不包含 confirm_interview)。"""
        return {
            "intent": intent,
            "summary": "候选人确认时间",
            "need_boss_action": need_boss,
            "urgency": "low",
            "details": {},
            "_meta": {"prompt_name": "inbox_general",
                      "prompt_version": "v35", "model": "qwen3-max"},
        }

    def test_round1_scheduling_confirm_forces_need_boss(self):
        """ROUND1_SCHEDULING + confirm_interview → 即使 LLM 标 need_boss=False,
        cmd_analyze 也强制 override 为 True,推飞书 warn 卡。"""
        from inbox import cmd_analyze
        row = self._fake_email_row("ROUND1_SCHEDULING")
        with mock.patch("inbox.cmd_analyze.analyzer.analyze",
                        return_value=self._fake_analyzer_result(need_boss=False)), \
             mock.patch("inbox.cmd_analyze.talent_db.set_email_analyzed"), \
             mock.patch("inbox.cmd_analyze.assert_email_analyzed"), \
             mock.patch("inbox.cmd_analyze._send_feishu_all_inbound",
                        return_value={"boss": True, "polaris": True}) as feishu_mock:
            res = cmd_analyze._analyze_one(row, dry_run=False, no_feishu=False)

        self.assertEqual(res["intent"], "confirm_interview")
        self.assertTrue(res["need_boss_action"],
                        "v3.8.4 必须强制 override need_boss_action=True")
        self.assertTrue(res["feishu_pushed"])
        feishu_mock.assert_called_once()

    def test_round2_scheduling_confirm_forces_need_boss(self):
        """ROUND2_SCHEDULING + confirm_interview → 同上,二面也分权。"""
        from inbox import cmd_analyze
        row = self._fake_email_row("ROUND2_SCHEDULING")
        with mock.patch("inbox.cmd_analyze.analyzer.analyze",
                        return_value=self._fake_analyzer_result(need_boss=False)), \
             mock.patch("inbox.cmd_analyze.talent_db.set_email_analyzed"), \
             mock.patch("inbox.cmd_analyze.assert_email_analyzed"), \
             mock.patch("inbox.cmd_analyze._send_feishu_all_inbound",
                        return_value={"boss": True, "polaris": True}) as feishu_mock:
            res = cmd_analyze._analyze_one(row, dry_run=False, no_feishu=False)

        self.assertTrue(res["need_boss_action"])
        feishu_mock.assert_called_once()

    def test_round1_scheduled_confirm_not_forced_but_still_notified(self):
        """ROUND1_SCHEDULED 重复确认不提升 need_boss，但仍通知老板 + Polaris。"""
        from inbox import cmd_analyze
        row = self._fake_email_row("ROUND1_SCHEDULED")
        with mock.patch("inbox.cmd_analyze.analyzer.analyze",
                        return_value=self._fake_analyzer_result(need_boss=False)), \
             mock.patch("inbox.cmd_analyze.talent_db.set_email_analyzed"), \
             mock.patch("inbox.cmd_analyze.assert_email_analyzed"), \
             mock.patch("inbox.cmd_analyze._send_feishu_all_inbound",
                        return_value={"boss": True, "polaris": True}) as feishu_mock:
            res = cmd_analyze._analyze_one(row, dry_run=False, no_feishu=False)

        self.assertEqual(res["intent"], "confirm_interview")
        self.assertFalse(res["need_boss_action"],
                         "ROUND1_SCHEDULED 重复确认不该 override")
        self.assertTrue(res["feishu_pushed"])
        self.assertTrue(res["feishu_pushed_boss"])
        self.assertTrue(res["feishu_pushed_polaris"])
        feishu_mock.assert_called_once()

    def test_scheduling_non_confirm_intent_NOT_affected(self):
        """SCHEDULING + 非 confirm_interview intent 不受 override 影响。
        例如 ROUND1_SCHEDULING + question_boss 仍走 analyzer._NEED_BOSS_INTENTS
        的兜底（need_boss=True 是 analyzer 自身行为,不是 cmd_analyze 的 override）。"""
        from inbox import cmd_analyze
        row = self._fake_email_row("ROUND1_SCHEDULING")
        # analyzer 对 question_boss 自己会设 need_boss=True
        analyzer_result = self._fake_analyzer_result(
            intent="question_boss", need_boss=True)
        with mock.patch("inbox.cmd_analyze.analyzer.analyze",
                        return_value=analyzer_result), \
             mock.patch("inbox.cmd_analyze.talent_db.set_email_analyzed"), \
             mock.patch("inbox.cmd_analyze.assert_email_analyzed"), \
             mock.patch("inbox.cmd_analyze._send_feishu_all_inbound",
                        return_value={"boss": True, "polaris": True}):
            res = cmd_analyze._analyze_one(row, dry_run=False, no_feishu=False)

        self.assertEqual(res["intent"], "question_boss")
        self.assertTrue(res["need_boss_action"])

    def test_thanks_fyi_is_also_sent_to_boss_and_polaris(self):
        """v3.8.6：所有 inbound 邮件都通知老板 + Polaris，即使只是 FYI。"""
        from inbox import cmd_analyze
        row = self._fake_email_row("ROUND1_SCHEDULED")
        analyzer_result = self._fake_analyzer_result(
            intent="thanks_fyi", need_boss=False)
        with mock.patch("inbox.cmd_analyze.analyzer.analyze",
                        return_value=analyzer_result), \
             mock.patch("inbox.cmd_analyze.talent_db.set_email_analyzed"), \
             mock.patch("inbox.cmd_analyze.assert_email_analyzed"), \
             mock.patch("inbox.cmd_analyze._send_feishu_all_inbound",
                        return_value={"boss": True, "polaris": True}) as feishu_mock:
            res = cmd_analyze._analyze_one(row, dry_run=False, no_feishu=False)

        self.assertEqual(res["intent"], "thanks_fyi")
        self.assertFalse(res["need_boss_action"])
        self.assertTrue(res["feishu_pushed"])
        self.assertTrue(res["feishu_pushed_boss"])
        self.assertTrue(res["feishu_pushed_polaris"])
        feishu_mock.assert_called_once()

    def test_reschedule_within_24h_forces_decision_card(self):
        """SCHEDULED 阶段 24h 内改期必须升级为老板三选一决策卡。"""
        from inbox import cmd_analyze
        row = self._fake_email_row("ROUND1_SCHEDULED")
        interview_time = datetime.now() + timedelta(hours=3)
        row["round1_time"] = interview_time.strftime("%Y-%m-%d %H:%M")
        row["body_full"] = "抱歉临时有事，今天下午面试能不能改期？"
        row["body_excerpt"] = row["body_full"]
        analyzer_result = self._fake_analyzer_result(
            intent="reschedule_request", need_boss=True)
        analyzer_result["urgency"] = "low"
        analyzer_result["summary"] = "候选人临时请求改期"
        analyzer_result["details"] = {"reason": "临时有事", "new_time": None}
        with mock.patch("inbox.cmd_analyze.analyzer.analyze",
                        return_value=analyzer_result), \
             mock.patch("inbox.cmd_analyze.talent_db.set_email_analyzed"), \
             mock.patch("inbox.cmd_analyze.assert_email_analyzed"), \
             mock.patch("inbox.cmd_analyze._send_feishu_all_inbound",
                        return_value={"boss": True, "polaris": True}) as feishu_mock:
            res = cmd_analyze._analyze_one(row, dry_run=False, no_feishu=False)

        self.assertEqual(res["intent"], "reschedule_request")
        self.assertTrue(res["need_boss_action"])
        text = feishu_mock.call_args.args[0]
        self.assertIn("[候选人临近改期待老板决策]", text)
        self.assertIn("请老板三选一", text)
        self.assertIn("判定为鸽", text)

    def test_reschedule_after_24h_keeps_generic_card(self):
        """超过 24h 的改期仍是普通待老板决策来信卡。"""
        from inbox import cmd_analyze
        row = self._fake_email_row("ROUND1_SCHEDULED")
        interview_time = datetime.now() + timedelta(hours=30)
        row["round1_time"] = interview_time.strftime("%Y-%m-%d %H:%M")
        analyzer_result = self._fake_analyzer_result(
            intent="reschedule_request", need_boss=True)
        analyzer_result["urgency"] = "low"
        analyzer_result["summary"] = "候选人请求改期"
        with mock.patch("inbox.cmd_analyze.analyzer.analyze",
                        return_value=analyzer_result), \
             mock.patch("inbox.cmd_analyze.talent_db.set_email_analyzed"), \
             mock.patch("inbox.cmd_analyze.assert_email_analyzed"), \
             mock.patch("inbox.cmd_analyze._send_feishu_all_inbound",
                        return_value={"boss": True, "polaris": True}) as feishu_mock:
            res = cmd_analyze._analyze_one(row, dry_run=False, no_feishu=False)

        self.assertEqual(res["intent"], "reschedule_request")
        text = feishu_mock.call_args.args[0]
        self.assertIn("[候选人来信待老板决策]", text)
        self.assertNotIn("[候选人临近改期待老板决策]", text)

    def test_exam_submitted_in_exam_sent_triggers_auto_review(self):
        """EXAM_SENT + exam_submitted → 自动跑 AI 笔试评审并写事件。"""
        from inbox import cmd_analyze
        row = self._fake_email_row("EXAM_SENT")
        row["talent_id"] = "t_exam"
        row["attachments"] = [{
            "name": "answer.zip",
            "mime": "application/zip",
            "size": 1024,
            "path": "candidates/t_exam/exam_answer/answer.zip",
            "saved": True,
        }]
        analyzer_result = self._fake_analyzer_result(
            intent="exam_submitted", need_boss=False)
        analyzer_result["summary"] = "候选人回复邮件，但未见提交笔试附件。"
        analyzer_result["details"] = {"has_attachment": False}
        fake_rc = {
            "ok": True,
            "returncode": 0,
            "stdout": "review ok",
            "stderr": "",
            "cmd": ["python", "-m", "exam.cmd_exam_ai_review"],
            "json": None,
        }
        with mock.patch("inbox.cmd_analyze.analyzer.analyze",
                        return_value=analyzer_result), \
             mock.patch("inbox.cmd_analyze.talent_db.set_email_analyzed"), \
             mock.patch("inbox.cmd_analyze.assert_email_analyzed"), \
             mock.patch("inbox.cmd_analyze._send_feishu_all_inbound",
                        return_value={"boss": True, "polaris": True}), \
             mock.patch("inbox.cmd_analyze.run_module",
                        return_value=fake_rc) as run_mock:
            res = cmd_analyze._analyze_one(row, dry_run=False, no_feishu=False)

        run_mock.assert_called_once_with(
            "exam.cmd_exam_ai_review",
            ["--talent-id", "t_exam", "--save-event", "--feishu"],
            timeout=cmd_analyze._EXAM_REVIEW_TIMEOUT_SEC,
            parse_json=False,
        )
        self.assertTrue(res["exam_review_triggered"])
        self.assertTrue(res["exam_review_ok"])
        self.assertTrue(res["exam_review"]["ok"])

    def test_exam_submitted_attachment_metadata_corrects_llm_payload_and_card(self):
        """真实附件优先于 LLM 对 has_attachment 的误判，并展示在飞书卡片。"""
        from inbox import cmd_analyze
        row = self._fake_email_row("EXAM_SENT")
        row["attachments"] = [{
            "name": "黄琪笔试答案.zip",
            "mime": "application/zip",
            "size": 895905,
            "path": "candidates/t_exam/exam_answer/answer.zip",
            "saved": True,
        }]
        analyzer_result = self._fake_analyzer_result(
            intent="exam_submitted", need_boss=False)
        analyzer_result["summary"] = "候选人回复邮件，但未见提交笔试附件或明确说明已完成。"
        analyzer_result["details"] = {"has_attachment": False}
        with mock.patch("inbox.cmd_analyze.analyzer.analyze",
                        return_value=analyzer_result), \
             mock.patch("inbox.cmd_analyze.talent_db.set_email_analyzed") as set_analyzed, \
             mock.patch("inbox.cmd_analyze.assert_email_analyzed"), \
             mock.patch("inbox.cmd_analyze._send_feishu_all_inbound",
                        return_value={"boss": True, "polaris": True}) as feishu_mock, \
             mock.patch("inbox.cmd_analyze.run_module",
                        return_value={"ok": True, "returncode": 0, "stdout": "",
                                      "stderr": "", "cmd": [], "json": None}):
            res = cmd_analyze._analyze_one(row, dry_run=False, no_feishu=False)

        payload = set_analyzed.call_args.kwargs["ai_payload"]
        self.assertTrue(payload["details"]["has_attachment"])
        self.assertEqual(payload["details"]["attachments"][0]["name"], "黄琪笔试答案.zip")
        self.assertIn("已回复笔试邮件并附上笔试答案附件", payload["summary"])
        text = feishu_mock.call_args.args[0]
        self.assertIn("附件：黄琪笔试答案.zip", text)
        self.assertTrue(res["exam_review_ok"])

    def test_exam_submitted_no_feishu_still_saves_event_without_review_push(self):
        """--no-feishu 调试时仍保存评审事件，但不让评审 CLI 推飞书。"""
        from inbox import cmd_analyze
        row = self._fake_email_row("EXAM_SENT")
        row["talent_id"] = "t_exam"
        row["attachments"] = [{
            "name": "answer.zip",
            "mime": "application/zip",
            "size": 1024,
            "path": "candidates/t_exam/exam_answer/answer.zip",
            "saved": True,
        }]
        analyzer_result = self._fake_analyzer_result(
            intent="exam_submitted", need_boss=False)
        with mock.patch("inbox.cmd_analyze.analyzer.analyze",
                        return_value=analyzer_result), \
             mock.patch("inbox.cmd_analyze.talent_db.set_email_analyzed"), \
             mock.patch("inbox.cmd_analyze.assert_email_analyzed"), \
             mock.patch("inbox.cmd_analyze._send_feishu_all_inbound",
                        return_value={"boss": True, "polaris": True}), \
             mock.patch("inbox.cmd_analyze.run_module",
                        return_value={"ok": True, "returncode": 0, "stdout": "",
                                      "stderr": "", "cmd": [], "json": None}) as run_mock:
            res = cmd_analyze._analyze_one(row, dry_run=False, no_feishu=True)

        run_mock.assert_called_once_with(
            "exam.cmd_exam_ai_review",
            ["--talent-id", "t_exam", "--save-event"],
            timeout=cmd_analyze._EXAM_REVIEW_TIMEOUT_SEC,
            parse_json=False,
        )
        self.assertTrue(res["exam_review_ok"])

    def test_exam_submitted_without_saved_attachment_does_not_review(self):
        """LLM 误判 exam_submitted 但 DB 没有已保存附件时，不自动评审。"""
        from inbox import cmd_analyze
        row = self._fake_email_row("EXAM_SENT")
        analyzer_result = self._fake_analyzer_result(
            intent="exam_submitted", need_boss=False)
        analyzer_result["details"] = {"has_attachment": False}
        with mock.patch("inbox.cmd_analyze.analyzer.analyze",
                        return_value=analyzer_result), \
             mock.patch("inbox.cmd_analyze.talent_db.set_email_analyzed"), \
             mock.patch("inbox.cmd_analyze.assert_email_analyzed"), \
             mock.patch("inbox.cmd_analyze._send_feishu_all_inbound",
                        return_value={"boss": True, "polaris": True}), \
             mock.patch("inbox.cmd_analyze.run_module") as run_mock:
            res = cmd_analyze._analyze_one(row, dry_run=False, no_feishu=False)

        run_mock.assert_not_called()
        self.assertFalse(res["exam_review_triggered"])
        self.assertIsNone(res["exam_review_ok"])

    def test_exam_submitted_outside_exam_sent_does_not_review(self):
        """只有 EXAM_SENT 阶段的笔试提交才自动评审，避免历史邮件误触发。"""
        from inbox import cmd_analyze
        row = self._fake_email_row("EXAM_REVIEWED")
        row["attachments"] = [{
            "name": "answer.zip",
            "saved": True,
        }]
        analyzer_result = self._fake_analyzer_result(
            intent="exam_submitted", need_boss=False)
        with mock.patch("inbox.cmd_analyze.analyzer.analyze",
                        return_value=analyzer_result), \
             mock.patch("inbox.cmd_analyze.talent_db.set_email_analyzed"), \
             mock.patch("inbox.cmd_analyze.assert_email_analyzed"), \
             mock.patch("inbox.cmd_analyze._send_feishu_all_inbound",
                        return_value={"boss": True, "polaris": True}), \
             mock.patch("inbox.cmd_analyze.run_module") as run_mock:
            res = cmd_analyze._analyze_one(row, dry_run=False, no_feishu=False)

        run_mock.assert_not_called()
        self.assertFalse(res["exam_review_triggered"])
        self.assertIsNone(res["exam_review_ok"])

    def test_exam_review_failure_makes_cmd_analyze_nonzero(self):
        """自动评审失败应让 cmd_analyze 非零退出，交给 cron_runner 告警。"""
        from inbox import cmd_analyze
        row = self._fake_email_row("EXAM_SENT")
        row["attachments"] = [{
            "name": "answer.zip",
            "saved": True,
        }]
        analyzer_result = self._fake_analyzer_result(
            intent="exam_submitted", need_boss=False)
        with mock.patch("inbox.cmd_analyze.talent_db.list_unanalyzed_inbound",
                        return_value=[row]), \
             mock.patch("inbox.cmd_analyze.analyzer.analyze",
                        return_value=analyzer_result), \
             mock.patch("inbox.cmd_analyze.talent_db.set_email_analyzed"), \
             mock.patch("inbox.cmd_analyze.assert_email_analyzed"), \
             mock.patch("inbox.cmd_analyze._send_feishu_all_inbound",
                        return_value={"boss": True, "polaris": True}), \
             mock.patch("inbox.cmd_analyze.run_module",
                        return_value={"ok": False, "returncode": 1,
                                      "stdout": "", "stderr": "rubric missing",
                                      "cmd": [], "json": None}):
            rc = cmd_analyze.main([])

        self.assertEqual(rc, 4)

    def test_analyze_returns_nonzero_when_any_notification_target_fails(self):
        """v3.8.6：老板或 Polaris 任一通知失败，cmd_analyze 非零退出触发 cron 告警。"""
        from inbox import cmd_analyze
        row = self._fake_email_row("ROUND1_SCHEDULED")
        analyzer_result = self._fake_analyzer_result(
            intent="thanks_fyi", need_boss=False)
        with mock.patch("inbox.cmd_analyze.talent_db.list_unanalyzed_inbound",
                        return_value=[row]), \
             mock.patch("inbox.cmd_analyze.analyzer.analyze",
                        return_value=analyzer_result), \
             mock.patch("inbox.cmd_analyze.talent_db.set_email_analyzed"), \
             mock.patch("inbox.cmd_analyze.assert_email_analyzed"), \
             mock.patch("inbox.cmd_analyze._send_feishu_all_inbound",
                        return_value={"boss": True, "polaris": False}):
            rc = cmd_analyze.main([])

        self.assertEqual(rc, 3)

    def test_override_table_contents(self):
        """显式验 _STAGE_AWARE_NEED_BOSS 表的内容,防止未来误删/误改。"""
        from inbox.cmd_analyze import _STAGE_AWARE_NEED_BOSS
        self.assertIn(("confirm_interview", "ROUND1_SCHEDULING"),
                      _STAGE_AWARE_NEED_BOSS)
        self.assertIn(("confirm_interview", "ROUND2_SCHEDULING"),
                      _STAGE_AWARE_NEED_BOSS)
        # SCHEDULED 不在表里
        self.assertNotIn(("confirm_interview", "ROUND1_SCHEDULED"),
                         _STAGE_AWARE_NEED_BOSS)
        self.assertNotIn(("confirm_interview", "ROUND2_SCHEDULED"),
                         _STAGE_AWARE_NEED_BOSS)


# ════════════════════════════════════════════════════════════════════════════
# 场景 12：cmd_scan _SKIP_STAGES 终态分权
# ════════════════════════════════════════════════════════════════════════════

class TestInboxScanTerminalSkip(unittest.TestCase):
    """v3.8.4 场景 12：cmd_scan 跳过 ONBOARDED + OFFER_DECLINED_KEEP。"""

    def _candidate(self, stage):
        return {
            "talent_id": "t_skip",
            "candidate_name": "测试",
            "candidate_email": "c@example.com",
            "current_stage": stage,
        }

    def test_onboarded_skipped_without_imap_call(self):
        """ONBOARDED 候选人直接 short-circuit,不调 IMAP。"""
        from inbox import cmd_scan
        fake_imap = mock.MagicMock()
        with mock.patch("inbox.cmd_scan._fetch_messages_for_email",
                        return_value=[]) as fetch_mock:
            res = cmd_scan._process_candidate(
                fake_imap, self._candidate("ONBOARDED"), since_dt=None,
                dry_run=False)
        self.assertEqual(res.get("skipped_stage"), "ONBOARDED")
        self.assertEqual(res.get("inserted"), [])
        self.assertEqual(res.get("scanned"), 0)
        fetch_mock.assert_not_called()

    def test_offer_declined_keep_skipped(self):
        """OFFER_DECLINED_KEEP 候选人直接 short-circuit。"""
        from inbox import cmd_scan
        fake_imap = mock.MagicMock()
        with mock.patch("inbox.cmd_scan._fetch_messages_for_email",
                        return_value=[]) as fetch_mock:
            res = cmd_scan._process_candidate(
                fake_imap, self._candidate("OFFER_DECLINED_KEEP"),
                since_dt=None, dry_run=False)
        self.assertEqual(res.get("skipped_stage"), "OFFER_DECLINED_KEEP")
        fetch_mock.assert_not_called()

    def test_exam_reject_keep_still_scanned(self):
        """EXAM_REJECT_KEEP 不在跳过列表——候选人没拒我们,仍可能回头追问。"""
        from inbox import cmd_scan
        fake_imap = mock.MagicMock()
        with mock.patch("inbox.cmd_scan._fetch_messages_for_email",
                        return_value=[]) as fetch_mock:
            res = cmd_scan._process_candidate(
                fake_imap, self._candidate("EXAM_REJECT_KEEP"),
                since_dt=None, dry_run=False)
        self.assertNotIn("skipped_stage", res)
        fetch_mock.assert_called_once()

    def test_round2_done_reject_keep_still_scanned(self):
        """ROUND2_DONE_REJECT_KEEP 不在跳过列表——是我们 say no,候选人没拒我们。"""
        from inbox import cmd_scan
        fake_imap = mock.MagicMock()
        with mock.patch("inbox.cmd_scan._fetch_messages_for_email",
                        return_value=[]) as fetch_mock:
            res = cmd_scan._process_candidate(
                fake_imap, self._candidate("ROUND2_DONE_REJECT_KEEP"),
                since_dt=None, dry_run=False)
        self.assertNotIn("skipped_stage", res)
        fetch_mock.assert_called_once()

    def test_wait_return_still_scanned(self):
        """WAIT_RETURN 必须扫——这是等候选人回国信号的入口。"""
        from inbox import cmd_scan
        fake_imap = mock.MagicMock()
        with mock.patch("inbox.cmd_scan._fetch_messages_for_email",
                        return_value=[]) as fetch_mock:
            res = cmd_scan._process_candidate(
                fake_imap, self._candidate("WAIT_RETURN"),
                since_dt=None, dry_run=False)
        self.assertNotIn("skipped_stage", res)
        fetch_mock.assert_called_once()

    def test_skip_stages_frozenset_contents(self):
        """显式验 _SKIP_STAGES 内容,防止未来误删/误加。"""
        from inbox.cmd_scan import _SKIP_STAGES
        self.assertEqual(_SKIP_STAGES,
                         frozenset({"ONBOARDED", "OFFER_DECLINED_KEEP"}))


if __name__ == "__main__":
    unittest.main()
