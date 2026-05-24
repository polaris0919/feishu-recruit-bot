#!/usr/bin/env python3
"""feishu/cmd_send_file.py —— 通过飞书把本地文件发给指定角色或 open_id。"""
from __future__ import print_function

import argparse
import json
from pathlib import Path

from lib.cli_wrapper import UserInputError
from lib.file_policy import FilePolicyError, validate_sendable_file


def _resolve_open_id(to_role, explicit_open_id):
    if explicit_open_id:
        return explicit_open_id.strip()
    from lib import config as _cfg
    feishu = _cfg.get("feishu")
    if to_role == "boss":
        return (feishu.get("boss_open_id") or "").strip()
    if to_role == "hr":
        return (feishu.get("hr_open_id") or "").strip()
    if to_role == "polaris":
        return (feishu.get("polaris_open_id") or feishu.get("scheduler_open_id") or "").strip()
    if to_role == "interviewer-master":
        return (feishu.get("interviewer_master_open_id") or "").strip()
    if to_role == "interviewer-bachelor":
        return (feishu.get("interviewer_bachelor_open_id") or "").strip()
    if to_role == "interviewer-cpp":
        return (feishu.get("interviewer_cpp_open_id") or "").strip()
    raise UserInputError("未知 --to: {}".format(to_role))


def _build_parser():
    p = argparse.ArgumentParser(description="通过飞书发送本地文件")
    p.add_argument("--file", required=True, help="本地文件路径")
    p.add_argument("--title", default="", help="发送文件前附带的一条说明文本")
    p.add_argument("--to", default="boss", choices=[
        "boss", "hr", "polaris",
        "interviewer-master", "interviewer-bachelor", "interviewer-cpp",
    ])
    p.add_argument("--open-id", default="", help="显式 open_id；提供后覆盖 --to")
    p.add_argument("--confirm-open-id", default="",
                   help="使用 --open-id 时必须重复传入同一个值，防误发到任意用户")
    p.add_argument("--allow-unsafe-file", action="store_true",
                   help="允许发送白名单目录外的非敏感文件；必须配 --confirm-unsafe-file")
    p.add_argument("--confirm-unsafe-file", default="",
                   help="白名单目录外文件的 resolved path 确认值")
    p.add_argument("--file-type", default="stream", help="飞书 IM 文件类型，默认 stream")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        path = validate_sendable_file(
            args.file,
            allow_unsafe=args.allow_unsafe_file,
            confirm_path=args.confirm_unsafe_file or None,
        )
    except FilePolicyError as e:
        raise UserInputError(str(e))
    if args.open_id and args.confirm_open_id != args.open_id:
        raise UserInputError(
            "使用 --open-id 需要同时传 --confirm-open-id 且值完全一致，防止误发。"
        )
    open_id = _resolve_open_id(args.to, args.open_id)
    if not open_id:
        raise UserInputError("未配置目标 open_id: --to {}".format(args.to))
    payload = {
        "file": str(path),
        "file_name": path.name,
        "size": path.stat().st_size,
        "to": args.to,
        "open_id": open_id,
        "title": args.title,
        "dry_run": bool(args.dry_run),
    }
    if args.dry_run:
        payload["ok"] = True
        print(json.dumps(payload, ensure_ascii=False) if args.json else payload)
        return 0
    from lib import feishu
    ok = feishu.send_file(str(path), open_id=open_id, title=args.title or None,
                          file_type=args.file_type)
    payload["ok"] = bool(ok)
    print(json.dumps(payload, ensure_ascii=False) if args.json else payload)
    return 0 if ok else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UserInputError as e:
        print("[feishu.cmd_send_file] INPUT ERROR: {}".format(e))
        raise SystemExit(1)
