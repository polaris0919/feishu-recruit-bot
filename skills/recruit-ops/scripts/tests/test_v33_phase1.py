#!/usr/bin/env python3
"""tests/test_v33_phase1.py —— v3.3 Phase 1 基石测试。

覆盖：
  * lib/self_verify.py  —— 5 类断言的 happy/fail 路径
  * outbound/cmd_send   —— 模板模式、自由文本模式、cleanup 行为、自验证错误传播
  * talent/cmd_update   —— natural / forced 转换、字段编辑、跳转拒绝
  * talent/cmd_delete   —— 备份 + 删除 + 自验证

测试约定与现有套保持一致：
  - mem_tdb 充当 talent_db
  - RECRUIT_DISABLE_SIDE_EFFECTS=1 阻止真发邮件 / 真推飞书
  - smtp_sender.send_email_with_threading 在 side_effects_disabled 时返回
    'dry-run' Message-ID，本测试无需 mock SMTP；但需要把 message_id 反向写进 mem_tdb
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

# 必须在导入业务代码前 setup helpers
import tests.helpers as helpers  # noqa: E402

# 关掉自验证告警（cli_wrapper 会推飞书）
os.environ["RECRUIT_SUPPRESS_SELF_VERIFY_ALERT"] = "1"

from lib import self_verify as sv  # noqa: E402


def _mk_talent(talent_id="t_abc123", name="张三", email="zhangsan@example.com",
               stage="ROUND1_SCHEDULING"):
    """直接往 mem_tdb 塞一个候选人。"""
    helpers.mem_tdb._state.setdefault("candidates", {})[talent_id] = {
        "talent_id": talent_id,
        "candidate_name": name,
        "candidate_email": email,
        "current_stage": stage,
        "stage": stage,  # 兼容
    }
    return talent_id


# ════════════════════════════════════════════════════════════════════════════
# self_verify 单元测试
# ════════════════════════════════════════════════════════════════════════════

class TestSelfVerify(unittest.TestCase):

    def setUp(self):
        helpers.wipe_state()

    # ── assert_email_sent ────────────────────────────────────────────────

    def test_assert_email_sent_passes_when_outbound_present(self):
        tid = _mk_talent()
        helpers.mem_tdb.insert_email_if_absent(
            talent_id=tid, message_id="<m1@x>", direction="outbound",
            context="round1", sender="sys@x", sent_at="2026-04-17T10:00:00",
        )
        sv.assert_email_sent(tid, "<m1@x>")  # 不抛即通过

    def test_assert_email_sent_fails_when_missing(self):
        tid = _mk_talent()
        with self.assertRaises(sv.SelfVerifyError) as ctx:
            sv.assert_email_sent(tid, "<missing@x>")
        self.assertEqual(ctx.exception.check, "assert_email_sent")
        self.assertIn("message_id", ctx.exception.context)

    def test_assert_email_sent_fails_when_only_inbound(self):
        tid = _mk_talent()
        helpers.mem_tdb.insert_email_if_absent(
            talent_id=tid, message_id="<m2@x>", direction="inbound",
            context="round1", sender="cand@x", sent_at="2026-04-17T10:00:00",
        )
        with self.assertRaises(sv.SelfVerifyError):
            sv.assert_email_sent(tid, "<m2@x>")

    # ── assert_emails_inserted ───────────────────────────────────────────

    def test_assert_emails_inserted_all_present(self):
        tid = _mk_talent()
        for i in range(3):
            helpers.mem_tdb.insert_email_if_absent(
                talent_id=tid, message_id="<{}@x>".format(i),
                direction="inbound", context="round1",
                sender="x@x", sent_at="2026-04-17T10:00:00",
            )
        sv.assert_emails_inserted(tid, ["<0@x>", "<1@x>", "<2@x>"])

    def test_assert_emails_inserted_reports_missing(self):
        tid = _mk_talent()
        helpers.mem_tdb.insert_email_if_absent(
            talent_id=tid, message_id="<a@x>", direction="inbound",
            context="round1", sender="x@x", sent_at="2026-04-17T10:00:00",
        )
        with self.assertRaises(sv.SelfVerifyError) as ctx:
            sv.assert_emails_inserted(tid, ["<a@x>", "<b@x>", "<c@x>"])
        self.assertEqual(ctx.exception.context["missing_count"], 2)
        self.assertIn("<b@x>", ctx.exception.context["missing"])

    # ── assert_talent_state ──────────────────────────────────────────────

    def test_assert_talent_state_stage_match(self):
        tid = _mk_talent(stage="ROUND2_SCHEDULING")
        sv.assert_talent_state(tid, expected_stage="ROUND2_SCHEDULING")

    def test_assert_talent_state_stage_mismatch_collects_diff(self):
        tid = _mk_talent(stage="ROUND1_SCHEDULING")
        with self.assertRaises(sv.SelfVerifyError) as ctx:
            sv.assert_talent_state(tid, expected_stage="EXAM_SENT")
        diffs = ctx.exception.context["mismatches"]
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0]["expected"], "EXAM_SENT")
        self.assertEqual(diffs[0]["actual"], "ROUND1_SCHEDULING")

    def test_assert_talent_state_field_match(self):
        tid = _mk_talent()
        helpers.mem_tdb._state["candidates"][tid]["phone"] = "13800001111"
        sv.assert_talent_state(tid, expected_fields={"phone": "13800001111"})

    def test_assert_talent_state_field_set_marker(self):
        tid = _mk_talent()
        helpers.mem_tdb._state["candidates"][tid]["round1_invite_sent_at"] = "2026-04-17T10:00:00"
        sv.assert_talent_state(tid, expected_fields={"round1_invite_sent_at": "__SET__"})

    def test_assert_talent_state_field_set_marker_fails_when_null(self):
        tid = _mk_talent()
        with self.assertRaises(sv.SelfVerifyError):
            sv.assert_talent_state(tid, expected_fields={"round1_invite_sent_at": "__SET__"})

    def test_assert_talent_state_missing_talent(self):
        with self.assertRaises(sv.SelfVerifyError) as ctx:
            sv.assert_talent_state("t_nope")
        self.assertIn("missing", ctx.exception.context["hint"])

    # ── assert_talent_deleted ────────────────────────────────────────────

    def test_assert_talent_deleted_passes_when_absent(self):
        sv.assert_talent_deleted("t_already_gone")

    def test_assert_talent_deleted_fails_when_present(self):
        tid = _mk_talent()
        with self.assertRaises(sv.SelfVerifyError) as ctx:
            sv.assert_talent_deleted(tid)
        self.assertEqual(ctx.exception.check, "assert_talent_deleted")


# ════════════════════════════════════════════════════════════════════════════
# outbound/cmd_send 测试
# ════════════════════════════════════════════════════════════════════════════

class TestCmdSend(unittest.TestCase):

    def setUp(self):
        helpers.wipe_state()
        # 强制 SMTP 走 dry-run（side_effects_disabled），但需要捕获返回的 message_id
        # 才能让 mem_tdb 存进去。我们 patch send_email_with_threading 直接返回固定 id。
        self._patcher = mock.patch(
            "lib.smtp_sender.send_email_with_threading",
            return_value="<test-msgid-12345@local>",
        )
        self._patcher.start()
        # cmd_send 也通过 `from lib import smtp_sender` 拿到，这条 patch 会覆盖
        self._patcher2 = mock.patch(
            "outbound.cmd_send.smtp_sender.send_email_with_threading",
            return_value="<test-msgid-12345@local>",
        )
        self._patcher2.start()

    def tearDown(self):
        self._patcher.stop()
        self._patcher2.stop()

    def test_template_mode_invokes_renderer_and_persists(self):
        tid = _mk_talent()
        out, err, rc = helpers.call_main("outbound.cmd_send", [
            "--talent-id", tid,
            "--template", "rejection_generic",
            "--vars",
            "candidate_name=张三",
            "company=示例科技公司",
            "talent_id=" + tid,
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        result = json.loads(out)
        self.assertTrue(result["ok"])
        self.assertEqual(result["template"], "rejection_generic")
        self.assertEqual(result["message_id"], "<test-msgid-12345@local>")
        # talent_emails 已落表
        row = helpers.mem_tdb.find_outbound_email_by_message_id(
            tid, "<test-msgid-12345@local>")
        self.assertIsNotNone(row)
        self.assertEqual(row["direction"], "outbound")

    def test_freetext_mode_with_body_arg(self):
        tid = _mk_talent()
        out, err, rc = helpers.call_main("outbound.cmd_send", [
            "--talent-id", tid,
            "--subject", "Re: 关于薪资",
            "--body", "您好，关于薪资标准的回复...",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        result = json.loads(out)
        self.assertEqual(result["template"], "freeform")
        self.assertEqual(result["subject"], "Re: 关于薪资")

    def test_json_output_includes_sent_at(self):
        """v3.4 Phase 0.2：--json 必须输出 sent_at（让 wrapper 链式取值）。"""
        tid = _mk_talent()
        out, err, rc = helpers.call_main("outbound.cmd_send", [
            "--talent-id", tid,
            "--subject", "test",
            "--body", "x",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        result = json.loads(out)
        self.assertIn("sent_at", result)
        self.assertIsNotNone(result["sent_at"])
        # 形如 2026-04-21T03:14:15Z
        self.assertRegex(result["sent_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        # 同时 message_id / email_id 都必须有
        self.assertIsNotNone(result["message_id"])
        self.assertIsNotNone(result["email_id"])

    def test_freetext_mode_body_file_default_cleanup(self):
        tid = _mk_talent()
        import tempfile
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8")
        f.write("正文 from 临时文件")
        f.close()
        try:
            out, err, rc = helpers.call_main("outbound.cmd_send", [
                "--talent-id", tid,
                "--subject", "test",
                "--body-file", f.name,
                "--json",
            ])
            self.assertEqual(rc, 0, "stderr=" + err)
            result = json.loads(out)
            self.assertTrue(result["cleanup_body_file"])
            self.assertFalse(os.path.exists(f.name),
                             "cleanup ON 时临时文件应被删除")
        finally:
            if os.path.exists(f.name):
                os.unlink(f.name)

    def test_freetext_mode_body_file_no_cleanup(self):
        tid = _mk_talent()
        import tempfile
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8")
        f.write("保留的正文")
        f.close()
        try:
            out, err, rc = helpers.call_main("outbound.cmd_send", [
                "--talent-id", tid,
                "--subject", "test",
                "--body-file", f.name,
                "--no-cleanup-body-file",
                "--json",
            ])
            self.assertEqual(rc, 0, "stderr=" + err)
            result = json.loads(out)
            self.assertFalse(result["cleanup_body_file"])
            self.assertTrue(os.path.exists(f.name),
                            "no-cleanup 时临时文件必须保留")
        finally:
            if os.path.exists(f.name):
                os.unlink(f.name)

    def test_missing_talent_raises(self):
        # cmd_send 直接 raise → call_main 把它映射为 rc=1
        out, err, rc = helpers.call_main("outbound.cmd_send", [
            "--talent-id", "t_nope",
            "--subject", "x",
            "--body", "x",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("不存在", err)

    def test_invalid_email_raises(self):
        helpers.mem_tdb._state.setdefault("candidates", {})["t_bad"] = {
            "talent_id": "t_bad",
            "candidate_email": "",  # 空邮箱
            "candidate_name": "无效",
            "current_stage": "NEW",
        }
        out, err, rc = helpers.call_main("outbound.cmd_send", [
            "--talent-id", "t_bad",
            "--subject", "x",
            "--body", "x",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("candidate_email", err)

    def test_template_missing_var_raises(self):
        tid = _mk_talent()
        # reschedule 需要 round_label（没 setdefault 的非标准变量）
        # cmd_send 会自动补 candidate_name / company / talent_id，但 round_label 必须显式传。
        out, err, rc = helpers.call_main("outbound.cmd_send", [
            "--talent-id", tid,
            "--template", "reschedule",
            # 故意不传 --vars round_label=...
        ])
        self.assertEqual(rc, 1)
        self.assertIn("缺变量", err)

    def test_dry_run_does_not_send(self):
        tid = _mk_talent()
        out, err, rc = helpers.call_main("outbound.cmd_send", [
            "--talent-id", tid,
            "--subject", "test",
            "--body", "body",
            "--dry-run",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        result = json.loads(out)
        self.assertTrue(result["dry_run"])
        self.assertIsNone(result["email_id"], "dry-run 不应写 talent_emails")
        # SMTP 不应被调
        from outbound.cmd_send import smtp_sender as ss
        ss.send_email_with_threading.assert_not_called()
        # talent_emails 也不应有行
        rows = [r for (atid, _mid), r in helpers.mem_tdb._emails.items() if atid == tid]
        self.assertEqual(len(rows), 0, "dry-run 漏写 talent_emails")


# ════════════════════════════════════════════════════════════════════════════
# talent/cmd_update 测试
# ════════════════════════════════════════════════════════════════════════════

class TestCmdUpdate(unittest.TestCase):

    def setUp(self):
        helpers.wipe_state()

    def test_natural_transition_works(self):
        tid = _mk_talent(stage="ROUND1_SCHEDULING")
        out, err, rc = helpers.call_main("talent.cmd_update", [
            "--talent-id", tid,
            "--stage", "ROUND1_SCHEDULED",
            "--reason", "排好了",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        result = json.loads(out)
        self.assertTrue(result["transition"]["natural"])
        self.assertFalse(result["transition"]["forced"])
        self.assertEqual(
            helpers.mem_tdb.get_talent_current_stage(tid),
            "ROUND1_SCHEDULED",
        )

    def test_unnatural_transition_rejected_without_force(self):
        tid = _mk_talent(stage="NEW")
        out, err, rc = helpers.call_main("talent.cmd_update", [
            "--talent-id", tid,
            # NEW → POST_OFFER_FOLLOWUP 不在 natural 表（跨多步）。
            "--stage", "POST_OFFER_FOLLOWUP",
            "--reason", "想直接发 offer",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("natural transitions", err)
        # stage 不应被改
        self.assertEqual(helpers.mem_tdb.get_talent_current_stage(tid), "NEW")

    def test_unnatural_transition_with_force_succeeds(self):
        tid = _mk_talent(stage="NEW")
        out, err, rc = helpers.call_main("talent.cmd_update", [
            "--talent-id", tid,
            "--stage", "POST_OFFER_FOLLOWUP",
            "--force",
            "--reason", "电话面试通过，直接进 offer 沟通",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        result = json.loads(out)
        self.assertTrue(result["transition"]["forced"])
        self.assertFalse(result["transition"]["natural"])
        self.assertEqual(
            helpers.mem_tdb.get_talent_current_stage(tid), "POST_OFFER_FOLLOWUP")

    def test_field_edit_works(self):
        tid = _mk_talent()
        out, err, rc = helpers.call_main("talent.cmd_update", [
            "--talent-id", tid,
            "--field", "phone",
            "--value", "13800001111",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "phone"), "13800001111")

    def test_field_edit_null_marker(self):
        tid = _mk_talent()
        helpers.mem_tdb._state["candidates"][tid]["phone"] = "x"
        out, err, rc = helpers.call_main("talent.cmd_update", [
            "--talent-id", tid,
            "--field", "phone",
            "--value", "__NULL__",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        self.assertIsNone(helpers.mem_tdb.get_talent_field(tid, "phone"))

    def test_no_op_same_stage(self):
        tid = _mk_talent(stage="ROUND1_SCHEDULING")
        out, err, rc = helpers.call_main("talent.cmd_update", [
            "--talent-id", tid,
            "--stage", "ROUND1_SCHEDULING",
            "--json",
        ])
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertIsNone(result["transition"])

    def test_missing_talent_raises(self):
        out, err, rc = helpers.call_main("talent.cmd_update", [
            "--talent-id", "t_nope",
            "--stage", "EXAM_SENT",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("不存在", err)

    # ── v3.4 新增：--set / __NOW__ / 多字段原子 ──────────────────────────

    def test_set_pair_basic(self):
        tid = _mk_talent()
        out, err, rc = helpers.call_main("talent.cmd_update", [
            "--talent-id", tid,
            "--set", "phone=13900009999",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "phone"), "13900009999")
        result = json.loads(out)
        self.assertEqual(len(result["field_changes"]), 1)
        self.assertEqual(result["field_changes"][0]["field"], "phone")

    def test_set_null_token(self):
        tid = _mk_talent()
        helpers.mem_tdb._state["candidates"][tid]["wechat"] = "old_wx"
        out, err, rc = helpers.call_main("talent.cmd_update", [
            "--talent-id", tid,
            "--set", "wechat=__NULL__",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        self.assertIsNone(helpers.mem_tdb.get_talent_field(tid, "wechat"))

    def test_set_now_token(self):
        tid = _mk_talent()
        out, err, rc = helpers.call_main("talent.cmd_update", [
            "--talent-id", tid,
            "--set", "round1_invite_sent_at=__NOW__",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        actual = helpers.mem_tdb.get_talent_field(tid, "round1_invite_sent_at")
        self.assertIsNotNone(actual)
        # ISO with +08:00 suffix
        self.assertIn("+08:00", actual)
        # 大致格式：YYYY-MM-DDTHH:MM:SS
        self.assertRegex(actual, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$")

    def test_atomic_stage_plus_multi_set(self):
        """v3.4 Phase 0.1 关键场景：替代旧 cmd_round1_schedule 的原子写法
        （v3.5 起 wrapper 已彻底删除，本测试就是 agent 直接调原子 CLI 的等价路径）。"""
        tid = _mk_talent(stage="NEW")
        out, err, rc = helpers.call_main("talent.cmd_update", [
            "--talent-id", tid,
            "--stage", "ROUND1_SCHEDULING",
            "--set", "round1_time=2026-04-25 14:00",
            "--set", "round1_invite_sent_at=__NOW__",
            "--set", "round1_confirm_status=PENDING",
            "--set", "round1_calendar_event_id=__NULL__",
            "--set", "wait_return_round=__NULL__",
            "--reason", "boss schedule round1",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        result = json.loads(out)
        # stage 推进
        self.assertEqual(result["transition"]["to"], "ROUND1_SCHEDULING")
        self.assertTrue(result["transition"]["natural"])
        # 5 个字段都落地
        self.assertEqual(len(result["field_changes"]), 5)
        self.assertEqual(
            helpers.mem_tdb.get_talent_current_stage(tid), "ROUND1_SCHEDULING")
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "round1_time"), "2026-04-25 14:00")
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "round1_confirm_status"), "PENDING")
        self.assertIsNone(
            helpers.mem_tdb.get_talent_field(tid, "round1_calendar_event_id"))
        self.assertIsNotNone(
            helpers.mem_tdb.get_talent_field(tid, "round1_invite_sent_at"))

    def test_set_invalid_format_rejected(self):
        tid = _mk_talent()
        out, err, rc = helpers.call_main("talent.cmd_update", [
            "--talent-id", tid,
            "--set", "no_equals_sign",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("FIELD=VALUE", err)

    def test_set_unknown_field_rejected(self):
        tid = _mk_talent()
        out, err, rc = helpers.call_main("talent.cmd_update", [
            "--talent-id", tid,
            "--set", "totally_made_up_field=x",
        ])
        self.assertEqual(rc, 1)
        # 来自 talent_db.get_talent_field 的白名单错误
        self.assertIn("白名单", err)

    def test_legacy_field_value_still_works_with_warning(self):
        tid = _mk_talent()
        out, err, rc = helpers.call_main("talent.cmd_update", [
            "--talent-id", tid,
            "--field", "phone",
            "--value", "13700001111",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        self.assertIn("DeprecationWarning", err)
        self.assertEqual(
            helpers.mem_tdb.get_talent_field(tid, "phone"), "13700001111")

    def test_dry_run_does_not_write(self):
        tid = _mk_talent(stage="NEW")
        out, err, rc = helpers.call_main("talent.cmd_update", [
            "--talent-id", tid,
            "--stage", "ROUND1_SCHEDULING",
            "--set", "round1_time=2026-04-25 14:00",
            "--dry-run",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        self.assertEqual(helpers.mem_tdb.get_talent_current_stage(tid), "NEW")
        self.assertIsNone(helpers.mem_tdb.get_talent_field(tid, "round1_time"))


# ════════════════════════════════════════════════════════════════════════════
# talent/cmd_delete 测试
# ════════════════════════════════════════════════════════════════════════════

class TestCmdDelete(unittest.TestCase):

    def setUp(self):
        helpers.wipe_state()
        import tempfile
        self._archive_dir = tempfile.mkdtemp(prefix="recruit_v33_archive_")
        os.environ["RECRUIT_DELETED_ARCHIVE_DIR"] = self._archive_dir

    def tearDown(self):
        os.environ.pop("RECRUIT_DELETED_ARCHIVE_DIR", None)
        import shutil
        shutil.rmtree(self._archive_dir, ignore_errors=True)

    def test_delete_with_default_backup(self):
        tid = _mk_talent()
        helpers.mem_tdb.insert_email_if_absent(
            talent_id=tid, message_id="<m1@x>", direction="inbound",
            context="round1", sender="x", sent_at="2026-04-17T10:00:00",
        )
        out, err, rc = helpers.call_main("talent.cmd_delete", [
            "--talent-id", tid,
            "--reason", "二面未通过",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        result = json.loads(out)
        self.assertIsNotNone(result["archive_path"])
        self.assertTrue(os.path.exists(result["archive_path"]))
        # 归档内容含原 talent 数据
        with open(result["archive_path"], encoding="utf-8") as f:
            backup = json.load(f)
        self.assertEqual(backup["talent"]["talent_id"], tid)
        self.assertEqual(backup["reason"], "二面未通过")
        # talents 表已删
        self.assertFalse(helpers.mem_tdb.talent_exists(tid))

    def test_delete_no_backup_skips_archive(self):
        tid = _mk_talent()
        out, err, rc = helpers.call_main("talent.cmd_delete", [
            "--talent-id", tid,
            "--reason", "脏数据",
            "--no-backup",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        result = json.loads(out)
        self.assertIsNone(result["archive_path"])
        self.assertFalse(helpers.mem_tdb.talent_exists(tid))

    def test_delete_missing_talent_raises(self):
        out, err, rc = helpers.call_main("talent.cmd_delete", [
            "--talent-id", "t_nope",
            "--reason", "x",
        ])
        self.assertEqual(rc, 1)
        self.assertIn("不存在", err)

    def test_dry_run_does_not_delete_or_archive(self):
        tid = _mk_talent()
        out, err, rc = helpers.call_main("talent.cmd_delete", [
            "--talent-id", tid,
            "--reason", "测试",
            "--dry-run",
            "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        result = json.loads(out)
        self.assertTrue(result["dry_run"])
        self.assertIsNone(result["archive_path"])
        self.assertTrue(helpers.mem_tdb.talent_exists(tid),
                        "dry-run 不应该删除")


if __name__ == "__main__":
    unittest.main(verbosity=2)
