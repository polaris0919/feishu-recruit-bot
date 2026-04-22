#!/usr/bin/env python3
"""inbox/cmd_analyze.py —— v3.3 LLM 分析 inbound 邮件，打 analyzed_at + 推飞书。

【职责】
  1. 查 talent_emails WHERE direction='inbound' AND analyzed_at IS NULL
  2. 对每封邮件调 inbox.analyzer.analyze() 拿 intent / summary / urgency
  3. UPDATE analyzed_at=NOW, ai_summary, ai_intent, ai_payload
  4. need_boss_action=true 的推飞书卡片（含候选人姓名、当前阶段、意图、摘要、正文节选）
  5. 自验证（D5）：逐封 assert_email_analyzed

【绝不做】
  - 不动 talents.current_stage
  - 不发邮件
  - 不起草回复（v3.3 agent 在对话里起草，不是 LLM 直接吐给系统）

【兜底】
  - LLM 失败：仍然标 analyzed_at=NOW（避免死循环），但 ai_intent=NULL
    → 这种邮件不推飞书；agent 走 inbox/cmd_review 人工看
  - 单封失败不影响其他封（continue）

【调用示例】
  # 跑一批
  PYTHONPATH=scripts python3 -m inbox.cmd_analyze --limit 20 --json

  # 干跑（只看会分析哪些，不调 LLM、不写 DB、不推飞书）
  PYTHONPATH=scripts python3 -m inbox.cmd_analyze --dry-run --limit 5

  # 不推飞书（只 LLM + 写 DB）
  PYTHONPATH=scripts python3 -m inbox.cmd_analyze --no-feishu --limit 10
"""
from __future__ import print_function

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from lib import talent_db
from lib.cli_wrapper import run_with_self_verify
from lib.self_verify import assert_email_analyzed
from lib.core_state import STAGE_LABELS
from inbox import analyzer


_URGENCY_ICON = {"low": "🟢", "medium": "🟡", "high": "🔴"}
_INTENT_LABEL = {
    "confirm_interview":  "确认面试",
    "reschedule_request": "改期请求",
    "question_boss":      "老板需拍板",
    "exam_submitted":     "笔试提交",
    "thanks_fyi":         "仅告知/致谢",
    "decline_withdraw":   "主动退出",
    "other":              "其他",
}


def _format_feishu_card(email_row, intent_result):
    # type: (Dict[str, Any], Dict[str, Any]) -> str
    """拼装飞书卡片文本。v3.3 格式，不带交互按钮（按钮靠 agent 对话）。"""
    candidate_name = email_row.get("candidate_name") or "(未知候选人)"
    talent_id = email_row.get("talent_id") or ""
    stage = email_row.get("current_stage") or email_row.get("stage_at_receipt") or ""
    stage_label = STAGE_LABELS.get(stage, stage)

    intent = intent_result.get("intent") or "other"
    intent_cn = _INTENT_LABEL.get(intent, intent)
    urgency = intent_result.get("urgency") or "low"
    urgency_icon = _URGENCY_ICON.get(urgency, "⚪")
    summary = intent_result.get("summary") or "(无总结)"
    details = intent_result.get("details") or {}

    subject = email_row.get("subject") or "(无主题)"
    sender = email_row.get("sender") or ""
    body_excerpt = (email_row.get("body_excerpt") or "").strip() or "(空)"
    sent_at = email_row.get("sent_at")
    sent_at_str = sent_at.strftime("%Y-%m-%d %H:%M") if sent_at else ""

    details_line = ""
    if details:
        details_line = "补充：{}\n".format(
            json.dumps(details, ensure_ascii=False, indent=None))

    return (
        "[候选人来信待老板决策]\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "候选人：{name}（{tid}）\n"
        "当前阶段：{stage_cn}（{stage}）\n"
        "意图：{intent_cn} · {urgency_icon} {urgency}\n"
        "AI 总结：{summary}\n"
        "{details}"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "邮件主题：{subject}\n"
        "发件人：{sender}\n"
        "时间：{time}\n"
        "正文节选：\n{body}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "后续操作：在 Cursor 里让 agent 起草回复，确认后用 outbound.cmd_send 发送。"
    ).format(
        name=candidate_name, tid=talent_id,
        stage_cn=stage_label, stage=stage,
        intent_cn=intent_cn, urgency_icon=urgency_icon, urgency=urgency,
        summary=summary,
        details=details_line,
        subject=subject, sender=sender, time=sent_at_str,
        body=body_excerpt,
    )


def _send_feishu(text, no_feishu=False):
    # type: (str, bool) -> bool
    if no_feishu:
        print("[inbox.cmd_analyze][no-feishu]\n{}".format(text), file=sys.stderr)
        return True
    try:
        from lib import feishu
        return bool(feishu.send_text(text))
    except Exception as e:
        print("[inbox.cmd_analyze] 飞书推送失败: {}".format(e), file=sys.stderr)
        return False


