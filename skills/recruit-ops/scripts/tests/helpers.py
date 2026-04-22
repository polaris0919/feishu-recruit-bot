#!/usr/bin/env python3
"""
测试公共基础设施：
  - 安装态模块导入
  - _InMemoryTdb（内存 DB 注入）
  - _call_main / _wipe_state / _new_candidate 工具函数
"""
import contextlib
import copy
import io
import os
import re
import sys

# ─── 测试环境隔离 ─────────────────────────────────────────────────────────────
os.environ.pop("TALENT_DB_PASSWORD", None)
# v3.5.8：强制（而非 setdefault），避免外层 shell export 了 RECRUIT_DISABLE_SIDE_EFFECTS=0
# 之后跑测试就把 cv/exam_answer/email 三件套真写到 <RECRUIT_WORKSPACE>/data 下，
# 一晚上能堆 75 个孤儿目录（事故复盘见 docs/AGENT_RULES.md §8）。
# 任何想真写盘的测试要走 setUp/tearDown 自己 pop + 设 RECRUIT_DATA_ROOT 到 tmp。
os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = "1"

# 先导入真实 talent_db（供 test_infra.py 直接测模块行为）
from lib import talent_db as real_talent_db  # noqa: E402

_LEGACY_MODULE_ALIASES = {
    # 保留 short → 完整子包名的兜底映射（call_main / subprocess 反查仍在用）。
    # v3.5：所有剧本类 wrapper（round1/round2/followup/interview/common 下的
    # cmd_*_schedule / cmd_*_reschedule / cmd_reschedule_request / cmd_followup_*
    # / cmd_finalize_interview_time / cmd_wait_return_resume / interview.cmd_{defer,
    # reschedule,confirm}）已全部下线，由 agent 用 atomic CLI 现场拼链替代。
    "cmd_new_candidate": "intake.cmd_new_candidate",
    "cmd_import_candidate": "intake.cmd_import_candidate",
    "cmd_ingest_cv": "intake.cmd_ingest_cv",
    "cmd_attach_cv": "intake.cmd_attach_cv",
    "cmd_exam_result": "exam.cmd_exam_result",
    "cmd_status": "common.cmd_status",
    "cmd_search": "common.cmd_search",
    "cmd_remove": "common.cmd_remove",
}


# ─── 内存 DB ──────────────────────────────────────────────────────────────────

