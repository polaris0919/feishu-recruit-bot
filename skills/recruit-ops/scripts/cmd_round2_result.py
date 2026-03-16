#!/usr/bin/env python3
"""
处理 /round2_result 命令：
  - result=pending       → ROUND2_DONE_PENDING（30分钟后自动飞书催问）
  - result=pass          → OFFER_HANDOFF
  - result=reject_keep   → ROUND2_DONE_REJECT_KEEP
  - result=reject_delete → ROUND2_DONE_REJECT_DELETE
"""
import argparse
import sys
from datetime import datetime
from typing import List, Optional

from core_state import (
    append_audit,
    ensure_stage_transition,
    get_candidate,
    load_state,
    normalize_for_save,
    save_state,
)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="处理 /round2_result 命令")
    p.add_argument("--talent-id", required=True)
    p.add_argument(
        "--result", required=True,
        choices=["pending", "pass", "reject_keep", "reject_delete"],
    )
    p.add_argument("--notes", default="", help="备注（面试官评价）")
    p.add_argument("--actor", default="system")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    talent_id = args.talent_id.strip()
    result = args.result

    state = load_state()
    cand = get_candidate(state, talent_id)
    current_stage = cand.get("stage") or "NEW"
    allowed_from = {"ROUND2_SCHEDULED", "ROUND2_DONE_PENDING", "ROUND2_DONE_PASS"}

    if result == "pending":
        ok = ensure_stage_transition(cand, {"ROUND2_SCHEDULED", "ROUND2_DONE_PENDING"}, "ROUND2_DONE_PENDING")
        if not ok:
            print("ERROR: 当前阶段 {} 不允许执行 round2_result=pending。".format(current_stage), file=sys.stderr)
            return 1

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        cand["round2_time"] = now_str
        cand["interview_reminded_at"] = None
        if args.notes:
            cand["round2_notes"] = args.notes.strip()

        append_audit(cand, actor=args.actor, action="round2_done_pending",
                     payload={"notes": args.notes})
        lines = [
            "[二面已结束，暂保留结论]",
            "- talent_id: {}".format(talent_id),
            "- 状态: ROUND2_DONE_PENDING（30分钟后系统将自动发飞书提醒）",
            "- 二面完成时间: {}".format(now_str),
        ]
        if args.notes:
            lines.append("- 备注: {}".format(args.notes))

    elif result == "pass":
        ok = ensure_stage_transition(cand, allowed_from, "OFFER_HANDOFF")
        if not ok:
            print("ERROR: 当前阶段 {} 不允许执行 round2_result=pass。（需要处于 ROUND2_SCHEDULED 阶段）".format(current_stage), file=sys.stderr)
            return 1

        if args.notes:
            cand["round2_notes"] = args.notes.strip()
        append_audit(cand, actor=args.actor, action="round2_pass_offer_handoff",
                     payload={"notes": args.notes})
        lines = [
            "[二面结果已记录]",
            "- talent_id: {}".format(talent_id),
            "- 结果: 通过 🎉",
            "- 候选人邮箱: {}".format(cand.get("candidate_email", "未记录")),
            "- 当前阶段: OFFER_HANDOFF（等待发放 Offer）",
            "",
            "📋 后续动作（HR 请手动完成）：",
            "  1. 与业务负责人确认 Offer 薪资和职级",
            "  2. 通过 HR 系统发送正式 Offer",
            "  3. 候选人确认后更新人才库状态",
        ]
        if args.notes:
            lines.insert(4, "- 面试备注: {}".format(args.notes))

    elif result == "reject_keep":
        ok = ensure_stage_transition(cand, allowed_from, "ROUND2_DONE_REJECT_KEEP")
        if not ok:
            print("ERROR: 当前阶段 {} 不允许执行 round2_result=reject_keep。".format(current_stage), file=sys.stderr)
            return 1

        if args.notes:
            cand["round2_notes"] = args.notes.strip()
        append_audit(cand, actor=args.actor, action="round2_reject_keep",
                     payload={"notes": args.notes})
        lines = [
            "[二面结果已记录]",
            "- talent_id: {}".format(talent_id),
            "- 结果: 未通过（保留人才库）",
            "- 候选人邮箱: {}".format(cand.get("candidate_email", "未记录")),
            "- 后续动作: 候选人已保留在人才库，可在未来合适职位时重新激活。",
        ]
        if args.notes:
            lines.insert(4, "- 面试备注: {}".format(args.notes))

    else:  # reject_delete
        ok = ensure_stage_transition(cand, allowed_from, "ROUND2_DONE_REJECT_DELETE")
        if not ok:
            print("ERROR: 当前阶段 {} 不允许执行 round2_result=reject_delete。".format(current_stage), file=sys.stderr)
            return 1

        if args.notes:
            cand["round2_notes"] = args.notes.strip()
        append_audit(cand, actor=args.actor, action="round2_reject_delete",
                     payload={"notes": args.notes})
        lines = [
            "[二面结果已记录]",
            "- talent_id: {}".format(talent_id),
            "- 结果: 未通过（从人才库移除）",
            "- 后续动作: 候选人已标记为不再联系，人才库记录将清除。",
        ]
        if args.notes:
            lines.insert(3, "- 面试备注: {}".format(args.notes))

    print("\n".join(lines))
    state = normalize_for_save(state)
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
