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

import config as _cfg

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
    "exam_last_email_id", "round1_last_email_id", "round2_last_email_id",
    # 个人信息
    "source", "position", "education", "work_years", "experience", "school",
    "phone", "wechat", "cv_path",
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
            exam_sent_at, cv_path,
            exam_last_email_id, round1_last_email_id, round2_last_email_id,
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
            %(exam_sent_at)s, %(cv_path)s,
            %(exam_last_email_id)s, %(r1_last_email_id)s, %(r2_last_email_id)s,
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
            exam_last_email_id = EXCLUDED.exam_last_email_id,
            round1_last_email_id = EXCLUDED.round1_last_email_id,
            round2_last_email_id = EXCLUDED.round2_last_email_id,
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
        "exam_last_email_id": cand.get("exam_last_email_id"),
        "r1_last_email_id": cand.get("round1_last_email_id"),
        "r2_last_email_id": cand.get("round2_last_email_id"),
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
    last_eid_col = "{}_last_email_id".format(prefix)
    rows = _query_all("""
        SELECT talent_id, candidate_email, candidate_name,
               {p}_time, {p}_invite_sent_at, {p}_confirm_status, {p}_calendar_event_id,
               {p}_last_email_id
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
            last_eid_col: _s(r.get(last_eid_col)),
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


# ─── 邮件游标 ─────────────────────────────────────────────────────────────────

def update_last_email_id(talent_id, context, email_id):
    # type: (str, str, str) -> None
    """更新候选人某阶段最后一封已处理邮件的 Message-ID。
    context: 'exam' | 'round1' | 'round2'
    """
    col = "{}_last_email_id".format(context)
    _update("UPDATE talents SET {} = %s WHERE talent_id = %s".format(col),
            (email_id, talent_id))


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
        WHERE current_stage IN ('ROUND2_SCHEDULED', 'ROUND2_DONE_PENDING')
          AND round2_reminded_at IS NULL
          AND round2_confirm_status = 'CONFIRMED'
          AND round2_time IS NOT NULL
    """)
    return _parse_pending_reminders(rows, "round2_time")


# ─── 笔试预审 ─────────────────────────────────────────────────────────────────

def save_exam_prereview(talent_id, exam_score, exam_notes):
    # type: (str, int, str) -> None
    """笔试预审结果写入 talent_events，不再占用 talents 表列。"""
    if not _is_enabled():
        return
    entry = {
        "event_id": str(uuid.uuid4()),
        "at": datetime.now().isoformat(),
        "actor": "system",
        "action": "exam_prereview",
        "payload": {"score": exam_score, "summary": exam_notes},
    }
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                _insert_events(cur, talent_id, [entry])
    except Exception as e:
        print("[talent_db] save_exam_prereview 失败: {}".format(e), file=sys.stderr)


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


if __name__ == "__main__":
    state = load_state_from_db()
    n = len(state.get("candidates") or {})
    print("load_state_from_db: {} 位候选人".format(n))
