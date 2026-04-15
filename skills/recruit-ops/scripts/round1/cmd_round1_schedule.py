#!/usr/bin/env python3

"""
安排一面时间：
  1. 发一面邀请邮件给候选人（确认时间）
  2. 将候选人阶段更新为 ROUND1_SCHEDULING
  3. 写 round1_invite_sent_at 到 DB（供48h超时判断）

日历事件不在此步创建——等候选人确认（cmd_round1_confirm.py）后才推日历给老板，
与二面流程保持一致。

用法：
  python3 cmd_round1_schedule.py \
    --talent-id t_xxx \
    --time "2026-04-10 14:00" \
    [--interviewer 老板]
"""
import argparse
import os
import sys
from datetime import datetime
from typing import List, Optional

from bg_helpers import send_bg_email
from core_state import (
    append_audit,
    ensure_stage_transition,
    load_candidate,
    save_candidate,
)


def _send_round1_invite_email(to_email, talent_id, round1_time, candidate_name="", position=""):
    # type: (str, str, str, str, str) -> int
    """后台发一面邀请邮件，返回 PID。"""
    company = "致邃投资"
    pos_line = "（{}）".format(position) if position else ""
    subject = "【面试邀请】{} - 一面邀请{}".format(company, pos_line)

    body_parts = [
        "您好，{}，".format(candidate_name if candidate_name else ""),
        "",
        "感谢您对 {} 的关注！非常高兴通知您，您的简历已通过初步筛选。".format(company),
        "",
        "我们诚邀您参加一面，详情如下：",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🗓 面试详情",
        "━━━━━━━━━━━━━━━━━━━━",
        "· 面试时间：" + round1_time,
        "· 面试形式：线下面试",
        "· 面试地点：上海市浦东新区杨高中路丁香国际商业中心西塔21楼致邃投资",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "请您确认是否可以按时参加。",
        "如有时间冲突，请提前回复本邮件告知，我们会及时为您调整。",
        "",
        "期待与您的交流！",
        "",
        company + " 招聘团队",
        "",
        "---",
        "TALENT_ID: " + talent_id,
    ]
    body = "\n".join(body_parts)

    return send_bg_email(to_email, subject, body, tag="round1_invite")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="安排一面时间并发邀请邮件")
    p.add_argument("--talent-id",   required=True, help="候选人 talent_id")
    p.add_argument("--time",   required=True, help="一面时间，例如 '2026-04-10 14:00'")
    p.add_argument("--actor", default="boss", help="操作人（用于审计）")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    # type: (Optional[List[str]]) -> int
    args = parse_args(argv)
    talent_id = args.talent_id.strip()
    round1_time = args.time.strip()

    cand = load_candidate(talent_id)
    if not cand:
        print("ERROR: 未找到候选人 {}".format(talent_id), file=sys.stderr)
        return 1
    current_stage = cand.get("stage") or "NEW"

    # 允许从 NEW 或 ROUND1_SCHEDULING（重发）进入
    allowed_from = {"NEW", "ROUND1_SCHEDULING", "ROUND1_SCHEDULED"}
    ok = ensure_stage_transition(cand, allowed_from, "ROUND1_SCHEDULING")
    if not ok:
        print(
            "ERROR: 候选人 {} 当前阶段为 {}，无法安排一面。"
            "（需要处于 NEW / ROUND1_SCHEDULING 阶段）".format(talent_id, current_stage),
            file=sys.stderr,
        )
        return 1

    cand["round1_time"] = round1_time
    cand["round1_confirm_status"] = "PENDING"
    cand["round1_invite_sent_at"] = datetime.now().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    cand["round1_calendar_event_id"] = None
    cand["wait_return_round"] = None

    candidate_email = cand.get("candidate_email", "")
    candidate_name = cand.get("candidate_name", "")
    position = cand.get("position", "")

    append_audit(
        cand,
        actor=args.actor,
        action="round1_scheduled",
        payload={
            "round1_time": round1_time,
            "notification_sent_to": candidate_email,
        },
    )

    save_candidate(talent_id, cand)
    import talent_db as _tdb
    if _tdb._is_enabled():
        _tdb.clear_round_followup_fields(talent_id, 1)

    lines = [
        "[一面已安排]",
        "- talent_id: " + talent_id,
        "- 候选人: {}".format(candidate_name or talent_id),
        "- 一面时间: " + round1_time,
        "- 当前阶段: ROUND1_SCHEDULING（等待候选人确认）",
        "- 候选人邮箱: " + (candidate_email or "未记录"),
    ]

    # 后台发邀请邮件
    if candidate_email:
        email_pid = _send_round1_invite_email(
            candidate_email, talent_id, round1_time,
            candidate_name, position
        )
        lines.append("- 邀请邮件: 发送中（后台 PID={}）".format(email_pid))
    else:
        lines.append("- 邀请邮件: 候选人邮箱未记录，请手动联系候选人")

    lines.append("- 飞书日历: 待候选人确认后自动创建")
    lines.append("")
    lines.append("候选人回复后，系统将自动扫描邮件分析意图，确认后创建日历。")
    lines.append("手动触发扫描：python3 exam/daily_exam_review.py")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
