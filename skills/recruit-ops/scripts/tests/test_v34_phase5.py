#!/usr/bin/env python3
"""tests/test_v34_phase5.py —— v3.4 Phase 5 测试。

覆盖：
  - feishu.cmd_calendar_create  正常 / dry-run / 错误 / event_id 抠取
  - feishu.cmd_calendar_delete  正常 / dry-run / 失败
  - lib.bg_helpers.spawn_calendar / delete_calendar 的 argv 构造
    （应该走 `python -m feishu.cmd_calendar_*` 而不是直接 exec 旧脚本）
"""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import tests.helpers as helpers  # noqa: F401  side-effect: stub talent_db / env

os.environ["RECRUIT_SUPPRESS_SELF_VERIFY_ALERT"] = "1"


# ════════════════════════════════════════════════════════════════════════════
# feishu.cmd_calendar_create
# ════════════════════════════════════════════════════════════════════════════

class TestCmdCalendarCreate(unittest.TestCase):

    def setUp(self):
        helpers.wipe_state()

    def _call(self, argv):
        return helpers.call_main("feishu.cmd_calendar_create", argv)

    def test_dry_run_short_circuits(self):
        out, err, rc = self._call([
            "--talent-id", "t1",
            "--time", "2026-04-25 14:00",
            "--round", "2",
            "--dry-run", "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dry_run"])
        self.assertIsNone(payload["event_id"])
        self.assertIn("DRY-RUN", payload["message"])

    def test_missing_time_raises_user_input_error(self):
        out, err, rc = self._call([
            "--talent-id", "t1", "--round", "1", "--json",
        ])
        self.assertNotEqual(rc, 0)
        self.assertIn("--time", err)

    def test_success_extracts_event_id_from_message(self):
        with mock.patch(
            "lib.feishu.create_interview_event",
            return_value="二面日历事件创建成功 event_id=evt_abc123 talent_id=t1",
        ) as m:
            out, err, rc = self._call([
                "--talent-id", "t1",
                "--time", "2026-04-25 14:00",
                "--round", "2",
                "--candidate-email", "c@x.com",
                "--candidate-name", "张三",
                "--json",
            ])
        self.assertEqual(rc, 0, "stderr=" + err)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["event_id"], "evt_abc123")
        self.assertEqual(payload["talent_id"], "t1")
        self.assertEqual(payload["round"], 2)

        kwargs = m.call_args.kwargs
        self.assertEqual(kwargs["talent_id"], "t1")
        self.assertEqual(kwargs["interview_time"], "2026-04-25 14:00")
        self.assertEqual(kwargs["round_num"], 2)
        self.assertEqual(kwargs["candidate_email"], "c@x.com")
        self.assertEqual(kwargs["candidate_name"], "张三")

    def test_success_with_no_event_id_in_message(self):
        with mock.patch(
            "lib.feishu.create_interview_event",
            return_value="测试模式：已跳过创建日历事件 talent_id=t1 round=2 time=2026-04-25 14:00",
        ):
            out, err, rc = self._call([
                "--talent-id", "t1", "--time", "2026-04-25 14:00",
                "--round", "2", "--json",
            ])
        self.assertEqual(rc, 0, "stderr=" + err)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertTrue(payload["ok"])
        self.assertIsNone(payload["event_id"])

    def test_underlying_exception_returns_error(self):
        with mock.patch(
            "lib.feishu.create_interview_event",
            side_effect=RuntimeError("飞书 token 失效"),
        ):
            out, err, rc = self._call([
                "--talent-id", "t1", "--time", "2026-04-25 14:00",
                "--round", "2", "--json",
            ])
        self.assertEqual(rc, 1)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertFalse(payload["ok"])
        self.assertIn("飞书 token 失效", payload["error"])
        self.assertEqual(payload["talent_id"], "t1")

    def test_round2_time_alias_still_works(self):
        with mock.patch(
            "lib.feishu.create_interview_event",
            return_value="ok event_id=evt_xyz",
        ) as m:
            out, err, rc = self._call([
                "--talent-id", "t1",
                "--round2-time", "2026-04-25 14:00",
                "--round", "2", "--json",
            ])
        self.assertEqual(rc, 0, "stderr=" + err)
        self.assertEqual(m.call_args.kwargs["interview_time"], "2026-04-25 14:00")

    # ── v3.5.7：--extra-attendee + --duration-minutes ──────────────────────

    def test_extra_attendee_passed_through(self):
        """`--extra-attendee` 多次重复 → 列表透传给 lib.feishu.create_interview_event。"""
        with mock.patch(
            "lib.feishu.create_interview_event",
            return_value="ok event_id=evt_iv",
        ) as m:
            out, err, rc = self._call([
                "--talent-id", "t_iv",
                "--time", "2026-04-25 14:00",
                "--round", "1",
                "--duration-minutes", "30",
                "--extra-attendee", "ou_iv1",
                "--extra-attendee", "ou_iv2",
                "--candidate-name", "张三",
                "--json",
            ])
        self.assertEqual(rc, 0, "stderr=" + err)
        kwargs = m.call_args.kwargs
        self.assertEqual(kwargs["extra_attendee_open_ids"], ["ou_iv1", "ou_iv2"])
        self.assertEqual(kwargs["duration_minutes"], 30)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(payload["extra_attendees"], ["ou_iv1", "ou_iv2"])
        self.assertEqual(payload["duration_minutes"], 30)

    def test_dry_run_echoes_extras(self):
        """dry-run 也要 echo extra_attendees / duration_minutes，方便 chain debug。"""
        out, err, rc = self._call([
            "--talent-id", "t_dr",
            "--time", "2026-04-25 14:00",
            "--round", "1",
            "--duration-minutes", "30",
            "--extra-attendee", "ou_iv1",
            "--dry-run", "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["extra_attendees"], ["ou_iv1"])
        self.assertEqual(payload["duration_minutes"], 30)

    def test_no_extra_attendee_default_empty(self):
        """没传 --extra-attendee → []，不破坏老路径（§5.2）。"""
        with mock.patch(
            "lib.feishu.create_interview_event",
            return_value="ok event_id=evt_old",
        ) as m:
            self._call([
                "--talent-id", "t1",
                "--time", "2026-04-25 14:00",
                "--round", "2", "--json",
            ])
        kwargs = m.call_args.kwargs
        self.assertEqual(kwargs["extra_attendee_open_ids"], [])
        self.assertIsNone(kwargs["duration_minutes"])  # default → None → lib 内部走 60


# ════════════════════════════════════════════════════════════════════════════
# feishu.cmd_calendar_delete
# ════════════════════════════════════════════════════════════════════════════

class TestCmdCalendarDelete(unittest.TestCase):

    def setUp(self):
        helpers.wipe_state()

    def _call(self, argv):
        return helpers.call_main("feishu.cmd_calendar_delete", argv)

    def test_dry_run(self):
        out, err, rc = self._call([
            "--event-id", "evt_x", "--dry-run", "--json",
        ])
        self.assertEqual(rc, 0, "stderr=" + err)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["event_id"], "evt_x")
        self.assertFalse(payload["deleted"])

    def test_success(self):
        with mock.patch(
            "lib.feishu.delete_calendar_event_by_id", return_value=True,
        ) as m:
            out, err, rc = self._call([
                "--event-id", "evt_x", "--reason", "round2_defer", "--json",
            ])
        self.assertEqual(rc, 0, "stderr=" + err)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["deleted"])
        self.assertEqual(payload["reason"], "round2_defer")
        m.assert_called_once_with("evt_x")

    def test_failure_returns_rc1(self):
        with mock.patch(
            "lib.feishu.delete_calendar_event_by_id", return_value=False,
        ):
            out, err, rc = self._call([
                "--event-id", "evt_x", "--json",
            ])
        self.assertEqual(rc, 1)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertTrue(payload["ok"])  # 调用本身没抛异常
        self.assertFalse(payload["deleted"])

    def test_underlying_exception(self):
        with mock.patch(
            "lib.feishu.delete_calendar_event_by_id",
            side_effect=RuntimeError("network down"),
        ):
            out, err, rc = self._call([
                "--event-id", "evt_x", "--json",
            ])
        self.assertEqual(rc, 1)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertFalse(payload["ok"])
        self.assertIn("network down", payload["error"])


