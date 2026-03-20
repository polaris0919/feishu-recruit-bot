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

import feishu_notify as _fn
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
            "- 当前阶段: OFFER_HANDOFF（已通知 HR 处理 Offer）",
        ]
        if args.notes:
            lines.insert(4, "- 面试备注: {}".format(args.notes))

        # 通知 HR
        hr_msg = (
            "[Offer 处理通知]\n"
            "候选人 {name}（{talent_id}）已通过二面\n"
            "邮箱：{email}\n"
            "请给该候选人发放offer"
        ).format(
            name=cand.get("candidate_name", talent_id),
            talent_id=talent_id,
            email=cand.get("candidate_email", "未记录"),
        )
        _fn.send_text_to_hr(hr_msg)

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
        if current_stage not in allowed_from:
            print("ERROR: 当前阶段 {} 不允许执行 round2_result=reject_delete。".format(current_stage), file=sys.stderr)
            return 1

        # 从本地 state 中删除
        state.get("candidates", {}).pop(talent_id, None)
        state = normalize_for_save(state)
        save_state(state)

        # 从数据库彻底删除
        try:
            import talent_db as _tdb
            _tdb.delete_talent(talent_id)
        except Exception as e:
            print("⚠ DB 删除失败: {}".format(e), file=sys.stderr)

        lines = [
            "[二面结果已记录]",
            "- talent_id: {}".format(talent_id),
            "- 结果: 未通过（已从人才库彻底删除）",
            "- 后续动作: 候选人记录已清除，不再联系。",
        ]
        if args.notes:
            lines.insert(3, "- 面试备注: {}".format(args.notes))
        print("\n".join(lines))
        return 0

    print("\n".join(lines))
    state = normalize_for_save(state)
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
