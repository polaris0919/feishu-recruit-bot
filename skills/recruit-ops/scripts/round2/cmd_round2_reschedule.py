#!/usr/bin/env python3
"""向后兼容包装：转发到 interview/cmd_reschedule.py --round 2"""
import os, sys
_LIB = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))
from interview.cmd_reschedule import main as _main

def main(argv=None):
    args = list(argv or sys.argv[1:])
    if "--no-confirm" not in args:
        args.insert(0, "--confirmed")
    return _main(["--round", "2"] + args)

if __name__ == "__main__":
    raise SystemExit(main())
