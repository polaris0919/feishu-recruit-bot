"""
单元测试：core_state.py — 状态机核心逻辑
覆盖：load/save state、get_candidate、append_audit、ensure_stage_transition
"""
import json
import os
import sys
import tempfile
import unittest

# 把 scripts/ 加入 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "skills", "recruit-ops", "scripts"))
import core_state


def make_temp_state(content=None):
    """创建临时状态文件，返回路径。"""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(content or {"candidates": {}}, f, ensure_ascii=False)
    f.close()
    return f.name


class TestLoadSaveState(unittest.TestCase):

    def test_load_empty_file(self):
        path = make_temp_state()
        os.environ["RECRUIT_STATE_PATH"] = path
        state = core_state.load_state()
        self.assertIn("candidates", state)
        self.assertEqual(state["candidates"], {})
        os.unlink(path)

    def test_load_nonexistent_returns_empty(self):
        os.environ["RECRUIT_STATE_PATH"] = "/tmp/nonexistent_recruit_state_xyz.json"
        state = core_state.load_state()
        self.assertEqual(state, {"candidates": {}})

    def test_load_corrupt_returns_empty(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        f.write("{ corrupt json @@@ }")
        f.close()
        os.environ["RECRUIT_STATE_PATH"] = f.name
        state = core_state.load_state()
        self.assertEqual(state, {"candidates": {}})
        os.unlink(f.name)

    def test_save_and_reload(self):
        path = make_temp_state()
        os.environ["RECRUIT_STATE_PATH"] = path
        state = {"candidates": {"t001": {"talent_id": "t001", "stage": "EXAM_PENDING", "audit": []}}}
        core_state.save_state(state)
        reloaded = core_state.load_state()
        self.assertEqual(reloaded["candidates"]["t001"]["stage"], "EXAM_PENDING")
        os.unlink(path)


class TestGetCandidate(unittest.TestCase):

    def test_new_candidate_defaults_to_NEW(self):
        state = {"candidates": {}}
        cand = core_state.get_candidate(state, "t001")
        self.assertEqual(cand["stage"], "NEW")
        self.assertEqual(cand["talent_id"], "t001")
        self.assertIn("t001", state["candidates"])

    def test_existing_candidate_returned_unchanged(self):
        state = {"candidates": {"t001": {"talent_id": "t001", "stage": "EXAM_PENDING", "audit": []}}}
        cand = core_state.get_candidate(state, "t001")
        self.assertEqual(cand["stage"], "EXAM_PENDING")


class TestAppendAudit(unittest.TestCase):

    def test_audit_entry_appended(self):
        cand = {"talent_id": "t001", "stage": "NEW", "audit": []}
        core_state.append_audit(cand, actor="hr01", action="round1_pass", payload={"email": "a@b.com"})
        self.assertEqual(len(cand["audit"]), 1)
        entry = cand["audit"][0]
        self.assertEqual(entry["actor"], "hr01")
        self.assertEqual(entry["action"], "round1_pass")
        self.assertEqual(entry["payload"]["email"], "a@b.com")
        self.assertIn("at", entry)

    def test_audit_none_payload_defaults_to_empty(self):
        cand = {"talent_id": "t001", "stage": "NEW", "audit": []}
        core_state.append_audit(cand, actor="system", action="test")
        self.assertEqual(cand["audit"][0]["payload"], {})


class TestEnsureStageTransition(unittest.TestCase):

    def test_valid_transition(self):
        cand = {"stage": "NEW"}
        ok = core_state.ensure_stage_transition(cand, {"NEW", "ROUND1_SCHEDULED"}, "EXAM_PENDING")
        self.assertTrue(ok)
        self.assertEqual(cand["stage"], "EXAM_PENDING")

    def test_invalid_source_stage(self):
        cand = {"stage": "OFFER_HANDOFF"}
        ok = core_state.ensure_stage_transition(cand, {"EXAM_PENDING", "EXAM_REVIEWED"}, "ROUND2_SCHEDULED")
        self.assertFalse(ok)
        self.assertEqual(cand["stage"], "OFFER_HANDOFF")  # unchanged

    def test_invalid_target_stage(self):
        cand = {"stage": "NEW"}
        ok = core_state.ensure_stage_transition(cand, {"NEW"}, "NONEXISTENT_STAGE")
        self.assertFalse(ok)

    def test_empty_allowed_from_means_unrestricted(self):
        """空集合 allowed_from 表示"不限制来源阶段"，等价于允许所有来源。"""
        cand = {"stage": "NEW"}
        ok = core_state.ensure_stage_transition(cand, set(), "EXAM_PENDING")
        self.assertTrue(ok)
        self.assertEqual(cand["stage"], "EXAM_PENDING")

    def test_all_valid_stages(self):
        """验证 STAGES 集合里的每个阶段都能作为目标。"""
        for stage in core_state.STAGES:
            cand = {"stage": "NEW"}
            ok = core_state.ensure_stage_transition(cand, {"NEW"}, stage)
            self.assertTrue(ok, "stage {} should be valid".format(stage))


if __name__ == "__main__":
    unittest.main(verbosity=2)
