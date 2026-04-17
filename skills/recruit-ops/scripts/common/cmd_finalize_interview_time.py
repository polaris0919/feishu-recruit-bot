#!/usr/bin/env python3
"""统一收口脚本：老板最终确认面试时间。

v2 重构：用函数调用替代 subprocess，不再 spawn 子进程。

用法：
  python3 common/cmd_finalize_interview_time.py --talent-id t_xxx [--round 1|2]
"""
import argparse
import os
import sys

from core_state import load_candidate


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="老板最终确认面试时间")
    p.add_argument("--talent-id", required=True)
    p.add_argument("--round", type=int, choices=[1, 2], default=0)
    p.add_argument("--time", default="")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    talent_id = args.talent_id.strip()
    override_time = args.time.strip() if args.time else ""

    import talent_db as _tdb_mod
    if not _tdb_mod._is_enabled():
        print("ERROR: DB 未配置", file=sys.stderr)
        return 1

    cand = load_candidate(talent_id)
    if not cand:
        print("ERROR: 未找到候选人 {}".format(talent_id), file=sys.stderr)
        return 1

    _tdb = _tdb_mod

    stage = cand.get("stage", "")
    candidate_name = cand.get("candidate_name") or talent_id

    # 自动检测 round
    round_num = args.round
    if not round_num:
        p1 = _tdb.get_boss_confirm_pending(talent_id, 1)
        p2 = _tdb.get_boss_confirm_pending(talent_id, 2)
        if p1.get("pending") and not p2.get("pending"):
            round_num = 1
        elif p2.get("pending") and not p1.get("pending"):
            round_num = 2
        elif p1.get("pending") and p2.get("pending"):
            print("ERROR: 同时存在一面和二面待确认，请用 --round 指定", file=sys.stderr)
            return 1
        else:
            if stage in ("ROUND1_SCHEDULING", "ROUND1_SCHEDULED"):
                round_num = 1
            elif stage in ("ROUND2_SCHEDULING", "ROUND2_SCHEDULED"):
                round_num = 2
            else:
                print("ERROR: 候选人 {} 当前阶段 {}，无待确认面试".format(talent_id, stage), file=sys.stderr)
                return 1

    round_label = "一面" if round_num == 1 else "二面"
    pending_info = _tdb.get_boss_confirm_pending(talent_id, round_num)

    final_time = override_time
    if not final_time and pending_info.get("time"):
        final_time = pending_info["time"]
    if not final_time:
        final_time = cand.get("round{}_time".format(round_num)) or ""
    if not final_time:
        print("ERROR: 无法确定{}最终时间，请用 --time 指定".format(round_label), file=sys.stderr)
        return 1

    current_time = cand.get("round{}_time".format(round_num)) or ""
    time_changed = final_time != current_time

    lines = [
        "[{}时间最终确认]".format(round_label),
        "- talent_id: {}".format(talent_id),
        "- 候选人: {}".format(candidate_name),
    ]

    # 直接用函数调用替代 subprocess
    from interview.cmd_confirm import main as confirm_main
    from interview.cmd_reschedule import main as reschedule_main
    import io
    from contextlib import redirect_stdout

    if time_changed:
        reschedule_args = [
            "--talent-id", talent_id,
            "--round", str(round_num),
            "--time", final_time,
            "--confirmed",
        ]
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = reschedule_main(reschedule_args)
        out = buf.getvalue().strip()
        if rc != 0:
            print("ERROR: reschedule 失败", file=sys.stderr)
            if out:
                print(out, file=sys.stderr)
            return 1
        lines.append("- 原时间: {}".format(current_time or "（未设定）"))
        lines.append("- 最终确认时间: {}".format(final_time))
        lines.append("- 状态: 已确认")
        if out:
            lines.append("")
            lines.append(out)
    else:
        confirm_args = ["--talent-id", talent_id, "--round", str(round_num)]
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = confirm_main(confirm_args)
        out = buf.getvalue().strip()
        if rc != 0:
            print("ERROR: confirm 失败", file=sys.stderr)
            if out:
                print(out, file=sys.stderr)
            return 1
        lines.append("- 确认时间: {}".format(final_time))
        lines.append("- 状态: 已确认")
        if out:
            lines.append("")
            lines.append(out)

    _tdb.clear_boss_confirm_pending(talent_id, round_num)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
