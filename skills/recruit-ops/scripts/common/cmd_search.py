#!/usr/bin/env python3

"""候选人模糊搜索（talent_id / 邮箱 / 姓名）。"""
import argparse
import json
import sys
from typing import Any, Dict, List

from core_state import STAGE_LABELS, load_state

ACTIVE_STAGES = {
    "NEW", "ROUND1_SCHEDULING", "ROUND1_SCHEDULED", "EXAM_SENT", "EXAM_REVIEWED",
    "WAIT_RETURN", "ROUND2_SCHEDULING", "ROUND2_SCHEDULED", "ROUND2_DONE_PENDING", "OFFER_HANDOFF",
}


def _interview_status(cand, round_num):
    status = cand.get("round{}_confirm_status".format(round_num)) or "UNSET"
    if status == "UNSET":
        return ""
    return "confirmed" if status == "CONFIRMED" else "pending_confirmation"


def _interview_time(cand, round_num):
    return cand.get("round{}_time".format(round_num)) or ""


def _next_interview(cand):
    r2_time = _interview_time(cand, 2)
    r1_time = _interview_time(cand, 1)
    if r2_time:
        return {
            "next_interview_round": 2,
            "next_interview_time": r2_time,
            "next_interview_confirmed": cand.get("round2_confirm_status") == "CONFIRMED",
        }
    if r1_time:
        return {
            "next_interview_round": 1,
            "next_interview_time": r1_time,
            "next_interview_confirmed": cand.get("round1_confirm_status") == "CONFIRMED",
        }
    return {
        "next_interview_round": None,
        "next_interview_time": "",
        "next_interview_confirmed": False,
    }


def format_candidate(cand):
    stage = cand.get("stage", "NEW")
    data = {
        "talent_id":       cand.get("talent_id", ""),
        "candidate_name":  cand.get("candidate_name") or "",
        "stage":           stage,
        "stage_label":     STAGE_LABELS.get(stage, stage),
        "candidate_email": cand.get("candidate_email", ""),
        "wait_return_round": cand.get("wait_return_round"),
        "round1_time":          _interview_time(cand, 1),
        "round1_confirm_status": cand.get("round1_confirm_status") or "UNSET",
        "round1_status":         _interview_status(cand, 1),
        "round2_time":           _interview_time(cand, 2),
        "round2_confirm_status": cand.get("round2_confirm_status") or "UNSET",
        "round2_status":         _interview_status(cand, 2),
        "exam_id":         cand.get("exam_id") or "",
    }
    data.update(_next_interview(cand))
    return data


def search(query, stage_filter=None):
    state = load_state()
    q = query.strip().lower()
    results = []
    for tid, cand in state.get("candidates", {}).items():
        stage = cand.get("stage", "NEW")
        if stage_filter and stage != stage_filter:
            continue
        email = (cand.get("candidate_email") or "").lower()
        name = (cand.get("candidate_name") or "").lower()
        if q in tid.lower() or q in email or q in name:
            results.append(format_candidate(cand))
    return results


def list_active():
    state = load_state()
    return [
        format_candidate(cand)
        for cand in state.get("candidates", {}).values()
        if cand.get("stage", "NEW") in ACTIVE_STAGES
    ]


def main(argv=None):
    p = argparse.ArgumentParser(description="候选人模糊搜索")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--query", "-q", help="搜索关键词（talent_id / 邮箱 / 姓名）")
    group.add_argument("--all-active", action="store_true", help="列出所有进行中的候选人")
    p.add_argument("--stage", default="", help="按阶段过滤（可选）")
    args = p.parse_args(argv or sys.argv[1:])

    results = list_active() if args.all_active else search(args.query, args.stage or None)

    if not results:
        print(json.dumps({"found": 0, "candidates": [], "message": "未找到匹配的候选人"}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({"found": len(results), "candidates": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
