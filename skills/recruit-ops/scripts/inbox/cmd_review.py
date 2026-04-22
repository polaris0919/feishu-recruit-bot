#!/usr/bin/env python3
"""inbox/cmd_review.py —— v3.3 只读查看候选人邮件流。

老 common/cmd_email_thread.py 的 v3.3 等价物，额外显示：
  - analyzed_at（v3.3 字段）
  - ai_intent / ai_summary（来自 inbox/cmd_analyze）
  - template（outbound 模板名，v3.3 字段）

【读写语义】
  绝对只读。不调 LLM、不写 DB、不发邮件。可以在生产上随便跑。

【调用示例】
  # 完整时间线
  PYTHONPATH=scripts python3 -m inbox.cmd_review --talent-id t_abc123

  # 倒序 + 完整正文
  PYTHONPATH=scripts python3 -m inbox.cmd_review --talent-id t_abc123 --desc --full

  # 按姓名 / 邮箱找 talent_id
  PYTHONPATH=scripts python3 -m inbox.cmd_review --search 候选人L

  # JSON 供 agent 消费
  PYTHONPATH=scripts python3 -m inbox.cmd_review --talent-id t_abc123 --json
"""
from __future__ import print_function

import argparse
import json
import sys

from lib import talent_db as _tdb


_DIR_LABEL = {"inbound": "← 候选人", "outbound": "→ 我们  "}
_STATUS_LABEL = {
    "received": "未分析",
    "pending_boss": "待老板",
    "auto_processed": "已处理",
    "replied": "已回复",
    "dismissed": "已忽略",
    "snoozed": "暂缓",
    "duplicate_skipped": "重复跳过",
    "error": "出错",
}


def _fmt_one(e, show_full=False):
    sent = str(e.get("sent_at") or "-")[:19]
    direction = _DIR_LABEL.get(e.get("direction"), e.get("direction") or "?")
    status = _STATUS_LABEL.get(e.get("status"), e.get("status") or "?")
    ctx = e.get("context") or "-"
    subject = e.get("subject") or "(无主题)"
    body = e.get("body_excerpt") or e.get("body_full") or ""

    tmpl = e.get("template") or ""
    intent = e.get("ai_intent") or ""
    summary = e.get("ai_summary") or ""
    analyzed_at = e.get("analyzed_at")

    meta_parts = ["ctx={}".format(ctx), "状态={}".format(status)]
    if tmpl and tmpl != "freeform":
        meta_parts.append("模板={}".format(tmpl))
    elif tmpl == "freeform":
        meta_parts.append("自由文本")
    if analyzed_at:
        meta_parts.append("已分析")

    print("─" * 100)
    print("[{}] {} | {}".format(sent, direction, " | ".join(meta_parts)))
    print("  主题: {}".format(subject))
    if intent or summary:
        parts = []
        if intent:
            parts.append("[{}]".format(intent))
        if summary:
            parts.append(summary)
        print("  AI:   {}".format(" ".join(parts)))
    if show_full and body:
        print("  正文:")
        for line in body.splitlines():
            print("    " + line)
    elif body:
        snippet = " ".join(body.split())[:200]
        print("  节选: {}{}".format(snippet, "..." if len(body) > 200 else ""))


def _search_talents(keyword):
    rows = _tdb._query_all(
        "SELECT talent_id, candidate_name, candidate_email, current_stage "
        "FROM talents WHERE candidate_name ILIKE %s OR candidate_email ILIKE %s "
        "ORDER BY created_at DESC LIMIT 30",
        ("%" + keyword + "%", "%" + keyword + "%"),
    )
    if not rows:
        print("未找到匹配的候选人。")
        return 1
    print("匹配 {} 位候选人：".format(len(rows)))
    for r in rows:
        print("  {tid:14s}  {name:12s}  {email:35s}  [{stage}]".format(
            tid=r["talent_id"], name=(r["candidate_name"] or "-")[:12],
            email=(r["candidate_email"] or "-"),
            stage=r["current_stage"] or "-"))
    return 0


def _do_review(args):
    if args.search:
        return _search_talents(args.search)
    if not args.talent_id:
        print("必须 --talent-id 或 --search", file=sys.stderr)
        return 2

    cand = _tdb._query_one(
        "SELECT candidate_name, candidate_email, current_stage, position "
        "FROM talents WHERE talent_id = %s", (args.talent_id,))
    if not cand:
        print("候选人 {} 不存在".format(args.talent_id), file=sys.stderr)
        return 1

    # v3.3 拉全字段（包含 analyzed_at / template）
    emails = _tdb._query_all(
        "SELECT email_id, talent_id, message_id, direction, context, status, "
        "       sender, subject, sent_at, received_at, body_full, body_excerpt, "
        "       ai_summary, ai_intent, ai_payload, analyzed_at, template, "
        "       in_reply_to, references_chain, reply_id "
        "FROM talent_emails WHERE talent_id = %s "
        "ORDER BY sent_at {} LIMIT %s".format("DESC" if args.desc else "ASC"),
        (args.talent_id, args.limit),
    )

    if args.json:
        out = {
            "talent_id": args.talent_id,
            "candidate_name": cand.get("candidate_name"),
            "candidate_email": cand.get("candidate_email"),
            "current_stage": cand.get("current_stage"),
            "position": cand.get("position"),
            "emails_count": len(emails),
            "emails": emails,
        }
        print(json.dumps(out, ensure_ascii=False, default=str, indent=2))
        return 0

    print("=" * 100)
    print("候选人：{name}（{tid}）  邮箱：{email}  阶段：{stage}  岗位：{pos}".format(
        name=cand.get("candidate_name") or "-",
        tid=args.talent_id,
        email=cand.get("candidate_email") or "-",
        stage=cand.get("current_stage") or "-",
        pos=cand.get("position") or "-",
    ))
    print("邮件数：{}（顺序：{}）".format(
        len(emails), "倒序" if args.desc else "升序"))
    print()

    if not emails:
        print("(未找到邮件。可能从未沟通，或历史邮件未回填。)")
        return 0

    for e in emails:
        _fmt_one(e, show_full=args.full)
    print("─" * 100)
    return 0


def _build_parser():
    p = argparse.ArgumentParser(
        description="v3.3 只读查看候选人邮件流（含 analyzed_at / ai_intent / template）")
    p.add_argument("--talent-id", help="候选人 ID（如 t_abc123）")
    p.add_argument("--search", help="按姓名 / 邮箱模糊查 talent_id")
    p.add_argument("--desc", action="store_true", help="倒序（最新在前）")
    p.add_argument("--full", action="store_true", help="显示完整正文")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--json", action="store_true", help="JSON 输出（agent 消费）")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    return _do_review(args)


if __name__ == "__main__":
    # 只读脚本不走 cli_wrapper，保持轻量；失败直接暴露 traceback 即可
    sys.exit(main() or 0)
