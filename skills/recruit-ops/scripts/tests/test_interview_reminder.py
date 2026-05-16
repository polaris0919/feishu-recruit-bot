#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timedelta
import unittest
from unittest import mock

import tests.helpers as helpers  # noqa: F401


class TestInterviewReminderSelection(unittest.TestCase):

    def test_round1_waits_until_end_plus_buffer(self):
        talent_db = helpers.real_talent_db
        now = datetime.now()
        rows = [{
            "talent_id": "t1",
            "candidate_name": "张三",
            "candidate_email": "z@example.com",
            "round1_time": now - timedelta(minutes=44),
            "round1_reminded_at": None,
        }]
        self.assertEqual(
            talent_db._parse_pending_reminders(
                rows, "round1_time", "round1_reminded_at", 30),
            [],
        )
        rows[0]["round1_time"] = now - timedelta(minutes=45)
        out = talent_db._parse_pending_reminders(
            rows, "round1_time", "round1_reminded_at", 30)
        self.assertEqual(len(out), 1)
        self.assertGreaterEqual(out[0]["elapsed_minutes"], 45)
        self.assertIsNone(out[0]["minutes_since_reminder"])

    def test_repeat_reminder_waits_30_minutes(self):
        talent_db = helpers.real_talent_db
        now = datetime.now()
        rows = [{
            "talent_id": "t2",
            "candidate_name": "李四",
            "candidate_email": "l@example.com",
            "round2_time": now - timedelta(minutes=120),
            "round2_reminded_at": now - timedelta(minutes=29),
        }]
        self.assertEqual(
            talent_db._parse_pending_reminders(
                rows, "round2_time", "round2_reminded_at", 60),
            [],
        )
        rows[0]["round2_reminded_at"] = now - timedelta(minutes=30)
        out = talent_db._parse_pending_reminders(
            rows, "round2_time", "round2_reminded_at", 60)
        self.assertEqual(len(out), 1)
        self.assertGreaterEqual(out[0]["minutes_since_reminder"], 30)


class TestInterviewReminderCommand(unittest.TestCase):

    def test_repeated_reminder_message_and_marks_sent(self):
        from common import cmd_interview_reminder

        round1 = [{
            "talent_id": "t1",
            "candidate_name": "张三",
            "round1_time": "2026-05-16 10:00",
            "elapsed_minutes": 80,
            "minutes_since_reminder": 35,
        }]
        with mock.patch("common.cmd_interview_reminder.talent_db._is_enabled",
                        return_value=True), \
             mock.patch("common.cmd_interview_reminder.talent_db.get_pending_round1_reminders",
                        return_value=round1), \
             mock.patch("common.cmd_interview_reminder.talent_db.get_pending_interview_reminders",
                        return_value=[]), \
             mock.patch("common.cmd_interview_reminder.feishu.send_text",
                        return_value=True) as send_text, \
             mock.patch("common.cmd_interview_reminder.talent_db.mark_round1_reminded") as mark_r1:
            rc = cmd_interview_reminder.main([])

        self.assertEqual(rc, 0)
        send_text.assert_called_once()
        msg = send_text.call_args.args[0]
        self.assertIn("结束后再缓冲 15 分钟", msg)
        self.assertIn("每 30 分钟重复提醒", msg)
        self.assertIn("上次已催问", msg)
        mark_r1.assert_called_once_with("t1")


if __name__ == "__main__":
    unittest.main()
