#!/usr/bin/env python3
"""
标记二面时间已确认。

用法：
  # 候选人主动确认
  python3 cmd_round2_confirm.py --talent-id t_xxx

  # 超过48小时未回复，系统自动默认确认
  python3 cmd_round2_confirm.py --talent-id t_xxx --auto
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="标记二面时间已确认")
    p.add_argument("--talent-id", required=True, help="候选人 talent_id")
    p.add_argument("--auto", action="store_true", help="超时默认确认（48h无回复）")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    talent_id = args.talent_id.strip()

    try:
        import talent_db as _tdb
        if not _tdb._is_enabled():
            print("ERROR: DB 未配置，无法标记确认", file=sys.stderr)
            return 1

        # 验证候选人存在且处于 ROUND2_SCHEDULED 阶段
        db_state = _tdb.load_state_from_db()
        cand = (db_state.get("candidates") or {}).get(talent_id)
        if not cand:
            print("ERROR: 未找到候选人 {}".format(talent_id), file=sys.stderr)
            return 1
        if cand.get("stage") != "ROUND2_SCHEDULED":
            print(
                "ERROR: 候选人 {} 当前阶段为 {}，不处于 ROUND2_SCHEDULED".format(
                    talent_id, cand.get("stage")
                ),
                file=sys.stderr,
            )
            return 1

        _tdb.mark_round2_confirmed(talent_id, auto=args.auto)

        round2_time = cand.get("round2_time", "（未知）")
        candidate_name = cand.get("candidate_name") or talent_id
        confirm_type = "超时默认确认（48h无回复）" if args.auto else "候选人主动确认"

        print(
            "[二面时间已确认]\n"
            "- talent_id: {}\n"
            "- 候选人: {}\n"
            "- 确认方式: {}\n"
            "- 二面时间: {}".format(talent_id, candidate_name, confirm_type, round2_time)
        )
        return 0

    except Exception as e:
        print("ERROR: {}".format(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
