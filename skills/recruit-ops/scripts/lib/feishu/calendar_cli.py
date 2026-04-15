#!/usr/bin/env python3
"""飞书日历 CLI 入口，供 bg_helpers 子进程使用。"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

from feishu import (
    create_interview_event,
    delete_calendar_event_by_id as delete_calendar_event_standalone,
)
from side_effect_guard import side_effects_disabled


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="创建飞书日历面试事件")
    p.add_argument("--talent-id", required=True)
    p.add_argument("--round2-time", default="", help="面试时间")
    p.add_argument("--candidate-email", default="")
    p.add_argument("--candidate-name", default="")
    p.add_argument("--event-round", default="2")
    p.add_argument("--old-event-id", default="")
    p.add_argument("--delete-event-id", default="")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    if side_effects_disabled():
        print("测试模式：已跳过日历操作")
        return 0
    if args.delete_event_id:
        ok = delete_calendar_event_standalone(args.delete_event_id)
        print("删除日历事件 {}: {}".format(args.delete_event_id, "成功" if ok else "失败"))
        return 0 if ok else 1

    interview_time = args.round2_time.strip()
    if not interview_time:
        print("ERROR: --round2-time 不能为空", file=sys.stderr)
        return 1
    try:
        round_num = int(args.event_round)
        msg = create_interview_event(
            talent_id=args.talent_id,
            interview_time=interview_time,
            round_num=round_num,
            candidate_email=args.candidate_email,
            candidate_name=args.candidate_name,
            old_event_id=args.old_event_id,
        )
        print(msg)
        return 0
    except Exception as e:
        print("ERROR: " + str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
