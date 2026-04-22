#!/usr/bin/env python3
"""ops/cmd_replay_notifications.py —— 回放 inbox.cmd_analyze 的飞书通知。

【用途】
  cmd_analyze 在分析完入站邮件后，如果 need_boss_action=true 就会推飞书卡片给老板。
  但偶尔会发生：
    - 飞书推送瞬时失败（网络抖）
    - 老板在飞书里误删卡片
    - 某天体检发现有一批邮件漏通知

  这个脚本按条件回放（重发）那些分析过的邮件的飞书卡片。
  不改变 talent_emails / talents 状态，纯 read-only + 推消息。

【筛选条件（可组合）】
  --talent-id      只回放某个候选人的邮件
  --since YYYY-MM-DD  只看这天及以后的邮件
  --intent         过滤 ai_intent（可重复）
  --need-boss-only 只回放 ai_payload->>need_boss_action=true 的（默认 ON）
  --limit          最多回放几条（默认 10，防止批量打扰）
  --dry-run        只打印不推
  --force-urgency-only low|medium|high   只推某紧急度

【调用示例】
  PYTHONPATH=scripts python3 -m ops.cmd_replay_notifications \\
      --talent-id t_xxx --dry-run

  PYTHONPATH=scripts python3 -m ops.cmd_replay_notifications \\
      --since 2026-04-18 --intent reschedule_request --limit 5
"""
from __future__ import print_function

import argparse
import json
import sys
from datetime import datetime

import psycopg2
import psycopg2.extras

from lib import config as _cfg
from lib.cli_wrapper import UserInputError, run_with_self_verify


def _fetch_candidates(args):
    conn = psycopg2.connect(**_cfg.db_conn_params())
    conn.set_session(readonly=True)
    where = ["te.direction='inbound'", "te.analyzed_at IS NOT NULL"]
    params = []

    if args.talent_id:
        where.append("te.talent_id = %s")
        params.append(args.talent_id)
    if args.since:
        try:
            since_dt = datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            raise UserInputError("--since 格式必须是 YYYY-MM-DD")
        where.append("te.sent_at >= %s")
        params.append(since_dt)
    if args.intent:
        where.append("te.ai_intent = ANY(%s)")
        params.append(list(args.intent))
    if args.need_boss_only:
        where.append("(te.ai_payload->>'need_boss_action') = 'true'")
    if args.force_urgency_only:
        where.append("(te.ai_payload->>'urgency') = %s")
        params.append(args.force_urgency_only)

    sql = ("""
        SELECT te.email_id, te.talent_id, te.message_id, te.sender, te.subject,
               te.body_excerpt, te.sent_at, te.ai_summary, te.ai_intent, te.ai_payload,
               t.candidate_name, t.current_stage
        FROM talent_emails te
        JOIN talents t ON t.talent_id = te.talent_id
        WHERE {where}
        ORDER BY te.sent_at DESC
        LIMIT %s
    """).format(where=" AND ".join(where))
    params.append(args.limit)

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _intent_result_from_row(row):
    payload = row.get("ai_payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    return {
        "intent": row.get("ai_intent"),
        "summary": row.get("ai_summary"),
        "urgency": payload.get("urgency", "low"),
        "need_boss_action": payload.get("need_boss_action", False),
        "details": payload.get("details") or {},
    }


def _build_parser():
    p = argparse.ArgumentParser(description="回放 inbox 飞书通知")
    p.add_argument("--talent-id", default=None)
    p.add_argument("--since", default=None, help="YYYY-MM-DD（含）")
    p.add_argument("--intent", action="append", default=[],
                   help="ai_intent 筛选（可重复）")
    p.add_argument("--need-boss-only", action="store_true", default=True,
                   help="只回放 need_boss_action=true 的（默认 ON）")
    p.add_argument("--all-intents", action="store_true",
                   help="关掉 --need-boss-only 的默认值，回放所有 intent")
    p.add_argument("--force-urgency-only", default=None,
                   choices=["low", "medium", "high"])
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-feishu", action="store_true",
                   help="只打印卡片到 stderr，不真推飞书（等价 dry-run 效果）")
    p.add_argument("--json", action="store_true")
    return p


def _do_replay(args):
    if args.all_intents:
        args.need_boss_only = False

    rows = _fetch_candidates(args)
    if not rows:
        msg = {"found": 0, "pushed": 0, "note": "没有符合条件的分析过的邮件"}
        print(json.dumps(msg, ensure_ascii=False) if args.json else
              "没有符合条件的邮件可回放。")
        return 0

    # 懒加载（dry-run 时可以无视）
    from inbox.cmd_analyze import _format_feishu_card, _send_feishu

    pushed = 0
    failed = 0
    summaries = []
    for row in rows:
        intent_result = _intent_result_from_row(row)
        text = _format_feishu_card(row, intent_result)
        text = "[🔁 回放] " + text

        if args.dry_run:
            summaries.append({
                "email_id": row["email_id"],
                "talent_id": row["talent_id"],
                "subject": row.get("subject"),
                "intent": row.get("ai_intent"),
                "dry_run": True,
            })
            if not args.json:
                print("[DRY] {} / {} / {}".format(
                    row.get("candidate_name"), row.get("subject"), row.get("ai_intent")))
            continue

        ok = _send_feishu(text, no_feishu=args.no_feishu)
        if ok:
            pushed += 1
        else:
            failed += 1
        summaries.append({
            "email_id": row["email_id"],
            "talent_id": row["talent_id"],
            "subject": row.get("subject"),
            "intent": row.get("ai_intent"),
            "pushed": ok,
        })
        if not args.json:
            marker = "✓" if ok else "✗"
            print("  [{}] {} / {} / {}".format(
                marker, row.get("candidate_name"),
                row.get("subject"), row.get("ai_intent")))

    summary = {
        "found": len(rows),
        "pushed": pushed,
        "failed": failed,
        "dry_run": args.dry_run,
        "items": summaries,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("\nfound={} pushed={} failed={} dry_run={}".format(
            summary["found"], pushed, failed, args.dry_run))
    return 0 if failed == 0 else 2


def main(argv=None):
    args = _build_parser().parse_args(argv)
    return _do_replay(args)


if __name__ == "__main__":
    run_with_self_verify("ops.cmd_replay_notifications", main)