# ════════════════════════════════════════════════════════════════════════════
# lib.bg_helpers 子进程命令构造
# ════════════════════════════════════════════════════════════════════════════

class _FakeProc(object):
    def __init__(self, pid=12345):
        self.pid = pid


class TestBgHelpersCalendarDispatch(unittest.TestCase):
    """spawn_calendar / delete_calendar 不应再 exec 旧 lib/feishu/calendar_cli.py，
    而应通过 `python -m feishu.cmd_calendar_*` 启动 atomic CLI。"""

    def setUp(self):
        # bg_helpers 在 side_effects_disabled 时直接返回 fake_pid，不会 Popen，
        # 所以这里要临时关掉守卫，让我们能验证 Popen 命令。
        self._saved = os.environ.pop("RECRUIT_DISABLE_SIDE_EFFECTS", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = self._saved

    def test_spawn_calendar_uses_new_atomic_cli(self):
        from lib import bg_helpers
        with mock.patch.object(bg_helpers.subprocess, "Popen",
                                return_value=_FakeProc(111)) as m_popen:
            pid = bg_helpers.spawn_calendar(
                "t1", "2026-04-25 14:00",
                event_round=1, candidate_email="c@x.com",
                candidate_name="张三", old_event_id="old_evt", tag="round1_confirm",
            )

        self.assertEqual(pid, 111)
        cmd = m_popen.call_args.args[0]
        self.assertIn("-m", cmd)
        self.assertIn("feishu.cmd_calendar_create", cmd)
        self.assertIn("--talent-id", cmd)
        self.assertIn("t1", cmd)
        self.assertIn("--time", cmd)
        self.assertIn("2026-04-25 14:00", cmd)
        self.assertIn("--round", cmd)
        self.assertIn("1", cmd)
        self.assertIn("--candidate-email", cmd)
        self.assertIn("--candidate-name", cmd)
        self.assertIn("--old-event-id", cmd)
        self.assertIn("--json", cmd)
        # 不应该再引用旧脚本
        joined = " ".join(cmd)
        self.assertNotIn("lib/feishu/calendar_cli", joined)

    def test_delete_calendar_uses_new_atomic_cli(self):
        from lib import bg_helpers
        with mock.patch.object(bg_helpers.subprocess, "Popen",
                                return_value=_FakeProc(222)) as m_popen:
            pid = bg_helpers.delete_calendar("evt_xyz", tag="round2_defer")

        self.assertEqual(pid, 222)
        cmd = m_popen.call_args.args[0]
        self.assertIn("-m", cmd)
        self.assertIn("feishu.cmd_calendar_delete", cmd)
        self.assertIn("--event-id", cmd)
        self.assertIn("evt_xyz", cmd)
        self.assertIn("--reason", cmd)
        self.assertIn("round2_defer", cmd)
        self.assertIn("--json", cmd)
        joined = " ".join(cmd)
        self.assertNotIn("lib/feishu/calendar_cli", joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
