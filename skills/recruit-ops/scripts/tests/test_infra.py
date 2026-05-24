#!/usr/bin/env python3
"""基础设施测试：core_state 状态机 / talent_db 行为 / feishu 模块。"""
import os
import unittest
from unittest import mock

from tests.helpers import call_main, patch_module, real_talent_db, wipe_state


class TestCoreState(unittest.TestCase):

    def test_stages_set_is_complete(self):
        from lib import core_state
        # v3.6: OFFER_HANDOFF / *_DONE_REJECT_DELETE 已下线 (v3.6 migrations
        # 20260427/20260428, v3.8.7 已删档, git log 取)。
        # v3.8: ONBOARDED 终态新增 (v3.8 migration 20260510, v3.8.7 已删档)。
        # v3.8.2: OFFER_DECLINED_KEEP 拆桶 (v3.8.2 migration 20260511, v3.8.7 已删档)。
        # 当前 stages 终态唯一定义于 lib/migrations/schema.sql 的 chk_current_stage CHECK
        # (B2 contract: tests/test_architecture_contracts.py 保证 Python<->DB 同步)。
        expected = {
            "NEW", "ROUND1_SCHEDULING", "ROUND1_SCHEDULED",
            "EXAM_SENT", "EXAM_REVIEWED", "EXAM_REJECT_KEEP", "WAIT_RETURN",
            "ROUND2_SCHEDULING", "ROUND2_SCHEDULED",
            "ROUND2_DONE_REJECT_KEEP",
            "OFFER_DECLINED_KEEP",
            "POST_OFFER_FOLLOWUP",
            "ONBOARDED",
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

    def test_watcher_success_no_feishu_no_failure_event(self):
        from lib import email_watch
        notify_calls, audit_calls = [], []
        with mock.patch.object(email_watch, "_deliver_email", return_value="<ok@test>"), \
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
        notify_calls, audit_calls = [], []

        def _capture_notify(*a, **k):
            notify_calls.append(a)

        def _capture_audit(*a, **k):
            audit_calls.append(a)

        with mock.patch.object(email_watch, "_deliver_email",
                               side_effect=RuntimeError("bad recipient")), \
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


class TestFilePolicy(unittest.TestCase):
    """本地文件外发边界：默认只允许招聘资料白名单目录。"""

    def test_allows_candidate_artifact_under_data_root(self):
        import tempfile
        import shutil
        from pathlib import Path
        from lib.file_policy import validate_sendable_file

        root = tempfile.mkdtemp(prefix="file_policy_")
        old_root = os.environ.get("RECRUIT_DATA_ROOT")
        os.environ["RECRUIT_DATA_ROOT"] = root
        try:
            p = Path(root) / "candidate_cv" / "张三__t_abc" / "cv.pdf"
            p.parent.mkdir(parents=True)
            p.write_bytes(b"%PDF")
            self.assertEqual(validate_sendable_file(str(p)), p.resolve())
        finally:
            if old_root is None:
                os.environ.pop("RECRUIT_DATA_ROOT", None)
            else:
                os.environ["RECRUIT_DATA_ROOT"] = old_root
            shutil.rmtree(root, ignore_errors=True)

    def test_rejects_config_even_with_unsafe_override(self):
        from lib.file_policy import FilePolicyError, validate_sendable_file
        from lib.recruit_paths import workspace_path

        config_path = str(workspace_path("config", "openclaw.example.json"))
        with self.assertRaises(FilePolicyError):
            validate_sendable_file(
                config_path,
                allow_unsafe=True,
                confirm_path=config_path,
            )

    def test_requires_exact_confirm_for_non_whitelisted_file(self):
        import tempfile
        import os as _os
        from pathlib import Path
        from lib.file_policy import FilePolicyError, validate_sendable_file

        fd, path = tempfile.mkstemp(prefix="file_policy_public_", suffix=".txt")
        _os.close(fd)
        try:
            with self.assertRaises(FilePolicyError):
                validate_sendable_file(path)
            self.assertEqual(
                validate_sendable_file(path, allow_unsafe=True, confirm_path=path),
                Path(path).resolve(),
            )
        finally:
            _os.unlink(path)


class TestRoundPrefixWhitelist(unittest.TestCase):
    """A1 (v3.8.6+): talent_db._round_prefix 把 SQL 拼接前缀收口到白名单。

    本测试守住"理论 SQL 注入面"。任何 caller 把非 1/2 的 round_num 透传
    进来都必须 fail-fast,而不是悄悄拼成 'round3' 之类的字段名。
    """

    # helpers._InMemoryTdb 在 sys.modules 把 lib.talent_db 替换掉了, 所以这里
    # 必须走 helpers.real_talent_db（真模块）才能测到 _round_prefix 本体。

    def test_valid_round_numbers_return_prefix(self):
        self.assertEqual(real_talent_db._round_prefix(1), "round1")
        self.assertEqual(real_talent_db._round_prefix(2), "round2")

    def test_invalid_round_numbers_raise_value_error(self):
        for bad in (0, 3, -1, None, "1", "round1", "1; DROP TABLE talents--"):
            with self.assertRaises(ValueError):
                real_talent_db._round_prefix(bad)

    def test_round_time_key_routes_through_whitelist(self):
        self.assertEqual(real_talent_db._round_time_key(1), "round1_time")
        self.assertEqual(real_talent_db._round_time_key(2), "round2_time")
        with self.assertRaises(ValueError):
            real_talent_db._round_time_key(3)


class TestDryRunMasterSwitch(unittest.TestCase):
    """A2 (v3.8.7): RECRUIT_DRY_RUN 主开关替代 4 个旧 env vars。

    本测试守住"主开关 ON ⇒ 4 闸全关"。如果以后有人在 db_enabled /
    side_effects_disabled / db_writes_disabled / cli_wrapper 推送闸里
    单独读旧变量却忘了 OR 主开关, 这里会失败。
    """

    _MANAGED_KEYS = (
        "RECRUIT_DRY_RUN",
        "RECRUIT_DISABLE_SIDE_EFFECTS",
        "RECRUIT_DISABLE_DB_WRITES",
        "RECRUIT_DISABLE_DB",
        "RECRUIT_SUPPRESS_SELF_VERIFY_ALERT",
    )

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._MANAGED_KEYS}
        for k in self._MANAGED_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_master_switch_disables_side_effects(self):
        from lib import side_effect_guard as g
        self.assertFalse(g.side_effects_disabled())
        os.environ["RECRUIT_DRY_RUN"] = "1"
        self.assertTrue(g.side_effects_disabled())
        self.assertTrue(g.db_writes_disabled())
        self.assertTrue(g.dry_run_master())

    def test_legacy_env_still_works_independently(self):
        from lib import side_effect_guard as g
        os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = "1"
        self.assertTrue(g.side_effects_disabled())
        self.assertFalse(g.db_writes_disabled())
        self.assertFalse(g.dry_run_master())

    def test_enable_dry_run_sets_all_compat_vars(self):
        """enable_dry_run() 必须把所有兼容 env vars 一起设上,
        否则不走 lib.side_effect_guard 函数的第三方 / 历史代码会漏闸。"""
        from lib import side_effect_guard as g
        g.enable_dry_run()
        self.assertEqual(os.environ.get("RECRUIT_DRY_RUN"), "1")
        self.assertEqual(os.environ.get("RECRUIT_DISABLE_SIDE_EFFECTS"), "1")
        self.assertEqual(os.environ.get("RECRUIT_DISABLE_DB_WRITES"), "1")
        self.assertEqual(os.environ.get("RECRUIT_DISABLE_DB"), "1")
        self.assertEqual(os.environ.get("RECRUIT_SUPPRESS_SELF_VERIFY_ALERT"), "1")

    def test_master_switch_disables_db_at_config_level(self):
        from lib import config
        os.environ["RECRUIT_DRY_RUN"] = "1"
        self.assertFalse(config.db_enabled())


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
            "【导入候选人】\n姓名：黄琪\n邮箱：2511391@tongji.edu.cn\n当前阶段：待安排二面"
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

    def test_send_text_to_boss_mirrors_to_polaris(self):
        from lib import feishu
        client = object()
        cfg = {
            "boss_open_id": "ou_boss",
            "polaris_open_id": "ou_polaris",
            "scheduler_open_id": "",
        }
        with mock.patch.object(feishu._cfg, "get", return_value=cfg), \
             mock.patch.object(feishu, "_get_client", return_value=client), \
             mock.patch.object(feishu, "side_effects_disabled", return_value=False), \
             mock.patch.object(feishu, "_send_text_with_retry",
                               return_value=True) as send_mock:
            result = feishu.send_text("hello world test")
        self.assertTrue(result)
        self.assertEqual(send_mock.call_count, 2)
        self.assertEqual(send_mock.call_args_list[0].args[1], "ou_boss")
        self.assertEqual(send_mock.call_args_list[1].args[1], "ou_polaris")

    def test_send_text_explicit_target_does_not_mirror(self):
        from lib import feishu
        client = object()
        cfg = {
            "boss_open_id": "ou_boss",
            "polaris_open_id": "ou_polaris",
            "scheduler_open_id": "",
        }
        with mock.patch.object(feishu._cfg, "get", return_value=cfg), \
             mock.patch.object(feishu, "_get_client", return_value=client), \
             mock.patch.object(feishu, "side_effects_disabled", return_value=False), \
             mock.patch.object(feishu, "_send_text_with_retry",
                               return_value=True) as send_mock:
            result = feishu.send_text("hello world test", open_id="ou_hr")
        self.assertTrue(result)
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.args[1], "ou_hr")

    def test_send_text_to_polaris_does_not_mirror_again(self):
        from lib import feishu
        client = object()
        cfg = {
            "boss_open_id": "ou_boss",
            "polaris_open_id": "ou_polaris",
            "scheduler_open_id": "",
        }
        with mock.patch.object(feishu._cfg, "get", return_value=cfg), \
             mock.patch.object(feishu, "_get_client", return_value=client), \
             mock.patch.object(feishu, "side_effects_disabled", return_value=False), \
             mock.patch.object(feishu, "_send_text_with_retry",
                               return_value=True) as send_mock:
            result = feishu.send_text_to_polaris("hello world test")
        self.assertTrue(result)
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.args[1], "ou_polaris")


if __name__ == "__main__":
    unittest.main(verbosity=2)
