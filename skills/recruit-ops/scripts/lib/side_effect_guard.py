#!/usr/bin/env python3
"""测试环境下禁用外部副作用。"""
import os
import time


SIDE_EFFECTS_DISABLED_ENV = "RECRUIT_DISABLE_SIDE_EFFECTS"


def side_effects_disabled():
    # type: () -> bool
    value = (os.environ.get(SIDE_EFFECTS_DISABLED_ENV) or "").strip().lower()
    return value in ("1", "true", "yes", "on")


def fake_pid():
    # type: () -> int
    return int(time.time())
