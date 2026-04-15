#!/usr/bin/env python3
"""兼容入口：转发到 `interview/cmd_confirm.py --round 2`。"""
import os, sys
_SCRIPTS = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_LIB = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
if _SCRIPTS not in sys.path:
    sys.path.append(_SCRIPTS)
from interview.cmd_confirm import main as _main

def main(argv=None):
    args = argv or sys.argv[1:]
    return _main(["--round", "2"] + list(args))

if __name__ == "__main__":
    raise SystemExit(main())
