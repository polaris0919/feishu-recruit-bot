#!/usr/bin/env python3
"""ops/cmd_db_migrate.py —— v3.3 最小可用的 SQL 迁移工具。

【设计】
  - lib/migrations/schema.sql          =  规范当前 DB 应有的全部 DDL（手动维护的
                                          「终态」描述）。这个脚本**不**直接跑它。
  - lib/migrations/YYYYMMDD_*.sql      =  增量迁移脚本，按文件名字典序应用
                                          （新增列 / 新增索引 / 数据回填 / ...）。
  - recruit_migrations                 =  v3.3 系统表，记录已应用的迁移文件名。
                                          （旧的 schema_migrations 用 version int，
                                           语义不一致；这里另起新表，不侵入遗留。）

  首次运行时：
    ① 若 recruit_migrations 不存在 → 自动创建；
    ② 扫 lib/migrations/ 下除 schema.sql 外的 *.sql；
    ③ 比对 recruit_migrations 已记录的 filename，跑尚未应用的。

【幂等】
  每个迁移 SQL 应该自己幂等（用 `IF NOT EXISTS`、`ADD COLUMN IF NOT EXISTS`），
  这个脚本只负责"按顺序跑、记录跑过"。

【调用示例】
  # 看有哪些待应用迁移（dry-run）
  PYTHONPATH=scripts python3 -m ops.cmd_db_migrate --status

  # 应用所有 pending 迁移
  PYTHONPATH=scripts python3 -m ops.cmd_db_migrate --apply

  # 强制重跑某个文件（绕过 schema_migrations 记录；通常只用于修坏数据）
  PYTHONPATH=scripts python3 -m ops.cmd_db_migrate --force-file 20260417_v33_talent_emails_extend.sql
"""
from __future__ import print_function

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import List, Tuple

import psycopg2

from lib import config as _cfg
from lib.cli_wrapper import run_with_self_verify, UserInputError


_MIGRATIONS_TABLE = "recruit_migrations"

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS recruit_migrations (
    filename     TEXT PRIMARY KEY,
    applied_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sha1         TEXT,
    notes        TEXT
);
"""


def _migrations_dir():
    # lib/migrations 相对 scripts 根目录
    here = os.path.dirname(os.path.abspath(__file__))
    scripts_root = os.path.dirname(here)
    return os.path.join(scripts_root, "lib", "migrations")


def _list_migration_files():
    d = _migrations_dir()
    if not os.path.isdir(d):
        raise UserInputError("migrations 目录不存在: {}".format(d))
    files = []
    for name in sorted(os.listdir(d)):
        if name == "schema.sql":
            continue
        if not name.endswith(".sql"):
            continue
        files.append(name)
    return files


def _ensure_migrations_table(conn):
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_MIGRATIONS_DDL)
    conn.commit()


def _applied_set(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT filename FROM recruit_migrations")
        return {r[0] for r in cur.fetchall()}


def _sha1(path):
    import hashlib
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _apply_one(conn, filename, dry_run=False):
    # type: (Any, str, bool) -> Tuple[bool, str]
    path = os.path.join(_migrations_dir(), filename)
    if not os.path.isfile(path):
        raise UserInputError("迁移文件不存在: {}".format(path))
    with open(path, "r", encoding="utf-8") as f:
        sql = f.read()

    if dry_run:
        return True, "dry-run: {} bytes".format(len(sql))

    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    except Exception as e:
        conn.rollback()
        return False, str(e)[:400]

    # 写 recruit_migrations
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO recruit_migrations (filename, sha1, notes) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (filename) DO UPDATE SET "
                "  applied_at = NOW(), sha1 = EXCLUDED.sha1, notes = EXCLUDED.notes",
                (filename, _sha1(path), "applied by ops.cmd_db_migrate"),
            )
        conn.commit()
    except Exception as e:
        print("[ops.cmd_db_migrate] 记录 recruit_migrations 失败（迁移已生效）: {}".format(e),
              file=sys.stderr)
    return True, "OK"


def _build_parser():
    p = argparse.ArgumentParser(description="v3.3 最小迁移器")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", default=True,
                       help="显示 pending / applied 迁移列表（默认行为）")
    group.add_argument("--apply", action="store_true",
                       help="应用所有 pending 迁移")
    group.add_argument("--force-file", default=None,
                       help="强制重跑单个迁移文件")
    p.add_argument("--dry-run", action="store_true",
                   help="配合 --apply 使用，只显示会跑哪些，不真跑")
    p.add_argument("--json", action="store_true")
    return p


def _do_migrate(args):
    conn = psycopg2.connect(**_cfg.db_conn_params())
    try:
        _ensure_migrations_table(conn)
        applied = _applied_set(conn)
        available = _list_migration_files()
        pending = [f for f in available if f not in applied]

        # ── 强制重跑单个文件 ──
        if args.force_file:
            if args.force_file not in available:
                raise UserInputError("{} 不在 migrations 目录下".format(args.force_file))
            ok, note = _apply_one(conn, args.force_file, dry_run=args.dry_run)
            result = {
                "action": "force", "file": args.force_file,
                "ok": ok, "note": note, "dry_run": args.dry_run,
            }
            print(json.dumps(result, ensure_ascii=False) if args.json
                  else "{} FORCE-RAN {}: {}".format(
                      "OK" if ok else "FAIL", args.force_file, note))
            return 0 if ok else 1

        # ── 应用所有 pending ──
        if args.apply:
            results = []
            for f in pending:
                ok, note = _apply_one(conn, f, dry_run=args.dry_run)
                results.append({"file": f, "ok": ok, "note": note})
                marker = "OK " if ok else "FAIL"
                print("  [{}] {}: {}".format(marker, f, note))
                if not ok:
                    print("  → 终止（前面已成功的已落表，可修后再 --apply）",
                          file=sys.stderr)
                    break
            summary = {
                "action": "apply",
                "pending_count": len(pending),
                "applied_this_run": sum(1 for r in results if r["ok"]),
                "failed_this_run": sum(1 for r in results if not r["ok"]),
                "dry_run": args.dry_run,
                "results": results,
            }
            if args.json:
                print(json.dumps(summary, ensure_ascii=False))
            return 0 if summary["failed_this_run"] == 0 else 1

        # ── 默认：status ──
        status = {
            "applied_count": len(applied),
            "pending_count": len(pending),
            "pending": pending,
            "applied_recent": sorted(applied)[-5:] if applied else [],
        }
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            print("Applied migrations: {}".format(status["applied_count"]))
            for f in status["applied_recent"]:
                print("  ✓ {}".format(f))
            print("Pending migrations: {}".format(status["pending_count"]))
            for f in pending:
                print("  ⏳ {}".format(f))
            if pending:
                print("\n→ 应用：PYTHONPATH=scripts python3 -m ops.cmd_db_migrate --apply")
        return 0
    finally:
        conn.close()


def main(argv=None):
    args = _build_parser().parse_args(argv)
    return _do_migrate(args)


if __name__ == "__main__":
    run_with_self_verify("ops.cmd_db_migrate", main)
