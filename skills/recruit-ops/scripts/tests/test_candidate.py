#!/usr/bin/env python3
"""候选人基础操作测试：新建 / 查状态 / 搜索 / 删除。"""
import json
import re
import unittest
from unittest import mock

from tests.helpers import call_main, new_candidate, wipe_state
from core_state import load_candidate


class CandidateFlowTestCase(unittest.TestCase):

    def setUp(self):
        wipe_state()

    def assert_cli_ok(self, module_name, argv):
        out, err, rc = call_main(module_name, argv)
        self.assertEqual(
            rc, 0,
            "命令 {} 应成功。\nstdout:\n{}\nstderr:\n{}".format(module_name, out, err),
        )
        self.assertNotIn(
            "EXCEPTION:", err,
            "命令 {} 不应因异常失败。\nstderr:\n{}".format(module_name, err),
        )
        return out, err

    def assert_cli_business_fail(self, module_name, argv, expected_text=None):
        out, err, rc = call_main(module_name, argv)
        combined = "\n".join(part for part in (out, err) if part)
        self.assertNotEqual(rc, 0, "命令 {} 应失败".format(module_name))
        self.assertNotIn(
            "EXCEPTION:", combined,
            "命令 {} 不应靠异常实现失败。\n输出:\n{}".format(module_name, combined),
        )
        if expected_text:
            self.assertIn(expected_text, combined)
        return out, err

    def extract_talent_id(self, text):
        m = re.search(r"t_[a-z0-9]{6}", text)
        self.assertIsNotNone(m, "输出中应包含 talent_id:\n{}".format(text))
        return m.group(0)


class TestNewCandidate(CandidateFlowTestCase):

    def test_creates_candidate(self):
        out, _ = self.assert_cli_ok("cmd_new_candidate", [
            "--name", "王芳", "--email", "wf@test.com",
        ])
        self.assertIn("已录入", out)
        tid = self.extract_talent_id(out)
        cand = load_candidate(tid)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["candidate_name"], "王芳")
        self.assertEqual(cand["candidate_email"], "wf@test.com")
        self.assertEqual(cand["stage"], "NEW")

    def test_talent_id_is_unique(self):
        id1 = new_candidate("A", "a@a.com")
        id2 = new_candidate("B", "b@b.com")
        self.assertNotEqual(id1, id2)

    def test_talent_id_format(self):
        tid = new_candidate()
        self.assertRegex(tid, r"^t_[a-z0-9]{6}$")

    def test_optional_fields_are_persisted(self):
        out, _ = self.assert_cli_ok("cmd_new_candidate", [
            "--name", "李梅", "--email", "lm@x.com",
            "--position", "产品经理",
            "--education", "本科",
            "--school", "复旦大学",
            "--work-years", "3",
            "--source", "猎头",
        ])
        tid = self.extract_talent_id(out)
        cand = load_candidate(tid)
        self.assertEqual(cand["position"], "产品经理")
        self.assertEqual(cand["education"], "本科")
        self.assertEqual(cand["school"], "复旦大学")
        self.assertEqual(cand["work_years"], 3)
        self.assertEqual(cand["source"], "猎头")

    def test_missing_name_fails_with_validation_message(self):
        self.assert_cli_business_fail(
            "cmd_new_candidate",
            ["--email", "x@x.com"],
            "--name 必填",
        )

    def test_missing_email_fails_with_validation_message(self):
        self.assert_cli_business_fail(
            "cmd_new_candidate",
            ["--name", "测试"],
            "--email 必填",
        )


