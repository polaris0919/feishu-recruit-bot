#!/usr/bin/env python3
"""
人才库 PostgreSQL 读写模块。
- 使用 RealDictCursor 消除 row[N] 硬编码
- 配置统一由 config.py 提供
- schema 初始化：psql "$DATABASE_URL" -f lib/migrations/schema.sql
"""
import sys
import json
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

from lib import config as _cfg

import psycopg2
from psycopg2.extras import RealDictCursor, Json

# ─── 工具函数 ─────────────────────────────────────────────────────────────────

def _s(v, default=None):
    # type: (Any, Any) -> Any
    """去除字符串空白；None 或空字符串返回 default。"""
    if v is None:
        return default
    s = str(v).strip()
    return s if s else default


# ─── 连接管理 ─────────────────────────────────────────────────────────────────

def _is_enabled():
    return _cfg.db_enabled()


def _conn_params():
    # type: () -> dict
    """Backward-compatible DB params helper for legacy callers."""
    return _cfg.db_conn_params()


@contextmanager
def _connect():
    """上下文管理器：获取连接，自动提交或回滚，最后关闭。"""
    conn = psycopg2.connect(**_conn_params())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── 状态加载 ─────────────────────────────────────────────────────────────────

_TALENT_FIELDS = [
    "talent_id", "candidate_email", "candidate_name",
    "wait_return_round",
    "exam_id",
    # 一面
    "round1_confirm_status", "round1_time",
    "round1_invite_sent_at", "round1_calendar_event_id",
    "round1_reminded_at", "round1_confirm_prompted_at",
    # 二面
    "round2_confirm_status", "round2_time",
    "round2_invite_sent_at", "round2_calendar_event_id",
    "round2_reminded_at", "round2_confirm_prompted_at",
    # 笔试
    "exam_sent_at",
    # 个人信息
    "source", "position", "education", "work_years", "experience", "school",
    "phone", "wechat", "cv_path",
    # v3.5.7：CV 解析的「是否会 C++」（True/False/None 三态）
    "has_cpp",
    # 时间追踪
    "created_at", "updated_at",
]

# 这些字段由定向 UPDATE / 后台流程维护，而不是通过 save_candidate() 全量覆盖。
# 保持它们不进入 upsert，可避免旧 cand 快照把其他流程刚写入的提醒时间戳覆盖掉。
_DB_MANAGED_ROUND_FIELDS = (
    "reminded_at",
    "confirm_prompted_at",
)

# TIMESTAMPTZ 字段：以 ISO 格式字符串返回
_TIMESTAMPTZ_ISO_KEYS = frozenset({
    "updated_at", "created_at", "exam_sent_at",
    "round1_invite_sent_at", "round2_invite_sent_at",
})

# TIMESTAMPTZ 字段：以本地时间 "YYYY-MM-DD HH:MM" 格式返回（面试预约时间，用于展示）
_TIMESTAMPTZ_LOCAL_KEYS = frozenset({
    "round1_time", "round2_time",
})


def _dt_to_local_str(val):
    # type: (Any) -> Optional[str]
    """将 TIMESTAMPTZ datetime 对象转为本地时间字符串 'YYYY-MM-DD HH:MM'。"""
    if val is None:
        return None
    if hasattr(val, "timestamp"):
        return datetime.fromtimestamp(val.timestamp()).strftime("%Y-%m-%d %H:%M")
    s = str(val).strip()
    return s[:16] if s else None


def _round_time_key(round_num):
    # type: (int) -> str
    return "round{}_time".format(round_num)


def _candidate_round_time(cand, round_num):
    # type: (Dict[str, Any], int) -> Optional[str]
    key = _round_time_key(round_num)
    return (cand.get(key)
            or cand.get("round{}_confirmed_time".format(round_num))
            or cand.get("round{}_proposed_time".format(round_num)))


def _row_to_event(row):
    # type: (dict) -> dict
    """将 talent_events 行转为 audit dict。"""
    at_val = row["at"]
    at_str = at_val.isoformat().replace("+00:00", "Z") if hasattr(at_val, "isoformat") else str(at_val)
    payload = row["payload"] if isinstance(row["payload"], dict) else {}
    return {
        "event_id": _s(row.get("event_id")),
        "at": at_str,
        "actor": _s(row.get("actor"), "system"),
        "action": _s(row.get("action"), ""),
        "payload": payload,
    }


def _legacy_event_id(tid, entry):
    # type: (str, Dict[str, Any]) -> str
    payload = entry.get("payload") or {}
    payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    seed = "|".join([
        _s(tid, ""),
        _s(entry.get("at"), ""),
        _s(entry.get("actor"), "system"),
        _s(entry.get("action"), ""),
        payload_json,
    ])
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "recruit-ops:talent-event:" + seed))


def _event_values(tid, entry):
    # type: (str, Dict[str, Any]) -> tuple[str, str, str, str, Dict[str, Any]]
    at_str = _s(entry.get("at")) or datetime.now().isoformat()
    actor = _s(entry.get("actor"), "system")
    action = _s(entry.get("action"), "")
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    event_id = _s(entry.get("event_id")) or _legacy_event_id(tid, {
        "at": at_str,
        "actor": actor,
        "action": action,
        "payload": payload,
    })
    entry["event_id"] = event_id
    return event_id, at_str, actor, action, payload


def _row_to_candidate(row):
    # type: (dict) -> dict
    """将 DB 行（RealDictRow）转为候选人 dict。"""
    cand = {"audit": [], "stage": _s(row.get("current_stage"), "NEW")}
    for key in _TALENT_FIELDS:
        val = row.get(key)
        if val is None:
            cand[key] = None
            continue
        if key in ("round1_confirm_status", "round2_confirm_status"):
            cand[key] = str(val).strip() or "UNSET"
        elif key in _TIMESTAMPTZ_ISO_KEYS:
            cand[key] = val.isoformat() if hasattr(val, "isoformat") else str(val)
        elif key in _TIMESTAMPTZ_LOCAL_KEYS:
            cand[key] = _dt_to_local_str(val)
        elif isinstance(val, str):
            cand[key] = val.strip() or None
        else:
            cand[key] = val
    for round_num in (1, 2):
        key = _round_time_key(round_num)
        if cand.get(key) is None:
            cand[key] = _dt_to_local_str(
                row.get(key)
                or row.get("round{}_confirmed_time".format(round_num))
                or row.get("round{}_proposed_time".format(round_num))
            )
    return cand


def load_state_from_db():
    # type: () -> Dict[str, Any]
    if not _is_enabled():
        return {"candidates": {}}
    candidates = {}
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM talents")
                for row in cur.fetchall():
                    tid = _s(row.get("talent_id"), "")
                    if not tid:
                        continue
                    candidates[tid] = _row_to_candidate(row)

                cur.execute(
                    "SELECT talent_id, event_id, at, actor, action, payload "
                    "FROM talent_events ORDER BY at ASC"
                )
                for row in cur.fetchall():
                    tid = _s(row["talent_id"], "")
                    if tid not in candidates:
                        continue
                    candidates[tid]["audit"].append(_row_to_event(row))
    except Exception as e:
        print("[talent_db] load_state_from_db 失败: {}".format(e), file=sys.stderr)
        return {"candidates": {}}
    return {"candidates": candidates}


