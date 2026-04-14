#!/usr/bin/env python3
"""
合并后的面试改期脚本：一面/二面统一处理（统一线下面试）。
用法：
  python3 interview/cmd_reschedule.py --talent-id t_xxx --round 1|2 --time "YYYY-MM-DD HH:MM" [--confirmed]
"""
import os, sys
_LIB = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import argparse
from bg_helpers import send_bg_email, spawn_calendar, delete_calendar
from core_state import append_audit, load_candidate, save_candidate


def _send_reschedule_email(to_email, talent_id, new_time, round_num,
                           candidate_name=""):
    round_label = "第一轮" if round_num == 1 else "第二轮"
    subject = "【面试通知】{}面试时间更新 - 致邃投资".format(round_label)
    detail_lines = [
        "· 新的面试时间：" + new_time,
        "· 面试形式：线下面试",
        "· 面试地点：上海市浦东新区杨高中路丁香国际商业中心西塔21楼致邃投资",
    ]

    body_parts = [
        "您好，{}，".format(candidate_name or ""),
        "", "非常感谢您的耐心等待！",
        "", "我们已根据双方时间调整，重新确认了{}面试的时间：".format(round_label),
        "", "━━━━━━━━━━━━━━━━━━━━", "🗓 更新后的面试详情", "━━━━━━━━━━━━━━━━━━━━",
    ] + detail_lines + [
        "", "请确认您是否能够按时参加此新时间。",
        "如有时间冲突，请尽快回复本邮件说明。",
        "", "期待与您的进一步交流！", "", "致邃投资 招聘团队",
        "", "---", "TALENT_ID: " + talent_id,
    ]
    return send_bg_email(to_email, subject, "\n".join(body_parts),
                         tag="round{}_reschedule".format(round_num))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="重新约定面试时间（一面/二面通用）")
    p.add_argument("--talent-id", required=True)
    p.add_argument("--round", type=int, required=True, choices=[1, 2])
    p.add_argument("--time", required=True, help="新的面试时间")
    p.add_argument("--confirmed", action="store_true", default=False,
                   help="老板明确最终确认该时间")
    p.add_argument("--no-confirm", dest="confirmed", action="store_false")
    p.add_argument("--actor", default="boss")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    talent_id = args.talent_id.strip()
    new_time = args.time.strip()
    round_num = args.round
    round_label = "一面" if round_num == 1 else "二面"
    prefix = "round{}".format(round_num)

    cand = load_candidate(talent_id)
    if not cand:
        print("ERROR: 未找到候选人 {}".format(talent_id), file=sys.stderr)
        return 1
    current_stage = cand.get("stage") or "NEW"

    allowed = (
        {"ROUND1_SCHEDULING", "ROUND1_SCHEDULED"}
        if round_num == 1 else
        {"ROUND2_SCHEDULING", "ROUND2_SCHEDULED"}
    )
    if current_stage not in allowed:
        print("ERROR: 候选人 {} 当前阶段为 {}，无法改期。".format(talent_id, current_stage), file=sys.stderr)
        return 1

    candidate_email = cand.get("candidate_email", "")
    candidate_name = cand.get("candidate_name", "")
    old_time = cand.get("{}_time".format(prefix)) or "（未记录）"
    old_event_id = (cand.get("{}_calendar_event_id".format(prefix)) or "").strip()

    cand["{}_time".format(prefix)] = new_time
    cand["{}_confirm_status".format(prefix)] = "CONFIRMED" if args.confirmed else "PENDING"
    if round_num == 1:
        new_stage = "ROUND1_SCHEDULED" if args.confirmed else "ROUND1_SCHEDULING"
    else:
        new_stage = "ROUND2_SCHEDULED" if args.confirmed else "ROUND2_SCHEDULING"
    cand["stage"] = new_stage

    action = "{}_rescheduled{}".format(prefix, "_confirmed" if args.confirmed else "")
    append_audit(cand, actor=args.actor, action=action, payload={
        "old_time": old_time, "new_time": new_time,
        "old_calendar_event_id": old_event_id, "confirmed": args.confirmed,
    })

    save_candidate(talent_id, cand)

    import talent_db as _tdb
    if _tdb._is_enabled():
        if round_num == 2:
            _tdb.clear_calendar_event_id(talent_id, 2)
        _tdb.save_invite_info(talent_id, round_num)
        if args.confirmed:
            _tdb.mark_confirmed(talent_id, round_num)

    lines = [
        "[{}重新约时间]".format(round_label),
        "- talent_id: {}".format(talent_id),
        "- 候选人: {}".format(candidate_name or talent_id),
        "- 旧时间: {}".format(old_time),
        "- 新时间: {}".format(new_time),
        "- 候选人邮箱: {}".format(candidate_email or "未记录"),
        "- 状态: {}".format(
            "✅ 已直接确认，无需等候选人再次回复" if args.confirmed
            else "⏳ 等待候选人确认"
        ),
        "- 面试形式: 线下面试（统一）",
    ]

    if candidate_email:
        email_pid = _send_reschedule_email(
            candidate_email, talent_id, new_time, round_num,
            candidate_name,
        )
        lines.append("- {}发送中（后台 PID={}）".format(
            "时间更新通知邮件: " if args.confirmed else "新邀请邮件: ", email_pid))

    if old_event_id:
        delete_calendar(old_event_id, tag="round{}_reschedule_delete".format(round_num))
        lines.append("- 老板飞书日历: 旧事件删除中")

    if args.confirmed:
        cal_pid = spawn_calendar(
            talent_id, new_time, event_round=round_num,
            candidate_email=candidate_email,
            candidate_name=candidate_name,
            tag="round{}_reschedule".format(round_num),
        )
        lines.append("- 老板飞书日历: 已按确认时间创建新事件（后台 PID={}）".format(cal_pid))
    else:
        lines.append("- 老板飞书日历: 暂不创建，待候选人确认后再落盘")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
