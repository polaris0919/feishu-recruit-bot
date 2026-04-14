#!/usr/bin/env python3
"""已废弃：二面形式与会议信息不再入库，统一线下面试；线上面试请通过邮件与候选人沟通。"""

import argparse
import os
import sys

_LIB = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="（已废弃）切换二面形式")
    p.add_argument("--talent-id", default="")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    parse_args(argv)
    print(
        "此命令已废弃。\n"
        "系统不再记录二面线上/线下与会议链接；二面统一按线下处理。\n"
        "若需改为线上或改时间，请直接邮件联系候选人，或使用：\n"
        "  python3 interview/cmd_reschedule.py --talent-id <id> --round 2 --time \"...\""
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