# ─── 状态同步 ─────────────────────────────────────────────────────────────────

def _upsert_talent(cur, tid, cand):
    # type: (Any, str, dict) -> None
    stage = cand.get("stage") or "NEW"
    cur.execute("""
        INSERT INTO talents (talent_id, candidate_email, candidate_name, current_stage,
            wait_return_round,
            exam_id,
            round1_confirm_status, round1_time,
            round1_invite_sent_at, round1_calendar_event_id,
            round2_confirm_status, round2_time,
            round2_invite_sent_at, round2_calendar_event_id,
            source, position, education, work_years, experience, school, phone, wechat,
            exam_sent_at, cv_path, has_cpp,
            updated_at)
        VALUES (%(tid)s, %(email)s, %(name)s, %(stage)s,
            %(wait_return_round)s,
            %(exam_id)s,
            %(r1_status)s, %(r1_time)s,
            %(r1_invite_sent_at)s, %(r1_cal_eid)s,
            %(r2_status)s, %(r2_time)s,
            %(r2_invite_sent_at)s, %(r2_cal_eid)s,
            %(source)s, %(position)s, %(education)s, %(work_years)s, %(experience)s,
            %(school)s, %(phone)s, %(wechat)s,
            %(exam_sent_at)s, %(cv_path)s, %(has_cpp)s,
            NOW())
        ON CONFLICT (talent_id) DO UPDATE SET
            candidate_email = EXCLUDED.candidate_email,
            candidate_name = EXCLUDED.candidate_name,
            current_stage = EXCLUDED.current_stage,
            wait_return_round = EXCLUDED.wait_return_round,
            exam_id = EXCLUDED.exam_id,
            round1_confirm_status = EXCLUDED.round1_confirm_status,
            round1_time = EXCLUDED.round1_time,
            round1_invite_sent_at = EXCLUDED.round1_invite_sent_at,
            round1_calendar_event_id = EXCLUDED.round1_calendar_event_id,
            round2_confirm_status = EXCLUDED.round2_confirm_status,
            round2_time = EXCLUDED.round2_time,
            round2_invite_sent_at = EXCLUDED.round2_invite_sent_at,
            round2_calendar_event_id = EXCLUDED.round2_calendar_event_id,
            source = EXCLUDED.source,
            position = EXCLUDED.position,
            education = EXCLUDED.education,
            work_years = EXCLUDED.work_years,
            experience = EXCLUDED.experience,
            school = EXCLUDED.school,
            phone = EXCLUDED.phone,
            wechat = EXCLUDED.wechat,
            exam_sent_at = EXCLUDED.exam_sent_at,
            cv_path = EXCLUDED.cv_path,
            has_cpp = EXCLUDED.has_cpp,
            updated_at = NOW()
    """, {
        "tid": tid,
        "email": _s(cand.get("candidate_email")),
        "name": _s(cand.get("candidate_name")),
        "stage": stage,
        "wait_return_round": cand.get("wait_return_round"),
        "exam_id": cand.get("exam_id"),
        "r1_status": cand.get("round1_confirm_status") or "UNSET",
        "r1_time": _candidate_round_time(cand, 1),
        "r1_invite_sent_at": cand.get("round1_invite_sent_at"),
        "r1_cal_eid": cand.get("round1_calendar_event_id"),
        "r2_status": cand.get("round2_confirm_status") or "UNSET",
        "r2_time": _candidate_round_time(cand, 2),
        "r2_invite_sent_at": cand.get("round2_invite_sent_at"),
        "r2_cal_eid": cand.get("round2_calendar_event_id"),
        "source": cand.get("source"),
        "position": cand.get("position"),
        "education": cand.get("education"),
        "work_years": cand.get("work_years"),
        "experience": cand.get("experience"),
        "school": cand.get("school"),
        "phone": cand.get("phone"),
        "wechat": cand.get("wechat"),
        "exam_sent_at": cand.get("exam_sent_at"),
        "cv_path": cand.get("cv_path"),
        "has_cpp": cand.get("has_cpp"),  # 三态 True/False/None
    })


def _round_confirmation_candidates(round_num, confirmed):
    # type: (int, bool) -> List[Dict[str, Any]]
    prefix = "round{}".format(round_num)
    stage = (
        "ROUND1_SCHEDULED" if round_num == 1 else "ROUND2_SCHEDULED"
    ) if confirmed else (
        "ROUND1_SCHEDULING" if round_num == 1 else "ROUND2_SCHEDULING"
    )
    status = "CONFIRMED" if confirmed else "PENDING"
    rows = _query_all("""
        SELECT talent_id, candidate_email, candidate_name,
               {p}_time, {p}_invite_sent_at, {p}_confirm_status, {p}_calendar_event_id
        FROM talents
        WHERE current_stage = %s
          AND {p}_confirm_status = %s
          AND candidate_email IS NOT NULL
    """.format(p=prefix), (stage, status))
    results = []
    for r in rows:
        results.append({
            "talent_id": _s(r["talent_id"], ""),
            "candidate_email": _s(r["candidate_email"], ""),
            "candidate_name": _s(r.get("candidate_name")),
            "{}_time".format(prefix): _dt_to_local_str(r.get("{}_time".format(prefix))),
            "{}_invite_sent_at".format(prefix): r.get("{}_invite_sent_at".format(prefix)),
            "{}_confirm_status".format(prefix): _s(r.get("{}_confirm_status".format(prefix)), "UNSET"),
            "{}_calendar_event_id".format(prefix): _s(r.get("{}_calendar_event_id".format(prefix))),
        })
    return results


def _insert_events(cur, tid, audit):
    # type: (Any, str, list) -> None
    for entry in (audit or []):
        event_id, at_str, actor, action, payload = _event_values(tid, entry)
        try:
            cur.execute(
                "INSERT INTO talent_events (event_id, talent_id, at, actor, action, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (event_id) DO NOTHING",
                (event_id, tid, at_str, actor, action, Json(payload)),
            )
        except Exception as e:
            print("[talent_db] 插入事件失败: {}".format(e), file=sys.stderr)


def sync_state_to_db(state):
    # type: (Dict[str, Any]) -> bool
    if not _is_enabled():
        return False
    try:
        from lib.side_effect_guard import db_writes_disabled
        if db_writes_disabled():
            print("[talent_db][dry-run] 跳过 sync_state_to_db", file=sys.stderr)
            return True
    except ImportError:
        pass
    candidates = state.get("candidates") or {}
    if not candidates:
        return False
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                for tid, cand in candidates.items():
                    tid = _s(tid, "")
                    if not tid:
                        continue
                    _upsert_talent(cur, tid, cand)
                    _insert_events(cur, tid, cand.get("audit") or [])
        return True
    except Exception as e:
        print("[talent_db] sync_state_to_db 失败: {}".format(e), file=sys.stderr)
        return False


