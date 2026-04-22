#!/usr/bin/env python3
"""
合并后的面试结果脚本：一面/二面统一处理。
用法：
  python3 interview/cmd_result.py --talent-id t_xxx --round 1|2 --result pass|reject_keep|reject_delete [--notes ...]

Round 1 选项：
  --result pass         → 发笔试邮件
  --result pass_direct  → 跳过笔试直接二面
  --result reject_delete → 一面未通过，发拒信 + 直接 talent_db.delete_talent()
                           （v3.6 起不再经停 ROUND1_DONE_REJECT_DELETE 这个"占位 stage"）

Round 2 选项：
  --result pass         → 推到 POST_OFFER_FOLLOWUP + 通知 HR 准备发 offer
                           （v3.6 起合并了 OFFER_HANDOFF 瞬时态）
  --result reject_keep  → 二面未通过，保留人才库（ROUND2_DONE_REJECT_KEEP）
  --result reject_delete → 二面未通过，发拒信 + 直接物理删除
  （二面没有 pending：老板未做决定时让候选人停留在 ROUND2_SCHEDULED 即可）
"""
import os
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

from lib.bg_helpers import send_bg_email
from lib.core_state import (
    append_audit, ensure_stage_transition, load_candidate, save_candidate,
)
from lib.recruit_paths import exam_archive_dir
from lib.side_effect_guard import side_effects_disabled


def _get_exam_attachments():
    archive = str(exam_archive_dir())
    exam_files = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "exam_files"))
    candidates = [
        os.path.join(archive, "笔试题.tar.gz"),
        os.path.join(archive, "笔试题.zip"),
        os.path.join(exam_files, "exam_package.zip"),
        os.path.join(archive, "笔试题.tar"),
    ]
    for path in candidates:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return [path]
    return []


def _send_exam_email(talent_id, candidate_email, exam_id, candidate_name=""):
    if side_effects_disabled():
        return send_bg_email(candidate_email, "", "", tag="round1_exam_invite",
                             talent_id=talent_id, candidate_name=candidate_name)
    from email_templates import renderer
    from email_templates.constants import COMPANY
    subject, body = renderer.render(
        "exam_invite",
        candidate_name=candidate_name or "您",
        company=COMPANY,
        talent_id=talent_id,
    )
    attachments = _get_exam_attachments()
    try:
        return send_bg_email(
            candidate_email,
            subject,
            body,
            tag="round1_exam_invite",
            attachments=attachments,
            talent_id=talent_id,
            candidate_name=candidate_name,
        )
    except Exception as e:
        print("[result] 发邮件失败: {}".format(e), file=sys.stderr)
        return None


def _send_rejection_email(talent_id, candidate_email, candidate_name, tag):
    # type: (str, str, str, str) -> int
    """手动 reject_delete 时同步发拒信。

    历史 gap：2026-04-22 之前 _handle_reject_delete 只删 DB 不发拒信，
    候选人不知道结果。这里走 rejection_generic 模板，与 auto_reject 走同样
    的拒信体系，口径统一。
    """
    if not _is_valid_email(candidate_email):
        return -1
    if side_effects_disabled():
        return send_bg_email(candidate_email, "", "", tag=tag,
                             talent_id=talent_id, candidate_name=candidate_name)
    from email_templates import renderer
    from email_templates.constants import COMPANY
    subject, body = renderer.render(
        "rejection_generic",
        candidate_name=candidate_name or "您",
        company=COMPANY,
        talent_id=talent_id,
    )
    try:
        return send_bg_email(candidate_email, subject, body,
                             tag=tag, talent_id=talent_id,
                             candidate_name=candidate_name)
    except Exception as e:
        print("[result] 发拒信失败: {}".format(e), file=sys.stderr)
        return -1


