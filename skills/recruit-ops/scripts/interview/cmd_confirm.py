#!/usr/bin/env python3
"""
合并后的面试确认脚本：一面/二面统一处理。
用法：
  python3 interview/cmd_confirm.py --talent-id t_xxx --round 1|2 [--auto]
"""

import argparse
import sys
from bg_helpers import spawn_calendar
from core_state import load_candidate


def _spawn_calendar_bg(talent_id, time, round_num, candidate_email, candidate_name=""):
    return spawn_calendar(
        talent_id, time, event_round=round_num,
        candidate_email=candidate_email,
        candidate_name=candidate_name,
        tag="round{}_confirm".format(round_num),
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="标记面试时间已确认（一面/二面通用）")
    p.add_argument("--talent-id", required=True)
    p.add_argument("--round", type=int, required=True, choices=[1, 2], help="面试轮次")
    p.add_argument("--auto", action="store_true", help="超时默认确认")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    talent_id = args.talent_id.strip()
    round_num = args.round
    round_label = "一面" if round_num == 1 else "二面"
    expected_stage = "ROUND1_SCHEDULING" if round_num == 1 else "ROUND2_SCHEDULING"

    import talent_db as _tdb
    if not _tdb._is_enabled():
        print("ERROR: DB 未配置，无法标记确认", file=sys.stderr)
        return 1

    try:
        cand = load_candidate(talent_id)
        if not cand:
            print("ERROR: 未找到候选人 {}".format(talent_id), file=sys.stderr)
            return 1
        if cand.get("stage") != expected_stage:
            print("ERROR: 候选人 {} 当前阶段为 {}，不处于 {}".format(
                talent_id, cand.get("stage"), expected_stage), file=sys.stderr)
            return 1

        _tdb.mark_confirmed(talent_id, round_num, auto=args.auto)

        interview_time = cand.get("round{}_time".format(round_num)) or "（未知）"
        candidate_name = cand.get("candidate_name") or talent_id
        candidate_email = cand.get("candidate_email") or ""
        confirm_type = "超时默认确认（48h无回复）" if args.auto else "候选人主动确认"

        lines = [
            "[{}时间已确认]".format(round_label),
            "- talent_id: {}".format(talent_id),
            "- 候选人: {}".format(candidate_name),
            "- 确认方式: {}".format(confirm_type),
            "- {}时间: {}".format(round_label, interview_time),
        ]
        if round_num == 2:
            lines.append("- 二面形式: 线下面试（统一）")

        if interview_time and interview_time != "（未知）":
            cal_pid = _spawn_calendar_bg(
                talent_id, interview_time, round_num, candidate_email, candidate_name,
            )
            lines.append("- 飞书日历: 已触发后台创建（PID={}），请以实际日历结果/后台日志为准".format(cal_pid))

        print("\n".join(lines))
        return 0

    except Exception as e:
        print("ERROR: {}".format(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
