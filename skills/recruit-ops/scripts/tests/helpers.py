#!/usr/bin/env python3
"""
测试公共基础设施：
  - 安装态模块导入
  - _InMemoryTdb（内存 DB 注入）
  - _call_main / _wipe_state / _new_candidate 工具函数
"""
import copy
import io
import os
import re
import sys

# ─── 测试环境隔离 ─────────────────────────────────────────────────────────────
os.environ.pop("TALENT_DB_PASSWORD", None)
os.environ.setdefault("RECRUIT_DISABLE_SIDE_EFFECTS", "1")

# 先导入真实 talent_db（供 test_infra.py 直接测模块行为）
import talent_db as real_talent_db  # noqa: E402

_LEGACY_MODULE_ALIASES = {
    "cmd_new_candidate": "intake.cmd_new_candidate",
    "cmd_import_candidate": "intake.cmd_import_candidate",
    "cmd_ingest_cv": "intake.cmd_ingest_cv",
    "cmd_round1_schedule": "round1.cmd_round1_schedule",
    "cmd_round1_confirm": "round1.cmd_round1_confirm",
    "cmd_round1_defer": "round1.cmd_round1_defer",
    "cmd_round1_result": "round1.cmd_round1_result",
    "cmd_round2_confirm": "round2.cmd_round2_confirm",
    "cmd_round2_defer": "round2.cmd_round2_defer",
    "cmd_round2_reschedule": "round2.cmd_round2_reschedule",
    "cmd_round2_result": "round2.cmd_round2_result",
    "cmd_exam_result": "exam.cmd_exam_result",
    "cmd_status": "common.cmd_status",
    "cmd_search": "common.cmd_search",
    "cmd_remove": "common.cmd_remove",
    "cmd_reschedule_request": "common.cmd_reschedule_request",
    "cmd_wait_return_resume": "common.cmd_wait_return_resume",
}


# ─── 内存 DB ──────────────────────────────────────────────────────────────────

