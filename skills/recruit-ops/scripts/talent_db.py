#!/usr/bin/env python3
"""
人才库 PostgreSQL 读写模块。
配置优先级：环境变量 > 与脚本同目录的 talent-db-config.json > ~/.openclaw/talent-db-config.json
"""
from __future__ import print_function

import json
import os
import re as _re
import sys
from datetime import datetime
from typing import Any, Dict, List, Set

try:
    import psycopg2
    from psycopg2.extras import execute_values, Json
except ImportError:
    psycopg2 = None
    Json = None

_CONFIG_LOADED = False


def _config_file_paths():
    paths = []
    if os.environ.get("TALENT_DB_CONFIG_PATH"):
        paths.append(os.path.expanduser(os.environ["TALENT_DB_CONFIG_PATH"]))
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        paths.append(os.path.join(_here, "talent-db-config.json"))
        _workspace = os.path.normpath(os.path.join(_here, "..", "..", ".."))
        paths.append(os.path.join(_workspace, "talent-db-config.json"))
    except Exception:
        pass
    paths.append(os.path.expanduser("~/.openclaw/talent-db-config.json"))
    paths.append(os.path.expanduser("~/.openclaw/workspace/talent-db-config.json"))
    return paths


def _ensure_config_from_file():
    global _CONFIG_LOADED
    if _CONFIG_LOADED:
        return
    _CONFIG_LOADED = True
    if os.environ.get("TALENT_DB_PASSWORD"):
        return
    for path in _config_file_paths():
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                if k.startswith("TALENT_DB_") and v is not None and str(v).strip():
                    os.environ.setdefault(k, str(v).strip())
            if os.environ.get("TALENT_DB_PASSWORD"):
                return
        except Exception:
            continue


def _env(key, default):
    _ensure_config_from_file()
    return (os.environ.get(key) or "").strip() or default


def _is_enabled():
    _ensure_config_from_file()
    if not psycopg2:
        return False
    return bool(os.environ.get("TALENT_DB_PASSWORD", "").strip())


def _conn_params():
    _ensure_config_from_file()
    return {
        "host": _env("TALENT_DB_HOST", "127.0.0.1"),
        "port": int(_env("TALENT_DB_PORT", "5432")),
        "dbname": _env("TALENT_DB_NAME", "recruit"),
        "user": _env("TALENT_DB_USER", "recruit_app"),
        "password": (os.environ.get("TALENT_DB_PASSWORD") or "").strip(),
        "connect_timeout": 10,
    }


def _parse_iso(ts):
    """Parse ISO timestamp to naive UTC datetime. Python 3.6 compatible."""
    s = (ts or "").strip()
    if not s:
        return datetime.utcnow()
    s_plain = _re.sub(r"[+-]\d{2}:\d{2}$", "", s.replace("Z", "")).strip()
    s_plain = s_plain.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s_plain, fmt)
        except ValueError:
            continue
    return datetime.utcnow()