class _InMemoryTdb:
    """
    测试用内存 DB，注入 sys.modules["talent_db"]，替代真实 PostgreSQL。
    只需实现 core_state / cmd_* 脚本实际调用的接口即可。
    """
    # 暴露真实 talent_db 的字段白名单常量，让 cmd_update 等 CLI 在测试环境
    # 也能走相同的预校验逻辑（v3.4 Phase 0.1）。__getattr__ 兜底会把缺失属性
    # 转成 lambda，导致 `'phone' in mem_tdb.TALENT_UPDATABLE_FIELDS` 报
    # "argument of type 'function' is not iterable"，所以必须显式声明。
    TALENT_UPDATABLE_FIELDS = real_talent_db.TALENT_UPDATABLE_FIELDS

    def __init__(self):
        self._state = {"candidates": {}}
        self._boss_confirm_pending = {}
        # talent_emails 内存模拟：键为 (talent_id, message_id) → 行 dict
        self._emails = {}
        self._email_id_counter = 0

    def reset(self):
        self._state = {"candidates": {}}
        self._boss_confirm_pending = {}
        self._emails = {}
        self._email_id_counter = 0

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

    # ─── talent_emails 表的内存模拟 ──────────────────────────────────────────
    # 必须真实实现 insert_email_if_absent / mark_email_status，否则生产代码会把
    # __getattr__ 返回的 None 当成"该邮件已存在"而跳过所有测试邮件。

    def insert_email_if_absent(self, talent_id, message_id, direction, context,
                                sender, sent_at, **kw):
        if not message_id:
            return None
        key = (talent_id, message_id)
        if key in self._emails:
            return None  # 唯一约束命中
        self._email_id_counter += 1
        eid = "eml_{}".format(self._email_id_counter)
        row = {
            "email_id": eid, "talent_id": talent_id, "message_id": message_id,
            "direction": direction, "context": context,
            "sender": sender, "sent_at": sent_at,
            "status": kw.get("initial_status", "received"),
        }
        for k in ("subject", "in_reply_to", "references_chain", "recipients",
                  "received_at", "body_full", "body_excerpt", "stage_at_receipt",
                  "ai_summary", "ai_intent", "ai_payload", "reply_id",
                  "template"):  # v3.5：cmd_send 把模板名也写进 talent_emails
            row[k] = kw.get(k)
        self._emails[key] = row
        return eid

    def fetch_email(self, email_id):
        # v3.4 cmd_send --use-cached-draft 通过 email_id 直接取整行
        for row in self._emails.values():
            if row.get("email_id") == email_id:
                import copy as _copy
                return _copy.deepcopy(row)
        return None

    def update_email_attachments(self, email_id, attachments):
        # v3.5.6 inbox.cmd_scan 调；测试只需要把数据塞进 _emails 行
        if attachments is None:
            attachments = []
        if not isinstance(attachments, list):
            raise ValueError("attachments 必须是 list")
        for row in self._emails.values():
            if row.get("email_id") == email_id:
                row["attachments"] = attachments
                return True
        return False

    def set_email_analyzed(self, email_id, ai_summary=None, ai_intent=None,
                           ai_payload=None):
        # v3.4 inbox.cmd_analyze 在测试里需要把 LLM 输出（含 draft）写到 ai_payload
        from datetime import datetime as _dt
        for row in self._emails.values():
            if row.get("email_id") == email_id:
                row["analyzed_at"] = _dt.now()
                if ai_summary is not None:
                    row["ai_summary"] = ai_summary
                if ai_intent is not None:
                    row["ai_intent"] = ai_intent
                if ai_payload is not None:
                    row["ai_payload"] = ai_payload
                return True
        return False

    def mark_email_status(self, email_id, status, **kw):
        for row in self._emails.values():
            if row.get("email_id") == email_id:
                row["status"] = status
                for k in ("ai_summary", "ai_intent", "ai_payload", "reply_id",
                          "replied_by_email_id"):
                    if kw.get(k) is not None:
                        row[k] = kw[k]
                return True
        return False

    def get_processed_message_ids(self, talent_id, direction="inbound"):
        return {mid for (tid, mid), r in self._emails.items()
                if tid == talent_id
                and (direction is None or r.get("direction") == direction)}

    # ─── auto_reject 模块要用的接口 ───────────────────────────────────────

    def get_exam_timeout_candidates(self, threshold_days=3):
        from datetime import datetime, timedelta, timezone
        threshold_dt = datetime.now(timezone.utc) - timedelta(days=int(threshold_days))
        out = []
        for cand in self._state.get("candidates", {}).values():
            if cand.get("stage") != "EXAM_SENT":
                continue
            sent_at = cand.get("exam_sent_at")
            if not sent_at:
                continue
            # 测试里 sent_at 可能是字符串 ISO 或 datetime
            try:
                if hasattr(sent_at, "tzinfo"):
                    sent_dt = sent_at
                else:
                    from dateutil import parser as _dtp
                    sent_dt = _dtp.parse(str(sent_at))
                if sent_dt.tzinfo is None:
                    sent_dt = sent_dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if sent_dt > threshold_dt:
                continue
            out.append({
                "talent_id": cand.get("talent_id"),
                "candidate_name": cand.get("candidate_name"),
                "candidate_email": cand.get("candidate_email"),
                "exam_sent_at": sent_at,
            })
        return out

    def has_inbound_email_after(self, talent_id, after_dt):
        for (tid, _mid), row in self._emails.items():
            if tid != talent_id or row.get("direction") != "inbound":
                continue
            sent_at = row.get("sent_at")
            if after_dt is None or sent_at is None or str(sent_at) > str(after_dt):
                return True
        return False

    def has_outbound_rejection(self, talent_id):
        for (tid, _mid), row in self._emails.items():
            if (tid == talent_id
                    and row.get("direction") == "outbound"
                    and row.get("context") == "rejection"):
                return True
        return False

    def find_email_by_message_id(self, talent_id, message_id):
        return self._emails.get((talent_id, message_id))

    def find_outbound_email_by_message_id(self, talent_id, message_id):
        row = self._emails.get((talent_id, message_id))
        if not row or row.get("direction") != "outbound":
            return None
        return dict(row)

    def get_email_thread(self, talent_id, limit=50):
        rows = [r for (tid, _mid), r in self._emails.items() if tid == talent_id]
        rows.sort(key=lambda r: str(r.get("sent_at") or ""))
        return [dict(r) for r in rows[:limit]]

    # ─── v3.3 talent 字段 / 状态 helper ──────────────────────────────────

    def talent_exists(self, talent_id):
        return talent_id in (self._state.get("candidates") or {})

    def get_talent_current_stage(self, talent_id):
        cand = (self._state.get("candidates") or {}).get(talent_id)
        if not cand:
            return None
        return cand.get("current_stage") or cand.get("stage")

    def set_current_stage(self, talent_id, new_stage, actor="system", reason=None):
        cand = (self._state.get("candidates") or {}).get(talent_id)
        if not cand:
            return False
        cand["current_stage"] = new_stage
        cand["stage"] = new_stage  # 保持双写：测试里两个键都用
        return True

    def get_talent_field(self, talent_id, field):
        cand = (self._state.get("candidates") or {}).get(talent_id)
        if not cand:
            return None
        if field == "current_stage":
            return cand.get("current_stage") or cand.get("stage")
        return cand.get(field)

    def update_talent_field(self, talent_id, field, value):
        cand = (self._state.get("candidates") or {}).get(talent_id)
        if not cand:
            return False
        cand[field] = value
        return True

    def get_full_talent_snapshot(self, talent_id):
        cand = (self._state.get("candidates") or {}).get(talent_id)
        return copy.deepcopy(cand) if cand else None

    def save_audit_event(self, talent_id, action, payload=None, actor="system"):
        return True  # 测试里不需要持久化审计

    def get_email_by_reply_id(self, reply_id):
        if not reply_id:
            return None
        for row in self._emails.values():
            if row.get("reply_id") == reply_id:
                return {"email_id": row["email_id"],
                        "talent_id": row["talent_id"],
                        "message_id": row["message_id"],
                        "status": row["status"]}
        return None

    def __getattr__(self, name):
        return lambda *a, **kw: None


