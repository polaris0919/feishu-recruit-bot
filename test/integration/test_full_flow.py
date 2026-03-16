"""
集成测试：完整招聘流程（Path A–E）

  Path A（Happy Path）：R1通过 → 笔试 → 笔试通过 → R2 → OFFER_HANDOFF
  Path B：R1 不通过，保留人才库
  Path C：R1 不通过，删除
  Path D：笔试不通过（保留 / 删除）
  Path E：R2 不通过（保留 / 删除）
  幂等性：重复 round1 pass 不重复发邮件
  状态机防护：跨阶段非法命令返回 error
  多候选人并行：各自独立互不干扰
"""
import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from unittest.mock import patch, MagicMock

SCRIPTS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "skills", "recruit-ops", "scripts")
)
sys.path.insert(0, SCRIPTS)

import cmd_round1_result
import cmd_exam_result
import cmd_round2_result
import cmd_status


# ─── 工具 ─────────────────────────────────────────────────────────────────────

def fresh_state_file(candidates=None):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"candidates": candidates or {}}, f, ensure_ascii=False)
    f.close()
    return f.name


def get_stage(state_file, talent_id):
    return json.load(open(state_file)).get("candidates", {}).get(talent_id, {}).get("stage")


def get_audit_actions(state_file, talent_id):
    return [e["action"] for e in
            json.load(open(state_file)).get("candidates", {}).get(talent_id, {}).get("audit", [])]


def mock_popen():
    m = MagicMock()
    m.return_value = MagicMock(pid=99999)
    return m


def set_stage(state_file, talent_id, stage):
    state = json.load(open(state_file))
    state["candidates"][talent_id]["stage"] = stage
    json.dump(state, open(state_file, "w"), ensure_ascii=False, indent=2)


# ─── Path A：完整主干流程 ─────────────────────────────────────────────────────

class TestPathA_HappyPath(unittest.TestCase):

    def setUp(self):
        self.state_file = fresh_state_file()
        os.environ["RECRUIT_STATE_PATH"] = self.state_file
        self.tid = "hp_001"

    def tearDown(self):
        os.unlink(self.state_file)

    def test_full_flow_a_to_offer(self):
        # Step 1: R1 通过
        mock_r1 = mock_popen()
        with patch.object(cmd_round1_result.subprocess, "Popen", mock_r1):
            rc = cmd_round1_result.main([
                "--talent-id", self.tid, "--result", "pass", "--email", "hp@x.com",
            ])
        self.assertEqual(rc, 0)
        self.assertEqual(get_stage(self.state_file, self.tid), "EXAM_PENDING")
        self.assertTrue(mock_r1.called, "笔试邮件应已后台启动")

        # Step 2: 模拟笔试审阅完成（跳过 IMAP）
        set_stage(self.state_file, self.tid, "EXAM_REVIEWED")

        # Step 3: 笔试通过，安排二面
        mock_email = mock_popen()
        mock_cal = MagicMock(return_value=11111)
        with patch.object(cmd_exam_result.subprocess, "Popen", mock_email), \
             patch.object(cmd_exam_result, "_spawn_calendar_bg", mock_cal):
            rc = cmd_exam_result.main([
                "--talent-id", self.tid, "--result", "pass",
                "--round2-time", "2026-04-15 14:00", "--interviewer", "老板",
            ])
        self.assertEqual(rc, 0)
        self.assertEqual(get_stage(self.state_file, self.tid), "ROUND2_SCHEDULED")
        self.assertTrue(mock_email.called, "二面通知邮件应已后台启动")
        self.assertTrue(mock_cal.called, "飞书日历应已后台启动")

        # Step 4: R2 通过
        rc = cmd_round2_result.main(["--talent-id", self.tid, "--result", "pass", "--notes", "优秀"])
        self.assertEqual(rc, 0)
        self.assertEqual(get_stage(self.state_file, self.tid), "OFFER_HANDOFF")

        # Step 5: 状态查询
        with patch("sys.stdout", new_callable=StringIO) as out:
            rc = cmd_status.main(["--talent-id", self.tid])
        self.assertEqual(rc, 0)
        self.assertIn("OFFER_HANDOFF", out.getvalue())

        # 审计链验证
        actions = get_audit_actions(self.state_file, self.tid)
        self.assertIn("round1_pass_and_exam_invite_sent", actions)
        self.assertIn("exam_result_pass_round2_scheduled", actions)
        self.assertIn("round2_pass_offer_handoff", actions)