class _InMemoryTdb:
    """
    测试用内存 DB，注入 sys.modules["talent_db"]，替代真实 PostgreSQL。
    只需实现 core_state / cmd_* 脚本实际调用的接口即可。
    """
    def __init__(self):
        self._state = {"candidates": {}}
        self._boss_confirm_pending = {}

    def reset(self):
        self._state = {"candidates": {}}
        self._boss_confirm_pending = {}

    def _is_enabled(self):
        return True

    def load_state_from_db(self):
        return copy.deepcopy(self._state)

    def sync_state_to_db(self, state):
        new_cands = (state or {}).get("candidates") or {}
        self._state.setdefault("candidates", {}).update(copy.deepcopy(new_cands))
        return True

    def get_one(self, talent_id):
        cands = self._state.get("candidates") or {}
        cand = cands.get(talent_id)
        return copy.deepcopy(cand) if cand is not None else None

    def upsert_one(self, talent_id, cand):
        self._state.setdefault("candidates", {})[talent_id] = copy.deepcopy(cand)

    def delete_talent(self, talent_id):
        self._boss_confirm_pending.pop((talent_id, 1), None)
        self._boss_confirm_pending.pop((talent_id, 2), None)
        return bool(self._state.get("candidates", {}).pop(talent_id, None))

    def save_invite_info(self, talent_id, round_num, calendar_event_id=None):
        cand = self._state.get("candidates", {}).get(talent_id)
        if not cand:
            return
        prefix = "round{}".format(round_num)
        cand["wait_return_round"] = None
        cand["{}_invite_sent_at".format(prefix)] = "TEST_NOW"
        cand["{}_confirm_status".format(prefix)] = "PENDING"
        cand["{}_calendar_event_id".format(prefix)] = calendar_event_id

    def mark_confirmed(self, talent_id, round_num, auto=False):
        cand = self._state.get("candidates", {}).get(talent_id)
        if not cand:
            return
        prefix = "round{}".format(round_num)
        cand["{}_confirm_status".format(prefix)] = "CONFIRMED"
        if round_num == 1:
            cand["stage"] = "ROUND1_SCHEDULED"
        elif round_num == 2:
            cand["stage"] = "ROUND2_SCHEDULED"
        cand["wait_return_round"] = None
        self._boss_confirm_pending.pop((talent_id, round_num), None)

    def update_calendar_event_id(self, talent_id, round_num, event_id):
        cand = self._state.get("candidates", {}).get(talent_id)
        if not cand:
            return
        cand["round{}_calendar_event_id".format(round_num)] = event_id

    def clear_calendar_event_id(self, talent_id, round_num):
        cand = self._state.get("candidates", {}).get(talent_id)
        if not cand:
            return
        cand["round{}_calendar_event_id".format(round_num)] = None

    def mark_reschedule_pending(self, talent_id, round_num):
        cand = self._state.get("candidates", {}).get(talent_id)
        if not cand:
            return
        prefix = "round{}".format(round_num)
        cand["stage"] = "ROUND1_SCHEDULING" if round_num == 1 else "ROUND2_SCHEDULING"
        cand["wait_return_round"] = None
        cand["{}_confirm_status".format(prefix)] = "PENDING"
        cand["{}_calendar_event_id".format(prefix)] = None

    def mark_wait_return(self, talent_id, round_num):
        cand = self._state.get("candidates", {}).get(talent_id)
        if not cand:
            return
        prefix = "round{}".format(round_num)
        cand["stage"] = "WAIT_RETURN"
        cand["wait_return_round"] = round_num
        cand["{}_confirm_status".format(prefix)] = "UNSET"
        cand["{}_time".format(prefix)] = None
        cand["{}_invite_sent_at".format(prefix)] = None
        cand["{}_calendar_event_id".format(prefix)] = None
        cand["{}_confirm_prompted_at".format(prefix)] = None
        cand["{}_reminded_at".format(prefix)] = None

    def resume_wait_return(self, talent_id):
        cand = self._state.get("candidates", {}).get(talent_id)
        if not cand:
            return None
        round_num = cand.get("wait_return_round")
        if round_num not in (1, 2):
            return None
        cand["stage"] = "ROUND1_SCHEDULING" if round_num == 1 else "ROUND2_SCHEDULING"
        cand["wait_return_round"] = None
        return round_num

    def set_boss_confirm_pending(self, talent_id, round_num, proposed_time):
        self._boss_confirm_pending[(talent_id, round_num)] = {
            "pending": True,
            "time": proposed_time,
            "proposed_time": proposed_time,
            "prompt_at": "TEST_NOW",
        }
        cand = self._state.get("candidates", {}).get(talent_id)
        if cand:
            cand["round{}_time".format(round_num)] = proposed_time
            cand["round{}_confirm_status".format(round_num)] = "PENDING"

    def get_boss_confirm_pending(self, talent_id, round_num):
        return copy.deepcopy(self._boss_confirm_pending.get(
            (talent_id, round_num),
            {"pending": False, "time": None, "proposed_time": None, "prompt_at": None},
        ))

    def clear_boss_confirm_pending(self, talent_id, round_num):
        self._boss_confirm_pending.pop((talent_id, round_num), None)

    def update_last_email_id(self, talent_id, context, email_id):
        cand = self._state.get("candidates", {}).get(talent_id)
        if not cand:
            return
        cand["{}_last_email_id".format(context)] = email_id

    def get_pending_confirmations(self, round_num):
        prefix = "round{}".format(round_num)
        expected_stage = "ROUND1_SCHEDULING" if round_num == 1 else "ROUND2_SCHEDULING"
        results = []
        for cand in self._state.get("candidates", {}).values():
            if cand.get("stage") != expected_stage:
                continue
            if cand.get("{}_confirm_status".format(prefix)) != "PENDING":
                continue
            if not cand.get("candidate_email"):
                continue
            results.append(copy.deepcopy({
                "talent_id": cand.get("talent_id"),
                "candidate_email": cand.get("candidate_email"),
                "candidate_name": cand.get("candidate_name"),
                "{}_time".format(prefix): cand.get("{}_time".format(prefix)),
                "{}_invite_sent_at".format(prefix): cand.get("{}_invite_sent_at".format(prefix)),
                "{}_confirm_status".format(prefix): cand.get("{}_confirm_status".format(prefix)),
                "{}_calendar_event_id".format(prefix): cand.get("{}_calendar_event_id".format(prefix)),
                "{}_last_email_id".format(prefix): cand.get("{}_last_email_id".format(prefix)),
            }))
        return results

    def get_confirmed_candidates(self, round_num):
        prefix = "round{}".format(round_num)
        expected_stage = "ROUND1_SCHEDULED" if round_num == 1 else "ROUND2_SCHEDULED"
        results = []
        for cand in self._state.get("candidates", {}).values():
            if cand.get("stage") != expected_stage:
                continue
            if cand.get("{}_confirm_status".format(prefix)) != "CONFIRMED":
                continue
            if not cand.get("candidate_email"):
                continue
            results.append(copy.deepcopy({
                "talent_id": cand.get("talent_id"),
                "candidate_email": cand.get("candidate_email"),
                "candidate_name": cand.get("candidate_name"),
                "{}_time".format(prefix): cand.get("{}_time".format(prefix)),
                "{}_invite_sent_at".format(prefix): cand.get("{}_invite_sent_at".format(prefix)),
                "{}_confirm_status".format(prefix): cand.get("{}_confirm_status".format(prefix)),
                "{}_calendar_event_id".format(prefix): cand.get("{}_calendar_event_id".format(prefix)),
                "{}_last_email_id".format(prefix): cand.get("{}_last_email_id".format(prefix)),
            }))
        return results

    def __getattr__(self, name):
        return lambda *a, **kw: None


