#!/usr/bin/env python3
"""测试 fixture：根据 argv 控制子进程行为，让 run_module 能跑到真子进程。

设计目标：
    一个 fixture 覆盖 cli_subprocess 的所有 case（env 注入 / 反向扫描
    JSON / 末尾混 debug 行 / 没有 JSON / 非零退出 / timeout），避免每个
    case 都新建 .py 文件。

调用方式（按 run_module(module="tests.fixtures.echo_env", args=[...]) 传入）：

    无 flag                    → 输出一行 JSON：{"PYTHONPATH": ..., "RECRUIT_WORKSPACE_ROOT": ...}
    --exit N                   → 输出 JSON 后以 N 退出（默认 0）
    --tail-debug               → 输出 JSON 后再 print 一行非 JSON debug 文本
                                 （用于验证 parse_json 不是简单取最后一行）
    --no-json                  → 只 print 一段纯文本,不输出 JSON
                                 （用于验证 parse_json=True 时返回 json=None）
    --stderr MSG               → 往 stderr 打印 MSG
    --sleep SEC                → 睡 SEC 秒后再退出
                                 （用于触发 run_module timeout 分支）
"""
from __future__ import print_function

import argparse
import json
import os
import sys
import time


def main(argv=None):
    p = argparse.ArgumentParser(prog="tests.fixtures.echo_env")
    p.add_argument("--exit", dest="exit_code", type=int, default=0)
    p.add_argument("--tail-debug", action="store_true",
                   help="JSON 行后再输出一行非 JSON debug 文本")
    p.add_argument("--no-json", action="store_true",
                   help="只 print 纯文本,不输出 JSON 行")
    p.add_argument("--stderr", default=None, help="往 stderr 打印的内容")
    p.add_argument("--sleep", type=float, default=0.0)
    args = p.parse_args(argv)

    if args.sleep > 0:
        time.sleep(args.sleep)

    if args.stderr:
        print(args.stderr, file=sys.stderr)

    if args.no_json:
        print("plain text line one")
        print("plain text line two")
    else:
        payload = {
            "PYTHONPATH": os.environ.get("PYTHONPATH"),
            "RECRUIT_WORKSPACE_ROOT": os.environ.get("RECRUIT_WORKSPACE_ROOT"),
        }
        print(json.dumps(payload))
        if args.tail_debug:
            print("post-json debug line (not JSON)")

    return args.exit_code


if __name__ == "__main__":
    sys.exit(main() or 0)
