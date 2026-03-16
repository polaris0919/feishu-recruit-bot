"""
单元测试：cmd_exam_result.py
覆盖每个分支的行为结果：
  - pass：状态 → ROUND2_SCHEDULED，后台发邮件，后台启动日历
  - pass 日历失败：流程不阻断，状态正常保存
  - reject_keep / reject_delete：状态正确，不触发邮件/日历
  - 审计日志写入
  - 非法状态跳转报错
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "skills", "recruit-ops", "scripts"))
import cmd_exam_result


def make_state_file(candidates=None):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"candidates": candidates or {}}, f, ensure_ascii=False)
    f.close()
    return f.name


def exam_reviewed(talent_id, email="test@example.com"):
    return {
        "talent_id": talent_id,
        "stage": "EXAM_REVIEWED",
        "candidate_email": email,
        "exam_id": "exam-{}-001".format(talent_id),
        "audit": [],
    }


def mock_popen():
    m = MagicMock()
    m.return_value = MagicMock(pid=88888)
    return m


# ─── pass 分支 ────────────────────────────────────────────────────────────────

class TestExamResultPass(unittest.TestCase):

    def setUp(self):
        self.state_file = make_state_file({"c001": exam_reviewed("c001", "c001@x.com")})
        os.environ["RECRUIT_STATE_PATH"] = self.state_file

    def tearDown(self):
        os.unlink(self.state_file)

    def test_pass_stage_becomes_round2_scheduled(self):
        with patch.object(cmd_exam_result.subprocess, "Popen", mock_popen()), \
             patch.object(cmd_exam_result, "_spawn_calendar_bg", return_value=11111):
            rc = cmd_exam_result.main([
                "--talent-id", "c001", "--result", "pass",
                "--round2-time", "2026-04-01 14:00", "--interviewer", "老板",
            ])
        self.assertEqual(rc, 0)
        state = json.load(open(self.state_file))
        self.assertEqual(state["candidates"]["c001"]["stage"], "ROUND2_SCHEDULED")

    def test_pass_stores_round2_time(self):
        with patch.object(cmd_exam_result.subprocess, "Popen", mock_popen()), \
             patch.object(cmd_exam_result, "_spawn_calendar_bg", return_value=11111):
            cmd_exam_result.main([
                "--talent-id", "c001", "--result", "pass",
                "--round2-time", "2026-04-01 14:00",
            ])
        state = json.load(open(self.state_file))
        self.assertEqual(state["candidates"]["c001"]["round2_time"], "2026-04-01 14:00")

    def test_pass_spawns_email(self):
        mock_p = mock_popen()
        with patch.object(cmd_exam_result.subprocess, "Popen", mock_p), \
             patch.object(cmd_exam_result, "_spawn_calendar_bg", return_value=11111):
            cmd_exam_result.main([
                "--talent-id", "c001", "--result", "pass",
                "--round2-time", "2026-04-01 14:00",
            ])
        self.assertTrue(mock_p.called, "Popen should be called to send email")

    def test_pass_spawns_calendar_bg(self):
        mock_cal = MagicMock(return_value=22222)
        with patch.object(cmd_exam_result.subprocess, "Popen", mock_popen()), \
             patch.object(cmd_exam_result, "_spawn_calendar_bg", mock_cal):
            cmd_exam_result.main([
                "--talent-id", "c001", "--result", "pass",
                "--round2-time", "2026-04-01 14:00", "--interviewer", "老板",
            ])
        mock_cal.assert_called_once()

    def test_calendar_failure_does_not_block(self):
        """日历启动失败不应阻断状态保存和邮件发送。"""
        with patch.object(cmd_exam_result.subprocess, "Popen", mock_popen()), \
             patch.object(cmd_exam_result, "_spawn_calendar_bg", side_effect=Exception("模拟失败")):
            rc = cmd_exam_result.main([
                "--talent-id", "c001", "--result", "pass",
                "--round2-time", "2026-04-01 14:00",
            ])
        self.assertEqual(rc, 0)
        state = json.load(open(self.state_file))
        self.assertEqual(state["candidates"]["c001"]["stage"], "ROUND2_SCHEDULED")

    def test_pass_audit_recorded(self):
        with patch.object(cmd_exam_result.subprocess, "Popen", mock_popen()), \
             patch.object(cmd_exam_result, "_spawn_calendar_bg", return_value=11111):
            cmd_exam_result.main([
                "--talent-id", "c001", "--result", "pass",
                "--round2-time", "2026-04-01 14:00",
            ])
        state = json.load(open(self.state_file))
        actions = [e["action"] for e in state["candidates"]["c001"]["audit"]]
        self.assertIn("exam_result_pass_round2_scheduled", actions)


# ─── reject 分支 ──────────────────────────────────────────────────────────────

class TestExamResultReject(unittest.TestCase):

    def setUp(self):
        self.state_file = make_state_file({
            "c_rk": exam_reviewed("c_rk"),
            "c_rd": exam_reviewed("c_rd"),
        })
        os.environ["RECRUIT_STATE_PATH"] = self.state_file

    def tearDown(self):
        os.unlink(self.state_file)

    def test_reject_keep_stage(self):
        rc = cmd_exam_result.main(["--talent-id", "c_rk", "--result", "reject_keep"])
        self.assertEqual(rc, 0)
        state = json.load(open(self.state_file))
        self.assertEqual(state["candidates"]["c_rk"]["stage"], "ROUND1_DONE_REJECT_KEEP")

    def test_reject_delete_stage(self):
        rc = cmd_exam_result.main(["--talent-id", "c_rd", "--result", "reject_delete"])
        self.assertEqual(rc, 0)
        state = json.load(open(self.state_file))
        self.assertEqual(state["candidates"]["c_rd"]["stage"], "ROUND1_DONE_REJECT_DELETE")

    def test_reject_does_not_spawn_email_or_calendar(self):
        mock_p = mock_popen()
        mock_cal = MagicMock()
        with patch.object(cmd_exam_result.subprocess, "Popen", mock_p), \
             patch.object(cmd_exam_result, "_spawn_calendar_bg", mock_cal):
            cmd_exam_result.main(["--talent-id", "c_rk", "--result", "reject_keep"])
        mock_p.assert_not_called()
        mock_cal.assert_not_called()

    def test_reject_audit_recorded(self):
        cmd_exam_result.main(["--talent-id", "c_rk", "--result", "reject_keep"])
        state = json.load(open(self.state_file))
        audit = state["candidates"]["c_rk"]["audit"]
        self.assertTrue(len(audit) > 0)
        self.assertIn("reject", audit[-1]["action"])


# ─── 非法跳转 ──────────────────────────────────────────────────────────────────

class TestExamResultIllegalTransition(unittest.TestCase):

    def setUp(self):
        self.state_file = make_state_file({
            "c_new":   {"talent_id": "c_new",   "stage": "NEW",           "audit": []},
            "c_offer": {"talent_id": "c_offer", "stage": "OFFER_HANDOFF", "audit": []},
        })
        os.environ["RECRUIT_STATE_PATH"] = self.state_file

    def tearDown(self):
        os.unlink(self.state_file)

    def test_cannot_pass_from_new(self):
        mock_p = mock_popen()
        with patch.object(cmd_exam_result.subprocess, "Popen", mock_p):
            rc = cmd_exam_result.main([
                "--talent-id", "c_new", "--result", "pass", "--round2-time", "2026-04-01 14:00",
            ])
        self.assertEqual(rc, 1)
        mock_p.assert_not_called()

    def test_cannot_reject_from_offer_handoff(self):
        rc = cmd_exam_result.main(["--talent-id", "c_offer", "--result", "reject_keep"])
        self.assertEqual(rc, 1)
        state = json.load(open(self.state_file))
        self.assertEqual(state["candidates"]["c_offer"]["stage"], "OFFER_HANDOFF")


if __name__ == "__main__":
    unittest.main(verbosity=2)
