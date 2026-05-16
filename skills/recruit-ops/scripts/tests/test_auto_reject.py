#!/usr/bin/env python3
"""auto_reject 模块测试（v3.8.3：拒+物理删档设计）。

覆盖：
  - find_timeout_candidates: 命中 / 跳过未到期 / 跳过已 inbound /
                             跳过已发过拒信（v3.5.11 引入,v3.8.3 保留的二次防护）
  - main(): mock executor 后命中候选人会触发 _send_rejection_email +
            _delete_talent（v3.8.3 回退;v3.5.11 的 _mark_exam_rejected_keep 已下线）
  - 幂等：连续扫两次第二次 0 命中——靠 talent_emails 里的拒信记录拦截
    （v3.8.3 候选人 DB 行已删,但 talent_emails 仍可被 has_outbound_rejection 读到,
    因为 cmd_delete 失败时不会写拒信记录;而 cmd_delete 成功时下次根本扫不到该
    talent_id——所以"已发拒信但未删档"才是 has_outbound_rejection 真正发挥作用
    的场景）
  - 失败模式：cmd_delete 失败时返回非 0，但拒信已发不会被重发
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
        """v3.5.11 引入 / v3.8.3 保留的二次防护：已发过 outbound rejection 邮件
        的候选人不再扫到（场景：上一轮 cmd_delete 抛错让人留在 EXAM_SENT）。"""
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

    def _fake_delete_factory(self, delete_calls):
        """模拟 executor._delete_talent：从内存 tdb 删掉,返回 archive_path。"""
        def fake_delete(tid, message_id, reason, dry_run=False):
            delete_calls.append((tid, message_id, reason, dry_run))
            if dry_run:
                return {"ok": True, "detail": "[dry-run]"}
            # 真实路径里 talent.cmd_delete 会归档 + DELETE FROM talents；
            # 测试里用 mem_tdb.delete_talent 模拟（与 cmd_delete in-memory 等价）。
            helpers.mem_tdb.delete_talent(tid)
            archive = "/tmp/fake-archive/{}_20260511.json".format(tid)
            return {"ok": True, "detail": "fake delete",
                    "archive_path": archive, "raw": {}}
        return fake_delete

    def test_main_rejects_and_deletes_each_candidate(self):
        self._add_exam_sent("t_rej1", sent_days_ago=4)
        self._add_exam_sent("t_rej2", sent_days_ago=5)

        send_calls, delete_calls = [], []
        with mock.patch("auto_reject.executor._send_rejection_email",
                        side_effect=self._fake_send_factory(send_calls)), \
             mock.patch("auto_reject.executor._delete_talent",
                        side_effect=self._fake_delete_factory(delete_calls)):
            from auto_reject import cmd_scan_exam_timeout
            rc = cmd_scan_exam_timeout.main(["--auto", "--no-feishu"])

        self.assertEqual(rc, 0)
        self.assertEqual(len(send_calls), 2)
        self.assertEqual(len(delete_calls), 2)
        # 候选人已物理删除（v3.8.3 回退到 cmd_delete）
        self.assertIsNone(helpers.mem_tdb.get_one("t_rej1"))
        self.assertIsNone(helpers.mem_tdb.get_one("t_rej2"))
        # 模板必须是 rejection_exam_no_reply
        for _tid, template, _reason in send_calls:
            self.assertEqual(template, "rejection_exam_no_reply")
        # cmd_delete 调用顺序：先 send 后 delete
        for tid, msg_id, reason, dry_run in delete_calls:
            self.assertIn(tid, {"t_rej1", "t_rej2"})
            self.assertEqual(reason, "exam_no_reply")
            self.assertFalse(dry_run)
            self.assertIsNotNone(msg_id, "message_id 必须从 send_res 传入 delete")

    def test_idempotent_second_scan_finds_nothing(self):
        """连扫两次：第二次应该 0 命中。

        v3.8.3 happy path：cmd_delete 成功后候选人 DB 行不在,第二次扫 SQL
        get_exam_timeout_candidates 根本拿不到该 talent_id（不依赖 has_outbound_rejection 二次防护）。"""
        self._add_exam_sent("t_iter", sent_days_ago=4)

        send_calls, delete_calls = [], []
        with mock.patch("auto_reject.executor._send_rejection_email",
                        side_effect=self._fake_send_factory(send_calls)), \
             mock.patch("auto_reject.executor._delete_talent",
                        side_effect=self._fake_delete_factory(delete_calls)):
            from auto_reject import cmd_scan_exam_timeout
            rc1 = cmd_scan_exam_timeout.main(["--auto", "--no-feishu"])
            self.assertEqual(rc1, 0)
            self.assertEqual(len(send_calls), 1)
            self.assertEqual(len(delete_calls), 1)

            # 第二轮——候选人已删,扫不到
            rc2 = cmd_scan_exam_timeout.main(["--auto", "--no-feishu"])
            self.assertEqual(rc2, 0)
            self.assertEqual(len(send_calls), 1, "第二次扫不应再发拒信！")
            self.assertEqual(len(delete_calls), 1)

    def test_dry_run_does_not_call_executor(self):
        self._add_exam_sent("t_dry", sent_days_ago=4)
        with mock.patch("auto_reject.executor._send_rejection_email") as send_mock, \
             mock.patch("auto_reject.executor._delete_talent") as delete_mock:
            from auto_reject import cmd_scan_exam_timeout
            rc = cmd_scan_exam_timeout.main(["--dry-run", "--auto", "--no-feishu"])

        self.assertEqual(rc, 0)
        send_mock.assert_not_called()
        delete_mock.assert_not_called()
        # dry-run 不动 stage
        self.assertEqual(helpers.mem_tdb.get_talent_current_stage("t_dry"), "EXAM_SENT")
        # 候选人仍在
        self.assertIsNotNone(helpers.mem_tdb.get_one("t_dry"))

    def test_send_failure_keeps_candidate_in_exam_sent(self):
        """发拒信失败时不应删档。"""
        self._add_exam_sent("t_failsend", sent_days_ago=4)

        def bad_send(*a, **kw):
            return {"ok": False, "detail": "smtp down", "raw": {}}

        with mock.patch("auto_reject.executor._send_rejection_email", side_effect=bad_send), \
             mock.patch("auto_reject.executor._delete_talent") as delete_mock:
            from auto_reject import cmd_scan_exam_timeout
            rc = cmd_scan_exam_timeout.main(["--auto", "--no-feishu"])

        self.assertNotEqual(rc, 0)
        delete_mock.assert_not_called()
        # 候选人仍在,stage 未变
        self.assertIsNotNone(helpers.mem_tdb.get_one("t_failsend"))
        self.assertEqual(helpers.mem_tdb.get_talent_current_stage("t_failsend"),
                         "EXAM_SENT")

    def test_delete_failure_returns_nonzero_but_does_not_resend(self):
        """v3.8.3 事故面回归：拒信已发但 cmd_delete 失败时——
        rc 非 0 提醒 HR 介入；下次扫描被 has_outbound_rejection 拦下,不重发拒信。

        这是 v3.5.11 引入的二次防护在 v3.8.3 回退后**真正发挥作用**的场景。"""
        self._add_exam_sent("t_delfail", sent_days_ago=4)

        send_calls = []
        with mock.patch("auto_reject.executor._send_rejection_email",
                        side_effect=self._fake_send_factory(send_calls)), \
             mock.patch("auto_reject.executor._delete_talent",
                        return_value={"ok": False, "detail": "DB down", "raw": {}}):
            from auto_reject import cmd_scan_exam_timeout
            rc1 = cmd_scan_exam_timeout.main(["--auto", "--no-feishu"])
            self.assertNotEqual(rc1, 0)
            self.assertEqual(len(send_calls), 1)

        # 候选人仍在 EXAM_SENT（cmd_delete 没成功）；
        # 但 talent_emails 已有拒信 → find_timeout_candidates 二次防护拦截。
        self.assertEqual(helpers.mem_tdb.get_talent_current_stage("t_delfail"),
                         "EXAM_SENT")
        from auto_reject.cmd_scan_exam_timeout import find_timeout_candidates
        self.assertEqual(find_timeout_candidates(threshold_days=3), [],
                         "v3.5.11 二次防护必须拦下这种 case,否则下个 cron tick 会重发拒信")


class TestExecutorDeleteTalent(AutoRejectTestBase):
    """直接测 _delete_talent 函数本身（v3.8.3 替代了 _mark_exam_rejected_keep）。"""

    def test_delete_dry_run_skips_subprocess(self):
        self._add_candidate("t_dry_del", stage="EXAM_SENT")
        from auto_reject import executor
        res = executor._delete_talent(
            "t_dry_del", "<msg@local>", "exam_no_reply", dry_run=True)
        self.assertTrue(res["ok"])
        self.assertIn("dry-run", res["detail"])
        # 没真的删
        self.assertIsNotNone(helpers.mem_tdb.get_one("t_dry_del"))

    def test_delete_calls_subprocess_with_hard_guard(self):
        """v3.8.1 hard guard: --confirm-delete-talent 必须 = --talent-id。
        verify executor._delete_talent 把这两个值传一致。

        v3.8.x 起 executor 不再自己手写 _run_cmd,改走通用层
        lib.cli_subprocess.run_module()。这里 patch 的是 executor 模块里
        引入的 run_module 名字（`from lib.cli_subprocess import run_module`），
        断言它被调用一次,argv 满足 hard guard 契约。
        """
        self._add_candidate("t_subproc", stage="EXAM_SENT")
        from auto_reject import executor

        # run_module 的返回结构（json 字段挂解析结果）
        fake_rc = {
            "ok": True, "returncode": 0,
            "stdout": '{"ok": true, "archive_path": "/tmp/x.json"}',
            "stderr": "", "cmd": [],
            "json": {"ok": True, "archive_path": "/tmp/x.json"},
        }
        with mock.patch("auto_reject.executor.run_module",
                        return_value=fake_rc) as run_mock:
            res = executor._delete_talent(
                "t_subproc", "<m@local>", "exam_no_reply")
        self.assertTrue(res["ok"])
        self.assertEqual(res.get("archive_path"), "/tmp/x.json")
        run_mock.assert_called_once()
        # positional args: (module, args_list)
        called_args = run_mock.call_args[0]
        self.assertEqual(called_args[0], "talent.cmd_delete")
        args_list = called_args[1]
        # hard guard 两个值必须一致
        i_tid = args_list.index("--talent-id")
        i_confirm = args_list.index("--confirm-delete-talent")
        self.assertEqual(args_list[i_tid + 1], "t_subproc")
        self.assertEqual(args_list[i_confirm + 1], "t_subproc")
        # actor 标识写明来源
        self.assertIn("--actor", args_list)
        self.assertEqual(args_list[args_list.index("--actor") + 1],
                         "auto_reject.cmd_scan_exam_timeout")
        # parse_json 必须为 True,否则 archive_path 拿不到
        self.assertTrue(run_mock.call_args.kwargs.get("parse_json"))


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
