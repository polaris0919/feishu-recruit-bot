#!/usr/bin/env python3
"""feishu/cmd_calendar_create.py —— v3.4 Phase 5 创建面试日历事件（atomic）。

【职责（只干这一件）】
  调 lib.feishu.create_interview_event 创建一次飞书日历事件，并把结果（含
  event_id、人话 message）以 JSON 输出。

【绝不做】
  - 不动 talents.* 任何字段
  - 不写 talent_emails
  - 不发邮件
  - 不推飞书业务通知

【与旧 lib/feishu/calendar_cli.py 的差异】
  - 标准化参数：--time 取代 --round2-time（兼容保留 --round2-time 别名）
  - 必带 --json 时输出 {"ok": ..., "event_id": ..., "message": ...}；
    bg_helpers / wrapper 可由此回填 talents.round{N}_calendar_event_id。
  - --dry-run / --json 行为对齐 v3.3 atomic CLI。
  - 走 cli_wrapper.run_with_self_verify（异常自动飞书告警）。

【调用】
  PYTHONPATH=scripts python3 -m feishu.cmd_calendar_create \\
      --talent-id t_xxx --time "2026-04-25 14:00" --round 2 \\
      --candidate-email cand@example.com --candidate-name 张三 --json
"""
from __future__ import print_function

import argparse
import json
import re
import sys
from typing import Optional

from lib.cli_wrapper import run_with_self_verify, UserInputError


_EVENT_ID_RE = re.compile(r"event_id\s*=\s*([\w\-:]+)", re.IGNORECASE)


def _extract_event_id(message):
    # type: (Optional[str]) -> Optional[str]
    """从 lib.feishu.create_interview_event 返回的人话 message 里抠出 event_id。

    create_interview_event 的实际返回字符串里（成功时）通常包含 'event_id=xxx'。
    若抠不到就返回 None；caller 可降级处理。"""
    if not message:
        return None
    m = _EVENT_ID_RE.search(message)
    return m.group(1) if m else None


def _build_parser():
    p = argparse.ArgumentParser(
        prog="feishu.cmd_calendar_create",
        description="创建面试日历事件（atomic, JSON 可读）",
    )
    p.add_argument("--talent-id", required=True)
    p.add_argument("--time", default=None,
                   help="面试时间 'YYYY-MM-DD HH:MM' 或 ISO；与 --round2-time 互通")
    p.add_argument("--round2-time", default=None,
                   help="历史别名（与 --time 等价）")
    p.add_argument("--round", dest="round_num", type=int, default=2, choices=[1, 2],
                   help="面试轮次，1=一面 2=二面（默认 2）")
    p.add_argument("--candidate-email", default="")
    p.add_argument("--candidate-name", default="")
    p.add_argument("--old-event-id", default="",
                   help="若提供，先尝试删除旧事件（lib.feishu 内置兜底）")
    # v3.5.7 §5.11 一面派单：把面试官 open_id 一同邀请进日历
    p.add_argument("--extra-attendee", action="append", default=[],
                   help="额外参与者的飞书 open_id（与老板并列），可重复传入。"
                        "典型用途：§5.11 把面试官加进日历。")
    p.add_argument("--duration-minutes", type=int, default=None,
                   help="事件时长（分钟）。默认 60；§5.11 一面用 30。")
    p.add_argument("--dry-run", action="store_true",
                   help="不真调飞书；输出会带 dry_run=True 标记")
    p.add_argument("--json", action="store_true", help="结果以 JSON 输出")
    return p


def _emit(args, payload):
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        if payload.get("ok"):
            print("[cmd_calendar_create] ok event_id={} msg={}".format(
                payload.get("event_id"), (payload.get("message") or "")[:160]))
        else:
            print("[cmd_calendar_create] FAILED: {}".format(
                payload.get("error") or "unknown"), file=sys.stderr)


def main(argv=None):
    args = _build_parser().parse_args(argv)
    interview_time = (args.time or args.round2_time or "").strip()
    if not interview_time:
        raise UserInputError("--time（或别名 --round2-time）必填")

    extra_attendees = [
        oid.strip() for oid in (args.extra_attendee or []) if oid and oid.strip()
    ]

    if args.dry_run:
        _emit(args, {
            "ok": True, "dry_run": True,
            "talent_id": args.talent_id, "round": args.round_num,
            "time": interview_time, "event_id": None,
            "extra_attendees": extra_attendees,
            "duration_minutes": args.duration_minutes,
            "message": "[DRY-RUN] 未真实创建日历事件",
        })
        return 0

    from lib.feishu import create_interview_event
    try:
        message = create_interview_event(
            talent_id=args.talent_id,
            interview_time=interview_time,
            round_num=args.round_num,
            candidate_email=args.candidate_email,
            candidate_name=args.candidate_name,
            old_event_id=args.old_event_id,
            extra_attendee_open_ids=extra_attendees,
            duration_minutes=args.duration_minutes,
        )
    except Exception as e:
        _emit(args, {
            "ok": False, "talent_id": args.talent_id, "round": args.round_num,
            "time": interview_time, "error": "{}: {}".format(type(e).__name__, e),
        })
        return 1

    event_id = _extract_event_id(message)
    payload = {
        "ok": True, "talent_id": args.talent_id, "round": args.round_num,
        "time": interview_time, "event_id": event_id, "message": message,
        "extra_attendees": extra_attendees,
        "duration_minutes": args.duration_minutes,
    }
    _emit(args, payload)
    return 0


if __name__ == "__main__":
    run_with_self_verify("feishu.cmd_calendar_create", main)