class TestStatus(CandidateFlowTestCase):

    def test_status_shows_name(self):
        tid = new_candidate("刘洋", "ly@x.com")
        out, _ = self.assert_cli_ok("cmd_status", ["--talent-id", tid])
        self.assertIn("刘洋", out)
        self.assertIn(tid, out)

    def test_status_shows_stage(self):
        tid = new_candidate()
        out, _ = self.assert_cli_ok("cmd_status", ["--talent-id", tid])
        self.assertIn("NEW", out)
        self.assertIn("新建", out)

    def test_status_not_found(self):
        self.assert_cli_business_fail(
            "cmd_status",
            ["--talent-id", "t_notexist"],
            "未找到",
        )

    def test_status_all_lists_all_candidates(self):
        tid1 = new_candidate("A", "a@a.com")
        tid2 = new_candidate("B", "b@b.com")
        out, _ = self.assert_cli_ok("cmd_status", ["--all"])
        self.assertIn("2 位候选人", out)
        self.assertIn(tid1, out)
        self.assertIn(tid2, out)
        self.assertIn("A", out)
        self.assertIn("B", out)

    def test_status_shows_round2_after_exam_pass(self):
        tid = new_candidate("刘洋", "ly@x.com")
        self.assert_cli_ok("cmd_round1_result", [
            "--talent-id", tid, "--result", "pass", "--email", "ly@x.com",
        ])
        self.assert_cli_ok("cmd_exam_result", [
            "--talent-id", tid, "--result", "pass",
            "--round2-time", "2026-04-01 14:00",
        ])
        out, _ = self.assert_cli_ok("cmd_status", ["--talent-id", tid])
        cand = load_candidate(tid)
        self.assertEqual(cand["stage"], "ROUND2_SCHEDULING")
        self.assertEqual(cand["round2_time"], "2026-04-01 14:00")
        self.assertEqual(cand["round2_confirm_status"], "PENDING")
        self.assertIn("二面时间: 2026-04-01 14:00", out)
        self.assertIn("ROUND2_SCHEDULING", out)

    def test_status_shows_round1_time_and_reschedule_pending_via_real_flow(self):
        tid = new_candidate("袁泽生", "yzs@test.com")
        self.assert_cli_ok("cmd_round1_schedule", [
            "--talent-id", tid, "--time", "2026-03-27 15:00",
        ])
        self.assert_cli_ok("cmd_reschedule_request", [
            "--talent-id", tid,
            "--round", "1",
            "--reason", "候选人临时有事",
        ])

        out, _ = self.assert_cli_ok("cmd_status", ["--talent-id", tid])
        cand = load_candidate(tid)
        self.assertEqual(cand["stage"], "ROUND1_SCHEDULING")
        self.assertEqual(cand["round1_time"], "2026-03-27 15:00")
        self.assertEqual(cand["round1_confirm_status"], "PENDING")
        self.assertEqual(cand["audit"][-1]["action"], "round1_reschedule_requested")
        self.assertIn("一面时间: 2026-03-27 15:00", out)
        self.assertIn("一面改期申请（待处理）", out)

    def test_status_shows_wait_return_round(self):
        tid = new_candidate("海外候选人", "overseas@test.com")
        self.assert_cli_ok("cmd_round1_schedule", [
            "--talent-id", tid, "--time", "2026-04-08 09:30",
        ])
        import cmd_round1_defer
        with mock.patch.object(cmd_round1_defer, "_send_defer_email", return_value=2468):
            self.assert_cli_ok("cmd_round1_defer", ["--talent-id", tid])

        out, _ = self.assert_cli_ok("cmd_status", ["--talent-id", tid])
        self.assertIn("WAIT_RETURN", out)
        self.assertIn("暂缓轮次: 一面", out)


