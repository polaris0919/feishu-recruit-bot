#!/usr/bin/env python3
"""auto_reject 模块测试（v3.5.11：拒+留池设计）。

覆盖：
  - find_timeout_candidates: 命中 / 跳过未到期 / 跳过已 inbound /
                             跳过已发过拒信（v3.5.11 新增二次防护）
  - main(): mock executor 后命中候选人会触发 _send_rejection_email +
            _mark_exam_rejected_keep（不再调 _delete_talent）
  - 幂等：连续扫两次第二次 0 命中（基于 talent_emails 里的拒信记录 + stage 改动）
  - 失败模式：mark stage 失败时返回非 0，但拒信已发不会被重发
"""
from __future__ import print_function

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import tests.helpers as helpers  # noqa: F401  side-effect: 装内存 talent_db


class AutoRejectTestBase(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("RECRUIT_DISABLE_SIDE_EFFECTS", "1")
        helpers.wipe_state()

    def _add_candidate(self, talent_id, **kw):
        cand = {
            "talent_id": talent_id,
            "candidate_email": kw.get("email", "{}@x.com".format(talent_id)),
            "candidate_name": kw.get("name", "测试" + talent_id),
            "stage": kw.get("stage", "EXAM_SENT"),
            "exam_sent_at": kw.get("exam_sent_at"),
        }
        helpers.mem_tdb.upsert_one(talent_id, cand)


class TestFindTimeoutCandidates(AutoRejectTestBase):
    def _add_exam_sent(self, tid, sent_days_ago, **kw):
        sent_at = (datetime.now(timezone.utc) - timedelta(days=sent_days_ago)).isoformat()
        self._add_candidate(tid, exam_sent_at=sent_at, **kw)
        return sent_at

    def test_finds_only_timed_out_candidates(self):
        self._add_exam_sent("t_timeout", sent_days_ago=4)   # 命中
        self._add_exam_sent("t_fresh", sent_days_ago=1)     # 未到 3 天
        from auto_reject.cmd_scan_exam_timeout import find_timeout_candidates
        rows = find_timeout_candidates(threshold_days=3)
        ids = {r["talent_id"] for r in rows}
        self.assertIn("t_timeout", ids)
        self.assertNotIn("t_fresh", ids)

    def test_skips_when_inbound_email_after_sent(self):
        self._add_exam_sent("t_replied", sent_days_ago=4)
        helpers.mem_tdb.insert_email_if_absent(
            talent_id="t_replied", message_id="<m1@x>", direction="inbound",
            context="exam", sender="t_replied@x.com",
            sent_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        )
        from auto_reject.cmd_scan_exam_timeout import find_timeout_candidates
        rows = find_timeout_candidates(threshold_days=3)
        self.assertNotIn("t_replied", {r["talent_id"] for r in rows})

    def test_skips_when_outbound_rejection_already_sent(self):
        """v3.5.11 二次防护：已经发过 outbound rejection 邮件的候选人不再扫到。"""
        self._add_exam_sent("t_already_rejected", sent_days_ago=4)
        helpers.mem_tdb.insert_email_if_absent(
            talent_id="t_already_rejected",
            message_id="<rej1@local>",
            direction="outbound",
            context="rejection",
            sender="hr@x.com",
            sent_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
            initial_status="auto_processed",
        )
        from auto_reject.cmd_scan_exam_timeout import find_timeout_candidates
        rows = find_timeout_candidates(threshold_days=3)
        self.assertNotIn("t_already_rejected", {r["talent_id"] for r in rows})


class TestScanMain(AutoRejectTestBase):
    def _add_exam_sent(self, tid, sent_days_ago):
        sent_at = (datetime.now(timezone.utc) - timedelta(days=sent_days_ago)).isoformat()
        self._add_candidate(tid, exam_sent_at=sent_at)

    def _fake_send_factory(self, send_calls):
        def fake_send(tid, template, reason, dry_run=False):
            send_calls.append((tid, template, reason))
            mid = "<msg-{}@local>".format(tid)
            # 真实路径：cmd_send 成功时会写一行 outbound rejection 到 talent_emails
            helpers.mem_tdb.insert_email_if_absent(
                talent_id=tid, message_id=mid, direction="outbound",
                context="rejection", sender="hr@x.com",
                sent_at=datetime.now(timezone.utc).isoformat(),
                initial_status="auto_processed",
            )
            return {"ok": True, "message_id": mid, "detail": "fake send", "raw": {}}
        return fake_send

    def test_main_rejects_and_marks_each_candidate(self):
        self._add_exam_sent("t_rej1", sent_days_ago=4)
        self._add_exam_sent("t_rej2", sent_days_ago=5)

        send_calls = []
        with mock.patch("auto_reject.executor._send_rejection_email",
                        side_effect=self._fake_send_factory(send_calls)):
            from auto_reject import cmd_scan_exam_timeout
            rc = cmd_scan_exam_timeout.main(["--auto", "--no-feishu"])

        self.assertEqual(rc, 0)
        self.assertEqual(len(send_calls), 2)
        # 候选人留在人才库（不再删档）
        self.assertIsNotNone(helpers.mem_tdb.get_one("t_rej1"))
        self.assertIsNotNone(helpers.mem_tdb.get_one("t_rej2"))
        # stage 推到 EXAM_REJECT_KEEP
        self.assertEqual(helpers.mem_tdb.get_talent_current_stage("t_rej1"),
                         "EXAM_REJECT_KEEP")
        self.assertEqual(helpers.mem_tdb.get_talent_current_stage("t_rej2"),
                         "EXAM_REJECT_KEEP")
        # 模板必须是 rejection_exam_no_reply
        for _tid, template, _reason in send_calls:
            self.assertEqual(template, "rejection_exam_no_reply")

    def test_idempotent_second_scan_finds_nothing(self):
        """连扫两次：第二次应该 0 命中（拒信已发 + stage 已改 = 双重拦截）。"""
        self._add_exam_sent("t_iter", sent_days_ago=4)

        send_calls = []
        with mock.patch("auto_reject.executor._send_rejection_email",
                        side_effect=self._fake_send_factory(send_calls)):
            from auto_reject import cmd_scan_exam_timeout
            rc1 = cmd_scan_exam_timeout.main(["--auto", "--no-feishu"])
            self.assertEqual(rc1, 0)
            self.assertEqual(len(send_calls), 1)

            # 第二轮——理应不发拒信
            rc2 = cmd_scan_exam_timeout.main(["--auto", "--no-feishu"])
            self.assertEqual(rc2, 0)
            self.assertEqual(len(send_calls), 1, "第二次扫不应再发拒信！")

    def test_dry_run_does_not_call_executor(self):
        self._add_exam_sent("t_dry", sent_days_ago=4)
        with mock.patch("auto_reject.executor._send_rejection_email") as send_mock, \
             mock.patch("auto_reject.executor._mark_exam_rejected_keep") as mark_mock:
            from auto_reject import cmd_scan_exam_timeout
            rc = cmd_scan_exam_timeout.main(["--dry-run", "--auto", "--no-feishu"])

        self.assertEqual(rc, 0)
        send_mock.assert_not_called()
        mark_mock.assert_not_called()
        self.assertEqual(helpers.mem_tdb.get_talent_current_stage("t_dry"), "EXAM_SENT")

    def test_send_failure_keeps_candidate_in_exam_sent(self):
        """发拒信失败时不应改 stage。"""
        self._add_exam_sent("t_failsend", sent_days_ago=4)

        def bad_send(*a, **kw):
            return {"ok": False, "detail": "smtp down", "raw": {}}

        with mock.patch("auto_reject.executor._send_rejection_email", side_effect=bad_send), \
             mock.patch("auto_reject.executor._mark_exam_rejected_keep") as mark_mock:
            from auto_reject import cmd_scan_exam_timeout
            rc = cmd_scan_exam_timeout.main(["--auto", "--no-feishu"])

        self.assertNotEqual(rc, 0)
        mark_mock.assert_not_called()
        self.assertEqual(helpers.mem_tdb.get_talent_current_stage("t_failsend"),
                         "EXAM_SENT")

    def test_mark_failure_returns_nonzero_but_does_not_resend(self):
        """mark stage 失败：rc 非 0 提醒人工介入；下次扫会被 has_outbound_rejection 拦下。"""
        self._add_exam_sent("t_markfail", sent_days_ago=4)

        send_calls = []
        with mock.patch("auto_reject.executor._send_rejection_email",
                        side_effect=self._fake_send_factory(send_calls)), \
             mock.patch("auto_reject.executor._mark_exam_rejected_keep",
                        return_value={"ok": False, "detail": "DB down"}):
            from auto_reject import cmd_scan_exam_timeout
            rc1 = cmd_scan_exam_timeout.main(["--auto", "--no-feishu"])
            self.assertNotEqual(rc1, 0)
            self.assertEqual(len(send_calls), 1)

        # 拒信已写入 talent_emails；stage 未改（仍 EXAM_SENT）。下次扫被二次防护拦截。
        self.assertEqual(helpers.mem_tdb.get_talent_current_stage("t_markfail"),
                         "EXAM_SENT")
        from auto_reject.cmd_scan_exam_timeout import find_timeout_candidates
        self.assertEqual(find_timeout_candidates(threshold_days=3), [])


class TestExecutorMarkRejectedKeep(AutoRejectTestBase):
    """直接测 _mark_exam_rejected_keep 函数本身。"""

    def test_mark_sets_stage_and_returns_ok(self):
        self._add_candidate("t_mark", stage="EXAM_SENT")
        from auto_reject import executor
        res = executor._mark_exam_rejected_keep(
            "t_mark", "<msg@local>", "exam_no_reply")
        self.assertTrue(res["ok"])
        self.assertEqual(helpers.mem_tdb.get_talent_current_stage("t_mark"),
                         "EXAM_REJECT_KEEP")

    def test_mark_dry_run_skips_db_write(self):
        self._add_candidate("t_dry_mark", stage="EXAM_SENT")
        from auto_reject import executor
        res = executor._mark_exam_rejected_keep(
            "t_dry_mark", "<msg@local>", "exam_no_reply", dry_run=True)
        self.assertTrue(res["ok"])
        self.assertEqual(helpers.mem_tdb.get_talent_current_stage("t_dry_mark"),
                         "EXAM_SENT")  # 没改


class TestRejectionContextIsValid(unittest.TestCase):
    """根因 bug 回归：lib.talent_db 必须接受 context='rejection'。"""

    def test_rejection_in_valid_contexts(self):
        # 直接读源文件，避免 helpers.py 把 lib.talent_db 替换成内存 mock 后
        # 真实模块的 _EMAIL_VALID_CONTEXTS 常量被屏蔽。
        import importlib.util
        import os
        scripts_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location(
            "_talent_db_real", os.path.join(scripts_root, "lib", "talent_db.py"))
        mod = importlib.util.module_from_spec(spec)
        # 不 exec_module（会触发 DB 连接）；直接源码字符串扫描即可
        with open(spec.origin, "r", encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('"rejection"', src.split("_EMAIL_VALID_CONTEXTS")[1].split(")")[0],
                      "_EMAIL_VALID_CONTEXTS 必须包含 \"rejection\"，否则 auto_reject "
                      "发拒信会崩在 cmd_send 写库步骤（线上事故 2026-04-22 11:30）")

    def test_insert_outbound_rejection_does_not_raise(self):
        helpers.wipe_state()
        helpers.mem_tdb.upsert_one("t_ctx", {
            "talent_id": "t_ctx", "candidate_email": "x@y.com",
            "candidate_name": "测", "stage": "EXAM_SENT",
        })
        helpers.mem_tdb.insert_email_if_absent(
            talent_id="t_ctx", message_id="<r@x>", direction="outbound",
            context="rejection", sender="hr@x.com",
            sent_at=datetime.now(timezone.utc).isoformat(),
            initial_status="auto_processed",
        )
        # 没 raise 就过


if __name__ == "__main__":
    unittest.main()
