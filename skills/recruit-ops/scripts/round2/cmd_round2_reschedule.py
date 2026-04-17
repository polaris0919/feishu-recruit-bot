#!/usr/bin/env python3
"""兼容入口：转发到 `interview/cmd_reschedule.py --round 2`，默认补 `--confirmed`。"""
import sys
from interview.cmd_reschedule import main as _main

def main(argv=None):
    args = list(argv or sys.argv[1:])
    if "--no-confirm" not in args:
        args.insert(0, "--confirmed")
    return _main(["--round", "2"] + args)

if __name__ == "__main__":
    raise SystemExit(main())
