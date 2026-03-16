"""
单元测试：cmd_round2_result.py
覆盖：
  - pass：状态推进到 OFFER_HANDOFF
  - reject_keep / reject_delete：状态推进到对应拒绝
  - 非法状态跳转报错
  - 审计日志写入
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "skills", "recruit-ops", "scripts"))
import cmd_round2_result


def make_state_file(candidates=None):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"candidates": candidates or {}}, f, ensure_ascii=False)
    f.close()
    return f.name


def r2_scheduled_candidate(talent_id, email="test@example.com"):
    return {
        "talent_id": talent_id,
        "stage": "ROUND2_SCHEDULED",
        "candidate_email": email,
        "round2_time": "2026-04-10 14:00",
        "audit": [],
    }


class TestRound2ResultPass(unittest.TestCase):

    def setUp(self):
        self.state_file = make_state_file({
            "r2c001": r2_scheduled_candidate("r2c001", "r2c001@example.com"),
        })
        os.environ["RECRUIT_STATE_PATH"] = self.state_file

    def tearDown(self):
        os.unlink(self.state_file)

    def test_pass_updates_to_offer_handoff(self):
        rc = cmd_round2_result.main(["--talent-id", "r2c001", "--result", "pass"])
        self.assertEqual(rc, 0)
        state = json.load(open(self.state_file))
        self.assertEqual(state["candidates"]["r2c001"]["stage"], "OFFER_HANDOFF")

    def test_pass_with_notes(self):
        rc = cmd_round2_result.main([
            "--talent-id", "r2c001",
            "--result", "pass",
            "--notes", "技术能力强，沟通顺畅",
        ])
        self.assertEqual(rc, 0)
        state = json.load(open(self.state_file))
        audit = state["candidates"]["r2c001"]["audit"]
        self.assertEqual(audit[-1]["payload"]["notes"], "技术能力强，沟通顺畅")

    def test_pass_audit_action_is_offer_handoff(self):
        cmd_round2_result.main(["--talent-id", "r2c001", "--result", "pass"])
        state = json.load(open(self.state_file))
        audit = state["candidates"]["r2c001"]["audit"]
        self.assertEqual(audit[-1]["action"], "round2_pass_offer_handoff")


class TestRound2ResultReject(unittest.TestCase):

    def setUp(self):
        self.state_file = make_state_file({
            "r2rk": r2_scheduled_candidate("r2rk"),
            "r2rd": r2_scheduled_candidate("r2rd"),
        })
        os.environ["RECRUIT_STATE_PATH"] = self.state_file

    def tearDown(self):
        os.unlink(self.state_file)

    def test_reject_keep_stage(self):
        rc = cmd_round2_result.main(["--talent-id", "r2rk", "--result", "reject_keep"])
        self.assertEqual(rc, 0)
        state = json.load(open(self.state_file))
        self.assertEqual(state["candidates"]["r2rk"]["stage"], "ROUND2_DONE_REJECT_KEEP")

    def test_reject_delete_stage(self):
        rc = cmd_round2_result.main(["--talent-id", "r2rd", "--result", "reject_delete"])
        self.assertEqual(rc, 0)
        state = json.load(open(self.state_file))
        self.assertEqual(state["candidates"]["r2rd"]["stage"], "ROUND2_DONE_REJECT_DELETE")

    def test_reject_audit_recorded(self):
        cmd_round2_result.main(["--talent-id", "r2rk", "--result", "reject_keep"])
        state = json.load(open(self.state_file))
        audit = state["candidates"]["r2rk"]["audit"]
        self.assertEqual(audit[-1]["action"], "round2_reject_keep")


class TestRound2IllegalTransition(unittest.TestCase):

    def setUp(self):
        self.state_file = make_state_file({
            "c_exam": {"talent_id": "c_exam", "stage": "EXAM_REVIEWED", "audit": []},
            "c_r1rk": {"talent_id": "c_r1rk", "stage": "ROUND1_DONE_REJECT_KEEP", "audit": []},
            "c_new": {"talent_id": "c_new", "stage": "NEW", "audit": []},
        })
        os.environ["RECRUIT_STATE_PATH"] = self.state_file

    def tearDown(self):
        os.unlink(self.state_file)

    def test_cannot_pass_from_exam_reviewed(self):
        rc = cmd_round2_result.main(["--talent-id", "c_exam", "--result", "pass"])
        self.assertEqual(rc, 1)
        state = json.load(open(self.state_file))
        self.assertEqual(state["candidates"]["c_exam"]["stage"], "EXAM_REVIEWED")

    def test_cannot_pass_from_reject_stage(self):
        rc = cmd_round2_result.main(["--talent-id", "c_r1rk", "--result", "pass"])
        self.assertEqual(rc, 1)

    def test_cannot_pass_from_new(self):
        rc = cmd_round2_result.main(["--talent-id", "c_new", "--result", "pass"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