# ─── 单记录读写（供 core_state.load_candidate / save_candidate 使用）──────────

def get_one(talent_id):
    # type: (str) -> Optional[Dict[str, Any]]
    """从 DB 加载单个候选人（含 audit 事件），未找到返回 None。"""
    if not _is_enabled():
        return None
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM talents WHERE talent_id = %s", (talent_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                cand = _row_to_candidate(row)
                cur.execute(
                    "SELECT event_id, at, actor, action, payload FROM talent_events "
                    "WHERE talent_id = %s ORDER BY at ASC",
                    (talent_id,),
                )
                for ev in cur.fetchall():
                    cand["audit"].append(_row_to_event(ev))
                return cand
    except Exception as e:
        print("[talent_db] get_one 失败: {}".format(e), file=sys.stderr)
        return None


def upsert_one(talent_id, cand):
    # type: (str, Dict[str, Any]) -> None
    """将单个候选人 upsert 到 DB（含 audit 事件）。"""
    if not _is_enabled():
        return
    try:
        from lib.side_effect_guard import db_writes_disabled
        if db_writes_disabled():
            print("[talent_db][dry-run] 跳过 upsert_one talent_id={}".format(talent_id),
                  file=sys.stderr)
            return
    except ImportError:
        pass
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                _upsert_talent(cur, talent_id, cand)
                _insert_events(cur, talent_id, cand.get("audit") or [])
    except Exception as e:
        print("[talent_db] upsert_one 失败: {}".format(e), file=sys.stderr)


# ─── 单条操作 ─────────────────────────────────────────────────────────────────

def _update(sql, params):
    # type: (str, tuple) -> bool
    if not _is_enabled():
        return False
    try:
        from lib.side_effect_guard import db_writes_disabled
        if db_writes_disabled():
            preview = " ".join(str(sql).split())[:160]
            print("[talent_db][dry-run] 跳过 _update: {}".format(preview), file=sys.stderr)
            return True
    except ImportError:
        pass
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
        return True
    except Exception as e:
        print("[talent_db] UPDATE 失败: {}".format(e), file=sys.stderr)
        return False


def _query_one(sql, params=()):
    # type: (str, tuple) -> Optional[dict]
    if not _is_enabled():
        return None
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                return cur.fetchone()
    except Exception as e:
        print("[talent_db] QUERY 失败: {}".format(e), file=sys.stderr)
        return None


def _query_all(sql, params=()):
    # type: (str, tuple) -> List[dict]
    if not _is_enabled():
        return []
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                return cur.fetchall()
    except Exception as e:
        print("[talent_db] QUERY 失败: {}".format(e), file=sys.stderr)
        return []


# ─── 候选人 CRUD ──────────────────────────────────────────────────────────────

def delete_talent(talent_id):
    # type: (str) -> bool
    """从数据库删除候选人及其关联数据（CASCADE），返回是否实际删除了记录。"""
    if not _is_enabled():
        return False
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM talents WHERE talent_id = %s", (talent_id,))
                deleted = cur.rowcount > 0
        if deleted:
            print("[talent_db] 已从数据库删除候选人 {}".format(talent_id))
        return deleted
    except Exception as e:
        print("[talent_db] delete_talent 失败: {}".format(e), file=sys.stderr)
        return False


# ─── 邮件游标（v3.5.2 已下线）──────────────────────────────────────────────────
#   talents.<context>_last_email_id 字段在 v3.5.2 全部 DROP，由 talent_emails 表
#   的 (talent_id, message_id) UNIQUE 接管去重。原 update_last_email_id 函数已删除。


# ─── 面试提醒 ─────────────────────────────────────────────────────────────────

def mark_interview_reminded(talent_id):
    _update("UPDATE talents SET round2_reminded_at = NOW() WHERE talent_id = %s", (talent_id,))


def mark_round1_reminded(talent_id):
    _update("UPDATE talents SET round1_reminded_at = NOW() WHERE talent_id = %s", (talent_id,))


def _parse_pending_reminders(rows, time_key):
    # type: (list, str) -> List[Dict[str, Any]]
    results = []
    for r in rows:
        val = r.get(time_key)
        if not val:
            continue
        try:
            if hasattr(val, "timestamp"):
                # TIMESTAMPTZ datetime → 转为本地无时区 datetime 进行比较
                dt = datetime.fromtimestamp(val.timestamp())
                time_str = dt.strftime("%Y-%m-%d %H:%M")
            else:
                time_str = str(val).strip()[:16]
                if not time_str:
                    continue
                dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
            elapsed = (datetime.now() - dt).total_seconds() / 60
            if elapsed >= 1:
                results.append({
                    "talent_id": r["talent_id"],
                    "candidate_name": r.get("candidate_name") or "",
                    "candidate_email": r.get("candidate_email") or "",
                    time_key: time_str,
                    "elapsed_minutes": int(elapsed),
                })
        except Exception:
            continue
    return results


def get_pending_round1_reminders():
    rows = _query_all("""
        SELECT talent_id, candidate_name, candidate_email, round1_time
        FROM talents
        WHERE current_stage = 'ROUND1_SCHEDULED'
          AND round1_reminded_at IS NULL
          AND round1_confirm_status = 'CONFIRMED'
          AND round1_time IS NOT NULL
    """)
    return _parse_pending_reminders(rows, "round1_time")


def get_pending_interview_reminders():
    rows = _query_all("""
        SELECT talent_id, candidate_name, candidate_email, round2_time
        FROM talents
        WHERE current_stage = 'ROUND2_SCHEDULED'
          AND round2_reminded_at IS NULL
          AND round2_confirm_status = 'CONFIRMED'
          AND round2_time IS NOT NULL
    """)
    return _parse_pending_reminders(rows, "round2_time")


# ─── 笔试预审（v3.5 已下架，原 save_exam_prereview helper 同步删除）──────────


def save_exam_ai_review(talent_id, ai_result, actor="system"):
    # type: (str, dict, str) -> None
    """
    AI 笔试评审结果写入 talent_events。
    注意：本函数仅记录评审，不修改候选人状态机字段；最终通过/不通过由老板拍板。
    """
    if not _is_enabled():
        return
    payload = {
        "rubric_version": (ai_result.get("_meta") or {}).get("rubric_version"),
        "main_score": ai_result.get("main_score"),
        "time_modifier": ai_result.get("time_modifier"),
        "bonus_total": ai_result.get("bonus_total"),
        "penalty_total": ai_result.get("penalty_total"),
        "final_score_for_reference": ai_result.get("final_score_for_reference"),
        "summary": ai_result.get("summary"),
        "highlights": ai_result.get("highlights"),
        "risks": ai_result.get("risks"),
        "next_steps_for_boss": ai_result.get("next_steps_for_boss"),
        "dimension_scores": ai_result.get("dimension_scores"),
        "logic_checklist_scores": ai_result.get("logic_checklist_scores"),
        "bonus_scores": ai_result.get("bonus_scores"),
        "penalty_scores": ai_result.get("penalty_scores"),
    }
    if ai_result.get("_error"):
        payload["_error"] = ai_result.get("_error")
        payload["_message"] = ai_result.get("_message")
    entry = {
        "event_id": str(uuid.uuid4()),
        "at": datetime.now().isoformat(),
        "actor": actor,
        "action": "exam_ai_review",
        "payload": payload,
    }
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                _insert_events(cur, talent_id, [entry])
    except Exception as e:
        print("[talent_db] save_exam_ai_review 失败: {}".format(e), file=sys.stderr)


