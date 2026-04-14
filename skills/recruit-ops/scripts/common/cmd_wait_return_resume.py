#!/usr/bin/env python3

import os, sys
_LIB = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

"""恢复 WAIT_RETURN 候选人到对应轮次的排期阶段。"""
import argparse
import sys

from core_state import append_audit, load_candidate, save_candidate


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="恢复 WAIT_RETURN 候选人到对应轮次排期阶段")
    p.add_argument("--talent-id", required=True, help="候选人 talent_id")
    p.add_argument("--actor", default="system", help="操作人（用于审计）")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    talent_id = args.talent_id.strip()

    cand = load_candidate(talent_id)
    if not cand:
        print("ERROR: 未找到候选人 {}".format(talent_id), file=sys.stderr)
        return 1
    if cand.get("stage") != "WAIT_RETURN":
        print("ERROR: 候选人 {} 当前阶段为 {}，不处于 WAIT_RETURN".format(
            talent_id, cand.get("stage") or "NEW"), file=sys.stderr)
        return 1

    round_num = cand.get("wait_return_round")
    if round_num not in (1, 2):
        print("ERROR: 候选人 {} 缺少有效的 wait_return_round".format(talent_id), file=sys.stderr)
        return 1

    target_stage = "ROUND1_SCHEDULING" if round_num == 1 else "ROUND2_SCHEDULING"
    round_label = "一面" if round_num == 1 else "二面"
    candidate_name = cand.get("candidate_name") or talent_id

    import talent_db as _tdb
    if _tdb._is_enabled():
        resumed_round = _tdb.resume_wait_return(talent_id)
        if resumed_round != round_num:
            print("ERROR: 恢复 WAIT_RETURN 失败", file=sys.stderr)
            return 1

    cand["stage"] = target_stage
    cand["wait_return_round"] = None
    append_audit(
        cand,
        actor=args.actor,
        action="wait_return_resumed",
        payload={"round": round_num, "target_stage": target_stage},
    )
    save_candidate(talent_id, cand)

    lines = [
        "[候选人已恢复排期]",
        "- talent_id: {}".format(talent_id),
        "- 候选人: {}".format(candidate_name),
        "- 恢复轮次: {}".format(round_label),
        "- 当前阶段: {}".format(target_stage),
        "- 后续动作: 请重新安排{}时间".format(round_label),
    ]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
