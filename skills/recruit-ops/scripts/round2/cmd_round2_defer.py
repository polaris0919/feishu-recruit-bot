#!/usr/bin/env python3

import os, sys
_LIB = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

"""暂缓二面安排：候选人暂时不在国内/上海，之后再约。"""
import argparse
import sys

from core_state import append_audit, load_candidate, save_candidate
from bg_helpers import send_bg_email, delete_calendar


def _send_defer_email(to_email, talent_id, candidate_name=""):
    # type: (str, str, str) -> int
    body = "\n".join([
        "您好，感谢您的回复。",
        "了解到您目前暂时不在国内/上海，我们这边先不安排本次面试。",
        "等您之后方便回国或到上海时，可以再与我们联系，我们再为您协调安排。",
        "致邃投资 招聘团队",
        "",
        "---",
        "TALENT_ID: " + talent_id,
    ])
    subject = "【面试安排更新】第二轮面试暂缓，待回国后再约 - 致邃投资"
    return send_bg_email(to_email, subject, body, tag="round2_defer")


def _spawn_calendar_delete_bg(event_id):
    # type: (str) -> int
    return delete_calendar(event_id, tag="round2_defer_delete")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="暂缓二面安排，待候选人之后回国/到上海后再约")
    p.add_argument("--talent-id", required=True, help="候选人 talent_id")
    p.add_argument("--reason", default="", help="暂缓原因")
    p.add_argument("--actor", default="system", help="操作人（用于审计）")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    talent_id = args.talent_id.strip()
    reason = (args.reason or "").strip() or "候选人暂时不在国内/上海，之后再约"

    cand = load_candidate(talent_id)
    if not cand:
        print("ERROR: 未找到候选人 {}".format(talent_id), file=sys.stderr)
        return 1
    current_stage = cand.get("stage") or "NEW"
    if current_stage not in ("ROUND2_SCHEDULING", "ROUND2_SCHEDULED"):
        print(
            "ERROR: 候选人 {} 当前阶段为 {}，只有 ROUND2_SCHEDULING / ROUND2_SCHEDULED 才能暂缓二面。".format(
                talent_id, current_stage
            ),
            file=sys.stderr,
        )
        return 1

    candidate_email = cand.get("candidate_email") or ""
    candidate_name = cand.get("candidate_name") or ""
    old_event_id = (cand.get("round2_calendar_event_id") or "").strip()

    cand["stage"] = "WAIT_RETURN"
    cand["wait_return_round"] = 2
    cand["round2_confirm_status"] = "UNSET"
    cand["round2_time"] = None
    cand["round2_invite_sent_at"] = None
    cand["round2_calendar_event_id"] = None
    cand["round2_confirm_prompted_at"] = None
    cand["round2_reminded_at"] = None

    append_audit(
        cand,
        actor=args.actor,
        action="round2_deferred_until_return",
        payload={"reason": reason, "wait_return_round": 2},
    )

    save_candidate(talent_id, cand)

    import talent_db as _tdb
    if _tdb._is_enabled():
        _tdb.mark_wait_return(talent_id, 2)

    lines = [
        "[二面暂缓安排]",
        "- talent_id: {}".format(talent_id),
        "- 候选人: {}".format(candidate_name or talent_id),
        "- 暂缓原因: {}".format(reason),
        "- 当前阶段: WAIT_RETURN",
        "- 状态说明: 已进入统一暂缓状态，待回国后再恢复二面安排",
    ]

    if candidate_email:
        email_pid = _send_defer_email(candidate_email, talent_id, candidate_name=candidate_name)
        lines.append("- 候选人通知邮件: 发送中（后台 PID={}）".format(email_pid))
    else:
        lines.append("- ⚠ 候选人邮箱未记录，无法自动发送通知邮件")

    if old_event_id:
        delete_pid = _spawn_calendar_delete_bg(old_event_id)
        lines.append("- 老板飞书日历: 旧事件删除中（后台 PID={}）".format(delete_pid))

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