# ─── Round 通用操作（round_num 参数化，消除 round1/round2 重复）─────────────────

def save_invite_info(talent_id, round_num, calendar_event_id=None):
    # type: (str, int, Optional[str]) -> None
    """记录面试邀请发出时间，状态设为 PENDING，round_num=1 或 2。"""
    prefix = "round{}".format(round_num)
    if calendar_event_id:
        _update(
            "UPDATE talents SET {p}_invite_sent_at = NOW(), "
            "{p}_confirm_status = 'PENDING', "
            "{p}_calendar_event_id = %s, wait_return_round = NULL WHERE talent_id = %s".format(p=prefix),
            (calendar_event_id, talent_id),
        )
    else:
        _update(
            "UPDATE talents SET {p}_invite_sent_at = NOW(), "
            "{p}_confirm_status = 'PENDING', "
            "{p}_calendar_event_id = NULL, wait_return_round = NULL WHERE talent_id = %s".format(p=prefix),
            (talent_id,),
        )


def mark_confirmed(talent_id, round_num, auto=False):
    # type: (str, int, bool) -> None
    """标记面试已确认：只更新 confirm_status，不复制第二份时间。"""
    prefix = "round{}".format(round_num)
    sql = "UPDATE talents SET {p}_confirm_status = 'CONFIRMED'".format(p=prefix)
    if round_num == 1:
        sql += ", current_stage = 'ROUND1_SCHEDULED', wait_return_round = NULL"
    elif round_num == 2:
        sql += ", current_stage = 'ROUND2_SCHEDULED', wait_return_round = NULL"
    sql += " WHERE talent_id = %s"
    _update(sql, (talent_id,))


def update_calendar_event_id(talent_id, round_num, event_id):
    # type: (str, int, str) -> None
    prefix = "round{}".format(round_num)
    _update("UPDATE talents SET {}_calendar_event_id = %s WHERE talent_id = %s".format(prefix),
            (event_id, talent_id))


def clear_calendar_event_id(talent_id, round_num):
    prefix = "round{}".format(round_num)
    _update("UPDATE talents SET {}_calendar_event_id = NULL WHERE talent_id = %s".format(prefix),
            (talent_id,))


def clear_round_followup_fields(talent_id, round_num):
    # type: (str, int) -> None
    prefix = "round{}".format(round_num)
    _update(
        "UPDATE talents SET {p}_confirm_prompted_at = NULL, {p}_reminded_at = NULL "
        "WHERE talent_id = %s".format(p=prefix),
        (talent_id,),
    )


def reset_round_scheduling_tracking(talent_id, round_num):
    prefix = "round{}".format(round_num)
    _update(
        "UPDATE talents SET {p}_confirm_status = 'UNSET', "
        "{p}_time = NULL, "
        "{p}_invite_sent_at = NULL, {p}_calendar_event_id = NULL, "
        "{p}_confirm_prompted_at = NULL, {p}_reminded_at = NULL "
        "WHERE talent_id = %s".format(p=prefix),
        (talent_id,),
    )


def reset_round2_scheduling_tracking(talent_id):
    reset_round_scheduling_tracking(talent_id, 2)


# ─── 待确认列表查询 ───────────────────────────────────────────────────────────

def get_pending_confirmations(round_num):
    # type: (int) -> List[Dict[str, Any]]
    return _round_confirmation_candidates(round_num, confirmed=False)


def get_confirmed_candidates(round_num):
    # type: (int) -> List[Dict[str, Any]]
    return _round_confirmation_candidates(round_num, confirmed=True)


# ─── 改期 ─────────────────────────────────────────────────────────────────────

def mark_reschedule_pending(talent_id, round_num):
    """候选人申请改期：撤销确认状态 → PENDING，清除日历事件 ID。"""
    prefix = "round{}".format(round_num)
    stage = "ROUND1_SCHEDULING" if round_num == 1 else "ROUND2_SCHEDULING"
    _update(
        "UPDATE talents SET current_stage = %s, {p}_confirm_status = 'PENDING', "
        "{p}_calendar_event_id = NULL WHERE talent_id = %s".format(p=prefix),
        (stage, talent_id),
    )


def migrate_round2_pending_stage():
    """将历史 ROUND2_SCHEDULED + PENDING 修正为 ROUND2_SCHEDULING。"""
    _update(
        "UPDATE talents SET current_stage = 'ROUND2_SCHEDULING' "
        "WHERE current_stage = 'ROUND2_SCHEDULED' AND round2_confirm_status = 'PENDING'",
        (),
    )


def mark_wait_return(talent_id, round_num):
    # type: (str, int) -> None
    prefix = "round{}".format(round_num)
    _update(
        "UPDATE talents SET current_stage = 'WAIT_RETURN', wait_return_round = %s, "
        "{p}_confirm_status = 'UNSET', {p}_time = NULL, "
        "{p}_invite_sent_at = NULL, {p}_calendar_event_id = NULL, "
        "{p}_confirm_prompted_at = NULL, {p}_reminded_at = NULL "
        "WHERE talent_id = %s".format(p=prefix),
        (round_num, talent_id),
    )


def resume_wait_return(talent_id):
    # type: (str) -> Optional[int]
    row = _query_one("SELECT wait_return_round FROM talents WHERE talent_id = %s", (talent_id,))
    if not row:
        return None
    round_num = row.get("wait_return_round")
    if round_num not in (1, 2):
        return None
    stage = "ROUND1_SCHEDULING" if round_num == 1 else "ROUND2_SCHEDULING"
    ok = _update(
        "UPDATE talents SET current_stage = %s, wait_return_round = NULL WHERE talent_id = %s",
        (stage, talent_id),
    )
    return round_num if ok else None


# ─── 老板最终确认握手 ─────────────────────────────────────────────────────────

def set_boss_confirm_pending(talent_id, round_num, proposed_time):
    # type: (str, int, str) -> None
    """记录待老板确认的时间，状态置为 PENDING。"""
    _update(
        "UPDATE talents SET round{n}_time = %s, "
        "round{n}_confirm_status = 'PENDING', round{n}_confirm_prompted_at = NOW() "
        "WHERE talent_id = %s".format(n=round_num),
        (proposed_time, talent_id),
    )


