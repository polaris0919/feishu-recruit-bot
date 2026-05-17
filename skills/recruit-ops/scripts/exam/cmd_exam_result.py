#!/usr/bin/env python3

"""
处理 /exam_result 命令：
  - result=pass        → 二面通知邮件发送成功后推进到 ROUND2_SCHEDULING
  - result=reject_keep → 状态推进到 EXAM_REJECT_KEEP（笔试未通过，保留人才库）
  - result=reject_delete → 发拒信后调用 talent.cmd_delete 归档删档
"""
import argparse
import sys
from datetime import datetime
from typing import List, Optional
from lib.bg_helpers import send_outbound_template
from lib.cli_subprocess import run_module

from lib.core_state import (
    append_audit,
    ensure_stage_transition,
    load_candidate,
    save_candidate,
)


def send_round2_notification(
    to_email,
    talent_id,
    round2_time,
    company="",
    candidate_name="",
):
    # type: (str, str, str, str, str) -> dict
    """通过 outbound.cmd_send 发送二面通知邮件。

    候选人语言里这是"第三轮"——一面=第一轮、笔试=第二轮、二面=第三轮。
    模板见 email_templates/round2_invite.txt。
    """
    return send_outbound_template(
        talent_id=talent_id,
        template="round2_invite",
        context="round2",
        vars={"round2_time": round2_time or "待定，HR 将另行通知"},
    )


def send_rejection_notification(talent_id):
    # type: (str) -> dict
    return send_outbound_template(
        talent_id=talent_id,
        template="rejection_generic",
        context="rejection",
    )


def delete_talent_archive(talent_id, reason, actor):
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


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="处理 /exam_result 命令")
    p.add_argument("--talent-id", required=True, help="候选人唯一标识 talent_id")
    p.add_argument(
        "--result",
        required=True,
        choices=["pass", "reject_keep", "reject_delete"],
        help="笔试结果：pass / reject_keep / reject_delete",
    )
    p.add_argument(
        "--round2-time",
        default="",
        help="二面时间（result=pass 时建议填写，例如 2026-03-20 14:00）",
    )
    p.add_argument(
        "--notes",
        default="",
        help="备注（写入审计日志，不单独落库）",
    )
    p.add_argument(
        "--actor",
        default="system",
        help="执行人（用于审计，建议传入 HR 的飞书 user_id）",
    )

    # ── v3.8.1 hard guard（事故源 INCIDENT_RULES.md §12 / §13） ────────────
    p.add_argument(
        "--confirm-reject-delete",
        default=None, metavar="<talent_id>",
        help="必填 hard guard（仅当 --result=reject_delete）："
             "值必须严格等于 --talent-id。事故源 INCIDENT_RULES.md §12 / §13。",
    )

    return p.parse_args(argv)


