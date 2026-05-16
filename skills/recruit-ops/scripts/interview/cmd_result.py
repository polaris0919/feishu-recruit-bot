#!/usr/bin/env python3
"""
合并后的面试结果脚本：一面/二面统一处理。
用法：
  python3 interview/cmd_result.py --talent-id t_xxx --round 1|2 --result pass|reject_keep|reject_delete [--notes ...]

Round 1 选项：
  --result pass         → 笔试邮件发送成功后推进到 EXAM_SENT
  --result pass_direct  → 跳过笔试直接二面
  --result reject_delete → 一面未通过，发拒信 + talent.cmd_delete 归档删档

Round 2 选项：
  --result pass         → 推到 POST_OFFER_FOLLOWUP + 提醒老板确认入职前邮件
                           （v3.6 起合并了 OFFER_HANDOFF 瞬时态）
  --result reject_keep  → 二面未通过，保留人才库（ROUND2_DONE_REJECT_KEEP）
  --result reject_delete → 二面未通过，发拒信 + talent.cmd_delete 归档删档
  （二面没有 pending：老板未做决定时让候选人停留在 ROUND2_SCHEDULED 即可）
"""
import re
import sys

import argparse
from datetime import datetime

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _is_valid_email(value):
    # type: (str) -> bool
    """严格的邮箱格式校验，防止上层（如 hermes / 飞书 prompt）误传占位符 / 模板字面量。

    历史事故：04-20 闵思涵案，--email 被传成了字面字符串 '笔试邀请邮件内容'，
    SMTP 投递了一封根本送不到任何人的邮件，audit 却显示已发。"""
    if not value:
        return False
    if len(value) > 254:
        return False
    return bool(_EMAIL_RE.match(value.strip()))

from lib.bg_helpers import send_outbound_template
from lib.cli_subprocess import run_module
from lib.core_state import (
    append_audit, ensure_stage_transition, load_candidate, save_candidate,
)


def _send_exam_email(talent_id, candidate_email, exam_id, candidate_name=""):
    from email_templates.auto_attachments import auto_attachments_for
    try:
        attachments = [str(p) for p in auto_attachments_for("exam_invite")]
    except RuntimeError as e:
        print("[result] 笔试题附件缺失，拒绝发邀请：{}".format(e), file=sys.stderr)
        return None
    res = send_outbound_template(
        talent_id=talent_id,
        template="exam_invite",
        attachments=attachments,
    )
    if res.get("ok"):
        return res
    print("[result] 发笔试邀请失败: {}".format(
        res.get("stderr") or res.get("stdout") or res.get("returncode")), file=sys.stderr)
    return None


def _send_rejection_email(talent_id, candidate_email, candidate_name, tag):
    # type: (str, str, str, str) -> dict
    """手动 reject_delete 时同步发拒信。

    历史 gap：2026-04-22 之前 _handle_reject_delete 只删 DB 不发拒信，
    候选人不知道结果。这里走 rejection_generic 模板，与 auto_reject 走同样
    的拒信体系，口径统一。
    """
    if not _is_valid_email(candidate_email):
        return {"ok": False, "error": "invalid_email"}
    res = send_outbound_template(
        talent_id=talent_id,
        template="rejection_generic",
        context="rejection",
    )
    if res.get("ok"):
        return res
    print("[result] 发拒信失败: {}".format(
        res.get("stderr") or res.get("stdout") or res.get("returncode")), file=sys.stderr)
    return {"ok": False, "error": res.get("stderr") or res.get("stdout") or res.get("returncode")}


def _cmd_delete(talent_id, reason, actor):
    # type: (str, str, str) -> dict
    return run_module(
        "talent.cmd_delete",
        [
            "--talent-id", talent_id,
            "--confirm-delete-talent", talent_id,
            "--reason", reason,
            "--actor", actor,
            "--json",
        ],
        parse_json=True,
    )


