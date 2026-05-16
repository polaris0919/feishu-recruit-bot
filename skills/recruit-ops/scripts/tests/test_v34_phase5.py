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
from pathlib import Path
import tempfile
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

    def test_round1_dry_run_auto_fills_name_and_routes_interviewer(self):
        from tests.helpers import mem_tdb
        mem_tdb.upsert_one("t_iv_master", {
            "talent_id": "t_iv_master",
            "candidate_name": "黄琪",
            "candidate_email": "huangqi@example.com",
            "education": "博士",
            "has_cpp": False,
            "stage": "ROUND1_SCHEDULING",
        })
        from lib import config as _cfg
        _cfg._ensure_loaded()
        saved = dict(_cfg._cache.get("feishu") or {})
        _cfg._cache["feishu"] = dict(saved)
        _cfg._cache["feishu"].update({
            "interviewer_master_open_id": "ou_master_real",
            "interviewer_bachelor_open_id": "ou_bach_real",
            "interviewer_cpp_open_id": "ou_cpp_real",
        })
        try:
            out, err, rc = self._call([
                "--talent-id", "t_iv_master",
                "--time", "2026-04-25 14:00",
                "--round", "1",
                "--duration-minutes", "30",
                "--dry-run", "--json",
            ])
        finally:
            _cfg._cache["feishu"] = saved
        self.assertEqual(rc, 0, "stderr=" + err)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(payload["candidate_name"], "黄琪")
        self.assertEqual(payload["candidate_email"], "huangqi@example.com")
        self.assertEqual(payload["extra_attendees"], ["ou_master_real"])
        self.assertEqual(payload["route"]["interviewer_roles"], ["master"])

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
        self.assertTrue(kwargs["attach_cv"])

    def test_success_extracts_event_id_from_chinese_message(self):
        """v3.8.6: 真实飞书返回是中文 '事件ID: xxx'，JSON 必须直接给 event_id。

        否则上游 agent 会从 message 里猜 ID，再只写 calendar_event_id 字段，
        很容易留下 SCHEDULING/PENDING + 已有日历的半状态。
        """
        message = (
            "已在飞书日历创建一面事件：[一面] 何卓远\n"
            "  - 时间: 2026-05-15 09:30\n"
            "  - 时长: 30 分钟\n"
            "  - 事件ID: 9538b254-c881-4d91-aa8f-e3f549e9734c_0\n"
            "  - 直达链接: https://applink.feishu.cn/client/calendar/event/detail?calendarId=xxx"
        )
        with mock.patch("lib.feishu.create_interview_event", return_value=message):
            out, err, rc = self._call([
                "--talent-id", "t_cn",
                "--time", "2026-05-15 09:30",
                "--round", "2",
                "--json",
            ])
        self.assertEqual(rc, 0, "stderr=" + err)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertTrue(payload["ok"])
        self.assertEqual(
            payload["event_id"],
            "9538b254-c881-4d91-aa8f-e3f549e9734c_0")

    def test_success_extracts_event_id_from_chinese_fullwidth_colon(self):
        """中文全角冒号也要支持。"""
        with mock.patch(
            "lib.feishu.create_interview_event",
            return_value="已创建日历\n事件ID：abc_def-123_0\n完成",
        ):
            out, err, rc = self._call([
                "--talent-id", "t_cn2",
                "--time", "2026-05-15 09:30",
                "--round", "2",
                "--json",
            ])
        self.assertEqual(rc, 0, "stderr=" + err)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(payload["event_id"], "abc_def-123_0")

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
        self.assertTrue(kwargs["attach_cv"])
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(payload["extra_attendees"], ["ou_iv1", "ou_iv2"])
        self.assertEqual(payload["duration_minutes"], 30)
        self.assertTrue(payload["attach_cv"])

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
        self.assertTrue(payload["attach_cv"])

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

    def test_no_attach_cv_passed_through(self):
        """允许特殊场景关闭 CV 日程附件。"""
        with mock.patch(
            "lib.feishu.create_interview_event",
            return_value="ok event_id=evt_no_cv",
        ) as m:
            out, err, rc = self._call([
                "--talent-id", "t1",
                "--time", "2026-04-25 14:00",
                "--round", "2",
                "--no-attach-cv",
                "--json",
            ])
        self.assertEqual(rc, 0, "stderr=" + err)
        self.assertFalse(m.call_args.kwargs["attach_cv"])
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertFalse(payload["attach_cv"])