mem_tdb = _InMemoryTdb()
sys.modules["talent_db"] = mem_tdb


# ─── 工具函数 ─────────────────────────────────────────────────────────────────

def call_main(module_name, argv):
    """In-process 调用模块 main(argv)，返回 (stdout, stderr, returncode)。"""
    import importlib
    resolved_name = _LEGACY_MODULE_ALIASES.get(module_name, module_name)
    mod = importlib.import_module(resolved_name)

    buf_out, buf_err = io.StringIO(), io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = buf_out, buf_err

    rc = 0
    try:
        result = mod.main(argv)
        rc = int(result) if result is not None else 0
    except SystemExit as e:
        rc = int(e.code) if e.code is not None else 0
    except Exception as exc:
        buf_err.write("EXCEPTION: {}\n".format(exc))
        rc = 1
    finally:
        sys.stdout, sys.stderr = old_out, old_err

    return buf_out.getvalue().strip(), buf_err.getvalue().strip(), rc


def wipe_state():
    """每个测试用例前清空内存 DB。"""
    mem_tdb.reset()


def new_candidate(name="测试候选人", email="test@example.com", position="后端工程师"):
    """调 cmd_new_candidate 创建一个候选人，返回 talent_id。"""
    out, err, rc = call_main("cmd_new_candidate", [
        "--name", name, "--email", email, "--position", position,
    ])
    assert rc == 0, "cmd_new_candidate 失败 rc={} err={}".format(rc, err)
    m = re.search(r"\bt_([ a-z0-9]{6})\b", out.replace(" ", ""))
    if m:
        return "t_" + m.group(1).strip()
    m = re.search(r"talent_id\s*:\s*(t_[a-z0-9]{6})", out)
    if m:
        return m.group(1)
    raise AssertionError("无法从输出中提取 talent_id:\n{}".format(out))