def _notify_boss_offer_prompt(cand, talent_id):
    # type: (dict, str) -> bool
    from lib import feishu
    boss_msg = (
        "[二面通过 · 请确认入职前邮件]\n"
        "候选人 {name}（{tid}）已通过二面，状态已进入 POST_OFFER_FOLLOWUP。\n"
        "邮箱：{email}\n\n"
        "是否现在发送入职前邮件（含实习协议 + 入职信息登记表）？\n"
        "请提供：\n"
        "1. 入职时间\n"
        "2. 开始日薪（默认 350 元/天；如认可默认值可直接说按 350）\n\n"
        "示例：确认发送，入职时间 2026-06-01，日薪 350。"
    ).format(
        name=cand.get("candidate_name", talent_id), tid=talent_id,
        email=cand.get("candidate_email", "未记录"))
    return bool(feishu.send_text(boss_msg))


def _handle_reject_delete(talent_id, round_num, notes, actor, skip_email=False):
    from lib import talent_db as _tdb
    round_label = "一面" if round_num == 1 else "二面"

    cand = _tdb.get_one(talent_id) if _tdb._is_enabled() else None
    candidate_email = (cand.get("candidate_email") or "").strip() if cand else ""
    candidate_name = (cand.get("candidate_name") or "").strip() if cand else ""

    email_res = None
    if not skip_email and candidate_email:
        email_res = _send_rejection_email(
            talent_id, candidate_email, candidate_name,
            tag="round{}_reject_delete".format(round_num),
        )
        if not (isinstance(email_res, dict) and email_res.get("ok")):
            print(
                "ERROR: 拒信未发送，未执行删档。detail={}".format(
                    email_res.get("error") if isinstance(email_res, dict) else email_res),
                file=sys.stderr,
            )
            return 1

    reason = "{}未通过 reject_delete".format(round_label)
    if notes:
        reason += "；{}".format(notes)
    delete_res = _cmd_delete(talent_id, reason, actor)
    if not delete_res.get("ok"):
        print(
            "ERROR: 删档失败，候选人未确认删除。returncode={} stderr={} stdout={}".format(
                delete_res.get("returncode"),
                (delete_res.get("stderr") or "").strip()[:500],
                (delete_res.get("stdout") or "").strip()[:500],
            ),
            file=sys.stderr,
        )
        return 1

    lines = [
        "[{}结果已记录]".format(round_label),
        "- talent_id: {}".format(talent_id),
        "- 结果: 未通过（已从人才库彻底删除）",
    ]
    if isinstance(email_res, dict) and email_res.get("ok"):
        lines.append("- 拒信: 已发送（message_id={}）".format(
            email_res.get("message_id") or "?"))
    elif skip_email:
        lines.append("- 拒信: 跳过（--skip-email）")
    elif not candidate_email:
        lines.append("- 拒信: 跳过（候选人无邮箱）")
    if notes:
        lines.append("- 评价: {}".format(notes))
    if (delete_res.get("json") or {}).get("archive_path"):
        lines.append("- 归档: {}".format((delete_res.get("json") or {}).get("archive_path")))
    print("\n".join(lines))
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="处理面试结果（一面/二面通用）")
    p.add_argument("--talent-id", required=True)
    p.add_argument("--round", type=int, required=True, choices=[1, 2])
    p.add_argument("--result", required=True,
                   choices=["pass", "pass_direct", "reject_keep", "reject_delete"])
    p.add_argument("--email", default="")
    p.add_argument("--round2-time", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--skip-email", action="store_true")
    p.add_argument("--actor", default="system")

    # ── v3.8.1 hard guard（事故源 INCIDENT_RULES.md §12 / §13） ────────────
    # --result reject_delete 等价于 talent.cmd_delete（CLI 内部会发拒信 +
    # 物理删档），同样不可逆。强制 caller 显式传 --confirm-reject-delete
    # <talent_id>，且仅当 --result=reject_delete 时才校验。
    p.add_argument("--confirm-reject-delete",
                   default=None, metavar="<talent_id>",
                   help="必填 hard guard（仅当 --result=reject_delete）："
                        "值必须严格等于 --talent-id。防止 LLM 把自然语言里的"
                        "'拒了/不要了/删了'误识别为已 confirm。"
                        "事故源 INCIDENT_RULES.md §12 / §13。")

    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    talent_id = args.talent_id.strip()
    result = args.result
    round_num = args.round

    # ── v3.8.1 hard guard（事故源 INCIDENT_RULES.md §12 / §13） ────────────
    if result == "reject_delete":
        if not args.confirm_reject_delete:
            print(
                "ERROR: --result reject_delete 缺失 --confirm-reject-delete。\n"
                "       reject_delete 会发拒信 + 物理删档,不可逆。必须把 talent_id 写两遍才能跑。\n"
                "       正确用法：interview.cmd_result --talent-id {tid} --round {r} --result reject_delete \\\n"
                "                  --confirm-reject-delete {tid} ...\n"
                "       事故源 INCIDENT_RULES.md §12 / §13。"
                .format(tid=talent_id, r=round_num),
                file=sys.stderr,
            )
            return 1
        if args.confirm_reject_delete != talent_id:
            print(
                "ERROR: --confirm-reject-delete 与 --talent-id 不匹配:\n"
                "         --talent-id              = {tid}\n"
                "         --confirm-reject-delete  = {confirm}\n"
                "       两者必须严格相等。"
                .format(tid=talent_id, confirm=args.confirm_reject_delete),
                file=sys.stderr,
            )
            return 1

    cand = load_candidate(talent_id)
    if not cand:
        print("ERROR: 未找到候选人 {}".format(talent_id), file=sys.stderr)
        return 1
    current_stage = cand.get("stage") or "NEW"

    # ── Round 1 ──
    if round_num == 1:
        allowed_from = {"NEW", "ROUND1_SCHEDULED"}

        if result == "pass":
            if not args.email:
                print("ERROR: --result pass 需要提供 --email", file=sys.stderr)
                return 1
            if not _is_valid_email(args.email):
                print(
                    "ERROR: --email 不是合法邮箱地址: {!r}\n"
                    "       这是候选人邮箱（SMTP 收件人），不是邮件正文。"
                    "       如果不知道该填什么，请先用 cmd_status / cmd_search 查 candidate_email，"
                    "       或加 --skip-email 仅做状态推进。".format(args.email),
                    file=sys.stderr,
                )
                return 1
            if current_stage not in allowed_from:
                print("ERROR: 阶段 {} 不允许 round1 pass".format(current_stage), file=sys.stderr)
                return 1
            exam_id = "exam-{}-{}".format(talent_id, datetime.now().strftime("%Y%m%d%H%M%S"))
            email_res = None
            if not args.skip_email:
                email_res = _send_exam_email(
                    talent_id, args.email, exam_id, cand.get("candidate_name", ""))
                if not (isinstance(email_res, dict) and email_res.get("ok")):
                    print(
                        "ERROR: 笔试邀请未发送，候选人仍停留在 {}。".format(current_stage),
                        file=sys.stderr,
                    )
                    return 1
            ok = ensure_stage_transition(cand, allowed_from, "EXAM_SENT")
            if not ok:
                print("ERROR: 阶段 {} 不允许 round1 pass".format(current_stage), file=sys.stderr)
                return 1
            cand["candidate_email"] = args.email.strip()
            cand["exam_id"] = exam_id
            cand["exam_sent_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")
            append_audit(cand, args.actor, "round1_pass_and_exam_invite_sent",
                         {"email": args.email, "exam_id": exam_id,
                          "notes": args.notes, "skip_email": bool(args.skip_email)})
            save_candidate(talent_id, cand)
            if args.skip_email:
                status = "已跳过"
            elif isinstance(email_res, dict) and email_res.get("ok"):
                status = "已发送（message_id={}）".format(
                    email_res.get("message_id") or "?")
            else:
                status = "发送失败"
            lines = [
                "[一面结果已记录]", "- talent_id: {}".format(talent_id),
                "- 结果: 一面通过", "- 笔试邀请: {}".format(status),
                "- 笔试 ID: {}".format(exam_id), "- 阶段: EXAM_SENT",
            ]
            if args.notes:
                lines.insert(3, "- 一面评价: {}".format(args.notes))
            print("\n".join(lines))
            return 0

        elif result == "pass_direct":
            if not args.round2_time:
                print("ERROR: pass_direct 需要 --round2-time", file=sys.stderr)
                return 1
            ok = ensure_stage_transition(cand, allowed_from, "ROUND2_SCHEDULING")
            if not ok:
                print("ERROR: 阶段 {} 不允许 pass_direct".format(current_stage), file=sys.stderr)
                return 1
            cand["round2_time"] = args.round2_time.strip()
            cand["round2_confirm_status"] = "PENDING"
            if args.email:
                if not _is_valid_email(args.email):
                    print(
                        "ERROR: --email 不是合法邮箱地址: {!r}（pass_direct 路径）".format(args.email),
                        file=sys.stderr,
                    )
                    return 1
                cand["candidate_email"] = args.email.strip()
            append_audit(cand, args.actor, "round1_pass_direct_round2_scheduled", {
                "round2_time": args.round2_time, "notes": args.notes})
            save_candidate(talent_id, cand)
            lines = [
                "[一面结果已记录]", "- talent_id: {}".format(talent_id),
                "- 结果: 一面通过（直接二面）",
                "- 二面时间: {}".format(args.round2_time),
                "- 二面形式: 线下面试（统一）",
                "- 阶段: ROUND2_SCHEDULING",
            ]
            print("\n".join(lines))
            return 0

        elif result == "reject_keep":
            print(
                "ERROR: 一面未通过不再支持 reject_keep（人才池统一收口）。"
                "请用 --result reject_delete 直接删除该候选人。",
                file=sys.stderr,
            )
            return 1

        else:  # reject_delete
            if current_stage not in allowed_from:
                print("ERROR: 阶段 {} 不允许 reject_delete".format(current_stage), file=sys.stderr)
                return 1
            return _handle_reject_delete(talent_id, 1, args.notes, args.actor,
                                         skip_email=args.skip_email)

    # ── Round 2 ──
    allowed_from = {"ROUND2_SCHEDULED"}

    if result == "pass":
        # v3.6 (2026-04-27)：OFFER_HANDOFF 瞬时态合并入 POST_OFFER_FOLLOWUP。
        # v3.9：二面通过后不再立即通知 HR，而是先请老板确认入职前邮件。
        ok = ensure_stage_transition(cand, allowed_from, "POST_OFFER_FOLLOWUP")
        if not ok:
            print("ERROR: 阶段 {} 不允许 round2 pass".format(current_stage), file=sys.stderr)
            return 1
        append_audit(cand, args.actor, "round2_pass_enter_post_offer_followup",
                     {"notes": args.notes})
        save_candidate(talent_id, cand)
        # 飞书是内部提醒：状态已落库后再推送；推送失败不回滚候选人状态。
        boss_notify_ok = _notify_boss_offer_prompt(cand, talent_id)
        if not boss_notify_ok:
            print(
                "WARN: Boss Feishu 入职前邮件确认提示投递失败（候选人 {} 已 round2 pass）。"
                "状态已保持 POST_OFFER_FOLLOWUP，不回滚。请手动提醒老板确认入职时间和日薪。".format(talent_id),
                file=sys.stderr,
            )
        print(
            "[二面结果已记录]\n"
            "- talent_id: {}\n"
            "- 结果: 通过\n"
            "- 阶段: POST_OFFER_FOLLOWUP（已结束面试流程，等待老板确认入职前邮件）\n"
            "- 下一步: 请老板确认是否发送入职前邮件，并提供入职时间与日薪（默认 350 元/天）"
            .format(talent_id))
        return 0

    elif result == "reject_keep":
        ok = ensure_stage_transition(cand, allowed_from, "ROUND2_DONE_REJECT_KEEP")
        if not ok:
            print("ERROR: 阶段 {} 不允许 reject_keep".format(current_stage), file=sys.stderr)
            return 1
        append_audit(cand, args.actor, "round2_reject_keep", {"notes": args.notes})
        save_candidate(talent_id, cand)
        print("[二面结果已记录]\n- talent_id: {}\n- 结果: 未通过（保留人才库）".format(talent_id))
        return 0

    else:  # reject_delete
        if current_stage not in allowed_from:
            print("ERROR: 阶段 {} 不允许 reject_delete".format(current_stage), file=sys.stderr)
            return 1
        return _handle_reject_delete(talent_id, 2, args.notes, args.actor,
                                     skip_email=args.skip_email)


if __name__ == "__main__":
    raise SystemExit(main())
