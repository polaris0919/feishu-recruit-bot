#!/usr/bin/env python3
"""Migrate CV files into recruit-files/candidate_cv/<name>__<talent_id>/.

This command is intentionally side-effect free unless --apply is passed.
It updates talents.cv_path only after the target file has been materialized.
"""
from __future__ import print_function

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib import candidate_storage as cs
from lib import talent_db as tdb


_CV_SUFFIXES = (".pdf", ".doc", ".docx", ".wps", ".rtf")


def _is_under(path, parent):
    # type: (Path, Path) -> bool
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _latest_file(paths):
    # type: (list) -> Path
    files = [p for p in paths if p.is_file() and not p.name.startswith("_")]
    if not files:
        return None
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0]


def _legacy_cv_source(talent_id):
    # type: (str) -> Path
    base = cs.legacy_cv_dir(talent_id)
    if not base.is_dir():
        return None
    return _latest_file([
        p for p in base.iterdir()
        if p.suffix.lower() in _CV_SUFFIXES
    ])


def _flat_cv_source(talent_id):
    # type: (str) -> Path
    base = cs.data_root() / "candidate_cvs"
    if not base.is_dir():
        return None
    return _latest_file([
        p for p in base.glob("{}__*".format(talent_id))
        if p.suffix.lower() in _CV_SUFFIXES
    ])


def _choose_source(row):
    # type: (dict) -> tuple
    cv_path = (row.get("cv_path") or "").strip()
    if cv_path:
        path = Path(cv_path).expanduser()
        if path.is_file():
            return path.resolve(), "talents.cv_path"
    for label, finder in (
        ("legacy candidates/<tid>/cv", _legacy_cv_source),
        ("legacy candidate_cvs flat index", _flat_cv_source),
    ):
        found = finder(row["talent_id"])
        if found:
            return found.resolve(), label
    return None, "missing"


def _planned_db_path(talent_id, candidate_name, source, imported_path):
    # type: (str, str, Path, Path) -> Path
    target_dir = cs.cv_dir(talent_id, candidate_name=candidate_name)
    if _is_under(imported_path, target_dir):
        return imported_path.resolve()
    expected = target_dir / cs.strip_feishu_prefix(source.name)
    if expected.is_file():
        return expected.resolve()
    return imported_path.resolve()


def _load_rows():
    if not tdb._is_enabled():
        raise RuntimeError("talent_db 未启用，无法迁移 talents.cv_path")
    return tdb._query_all(
        "SELECT talent_id, candidate_name, cv_path FROM talents ORDER BY talent_id",
        (),
    )


def main(argv=None):
    p = argparse.ArgumentParser(description="迁移候选人 CV 到 candidate_cv/<姓名>__<talent_id>/")
    p.add_argument("--apply", action="store_true", help="实际复制/移动文件并更新 DB")
    p.add_argument("--mode", choices=("copy", "move"), default="copy",
                   help="迁移文件模式；默认 copy，保留旧文件作为回滚参考")
    p.add_argument("--limit", type=int, default=0, help="最多处理多少条，0 表示不限")
    args = p.parse_args(argv or sys.argv[1:])

    rows = _load_rows()
    if args.limit > 0:
        rows = rows[:args.limit]

    summary = {
        "apply": bool(args.apply),
        "mode": args.mode,
        "total": len(rows),
        "migrated": 0,
        "already_ok": 0,
        "missing": 0,
        "errors": 0,
    }

    for row in rows:
        tid = row["talent_id"]
        name = (row.get("candidate_name") or "").strip()
        source, source_label = _choose_source(row)
        if not source:
            summary["missing"] += 1
            print("[missing] {} {} source={}".format(tid, name, source_label))
            continue

        target_dir = cs.cv_dir(tid, candidate_name=name)
        try:
            already = _is_under(source, target_dir)
            if args.apply:
                imported = cs.import_cv(tid, str(source), mode=args.mode, candidate_name=name)
                db_path = _planned_db_path(tid, name, source, Path(imported))
                current = (row.get("cv_path") or "").strip()
                changed = current != str(db_path) or not already
                if current != str(db_path):
                    tdb._update(
                        "UPDATE talents SET cv_path = %s, updated_at = now() WHERE talent_id = %s",
                        (str(db_path), tid),
                    )
                if changed:
                    tdb.save_audit_event(
                        tid,
                        "cv_storage_migrated",
                        payload={
                            "from": str(source),
                            "from_source": source_label,
                            "to": str(db_path),
                            "mode": args.mode,
                        },
                        actor="ops.cv_storage_migration",
                    )
                final = db_path
            else:
                final = target_dir / cs.strip_feishu_prefix(source.name)

            if already and (row.get("cv_path") or "").strip() == str(final):
                summary["already_ok"] += 1
                status = "ok"
            else:
                summary["migrated"] += 1
                status = "plan" if not args.apply else "migrated"
            print("[{}] {} {} {} -> {}".format(status, tid, name, source, final))
        except Exception as e:
            summary["errors"] += 1
            print("[error] {} {}: {}".format(tid, source, e), file=sys.stderr)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