class TestCalendarCvAttachment(unittest.TestCase):

    def setUp(self):
        helpers.wipe_state()
        os.environ.pop("RECRUIT_DISABLE_SIDE_EFFECTS", None)

    def tearDown(self):
        os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = "1"

    def _client(self, event_id="evt_cv", upload_token="file_token_cv"):
        upload_resp = mock.Mock()
        upload_resp.success.return_value = True
        upload_resp.data.file_token = upload_token
        create_resp = mock.Mock()
        create_resp.success.return_value = True
        create_resp.data.event.event_id = event_id
        create_resp.data.event.app_link = ""
        att_resp = mock.Mock()
        att_resp.success.return_value = True
        client = mock.Mock()
        client.drive.v1.media.upload_all.return_value = upload_resp
        client.calendar.v4.calendar_event.create.return_value = create_resp
        client.calendar.v4.calendar_event_attendee.create.return_value = att_resp
        return client

    def _attendee_user_ids(self, client):
        req = client.calendar.v4.calendar_event_attendee.create.call_args.args[0]
        return [att.user_id for att in req.request_body.attendees]

    def test_round1_attendees_are_boss_polaris_and_interviewer(self):
        from lib import feishu
        client = self._client(event_id="evt_attendees")
        with mock.patch("lib.feishu._get_client", return_value=client), \
             mock.patch("lib.config.get", return_value={
                 "app_id": "cli_x",
                 "app_secret": "sec",
                 "calendar_id": "cal_x",
                 "boss_open_id": "ou_boss",
                 "polaris_open_id": "ou_polaris",
                 "interviewer_bachelor_open_id": "ou_bachelor",
             }):
            msg = feishu.create_interview_event(
                talent_id="t_attendees",
                interview_time="2026-04-25 14:00",
                round_num=1,
                candidate_name="黄琪",
                extra_attendee_open_ids=["ou_master"],
                duration_minutes=30,
                attach_cv=False,
            )

        self.assertIn("老板、Polaris（日程安排者）、面试官（共 3 人）", msg)
        self.assertEqual(
            self._attendee_user_ids(client),
            ["ou_boss", "ou_polaris", "ou_master"],
        )
        self.assertNotIn("ou_bachelor", self._attendee_user_ids(client))

    def test_round2_attendees_are_boss_and_polaris_only(self):
        from lib import feishu
        client = self._client(event_id="evt_round2_attendees")
        with mock.patch("lib.feishu._get_client", return_value=client), \
             mock.patch("lib.config.get", return_value={
                 "app_id": "cli_x",
                 "app_secret": "sec",
                 "calendar_id": "cal_x",
                 "boss_open_id": "ou_boss",
                 "polaris_open_id": "ou_polaris",
                 "interviewer_bachelor_open_id": "ou_bachelor",
             }):
            msg = feishu.create_interview_event(
                talent_id="t_round2_attendees",
                interview_time="2026-04-25 14:00",
                round_num=2,
                candidate_name="黄琪",
                attach_cv=False,
            )

        self.assertIn("老板、Polaris（日程安排者）（共 2 人）", msg)
        self.assertEqual(self._attendee_user_ids(client), ["ou_boss", "ou_polaris"])

    def test_create_event_uploads_cv_as_calendar_attachment(self):
        from lib import feishu
        from tests.helpers import mem_tdb

        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(b"%PDF-1.4 demo")
            f.flush()
            mem_tdb.upsert_one("t_cv", {
                "talent_id": "t_cv",
                "stage": "ROUND1_SCHEDULED",
                "candidate_name": "张三",
                "candidate_email": "c@example.com",
                "cv_path": f.name,
            })
            client = self._client()
            with mock.patch("lib.feishu._get_client", return_value=client), \
                 mock.patch("lib.config.get", return_value={
                     "app_id": "cli_x",
                     "app_secret": "sec",
                     "calendar_id": "cal_x",
                     "boss_open_id": "ou_boss",
                     "polaris_open_id": "ou_polaris",
                 }):
                msg = feishu.create_interview_event(
                    talent_id="t_cv",
                    interview_time="2026-04-25 14:00",
                    round_num=1,
                    candidate_email="c@example.com",
                    candidate_name="张三",
                    duration_minutes=30,
                )

        self.assertIn("CV附件：已上传并挂载", msg)
        self.assertTrue(client.drive.v1.media.upload_all.called)
        req = client.calendar.v4.calendar_event.create.call_args.args[0]
        event = req.request_body
        self.assertEqual(event.attachments[0].file_token, "file_token_cv")

    def test_missing_cv_does_not_block_calendar_create(self):
        from lib import feishu
        from tests.helpers import mem_tdb

        mem_tdb.upsert_one("t_no_cv", {
            "talent_id": "t_no_cv",
            "stage": "ROUND1_SCHEDULED",
            "candidate_name": "张三",
            "candidate_email": "c@example.com",
            "cv_path": "",
        })
        client = self._client(event_id="evt_no_cv")
        with mock.patch("lib.feishu._get_client", return_value=client), \
             mock.patch("lib.config.get", return_value={
                 "app_id": "cli_x",
                 "app_secret": "sec",
                 "calendar_id": "cal_x",
                 "boss_open_id": "ou_boss",
                 "polaris_open_id": "ou_polaris",
             }):
            msg = feishu.create_interview_event(
                talent_id="t_no_cv",
                interview_time="2026-04-25 14:00",
                round_num=1,
                candidate_email="c@example.com",
                candidate_name="张三",
            )

        self.assertIn("CV附件：未找到 cv_path 或 candidates/<tid>/cv 文件，已跳过", msg)
        self.assertFalse(client.drive.v1.media.upload_all.called)

    def test_create_event_falls_back_to_candidate_cv_dir(self):
        from lib import feishu
        from tests.helpers import mem_tdb

        with tempfile.TemporaryDirectory() as tmp:
            old_root = os.environ.get("RECRUIT_DATA_ROOT")
            os.environ["RECRUIT_DATA_ROOT"] = tmp
            try:
                cv_dir = Path(tmp) / "candidates" / "t_cv_dir" / "cv"
                cv_dir.mkdir(parents=True)
                cv_file = cv_dir / "候选人简历.pdf"
                cv_file.write_bytes(b"%PDF-1.4 demo")
                mem_tdb.upsert_one("t_cv_dir", {
                    "talent_id": "t_cv_dir",
                    "stage": "ROUND1_SCHEDULED",
                    "candidate_name": "张三",
                    "candidate_email": "c@example.com",
                    "cv_path": "",
                })
                client = self._client(upload_token="file_token_dir")
                with mock.patch("lib.feishu._get_client", return_value=client), \
                     mock.patch("lib.config.get", return_value={
                         "app_id": "cli_x",
                         "app_secret": "sec",
                         "calendar_id": "cal_x",
                         "boss_open_id": "ou_boss",
                         "polaris_open_id": "ou_polaris",
                     }):
                    msg = feishu.create_interview_event(
                        talent_id="t_cv_dir",
                        interview_time="2026-04-25 14:00",
                        round_num=1,
                        candidate_email="c@example.com",
                        candidate_name="张三",
                    )
            finally:
                if old_root is None:
                    os.environ.pop("RECRUIT_DATA_ROOT", None)
                else:
                    os.environ["RECRUIT_DATA_ROOT"] = old_root

        self.assertIn("source=cv_dir", msg)
        req = client.calendar.v4.calendar_event.create.call_args.args[0]
        event = req.request_body
        self.assertEqual(event.attachments[0].file_token, "file_token_dir")


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
        # A2 (v3.8.7): 主开关 RECRUIT_DRY_RUN 也会触发 side_effects_disabled,
        # 所以同时 pop。
        self._saved_side_effects = os.environ.pop("RECRUIT_DISABLE_SIDE_EFFECTS", None)
        self._saved_dry_run = os.environ.pop("RECRUIT_DRY_RUN", None)

    def tearDown(self):
        if self._saved_side_effects is not None:
            os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = self._saved_side_effects
        if self._saved_dry_run is not None:
            os.environ["RECRUIT_DRY_RUN"] = self._saved_dry_run

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