def _upsert_talent(conn, tid, cand):
    stage = (cand.get("stage") or "NEW").strip()
    email = (cand.get("candidate_email") or "").strip() or None
    name = (cand.get("candidate_name") or "").strip() or None
    exam_id = (cand.get("exam_id") or "").strip() or None
    round2_time = (cand.get("round2_time") or "").strip() or None
    round2_interviewer = (cand.get("round2_interviewer") or "").strip() or None
    position = (cand.get("position") or "").strip() or None
    education = (cand.get("education") or "").strip() or None
    work_years = cand.get("work_years") or None
    experience = (cand.get("experience") or "").strip() or None
    source = (cand.get("source") or "").strip() or None
    school = (cand.get("school") or "").strip() or None
    phone = (cand.get("phone") or "").strip() or None
    wechat = (cand.get("wechat") or "").strip() or None
    round1_notes = (cand.get("round1_notes") or "").strip() or None
    exam_score = cand.get("exam_score") or None
    exam_notes = (cand.get("exam_notes") or "").strip() or None
    round2_score = cand.get("round2_score") or None
    round2_notes = (cand.get("round2_notes") or "").strip() or None
    exam_sent_at_raw = cand.get("exam_sent_at")
    try:
        exam_sent_at = _parse_iso(exam_sent_at_raw) if exam_sent_at_raw else None
    except Exception:
        exam_sent_at = None
    now = datetime.utcnow()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO talents (
                talent_id, candidate_email, candidate_name, current_stage,
                exam_id, round2_time, round2_interviewer, source,
                position, education, work_years, experience,
                school, phone, wechat,
                round1_notes, exam_score, exam_notes,
                round2_score, round2_notes,
                exam_sent_at,
                created_at, updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (talent_id) DO UPDATE SET
                candidate_email    = EXCLUDED.candidate_email,
                candidate_name     = EXCLUDED.candidate_name,
                current_stage      = EXCLUDED.current_stage,
                exam_id            = EXCLUDED.exam_id,
                round2_time        = EXCLUDED.round2_time,
                round2_interviewer = EXCLUDED.round2_interviewer,
                source             = EXCLUDED.source,
                position           = EXCLUDED.position,
                education          = EXCLUDED.education,
                work_years         = EXCLUDED.work_years,
                experience         = EXCLUDED.experience,
                school             = EXCLUDED.school,
                phone              = EXCLUDED.phone,
                wechat             = EXCLUDED.wechat,
                round1_notes       = EXCLUDED.round1_notes,
                exam_score         = EXCLUDED.exam_score,
                exam_notes         = EXCLUDED.exam_notes,
                round2_score       = EXCLUDED.round2_score,
                round2_notes       = EXCLUDED.round2_notes,
                exam_sent_at       = COALESCE(talents.exam_sent_at, EXCLUDED.exam_sent_at),
                updated_at         = EXCLUDED.updated_at
            """,
            (tid, email, name, stage, exam_id, round2_time, round2_interviewer, source,
             position, education, work_years, experience,
             school, phone, wechat,
             round1_notes, exam_score, exam_notes,
             round2_score, round2_notes, exam_sent_at, now, now),
        )


def _existing_event_keys(conn, talent_id):
    with conn.cursor() as cur:
        cur.execute("SELECT at, action FROM talent_events WHERE talent_id = %s", (talent_id,))
        result = set()
        for r in cur.fetchall():
            at_val = r[0]
            if hasattr(at_val, "replace"):
                at_naive = at_val.replace(tzinfo=None) if at_val.tzinfo else at_val
                k = at_naive.strftime("%Y-%m-%d %H:%M:%S")
            else:
                k = str(at_val)[:19]
            result.add((k, r[1]))
        return result


def _insert_events(conn, talent_id, audit):
    if not audit:
        return
    existing = _existing_event_keys(conn, talent_id)
    rows = []
    for entry in audit:
        at_str = (entry.get("at") or "").strip()
        actor = (entry.get("actor") or "system").strip() or "system"
        action = (entry.get("action") or "").strip()
        payload = entry.get("payload")
        payload_obj = payload if isinstance(payload, dict) else {}
        try:
            at_dt = _parse_iso(at_str)
        except Exception:
            at_dt = datetime.utcnow()
        key = (at_dt.strftime("%Y-%m-%d %H:%M:%S"), action)
        if key in existing:
            continue
        rows.append((talent_id, at_dt, actor, action, Json(payload_obj) if Json else json.dumps(payload_obj)))
    if not rows:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO talent_events (talent_id, at, actor, action, payload) VALUES %s",
            rows,
        )


# ─── 公开接口 ─────────────────────────────────────────────────────────────────

def load_state_from_db():
    # type: () -> Dict[str, Any]
    if not _is_enabled():
        return {"candidates": {}}
    try:
        conn = psycopg2.connect(**_conn_params())
    except Exception as e:
        print("[talent_db] 连接失败: {}".format(e), file=sys.stderr)
        return {"candidates": {}}
    candidates = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT talent_id, candidate_email, candidate_name, current_stage, "
                "exam_id, round2_time, round2_interviewer, source, "
                "position, education, work_years, experience, "
                "school, phone, wechat, "
                "round1_notes, exam_score, exam_notes, "
                "round2_score, round2_notes, updated_at, exam_sent_at FROM talents"
            )
            for row in cur.fetchall():
                tid = (row[0] or "").strip()
                if not tid:
                    continue
                cand = {
                    "talent_id": tid,
                    "candidate_email": (row[1] or "").strip(),
                    "candidate_name": (row[2] or "").strip() or None,
                    "stage": (row[3] or "NEW").strip(),
                    "audit": [],
                    "exam_id": (row[4] or "").strip() or None,
                    "round2_time": str(row[5]).strip() if row[5] else None,
                    "round2_interviewer": (row[6] or "").strip() or None,
                    "source": (row[7] or "").strip() or None,
                    "position": (row[8] or "").strip() or None,
                    "education": (row[9] or "").strip() or None,
                    "work_years": row[10],
                    "experience": (row[11] or "").strip() or None,
                    "school": (row[12] or "").strip() or None,
                    "phone": (row[13] or "").strip() or None,
                    "wechat": (row[14] or "").strip() or None,
                    "round1_notes": (row[15] or "").strip() or None,
                    "exam_score": row[16],
                    "exam_notes": (row[17] or "").strip() or None,
                    "round2_score": row[18],
                    "round2_notes": (row[19] or "").strip() or None,
                    "updated_at": row[20].isoformat() if row[20] else None,
                    "exam_sent_at": row[21].isoformat() if row[21] else None,
                }
                candidates[tid] = cand
        with conn.cursor() as cur:
            cur.execute(
                "SELECT talent_id, at, actor, action, payload FROM talent_events ORDER BY at ASC"
            )
            for row in cur.fetchall():
                tid = (row[0] or "").strip()
                if tid not in candidates:
                    continue
                at_val = row[1]
                at_str = at_val.isoformat().replace("+00:00", "Z") if hasattr(at_val, "isoformat") else str(at_val)
                payload = row[4] if isinstance(row[4], dict) else {}
                candidates[tid]["audit"].append({
                    "at": at_str,
                    "actor": (row[2] or "system").strip(),
                    "action": (row[3] or "").strip(),
                    "payload": payload or {},
                })
    except Exception as e:
        print("[talent_db] load_state_from_db 失败: {}".format(e), file=sys.stderr)
        return {"candidates": {}}
    finally:
        conn.close()
    return {"candidates": candidates}


def sync_state_to_db(state):
    # type: (Dict[str, Any]) -> None
    if not _is_enabled():
        return
    candidates = state.get("candidates") or {}
    if not candidates:
        return
    try:
        conn = psycopg2.connect(**_conn_params())
    except Exception as e:
        print("[talent_db] 连接失败: {}".format(e), file=sys.stderr)
        return
    try:
        for tid, cand in candidates.items():
            tid = (tid or "").strip()
            if not tid:
                continue
            try:
                _upsert_talent(conn, tid, cand)
                _insert_events(conn, tid, cand.get("audit") or [])
            except Exception as e:
                print("[talent_db] 同步候选人 {} 失败: {}".format(tid, e), file=sys.stderr)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print("[talent_db] sync_state_to_db 失败: {}".format(e), file=sys.stderr)
    finally:
        conn.close()


def delete_talent_from_db(talent_id):
    # type: (str) -> bool
    if not _is_enabled():
        return False
    talent_id = (talent_id or "").strip()
    if not talent_id:
        return False
    try:
        conn = psycopg2.connect(**_conn_params())
    except Exception as e:
        print("[talent_db] 连接失败: {}".format(e), file=sys.stderr)
        return False
    deleted = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM talents WHERE talent_id = %s", (talent_id,))
            if cur.fetchone() is None:
                return False
            cur.execute("DELETE FROM talent_events WHERE talent_id = %s", (talent_id,))
            cur.execute("DELETE FROM talents WHERE talent_id = %s", (talent_id,))
        conn.commit()
        deleted = True
    except Exception as e:
        conn.rollback()
        print("[talent_db] delete_talent_from_db 失败: {}".format(e), file=sys.stderr)
    finally:
        conn.close()
    return deleted


def get_processed_email_ids():
    # type: () -> Set[str]
    if not _is_enabled():
        return set()
    try:
        conn = psycopg2.connect(**_conn_params())
    except Exception as e:
        print("[talent_db] 连接失败: {}".format(e), file=sys.stderr)
        return set()
    result = set()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT message_id FROM processed_emails")
            for row in cur.fetchall():
                result.add(row[0])
    except Exception as e:
        print("[talent_db] get_processed_email_ids 失败: {}".format(e), file=sys.stderr)
    finally:
        conn.close()
    return result


def mark_emails_processed(entries):
    # type: (List[tuple]) -> None
    if not _is_enabled() or not entries:
        return
    try:
        conn = psycopg2.connect(**_conn_params())
    except Exception as e:
        print("[talent_db] 连接失败: {}".format(e), file=sys.stderr)
        return
    try:
        with conn.cursor() as cur:
            for mid, tid in entries:
                cur.execute(
                    "INSERT INTO processed_emails (message_id, talent_id) VALUES (%s, %s) "
                    "ON CONFLICT (message_id) DO NOTHING",
                    (mid, tid),
                )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print("[talent_db] mark_emails_processed 失败: {}".format(e), file=sys.stderr)
    finally:
        conn.close()


def mark_interview_reminded(talent_id):
    # type: (str) -> None
    if not _is_enabled():
        return
    try:
        conn = psycopg2.connect(**_conn_params())
    except Exception as e:
        print("[talent_db] 连接失败: {}".format(e), file=sys.stderr)
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE talents SET interview_reminded_at = NOW() WHERE talent_id = %s",
                (talent_id,),
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print("[talent_db] mark_interview_reminded 失败: {}".format(e), file=sys.stderr)
    finally:
        conn.close()


def get_pending_interview_reminders():
    # type: () -> List[Dict[str, Any]]
    if not _is_enabled():
        return []
    try:
        conn = psycopg2.connect(**_conn_params())
    except Exception as e:
        print("[talent_db] 连接失败: {}".format(e), file=sys.stderr)
        return []
    results = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT talent_id, candidate_email, round2_time
                FROM talents
                WHERE current_stage IN ('ROUND2_SCHEDULED', 'ROUND2_DONE_PENDING')
                  AND interview_reminded_at IS NULL
                  AND round2_time IS NOT NULL
                """,
            )
            rows = cur.fetchall()
        for row in rows:
            tid, email, r2time_raw = row[0], row[1] or "", row[2]
            if not r2time_raw:
                continue
            r2time_str = str(r2time_raw).strip()
            try:
                r2dt = datetime.strptime(r2time_str[:16], "%Y-%m-%d %H:%M")
                from datetime import timedelta
                now_cst = datetime.utcnow() + timedelta(hours=8)
                elapsed_minutes = (now_cst - r2dt).total_seconds() / 60
                if elapsed_minutes >= 1:
                    results.append({
                        "talent_id": tid,
                        "candidate_email": email,
                        "round2_time": r2time_str,
                        "elapsed_minutes": int(elapsed_minutes),
                    })
            except Exception:
                continue
    except Exception as e:
        print("[talent_db] get_pending_interview_reminders 失败: {}".format(e), file=sys.stderr)
    finally:
        conn.close()
    return results


