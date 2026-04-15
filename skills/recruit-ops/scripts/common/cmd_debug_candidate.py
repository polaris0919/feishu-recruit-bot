#!/usr/bin/env python3

import os
import sys

_LIB = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

"""
直接从 PostgreSQL 打印候选人的完整 DB 视图，便于本地真库联调。

示例：
  python3 common/cmd_debug_candidate.py --talent-id t_xxxxx
"""

import argparse
import json

import psycopg2
from psycopg2.extras import RealDictCursor

import config as _cfg


_TALENT_SQL = """
SELECT
  talent_id,
  candidate_name,
  candidate_email,
  current_stage,
  wait_return_round,
  exam_id,
  round1_confirm_status,
  round1_time,
  round1_invite_sent_at,
  round1_calendar_event_id,
  round1_last_email_id,
  round1_reminded_at,
  round1_confirm_prompted_at,
  round2_confirm_status,
  round2_time,
  round2_invite_sent_at,
  round2_calendar_event_id,
  round2_last_email_id,
  round2_reminded_at,
  round2_confirm_prompted_at,
  exam_last_email_id,
  exam_sent_at,
  source,
  position,
  education,
  work_years,
  experience,
  school,
  phone,
  wechat,
  cv_path,
  created_at,
  updated_at
FROM talents
WHERE talent_id = %(talent_id)s
"""


_EVENTS_SQL = """
SELECT id, at, actor, action, payload
FROM talent_events
WHERE talent_id = %(talent_id)s
ORDER BY at DESC
LIMIT %(limit)s
"""


def _to_jsonable(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    return value


def main(argv=None):
    p = argparse.ArgumentParser(description="打印候选人的完整 DB 视图")
    p.add_argument("--talent-id", required=True, help="候选人 talent_id")
    p.add_argument("--event-limit", type=int, default=20, help="展示最近多少条 talent_events")
    args = p.parse_args(argv or sys.argv[1:])

    if not _cfg.db_enabled():
        print(json.dumps({
            "ok": False,
            "error": "DB 未配置，无法打印候选人完整视图",
        }, ensure_ascii=False, indent=2))
        return 1

    params = _cfg.db_conn_params()
    try:
        conn = psycopg2.connect(**params)
    except Exception as e:
        print(json.dumps({
            "ok": False,
            "error": "DB 连接失败: {}".format(e),
        }, ensure_ascii=False, indent=2))
        return 1

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(_TALENT_SQL, {"talent_id": args.talent_id})
            talent = cur.fetchone()
            if not talent:
                print(json.dumps({
                    "ok": False,
                    "error": "未找到候选人 {}".format(args.talent_id),
                }, ensure_ascii=False, indent=2))
                return 1

            cur.execute(_EVENTS_SQL, {
                "talent_id": args.talent_id,
                "limit": args.event_limit,
            })
            events = cur.fetchall()

        payload = {
            "ok": True,
            "talent": _to_jsonable(dict(talent)),
            "events": [_to_jsonable(dict(item)) for item in events],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
