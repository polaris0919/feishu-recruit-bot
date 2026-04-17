#!/usr/bin/env python3
"""兼容入口：转发到 `interview/cmd_result.py --round 2`。"""
import sys
from interview.cmd_result import main as _main

def main(argv=None):
    args = argv or sys.argv[1:]
    return _main(["--round", "2"] + list(args))

if __name__ == "__main__":
    raise SystemExit(main())
