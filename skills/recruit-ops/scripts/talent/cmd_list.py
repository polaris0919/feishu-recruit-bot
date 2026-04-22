#!/usr/bin/env python3
"""talent/cmd_list.py —— v3.3 列出候选人（只读，支持多种过滤）。

【调用示例】
  # 全部候选人，按 created_at 倒序
  PYTHONPATH=scripts python3 -m talent.cmd_list

  # 按 stage 过滤
  PYTHONPATH=scripts python3 -m talent.cmd_list --stage ROUND1_SCHEDULING

  # 按多个 stage 过滤（逗号分隔）
  PYTHONPATH=scripts python3 -m talent.cmd_list --stage EXAM_SENT,ROUND2_SCHEDULING

  # 模糊搜姓名 / 邮箱
  PYTHONPATH=scripts python3 -m talent.cmd_list --search 候选人L

  # 只看未处理邮件数 > 0 的
  PYTHONPATH=scripts python3 -m talent.cmd_list --has-unanalyzed

  # JSON 输出
  PYTHONPATH=scripts python3 -m talent.cmd_list --stage EXAM_SENT --json
"""
from __future__ import print_function

import argparse
import json
import sys

from lib import talent_db


def _build_parser():
    p = argparse.ArgumentParser(description="v3.3 列出候选人（只读）")
    p.add_argument("--stage", default=None,
                   help="stage 过滤，逗号分隔多个；大小写敏感")
    p.add_argument("--search", default=None, help="姓名 / 邮箱模糊匹配")
    p.add_argument("--has-unanalyzed", action="store_true",
                   help="只显示还有 analyzed_at IS NULL inbound 邮件的候选人")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--order", choices=["created", "updated", "stage"],
                   default="updated",
                   help="排序字段（默认按 updated_at 倒序）")
    p.add_argument("--json", action="store_true")
    return p


def _do_list(args):
    where = []
    params = []

    if args.stage:
        stages = [s.strip() for s in args.stage.split(",") if s.strip()]
        placeholders = ",".join(["%s"] * len(stages))
        where.append("current_stage IN ({})".format(placeholders))
        params.extend(stages)

    if args.search:
        where.append("(candidate_name ILIKE %s OR candidate_email ILIKE %s)")
        params.extend(["%" + args.search + "%"] * 2)

    if args.has_unanalyzed:
        where.append(
            "EXISTS (SELECT 1 FROM talent_emails e "
            "        WHERE e.talent_id = talents.talent_id "
            "          AND e.direction = 'inbound' "
            "          AND e.analyzed_at IS NULL)"
        )

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    order_sql = {
        "created": "created_at DESC",
        "updated": "updated_at DESC NULLS LAST",
        "stage":   "current_stage ASC, updated_at DESC NULLS LAST",
    }[args.order]

    sql = (
        "SELECT talent_id, candidate_name, candidate_email, current_stage, "
        "       position, phone, created_at, updated_at "
        "FROM talents {} ORDER BY {} LIMIT %s"
    ).format(where_clause, order_sql)
    params.append(args.limit)

    rows = talent_db._query_all(sql, tuple(params))

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, default=str, indent=2))
        return 0

    if not rows:
        print("(无匹配候选人)")
        return 0

    print("共 {} 位候选人：".format(len(rows)))
    for r in rows:
        updated = str(r.get("updated_at") or r.get("created_at") or "")[:10]
        print("  {tid:12s}  {name:12s}  {stage:25s}  {pos:12s}  {email:35s}  (更新 {up})".format(
            tid=r.get("talent_id") or "-",
            name=(r.get("candidate_name") or "-")[:12],
            stage=(r.get("current_stage") or "-")[:25],
            pos=(r.get("position") or "-")[:12],
            email=(r.get("candidate_email") or "-")[:35],
            up=updated,
        ))
    return 0


def main(argv=None):
    args = _build_parser().parse_args(argv)
    return _do_list(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
