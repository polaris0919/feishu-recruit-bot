#!/usr/bin/env python3
"""talent/cmd_show.py —— v3.3 只读查看单个候选人完整快照。

输出：talents 行全部字段 + 最近若干审计事件 + 邮件数量统计。
不写任何东西；适合 agent 在决策前拉一个完整画像。

【调用示例】
  PYTHONPATH=scripts python3 -m talent.cmd_show --talent-id t_abc123
  PYTHONPATH=scripts python3 -m talent.cmd_show --talent-id t_abc123 --json
  PYTHONPATH=scripts python3 -m talent.cmd_show --talent-id t_abc123 --audit-limit 30
"""
from __future__ import print_function

import argparse
import json
import sys

from lib import talent_db


_SEP = "─" * 88


def _fmt_plain(snap, email_stats, audit):
    name = snap.get("candidate_name") or "-"
    tid = snap.get("talent_id")
    stage = snap.get("current_stage") or snap.get("stage") or "-"
    print("=" * 88)
    print("{} ({})  当前阶段：{}".format(name, tid, stage))
    print(_SEP)

    ordered_keys = [
        ("candidate_email", "邮箱"),
        ("phone", "电话"),
        ("wechat", "微信"),
        ("position", "应聘岗位"),
        ("education", "学历"),
        ("school", "院校"),
        ("work_years", "工作年限"),
        ("source", "来源"),
        ("experience", "简历摘要 / 经历"),
        ("created_at", "创建于"),
        ("updated_at", "最近更新"),
        ("exam_id", "笔试 ID"),
        ("exam_sent_at", "笔试发送时间"),
        ("round1_invite_sent_at", "一面邀请"),
        ("round1_time", "一面时间"),
        ("round1_confirm_status", "一面确认"),
        ("round2_invite_sent_at", "二面邀请"),
        ("round2_time", "二面时间"),
        ("round2_confirm_status", "二面确认"),
        ("wait_return_round", "WAIT_RETURN 轮"),
    ]
    for key, label in ordered_keys:
        val = snap.get(key)
        if val is None or val == "":
            continue
        print("  {:18s} {}".format(label + "：", val))

    print(_SEP)
    print("邮件统计：{}".format(
        "、".join("{}={}".format(k, v) for k, v in email_stats.items())
        if email_stats else "无邮件"))

    if audit:
        print(_SEP)
        print("最近 {} 条审计事件：".format(len(audit)))
        for ev in audit:
            at = str(ev.get("at") or "")[:19]
            print("  [{}] {} by {}".format(
                at, ev.get("action") or "?", ev.get("actor") or "?"))
    print("=" * 88)


def _do_show(args):
    snap = talent_db.get_full_talent_snapshot(args.talent_id)
    if not snap:
        from lib.cli_wrapper import UserInputError
        raise UserInputError("候选人 {} 不存在".format(args.talent_id))

    email_rows = talent_db._query_all(
        "SELECT direction, status, analyzed_at "
        "FROM talent_emails WHERE talent_id = %s",
        (args.talent_id,),
    )
    stats = {"inbound": 0, "outbound": 0, "unanalyzed_in": 0}
    for r in email_rows:
        if r.get("direction") == "inbound":
            stats["inbound"] += 1
            if not r.get("analyzed_at"):
                stats["unanalyzed_in"] += 1
        elif r.get("direction") == "outbound":
            stats["outbound"] += 1

    audit = talent_db._query_all(
        "SELECT at, action, actor, payload FROM talent_events "
        "WHERE talent_id = %s ORDER BY at DESC LIMIT %s",
        (args.talent_id, args.audit_limit),
    )

    if args.json:
        out = dict(snap)
        out["email_stats"] = stats
        out["audit"] = audit
        print(json.dumps(out, ensure_ascii=False, default=str, indent=2))
    else:
        _fmt_plain(snap, stats, list(reversed(audit)))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="v3.3 候选人完整快照（只读）")
    p.add_argument("--talent-id", required=True)
    p.add_argument("--audit-limit", type=int, default=15)
    p.add_argument("--json", action="store_true")
    return _do_show(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main() or 0)
