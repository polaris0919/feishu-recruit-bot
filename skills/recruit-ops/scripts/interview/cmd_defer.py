#!/usr/bin/env python3
"""
合并后的面试暂缓脚本：一面/二面统一处理。
用法：
  python3 interview/cmd_defer.py --talent-id t_xxx --round 1|2 [--reason ...]
"""

import argparse
import sys
from core_state import append_audit, load_candidate, save_candidate
from bg_helpers import send_bg_email, delete_calendar


def _send_defer_email(to_email, talent_id, round_num, candidate_name=""):
    # type: (str, str, int, str) -> int
    round_label = "第一轮" if round_num == 1 else "第二轮"
    body = "\n".join([
        "您好，感谢您的回复。",
        "了解到您目前暂时不在国内/上海，我们这边先不安排本次面试。",
        "等您之后方便回国或到上海时，可以再与我们联系，我们再为您协调安排。",
        "致邃投资 招聘团队",
        "",
        "---",
        "TALENT_ID: " + talent_id,
    ])
    subject = "【面试安排更新】{}面试暂缓，待回国后再约 - 致邃投资".format(round_label)
    return send_bg_email(
        to_email,
        subject,
        body,
        tag="round{}_defer".format(round_num),
    )


def _spawn_calendar_delete_bg(event_id, round_num):
    # type: (str, int) -> int
    return delete_calendar(event_id, tag="round{}_defer_delete".format(round_num))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="暂缓一面/二面安排，待候选人回国或到上海后再约")
    p.add_argument("--talent-id", required=True, help="候选人 talent_id")
    p.add_argument("--round", type=int, required=True, choices=[1, 2], help="轮次：1 或 2")
    p.add_argument("--reason", default="", help="暂缓原因")
    p.add_argument("--actor", default="system", help="操作人（用于审计）")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    talent_id = args.talent_id.strip()
    round_num = args.round
    prefix = "round{}".format(round_num)
    round_label = "一面" if round_num == 1 else "二面"
    current_allowed = (
        {"ROUND1_SCHEDULING", "ROUND1_SCHEDULED"}
        if round_num == 1 else
        {"ROUND2_SCHEDULING", "ROUND2_SCHEDULED"}
    )
    reason = (args.reason or "").strip() or "候选人暂时不在国内/上海，之后再约"

    cand = load_candidate(talent_id)
    if not cand:
        print("ERROR: 未找到候选人 {}".format(talent_id), file=sys.stderr)
        return 1

    current_stage = cand.get("stage") or "NEW"
    if current_stage not in current_allowed:
        print(
            "ERROR: 候选人 {} 当前阶段为 {}，只有 {} 才能暂缓{}。".format(
                talent_id,
                current_stage,
                " / ".join(sorted(current_allowed)),
                round_label,
            ),
            file=sys.stderr,
        )
        return 1

    candidate_email = cand.get("candidate_email") or ""
    candidate_name = cand.get("candidate_name") or ""
    old_event_id = (cand.get("{}_calendar_event_id".format(prefix)) or "").strip()

    cand["stage"] = "WAIT_RETURN"
    cand["wait_return_round"] = round_num
    cand["{}_confirm_status".format(prefix)] = "UNSET"
    cand["{}_time".format(prefix)] = None
    cand["{}_invite_sent_at".format(prefix)] = None
    cand["{}_calendar_event_id".format(prefix)] = None

    append_audit(
        cand,
        actor=args.actor,
        action="{}_deferred_until_return".format(prefix),
        payload={"reason": reason, "wait_return_round": round_num},
    )
    save_candidate(talent_id, cand)

    import talent_db as _tdb
    if _tdb._is_enabled():
        _tdb.clear_round_followup_fields(talent_id, round_num)

    lines = [
        "[{}暂缓安排]".format(round_label),
        "- talent_id: {}".format(talent_id),
        "- 候选人: {}".format(candidate_name or talent_id),
        "- 暂缓原因: {}".format(reason),
        "- 当前阶段: WAIT_RETURN",
        "- 状态说明: 已进入统一暂缓状态，待回国后再恢复{}安排".format(round_label),
    ]

    if candidate_email:
        email_pid = _send_defer_email(candidate_email, talent_id, round_num, candidate_name=candidate_name)
        lines.append("- 候选人通知邮件: 发送中（后台 PID={}）".format(email_pid))
    else:
        lines.append("- 候选人邮箱未记录，无法自动发送通知邮件")

    if old_event_id:
        delete_pid = _spawn_calendar_delete_bg(old_event_id, round_num)
        lines.append("- 老板飞书日历: 旧事件删除中（后台 PID={}）".format(delete_pid))

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
