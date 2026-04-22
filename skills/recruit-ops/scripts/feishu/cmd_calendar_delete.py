#!/usr/bin/env python3
"""feishu/cmd_calendar_delete.py —— v3.4 Phase 5 删除一次面试日历事件（atomic）。

【职责】
  调 lib.feishu.delete_calendar_event_by_id 删除一个 event_id 对应的飞书日历事件。

【调用】
  PYTHONPATH=scripts python3 -m feishu.cmd_calendar_delete \\
      --event-id evt_xxx --json
"""
from __future__ import print_function

import argparse
import json
import sys

from lib.cli_wrapper import run_with_self_verify, UserInputError


def _build_parser():
    p = argparse.ArgumentParser(
        prog="feishu.cmd_calendar_delete",
        description="删除面试日历事件（atomic, JSON 可读）",
    )
    p.add_argument("--event-id", required=True, help="飞书日历 event_id")
    p.add_argument("--reason", default="",
                   help="删除原因（仅写日志，不传给飞书）")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    return p


def _emit(args, payload):
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        if payload.get("ok"):
            print("[cmd_calendar_delete] ok event_id={} deleted={}".format(
                payload.get("event_id"), payload.get("deleted")))
        else:
            print("[cmd_calendar_delete] FAILED: {}".format(
                payload.get("error") or "unknown"), file=sys.stderr)


def main(argv=None):
    args = _build_parser().parse_args(argv)
    event_id = (args.event_id or "").strip()
    if not event_id:
        raise UserInputError("--event-id 不能为空")

    if args.dry_run:
        _emit(args, {
            "ok": True, "dry_run": True,
            "event_id": event_id, "deleted": False,
            "reason": args.reason or None,
        })
        return 0

    from lib.feishu import delete_calendar_event_by_id
    try:
        ok = delete_calendar_event_by_id(event_id)
    except Exception as e:
        _emit(args, {
            "ok": False, "event_id": event_id,
            "error": "{}: {}".format(type(e).__name__, e),
        })
        return 1

    payload = {
        "ok": True, "event_id": event_id, "deleted": bool(ok),
        "reason": args.reason or None,
    }
    _emit(args, payload)
    return 0 if ok else 1


if __name__ == "__main__":
    run_with_self_verify("feishu.cmd_calendar_delete", main)