# ─── Path B / C：R1 拒绝 ──────────────────────────────────────────────────────

class TestPathBC_R1Reject(unittest.TestCase):

    def setUp(self):
        self.state_file = fresh_state_file()
        os.environ["RECRUIT_STATE_PATH"] = self.state_file

    def tearDown(self):
        os.unlink(self.state_file)

    def test_path_b_r1_reject_keep(self):
        rc = cmd_round1_result.main(["--talent-id", "pb", "--result", "reject_keep"])
        self.assertEqual(rc, 0)
        self.assertEqual(get_stage(self.state_file, "pb"), "ROUND1_DONE_REJECT_KEEP")
        # 拒绝后不能进入笔试流程
        rc2 = cmd_exam_result.main(["--talent-id", "pb", "--result", "pass", "--round2-time", "x"])
        self.assertEqual(rc2, 1)

    def test_path_c_r1_reject_delete(self):
        rc = cmd_round1_result.main(["--talent-id", "pc", "--result", "reject_delete"])
        self.assertEqual(rc, 0)
        self.assertEqual(get_stage(self.state_file, "pc"), "ROUND1_DONE_REJECT_DELETE")


# ─── Path D：笔试拒绝 ─────────────────────────────────────────────────────────

class TestPathD_ExamReject(unittest.TestCase):

    def setUp(self):
        self.state_file = fresh_state_file({
            "pd_rk": {"talent_id": "pd_rk", "stage": "EXAM_REVIEWED",
                      "candidate_email": "pd_rk@x.com", "audit": []},
            "pd_rd": {"talent_id": "pd_rd", "stage": "EXAM_REVIEWED",
                      "candidate_email": "pd_rd@x.com", "audit": []},
        })
        os.environ["RECRUIT_STATE_PATH"] = self.state_file

    def tearDown(self):
        os.unlink(self.state_file)

    def test_path_d1_exam_reject_keep(self):
        rc = cmd_exam_result.main(["--talent-id", "pd_rk", "--result", "reject_keep"])
        self.assertEqual(rc, 0)
        self.assertEqual(get_stage(self.state_file, "pd_rk"), "ROUND1_DONE_REJECT_KEEP")
        # 拒绝后不能进入二面
        rc2 = cmd_round2_result.main(["--talent-id", "pd_rk", "--result", "pass"])
        self.assertEqual(rc2, 1)

    def test_path_d2_exam_reject_delete(self):
        rc = cmd_exam_result.main(["--talent-id", "pd_rd", "--result", "reject_delete"])
        self.assertEqual(rc, 0)
        self.assertEqual(get_stage(self.state_file, "pd_rd"), "ROUND1_DONE_REJECT_DELETE")


# ─── Path E：R2 拒绝 ──────────────────────────────────────────────────────────

class TestPathE_R2Reject(unittest.TestCase):

    def setUp(self):
        self.state_file = fresh_state_file({
            "pe_rk": {"talent_id": "pe_rk", "stage": "ROUND2_SCHEDULED",
                      "candidate_email": "pe_rk@x.com", "audit": []},
            "pe_rd": {"talent_id": "pe_rd", "stage": "ROUND2_SCHEDULED",
                      "candidate_email": "pe_rd@x.com", "audit": []},
        })
        os.environ["RECRUIT_STATE_PATH"] = self.state_file

    def tearDown(self):
        os.unlink(self.state_file)

    def test_path_e1_r2_reject_keep(self):
        rc = cmd_round2_result.main(["--talent-id", "pe_rk", "--result", "reject_keep"])
        self.assertEqual(rc, 0)
        self.assertEqual(get_stage(self.state_file, "pe_rk"), "ROUND2_DONE_REJECT_KEEP")

    def test_path_e2_r2_reject_delete(self):
        rc = cmd_round2_result.main(["--talent-id", "pe_rd", "--result", "reject_delete"])
        self.assertEqual(rc, 0)
        self.assertEqual(get_stage(self.state_file, "pe_rd"), "ROUND2_DONE_REJECT_DELETE")


