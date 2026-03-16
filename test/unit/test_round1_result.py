"""
单元测试：cmd_round1_result.py
覆盖每个分支的行为结果：
  - pass：状态 → EXAM_PENDING，后台发邮件（Popen mock）
  - pass 幂等：重复执行不重复发邮件
  - pass 缺 email：返回 1，状态不变
  - reject_keep / reject_delete：状态正确，不触发邮件
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
import cmd_round1_result


def make_state_file(content=None):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(content or {"candidates": {}}, f, ensure_ascii=False)
    f.close()
    return f.name


def mock_popen():
    """返回模拟 Popen 对象（有 .pid 属性）。"""
    m = MagicMock()
    m.return_value = MagicMock(pid=99999)
    return m


# ─── pass 分支 ────────────────────────────────────────────────────────────────

class TestRound1Pass(unittest.TestCase):

    def setUp(self):
        self.state_file = make_state_file()
        os.environ["RECRUIT_STATE_PATH"] = self.state_file

    def tearDown(self):
        os.unlink(self.state_file)

    def test_pass_stage_becomes_exam_pending(self):
        with patch.object(cmd_round1_result.subprocess, "Popen", mock_popen()):
            rc = cmd_round1_result.main(["--talent-id", "c001", "--result", "pass", "--email", "c001@x.com"])
        self.assertEqual(rc, 0)
        state = json.load(open(self.state_file))
        self.assertEqual(state["candidates"]["c001"]["stage"], "EXAM_PENDING")

    def test_pass_stores_candidate_email(self):
        with patch.object(cmd_round1_result.subprocess, "Popen", mock_popen()):
            cmd_round1_result.main(["--talent-id", "c002", "--result", "pass", "--email", "c002@x.com"])
        state = json.load(open(self.state_file))
        self.assertEqual(state["candidates"]["c002"]["candidate_email"], "c002@x.com")

    def test_pass_spawns_email_background_process(self):
        mock_p = mock_popen()
        with patch.object(cmd_round1_result.subprocess, "Popen", mock_p):
            cmd_round1_result.main(["--talent-id", "c003", "--result", "pass", "--email", "c003@x.com"])
        self.assertTrue(mock_p.called, "Popen should be called to spawn email process")

    def test_pass_idempotent_no_double_email(self):
        """重复 pass 同一候选人，不重复发邮件。"""
        mock_p = mock_popen()
        with patch.object(cmd_round1_result.subprocess, "Popen", mock_p):
            cmd_round1_result.main(["--talent-id", "idem", "--result", "pass", "--email", "idem@x.com"])
            count_after_first = mock_p.call_count
            cmd_round1_result.main(["--talent-id", "idem", "--result", "pass", "--email", "idem@x.com"])
            count_after_second = mock_p.call_count
        self.assertEqual(count_after_first, count_after_second, "Email must not be sent twice")
        state = json.load(open(self.state_file))
        self.assertEqual(state["candidates"]["idem"]["stage"], "EXAM_PENDING")

    def test_pass_without_email_returns_error(self):
        rc = cmd_round1_result.main(["--talent-id", "c004", "--result", "pass"])
        self.assertEqual(rc, 1)
        state = json.load(open(self.state_file))
        self.assertNotIn("c004", state["candidates"])

    def test_pass_audit_recorded(self):
        with patch.object(cmd_round1_result.subprocess, "Popen", mock_popen()):
            cmd_round1_result.main(["--talent-id", "c005", "--result", "pass", "--email", "c005@x.com"])
        state = json.load(open(self.state_file))
        actions = [e["action"] for e in state["candidates"]["c005"]["audit"]]
        self.assertIn("round1_pass_and_exam_invite_sent", actions)


# ─── reject 分支 ──────────────────────────────────────────────────────────────

class TestRound1Reject(unittest.TestCase):

    def setUp(self):
        self.state_file = make_state_file()
        os.environ["RECRUIT_STATE_PATH"] = self.state_file

    def tearDown(self):
        os.unlink(self.state_file)

    def test_reject_keep_stage(self):
        rc = cmd_round1_result.main(["--talent-id", "rk001", "--result", "reject_keep"])
        self.assertEqual(rc, 0)
        state = json.load(open(self.state_file))
        self.assertEqual(state["candidates"]["rk001"]["stage"], "ROUND1_DONE_REJECT_KEEP")

    def test_reject_delete_stage(self):
        rc = cmd_round1_result.main(["--talent-id", "rd001", "--result", "reject_delete"])
        self.assertEqual(rc, 0)
        state = json.load(open(self.state_file))
        self.assertEqual(state["candidates"]["rd001"]["stage"], "ROUND1_DONE_REJECT_DELETE")

    def test_reject_does_not_spawn_email(self):
        mock_p = mock_popen()
        with patch.object(cmd_round1_result.subprocess, "Popen", mock_p):
            cmd_round1_result.main(["--talent-id", "rk002", "--result", "reject_keep"])
        mock_p.assert_not_called()

    def test_reject_audit_recorded(self):
        cmd_round1_result.main(["--talent-id", "rk003", "--result", "reject_keep"])
        state = json.load(open(self.state_file))
        audit = state["candidates"]["rk003"]["audit"]
        self.assertTrue(len(audit) > 0)
        self.assertEqual(audit[-1]["action"], "round1_result_reject_keep")


# ─── 非法跳转 ──────────────────────────────────────────────────────────────────

class TestRound1IllegalTransition(unittest.TestCase):

    def setUp(self):
        self.state_file = make_state_file({
            "candidates": {
                "c_offer": {"talent_id": "c_offer", "stage": "OFFER_HANDOFF", "audit": []},
            }
        })
        os.environ["RECRUIT_STATE_PATH"] = self.state_file

    def tearDown(self):
        os.unlink(self.state_file)

    def test_cannot_pass_from_offer_handoff(self):
        mock_p = mock_popen()
        with patch.object(cmd_round1_result.subprocess, "Popen", mock_p):
            rc = cmd_round1_result.main(["--talent-id", "c_offer", "--result", "pass", "--email", "x@x.com"])
        self.assertEqual(rc, 1)
        mock_p.assert_not_called()
        state = json.load(open(self.state_file))
        self.assertEqual(state["candidates"]["c_offer"]["stage"], "OFFER_HANDOFF")


if __name__ == "__main__":
    unittest.main(verbosity=2)
