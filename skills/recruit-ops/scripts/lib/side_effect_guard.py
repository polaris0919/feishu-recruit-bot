#!/usr/bin/env python3
"""测试环境 / dry-run 下禁用外部副作用。

A2 (v3.8.7): 引入主开关 RECRUIT_DRY_RUN, 一个变量同时关 4 闸:
  - 外部副作用（SMTP / Feishu / 日历 / bg 邮件）
  - DB 写入（_update / upsert / set_email_*）
  - DB 整体可用性（db_enabled() 返回 False, 测试 / CI 时彻底跳过连接）
  - self_verify 飞书告警推送（cli_wrapper._push_alert）

兼容性约定:
  老 4 个 env vars 仍生效, 取 OR 语义。本轮发版起在 SKILL/README 标
  deprecated, 下一个 release 周期（v3.9 / v4.0）真删, 详见 CHANGELOG。

旧变量列表:
  RECRUIT_DISABLE_SIDE_EFFECTS    SMTP / Feishu / 日历
  RECRUIT_DISABLE_DB_WRITES       talent_db._update / upsert
  RECRUIT_DISABLE_DB              整个 DB 连接闸（在 lib/config.db_enabled）
  RECRUIT_SUPPRESS_SELF_VERIFY_ALERT  飞书 self_verify 告警（在 cli_wrapper）

辅助: enable_dry_run() 给 CLI --dry-run 统一调用; 现在它只设新主开关即可。
"""
import os
import time


SIDE_EFFECTS_DISABLED_ENV = "RECRUIT_DISABLE_SIDE_EFFECTS"
DB_WRITES_DISABLED_ENV = "RECRUIT_DISABLE_DB_WRITES"
DRY_RUN_ENV = "RECRUIT_DRY_RUN"  # A2 (v3.8.7): 主开关


def _truthy(value):
    # type: (object) -> bool
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def dry_run_master():
    # type: () -> bool
    """主开关。任何模块若想"一行 if 关掉全部副作用", 用本函数。"""
    return _truthy(os.environ.get(DRY_RUN_ENV))


def side_effects_disabled():
    # type: () -> bool
    return dry_run_master() or _truthy(os.environ.get(SIDE_EFFECTS_DISABLED_ENV))


def db_writes_disabled():
    # type: () -> bool
    return dry_run_master() or _truthy(os.environ.get(DB_WRITES_DISABLED_ENV))


def enable_dry_run():
    # type: () -> None
    """打开全部副作用闸门。CLI 的 --dry-run 应统一调用这个函数。

    A2 (v3.8.7+): 新代码只设主开关 RECRUIT_DRY_RUN; 旧变量自动 OR-in 不
    需要这里显式 export, 但为兼容那些直接读旧 env 而不走本模块函数的
    第三方/历史代码, 我们仍然把 4 个都一起设上, 行为完全等价。
    """
    os.environ[DRY_RUN_ENV] = "1"
    os.environ[SIDE_EFFECTS_DISABLED_ENV] = "1"
    os.environ[DB_WRITES_DISABLED_ENV] = "1"
    os.environ["RECRUIT_DISABLE_DB"] = "1"
    os.environ["RECRUIT_SUPPRESS_SELF_VERIFY_ALERT"] = "1"


def fake_pid():
    # type: () -> int
    return int(time.time())