def save_exam_prereview(talent_id, exam_score, exam_notes):
    # type: (str, int, str) -> None
    """
    将笔试预审初评分和摘要写入 talents 表。
    仅在候选人存在且处于笔试相关阶段时写入，不覆盖人工填写的内容（若已有值则追加）。
    """
    if not _is_enabled():
        return
    try:
        conn = psycopg2.connect(**_conn_params())
    except Exception as e:
        print("[talent_db] 连接失败: {}".format(e), file=sys.stderr)
        return
    try:
        with conn.cursor() as cur:
            # 读取现有值，避免覆盖人工填写
            cur.execute(
                "SELECT exam_score, exam_notes FROM talents WHERE talent_id = %s",
                (talent_id,),
            )
            row = cur.fetchone()
            if row is None:
                print("[talent_db] save_exam_prereview: 未找到 {}".format(talent_id),
                      file=sys.stderr)
                return

            existing_score, existing_notes = row[0], row[1] or ""

            # 仅在没有人工评分时写入初评分
            new_score = existing_score if existing_score is not None else exam_score

            # 追加预审摘要（避免覆盖已有的人工备注）
            if existing_notes and exam_notes not in existing_notes:
                new_notes = existing_notes.rstrip() + "\n" + exam_notes
            else:
                new_notes = exam_notes

            cur.execute(
                "UPDATE talents SET exam_score = %s, exam_notes = %s WHERE talent_id = %s",
                (new_score, new_notes, talent_id),
            )
        conn.commit()
        print("[talent_db] save_exam_prereview: {} score={} ok".format(talent_id, new_score))
    except Exception as e:
        conn.rollback()
        print("[talent_db] save_exam_prereview 失败: {}".format(e), file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    state = load_state_from_db()
    n = len(state.get("candidates") or {})
    print("load_state_from_db: {} 位候选人".format(n))
