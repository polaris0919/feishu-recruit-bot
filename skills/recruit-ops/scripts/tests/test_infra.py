#!/usr/bin/env python3
"""基础设施测试：core_state 状态机 / talent_db 行为 / feishu 模块。"""
import os
import unittest
from unittest import mock

from tests.helpers import call_main, patch_module, real_talent_db, wipe_state


class TestCoreState(unittest.TestCase):

    def test_stages_set_is_complete(self):
        from lib import core_state
        # v3.6: OFFER_HANDOFF / *_DONE_REJECT_DELETE 已下线，见
        # 20260427_v36_drop_offer_handoff.sql / 20260428_v36_drop_done_reject_delete.sql。
        expected = {
            "NEW", "ROUND1_SCHEDULING", "ROUND1_SCHEDULED",
            "EXAM_SENT", "EXAM_REVIEWED", "EXAM_REJECT_KEEP", "WAIT_RETURN",
            "ROUND2_SCHEDULING", "ROUND2_SCHEDULED",
            "ROUND2_DONE_REJECT_KEEP",
            "POST_OFFER_FOLLOWUP",
        }
        self.assertEqual(expected, core_state.STAGES)

    def test_stages_do_not_include_dropped(self):
        """v3.6 删的 3 个 stage 不应再出现在 STAGES / STAGE_LABELS。"""
        from lib import core_state
        dropped = {"OFFER_HANDOFF", "ROUND1_DONE_REJECT_DELETE",
                   "ROUND2_DONE_REJECT_DELETE"}
        self.assertFalse(dropped & core_state.STAGES)
        self.assertFalse(dropped & set(core_state.STAGE_LABELS.keys()))

    def test_ensure_stage_transition_ok(self):
        from lib import core_state
        cand = {"talent_id": "t_test", "stage": "NEW", "audit": []}
        ok = core_state.ensure_stage_transition(cand, {"NEW"}, "EXAM_SENT")
        self.assertTrue(ok)
        self.assertEqual(cand["stage"], "EXAM_SENT")

    def test_ensure_stage_transition_wrong_stage(self):
        from lib import core_state
        cand = {"talent_id": "t_test", "stage": "EXAM_SENT", "audit": []}
        ok = core_state.ensure_stage_transition(cand, {"NEW"}, "ROUND2_SCHEDULED")
        self.assertFalse(ok)
        self.assertEqual(cand["stage"], "EXAM_SENT")

    def test_no_round1_score_field(self):
        from lib import core_state
        self.assertNotIn("round1_score", str(dir(core_state)))

    def test_append_audit_keeps_microsecond_precision(self):
        from lib import core_state

        cand = {"talent_id": "t_test", "stage": "NEW", "audit": []}
        core_state.append_audit(cand, "system", "first")
        core_state.append_audit(cand, "system", "second")

        self.assertEqual(len(cand["audit"]), 2)
        self.assertTrue(cand["audit"][0]["event_id"])
        self.assertTrue(cand["audit"][1]["event_id"])
        self.assertIn(".", cand["audit"][0]["at"])
        self.assertIn(".", cand["audit"][1]["at"])
        self.assertNotEqual(cand["audit"][0]["at"], cand["audit"][1]["at"])
        self.assertNotEqual(cand["audit"][0]["event_id"], cand["audit"][1]["event_id"])

    def test_insert_events_backfills_legacy_event_id_deterministically(self):
        calls = []

        class _FakeCursor:
            def execute(self, sql, params):
                calls.append((sql, params))

        legacy_entry = {
            "at": "2026-04-15T12:00:00.123456+08:00",
            "actor": "system",
            "action": "legacy_event",
            "payload": {"a": 1},
        }

        real_talent_db._insert_events(_FakeCursor(), "t_demo", [legacy_entry])

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1][1], "t_demo")
        self.assertEqual(calls[0][1][0], legacy_entry["event_id"])

        same_entry = {
            "at": "2026-04-15T12:00:00.123456+08:00",
            "actor": "system",
            "action": "legacy_event",
            "payload": {"a": 1},
        }
        same_event_id = real_talent_db._event_values("t_demo", same_entry)[0]
        self.assertEqual(same_event_id, legacy_entry["event_id"])


