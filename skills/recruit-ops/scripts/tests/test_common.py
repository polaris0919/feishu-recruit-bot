#!/usr/bin/env python3
"""公共跨阶段操作测试（v3.5 大幅瘦身）。

【v3.5 变更】
  原本这里大量测试 daily_exam_review.main / scan_*reschedule* / cmd_reschedule_request /
  cmd_finalize_interview_time / cmd_wait_return_resume / interview/cmd_{defer,reschedule,confirm}
  内部行为；这些 wrapper 全部在 v3.5 Phase 4 删除，对应剧本现在由 agent 按 docs/AGENT_RULES.md
  编排（用 atomic CLI 拼链）。剧本级测试搬到 tests/test_agent_chain.py。

  本文件只保留与具体 atomic CLI / 只读查询相关的回归测试。
"""
import datetime as dt
import json
import os
import subprocess
import unittest

from tests.helpers import call_main, new_candidate, wipe_state
from lib.core_state import load_candidate, save_candidate

_SCRIPTS = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))


class TestTodayInterviews(unittest.TestCase):

    def setUp(self):
        wipe_state()

    def _set_interview(self, tid, round_num, interview_time, confirm_status):
        cand = load_candidate(tid)
        cand["round{}_time".format(round_num)] = interview_time
        cand["round{}_confirm_status".format(round_num)] = confirm_status
        save_candidate(tid, cand)

    def test_today_interviews_lists_round1_and_round2(self):
        today = dt.datetime.now().strftime("%Y-%m-%d")
        tid1 = new_candidate(name="今日一面人", email="today-r1@example.com")
        tid2 = new_candidate(name="今日二面人", email="today-r2@example.com")
        self._set_interview(tid1, 1, "{} 10:00".format(today), "CONFIRMED")
        self._set_interview(tid2, 2, "{} 15:00".format(today), "PENDING")

        out, err, rc = call_main("common.cmd_today_interviews", [])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("今天（{}）的面试安排".format(today), out)
        self.assertIn("今日一面人 ({}) | 一面 | 已确认".format(tid1), out)
        self.assertIn("今日二面人 ({}) | 二面 | 待确认".format(tid2), out)

    def test_today_interviews_supports_date_filter(self):
        target_date = "2026-04-20"
        other_date = "2026-04-21"
        tid1 = new_candidate(name="指定日期人", email="on-date@example.com")
        tid2 = new_candidate(name="其他日期人", email="other-date@example.com")
        self._set_interview(tid1, 1, "{} 09:30".format(target_date), "CONFIRMED")
        self._set_interview(tid2, 2, "{} 14:00".format(other_date), "CONFIRMED")

        out, err, rc = call_main("common.cmd_today_interviews", ["--date", target_date])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("日期（{}）的面试安排".format(target_date), out)
        self.assertIn("指定日期人 ({})".format(tid1), out)
        self.assertNotIn("其他日期人", out)

    def test_today_interviews_confirmed_only_filters_pending(self):
        today = dt.datetime.now().strftime("%Y-%m-%d")
        confirmed_tid = new_candidate(name="已确认人", email="confirmed@example.com")
        pending_tid = new_candidate(name="待确认人", email="pending@example.com")
        self._set_interview(confirmed_tid, 1, "{} 10:30".format(today), "CONFIRMED")
        self._set_interview(pending_tid, 2, "{} 16:00".format(today), "PENDING")

        out, err, rc = call_main("common.cmd_today_interviews", ["--confirmed-only"])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        self.assertIn("今天（{}）的已确认面试安排".format(today), out)
        self.assertIn("已确认人 ({})".format(confirmed_tid), out)
        self.assertNotIn("待确认人", out)

    def test_today_interviews_json_empty_result(self):
        target_date = "2030-01-01"
        out, err, rc = call_main("common.cmd_today_interviews", ["--date", target_date, "--json"])
        self.assertEqual(rc, 0, "{}|{}".format(out, err))
        payload = json.loads(out)
        self.assertEqual(payload["date"], target_date)
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["items"], [])
        self.assertFalse(payload["confirmed_only"])


class TestAtomicCLIRegression(unittest.TestCase):
    """atomic CLI 直跑回归（替代原 TestRegressionFixes）。"""

    def setUp(self):
        wipe_state()

    def test_calendar_create_can_run_as_standalone_module(self):
        """v3.4 Phase 5 起：feishu.cmd_calendar_create 是 atomic CLI，
        在 RECRUIT_DISABLE_SIDE_EFFECTS=1 下能正常 dry-run + JSON。"""
        env = os.environ.copy()
        env["RECRUIT_DISABLE_SIDE_EFFECTS"] = "1"
        env["PYTHONPATH"] = _SCRIPTS + os.pathsep + env.get("PYTHONPATH", "")

        proc = subprocess.run(
            ["python3", "-m", "feishu.cmd_calendar_create",
             "--talent-id", "t_demo",
             "--time", "2026-04-20 14:00",
             "--round", "2",
             "--json"],
            cwd=_SCRIPTS,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        combined = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
        self.assertEqual(proc.returncode, 0, combined)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(payload.get("ok"), payload)
        self.assertIn("测试模式", payload.get("message") or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
