#!/usr/bin/env python3

"""
处理 /exam_result 命令：
  - result=pass        → 状态推进到 ROUND2_SCHEDULING，自动给候选人发二面通知邮件
  - result=reject_keep → 状态推进到 ROUND1_DONE_REJECT_KEEP（保留人才库）
  - result=reject_delete → 状态推进到 ROUND1_DONE_REJECT_DELETE（移除）
"""
import argparse
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


def send_round2_notification(
    to_email,
    talent_id,
    round2_time,
    company="",
    candidate_name="",
):
    # type: (str, str, str, str, str) -> int
    """在独立 session 后台发送二面通知邮件，返回 PID。（统一线下面试）"""
    company_display = company if company else "公司"
    subject = "【面试通知】笔试通过，邀请参加第二轮面试 - " + company_display

    time_line = round2_time if round2_time else "待定，HR 将另行通知"
    location_lines = [
        "· 面试形式：线下面试",
        "· 面试地点：上海市浦东新区杨高中路丁香国际商业中心西塔21楼致邃投资",
    ]

    body_parts = [
        "您好，{}，".format(candidate_name if candidate_name else ""),
        "",
        "感谢您认真完成笔试！经过评审，您已顺利通过本轮笔试，恭喜！",
        "",
        "我们诚邀您参加第二轮面试，详情如下：",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🗓 面试详情",
        "━━━━━━━━━━━━━━━━━━━━",
        "· 面试时间：" + time_line,
    ]
    body_parts.extend(location_lines)
    body_parts.extend([
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "请您确认是否可以按时参加。",
        "如有时间冲突，请提前回复本邮件告知，我们会及时为您调整。",
        "",
        "再次感谢您对 {} 的关注，期待与您的进一步交流！".format(company_display),
        "",
        company_display + " 招聘团队",
        "",
        "---",
        "TALENT_ID: " + talent_id,
    ])
    body = "\n".join(body_parts)

    return send_bg_email(to_email, subject, body, tag="round2_invite")


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="处理 /exam_result 命令")
    p.add_argument("--talent-id", required=True, help="候选人唯一标识 talent_id")
    p.add_argument(
        "--result",
        required=True,
        choices=["pass", "reject_keep", "reject_delete"],
        help="笔试结果：pass / reject_keep / reject_delete",
    )
    p.add_argument(
        "--round2-time",
        default="",
        help="二面时间（result=pass 时建议填写，例如 2026-03-20 14:00）",
    )
    p.add_argument(
        "--notes",
        default="",
        help="备注（写入审计日志，不单独落库）",
    )
    p.add_argument(
        "--actor",
        default="system",
        help="执行人（用于审计，建议传入 HR 的飞书 user_id）",
    )
    return p.parse_args(argv)


