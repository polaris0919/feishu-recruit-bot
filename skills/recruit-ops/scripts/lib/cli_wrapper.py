#!/usr/bin/env python3
"""cli_wrapper.py —— v3.3 写入脚本的入口包装器（D5 配套）。

每个 cmd_*（写入类）的 main() 都应该被 run_with_self_verify() 包一层：

    from lib.cli_wrapper import run_with_self_verify

    def main():
        ...

    if __name__ == "__main__":
        run_with_self_verify("outbound.cmd_send", main)

它做两件事：
  1. 抓 lib.self_verify.SelfVerifyError → 推飞书告警 + 非零退出
  2. 抓任何未捕获的 Exception → 也推飞书告警（防漏）+ 非零退出

为什么不简单 try/except 写在每个 main 里：
  - DRY：20+ 个脚本不应该重复写告警代码
  - 一致性：所有失败告警长相一样，老板飞书里看着不会乱
  - 可测试性：单元测试可以直接调 main()，run_with_self_verify 只在 __main__ 路径触发
"""
from __future__ import print_function

import json
import os
import socket
import sys
import time
import traceback
from typing import Callable

from lib.self_verify import SelfVerifyError


class UserInputError(Exception):
    """用户输入 / 业务规则违规（e.g. 忘加 --force、模板变量缺失、ID 不存在）。

    和 SelfVerifyError / 未捕获 Exception 不同，这类错误不推飞书告警，
    只走 stderr + exit 1。因为它们是可预期的人类错误，不需要打扰老板。
    """
    pass


# 飞书告警的环境变量开关：CI / 单元测试可以设 1 跳过推送
_ENV_SUPPRESS = "RECRUIT_SUPPRESS_SELF_VERIFY_ALERT"


def run_with_self_verify(script_name, main_fn):
    # type: (str, Callable[[], int]) -> None
    """运行 main_fn；任何失败都推飞书 + 非零退出。

    main_fn 可以返回 int（exit code）或抛异常或 sys.exit。
    无论哪种，失败路径都走 _push_alert。
    """
    started = time.time()
    try:
        rc = main_fn()
        sys.exit(rc if isinstance(rc, int) else 0)

    except SystemExit as e:
        # main_fn 主动 sys.exit；尊重它的 exit code
        rc = e.code if isinstance(e.code, int) else (1 if e.code else 0)
        if rc != 0 and rc != 2:  # 2 通常是 argparse 用法错误，不告警
            _push_alert(
                script_name=script_name,
                title="{} exited with code {}".format(script_name, rc),
                severity="warn",
                context={"exit_code": rc, "elapsed_s": round(time.time() - started, 2)},
            )
        sys.exit(rc)

    except SelfVerifyError as e:
        _push_alert(
            script_name=script_name,
            title="{} self-verify FAIL: {}".format(script_name, e.check),
            severity="error",
            context={**e.context,
                     "check": e.check,
                     "elapsed_s": round(time.time() - started, 2)},
        )
        print("[cli_wrapper] SELF-VERIFY FAIL: {}".format(e), file=sys.stderr)
        sys.exit(3)

    except UserInputError as e:
        # 用户输入 / 业务规则错误：stderr + exit 1，不推飞书（避免骚扰）
        print("[cli_wrapper] INPUT ERROR: {}".format(e), file=sys.stderr)
        sys.exit(1)

    except Exception as e:
        tb = traceback.format_exc(limit=8)
        _push_alert(
            script_name=script_name,
            title="{} CRASHED: {}".format(script_name, type(e).__name__),
            severity="critical",
            context={"error_type": type(e).__name__,
                     "error_message": str(e)[:300],
                     "traceback_tail": tb[-800:],
                     "elapsed_s": round(time.time() - started, 2)},
        )
        print("[cli_wrapper] CRASH: {}".format(e), file=sys.stderr)
        print(tb, file=sys.stderr)
        sys.exit(1)


def _push_alert(script_name, title, severity, context):
    # type: (str, str, str, dict) -> None
    """组装一段 plaintext 告警发飞书。失败不再抛（避免 alert 自己 crash）。

    输出固定结构方便老板扫一眼：
      🚨 [error] outbound.cmd_send self-verify FAIL: assert_email_sent
      host: <hostname>
      ctx: { ... json ... }
    """
    if os.getenv(_ENV_SUPPRESS) == "1":
        # 测试 / 干跑时不发
        return

    icon = {"info": "ℹ️", "warn": "⚠️",
            "error": "🚨", "critical": "🔥"}.get(severity, "⚠️")
    try:
        ctx_json = json.dumps(context, ensure_ascii=False, indent=2,
                              default=str, sort_keys=True)
    except Exception:
        ctx_json = repr(context)

    text_lines = [
        "{} [{}] {}".format(icon, severity, title),
        "host: {}".format(socket.gethostname()),
        "script: {}".format(script_name),
        "ctx:",
        ctx_json[:1800],
    ]
    text = "\n".join(text_lines)

    try:
        from lib import feishu
        feishu.send_text(text)
    except Exception as e:
        # alert 失败也只能 stderr 哀嚎一下，不能再抛
        print("[cli_wrapper] failed to push self-verify alert: {}".format(e),
              file=sys.stderr)
