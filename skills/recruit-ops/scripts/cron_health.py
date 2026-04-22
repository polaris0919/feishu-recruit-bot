#!/usr/bin/env python3
"""Cron 健康检查 — 独立脚本，不依赖 cron_runner 自身。

用法：
    python3 scripts/cron_health.py                # 打印状态
    python3 scripts/cron_health.py --alert        # 状态异常时推送 Feishu 告警
    python3 scripts/cron_health.py --threshold 26 # 自定义异常阈值（小时）

退出码：
    0 — 健康
    1 — 心跳过期 / 缺失
    2 — 内部错误（找不到心跳文件路径）
"""
from __future__ import print_function

import argparse
import sys
from datetime import datetime
from pathlib import Path


_HEARTBEAT_PATH = Path("<RECRUIT_WORKSPACE>/data/.cron_heartbeat")


def _read_last():
    # type: () -> datetime | None
    if not _HEARTBEAT_PATH.is_file():
        return None
    try:
        return datetime.fromisoformat(_HEARTBEAT_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser(description="cron_runner 健康检查")
    p.add_argument("--threshold", type=float, default=26.0,
                   help="异常阈值（小时，默认 26h）")
    p.add_argument("--alert", action="store_true",
                   help="异常时推送 Feishu 告警")
    args = p.parse_args()

    last = _read_last()
    if last is None:
        msg = "[cron_health] 心跳文件不存在 ({})。可能从未运行成功。".format(_HEARTBEAT_PATH)
        print(msg)
        if args.alert:
            try:
                from lib import feishu
                feishu.send_text(msg)
            except Exception as e:
                print("alert failed: {}".format(e), file=sys.stderr)
        return 1

    gap_h = (datetime.now() - last).total_seconds() / 3600.0
    healthy = gap_h < args.threshold
    status = "OK" if healthy else "STALE"
    line = "[cron_health] status={} last_success={} gap={:.2f}h threshold={:.1f}h".format(
        status, last.strftime("%Y-%m-%d %H:%M"), gap_h, args.threshold)
    print(line)

    if not healthy and args.alert:
        try:
            from lib import feishu
            feishu.send_text(line + "\n请到主机检查 cron 服务与 cron_runner.py 输出。")
        except Exception as e:
            print("alert failed: {}".format(e), file=sys.stderr)

    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
