#!/usr/bin/env python3
"""候选人模糊搜索（talent_id / 邮箱 / 姓名）。"""
import argparse
import json
import sys
from typing import Any, Dict, List

from core_state import load_state

STAGE_LABELS = {
    "NEW": "新建",
    "EXAM_PENDING": "笔试进行中",
    "EXAM_REVIEWED": "笔试已审阅",
    "ROUND2_SCHEDULED": "二面已安排",
    "ROUND2_DONE_PENDING": "二面结束待定",
    "OFFER_HANDOFF": "等待发放 Offer",
    "ROUND1_DONE_REJECT_KEEP": "一面未通过（保留）",
    "ROUND1_DONE_REJECT_DELETE": "一面未通过（移除）",
    "ROUND2_DONE_REJECT_KEEP": "二面未通过（保留）",
    "ROUND2_DONE_REJECT_DELETE": "二面未通过（移除）",
}

ACTIVE_STAGES = {
    "NEW", "EXAM_PENDING", "EXAM_REVIEWED",
    "ROUND2_SCHEDULED", "ROUND2_DONE_PENDING", "OFFER_HANDOFF",
}


def format_candidate(cand):
    stage = cand.get("stage", "NEW")
    return {
        "talent_id":       cand.get("talent_id", ""),
        "candidate_name":  cand.get("candidate_name") or "",
        "stage":           stage,
        "stage_label":     STAGE_LABELS.get(stage, stage),
        "candidate_email": cand.get("candidate_email", ""),
        "round2_time":     cand.get("round2_time") or "",
        "exam_id":         cand.get("exam_id") or "",
    }


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
