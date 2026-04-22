#!/usr/bin/env python3
"""talent/cmd_rebuild_aliases.py —— v3.5.9 全量重建 by_name/ 软链。

【用途】
  扫一遍 talents 表，为每个候选人重建 data/candidates/by_name/<姓名>__<tid>/
  软链；同时清理孤儿 alias（指向已删除候选人的）。日常 cron 跑一次也安全。

【调用】
  PYTHONPATH=scripts python3 -m talent.cmd_rebuild_aliases
  PYTHONPATH=scripts python3 -m talent.cmd_rebuild_aliases --json
  PYTHONPATH=scripts python3 -m talent.cmd_rebuild_aliases --dry-run

【退出码】
  0 = 全部成功（含 dry-run）
  1 = 至少一条候选人 alias 重建报错（其他仍尽量做完）
  2 = 参数错误
"""
from __future__ import annotations

import argparse
import json
import sys

from lib import talent_db
from lib.candidate_aliases import rebuild_all_aliases
from lib.cli_wrapper import UserInputError
from lib.side_effect_guard import side_effects_disabled


def _build_parser():
    p = argparse.ArgumentParser(description="全量重建 data/candidates/by_name/ 软链")
    p.add_argument("--json", action="store_true", help="机器友好输出")
    p.add_argument(
        "--dry-run", action="store_true",
        help="临时开启 RECRUIT_DISABLE_SIDE_EFFECTS=1 跑一遍，不动盘",
    )
    return p


def _collect_talents():
    """从 talents 表拿 [(tid, name), ...]。

    避开 load_state_from_db 的全量 audit join，省内存。
    """
    state = talent_db.load_state_from_db()
    out = []
    for tid, cand in state.get("candidates", {}).items():
        if not tid:
            continue
        name = cand.get("name") or cand.get("candidate_name")
        out.append((tid, name))
    return out


def main(argv=None):
    args = _build_parser().parse_args(argv)

    if args.dry_run:
        import os
        os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = "1"

    talents = _collect_talents()
    if not talents:
        msg = "talents 表为空（或 DB 未启用），nothing to do"
        if args.json:
            print(json.dumps({"ok": True, "talents": 0, "message": msg}, ensure_ascii=False))
        else:
            print("[cmd_rebuild_aliases] {}".format(msg))
        return 0

    summary = rebuild_all_aliases(talents)
    summary["talents_total"] = len(talents)
    summary["dry_run"] = bool(summary.get("dry_run") or side_effects_disabled())

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("by_name 软链重建完成（dry_run={}）".format(summary["dry_run"]))
        print("  total talents       : {}".format(summary["talents_total"]))
        print("  newly built / fixed : {}".format(len(summary.get("built", []))))
        print("  already correct     : {}".format(len(summary.get("already_ok", []))))
        print("  removed dangling    : {}".format(len(summary.get("removed_dangling", []))))
        if summary.get("errors"):
            print("  errors:")
            for e in summary["errors"]:
                print("    - {}: {}".format(e.get("talent_id"), e.get("error")))

    return 1 if summary.get("errors") else 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except UserInputError as e:
        print("[cmd_rebuild_aliases] INPUT ERROR: {}".format(e), file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print("[cmd_rebuild_aliases] CRASH: {}".format(e), file=sys.stderr)
        sys.exit(1)
