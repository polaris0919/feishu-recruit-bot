#!/usr/bin/env python3
"""talent/cmd_delete.py —— v3.3 候选人删除（带备份归档）。

【职责】
  1. 默认 dump 候选人完整快照 + 邮件 timeline 到 data/deleted_archive/<YYYY-MM>/
  2. v3.5.8：把 data/candidates/<tid>/ 整个目录也归档进 deleted_archive/<YYYY-MM>/<tid>__dir/，
     避免 cmd_delete 后 FS 残留空壳（孤儿目录）。
  3. DELETE FROM talents（CASCADE 会删 talent_emails / talent_events）
  4. 写一条审计事件（在 talent_events 删除前）
  5. 自验证：assert_talent_deleted(talent_id)

【绝对不做】
  - 不发拒信（要发 caller 自行调 outbound/cmd_send.py 的 rejection 模板）
  - --no-backup 也不删 candidate_dir，只跳过 JSON 归档；FS 目录依旧搬走

【调用示例】
  PYTHONPATH=scripts python3 -m talent.cmd_delete --talent-id t_abc --reason "二面未通过"
  PYTHONPATH=scripts python3 -m talent.cmd_delete --talent-id t_abc --reason "调整" --no-backup
"""
from __future__ import print_function

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from typing import Optional

from lib import recruit_paths, talent_db
from lib.cli_wrapper import run_with_self_verify, UserInputError
from lib.self_verify import assert_talent_deleted


# ─── 备份归档 ────────────────────────────────────────────────────────────────

def _archive_dir():
    # type: () -> str
    """data/deleted_archive/<YYYY-MM>/

    可由 RECRUIT_DELETED_ARCHIVE_DIR 环境变量覆盖（用于测试）。
    """
    override = os.environ.get("RECRUIT_DELETED_ARCHIVE_DIR")
    if override:
        base = os.path.expanduser(override)
    else:
        base = str(recruit_paths.workspace_path("data", "deleted_archive"))
    sub = datetime.now().strftime("%Y-%m")
    out = os.path.join(base, sub)
    os.makedirs(out, exist_ok=True)
    return out


def _write_archive(talent_id, snapshot, emails, reason, actor):
    # type: (str, dict, list, str, str) -> str
    fname = "{}_{}.json".format(
        talent_id, datetime.now().strftime("%Y%m%dT%H%M%S"))
    path = os.path.join(_archive_dir(), fname)
    payload = {
        "deleted_at": datetime.now().isoformat(timespec="seconds"),
        "deleted_by": actor,
        "reason": reason,
        "talent": snapshot,
        "emails_count": len(emails),
        "emails": emails,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    return path


def _archive_candidate_dir(talent_id):
    # type: (str) -> Optional[str]
    """v3.5.8：把 data/candidates/<tid>/ 整个搬到 deleted_archive/<YYYY-MM>/<tid>__dir_<ts>/。

    返回归档后的目标路径；如果源目录不存在或 candidate_storage 模块不可用则返回 None
    （兼容旧候选人未走 v3.5.8 的情况）。失败 warn-continue 不抛。
    """
    try:
        from lib.candidate_storage import candidate_dir
    except Exception as e:
        print("[cmd_delete] 跳过 FS 归档（candidate_storage 不可用）: {}".format(e),
              file=sys.stderr)
        return None
    src = candidate_dir(talent_id)
    if not src.exists():
        return None
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    dst_name = "{}__dir_{}".format(talent_id, ts)
    dst = os.path.join(_archive_dir(), dst_name)
    try:
        shutil.move(str(src), dst)
        return dst
    except OSError as e:
        print("[cmd_delete] FS 归档失败 src={} dst={}: {}".format(src, dst, e),
              file=sys.stderr)
        return None


# ─── 主流程 ──────────────────────────────────────────────────────────────────

def _build_parser():
    p = argparse.ArgumentParser(
        prog="talent.cmd_delete",
        description="v3.3 删除候选人（默认归档）",
    )
    p.add_argument("--talent-id", required=True)
    p.add_argument("--reason", required=True,
                   help="必填：删除原因（写入归档 + 审计事件）")
    p.add_argument("--actor", default="cli")
    p.add_argument("--no-backup", action="store_true",
                   help="跳过归档（极少场景，比如脏测试数据；正常请保留备份）")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    return p


def _do_delete(args):
    # type: (argparse.Namespace) -> int
    talent_id = args.talent_id

    snapshot = talent_db.get_full_talent_snapshot(talent_id)
    if not snapshot:
        raise UserInputError("候选人 {} 不存在或已删".format(talent_id))

    emails = []
    if not args.no_backup:
        try:
            emails = talent_db.get_email_thread(talent_id, limit=500)
        except Exception as e:
            print("[cmd_delete] 取邮件 timeline 失败（继续）: {}".format(e),
                  file=sys.stderr)

    archive_path = None
    dir_archive_path = None
    if args.dry_run:
        print("[cmd_delete] DRY-RUN talent={} reason={}".format(talent_id, args.reason),
              file=sys.stderr)
    else:
        if not args.no_backup:
            archive_path = _write_archive(
                talent_id, snapshot, emails, args.reason, args.actor)

        # v3.5.9：先撤掉 by_name 软链；不然 archive_dir 走了之后 alias 变 dangling
        # warn-continue：alias 撤不掉只 stderr
        try:
            from lib import candidate_aliases as _ca
            _ca.remove_alias_for(talent_id)
        except Exception as e:
            print("[cmd_delete] alias 移除异常: {}".format(e), file=sys.stderr)

        # v3.5.8：把候选人 FS 目录也搬走（warn-continue：失败只 stderr，不挡删 DB）
        dir_archive_path = _archive_candidate_dir(talent_id)

        # 在 DELETE 之前写审计；CASCADE 会把这条事件也删掉，所以这里只是为了
        # 让 _push_alert / 其他订阅者有机会看到 stage.deleted 事件
        talent_db.save_audit_event(
            talent_id, "talent.deleted",
            payload={"reason": args.reason, "archive_path": archive_path,
                     "dir_archive_path": dir_archive_path,
                     "emails_count": len(emails)},
            actor=args.actor,
        )

        deleted = talent_db.delete_talent(talent_id)
        if not deleted:
            raise RuntimeError("delete_talent 返回 False（已被并发删？）")

    # ── 自验证（D5）───────────────────────────────────────────────────────
    if not args.dry_run:
        assert_talent_deleted(talent_id)

    # ── 输出 ─────────────────────────────────────────────────────────────
    result = {
        "ok": True,
        "talent_id": talent_id,
        "reason": args.reason,
        "actor": args.actor,
        "archive_path": archive_path,
        "dir_archive_path": dir_archive_path,
        "emails_archived": len(emails),
        "dry_run": bool(args.dry_run),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("[cmd_delete] OK talent={} archive={} dir_archive={} emails_archived={}".format(
            talent_id, archive_path, dir_archive_path, len(emails)))
    return 0


def main(argv=None):
    args = _build_parser().parse_args(argv)
    return _do_delete(args)


if __name__ == "__main__":
    run_with_self_verify("talent.cmd_delete", main)
