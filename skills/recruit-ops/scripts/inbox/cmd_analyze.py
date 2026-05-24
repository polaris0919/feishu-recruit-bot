#!/usr/bin/env python3
"""inbox/cmd_analyze.py —— v3.3 LLM 分析 inbound 邮件，打 analyzed_at + 推飞书。

【职责】
  1. 查 talent_emails WHERE direction='inbound' AND analyzed_at IS NULL
  2. 对每封邮件调 inbox.analyzer.analyze() 拿 intent / summary / urgency
  3. UPDATE analyzed_at=NOW, ai_summary, ai_intent, ai_payload
  4. 所有扫描到的候选人 inbound 邮件都推飞书给老板 + Polaris；
     need_boss_action=true 的卡片仍标"待老板决策"，其他标"候选人来信通知"。
  5. 自验证（D5）：逐封 assert_email_analyzed

【绝不做】
  - 不动 talents.current_stage
  - 不发邮件
  - 不起草回复（v3.3 agent 在对话里起草，不是 LLM 直接吐给系统）

【兜底】
  - LLM 失败：仍然标 analyzed_at=NOW（避免死循环），但 ai_intent=NULL；
    仍推飞书，提示"分析失败，请人工查看"。
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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from lib import talent_db
from lib.cli_subprocess import run_module
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

# v3.8.4 场景 8 分权修订：候选人发"我可以"邮件后,系统**不再**自动建日历升级 SCHEDULED;
# 改为强制推飞书 warn 卡给老板,老板飞书消息说"OK 建日历" / "X 时间确认了"等显式
# 安排指令后才走 §4.2 chain。理由：建日历是面试时间的**最终确认**动作,候选人 confirm
# 邮件可能与老板/HR 已知的其他安排冲突（同一时段已约其他人 / 老板临时有会 / 改期未告
# 知 agent 等）,这一步必须老板拍板。
#
# 实现：analyzer.analyze() 是无状态 LLM 包装,不知道 stage;在本层根据 stage 后置 override
# need_boss_action 旗标即可让 cmd_analyze 推飞书,agent 看到 warn 卡后不主动跑 §4.2。
_STAGE_AWARE_NEED_BOSS = {
    # (intent, stage): need_boss=True
    ("confirm_interview", "ROUND1_SCHEDULING"),
    ("confirm_interview", "ROUND2_SCHEDULING"),
}

_RESCHEDULE_DECISION_THRESHOLD_HOURS = 24.0
_EXAM_REVIEW_TIMEOUT_SEC = 300


def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def _hours_until_interview(email_row, now=None):
    stage = email_row.get("current_stage") or email_row.get("stage_at_receipt") or ""
    if stage.startswith("ROUND1_"):
        round_num = 1
        when = email_row.get("round1_time")
    elif stage.startswith("ROUND2_"):
        round_num = 2
        when = email_row.get("round2_time")
    else:
        return None, None, None
    dt = _parse_dt(when)
    if not dt:
        return round_num, when, None
    now_dt = now or datetime.now(dt.tzinfo or timezone.utc)
    if dt.tzinfo is None and getattr(now_dt, "tzinfo", None) is not None:
        now_dt = now_dt.replace(tzinfo=None)
    elif dt.tzinfo is not None and getattr(now_dt, "tzinfo", None) is None:
        now_dt = now_dt.replace(tzinfo=dt.tzinfo)
    return round_num, when, (dt - now_dt).total_seconds() / 3600.0


def _apply_reschedule_proximity_policy(email_row, intent_result, now=None):
    if not intent_result or intent_result.get("intent") != "reschedule_request":
        return intent_result
    round_num, interview_time, hours_until = _hours_until_interview(email_row, now=now)
    if hours_until is None or hours_until > _RESCHEDULE_DECISION_THRESHOLD_HOURS:
        return intent_result
    intent_result["urgency"] = "high"
    intent_result["need_boss_action"] = True
    details = dict(intent_result.get("details") or {})
    details["round"] = round_num
    details["interview_time"] = str(interview_time or "")
    details["hours_until"] = round(hours_until, 1)
    details["proximity_policy"] = "within_24h_reschedule"
    intent_result["details"] = details
    intent_result["_reschedule_decision_card"] = True
    return intent_result


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

    if intent_result.get("_reschedule_decision_card"):
        return _format_reschedule_decision_card(email_row, intent_result)

    subject = email_row.get("subject") or "(无主题)"
    sender = email_row.get("sender") or ""
    body_excerpt = (email_row.get("body_excerpt") or "").strip() or "(空)"
    sent_at = email_row.get("sent_at")
    sent_at_str = sent_at.strftime("%Y-%m-%d %H:%M") if sent_at else ""
    attachment_line = _format_attachment_line(email_row)

    details_line = ""
    if details:
        details_line = "补充：{}\n".format(
            json.dumps(details, ensure_ascii=False, indent=None))

    need_boss = bool(intent_result.get("need_boss_action"))
    title = "[候选人来信待老板决策]" if need_boss else "[候选人来信通知]"
    followup = (
        "后续操作：请老板在飞书对 bot 明确下达下一步。"
        if need_boss else
        "后续操作：仅同步给老板和 Polaris；如需处理，请老板/HR 在飞书对 bot 下达指令。"
    )

    return (
        "{title}\n"
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
        "{attachment_line}"
        "正文节选：\n{body}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "{followup}"
    ).format(
        title=title,
        name=candidate_name, tid=talent_id,
        stage_cn=stage_label, stage=stage,
        intent_cn=intent_cn, urgency_icon=urgency_icon, urgency=urgency,
        summary=summary,
        details=details_line,
        subject=subject, sender=sender, time=sent_at_str,
        attachment_line=attachment_line,
        body=body_excerpt,
        followup=followup,
    )


def _format_reschedule_decision_card(email_row, intent_result):
    candidate_name = email_row.get("candidate_name") or "(未知候选人)"
    talent_id = email_row.get("talent_id") or ""
    stage = email_row.get("current_stage") or email_row.get("stage_at_receipt") or ""
    stage_label = STAGE_LABELS.get(stage, stage)
    details = intent_result.get("details") or {}
    round_num = details.get("round") or "?"
    interview_time = details.get("interview_time") or "(未知)"
    hours_until = details.get("hours_until")
    summary = intent_result.get("summary") or "(无总结)"
    reason = details.get("reason") or "未说明"
    new_time = details.get("new_time") or "未指定"
    subject = email_row.get("subject") or "(无主题)"
    sender = email_row.get("sender") or ""
    body_excerpt = (email_row.get("body_excerpt") or "").strip() or "(空)"
    sent_at = email_row.get("sent_at")
    sent_at_str = sent_at.strftime("%Y-%m-%d %H:%M") if sent_at else ""
    hours_text = "{:.1f}".format(float(hours_until)) if hours_until is not None else "未知"

    return (
        "[候选人临近改期待老板决策]\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "候选人：{name}（{tid}）\n"
        "当前阶段：{stage_cn}（{stage}）\n"
        "轮次：round={round_num}\n"
        "原面试时间：{interview_time}（距今 {hours_text} 小时）\n"
        "风险：候选人在面试前 24h 内请求改期，请老板判断是否接受。\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "AI 总结：{summary}\n"
        "候选人原因：{reason}\n"
        "候选人提议新时间：{new_time}\n"
        "邮件主题：{subject}\n"
        "发件人：{sender}\n"
        "时间：{sent_at}\n"
        "正文节选：\n{body}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "请老板三选一：\n"
        "1. 给新时间：在飞书回复新时间，agent 走改期 chain，保留候选人。\n"
        "2. 判定为鸽：回复明确拒绝并删档，agent 走 reject_delete（发拒信 + 物理删档）。\n"
        "3. 留人才池：回复留池，agent 走 reject_keep（发拒信 + 留池）。"
    ).format(
        name=candidate_name,
        tid=talent_id,
        stage_cn=stage_label,
        stage=stage,
        round_num=round_num,
        interview_time=interview_time,
        hours_text=hours_text,
        summary=summary,
        reason=reason,
        new_time=new_time,
        subject=subject,
        sender=sender,
        sent_at=sent_at_str,
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


def _send_feishu_all_inbound(text, no_feishu=False):
    # type: (str, bool) -> Dict[str, bool]
    """所有 inbound 邮件推老板；底层 send_text 会镜像给 Polaris。"""
    if no_feishu:
        print("[inbox.cmd_analyze][no-feishu][boss+polaris]\n{}".format(text),
              file=sys.stderr)
        return {"boss": True, "polaris": True}
    result = {"boss": False, "polaris": False}
    try:
        from lib import feishu
        result["boss"] = bool(feishu.send_text(text))
        result["polaris"] = result["boss"]
    except Exception as e:
        print("[inbox.cmd_analyze] 飞书推送异常: {}".format(e), file=sys.stderr)
    return result


def _format_llm_fail_card(email_row):
    # type: (Dict[str, Any]) -> str
    row = dict(email_row)
    return _format_feishu_card(row, {
        "intent": "other",
        "urgency": "medium",
        "summary": "LLM 分析失败，请人工查看邮件内容。",
        "details": {"email_id": row.get("email_id")},
        "need_boss_action": True,
    })


def _attachment_list(email_row):
    # type: (Dict[str, Any]) -> List[Dict[str, Any]]
    attachments = email_row.get("attachments") or []
    return attachments if isinstance(attachments, list) else []


def _saved_attachments(email_row):
    # type: (Dict[str, Any]) -> List[Dict[str, Any]]
    return [a for a in _attachment_list(email_row) if a and a.get("saved")]


def _format_attachment_line(email_row):
    # type: (Dict[str, Any]) -> str
    saved = _saved_attachments(email_row)
    if not saved:
        return ""
    names = []
    for a in saved[:5]:
        name = a.get("name") or a.get("path") or "(未命名附件)"
        size = a.get("size")
        if size:
            try:
                name = "{} ({:.1f}KB)".format(name, float(size) / 1024.0)
            except Exception:
                pass
        names.append(name)
    if len(saved) > 5:
        names.append("另 {} 个".format(len(saved) - 5))
    return "附件：{}\n".format("、".join(names))


def _attachment_context_for_llm(email_row):
    # type: (Dict[str, Any]) -> str
    saved = _saved_attachments(email_row)
    if not saved:
        return ""
    lines = ["", "\n[系统检测到本邮件真实附件，供意图判断使用]"]
    for a in saved[:10]:
        lines.append("- {name} | mime={mime} | size={size} | saved={saved}".format(
            name=a.get("name") or a.get("path") or "(未命名附件)",
            mime=a.get("mime") or "",
            size=a.get("size") or "",
            saved=a.get("saved"),
        ))
    return "\n".join(lines)


def _normalize_exam_submission_with_attachments(email_row, intent_result):
    # type: (Dict[str, Any], Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]
    if not intent_result:
        return intent_result
    if intent_result.get("intent") != "exam_submitted":
        return intent_result
    saved = _saved_attachments(email_row)
    if not saved:
        return intent_result
    details = dict(intent_result.get("details") or {})
    details["has_attachment"] = True
    details["attachments"] = [
        {
            "name": a.get("name"),
            "size": a.get("size"),
            "mime": a.get("mime"),
            "path": a.get("path"),
        }
        for a in saved[:10]
    ]
    intent_result["details"] = details
    if "未见" in (intent_result.get("summary") or "") or "未附" in (intent_result.get("summary") or ""):
        intent_result["summary"] = "候选人已回复笔试邮件并附上笔试答案附件。"
    return intent_result


def _maybe_run_exam_ai_review(email_row, intent_result, dry_run=False, no_feishu=False):
    # type: (Dict[str, Any], Dict[str, Any], bool, bool) -> Optional[Dict[str, Any]]
    """EXAM_SENT 收到笔试提交后，自动跑 rubric 驱动 AI 评审。"""
    if not intent_result or intent_result.get("intent") != "exam_submitted":
        return None
    stage = email_row.get("current_stage") or email_row.get("stage_at_receipt") or ""
    if stage != "EXAM_SENT":
        return None
    if not _saved_attachments(email_row):
        return None
    talent_id = (email_row.get("talent_id") or "").strip()
    if not talent_id:
        return {
            "triggered": False,
            "ok": False,
            "error": "missing_talent_id",
        }
    if dry_run:
        return {
            "triggered": True,
            "ok": True,
            "dry_run": True,
            "module": "exam.cmd_exam_ai_review",
            "args": ["--talent-id", talent_id, "--save-event"]
                    + ([] if no_feishu else ["--feishu"]),
        }

    args = ["--talent-id", talent_id, "--save-event"]
    if not no_feishu:
        args.append("--feishu")
    rc = run_module(
        "exam.cmd_exam_ai_review",
        args,
        timeout=_EXAM_REVIEW_TIMEOUT_SEC,
        parse_json=False,
    )
    return {
        "triggered": True,
        "ok": bool(rc.get("ok")),
        "returncode": rc.get("returncode"),
        "stderr": (rc.get("stderr") or "")[-1000:],
        "stdout_tail": (rc.get("stdout") or "")[-1000:],
        "cmd": rc.get("cmd"),
    }


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
        body=(email_row.get("body_full") or email_row.get("body_excerpt") or "")
             + _attachment_context_for_llm(email_row),
    )

    if result and (result.get("intent"), stage) in _STAGE_AWARE_NEED_BOSS:
        result["need_boss_action"] = True
    result = _normalize_exam_submission_with_attachments(email_row, result)
    result = _apply_reschedule_proximity_policy(email_row, result)

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

    # ── 推飞书（所有 inbound 推老板；lib.feishu 自动镜像 Polaris）──
    feishu_targets = {"boss": False, "polaris": False}
    text = _format_feishu_card(email_row, result) if result else _format_llm_fail_card(email_row)
    feishu_targets = _send_feishu_all_inbound(text, no_feishu=no_feishu)
    feishu_pushed = bool(feishu_targets.get("boss") and feishu_targets.get("polaris"))

    exam_review = _maybe_run_exam_ai_review(
        email_row, result, dry_run=dry_run, no_feishu=no_feishu)
    if exam_review and exam_review.get("triggered") and not exam_review.get("ok"):
        print("[inbox.cmd_analyze] 自动笔试评审失败 talent_id={}: {}".format(
            talent_id, exam_review.get("stderr") or exam_review.get("error") or "unknown"),
            file=sys.stderr)

    return {
        "email_id": email_id,
        "talent_id": talent_id,
        "intent": (result or {}).get("intent"),
        "summary": (result or {}).get("summary"),
        "need_boss_action": bool((result or {}).get("need_boss_action")),
        "feishu_pushed": feishu_pushed,
        "feishu_pushed_boss": bool(feishu_targets.get("boss")),
        "feishu_pushed_polaris": bool(feishu_targets.get("polaris")),
        "exam_review": exam_review,
        "exam_review_triggered": bool(exam_review and exam_review.get("triggered")),
        "exam_review_ok": bool(exam_review.get("ok")) if exam_review else None,
        "llm_ok": result is not None,
        "dry_run": bool(dry_run),
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
        "       e.body_excerpt, e.attachments, t.candidate_name, t.current_stage, "
        "       t.round1_time, t.round2_time "
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
    exam_review_fail = 0
    boss_action = 0

    for row in queue:
        try:
            r = _analyze_one(row, dry_run=args.dry_run, no_feishu=args.no_feishu)
            per_email.append(r)
            if not r.get("llm_ok"):
                llm_fail += 1
            if r.get("need_boss_action"):
                boss_action += 1
            # v3.8.6：每封 inbound 都应推老板 + Polaris；任一目标失败即计入。
            if (not r.get("feishu_pushed") and not args.dry_run
                    and not args.no_feishu):
                feishu_fail += 1
            if r.get("exam_review_triggered") and not r.get("exam_review_ok"):
                exam_review_fail += 1
        except Exception as e:
            print("[inbox.cmd_analyze] 处理 {} 失败: {}".format(row.get("email_id"), e),
                  file=sys.stderr)
            per_email.append({"email_id": row.get("email_id"), "error": str(e)[:200]})

    result = {
        "ok": feishu_fail == 0 and exam_review_fail == 0,
        "processed": len(per_email),
        "llm_fail": llm_fail,
        "need_boss_action": boss_action,
        "feishu_fail": feishu_fail,
        "exam_review_fail": exam_review_fail,
        "dry_run": bool(args.dry_run),
        "per_email": per_email,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str))
    else:
        print(("[inbox.cmd_analyze] processed={} need_boss={} llm_fail={} "
               "feishu_fail={} exam_review_fail={}{}").format(
            result["processed"], result["need_boss_action"],
            result["llm_fail"], result["feishu_fail"], result["exam_review_fail"],
            " [DRY-RUN]" if args.dry_run else ""))
    if feishu_fail and not args.dry_run and not args.no_feishu:
        print("[inbox.cmd_analyze] 飞书通知投递失败：{} 封 inbound 未同时送达老板+Polaris".format(
            feishu_fail), file=sys.stderr)
        return 3
    if exam_review_fail:
        print("[inbox.cmd_analyze] 自动笔试评审失败：{} 封 inbound 未完成评审".format(
            exam_review_fail), file=sys.stderr)
        return 4
    return 0


def main(argv=None):
    args = _build_parser().parse_args(argv)
    return _do_analyze(args)


if __name__ == "__main__":
    run_with_self_verify("inbox.cmd_analyze", main)