def _analyze_one(email_row, dry_run=False, no_feishu=False):
    # type: (Dict[str, Any], bool, bool) -> Dict[str, Any]
    """处理单封邮件。dry-run 只跑 LLM 不写 DB 不推飞书。"""
    email_id = email_row["email_id"]
    talent_id = email_row.get("talent_id") or ""
    stage = (email_row.get("current_stage")
             or email_row.get("stage_at_receipt") or "")
    stage_label = STAGE_LABELS.get(stage, stage)

    result = analyzer.analyze(
        candidate_name=email_row.get("candidate_name") or "",
        stage=stage,
        stage_label=stage_label,
        subject=email_row.get("subject") or "",
        body=email_row.get("body_full") or email_row.get("body_excerpt") or "",
    )

    if dry_run:
        return {
            "email_id": email_id,
            "talent_id": talent_id,
            "intent": (result or {}).get("intent"),
            "summary": (result or {}).get("summary"),
            "need_boss_action": bool((result or {}).get("need_boss_action")),
            "feishu_pushed": False,
            "llm_ok": result is not None,
            "dry_run": True,
        }

    # ── 写 DB ──
    if result:
        talent_db.set_email_analyzed(
            email_id,
            ai_summary=result.get("summary"),
            ai_intent=result.get("intent"),
            ai_payload=result,
        )
    else:
        # LLM 失败兜底：只打 analyzed_at，不写 ai_*，避免死循环
        talent_db.set_email_analyzed(email_id)

    # ── 自验证（D5）──
    assert_email_analyzed(email_id)

    # ── 推飞书（只推 need_boss_action）──
    feishu_pushed = False
    if result and result.get("need_boss_action"):
        text = _format_feishu_card(email_row, result)
        feishu_pushed = _send_feishu(text, no_feishu=no_feishu)

    return {
        "email_id": email_id,
        "talent_id": talent_id,
        "intent": (result or {}).get("intent"),
        "summary": (result or {}).get("summary"),
        "need_boss_action": bool((result or {}).get("need_boss_action")),
        "feishu_pushed": feishu_pushed,
        "llm_ok": result is not None,
        "dry_run": False,
    }


def _build_parser():
    p = argparse.ArgumentParser(
        description="v3.3 LLM 分析 inbound 邮件并推飞书",
    )
    p.add_argument("--limit", type=int, default=20,
                   help="本轮最多分析多少封（默认 20；按 sent_at 先到先分析）")
    p.add_argument("--email-id", default=None,
                   help="只分析指定 email_id（重跑 / 排查用；不受 limit 限制）")
    p.add_argument("--dry-run", action="store_true",
                   help="只跑 LLM 不写 DB 不推飞书")
    p.add_argument("--no-feishu", action="store_true",
                   help="正常写 DB，但不发飞书（仅 stderr 打印卡片）")
    p.add_argument("--json", action="store_true")
    return p


def _fetch_one_email(email_id):
    # type: (str) -> Optional[Dict[str, Any]]
    rows = talent_db._query_all(
        "SELECT e.email_id, e.talent_id, e.message_id, e.subject, e.sender, "
        "       e.sent_at, e.context, e.stage_at_receipt, e.body_full, "
        "       e.body_excerpt, t.candidate_name, t.current_stage "
        "FROM talent_emails e LEFT JOIN talents t ON e.talent_id = t.talent_id "
        "WHERE e.email_id = %s LIMIT 1",
        (email_id,),
    )
    return rows[0] if rows else None


def _do_analyze(args):
    if args.email_id:
        row = _fetch_one_email(args.email_id)
        if not row:
            from lib.cli_wrapper import UserInputError
            raise UserInputError("email {} 不存在".format(args.email_id))
        queue = [row]
    else:
        queue = talent_db.list_unanalyzed_inbound(limit=args.limit)

    if not queue:
        result = {"ok": True, "processed": 0, "note": "no pending inbound"}
        print(json.dumps(result, ensure_ascii=False) if args.json
              else "[inbox.cmd_analyze] 无待分析邮件")
        return 0

    per_email = []
    llm_fail = 0
    feishu_fail = 0
    boss_action = 0

    for row in queue:
        try:
            r = _analyze_one(row, dry_run=args.dry_run, no_feishu=args.no_feishu)
            per_email.append(r)
            if not r.get("llm_ok"):
                llm_fail += 1
            if r.get("need_boss_action"):
                boss_action += 1
                if not r.get("feishu_pushed") and not args.dry_run and not args.no_feishu:
                    feishu_fail += 1
        except Exception as e:
            print("[inbox.cmd_analyze] 处理 {} 失败: {}".format(row.get("email_id"), e),
                  file=sys.stderr)
            per_email.append({"email_id": row.get("email_id"), "error": str(e)[:200]})

    result = {
        "ok": True,
        "processed": len(per_email),
        "llm_fail": llm_fail,
        "need_boss_action": boss_action,
        "feishu_fail": feishu_fail,
        "dry_run": bool(args.dry_run),
        "per_email": per_email,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str))
    else:
        print(("[inbox.cmd_analyze] processed={} need_boss={} llm_fail={} "
               "feishu_fail={}{}").format(
            result["processed"], result["need_boss_action"],
            result["llm_fail"], result["feishu_fail"],
            " [DRY-RUN]" if args.dry_run else ""))
    return 0


def main(argv=None):
    args = _build_parser().parse_args(argv)
    return _do_analyze(args)


if __name__ == "__main__":
    run_with_self_verify("inbox.cmd_analyze", main)