class TestEmailWatch(unittest.TestCase):
    """SMTP 投递 watcher：失败必须回告警 + 写 audit 事件。"""

    def _fake_send_script(self, exit_code, stderr_msg=""):
        """在 /tmp 写一个 mock email_send.py，按指定 exit code 退出。"""
        import tempfile, textwrap
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", prefix="fake_email_send_",
            delete=False,
        )
        f.write(textwrap.dedent("""\
            import sys
            sys.stderr.write({stderr!r})
            sys.exit({rc})
        """).format(rc=exit_code, stderr=stderr_msg))
        f.close()
        return f.name

    def test_watcher_success_no_feishu_no_failure_event(self):
        from lib import email_watch
        script = self._fake_send_script(0)
        notify_calls, audit_calls = [], []
        with mock.patch.object(email_watch, "_resolve_email_send_script", return_value=script), \
             mock.patch.object(email_watch, "_notify_boss_failure", side_effect=lambda *a, **k: notify_calls.append(a)), \
             mock.patch.object(email_watch, "_record_failure_event", side_effect=lambda *a, **k: audit_calls.append(a)):
            rc = email_watch.main([
                "--to", "ok@example.com", "--subject", "S", "--body", "B",
                "--tag", "test_ok", "--talent-id", "t_demo",
            ])
        self.assertEqual(rc, 0)
        self.assertEqual(notify_calls, [], "成功路径不应触发飞书告警")
        self.assertEqual(audit_calls, [], "成功路径不应写 failure 事件")

    def test_watcher_failure_triggers_feishu_and_audit(self):
        from lib import email_watch
        script = self._fake_send_script(1, stderr_msg="❌ Failed to send email: bad recipient")
        notify_calls, audit_calls = [], []

        def _capture_notify(*a, **k):
            notify_calls.append(a)

        def _capture_audit(*a, **k):
            audit_calls.append(a)

        with mock.patch.object(email_watch, "_resolve_email_send_script", return_value=script), \
             mock.patch.object(email_watch, "_notify_boss_failure", side_effect=_capture_notify), \
             mock.patch.object(email_watch, "_record_failure_event", side_effect=_capture_audit):
            rc = email_watch.main([
                "--to", "bad@example.com", "--subject", "S", "--body", "B",
                "--tag", "test_fail", "--talent-id", "t_demo",
                "--candidate-name", "测试人",
            ])
        self.assertEqual(rc, 1)
        self.assertEqual(len(notify_calls), 1, "失败必须触发一次飞书告警")
        self.assertEqual(len(audit_calls), 1, "失败必须写一次 talent_events email_smtp_failed 事件")
        # talent_id, to, subject, tag, exit_code, log_path
        a = audit_calls[0]
        self.assertEqual(a[0], "t_demo")
        self.assertEqual(a[1], "bad@example.com")
        self.assertEqual(a[4], 1)


class TestDbFallback(unittest.TestCase):

    def setUp(self):
        wipe_state()

    def test_save_state_raises_when_db_fails(self):
        """DB 已配置但写入失败时，异常应向上抛出，不静默吞掉。"""
        import types as _types
        from lib import core_state

        fake_tdb = _types.SimpleNamespace()
        fake_tdb._is_enabled = lambda: True
        fake_tdb.sync_state_to_db = mock.Mock(side_effect=RuntimeError("db down"))

        state = {"candidates": {"t_demo": {"talent_id": "t_demo", "stage": "NEW", "audit": []}}}
        with patch_module("talent_db", fake_tdb):
            with self.assertRaises(RuntimeError):
                core_state.save_state(state)

    def test_load_state_returns_db_result_directly(self):
        """DB 已配置时直接返回 DB 数据，不做任何 JSON 兜底。"""
        import types as _types
        from lib import core_state

        fake_tdb = _types.SimpleNamespace()
        fake_tdb._is_enabled = lambda: True
        fake_tdb.load_state_from_db = mock.Mock(return_value={
            "candidates": {"t_demo": {"talent_id": "t_demo", "stage": "EXAM_REVIEWED", "audit": []}}
        })
        with patch_module("talent_db", fake_tdb):
            state = core_state.load_state()

        self.assertIn("t_demo", state["candidates"])
        fake_tdb.load_state_from_db.assert_called_once()

    def test_import_candidate_syncs_to_db(self):
        """导入候选人时 DB 已配置，应同步并显示已同步。"""
        out, err, rc = call_main("cmd_import_candidate", [
            "--template",
            "【导入候选人】\n姓名：候选人K\n邮箱：candidate-k@example.com\n当前阶段：待安排二面"
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("已同步", out)

    def test_import_candidate_supports_wait_return_stage(self):
        """补录 WAIT_RETURN 候选人时，应同步 wait_return_round。"""
        from lib.core_state import load_state

        out, err, rc = call_main("cmd_import_candidate", [
            "--template",
            "【导入候选人】\n姓名：李四\n邮箱：lisi@example.com\n当前阶段：待回国后二面"
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("WAIT_RETURN", out)

        candidates = list(load_state().get("candidates", {}).values())
        self.assertEqual(len(candidates), 1)
        cand = candidates[0]
        self.assertEqual(cand["stage"], "WAIT_RETURN")
        self.assertEqual(cand["wait_return_round"], 2)

    def test_talent_db_is_enabled_respects_no_config(self):
        """RECRUIT_DISABLE_DB 置位时 _is_enabled 返回 False。"""
        with mock.patch.dict(os.environ, {"RECRUIT_DISABLE_DB": "1"}):
            self.assertFalse(real_talent_db._is_enabled())

    def test_talent_db_load_state_disabled_returns_empty(self):
        """DB 禁用时 load_state_from_db 返回空候选人，不连接 DB。"""
        with mock.patch.dict(os.environ, {"RECRUIT_DISABLE_DB": "1"}):
            state = real_talent_db.load_state_from_db()
        self.assertEqual(state, {"candidates": {}})

    def test_talent_db_sync_state_disabled_returns_false(self):
        """DB 禁用时 sync_state_to_db 返回 False，不连接 DB。"""
        with mock.patch.dict(os.environ, {"RECRUIT_DISABLE_DB": "1"}):
            ok = real_talent_db.sync_state_to_db({"candidates": {"t_x": {}}})
        self.assertFalse(ok)


class TestFeishu(unittest.TestCase):

    def test_import_feishu(self):
        from lib import feishu
        self.assertTrue(hasattr(feishu, "send_text"))

    def test_send_text_no_client_returns_false(self):
        from lib import feishu
        with mock.patch.object(feishu, "_get_client", return_value=None), \
             mock.patch.object(feishu, "side_effects_disabled", return_value=False):
            result = feishu.send_text("hello world test")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