def _handle_reject_delete(talent_id, round_num, notes, skip_email=False):
    from lib import talent_db as _tdb
    round_label = "一面" if round_num == 1 else "二面"

    cand = _tdb.get_one(talent_id) if _tdb._is_enabled() else None
    candidate_email = (cand.get("candidate_email") or "").strip() if cand else ""
    candidate_name = (cand.get("candidate_name") or "").strip() if cand else ""

    email_pid = None
    if not skip_email and candidate_email:
        email_pid = _send_rejection_email(
            talent_id, candidate_email, candidate_name,
            tag="round{}_reject_delete".format(round_num),
        )

    if _tdb._is_enabled():
        try:
            _tdb.delete_talent(talent_id)
        except Exception as e:
            print("⚠ DB 删除失败: {}".format(e), file=sys.stderr)

    lines = [
        "[{}结果已记录]".format(round_label),
        "- talent_id: {}".format(talent_id),
        "- 结果: 未通过（已从人才库彻底删除）",
    ]
    if email_pid is not None and email_pid >= 0:
        lines.append("- 拒信: 后台发送中（PID={}）".format(email_pid))
    elif skip_email:
        lines.append("- 拒信: 跳过（--skip-email）")
    elif not candidate_email:
        lines.append("- 拒信: 跳过（候选人无邮箱）")
    if notes:
        lines.append("- 评价: {}".format(notes))
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
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    talent_id = args.talent_id.strip()
    result = args.result
    round_num = args.round

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
            ok = ensure_stage_transition(cand, allowed_from, "EXAM_SENT")
            if not ok:
                print("ERROR: 阶段 {} 不允许 round1 pass".format(current_stage), file=sys.stderr)
                return 1
            cand["candidate_email"] = args.email.strip()
            exam_id = "exam-{}-{}".format(talent_id, datetime.now().strftime("%Y%m%d%H%M%S"))
            cand["exam_id"] = exam_id
            cand["exam_sent_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")
            email_pid = None if args.skip_email else _send_exam_email(
                talent_id, args.email, exam_id, cand.get("candidate_name", ""))
            append_audit(cand, args.actor, "round1_pass_and_exam_invite_sent",
                         {"email": args.email, "exam_id": exam_id, "email_queued": bool(email_pid), "notes": args.notes})
            save_candidate(talent_id, cand)
            if args.skip_email:
                status = "已跳过"
            elif email_pid:
                status = "发送中（后台 PID={}）".format(email_pid)
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
            return _handle_reject_delete(talent_id, 1, args.notes,
                                         skip_email=args.skip_email)

    # ── Round 2 ──
    allowed_from = {"ROUND2_SCHEDULED"}

    if result == "pass":
        # v3.6 (2026-04-27)：OFFER_HANDOFF 瞬时态合并入 POST_OFFER_FOLLOWUP。
        # 直接一步推到 POST_OFFER_FOLLOWUP，HR 通知不变（依然 send_text_to_hr）。
        ok = ensure_stage_transition(cand, allowed_from, "POST_OFFER_FOLLOWUP")
        if not ok:
            print("ERROR: 阶段 {} 不允许 round2 pass".format(current_stage), file=sys.stderr)
            return 1
        append_audit(cand, args.actor, "round2_pass_enter_post_offer_followup",
                     {"notes": args.notes})
        save_candidate(talent_id, cand)
        from lib import feishu
        hr_msg = "[Offer 处理通知]\n候选人 {name}（{tid}）已通过二面\n邮箱：{email}\n请给该候选人发放offer".format(
            name=cand.get("candidate_name", talent_id), tid=talent_id,
            email=cand.get("candidate_email", "未记录"))
        hr_notify_ok = feishu.send_text_to_hr(hr_msg)
        if not hr_notify_ok:
            print(
                "WARN: HR Feishu 通知投递失败（候选人 {} 已 round2 pass 但 HR 可能不知情）。"
                "请手动确认 HR 收到，或用 feishu.send_text_to_hr 重发并记录 hr_offer_reminder_resent 事件。".format(talent_id),
                file=sys.stderr,
            )
        print("[二面结果已记录]\n- talent_id: {}\n- 结果: 通过\n- 阶段: POST_OFFER_FOLLOWUP（已结束面试流程，等待发放 Offer / 沟通入职）".format(talent_id))
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
        return _handle_reject_delete(talent_id, 2, args.notes,
                                     skip_email=args.skip_email)


if __name__ == "__main__":
    raise SystemExit(main())