def get_boss_confirm_pending(talent_id, round_num):
    # type: (str, int) -> Dict[str, Any]
    """查询某轮次是否有待老板确认的时间。"""
    empty = {"pending": False, "time": None, "prompt_at": None}
    row = _query_one(
        "SELECT round{n}_time, round{n}_confirm_status, "
        "round{n}_confirm_prompted_at "
        "FROM talents WHERE talent_id = %s".format(n=round_num),
        (talent_id,),
    )
    if not row:
        return empty
    status = _s(row.get("round{}_confirm_status".format(round_num)), "UNSET")
    prompted = row.get("round{}_confirm_prompted_at".format(round_num))
    current_time = _dt_to_local_str(row.get("round{}_time".format(round_num)))
    return {
        "time": current_time,
        "proposed_time": current_time,
        "pending": (status == "PENDING"),
        "prompt_at": prompted.isoformat() if prompted else None,
    }


def clear_boss_confirm_pending(talent_id, round_num):
    """清除催促时间戳（确认动作本身由 mark_confirmed / cmd_reschedule 完成）。"""
    _update(
        "UPDATE talents SET round{n}_confirm_prompted_at = NULL "
        "WHERE talent_id = %s".format(n=round_num),
        (talent_id,),
    )


def get_all_boss_confirm_pending():
    # type: () -> List[Dict[str, Any]]
    """返回所有处于待确认状态（PENDING）的面试安排，供老板审阅。"""
    rows = _query_all("""
        SELECT talent_id, candidate_name, candidate_email,
               round1_confirm_status, round1_time, round1_confirm_prompted_at,
               round2_confirm_status, round2_time, round2_confirm_prompted_at
        FROM talents
        WHERE round1_confirm_status = 'PENDING' OR round2_confirm_status = 'PENDING'
    """)
    results = []
    for r in rows:
        tid = _s(r["talent_id"], "")
        name = _s(r.get("candidate_name")) or tid
        email = _s(r["candidate_email"], "")
        if r.get("round1_confirm_status") == "PENDING":
            prompted = r.get("round1_confirm_prompted_at")
            results.append({
                "talent_id": tid, "candidate_name": name, "candidate_email": email,
                "round": 1,
                "time": _dt_to_local_str(r.get("round1_time")),
                "prompt_at": prompted.isoformat() if prompted else None,
            })
        if r.get("round2_confirm_status") == "PENDING":
            prompted = r.get("round2_confirm_prompted_at")
            results.append({
                "talent_id": tid, "candidate_name": name, "candidate_email": email,
                "round": 2,
                "time": _dt_to_local_str(r.get("round2_time")),
                "prompt_at": prompted.isoformat() if prompted else None,
            })
    return results


# ─── Offer 后跟进相关函数已在 v3.5.2 全部下线 ─────────────────────────────────
#   - enter_post_offer_followup(): 直接用 set_current_stage(tid, 'POST_OFFER_FOLLOWUP')
#   - get_active_followup_candidates(): followup_scanner 已废弃，无调用方
#   - get_followup_candidate(): 同上
#   - set_followup_status(): followup_status 字段已 DROP
#   - save_followup_event(): 用 save_audit_event() 替代，action 任意命名


def save_audit_event(talent_id, action, payload=None, actor="system"):
    # type: (str, str, Optional[dict], str) -> None
    """通用：往 talent_events 写一条 audit 事件（不限定 action 前缀）。

    与 save_followup_event 等价的更通用入口。设计目的：异步子进程
    （邮件投递 watcher、日历 watcher 等）只想追加一条事件，不想
    load_candidate -> save_candidate 整体覆盖（避免与主进程 race）。"""
    if not _is_enabled():
        return
    entry = {
        "event_id": str(uuid.uuid4()),
        "at": datetime.now().isoformat(),
        "actor": actor,
        "action": action,
        "payload": payload or {},
    }
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                _insert_events(cur, talent_id, [entry])
    except Exception as e:
        print("[talent_db] save_audit_event 失败: {}".format(e), file=sys.stderr)


# ─── 候选人邮件表 (talent_emails) ─────────────────────────────────────────────
#
# 这是 2026-04-20 引入的邮件实体表，目的是用 DB 唯一约束物理阻止重复识别
# （取代原 talents.<ctx>_last_email_id 单游标 + data/followup_pending JSON 兜底）。
# 详见 docs/PROJECT_OVERVIEW.md §4.1。
#
# 调用方约定：
#   - scanner 每识别一封邮件 → insert_email_if_absent()，None 返回即跳过；
#   - LLM 分析完 → mark_email_pending_boss()（含 ai_summary/ai_intent/reply_id）；
#   - 老板回信 → insert_outbound_reply() + mark_email_replied()；
#   - dismiss/snooze/close → mark_email_status()。
#
# 所有写入函数都受 db_writes_disabled() 拦截（dry-run 安全）。

_EMAIL_VALID_DIRECTIONS = ("inbound", "outbound")
_EMAIL_VALID_CONTEXTS = ("exam", "round1", "round2", "followup", "intake",
                         "rejection", "unknown")
# v3.5.11 (2026-04-22) 加入 "rejection"：auto_reject.cmd_scan_exam_timeout
# 一直以 --context rejection 调 outbound.cmd_send，但 cmd_send 在 SMTP 发完之后
# 才走到 insert_email_if_absent 校验，结果"邮件发了 / DB 没记 / executor 误判失败 /
# stage 不变 / 下个 cron tick 重发"——线上事故 2026-04-22 11:30 已发生 1 轮，
# 见 docs/CHANGELOG.md。
_EMAIL_VALID_STATUSES = (
    "received", "pending_boss", "replied", "dismissed",
    "snoozed", "auto_processed", "duplicate_skipped", "error",
)


def _dry_run_blocked(label):
    # type: (str) -> bool
    """统一的 dry-run 短路。返回 True 表示应跳过本次写入。"""
    try:
        from lib.side_effect_guard import db_writes_disabled
        if db_writes_disabled():
            print("[talent_db][dry-run] 跳过 {}".format(label), file=sys.stderr)
            return True
    except ImportError:
        pass
    return False


