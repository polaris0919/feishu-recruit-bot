#!/usr/bin/env python3

"""
处理已确认面试候选人的改期请求（一面/二面通用）：
  1. 撤销确认状态（confirm_status → PENDING）
  2. 删除已有飞书日历事件
  3. 自动回复候选人确认收到邮件
  4. 记录审计日志

用法：
  python3 cmd_reschedule_request.py \
    --talent-id t_xxx \
    --round 1|2 \
    [--reason "候选人说明"] \
    [--new-time "2026-04-18 15:00"]
"""
import argparse
import os
import sys

from core_state import append_audit, load_candidate, save_candidate
from bg_helpers import send_bg_email, delete_calendar


def _send_ack_email(to_email, talent_id, round_num, candidate_name=""):
    # type: (str, str, int, str) -> int
    round_label = "第一轮" if round_num == 1 else "第二轮"
    subject = "Re: 【面试通知】{}面试时间确认 - 致邃投资".format(round_label)
    body_parts = [
        "您好，{}，".format(candidate_name if candidate_name else ""),
        "",
        "收到您的改期申请，我们会尽快与您协调新的{}面试时间安排。".format(round_label),
        "如有任何问题，请随时回复本邮件。",
        "",
        "致邃投资 招聘团队",
        "",
        "---",
        "TALENT_ID: " + talent_id,
    ]
    body = "\n".join(body_parts)

    return send_bg_email(to_email, subject, body, tag="reschedule_ack")


def _spawn_calendar_delete_bg(event_id):
    # type: (str) -> int
    return delete_calendar(event_id, tag="reschedule_delete")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="处理已确认面试候选人的改期请求")
    p.add_argument("--talent-id", required=True, help="候选人 talent_id")
    p.add_argument("--round", type=int, required=True, choices=[1, 2], help="面试轮次 1 或 2")
    p.add_argument("--reason", default="", help="候选人改期原因摘要")
    p.add_argument("--new-time", default="", help="候选人提出的新时间（可选）")
    p.add_argument("--actor", default="system", help="操作人（用于审计）")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    talent_id = args.talent_id.strip()
    round_num = args.round

    cand = load_candidate(talent_id)
    if not cand:
        print("ERROR: 未找到候选人 {}".format(talent_id), file=sys.stderr)
        return 1
    current_stage = cand.get("stage") or "NEW"

    round_label = "一面" if round_num == 1 else "二面"

    if round_num == 1:
        expected_stages = ("ROUND1_SCHEDULING", "ROUND1_SCHEDULED")
    else:
        expected_stages = ("ROUND2_SCHEDULING", "ROUND2_SCHEDULED")

    if current_stage not in expected_stages:
        print(
            "ERROR: 候选人 {} 当前阶段为 {}，不在 {} 的有效改期阶段内。".format(
                talent_id, current_stage, "/".join(expected_stages)
            ),
            file=sys.stderr,
        )
        return 1

    candidate_email = cand.get("candidate_email", "")
    candidate_name = cand.get("candidate_name", "")
    prefix = "round{}".format(round_num)
    old_time = cand.get("{}_time".format(prefix)) or "(未记录)"
    cal_key = "round1_calendar_event_id" if round_num == 1 else "round2_calendar_event_id"
    old_event_id = cand.get(cal_key) or ""

    action_name = "round{}_reschedule_requested".format(round_num)
    append_audit(
        cand,
        actor=args.actor,
        action=action_name,
        payload={
            "old_time": old_time,
            "reason": args.reason or "",
            "new_time_proposed": args.new_time or None,
            "old_calendar_event_id": old_event_id or None,
        },
    )

    save_candidate(talent_id, cand)

    import talent_db as _tdb
    if _tdb._is_enabled():
        _tdb.mark_reschedule_pending(talent_id, round_num)

    lines = [
        "[{}改期请求已处理]".format(round_label),
        "- talent_id: {}".format(talent_id),
        "- 候选人: {}".format(candidate_name or talent_id),
        "- 原{}时间: {}".format(round_label, old_time),
        "- 改期原因: {}".format(args.reason or "(未说明)"),
    ]
    if args.new_time:
        lines.append("- 候选人建议新时间: {}".format(args.new_time))
    lines.append("- 确认状态: 已撤销")

    if old_event_id:
        delete_pid = _spawn_calendar_delete_bg(old_event_id)
        lines.append("- 飞书日历: 旧事件删除中（后台 PID={}）".format(delete_pid))
    else:
        lines.append("- 飞书日历: 无已有事件")

    if candidate_email:
        email_pid = _send_ack_email(candidate_email, talent_id, round_num,
                                    candidate_name=candidate_name)
        lines.append("- 确认收到邮件: 发送中（后台 PID={}）".format(email_pid))
    else:
        lines.append("- 候选人邮箱未记录，无法发送确认邮件")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
