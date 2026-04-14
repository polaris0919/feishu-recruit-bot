#!/usr/bin/env python3
"""
合并后的面试结果脚本：一面/二面统一处理。
用法：
  python3 interview/cmd_result.py --talent-id t_xxx --round 1|2 --result pass|reject_keep|reject_delete [--notes ...]

Round 1 额外选项：
  --result pass         → 发笔试邮件
  --result pass_direct  → 跳过笔试直接二面

Round 2 额外选项：
  --result pending      → 暂保留结论
"""
import os, sys
_LIB = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import argparse
import subprocess
from datetime import datetime

from bg_helpers import send_bg_email, spawn_calendar
from core_state import (
    append_audit, ensure_stage_transition, load_candidate, save_candidate,
)
from recruit_paths import exam_archive_dir
from side_effect_guard import side_effects_disabled


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
        return True
    script = os.path.normpath(os.path.join(
        os.path.dirname(__file__), "..", "..", "email-send", "scripts", "email_send.py"))
    if not os.path.isfile(script):
        print("[result] email_send.py 未找到", file=sys.stderr)
        return False
    subject = "【笔试邀请】致邃投资 技术岗位笔试"
    body = (
        "您好，{name}，\n\n感谢您参加我们的初步面试！\n\n"
        "我们诚邀您完成一份技术笔试。\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n📋 笔试说明\n━━━━━━━━━━━━━━━━━━━━\n"
        "· 题目已作为附件随本邮件发送\n"
        "· 完成后请直接回复本邮件\n"
        "· 建议完成时间：3~5 个工作日内\n\n"
        "期待您的回复！\n\n致邃投资 招聘团队"
    ).format(name=candidate_name or "您")
    attachments = _get_exam_attachments()
    cmd = ["python3", script, "--to", candidate_email, "--subject", subject, "--body", body]
    for f in attachments:
        cmd += ["--attachment", f]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return result.returncode == 0
    except Exception as e:
        print("[result] 发邮件失败: {}".format(e), file=sys.stderr)
        return False


def _handle_reject_delete(talent_id, round_num, notes):
    import talent_db as _tdb
    if _tdb._is_enabled():
        try:
            _tdb.delete_talent(talent_id)
        except Exception as e:
            print("⚠ DB 删除失败: {}".format(e), file=sys.stderr)
    round_label = "一面" if round_num == 1 else "二面"
    lines = [
        "[{}结果已记录]".format(round_label),
        "- talent_id: {}".format(talent_id),
        "- 结果: 未通过（已从人才库彻底删除）",
    ]
    if notes:
        lines.append("- 评价: {}".format(notes))
    print("\n".join(lines))
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="处理面试结果（一面/二面通用）")
    p.add_argument("--talent-id", required=True)
    p.add_argument("--round", type=int, required=True, choices=[1, 2])
    p.add_argument("--result", required=True,
                   choices=["pass", "pass_direct", "pending", "reject_keep", "reject_delete"])
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
            ok = ensure_stage_transition(cand, allowed_from, "EXAM_SENT")
            if not ok:
                print("ERROR: 阶段 {} 不允许 round1 pass".format(current_stage), file=sys.stderr)
                return 1
            cand["candidate_email"] = args.email.strip()
            exam_id = "exam-{}-{}".format(talent_id, datetime.now().strftime("%Y%m%d%H%M%S"))
            cand["exam_id"] = exam_id
            cand["exam_sent_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")
            email_ok = None if args.skip_email else _send_exam_email(
                talent_id, args.email, exam_id, cand.get("candidate_name", ""))
            append_audit(cand, args.actor, "round1_pass_and_exam_invite_sent",
                         {"email": args.email, "exam_id": exam_id, "email_sent": email_ok, "notes": args.notes})
            save_candidate(talent_id, cand)
            status = "已跳过" if args.skip_email else ("已发送" if email_ok else "发送失败")
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
            ok = ensure_stage_transition(cand, allowed_from, "ROUND1_DONE_REJECT_KEEP")
            if not ok:
                print("ERROR: 阶段 {} 不允许 reject_keep".format(current_stage), file=sys.stderr)
                return 1
            append_audit(cand, args.actor, "round1_result_reject_keep", {"notes": args.notes})
            save_candidate(talent_id, cand)
            print("[一面结果已记录]\n- talent_id: {}\n- 结果: 未通过（保留人才库）".format(talent_id))
            return 0

        else:  # reject_delete
            if current_stage not in allowed_from:
                print("ERROR: 阶段 {} 不允许 reject_delete".format(current_stage), file=sys.stderr)
                return 1
            return _handle_reject_delete(talent_id, 1, args.notes)

    # ── Round 2 ──
    allowed_from = {"ROUND2_SCHEDULED", "ROUND2_DONE_PENDING", "ROUND2_DONE_PASS"}

    if result == "pending":
        ok = ensure_stage_transition(cand, {"ROUND2_SCHEDULED", "ROUND2_DONE_PENDING"}, "ROUND2_DONE_PENDING")
        if not ok:
            print("ERROR: 阶段 {} 不允许 pending".format(current_stage), file=sys.stderr)
            return 1
        cand["round2_time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        cand["round2_reminded_at"] = None
        append_audit(cand, args.actor, "round2_done_pending", {"notes": args.notes})
        save_candidate(talent_id, cand)
        print("[二面已结束，暂保留结论]\n- talent_id: {}\n- 状态: ROUND2_DONE_PENDING".format(talent_id))
        return 0

    elif result == "pass":
        ok = ensure_stage_transition(cand, allowed_from, "OFFER_HANDOFF")
        if not ok:
            print("ERROR: 阶段 {} 不允许 round2 pass".format(current_stage), file=sys.stderr)
            return 1
        append_audit(cand, args.actor, "round2_pass_offer_handoff", {"notes": args.notes})
        save_candidate(talent_id, cand)
        import feishu
        hr_msg = "[Offer 处理通知]\n候选人 {name}（{tid}）已通过二面\n邮箱：{email}\n请给该候选人发放offer".format(
            name=cand.get("candidate_name", talent_id), tid=talent_id,
            email=cand.get("candidate_email", "未记录"))
        feishu.send_text_to_hr(hr_msg)
        print("[二面结果已记录]\n- talent_id: {}\n- 结果: 通过\n- 阶段: OFFER_HANDOFF".format(talent_id))
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
        return _handle_reject_delete(talent_id, 2, args.notes)


if __name__ == "__main__":
    raise SystemExit(main())
