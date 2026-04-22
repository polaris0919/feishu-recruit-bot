#!/usr/bin/env python3
"""talent/cmd_normalize_cv_filenames.py —— v3.5.10 剥离 CV 文件名的飞书 Gateway 前缀。

【背景】
  飞书把 HR 拖进来的附件落盘时会前缀 `doc_<12hex>_<original>`，纯属内部 ID。
  v3.5.10 之前 import_cv 没剥这个前缀就直接搬进 candidates/<tid>/cv/，
  导致 `talents.cv_path` 也带了这个前缀，飞书展示时一眼就能看出来：
    <RECRUIT_WORKSPACE>/data/candidates/t_demo03/cv/
        doc_0123456789ab_【股票量化研究员_上海 500-1000元_天】候选人B 1年以内.pdf

【这个脚本干嘛】
  1. SELECT talent_id, cv_path FROM talents WHERE cv_path LIKE '%/doc_%'
  2. 对每条：
     - 用 lib.candidate_storage.strip_feishu_prefix 算出干净文件名
     - 如果新名 == 老名 → 跳过（不是飞书前缀，可能是别的 doc_ 巧合）
     - 否则 mv 原文件 → 干净名（碰撞时加 (2) 后缀）
     - 同步 talents.cv_path = 新绝对路径（白名单字段，安全）
  3. dry-run 模式只打印计划不动盘 / 不写库

【调用】
  PYTHONPATH=scripts python3 -m talent.cmd_normalize_cv_filenames --dry-run
  PYTHONPATH=scripts python3 -m talent.cmd_normalize_cv_filenames
  PYTHONPATH=scripts python3 -m talent.cmd_normalize_cv_filenames --json

【幂等】
  - 已经剥过前缀的：cv_path LIKE '%/doc_%' 不再匹配 → skip
  - 同名碰撞：自动 (2) 后缀（同 import_cv._resolve_collision 行为）
  - 文件不存在：warn 并跳过（不报错，避免阻塞）

【退出码】
  0 = 全部成功（含 dry-run / no-op）
  1 = 至少一条出错（其他仍尽量做完）
  2 = 参数错误
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from lib import candidate_storage as cs
from lib import talent_db
from lib.cli_wrapper import UserInputError
from lib.side_effect_guard import side_effects_disabled


def _build_parser():
    p = argparse.ArgumentParser(
        description="剥离 talents.cv_path 中飞书 Gateway 的 doc_<hex>_ 前缀")
    p.add_argument("--dry-run", action="store_true",
                   help="只打印将要做的操作，不动盘 / 不写库")
    p.add_argument("--json", action="store_true", help="机器友好输出")
    return p


def _collect_candidates():
    """返回 [(talent_id, cv_path), ...]，只包含 cv_path 文件名带 doc_ 前缀的行。"""
    rows = talent_db._query_all(
        "SELECT talent_id, cv_path FROM talents "
        "WHERE cv_path IS NOT NULL AND cv_path <> '' "
        "ORDER BY talent_id"
    )
    out = []
    for r in rows:
        cv_path = r.get("cv_path")
        if not cv_path:
            continue
        old_name = Path(cv_path).name
        new_name = cs.strip_feishu_prefix(old_name)
        if new_name != old_name:
            out.append((r["talent_id"], cv_path))
    return out


def _plan_one(talent_id, old_path_str):
    """返回 dict：{talent_id, old_path, new_path, status, error?}"""
    old = Path(old_path_str)
    new_name = cs.strip_feishu_prefix(old.name)
    new = old.with_name(new_name)
    plan = {
        "talent_id": talent_id,
        "old_path": str(old),
        "new_path": str(new),
        "status": "pending",
        "error": None,
    }
    if new == old:
        plan["status"] = "skip_no_prefix"
        return plan
    if not old.is_file():
        # 文件不存在但 DB 里挂着 → 还是把 cv_path 同步到 new（让人手补文件即可）
        plan["status"] = "missing_file_db_only"
        return plan
    return plan


def _apply_one(plan, dry_run):
    """真正执行一条计划：mv 文件 + 同步 talents.cv_path。"""
    if plan["status"] in ("skip_no_prefix",):
        return plan

    old = Path(plan["old_path"])
    new = Path(plan["new_path"])

    # 防碰撞：目标已存在
    #   - 内容（size）完全一致 → 视为重复，直接删掉带前缀的副本，DB 指向已存在的干净文件
    #   - 内容不同 → 加 (2) 后缀避免覆盖
    if new.exists() and new.resolve() != old.resolve():
        try:
            same_size = (old.stat().st_size == new.stat().st_size)
        except OSError:
            same_size = False
        if same_size:
            plan["status"] = "duplicate_dropped"  # 标记后面单独处理
        else:
            n = 2
            while True:
                candidate = new.with_name("{} ({}){}".format(new.stem, n, new.suffix))
                if not candidate.exists():
                    new = candidate
                    plan["new_path"] = str(new)
                    break
                n += 1
                if n > 999:
                    plan["status"] = "error"
                    plan["error"] = "同名冲突过多"
                    return plan

    if dry_run:
        if plan["status"] == "pending":
            plan["status"] = "dry_run_ok"
        elif plan["status"] == "duplicate_dropped":
            plan["status"] = "dry_run_drop_duplicate"
        return plan

    try:
        if plan["status"] == "missing_file_db_only":
            pass  # 没文件可搬，只刷 DB
        elif plan["status"] == "duplicate_dropped":
            # 干净文件已存在 + size 一致 → 删带前缀那份
            old.unlink()
        else:
            shutil.move(str(old), str(new))
        ok = talent_db.update_talent_field(plan["talent_id"], "cv_path", str(new))
        if not ok and plan["status"] not in ("missing_file_db_only", "duplicate_dropped"):
            # update 返回 False 说明 talent 不存在或字段未变；前者很奇怪，记一下
            plan["status"] = "warn_db_no_update"
        elif plan["status"] == "missing_file_db_only":
            plan["status"] = "db_updated_only"
        elif plan["status"] == "duplicate_dropped":
            pass  # 保持 duplicate_dropped 标记
        else:
            plan["status"] = "renamed"
    except Exception as e:
        plan["status"] = "error"
        plan["error"] = "{}: {}".format(type(e).__name__, e)

    return plan


def _summarize(plans, dry_run):
    summary = {
        "dry_run": dry_run,
        "total": len(plans),
        "renamed": 0,
        "dry_run_ok": 0,
        "skip_no_prefix": 0,
        "missing_file_db_only": 0,
        "db_updated_only": 0,
        "warn_db_no_update": 0,
        "error": 0,
    }
    for p in plans:
        s = p["status"]
        summary[s] = summary.get(s, 0) + 1
    summary["plans"] = plans
    return summary


def main(argv=None):
    args = _build_parser().parse_args(argv)
    if args.dry_run:
        # 兜底：即使代码逻辑漏掉一处 mv/update 也不能动盘 / 写库
        os.environ["RECRUIT_DISABLE_SIDE_EFFECTS"] = "1"
    dry_run = args.dry_run or side_effects_disabled()

    candidates = _collect_candidates()
    if not candidates:
        out = {"dry_run": dry_run, "total": 0, "message": "no cv_path 带飞书前缀，nothing to do"}
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print("[normalize_cv] {}".format(out["message"]))
        return 0

    plans = []
    for tid, cv in candidates:
        plan = _plan_one(tid, cv)
        plan = _apply_one(plan, dry_run)
        plans.append(plan)

    summary = _summarize(plans, dry_run)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("[normalize_cv] dry_run={} total={}".format(dry_run, summary["total"]))
        for p in plans:
            tag = p["status"]
            if tag == "renamed" or tag == "dry_run_ok":
                print("  ✅ {} {} → {}".format(
                    p["talent_id"], Path(p["old_path"]).name, Path(p["new_path"]).name))
            elif tag in ("duplicate_dropped", "dry_run_drop_duplicate"):
                print("  ♻️  {} 重复副本（与 {} size 一致）→ {} 删除带前缀那份".format(
                    p["talent_id"], Path(p["new_path"]).name,
                    "[dry-run]" if tag == "dry_run_drop_duplicate" else "已"))
            elif tag == "missing_file_db_only":
                print("  ⚠️  {} 文件缺失（仅刷 DB cv_path）：{}".format(
                    p["talent_id"], p["old_path"]))
            elif tag == "db_updated_only":
                print("  ⚠️  {} DB 已更新（文件原本就缺）".format(p["talent_id"]))
            elif tag == "skip_no_prefix":
                print("  ⏭  {} 不带飞书前缀，skip".format(p["talent_id"]))
            elif tag == "warn_db_no_update":
                print("  ⚠️  {} 文件已 mv 但 DB 没更新（行不存在？）".format(p["talent_id"]))
            elif tag == "error":
                print("  ❌ {} 失败：{}".format(p["talent_id"], p["error"]))
            else:
                print("  ?? {} status={}".format(p["talent_id"], tag))

    return 1 if summary["error"] > 0 else 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except UserInputError as e:
        print("[normalize_cv] INPUT ERROR: {}".format(e), file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print("[normalize_cv] CRASH: {}".format(e), file=sys.stderr)
        sys.exit(1)