def main(argv=None):
    # type: (Optional[List[str]]) -> int
    args = parse_args(argv or sys.argv[1:])
    talent_id = args.talent_id.strip()
    result = args.result

    # ── v3.8.1 hard guard（事故源 INCIDENT_RULES.md §12 / §13） ────────────
    if result == "reject_delete":
        if not args.confirm_reject_delete:
            print(
                "ERROR: --result reject_delete 缺失 --confirm-reject-delete。\n"
                "       reject_delete 会发拒信 + 物理删档,不可逆。必须把 talent_id 写两遍才能跑。\n"
                "       正确用法：exam.cmd_exam_result --talent-id {tid} --result reject_delete \\\n"
                "                  --confirm-reject-delete {tid} ...\n"
                "       事故源 INCIDENT_RULES.md §12 / §13。"
                .format(tid=talent_id),
                file=sys.stderr,
            )
            return 1
        if args.confirm_reject_delete != talent_id:
            print(
                "ERROR: --confirm-reject-delete 与 --talent-id 不匹配。\n"
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

    if result == "pass":
        if not (args.round2_time or "").strip():
            print("ERROR: result=pass 时必须提供 --round2-time，禁止复用旧的二面时间。", file=sys.stderr)
            return 1
        allowed_from = {"EXAM_SENT", "EXAM_REVIEWED"}
        if current_stage not in allowed_from:
            print(
                f"ERROR: 当前阶段 {current_stage} 不允许执行 exam_result=pass。"
                f"（需要处于 EXAM_SENT / EXAM_REVIEWED 阶段）",
                file=sys.stderr,
            )
            return 1

        candidate_email = cand.get("candidate_email", "")
        if not candidate_email:
            print("ERROR: 候选人邮箱未记录，无法发送二面通知邮件，未推进状态。", file=sys.stderr)
            return 1

        email_res = send_round2_notification(
            to_email=candidate_email,
            talent_id=talent_id,
            round2_time=args.round2_time,
            company="致邃投资",
            candidate_name=cand.get("candidate_name", ""),
        )
        if not (isinstance(email_res, dict) and email_res.get("ok")):
            print(
                "ERROR: 二面通知邮件发送失败，候选人仍停留在 {}。detail={}".format(
                    current_stage,
                    email_res.get("stderr") or email_res.get("stdout") or email_res.get("returncode")
                    if isinstance(email_res, dict) else email_res,
                ),
                file=sys.stderr,
            )
            return 1

        ok = ensure_stage_transition(cand, allowed_from, "ROUND2_SCHEDULING")
        if not ok:
            print(
                f"ERROR: 当前阶段 {current_stage} 不允许执行 exam_result=pass。",
                file=sys.stderr,
            )
            return 1

        if args.round2_time:
            cand["round2_time"] = args.round2_time
            cand["round2_confirm_status"] = "PENDING"
            cand["round2_invite_sent_at"] = datetime.now().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S+08:00")
            cand["round2_calendar_event_id"] = None
            cand["wait_return_round"] = None

        append_audit(
            cand,
            actor=args.actor,
            action="exam_result_pass_round2_scheduling",
            payload={
                "round2_time": args.round2_time,
                "notification_sent_to": candidate_email,
                "notes": args.notes,
            },
        )

        lines = [
            "[笔试结果已记录]",
            "- talent_id: " + talent_id,
            "- 结果: 通过（进入二面阶段）",
            "- 当前阶段: ROUND2_SCHEDULING",
        ]
        if args.notes:
            lines.insert(3, "- 备注: " + args.notes)
        if args.round2_time:
            lines.append("- 二面时间（待候选人确认）: " + args.round2_time)
        lines.append("- 二面形式: 线下面试（统一）")
        lines.append("- 候选人邮箱: " + (candidate_email or "未记录"))
        save_candidate(talent_id, cand)
        from lib import talent_db as _tdb
        if _tdb._is_enabled():
            _tdb.clear_round_followup_fields(talent_id, 2)

        lines.append("- 二面通知邮件: 已发送（message_id={}）".format(
            email_res.get("message_id") or "?"))

        # 方案一：仅记录候选人侧时间，等候选人最终确认后再落老板日历
        if args.round2_time:
            lines.append("- 老板飞书日历: 暂不创建，待候选人确认后再落盘")
        else:
            lines.append("- 老板飞书日历: 未提供二面时间，跳过")

        print("\n".join(lines))
        return 0  # 提前 return，避免末尾重复 save_state

    elif result == "reject_keep":
        allowed_from = {"EXAM_SENT", "EXAM_REVIEWED"}
        ok = ensure_stage_transition(cand, allowed_from, "EXAM_REJECT_KEEP")
        if not ok:
            print(
                f"ERROR: 当前阶段 {current_stage} 不允许执行 exam_result=reject_keep。",
                file=sys.stderr,
            )
            return 1

        append_audit(
            cand,
            actor=args.actor,
            action="exam_result_reject_keep",
            payload={"notes": args.notes},
        )
        save_candidate(talent_id, cand)
        note_line = "\n- 备注: " + args.notes if args.notes else ""
        print(
            f"[笔试结果已记录]\n"
            f"- talent_id: {talent_id}\n"
            f"- 结果: 笔试未通过（保留人才库）"
            f"{note_line}\n"
            f"- 当前阶段: EXAM_REJECT_KEEP\n"
            f"- 候选人邮箱: {cand.get('candidate_email', '未记录')}\n"
            f"- 后续动作: 候选人已保留在人才库，可在未来合适职位时重新激活。"
        )
        return 0

    else:  # reject_delete
        allowed_from = {"EXAM_SENT", "EXAM_REVIEWED"}
        if current_stage not in allowed_from:
            print(
                "ERROR: 当前阶段 {} 不允许执行 exam_result=reject_delete。".format(current_stage),
                file=sys.stderr,
            )
            return 1

        candidate_email = (cand.get("candidate_email") or "").strip()
        if candidate_email:
            email_res = send_rejection_notification(talent_id)
            if not (isinstance(email_res, dict) and email_res.get("ok")):
                print(
                    "ERROR: 拒信未发送，未执行删档。detail={}".format(
                        email_res.get("stderr") or email_res.get("stdout") or email_res.get("returncode")
                        if isinstance(email_res, dict) else email_res,
                    ),
                    file=sys.stderr,
                )
                return 1

        reason = "笔试未通过 reject_delete"
        if args.notes:
            reason += "；{}".format(args.notes.strip())
        delete_res = delete_talent_archive(talent_id, reason, args.actor)
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

        notes_line = ""
        if args.notes:
            notes_line = "\n- 评价: {}".format(args.notes.strip())
        email_line = "\n- 拒信: 已发送（message_id={}）".format(
            email_res.get("message_id") or "?") if candidate_email else "\n- 拒信: 跳过（候选人无邮箱）"
        print(
            "[笔试结果已记录]\n"
            "- talent_id: {}\n"
            "- 结果: 未通过（已从人才库彻底删除）\n"
            "- 候选人记录已清除，不再联系。{}{}".format(talent_id, email_line, notes_line)
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