def main(argv=None):
    # type: (Optional[List[str]]) -> int
    args = parse_args(argv or sys.argv[1:])
    talent_id = args.talent_id.strip()
    result = args.result

    cand = load_candidate(talent_id)
    if not cand:
        print("ERROR: 未找到候选人 {}".format(talent_id), file=sys.stderr)
        return 1

    current_stage = cand.get("stage") or "NEW"

    if result == "pass":
        if not (args.round2_time or "").strip():
            print("ERROR: result=pass 时必须提供 --round2-time，禁止复用旧的二面时间。", file=sys.stderr)
            return 1
        allowed_from = {"EXAM_SENT", "EXAM_REVIEWED"}
        ok = ensure_stage_transition(cand, allowed_from, "ROUND2_SCHEDULING")
        if not ok:
            print(
                f"ERROR: 当前阶段 {current_stage} 不允许执行 exam_result=pass。"
                f"（需要处于 EXAM_SENT / EXAM_REVIEWED 阶段）",
                file=sys.stderr,
            )
            return 1

        if args.round2_time:
            cand["round2_time"] = args.round2_time
            cand["round2_confirm_status"] = "PENDING"
            cand["round2_invite_sent_at"] = datetime.now().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S+08:00")
            cand["round2_calendar_event_id"] = None
            cand["wait_return_round"] = None

        candidate_email = cand.get("candidate_email", "")

        # 自动给候选人后台发二面通知邮件（不阻塞主流程）
        email_pid = None
        if candidate_email:
            email_pid = send_round2_notification(
                to_email=candidate_email,
                talent_id=talent_id,
                round2_time=args.round2_time,
                company="致邃投资",
                candidate_name=cand.get("candidate_name", ""),
            )
        else:
            print("WARNING: 候选人邮箱未记录，无法自动发送二面通知邮件。", file=sys.stderr)

        append_audit(
            cand,
            actor=args.actor,
            action="exam_result_pass_round2_scheduled",
            payload={
                "round2_time": args.round2_time,
                "notification_sent_to": candidate_email,
                "notes": args.notes,
            },
        )

        lines = [
            "[笔试结果已记录]",
            "- talent_id: " + talent_id,
            "- 结果: 通过（进入二面阶段）",
            "- 当前阶段: ROUND2_SCHEDULING",
        ]
        if args.notes:
            lines.insert(3, "- 备注: " + args.notes)
        if args.round2_time:
            lines.append("- 二面时间（待候选人确认）: " + args.round2_time)
        lines.append("- 二面形式: 线下面试（统一）")
        lines.append("- 候选人邮箱: " + (candidate_email or "未记录"))
        if candidate_email and email_pid:
            lines.append("- 二面通知邮件: 发送中（后台 PID={}）".format(email_pid))

        save_candidate(talent_id, cand)
        import talent_db as _tdb
        if _tdb._is_enabled():
            _tdb.clear_round_followup_fields(talent_id, 2)

        # 方案一：仅记录候选人侧时间，等候选人最终确认后再落老板日历
        if args.round2_time:
            lines.append("- 老板飞书日历: 暂不创建，待候选人确认后再落盘")
        else:
            lines.append("- 老板飞书日历: 未提供二面时间，跳过")

        print("\n".join(lines))
        return 0  # 提前 return，避免末尾重复 save_state

    elif result == "reject_keep":
        allowed_from = {"EXAM_SENT", "EXAM_REVIEWED"}
        ok = ensure_stage_transition(cand, allowed_from, "ROUND1_DONE_REJECT_KEEP")
        if not ok:
            print(
                f"ERROR: 当前阶段 {current_stage} 不允许执行 exam_result=reject_keep。",
                file=sys.stderr,
            )
            return 1

        append_audit(
            cand,
            actor=args.actor,
            action="exam_result_reject_keep",
            payload={"notes": args.notes},
        )
        save_candidate(talent_id, cand)
        note_line = "\n- 备注: " + args.notes if args.notes else ""
        print(
            f"[笔试结果已记录]\n"
            f"- talent_id: {talent_id}\n"
            f"- 结果: 未通过（保留人才库）"
            f"{note_line}\n"
            f"- 候选人邮箱: {cand.get('candidate_email', '未记录')}\n"
            f"- 后续动作: 候选人已保留在人才库，可在未来合适职位时重新激活。"
        )
        return 0

    else:  # reject_delete
        allowed_from = {"EXAM_SENT", "EXAM_REVIEWED"}
        if current_stage not in allowed_from:
            print(
                "ERROR: 当前阶段 {} 不允许执行 exam_result=reject_delete。".format(current_stage),
                file=sys.stderr,
            )
            return 1

        import talent_db as _tdb
        if _tdb._is_enabled():
            try:
                _tdb.delete_talent(talent_id)
            except Exception as e:
                print("⚠ DB 删除失败: {}".format(e), file=sys.stderr)

        notes_line = ""
        if args.notes:
            notes_line = "\n- 评价: {}".format(args.notes.strip())
        print(
            "[笔试结果已记录]\n"
            "- talent_id: {}\n"
            "- 结果: 未通过（已从人才库彻底删除）\n"
            "- 候选人记录已清除，不再联系。{}".format(talent_id, notes_line)
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
