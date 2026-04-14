#!/usr/bin/env python3

import os, sys
_LIB = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

"""查询候选人当前阶段 + 审计历史摘要。"""
import argparse
import sys
from typing import List, Optional

from core_state import STAGE_LABELS, load_state

ACTION_LABELS = {
    "round1_pass_and_exam_invite_sent": "一面通过，已发笔试邀请",
    "round1_pass_direct_round2_scheduled": "一面通过（直接二面）",
    "round1_result_reject_keep": "一面未通过（保留）",
    "round1_result_reject_delete": "一面未通过（移除）",
    "exam_result_pass_round2_scheduled": "笔试通过，已安排二面",
    "exam_result_reject_keep": "笔试未通过（保留）",
    "exam_result_reject_delete": "笔试未通过（移除）",
    "round1_reschedule_requested": "一面改期申请（待处理）",
    "round2_reschedule_requested": "二面改期申请（待处理）",
    "round2_deferred_until_shanghai": "二面暂缓，待回上海后再约",
    "round1_deferred_until_return": "一面暂缓，待回国后再约",
    "round2_deferred_until_return": "二面暂缓，待回国后再约",
    "wait_return_resumed": "恢复排期，等待重新安排",
    "round2_done_pending": "二面结束，暂保留结论",
    "round2_pass_offer_handoff": "二面通过，进入 Offer 阶段",
    "round2_reject_keep": "二面未通过（保留）",
    "round2_reject_delete": "二面未通过（移除）",
}


def _has_audit_action(audit, action):
    return any(entry.get("action") == action for entry in (audit or []))


def _format_round_status(cand, round_num):
    status = cand.get("round{}_confirm_status".format(round_num)) or "UNSET"
    return {"CONFIRMED": "已确认", "PENDING": "待确认", "UNSET": "未排期"}.get(status, status)


def format_candidate(talent_id, cand, show_audit_lines=5):
    stage = cand.get("stage") or "NEW"
    label = STAGE_LABELS.get(stage, stage)
    name = cand.get("candidate_name") or ""
    email = cand.get("candidate_email") or cand.get("email") or "未记录"
    phone = cand.get("phone") or ""
    wechat = cand.get("wechat") or ""
    exam_id = cand.get("exam_id") or ""
    round1_time = cand.get("round1_time") or ""
    round2_time = cand.get("round2_time") or ""
    position = cand.get("position") or ""
    education = cand.get("education") or ""
    school = cand.get("school") or ""
    work_years = cand.get("work_years")
    source = cand.get("source") or ""
    wait_return_round = cand.get("wait_return_round")

    lines = [
        "【候选人状态查询】",
        "- talent_id: {}".format(talent_id),
        "- 姓名: {}".format(name if name else "未记录"),
        "- 邮箱: {}".format(email),
    ]
    if phone:
        lines.append("- 手机: {}".format(phone))
    if wechat:
        lines.append("- 微信: {}".format(wechat))
    lines.append("- 当前阶段: {} ({})".format(label, stage))
    if stage == "WAIT_RETURN":
        lines.append("- 暂缓轮次: {}".format("一面" if wait_return_round == 1 else "二面" if wait_return_round == 2 else "未记录"))
    if position:
        lines.append("- 岗位: {}".format(position))
    if education or school:
        lines.append("- 学历: {} {}".format(education, school).strip())
    if work_years is not None:
        lines.append("- 工作年限: {}年".format(work_years))
    if source:
        lines.append("- 来源: {}".format(source))
    if exam_id:
        lines.append("- 笔试 ID: {}".format(exam_id))
    audit = cand.get("audit") or []
    if round1_time:
        lines.append("- 一面时间: {}".format(round1_time))
        lines.append("- 一面状态: {}".format(_format_round_status(cand, 1)))
    if round2_time:
        lines.append("- 二面时间: {}".format(round2_time))
        lines.append("- 二面状态: {}".format(_format_round_status(cand, 2)))

    if audit:
        lines.append("")
        lines.append("📋 最近操作记录（最新 {} 条）：".format(min(show_audit_lines, len(audit))))
        for entry in audit[-show_audit_lines:]:
            at = entry.get("at", "")[:16].replace("T", " ")
            action = entry.get("action", "")
            actor = entry.get("actor", "")
            desc = ACTION_LABELS.get(action, action)
            lines.append("  {} | {} | {}".format(at, desc, actor))

    return "\n".join(lines)


def format_all_candidates(state):
    candidates = state.get("candidates") or {}
    if not candidates:
        return "当前人才库为空，暂无候选人记录。"

    lines = ["【所有候选人一览】", "共 {} 位候选人：".format(len(candidates)), ""]
    for tid, cand in sorted(candidates.items()):
        stage = cand.get("stage") or "NEW"
        label = STAGE_LABELS.get(stage, stage)
        name = cand.get("candidate_name") or ""
        email = cand.get("candidate_email") or cand.get("email") or "—"
        name_part = " {}".format(name) if name else ""
        extra_parts = []
        extra = "  [{}]".format(" | ".join(extra_parts)) if extra_parts else ""
        lines.append("- {}{} | {} | {}{}".format(tid, name_part, label, email, extra))

    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="查询候选人招聘状态")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--talent-id", help="查询指定候选人")
    group.add_argument("--all", action="store_true", help="列出所有候选人")
    p.add_argument("--audit-lines", type=int, default=5)
    args = p.parse_args(argv or sys.argv[1:])

    state = load_state()

    if args.all:
        print(format_all_candidates(state))
        return 0

    talent_id = args.talent_id.strip()
    candidates = state.get("candidates") or {}
    if talent_id not in candidates:
        print("未找到 talent_id={} 的候选人记录。".format(talent_id))
        return 1

    cand = candidates[talent_id]
    print(format_candidate(talent_id, cand, show_audit_lines=args.audit_lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
