#!/usr/bin/env python3
"""查询候选人当前阶段 + 审计历史摘要。"""
import argparse
import sys
from typing import List, Optional

from core_state import load_state

STAGE_LABELS = {
    "NEW": "新建",
    "ROUND1_SCHEDULED": "一面已安排",
    "ROUND1_DONE_PASS": "一面通过",
    "ROUND1_DONE_REJECT_KEEP": "一面未通过（保留）",
    "ROUND1_DONE_REJECT_DELETE": "一面未通过（移除）",
    "EXAM_PENDING": "笔试进行中",
    "EXAM_REVIEWED": "笔试已审阅",
    "ROUND2_SCHEDULED": "二面已安排",
    "ROUND2_DONE_PENDING": "二面结束待定",
    "ROUND2_DONE_PASS": "二面通过",
    "ROUND2_DONE_REJECT_KEEP": "二面未通过（保留）",
    "ROUND2_DONE_REJECT_DELETE": "二面未通过（移除）",
    "OFFER_HANDOFF": "等待发放 Offer",
}

ACTION_LABELS = {
    "round1_pass_and_exam_invite_sent": "一面通过，已发笔试邀请",
    "round1_pass_direct_round2_scheduled": "一面通过（直接二面）",
    "round1_result_reject_keep": "一面未通过（保留）",
    "round1_result_reject_delete": "一面未通过（移除）",
    "exam_result_pass_round2_scheduled": "笔试通过，已安排二面",
    "exam_result_reject_keep": "笔试未通过（保留）",
    "exam_result_reject_delete": "笔试未通过（移除）",
    "round2_done_pending": "二面结束，暂保留结论",
    "round2_pass_offer_handoff": "二面通过，进入 Offer 阶段",
    "round2_reject_keep": "二面未通过（保留）",
    "round2_reject_delete": "二面未通过（移除）",
}


def format_candidate(talent_id, cand, show_audit_lines=5):
    stage = cand.get("stage") or "NEW"
    label = STAGE_LABELS.get(stage, stage)
    name = cand.get("candidate_name") or ""
    email = cand.get("candidate_email") or cand.get("email") or "未记录"
    phone = cand.get("phone") or ""
    wechat = cand.get("wechat") or ""
    exam_id = cand.get("exam_id") or ""
    round2_time = cand.get("round2_time") or ""
    round2_interviewer = cand.get("round2_interviewer") or ""
    position = cand.get("position") or ""
    education = cand.get("education") or ""
    school = cand.get("school") or ""
    work_years = cand.get("work_years")
    source = cand.get("source") or ""

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
    if round2_time:
        lines.append("- 二面时间: {}".format(round2_time))
    if round2_interviewer:
        lines.append("- 二面面试官: {}".format(round2_interviewer))

    # 面试评价
    round1_notes = cand.get("round1_notes") or ""
    exam_score = cand.get("exam_score")
    exam_notes = cand.get("exam_notes") or ""
    round2_score = cand.get("round2_score")
    round2_notes = cand.get("round2_notes") or ""
    if round1_notes:
        lines.append("- 一面评价: {}".format(round1_notes))
    if exam_score is not None:
        lines.append("- 笔试评分: {}".format(exam_score))
    if exam_notes:
        lines.append("- 笔试评价: {}".format(exam_notes))
    if round2_score is not None:
        lines.append("- 二面评分: {}".format(round2_score))
    if round2_notes:
        lines.append("- 二面评价: {}".format(round2_notes))

    audit = cand.get("audit") or []
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
        lines.append("- {}{} | {} | {}".format(tid, name_part, label, email))

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
