#!/usr/bin/env python3
"""feishu/cmd_notify.py —— v3.5 飞书消息推送 atomic CLI（原 ops/cmd_push_alert）。

【为什么搬到 feishu/】
  v3.5 起 feishu/ 目录是「飞书 sink 的 atomic CLI 集合」（与 outbound/、talent/、
  inbox/ 等并列），现存：
    - feishu/cmd_calendar_create
    - feishu/cmd_calendar_delete
    - feishu/cmd_notify   ← 本文件
  原 ops/cmd_push_alert 的「飞书 push」职责天然属于这个层。ops/ 退化为
  「跨 sink 的运维工具」（health_check / db_migrate / replay_notifications）。

【职责（只干这一件）】
  把一段文本通过 lib.feishu 推到 boss 或 hr。
    - 无副作用写 DB（纯消息推送）；
    - 支持 stdin 长文本；
    - 支持 --dry-run（只打印不推）；
    - 不走 lib.cli_wrapper：alert 自身失败不应再触发 alert（递归死循环）。

【调用示例】
  # 直接推一条
  PYTHONPATH=scripts python3 -m feishu.cmd_notify \
      --title "Exam 批改失败" --body "talent t_xxx 批改超时"

  # 从 stdin 读长文本
  cat report.txt | PYTHONPATH=scripts python3 -m feishu.cmd_notify \
      --title "Daily Summary" --stdin

  # 推给 HR 而不是 boss
  PYTHONPATH=scripts python3 -m feishu.cmd_notify --to hr --title "..." --body "..."

  # 只演练，不真推
  PYTHONPATH=scripts python3 -m feishu.cmd_notify --title "X" --body "Y" --dry-run
"""
from __future__ import print_function

import argparse
import json
import sys
from datetime import datetime

from lib.cli_wrapper import UserInputError


_SEVERITY_EMOJI = {
    "info":     "ℹ️",
    "warn":     "⚠️",
    "error":    "🛑",
    "critical": "🔥",
}


def _format_text(title, body, severity, source):
    emoji = _SEVERITY_EMOJI.get(severity, "ℹ️")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "{} [{}] {}".format(emoji, severity.upper(), title),
        "",
        body.strip() if body else "(无正文)",
        "",
        "───────────────",
        "来源: {}".format(source) if source else "来源: feishu.cmd_notify",
        "时间: {}".format(now),
    ]
    return "\n".join(lines)


def _build_parser():
    p = argparse.ArgumentParser(description="飞书消息推送 atomic CLI（v3.5）")
    p.add_argument("--title", required=True, help="告警标题（一行）")

    src = p.add_mutually_exclusive_group()
    src.add_argument("--body", default=None, help="告警正文")
    src.add_argument("--stdin", action="store_true", help="从 stdin 读正文")

    p.add_argument("--severity", default="warn",
                   choices=["info", "warn", "error", "critical"])
    p.add_argument("--to", default="boss",
                   choices=[
                       "boss", "hr",
                       # v3.5.7 §5.11 一面派单：把通知直接推给对应面试官
                       "interviewer-master",   # 硕士面试官
                       "interviewer-bachelor", # 本科面试官
                       "interviewer-cpp",      # C++ 面试官
                   ],
                   help="推给谁（默认 boss）。interviewer-* 由 §5.11 派单 chain 用，"
                        "open_id 来自 lib.config['feishu']['interviewer_*_open_id']。")
    p.add_argument("--source", default=None,
                   help="调用方标识（进推送尾部方便排查）")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)

    if args.stdin:
        body = sys.stdin.read()
    else:
        body = args.body or ""

    if not args.title.strip():
        raise UserInputError("--title 不能为空")

    text = _format_text(args.title.strip(), body, args.severity, args.source)

    if args.dry_run:
        result = {"dry_run": True, "to": args.to, "chars": len(text), "preview": text}
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else text)
        return 0

    from lib import feishu
    if args.to == "hr":
        ok = feishu.send_text_to_hr(text)
    elif args.to == "interviewer-master":
        ok = feishu.send_text_to_interviewer_master(text)
    elif args.to == "interviewer-bachelor":
        ok = feishu.send_text_to_interviewer_bachelor(text)
    elif args.to == "interviewer-cpp":
        ok = feishu.send_text_to_interviewer_cpp(text)
    else:
        ok = feishu.send_text(text)

    result = {"ok": bool(ok), "to": args.to, "chars": len(text)}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("[feishu.cmd_notify] to={} ok={} chars={}".format(args.to, ok, len(text)))
    return 0 if ok else 2


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except UserInputError as e:
        print("[feishu.cmd_notify] INPUT ERROR: {}".format(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("[feishu.cmd_notify] CRASH: {}".format(e), file=sys.stderr)
        sys.exit(1)