class TestSearch(CandidateFlowTestCase):

    def test_search_by_email_returns_exact_candidate(self):
        tid = new_candidate("赵磊", "zhaolei@search.com")
        out, _ = self.assert_cli_ok("cmd_search", ["--query", "zhaolei"])
        data = json.loads(out)
        self.assertEqual(data["found"], 1)
        self.assertEqual(data["candidates"][0]["talent_id"], tid)
        self.assertEqual(data["candidates"][0]["candidate_email"], "zhaolei@search.com")

    def test_search_by_name_returns_exact_candidate(self):
        tid = new_candidate("唐书涵", "tsh@x.com")
        out, _ = self.assert_cli_ok("cmd_search", ["--query", "唐书涵"])
        data = json.loads(out)
        self.assertEqual(data["found"], 1)
        self.assertEqual(data["candidates"][0]["talent_id"], tid)
        self.assertEqual(data["candidates"][0]["candidate_name"], "唐书涵")

    def test_search_by_talent_id(self):
        tid = new_candidate()
        out, _ = self.assert_cli_ok("cmd_search", ["--query", tid])
        data = json.loads(out)
        self.assertEqual(data["found"], 1)
        self.assertEqual(data["candidates"][0]["talent_id"], tid)

    def test_search_no_result_returns_business_message(self):
        out, err = self.assert_cli_business_fail(
            "cmd_search",
            ["--query", "doesnotexist99999"],
            "未找到匹配的候选人",
        )
        data = json.loads(out or "{}")
        self.assertEqual(data.get("found"), 0)
        self.assertEqual(data.get("candidates"), [])
        self.assertEqual(data.get("message"), "未找到匹配的候选人")
        self.assertEqual(err, "")

    def test_search_all_active_excludes_rejected_candidates(self):
        active_tid = new_candidate("进行中", "active@x.com")
        rejected_tid = new_candidate("已淘汰", "reject@x.com")
        self.assert_cli_ok("cmd_round1_result", [
            "--talent-id", rejected_tid, "--result", "reject_keep",
        ])

        out, _ = self.assert_cli_ok("cmd_search", ["--all-active"])
        data = json.loads(out)
        ids = [cand["talent_id"] for cand in data["candidates"]]
        self.assertIn(active_tid, ids)
        self.assertNotIn(rejected_tid, ids)

    def test_search_includes_round1_time_and_confirmed_flags_via_real_flow(self):
        tid = new_candidate("王浩铖", "whc@test.com")
        self.assert_cli_ok("cmd_round1_schedule", [
            "--talent-id", tid, "--time", "2026-04-08 09:30",
        ])

        import interview.cmd_confirm as cmd_confirm
        with mock.patch.object(cmd_confirm, "_spawn_calendar_bg", return_value=2468):
            self.assert_cli_ok("cmd_round1_confirm", ["--talent-id", tid])

        out, _ = self.assert_cli_ok("cmd_search", ["--query", "王浩铖"])
        data = json.loads(out)
        cand = data["candidates"][0]
        self.assertEqual(cand["talent_id"], tid)
        self.assertEqual(cand["round1_time"], "2026-04-08 09:30")
        self.assertEqual(cand["round1_confirm_status"], "CONFIRMED")
        self.assertEqual(cand["round1_status"], "confirmed")
        self.assertEqual(cand["next_interview_round"], 1)
        self.assertEqual(cand["next_interview_time"], "2026-04-08 09:30")
        self.assertTrue(cand["next_interview_confirmed"])


class TestRemove(CandidateFlowTestCase):

    def test_remove_without_confirm_fails(self):
        tid = new_candidate()
        self.assert_cli_business_fail(
            "cmd_remove",
            ["--talent-id", tid],
            "--confirm",
        )

    def test_remove_with_confirm_really_deletes_candidate(self):
        tid = new_candidate()
        out, _ = self.assert_cli_ok("cmd_remove", ["--talent-id", tid, "--confirm"])
        data = json.loads(out)
        self.assertTrue(data["ok"])
        self.assertEqual(data["talent_id"], tid)
        self.assertIsNone(load_candidate(tid))
        self.assert_cli_business_fail(
            "cmd_status",
            ["--talent-id", tid],
            "未找到",
        )

    def test_remove_nonexistent_fails(self):
        self.assert_cli_business_fail(
            "cmd_remove",
            ["--talent-id", "t_xxxxxx", "--confirm"],
            "不存在",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
