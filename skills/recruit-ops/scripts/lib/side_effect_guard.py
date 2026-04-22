#!/usr/bin/env python3
"""测试环境 / dry-run 下禁用外部副作用。

闸门分两层：
1. RECRUIT_DISABLE_SIDE_EFFECTS=1   ——历史变量，覆盖 SMTP / Feishu / 日历 / 后台邮件。
                                      为了不破坏现有测试，**默认不**禁用 DB 写入。
2. RECRUIT_DISABLE_DB_WRITES=1      ——新增。明确禁用 talent_db._update / upsert 路径。
                                      由 CLI 的 --dry-run 设置，配合 (1) 使用可做到全无副作用。

辅助：调用 `enable_dry_run()` 一次性把两个变量都打开。
"""
import os
import time


SIDE_EFFECTS_DISABLED_ENV = "RECRUIT_DISABLE_SIDE_EFFECTS"
DB_WRITES_DISABLED_ENV = "RECRUIT_DISABLE_DB_WRITES"


def _truthy(value):
    # type: (object) -> bool
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def side_effects_disabled():
    # type: () -> bool
    return _truthy(os.environ.get(SIDE_EFFECTS_DISABLED_ENV))


def db_writes_disabled():
    # type: () -> bool
    return _truthy(os.environ.get(DB_WRITES_DISABLED_ENV))


def enable_dry_run():
    # type: () -> None
    """打开全部副作用闸门。CLI 的 --dry-run 应统一调用这个函数。"""
    os.environ[SIDE_EFFECTS_DISABLED_ENV] = "1"
    os.environ[DB_WRITES_DISABLED_ENV] = "1"


def fake_pid():
    # type: () -> int
    return int(time.time())
