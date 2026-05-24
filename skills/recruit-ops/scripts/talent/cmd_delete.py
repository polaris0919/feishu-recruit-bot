#!/usr/bin/env python3
"""talent/cmd_delete.py —— v3.3 候选人删除（带备份归档）。

【职责】
  1. 默认 dump 候选人完整快照 + 邮件 timeline 到 data/deleted_archive/<YYYY-MM>/
  2. 把候选人当前正式资料目录归档进 deleted_archive/<YYYY-MM>/：
     candidate_cv/<姓名>__<tid>/、exam_submissions/<姓名>__<tid>/、
     candidates/<tid>/email/，并兜底归档历史 cv/exam_answer 残留。
  3. DELETE FROM talents（CASCADE 会删 talent_emails / talent_events）
  4. 写一条审计事件（在 talent_events 删除前）
  5. 自验证：assert_talent_deleted(talent_id)

【绝对不做】
  - 不发拒信（要发 caller 自行调 outbound/cmd_send.py 的 rejection 模板）
  - --no-backup 也不跳过文件目录归档，只跳过 JSON/timeline 归档

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
from pathlib import Path
from typing import Optional

from lib import talent_db
from lib.candidate_storage import data_root
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
        base = str(data_root() / "deleted_archive")
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


def _is_under(path, parent):
    # type: (Path, Path) -> bool
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _unique_archive_dst(base):
    # type: (Path) -> Path
    if not base.exists():
        return base
    for n in range(2, 1000):
        candidate = base.with_name("{}__{}".format(base.name, n))
        if not candidate.exists():
            return candidate
    raise RuntimeError("归档目标重名过多: {}".format(base))


def _archive_path_if_exists(talent_id, src, label):
    # type: (str, Path, str) -> Optional[str]
    """把 data_root() 下的一个候选人资产目录搬进 deleted_archive。

    源路径不存在时返回 None；源路径存在但移动失败时抛错，阻止后续 DB 删除。
    """
    if not src:
        return None
    src = Path(src).expanduser()
    if not src.exists():
        return None
    root = data_root()
    if not _is_under(src, root):
        raise RuntimeError("拒绝归档 data_root 外路径: {}".format(src))
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    dst_name = "{}__{}__{}".format(talent_id, label, ts)
    dst = _unique_archive_dst(Path(_archive_dir()) / dst_name)
    try:
        shutil.move(str(src), str(dst))
        return str(dst)
    except Exception as e:
        raise RuntimeError(
            "FS 归档失败 src={} dst={}: {}".format(src, dst, e))


def _append_unique(paths, path):
    # type: (list, Optional[Path]) -> None
    if not path:
        return
    p = Path(path).expanduser()
    try:
        resolved = p.resolve()
    except OSError:
        resolved = p
    for existing in paths:
        try:
            if Path(existing).resolve() == resolved:
                return
        except OSError:
            if Path(existing) == p:
                return
    paths.append(p)


def _snapshot_name(snapshot):
    # type: (dict) -> Optional[str]
    return (snapshot.get("candidate_name") or snapshot.get("name") or "").strip() or None


def _archive_candidate_assets(talent_id, snapshot):
    # type: (str, dict) -> list
    """归档当前正式候选人文件目录，并兜底清理历史残留目录。"""
    try:
        from lib import candidate_storage as cs
    except Exception as e:
        print("[cmd_delete] 跳过 FS 归档（candidate_storage 不可用）: {}".format(e),
              file=sys.stderr)
        return []

    candidate_name = _snapshot_name(snapshot)
    cv_paths = []
    exam_paths = []
    email_paths = []
    legacy_paths = []

    cv_path = (snapshot.get("cv_path") or "").strip()
    if cv_path:
        cv_parent = Path(cv_path).expanduser().parent
        if _is_under(cv_parent, cs.candidate_cv_root()):
            _append_unique(cv_paths, cv_parent)
    _append_unique(cv_paths, cs.cv_dir(talent_id, candidate_name))
    if cs.candidate_cv_root().is_dir():
        for p in cs.candidate_cv_root().glob("*__{}".format(talent_id)):
            if p.is_dir():
                _append_unique(cv_paths, p)

    _append_unique(exam_paths, cs.exam_submission_dir(talent_id, candidate_name))
    if cs.exam_submissions_dir().is_dir():
        for p in cs.exam_submissions_dir().glob("*__{}".format(talent_id)):
            if p.is_dir():
                _append_unique(exam_paths, p)

    _append_unique(email_paths, cs.email_dir(talent_id))
    _append_unique(legacy_paths, cs.legacy_cv_dir(talent_id))
    _append_unique(legacy_paths, cs.exam_answer_dir(talent_id))

    archived = []
    for label, paths in (
        ("candidate_cv", cv_paths),
        ("exam_submissions", exam_paths),
        ("email", email_paths),
        ("legacy", legacy_paths),
    ):
        for src in paths:
            moved = _archive_path_if_exists(talent_id, src, label)
            if moved:
                archived.append(moved)

    # 子目录都搬走后，若 candidates/<tid>/ 只剩空壳，移除它避免孤儿目录。
    cdir = cs.candidate_dir(talent_id)
    try:
        if cdir.exists() and cdir.is_dir() and not any(cdir.iterdir()):
            cdir.rmdir()
    except OSError as e:
        print("[cmd_delete] 空 candidate_dir 清理失败 {}: {}".format(cdir, e),
              file=sys.stderr)
    return archived


def _archive_candidate_dir(talent_id):
    # type: (str) -> Optional[str]
    """旧接口兼容：归档 candidates/<tid>/ 整个目录。新主流程不用它。"""
    try:
        from lib.candidate_storage import candidate_dir
    except Exception as e:
        print("[cmd_delete] 跳过 FS 归档（candidate_storage 不可用）: {}".format(e),
              file=sys.stderr)
        return None
    return _archive_path_if_exists(talent_id, candidate_dir(talent_id), "candidate_dir")


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

    # ── v3.8.1 hard guard（事故源 INCIDENT_RULES.md §12 / §13） ────────────
    # cmd_delete 是物理删档，无法 undo。为防止 agent 把"老板自然语言中的删除
    # 动词"误识别为已 confirm（事故 2026-05-10 已发生 2 次），强制要求调用方
    # 显式传一个 --confirm-delete-talent 参数，值必须严格等于 --talent-id。
    # 这样：
    #   1. agent propose 时必须把 talent-id 写两次（值不一致 → 直接拒绝）
    #   2. 老板看到 propose 必须能识别 "为什么这个值出现两次" 才会 confirm
    #   3. cron / executor 等系统调用方显式传 = 表明知情授权
    p.add_argument("--confirm-delete-talent",
                   default=None, metavar="<talent_id>",
                   help="必填 hard guard：值必须严格等于 --talent-id。设计目的："
                        "强制 caller 在 propose 命令时把 talent_id 写两遍——"
                        "防止 LLM 把自然语言里的删除动词误识别为已 confirm。"
                        "事故源 INCIDENT_RULES.md §12 / §13。")
    return p


def _do_delete(args):
    # type: (argparse.Namespace) -> int
    talent_id = args.talent_id

    # ── v3.8.1 hard guard（事故源 INCIDENT_RULES.md §12 / §13） ────────────
    # --confirm-delete-talent 必须严格等于 --talent-id。任何不匹配 / 缺失 →
    # UserInputError 直接退出（rc=2，cli_wrapper 不会推飞书告警，stderr 即可）。
    if not args.confirm_delete_talent:
        raise UserInputError(
            "缺失 --confirm-delete-talent。物理删档是不可逆操作,必须把 talent_id 写两遍才能跑。\n"
            "正确用法：talent.cmd_delete --talent-id {tid} --confirm-delete-talent {tid} ...\n"
            "事故源 INCIDENT_RULES.md §12 / §13。"
            .format(tid=talent_id)
        )
    if args.confirm_delete_talent != talent_id:
        raise UserInputError(
            "--confirm-delete-talent 与 --talent-id 不匹配:\n"
            "  --talent-id            = {tid}\n"
            "  --confirm-delete-talent = {confirm}\n"
            "两者必须严格相等。事故源 INCIDENT_RULES.md §12 / §13。"
            .format(tid=talent_id, confirm=args.confirm_delete_talent)
        )

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
    asset_archive_paths = []
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

        # 删除 DB 前先搬走当前正式资料目录。若已有文件目录归档失败，直接中止删库。
        asset_archive_paths = _archive_candidate_assets(talent_id, snapshot)
        dir_archive_path = asset_archive_paths[0] if asset_archive_paths else None

        # 在 DELETE 之前写审计；CASCADE 会把这条事件也删掉，所以这里只是为了
        # 让 _push_alert / 其他订阅者有机会看到 stage.deleted 事件
        talent_db.save_audit_event(
            talent_id, "talent.deleted",
            payload={"reason": args.reason, "archive_path": archive_path,
                     "dir_archive_path": dir_archive_path,
                     "asset_archive_paths": asset_archive_paths,
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
        "asset_archive_paths": asset_archive_paths,
        "emails_archived": len(emails),
        "dry_run": bool(args.dry_run),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("[cmd_delete] OK talent={} archive={} assets={} emails_archived={}".format(
            talent_id, archive_path, len(asset_archive_paths), len(emails)))
    return 0


def main(argv=None):
    args = _build_parser().parse_args(argv)
    return _do_delete(args)


if __name__ == "__main__":
    run_with_self_verify("talent.cmd_delete", main)