mem_tdb = _InMemoryTdb()

# 注入内存 DB：所有调用方现在都用 `from lib import talent_db as _tdb`，
# 走的是 sys.modules["lib.talent_db"]；同时把 `lib.talent_db` 属性也指过去，
# 这样 `from lib import talent_db` 在任何时机都能拿到 mem_tdb。
import lib  # noqa: E402
sys.modules["lib.talent_db"] = mem_tdb
sys.modules["talent_db"] = mem_tdb  # 兼容老式 `import talent_db`（如 cmd_interview_reminder）
lib.talent_db = mem_tdb


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
    """每个测试用例前清空内存 DB。

    v3.5.8 顺手：把 RECRUIT_DISABLE_SIDE_EFFECTS 重新拉回 "1"，防止前一个用例
    pop 后没正确 tearDown（比如 SystemExit / KeyboardInterrupt 中途打断）让后续
    cmd_new_candidate 一路真写盘。RECRUIT_DATA_ROOT 不动，由各 setUp 自己管。
    """
    mem_tdb.reset()
    os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = "1"


@contextlib.contextmanager
def patch_module(name, fake):
    """
    把 lib.<name> 临时换成 fake 对象，覆盖三种解析路径：
      - sys.modules["lib.<name>"]（被 `from lib.<name> import ...` 用）
      - sys.modules["<name>"]    （被旧式 `import <name>` 用，遗留兼容）
      - lib.<name>               （被 `from lib import <name>` 用，最常见）
    """
    import lib  # 延迟到调用时再 import，避免 helpers 加载顺序问题
    real_at_lib = getattr(lib, name, None)
    real_in_sys_lib = sys.modules.get("lib." + name)
    real_in_sys_bare = sys.modules.get(name)
    sys.modules["lib." + name] = fake
    sys.modules[name] = fake
    setattr(lib, name, fake)
    try:
        yield fake
    finally:
        if real_at_lib is None:
            try:
                delattr(lib, name)
            except AttributeError:
                pass
        else:
            setattr(lib, name, real_at_lib)
        if real_in_sys_lib is None:
            sys.modules.pop("lib." + name, None)
        else:
            sys.modules["lib." + name] = real_in_sys_lib
        if real_in_sys_bare is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = real_in_sys_bare


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
