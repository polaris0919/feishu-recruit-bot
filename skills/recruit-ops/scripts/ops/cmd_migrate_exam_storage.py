#!/usr/bin/env python3
"""Migrate legacy exam_answer files into exam_submissions/<name>__<talent_id>/."""
from __future__ import print_function

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from psycopg2.extras import Json

from lib import candidate_storage as cs
from lib import talent_db as tdb


def _sha256(path):
    h = hashlib.sha256()
    with open(str(path), "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_conflict(target, src):
    if not target.exists():
        return target
    try:
        if target.stat().st_size == src.stat().st_size and _sha256(target) == _sha256(src):
            return target
    except OSError:
        pass
    stem, suffix = target.stem, target.suffix
    for n in range(2, 1000):
        candidate = target.with_name("{} ({}){}".format(stem, n, suffix))
        if not candidate.exists():
            return candidate
    raise RuntimeError("文件名冲突过多: {}".format(target))


def _iter_files(base):
    for root, _, files in os.walk(str(base)):
        for name in files:
            yield Path(root) / name


def _load_talents():
    if not tdb._is_enabled():
        raise RuntimeError("talent_db 未启用，无法迁移笔试附件")
    rows = tdb._query_all(
        "SELECT talent_id, candidate_name FROM talents ORDER BY talent_id",
        (),
    )
    return {r["talent_id"]: (r.get("candidate_name") or "").strip() for r in rows}


def _decode_attachments(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _rewrite_attachment_paths(talent_id, candidate_name, attachments):
    decoded = _decode_attachments(attachments)
    if not isinstance(decoded, list):
        return decoded, 0

    old_rel = "candidates/{}/exam_answer/".format(talent_id)
    old_abs = str(cs.exam_answer_dir(talent_id).resolve()) + os.sep
    new_rel = "exam_submissions/{}/".format(cs.cv_folder_name(talent_id, candidate_name))
    new_abs = str(cs.exam_submission_dir(talent_id, candidate_name).resolve()) + os.sep

    changed = 0
    out = []
    for item in decoded:
        if not isinstance(item, dict):
            out.append(item)
            continue
        next_item = dict(item)
        path = str(next_item.get("path") or "")
        if path.startswith(old_rel):
            next_item["path"] = new_rel + path[len(old_rel):]
            changed += 1
        elif path.startswith(old_abs):
            next_item["path"] = new_abs + path[len(old_abs):]
            changed += 1
        out.append(next_item)
    return out, changed


def _migrate_files(talent_id, candidate_name, apply):
    src_base = cs.exam_answer_dir(talent_id)
    if not src_base.is_dir():
        return 0, 0
    target_base = cs.exam_submission_dir(talent_id, candidate_name)
    planned = 0
    copied = 0
    for src in _iter_files(src_base):
        rel = src.relative_to(src_base)
        target = _resolve_conflict(target_base / rel, src)
        planned += 1
        if apply:
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            if not target.exists() or _sha256(target) != _sha256(src):
                shutil.copy2(str(src), str(target))
                try:
                    os.chmod(str(target), 0o600)
                except OSError:
                    pass
                copied += 1
    return planned, copied


def _migrate_db_paths(names, apply):
    rows = tdb._query_all(
        "SELECT email_id, talent_id, attachments FROM talent_emails "
        "WHERE attachments::text LIKE %s ORDER BY talent_id, email_id",
        ("%exam_answer%",),
    )
    changed_rows = 0
    changed_items = 0
    for row in rows:
        tid = row["talent_id"]
        rewritten, count = _rewrite_attachment_paths(tid, names.get(tid) or "", row["attachments"])
        if count <= 0:
            continue
        changed_rows += 1
        changed_items += count
        print("[db{}] {} {} paths={}".format(
            "" if apply else "-plan", tid, row["email_id"], count))
        if apply:
            tdb._update(
                "UPDATE talent_emails SET attachments = %s WHERE email_id = %s",
                (Json(rewritten), row["email_id"]),
            )
    return changed_rows, changed_items


def main(argv=None):
    p = argparse.ArgumentParser(description="迁移旧 candidates/<tid>/exam_answer 到 exam_submissions/<姓名>__<tid>")
    p.add_argument("--apply", action="store_true", help="实际复制文件并更新 talent_emails.attachments")
    p.add_argument("--limit", type=int, default=0, help="最多处理多少个候选人，0 表示不限")
    args = p.parse_args(argv or sys.argv[1:])

    names = _load_talents()
    items = sorted(names.items())
    if args.limit > 0:
        items = items[:args.limit]

    summary = {
        "apply": bool(args.apply),
        "talents": len(items),
        "file_paths_seen": 0,
        "files_copied": 0,
        "db_rows_changed": 0,
        "db_paths_changed": 0,
        "errors": 0,
    }

    for tid, name in items:
        try:
            planned, copied = _migrate_files(tid, name, args.apply)
            if planned:
                summary["file_paths_seen"] += planned
                summary["files_copied"] += copied
                print("[files{}] {} {} paths={} copied={}".format(
                    "" if args.apply else "-plan", tid, name, planned, copied))
                if args.apply and copied:
                    tdb.save_audit_event(
                        tid,
                        "exam_storage_migrated",
                        payload={
                            "from": str(cs.exam_answer_dir(tid)),
                            "to": str(cs.exam_submission_dir(tid, name)),
                            "file_paths_seen": planned,
                            "files_copied": copied,
                        },
                        actor="ops.exam_storage_migration",
                    )
        except Exception as e:
            summary["errors"] += 1
            print("[error] {}: {}".format(tid, e), file=sys.stderr)

    rows, paths = _migrate_db_paths(names, args.apply)
    summary["db_rows_changed"] = rows
    summary["db_paths_changed"] = paths

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