# ─── 幂等性 ───────────────────────────────────────────────────────────────────

class TestIdempotency(unittest.TestCase):

    def setUp(self):
        self.state_file = fresh_state_file()
        os.environ["RECRUIT_STATE_PATH"] = self.state_file

    def tearDown(self):
        os.unlink(self.state_file)

    def test_double_round1_pass_no_double_email(self):
        mock_p = mock_popen()
        with patch.object(cmd_round1_result.subprocess, "Popen", mock_p):
            cmd_round1_result.main(["--talent-id", "idem", "--result", "pass", "--email", "idem@x.com"])
            count_first = mock_p.call_count
            cmd_round1_result.main(["--talent-id", "idem", "--result", "pass", "--email", "idem@x.com"])
            count_second = mock_p.call_count
        self.assertEqual(count_first, count_second, "Email must not be sent twice")
        self.assertEqual(get_stage(self.state_file, "idem"), "EXAM_PENDING")


# ─── 非法跳转防护 ──────────────────────────────────────────────────────────────

class TestIllegalTransitions(unittest.TestCase):

    def setUp(self):
        self.state_file = fresh_state_file({
            "c_offer": {"talent_id": "c_offer", "stage": "OFFER_HANDOFF", "audit": []},
            "c_new":   {"talent_id": "c_new",   "stage": "NEW",           "audit": []},
            "c_r1rk":  {"talent_id": "c_r1rk",  "stage": "ROUND1_DONE_REJECT_KEEP", "audit": []},
        })
        os.environ["RECRUIT_STATE_PATH"] = self.state_file

    def tearDown(self):
        os.unlink(self.state_file)

    def test_exam_result_from_offer_fails(self):
        rc = cmd_exam_result.main(["--talent-id", "c_offer", "--result", "pass", "--round2-time", "x"])
        self.assertEqual(rc, 1)
        self.assertEqual(get_stage(self.state_file, "c_offer"), "OFFER_HANDOFF")

    def test_round2_from_new_fails(self):
        rc = cmd_round2_result.main(["--talent-id", "c_new", "--result", "pass"])
        self.assertEqual(rc, 1)
        self.assertEqual(get_stage(self.state_file, "c_new"), "NEW")

    def test_round2_from_rejected_fails(self):
        rc = cmd_round2_result.main(["--talent-id", "c_r1rk", "--result", "pass"])
        self.assertEqual(rc, 1)

    def test_round1_pass_from_offer_fails(self):
        mock_p = mock_popen()
        with patch.object(cmd_round1_result.subprocess, "Popen", mock_p):
            rc = cmd_round1_result.main(["--talent-id", "c_offer", "--result", "pass", "--email", "x@x.com"])
        self.assertEqual(rc, 1)
        mock_p.assert_not_called()


# ─── 多候选人并行 ──────────────────────────────────────────────────────────────

class TestMultipleCandidates(unittest.TestCase):

    def setUp(self):
        self.state_file = fresh_state_file()
        os.environ["RECRUIT_STATE_PATH"] = self.state_file

    def tearDown(self):
        os.unlink(self.state_file)

    def test_three_candidates_independent(self):
        mock_p = mock_popen()
        with patch.object(cmd_round1_result.subprocess, "Popen", mock_p):
            cmd_round1_result.main(["--talent-id", "m001", "--result", "pass",        "--email", "m001@x.com"])
            cmd_round1_result.main(["--talent-id", "m002", "--result", "reject_keep"])
            cmd_round1_result.main(["--talent-id", "m003", "--result", "reject_delete"])

        self.assertEqual(get_stage(self.state_file, "m001"), "EXAM_PENDING")
        self.assertEqual(get_stage(self.state_file, "m002"), "ROUND1_DONE_REJECT_KEEP")
        self.assertEqual(get_stage(self.state_file, "m003"), "ROUND1_DONE_REJECT_DELETE")

        with patch("sys.stdout", new_callable=StringIO) as out:
            cmd_status.main(["--all"])
        output = out.getvalue()
        for tid in ("m001", "m002", "m003"):
            self.assertIn(tid, output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
