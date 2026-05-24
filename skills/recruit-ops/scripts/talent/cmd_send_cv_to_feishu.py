#!/usr/bin/env python3
"""talent/cmd_send_cv_to_feishu.py —— 发送候选人 CV 文件到飞书。"""
from __future__ import print_function

import argparse
import json
from pathlib import Path

from lib import talent_db
from lib.cli_wrapper import UserInputError


def _build_parser():
    p = argparse.ArgumentParser(description="把候选人 CV 发到飞书")
    p.add_argument("--talent-id", required=True)
    p.add_argument("--to", default="boss", choices=[
        "boss", "hr", "polaris",
        "interviewer-master", "interviewer-bachelor", "interviewer-cpp",
    ])
    p.add_argument("--open-id", default="")
    p.add_argument("--confirm-open-id", default="",
                   help="使用 --open-id 时必须重复传入同一个值，防误发")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    snap = talent_db.get_full_talent_snapshot(args.talent_id)
    if not snap:
        raise UserInputError("候选人不存在: {}".format(args.talent_id))
    cv_path = (snap.get("cv_path") or "").strip()
    if not cv_path:
        raise UserInputError("候选人没有 cv_path: {}".format(args.talent_id))
    path = Path(cv_path).expanduser()
    if not path.is_file():
        raise UserInputError("CV 文件不存在: {}".format(path))

    candidate_name = snap.get("candidate_name") or args.talent_id
    title = "候选人 CV\n候选人：{} ({})\n岗位：{}".format(
        candidate_name, args.talent_id, snap.get("position") or "(未记录)")

    from feishu import cmd_send_file
    rc = cmd_send_file.main([
        "--file", str(path),
        "--to", args.to,
        *(["--open-id", args.open_id,
           "--confirm-open-id", args.confirm_open_id] if args.open_id else []),
        "--title", title,
        *(["--dry-run"] if args.dry_run else []),
        "--json",
    ])
    payload = {
        "ok": rc == 0,
        "talent_id": args.talent_id,
        "candidate_name": candidate_name,
        "cv_path": str(path),
        "dry_run": bool(args.dry_run),
    }
    print(json.dumps(payload, ensure_ascii=False) if args.json else payload)
    return 0 if rc == 0 else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UserInputError as e:
        print("[talent.cmd_send_cv_to_feishu] INPUT ERROR: {}".format(e))
        raise SystemExit(1)