def insert_email_if_absent(
    talent_id,
    message_id,
    direction,
    context,
    sender,
    sent_at,
    subject=None,
    in_reply_to=None,
    references_chain=None,
    recipients=None,
    received_at=None,
    body_full=None,
    body_excerpt=None,
    stage_at_receipt=None,
    ai_summary=None,
    ai_intent=None,
    ai_payload=None,
    reply_id=None,
    initial_status="received",
    template=None,
    analyzed_at=None,
):
    # type: (...) -> Optional[str]
    """把一封邮件写入 talent_emails；如已存在 (talent_id, message_id) 则跳过。

    返回新插入的 email_id（UUID 字符串），命中已有记录时返回 None。
    这是 scanner 去重的核心 API —— 调用方只要看返回值就知道这封邮件是否新。

    所有 *_VALID_* 字段在 DB 层有 CHECK 约束兜底；这里也做一次客户端校验，
    给非法输入更友好的报错。
    """
    if not _is_enabled():
        return None
    if direction not in _EMAIL_VALID_DIRECTIONS:
        raise ValueError("非法 direction: {!r}".format(direction))
    if context not in _EMAIL_VALID_CONTEXTS:
        raise ValueError("非法 context: {!r}".format(context))
    if initial_status not in _EMAIL_VALID_STATUSES:
        raise ValueError("非法 status: {!r}".format(initial_status))
    if not message_id:
        raise ValueError("message_id 不能为空")
    if not sent_at:
        raise ValueError("sent_at 不能为空（用 IMAP Date 头解析）")
    if _dry_run_blocked("insert_email_if_absent talent={}".format(talent_id)):
        # dry-run 时假装"插入成功"，让上游能继续走完逻辑而无副作用。
        return str(uuid.uuid4())
    new_id = str(uuid.uuid4())
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO talent_emails (
                        email_id, talent_id, message_id, in_reply_to,
                        references_chain, direction, sender, recipients,
                        subject, sent_at, received_at, context,
                        stage_at_receipt, status, body_full, body_excerpt,
                        ai_summary, ai_intent, ai_payload, reply_id,
                        template, analyzed_at
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s
                    )
                    ON CONFLICT (talent_id, message_id) DO NOTHING
                    RETURNING email_id
                """, (
                    new_id, talent_id, message_id, in_reply_to,
                    references_chain, direction, sender, recipients,
                    subject, sent_at, received_at, context,
                    stage_at_receipt, initial_status, body_full, body_excerpt,
                    ai_summary, ai_intent,
                    Json(ai_payload) if ai_payload is not None else None,
                    reply_id,
                    template, analyzed_at,
                ))
                row = cur.fetchone()
                if row is None:
                    return None
                return str(row[0])
    except Exception as e:
        print("[talent_db] insert_email_if_absent 失败 talent={} msg={}: {}".format(
            talent_id, (message_id or "")[:60], e), file=sys.stderr)
        return None


def find_email_by_message_id(talent_id, message_id):
    # type: (str, str) -> Optional[Dict[str, Any]]
    """按 (talent_id, message_id) 找 talent_emails 行，返回 dict 或 None。

    用途：scanner 在 INSERT ... ON CONFLICT 命中（返回 None）后，需要拿到
    既存行的 email_id / reply_id / status，避免给同一封邮件重复生成 reply_id
    （历史 bug：4-20 同一封邮件被分配过两次 reply_id 写到 followup_pending/）。
    """
    if not _is_enabled() or not message_id:
        return None
    rows = _query_all(
        "SELECT email_id, reply_id, status, context, direction, subject "
        "FROM talent_emails WHERE talent_id = %s AND message_id = %s",
        (talent_id, message_id),
    )
    return rows[0] if rows else None


# ─── auto_reject 配套查询 ─────────────────────────────────────────────────────

def get_exam_timeout_candidates(threshold_days=3):
    # type: (int) -> List[Dict[str, Any]]
    """查 EXAM_SENT 阶段且 exam_sent_at 距今 ≥ threshold_days 的候选人。

    用于 auto_reject.cmd_scan_exam_timeout 找出"笔试发出去 N 天还没动静"的候选人。
    返回 dict 列表，含 talent_id、candidate_name、candidate_email、exam_sent_at。
    """
    return _query_all(
        "SELECT talent_id, candidate_name, candidate_email, exam_sent_at "
        "FROM talents "
        "WHERE current_stage = 'EXAM_SENT' "
        "  AND exam_sent_at IS NOT NULL "
        "  AND exam_sent_at < NOW() - (%s || ' days')::interval "
        "ORDER BY exam_sent_at ASC",
        (str(threshold_days),),
    )


def has_inbound_email_after(talent_id, after_dt):
    # type: (str, Any) -> bool
    """该候选人在 after_dt 之后是否收到过任何 inbound 邮件。

    用于 cmd_exam_timeout_scan 的"双重 check"：避免候选人刚交卷还没被审过就被拒。
    after_dt 可以是 datetime 对象或 ISO 字符串，None 视为"自始"。
    """
    if not _is_enabled() or not talent_id:
        return False
    if after_dt is None:
        sql = ("SELECT 1 FROM talent_emails "
               "WHERE talent_id = %s AND direction = 'inbound' LIMIT 1")
        params = (talent_id,)
    else:
        sql = ("SELECT 1 FROM talent_emails "
               "WHERE talent_id = %s AND direction = 'inbound' "
               "  AND sent_at > %s LIMIT 1")
        params = (talent_id, after_dt)
    rows = _query_all(sql, params)
    return bool(rows)


def has_outbound_rejection(talent_id):
    # type: (str) -> bool
    """该候选人是否已发过任何 context=rejection 的 outbound 邮件。

    用于 auto_reject.cmd_scan_exam_timeout 的二次幂等防护：即便 stage 因任何
    原因没从 EXAM_SENT 推到 EXAM_REJECT_KEEP（例如 v3.5.11 之前的 cmd_send
    崩溃事故），只要 talent_emails 已记下一封拒信，下次扫描就跳过，绝不重发。
    """
    if not _is_enabled() or not talent_id:
        return False
    rows = _query_all(
        "SELECT 1 FROM talent_emails "
        "WHERE talent_id = %s AND direction = 'outbound' AND context = 'rejection' "
        "LIMIT 1",
        (talent_id,),
    )
    return bool(rows)


def get_processed_message_ids(talent_id, direction="inbound"):
    # type: (str, Optional[str]) -> set
    """返回该候选人某方向所有已处理邮件的 Message-ID 集合。

    direction=None 时返回全部方向；默认 'inbound'（scanner 关心的"候选人来信去重"）。
    一条 SQL 替代原先的 `pending_store.seen_message_ids()` 文件目录扫描。
    """
    if not _is_enabled():
        return set()
    if direction is not None and direction not in _EMAIL_VALID_DIRECTIONS:
        raise ValueError("非法 direction: {!r}".format(direction))
    if direction is None:
        rows = _query_all(
            "SELECT message_id FROM talent_emails WHERE talent_id = %s",
            (talent_id,),
        )
    else:
        rows = _query_all(
            "SELECT message_id FROM talent_emails WHERE talent_id = %s AND direction = %s",
            (talent_id, direction),
        )
    return {r["message_id"] for r in rows if r.get("message_id")}


def mark_email_status(email_id, status, ai_summary=None, ai_intent=None,
                      ai_payload=None, reply_id=None,
                      replied_by_email_id=None, error_note=None):
    # type: (...) -> bool
    """更新一封邮件的状态机字段。所有可选 kwarg 仅在非 None 时被写入（部分更新）。

    error_note 仅在 status='error' 时写入 ai_payload['error']。
    """
    if not _is_enabled():
        return False
    if status not in _EMAIL_VALID_STATUSES:
        raise ValueError("非法 status: {!r}".format(status))
    if _dry_run_blocked("mark_email_status email={} → {}".format(email_id, status)):
        return True
    sets = ["status = %s"]
    params = [status]
    if ai_summary is not None:
        sets.append("ai_summary = %s"); params.append(ai_summary)
    if ai_intent is not None:
        sets.append("ai_intent = %s"); params.append(ai_intent)
    if ai_payload is not None:
        sets.append("ai_payload = %s"); params.append(Json(ai_payload))
    elif error_note is not None:
        sets.append("ai_payload = COALESCE(ai_payload, '{}'::jsonb) || %s")
        params.append(Json({"error": error_note}))
    if reply_id is not None:
        sets.append("reply_id = %s"); params.append(reply_id)
    if replied_by_email_id is not None:
        sets.append("replied_by_email_id = %s"); params.append(replied_by_email_id)
    sql = "UPDATE talent_emails SET {} WHERE email_id = %s".format(", ".join(sets))
    params.append(email_id)
    return _update(sql, tuple(params))


def set_email_analyzed(email_id, ai_summary=None, ai_intent=None, ai_payload=None):
    # type: (str, Optional[str], Optional[str], Optional[Dict[str, Any]]) -> bool
    """v3.3 inbox/cmd_analyze 专用：把一封 inbound 邮件的 analyzed_at 打上当下
    时间，并同步写 ai_summary / ai_intent / ai_payload。

    不动 status（status 的状态机语义和 analyzed_at 分离：analyzed_at 只表示
    "LLM 跑过这封邮件"）。
    """
    if not _is_enabled():
        return False
    if _dry_run_blocked("set_email_analyzed email={}".format(email_id)):
        return True
    sets = ["analyzed_at = NOW()"]
    params = []
    if ai_summary is not None:
        sets.append("ai_summary = %s"); params.append(ai_summary)
    if ai_intent is not None:
        sets.append("ai_intent = %s"); params.append(ai_intent)
    if ai_payload is not None:
        sets.append("ai_payload = %s"); params.append(Json(ai_payload))
    sql = "UPDATE talent_emails SET {} WHERE email_id = %s".format(", ".join(sets))
    params.append(email_id)
    return _update(sql, tuple(params))


def update_email_attachments(email_id, attachments):
    # type: (str, List[Dict[str, Any]]) -> bool
    """v3.5.6: 把 lib.email_attachments.extract_and_save 返回的元数据列表
    整体写到 talent_emails.attachments JSONB 字段（覆盖式）。

    调用约定：
      - 仅在 inbox.cmd_scan 真插入了一条新 inbound 邮件后调用一次
      - attachments 可以是空列表（[]，表示扫过但本邮件无附件）或非空
      - 不会自动 merge 既有值（覆盖式），便于回填脚本以"重跑就能修"
    """
    if not _is_enabled():
        return False
    if not email_id:
        raise ValueError("email_id 不能为空")
    if attachments is None:
        attachments = []
    if not isinstance(attachments, list):
        raise ValueError("attachments 必须是 list, 拿到 {!r}".format(type(attachments)))
    if _dry_run_blocked("update_email_attachments email={} n={}".format(
            email_id, len(attachments))):
        return True
    return _update(
        "UPDATE talent_emails SET attachments = %s WHERE email_id = %s",
        (Json(attachments), email_id),
    )


def list_unanalyzed_inbound(limit=50):
    # type: (int) -> List[Dict[str, Any]]
    """取 analyzed_at IS NULL 的 inbound 邮件，按 sent_at 升序（先来先分析）。

    返回字段覆盖 analyzer 需要的全部上下文。
    """
    if not _is_enabled():
        return []
    sql = (
        "SELECT e.email_id, e.talent_id, e.message_id, e.subject, e.sender, "
        "       e.sent_at, e.context, e.stage_at_receipt, e.body_full, "
        "       e.body_excerpt, t.candidate_name, t.current_stage "
        "FROM talent_emails e "
        "LEFT JOIN talents t ON e.talent_id = t.talent_id "
        "WHERE e.direction = 'inbound' AND e.analyzed_at IS NULL "
        "ORDER BY e.sent_at ASC LIMIT %s"
    )
    return _query_all(sql, (limit,))


def list_emails_by_status(status, talent_id=None, context=None, limit=200):
    # type: (str, Optional[str], Optional[str], int) -> List[Dict[str, Any]]
    """按 status 列出邮件，monitoring/CLI 用。"""
    if status not in _EMAIL_VALID_STATUSES:
        raise ValueError("非法 status: {!r}".format(status))
    where = ["status = %s"]
    params = [status]
    if talent_id:
        where.append("talent_id = %s"); params.append(talent_id)
    if context:
        if context not in _EMAIL_VALID_CONTEXTS:
            raise ValueError("非法 context: {!r}".format(context))
        where.append("context = %s"); params.append(context)
    sql = (
        "SELECT email_id, talent_id, message_id, direction, context, status, "
        "sender, subject, sent_at, ai_summary, ai_intent, reply_id "
        "FROM talent_emails WHERE {} ORDER BY sent_at DESC LIMIT %s"
    ).format(" AND ".join(where))
    params.append(limit)
    rows = _query_all(sql, tuple(params))
    out = []
    for r in rows:
        out.append({
            "email_id": str(r["email_id"]),
            "talent_id": r["talent_id"],
            "message_id": r.get("message_id"),
            "direction": r.get("direction"),
            "context": r.get("context"),
            "status": r.get("status"),
            "sender": r.get("sender"),
            "subject": r.get("subject"),
            "sent_at": r["sent_at"].isoformat() if r.get("sent_at") else None,
            "ai_summary": r.get("ai_summary"),
            "ai_intent": r.get("ai_intent"),
            "reply_id": r.get("reply_id"),
        })
    return out


def fetch_email(email_id):
    # type: (str) -> Optional[Dict[str, Any]]
    """按 email_id 取一行 talent_emails 完整字段（含 ai_payload）。

    主要给 outbound/cmd_send.py --use-cached-draft 用：从指定 inbound 邮件
    的 ai_payload 里读 draft 当 body。
    """
    if not _is_enabled() or not email_id:
        return None
    row = _query_one(
        "SELECT email_id, talent_id, message_id, direction, context, sender, "
        "       sent_at, subject, in_reply_to, references_chain, status, "
        "       body_full, body_excerpt, stage_at_receipt, "
        "       ai_summary, ai_intent, ai_payload, reply_id "
        "FROM talent_emails WHERE email_id = %s LIMIT 1",
        (email_id,),
    )
    if not row:
        return None
    out = dict(row)
    out["email_id"] = str(out["email_id"])
    return out


def get_email_by_reply_id(reply_id):
    # type: (str) -> Optional[Dict[str, Any]]
    """通过 reply_id 反查到对应的邮件行（outbound.cmd_send --use-cached-draft 等链路用）。"""
    if not _is_enabled() or not reply_id:
        return None
    row = _query_one(
        "SELECT email_id, talent_id, message_id, status FROM talent_emails "
        "WHERE reply_id = %s ORDER BY created_at DESC LIMIT 1",
        (reply_id,),
    )
    if not row:
        return None
    return {
        "email_id": str(row["email_id"]),
        "talent_id": row["talent_id"],
        "message_id": row.get("message_id"),
        "status": row.get("status"),
    }


def get_email_thread(talent_id, limit=50):
    # type: (str, int) -> List[Dict[str, Any]]
    """返回某候选人的完整邮件时间线（inbound + outbound 混排）。"""
    rows = _query_all(
        "SELECT email_id, message_id, in_reply_to, direction, context, status, "
        "sender, subject, sent_at, ai_summary, ai_intent, reply_id, body_excerpt "
        "FROM talent_emails WHERE talent_id = %s ORDER BY sent_at ASC LIMIT %s",
        (talent_id, limit),
    )
    out = []
    for r in rows:
        out.append({
            "email_id": str(r["email_id"]),
            "message_id": r.get("message_id"),
            "in_reply_to": r.get("in_reply_to"),
            "direction": r.get("direction"),
            "context": r.get("context"),
            "status": r.get("status"),
            "sender": r.get("sender"),
            "subject": r.get("subject"),
            "sent_at": r["sent_at"].isoformat() if r.get("sent_at") else None,
            "ai_summary": r.get("ai_summary"),
            "ai_intent": r.get("ai_intent"),
            "reply_id": r.get("reply_id"),
            "body_excerpt": r.get("body_excerpt"),
        })
    return out


# ─── v3.3 通用 talent 字段更新 ────────────────────────────────────────────────

# 允许通过 update_talent_field() 写入的字段白名单
# 不包含 talent_id（主键）、created_at（自动）、current_stage（必须走 set_current_stage）
_TALENT_UPDATABLE_FIELDS = frozenset({
    "candidate_email", "candidate_name",
    "phone", "wechat", "school", "education", "work_years", "experience",
    "source", "position", "cv_path",
    # v3.5.7：用于 §5.11 一面派单
    "has_cpp",
    "wait_return_round", "exam_id",
    "round1_time", "round2_time",
    "round1_invite_sent_at", "round2_invite_sent_at",
    "round1_calendar_event_id", "round2_calendar_event_id",
    "round1_confirm_status", "round2_confirm_status",
    "round1_reminded_at", "round2_reminded_at",
    "round1_confirm_prompted_at", "round2_confirm_prompted_at",
    "exam_sent_at",
})

# v3.4 公开别名 —— CLI 脚本（如 talent/cmd_update）可以用它做提前白名单校验，
# 而不必依赖 update_talent_field 在数据库连接成功后才抛错。
TALENT_UPDATABLE_FIELDS = _TALENT_UPDATABLE_FIELDS


def update_talent_field(talent_id, field, value):
    # type: (str, str, Any) -> bool
    """通用 talent 字段更新（v3.3 talent/cmd_update.py 用）。

    - field 必须在 _TALENT_UPDATABLE_FIELDS 白名单里（防止 SQL 注入 + 业务边界）
    - current_stage 不在白名单：必须走 set_current_stage（语义更明确）
    - value=None 视为清空（写 NULL）
    - 返回是否真正更新了行（False = talent 不存在或字段未变）
    """
    if not _is_enabled():
        return False
    if field not in _TALENT_UPDATABLE_FIELDS:
        raise ValueError(
            "字段 {!r} 不在白名单。current_stage 请用 set_current_stage()；"
            "其他字段需要先添加到 _TALENT_UPDATABLE_FIELDS。".format(field))
    if _dry_run_blocked("update_talent_field talent={} field={}".format(talent_id, field)):
        return True
    sql = "UPDATE talents SET {} = %s, updated_at = NOW() WHERE talent_id = %s".format(field)
    return _update(sql, (value, talent_id))


def set_current_stage(talent_id, new_stage, actor="system", reason=None):
    # type: (str, str, str, Optional[str]) -> bool
    """更新 talents.current_stage（v3.3 talent/cmd_update.py 用）+ 写审计事件。

    不做 transition 合法性校验；那是 cmd_update 的 natural-transitions 白名单的事。
    DB 层有 chk_current_stage CHECK 约束兜底非法 stage。
    """
    if not _is_enabled():
        return False
    if _dry_run_blocked("set_current_stage talent={} → {}".format(talent_id, new_stage)):
        return True
    ok = _update(
        "UPDATE talents SET current_stage = %s, updated_at = NOW() WHERE talent_id = %s",
        (new_stage, talent_id),
    )
    if ok:
        save_audit_event(
            talent_id, "stage.changed",
            payload={"new_stage": new_stage, "reason": reason},
            actor=actor,
        )
    return ok


def get_talent_current_stage(talent_id):
    # type: (str) -> Optional[str]
    """返回 talents.current_stage；talent 不存在时返回 None。"""
    if not _is_enabled() or not talent_id:
        return None
    row = _query_one(
        "SELECT current_stage FROM talents WHERE talent_id = %s",
        (talent_id,),
    )
    return row["current_stage"] if row else None


def get_talent_field(talent_id, field):
    # type: (str, str) -> Any
    """读取候选人某个字段的当前值。用于 self_verify。"""
    if not _is_enabled() or not talent_id:
        return None
    if field not in _TALENT_UPDATABLE_FIELDS and field != "current_stage":
        raise ValueError("字段 {!r} 不允许读取（不在白名单）".format(field))
    row = _query_one(
        "SELECT {} AS v FROM talents WHERE talent_id = %s".format(field),
        (talent_id,),
    )
    return row["v"] if row else None


def talent_exists(talent_id):
    # type: (str) -> bool
    if not _is_enabled() or not talent_id:
        return False
    row = _query_one("SELECT 1 AS x FROM talents WHERE talent_id = %s", (talent_id,))
    return row is not None


def find_outbound_email_by_message_id(talent_id, message_id):
    # type: (str, str) -> Optional[Dict[str, Any]]
    """v3.3 self_verify 用：cmd_send 发完邮件后立刻查 talent_emails 确认入库。"""
    if not _is_enabled() or not message_id:
        return None
    row = _query_one(
        "SELECT email_id, talent_id, message_id, template, direction, sent_at "
        "FROM talent_emails WHERE talent_id = %s AND message_id = %s "
        "  AND direction = 'outbound'",
        (talent_id, message_id),
    )
    return dict(row) if row else None


def get_full_talent_snapshot(talent_id):
    # type: (str) -> Optional[Dict[str, Any]]
    """完整 dump 一行 talents 用于 cmd_delete 备份归档。"""
    if not _is_enabled() or not talent_id:
        return None
    row = _query_one("SELECT * FROM talents WHERE talent_id = %s", (talent_id,))
    if not row:
        return None
    out = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


if __name__ == "__main__":
    state = load_state_from_db()
    n = len(state.get("candidates") or {})
    print("load_state_from_db: {} 位候选人".format(n))
