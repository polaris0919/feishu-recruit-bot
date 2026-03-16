"""
单元测试：cmd_status.py
覆盖：
  - 查询单个候选人（各阶段标签正确）
  - 查询所有候选人
  - 查询不存在的候选人返回 1
  - 阶段标签中文映射正确
"""
import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "skills", "recruit-ops", "scripts"))
import cmd_status


def make_state_file(candidates=None):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"candidates": candidates or {}}, f, ensure_ascii=False)
    f.close()
    return f.name


STAGE_LABEL_CASES = [
    ("NEW", "新建"),
    ("EXAM_PENDING", "笔试进行中"),
    ("EXAM_REVIEWED", "笔试已审阅"),
    ("ROUND2_SCHEDULED", "二面已安排"),
    ("OFFER_HANDOFF", "等待发放 Offer"),
    ("ROUND1_DONE_REJECT_KEEP", "一面未通过（保留）"),
    ("ROUND2_DONE_REJECT_DELETE", "二面未通过（移除）"),
]


class TestStatusSingleCandidate(unittest.TestCase):

    def setUp(self):
        self.state_file = make_state_file({
            "s001": {
                "talent_id": "s001",
                "stage": "EXAM_REVIEWED",
                "candidate_email": "s001@example.com",
                "exam_id": "exam-001",
                "audit": [
                    {"at": "2026-03-13T01:00:00Z", "actor": "hr", "action": "round1_pass_and_exam_invite_sent", "payload": {}},
                    {"at": "2026-03-13T02:00:00Z", "actor": "system", "action": "exam_daily_review_text", "payload": {}},
                ],
            }
        })
        os.environ["RECRUIT_STATE_PATH"] = self.state_file

    def tearDown(self):
        os.unlink(self.state_file)

    def test_returns_zero_for_existing_candidate(self):
        with patch("sys.stdout", new_callable=StringIO):
            rc = cmd_status.main(["--talent-id", "s001"])
        self.assertEqual(rc, 0)

    def test_output_contains_talent_id(self):
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            cmd_status.main(["--talent-id", "s001"])
        self.assertIn("s001", mock_out.getvalue())

    def test_output_contains_email(self):
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            cmd_status.main(["--talent-id", "s001"])
        self.assertIn("s001@example.com", mock_out.getvalue())

    def test_output_contains_stage_label(self):
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            cmd_status.main(["--talent-id", "s001"])
        self.assertIn("笔试已审阅", mock_out.getvalue())

    def test_output_contains_audit_summary(self):
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            cmd_status.main(["--talent-id", "s001"])
        output = mock_out.getvalue()
        self.assertIn("操作记录", output)

    def test_nonexistent_candidate_returns_one(self):
        with patch("sys.stdout", new_callable=StringIO):
            rc = cmd_status.main(["--talent-id", "nonexistent"])
        self.assertEqual(rc, 1)


class TestStatusAllCandidates(unittest.TestCase):

    def setUp(self):
        self.state_file = make_state_file({
            "a001": {"talent_id": "a001", "stage": "EXAM_PENDING", "candidate_email": "a@x.com", "audit": []},
            "a002": {"talent_id": "a002", "stage": "ROUND2_SCHEDULED", "candidate_email": "b@x.com", "audit": []},
            "a003": {"talent_id": "a003", "stage": "OFFER_HANDOFF", "candidate_email": "c@x.com", "audit": []},
        })
        os.environ["RECRUIT_STATE_PATH"] = self.state_file

    def tearDown(self):
        os.unlink(self.state_file)

    def test_all_returns_zero(self):
        with patch("sys.stdout", new_callable=StringIO):
            rc = cmd_status.main(["--all"])
        self.assertEqual(rc, 0)

    def test_all_lists_every_candidate(self):
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            cmd_status.main(["--all"])
        output = mock_out.getvalue()
        self.assertIn("a001", output)
        self.assertIn("a002", output)
        self.assertIn("a003", output)

    def test_empty_state_returns_message(self):
        empty_state = make_state_file({})
        os.environ["RECRUIT_STATE_PATH"] = empty_state
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            cmd_status.main(["--all"])
        self.assertIn("空", mock_out.getvalue())
        os.unlink(empty_state)


class TestStageLabelMapping(unittest.TestCase):

    def test_all_stage_labels_correct(self):
        for stage, expected_label in STAGE_LABEL_CASES:
            self.assertEqual(
                cmd_status.STAGE_LABELS.get(stage),
                expected_label,
                "stage={} expected label '{}'".format(stage, expected_label),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
