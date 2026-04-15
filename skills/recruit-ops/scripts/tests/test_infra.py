#!/usr/bin/env python3
"""基础设施测试：core_state 状态机 / talent_db 行为 / feishu 模块。"""
import os
import unittest
from unittest import mock

from tests.helpers import call_main, real_talent_db, wipe_state


class TestCoreState(unittest.TestCase):

    def test_stages_set_is_complete(self):
        import core_state
        expected = {
            "NEW", "ROUND1_SCHEDULING", "ROUND1_SCHEDULED", "ROUND1_DONE_PASS",
            "ROUND1_DONE_REJECT_KEEP", "ROUND1_DONE_REJECT_DELETE",
            "EXAM_SENT", "EXAM_REVIEWED", "WAIT_RETURN",
            "ROUND2_SCHEDULING", "ROUND2_SCHEDULED", "ROUND2_DONE_PENDING",
            "ROUND2_DONE_PASS", "ROUND2_DONE_REJECT_KEEP", "ROUND2_DONE_REJECT_DELETE",
            "OFFER_HANDOFF",
        }
        self.assertTrue(expected.issubset(core_state.STAGES))

    def test_ensure_stage_transition_ok(self):
        import core_state
        cand = {"talent_id": "t_test", "stage": "NEW", "audit": []}
        ok = core_state.ensure_stage_transition(cand, {"NEW"}, "EXAM_SENT")
        self.assertTrue(ok)
        self.assertEqual(cand["stage"], "EXAM_SENT")

    def test_ensure_stage_transition_wrong_stage(self):
        import core_state
        cand = {"talent_id": "t_test", "stage": "EXAM_SENT", "audit": []}
        ok = core_state.ensure_stage_transition(cand, {"NEW"}, "ROUND2_SCHEDULED")
        self.assertFalse(ok)
        self.assertEqual(cand["stage"], "EXAM_SENT")

    def test_no_round1_score_field(self):
        import core_state
        self.assertNotIn("round1_score", str(dir(core_state)))

    def test_append_audit_keeps_microsecond_precision(self):
        import core_state

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


class TestDbFallback(unittest.TestCase):

    def setUp(self):
        wipe_state()

    def test_save_state_raises_when_db_fails(self):
        """DB 已配置但写入失败时，异常应向上抛出，不静默吞掉。"""
        import types as _types
        import core_state

        fake_tdb = _types.SimpleNamespace()
        fake_tdb._is_enabled = lambda: True
        fake_tdb.sync_state_to_db = mock.Mock(side_effect=RuntimeError("db down"))

        state = {"candidates": {"t_demo": {"talent_id": "t_demo", "stage": "NEW", "audit": []}}}
        with mock.patch.dict(__import__("sys").modules, {"talent_db": fake_tdb}):
            with self.assertRaises(RuntimeError):
                core_state.save_state(state)

    def test_load_state_returns_db_result_directly(self):
        """DB 已配置时直接返回 DB 数据，不做任何 JSON 兜底。"""
        import types as _types
        import core_state

        fake_tdb = _types.SimpleNamespace()
        fake_tdb._is_enabled = lambda: True
        fake_tdb.load_state_from_db = mock.Mock(return_value={
            "candidates": {"t_demo": {"talent_id": "t_demo", "stage": "EXAM_REVIEWED", "audit": []}}
        })
        with mock.patch.dict(__import__("sys").modules, {"talent_db": fake_tdb}):
            state = core_state.load_state()

        self.assertIn("t_demo", state["candidates"])
        fake_tdb.load_state_from_db.assert_called_once()

    def test_import_candidate_syncs_to_db(self):
        """导入候选人时 DB 已配置，应同步并显示已同步。"""
        out, err, rc = call_main("cmd_import_candidate", [
            "--template",
            "【导入候选人】\n姓名：黄琪\n邮箱：2511391@tongji.edu.cn\n当前阶段：待安排二面"
        ])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("已同步", out)

    def test_import_candidate_supports_wait_return_stage(self):
        """补录 WAIT_RETURN 候选人时，应同步 wait_return_round。"""
        from core_state import load_state

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
        import feishu
        self.assertTrue(hasattr(feishu, "send_text"))

    def test_send_text_no_client_returns_false(self):
        import feishu
        with mock.patch.object(feishu, "_get_client", return_value=None), \
             mock.patch.object(feishu, "side_effects_disabled", return_value=False):
            result = feishu.send_text("hello world test")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
