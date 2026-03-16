#!/usr/bin/env python3
"""
处理 /exam_result 命令：
  - result=pass        → 状态推进到 ROUND2_SCHEDULED，自动给候选人发二面通知邮件
  - result=reject_keep → 状态推进到 ROUND1_DONE_REJECT_KEEP（保留人才库）
  - result=reject_delete → 状态推进到 ROUND1_DONE_REJECT_DELETE（移除）
"""
import argparse
import os
import subprocess
import sys
import time
from typing import List, Optional

from core_state import (
    append_audit,
    ensure_stage_transition,
    get_candidate,
    load_state,
    normalize_for_save,
    save_state,
)


def _spawn_calendar_bg(talent_id, round2_time, interviewer, candidate_email):
    # type: (str, str, str, str) -> int
    """
    在独立 session（新进程组）中后台启动飞书日历脚本。
    start_new_session=True 等价于 setsid，父进程被 kill 时子进程不受影响。
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feishu_calendar.py")
    cmd = ["python3", script, "--talent-id", talent_id, "--round2-time", round2_time]
    if interviewer:
        cmd += ["--interviewer", interviewer]
    if candidate_email:
        cmd += ["--candidate-email", candidate_email]

    log_path = "/tmp/feishu_cal_{}_{}.log".format(talent_id, int(time.time()))
    log_fp = open(log_path, "w")
    proc = subprocess.Popen(
        cmd,
        start_new_session=True,   # 新 session，独立进程组，父进程被 kill 后仍运行
        stdout=log_fp,
        stderr=log_fp,
        close_fds=True,
    )
    log_fp.close()
    # 写主日志
    with open("/tmp/feishu_calendar_bg.log", "a") as f:
        f.write("[{}] calendar bg PID={} log={}\n".format(
            time.strftime("%Y-%m-%d %H:%M:%S"), proc.pid, log_path))
    return proc.pid

EMAIL_SEND_SCRIPT = os.path.expanduser(
    "~/.openclaw/workspace/skills/email-send/scripts/email_send.py"
)


def send_round2_notification(to_email, talent_id, round2_time, interviewer, company=""):
    # type: (str, str, str, str, str) -> int
    """在独立 session 后台发送二面通知邮件，返回 PID。"""
    subject = "[面试通知] 笔试通过，邀请参加第二轮面试"
    if company:
        subject = "[面试通知] 笔试通过，邀请参加第二轮面试 - " + company

    time_line = ("面试时间：" + round2_time) if round2_time else "面试时间：待定，HR 将另行通知"
    interviewer_line = ("面试官：" + interviewer) if interviewer else ""

    body_parts = [
        "您好，",
        "",
        "感谢您完成笔试！经过评审，您已通过本轮笔试，我们诚邀您参加第二轮面试。",
        "",
        "【面试详情】",
        time_line,
    ]
    if interviewer_line:
        body_parts.append(interviewer_line)
    body_parts += [
        "面试形式：视频/电话面试（HR 将提前发送具体会议链接）",
        "",
        "请确认您是否能够按时参加。如有时间冲突，请尽快回复本邮件说明。",
        "",
        "期待与您的进一步交流！",
        "",
        "---",
        "TALENT_ID: " + talent_id,
    ]
    body = "\n".join(body_parts)

    cmd = ["python3", EMAIL_SEND_SCRIPT, "--to", to_email, "--subject", subject, "--body", body]
    log_path = "/tmp/email_round2_{}_{}.log".format(to_email.replace("@", "_"), int(time.time()))
    log_fp = open(log_path, "w")
    proc = subprocess.Popen(
        cmd,
        start_new_session=True,
        stdout=log_fp,
        stderr=log_fp,
        close_fds=True,
    )
    log_fp.close()
    with open("/tmp/email_bg.log", "a") as f:
        f.write("[{}] email to={} PID={} log={}\n".format(
            time.strftime("%Y-%m-%d %H:%M:%S"), to_email, proc.pid, log_path))
    return proc.pid


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
        "--interviewer",
        default="",
        help="二面面试官姓名（可选）",
    )
    p.add_argument(
        "--notes",
        default="",
        help="笔试评价（自然语言，写入数据库 exam_notes）",
    )
    p.add_argument(
        "--actor",
        default="system",
        help="执行人（用于审计，建议传入 HR 的飞书 user_id）",
    )
    return p.parse_args(argv)


def main(argv=None):
    # type: (Optional[List[str]]) -> int
    args = parse_args(argv or sys.argv[1:])
    talent_id = args.talent_id.strip()
    result = args.result

    state = load_state()
    cand = get_candidate(state, talent_id)

    current_stage = cand.get("stage") or "NEW"

    if result == "pass":
        # 允许从 EXAM_REVIEWED 或 EXAM_PENDING（兜底）推进到二面
        allowed_from = {"EXAM_PENDING", "EXAM_REVIEWED"}
        ok = ensure_stage_transition(cand, allowed_from, "ROUND2_SCHEDULED")
        if not ok:
            print(
                f"ERROR: 当前阶段 {current_stage} 不允许执行 exam_result=pass。"
                f"（需要处于 EXAM_REVIEWED 或 EXAM_PENDING 阶段）",
                file=sys.stderr,
            )
            return 1

        if args.round2_time:
            cand["round2_time"] = args.round2_time
        if args.interviewer:
            cand["round2_interviewer"] = args.interviewer
        if args.notes:
            # 人工笔试评价：追加到已有 exam_notes（可能含自动预审内容）
            existing = (cand.get("exam_notes") or "").strip()
            manual_note = "[人工评价] " + args.notes.strip()
            cand["exam_notes"] = (existing + "\n" + manual_note).strip() if existing else manual_note

        candidate_email = cand.get("candidate_email", "")

        # 自动给候选人后台发二面通知邮件（不阻塞主流程）
        email_pid = None
        if candidate_email:
            email_pid = send_round2_notification(
                to_email=candidate_email,
                talent_id=talent_id,
                round2_time=args.round2_time,
                interviewer=args.interviewer,
            )
        else:
            print("WARNING: 候选人邮箱未记录，无法自动发送二面通知邮件。", file=sys.stderr)

        append_audit(
            cand,
            actor=args.actor,
            action="exam_result_pass_round2_scheduled",
            payload={
                "round2_time": args.round2_time,
                "interviewer": args.interviewer,
                "notification_sent_to": candidate_email,
                "notes": args.notes,
            },
        )

        lines = [
            "[笔试结果已记录]",
            "- talent_id: " + talent_id,
            "- 结果: 通过（进入二面阶段）",
            "- 当前阶段: ROUND2_SCHEDULED",
        ]
        if args.notes:
            lines.insert(3, "- 笔试评价: " + args.notes)
        if args.round2_time:
            lines.append("- 二面时间: " + args.round2_time)
        if args.interviewer:
            lines.append("- 面试官: " + args.interviewer)
        lines.append("- 候选人邮箱: " + (candidate_email or "未记录"))
        if candidate_email and email_pid:
            lines.append("- 二面通知邮件: 发送中（后台 PID={}）".format(email_pid))

        # 保存状态（必须在任何网络调用之前完成，防止超时导致状态丢失）
        state = normalize_for_save(state)
        save_state(state)

        # 飞书日历：后台独立进程，不阻塞主流程，不受 exec 超时影响
        if args.round2_time:
            try:
                cal_pid = _spawn_calendar_bg(
                    talent_id=talent_id,
                    round2_time=args.round2_time,
                    interviewer=args.interviewer,
                    candidate_email=candidate_email,
                )
                lines.append("- 飞书日历: 创建中（后台 PID={})，约10秒后完成".format(cal_pid))
            except Exception as cal_err:
                lines.append("- 飞书日历: 启动失败（" + str(cal_err) + "）")
        elif not args.round2_time:
            lines.append("- 飞书日历: 未提供二面时间，跳过")

        print("\n".join(lines))
        return 0  # 提前 return，避免末尾重复 save_state

    elif result == "reject_keep":
        allowed_from = {"EXAM_PENDING", "EXAM_REVIEWED"}
        ok = ensure_stage_transition(cand, allowed_from, "ROUND1_DONE_REJECT_KEEP")
        if not ok:
            print(
                f"ERROR: 当前阶段 {current_stage} 不允许执行 exam_result=reject_keep。",
                file=sys.stderr,
            )
            return 1

        if args.notes:
            existing = (cand.get("exam_notes") or "").strip()
            manual_note = "[人工评价] " + args.notes.strip()
            cand["exam_notes"] = (existing + "\n" + manual_note).strip() if existing else manual_note
        append_audit(
            cand,
            actor=args.actor,
            action="exam_result_reject_keep",
            payload={"notes": args.notes},
        )
        note_line = "\n- 笔试评价: " + args.notes if args.notes else ""
        print(
            f"[笔试结果已记录]\n"
            f"- talent_id: {talent_id}\n"
            f"- 结果: 未通过（保留人才库）"
            f"{note_line}\n"
            f"- 候选人邮箱: {cand.get('candidate_email', '未记录')}\n"
            f"- 后续动作: 候选人已保留在人才库，可在未来合适职位时重新激活。"
        )

    else:  # reject_delete
        allowed_from = {"EXAM_PENDING", "EXAM_REVIEWED"}
        ok = ensure_stage_transition(cand, allowed_from, "ROUND1_DONE_REJECT_DELETE")
        if not ok:
            print(
                f"ERROR: 当前阶段 {current_stage} 不允许执行 exam_result=reject_delete。",
                file=sys.stderr,
            )
            return 1

        if args.notes:
            existing = (cand.get("exam_notes") or "").strip()
            manual_note = "[人工评价] " + args.notes.strip()
            cand["exam_notes"] = (existing + "\n" + manual_note).strip() if existing else manual_note
        append_audit(
            cand,
            actor=args.actor,
            action="exam_result_reject_delete",
            payload={"notes": args.notes},
        )
        print(
            f"[笔试结果已记录]\n"
            f"- talent_id: {talent_id}\n"
            f"- 结果: 未通过（从人才库移除）\n"
            f"- 后续动作: 候选人已标记为不再联系，人才库记录将清除。"
        )

    # pass 分支已在上方提前 save_state + return，此处处理 reject 分支
    state = normalize_for_save(state)
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
