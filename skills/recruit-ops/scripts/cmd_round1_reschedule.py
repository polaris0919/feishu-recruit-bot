#!/usr/bin/env python3
"""
重新约定一面时间：
  1. 删除旧飞书日历事件（若有）
  2. 发送新一面邀请邮件给候选人
  3. 创建新飞书日历事件（自动写 event_id 到 DB）
  4. 更新 DB：round1_time、round1_invite_sent_at、reset round1_confirmed=false

用法：
  python3 cmd_round1_reschedule.py \
    --talent-id t_xxx \
    --time "2026-04-15 15:00" \
    [--interviewer 老板]
"""
import argparse
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from core_state import append_audit, get_candidate, load_state, normalize_for_save, save_state

EMAIL_SEND_SCRIPT = os.path.expanduser(
    "~/.openclaw/workspace/skills/email-send/scripts/email_send.py"
)


def _send_reschedule_email(to_email, talent_id, new_time, interviewer="", candidate_name=""):
    # type: (str, str, str, str, str) -> int
    """后台发一面重约时间邮件，返回 PID。"""
    subject = "【面试通知】第一轮面试时间更新 - 致邃投资"

    body_parts = [
        "您好，{}，".format(candidate_name if candidate_name else ""),
        "",
        "非常感谢您的耐心等待！",
        "",
        "我们已根据双方时间调整，重新确认了第一轮面试的时间：",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "🗓 更新后的面试详情",
        "━━━━━━━━━━━━━━━━━━━━",
        "· 新的面试时间：" + new_time,
        "· 面试形式：线下面试",
        "· 面试地点：上海市浦东新区杨高中路丁香国际商业中心西塔21楼致邃投资",
        "",
        "请确认您是否能够按时参加此新时间。",
        "如有时间冲突，请尽快回复本邮件说明。",
        "",
        "期待与您的进一步交流！",
        "",
        "致邃投资 招聘团队",
        "",
        "---",
        "TALENT_ID: " + talent_id,
    ]
    body = "\n".join(body_parts)

    cmd = ["python3", EMAIL_SEND_SCRIPT, "--to", to_email, "--subject", subject, "--body", body]
    log_path = "/tmp/email_round1_reschedule_{}_{}.log".format(
        to_email.replace("@", "_"), int(time.time())
    )
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
        f.write("[{}] round1 reschedule email to={} PID={} log={}\n".format(
            time.strftime("%Y-%m-%d %H:%M:%S"), to_email, proc.pid, log_path))
    return proc.pid


def _spawn_calendar_bg(talent_id, round1_time, interviewer, candidate_email, old_event_id=""):
    # type: (str, str, str, str, str) -> int
    """后台创建一面飞书日历事件（传入 old_event_id 则先删旧事件）。"""
    script = os.path.join(_HERE, "feishu_calendar.py")
    cmd = [
        "python3", script,
        "--talent-id", talent_id,
        "--round2-time", round1_time,  # feishu_calendar 复用此参数
        "--event-round", "1",
    ]
    if interviewer:
        cmd += ["--interviewer", interviewer]
    if candidate_email:
        cmd += ["--candidate-email", candidate_email]
    if old_event_id:
        cmd += ["--old-event-id", old_event_id]

    log_path = "/tmp/feishu_cal_round1_reschedule_{}_{}.log".format(talent_id, int(time.time()))
    log_fp = open(log_path, "w")
    proc = subprocess.Popen(
        cmd,
        start_new_session=True,
        stdout=log_fp,
        stderr=log_fp,
        close_fds=True,
    )
    log_fp.close()
    with open("/tmp/feishu_calendar_bg.log", "a") as f:
        f.write("[{}] round1 reschedule cal PID={} log={}\n".format(
            time.strftime("%Y-%m-%d %H:%M:%S"), proc.pid, log_path))
    return proc.pid


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="重新约定一面时间")
    p.add_argument("--talent-id",   required=True, help="候选人 talent_id")
    p.add_argument("--time",        required=True, help="新的一面时间，例如 '2026-04-15 15:00'")
    p.add_argument("--interviewer", default="",    help="面试官姓名（可选）")
    p.add_argument("--confirmed",   action="store_true",
                   help="老板最终拍板确认，直接标记为已确认（跳过等候选人再次回复）")
    p.add_argument("--actor",       default="boss", help="操作人（用于审计）")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    talent_id = args.talent_id.strip()
    new_time = args.time.strip()

    state = load_state()
    cand = get_candidate(state, talent_id)
    current_stage = cand.get("stage") or "NEW"

    if current_stage not in ("ROUND1_SCHEDULING", "ROUND1_SCHEDULED"):
        print(
            "ERROR: 候选人 {} 当前阶段为 {}，只有 ROUND1_SCHEDULING / ROUND1_SCHEDULED 阶段才能重新约时间。".format(
                talent_id, current_stage
            ),
            file=sys.stderr,
        )
        return 1

    candidate_email = cand.get("candidate_email", "")
    candidate_name = cand.get("candidate_name", "")
    old_time = cand.get("round1_time") or "（未记录）"
    interviewer = args.interviewer or cand.get("round1_interviewer", "")

    # 从 DB 获取旧日历 event_id
    old_event_id = ""
    try:
        import talent_db as _tdb
        if _tdb._is_enabled():
            db_state = _tdb.load_state_from_db()
            db_cand = (db_state.get("candidates") or {}).get(talent_id, {})
            old_event_id = db_cand.get("round1_calendar_event_id") or ""
    except Exception:
        pass

    # 更新候选人状态
    cand["round1_time"] = new_time
    # --confirmed：老板最终拍板，直接标记为已确认；否则回到 SCHEDULING 等候选人确认
    new_stage = "ROUND1_SCHEDULED" if args.confirmed else "ROUND1_SCHEDULING"
    cand["stage"] = new_stage
    if args.interviewer:
        cand["round1_interviewer"] = args.interviewer

    append_audit(
        cand,
        actor=args.actor,
        action="round1_rescheduled_confirmed" if args.confirmed else "round1_rescheduled",
        payload={
            "old_time": old_time,
            "new_time": new_time,
            "interviewer": interviewer,
            "old_calendar_event_id": old_event_id,
            "confirmed": args.confirmed,
        },
    )

    state = normalize_for_save(state)
    save_state(state)

    # 更新 DB
    try:
        import talent_db as _tdb
        if _tdb._is_enabled():
            if args.confirmed:
                _tdb.save_round1_invite_info(talent_id)
                _tdb.mark_round1_confirmed(talent_id)
            else:
                _tdb.save_round1_invite_info(talent_id)  # reset confirmed=false
    except Exception:
        pass

    lines = [
        "[一面重新约时间]",
        "- talent_id: {}".format(talent_id),
        "- 候选人: {}".format(candidate_name or talent_id),
        "- 旧时间: {}".format(old_time),
        "- 新时间: {}".format(new_time),
        "- 候选人邮箱: {}".format(candidate_email or "未记录"),
        "- 状态: {}".format(
            "✅ 已直接确认（ROUND1_SCHEDULED），无需等候选人再次回复"
            if args.confirmed else
            "⏳ 等待候选人确认（ROUND1_SCHEDULING）"
        ),
    ]

    if args.confirmed:
        # 最终确认时不再发"请确认"邮件，只发"时间已定"通知
        if candidate_email:
            email_pid = _send_reschedule_email(candidate_email, talent_id, new_time, interviewer,
                                               candidate_name=candidate_name)
            lines.append("- 时间更新通知邮件: 发送中（后台 PID={}）".format(email_pid))
    else:
        if candidate_email:
            email_pid = _send_reschedule_email(candidate_email, talent_id, new_time, interviewer,
                                               candidate_name=candidate_name)
            lines.append("- 新邀请邮件: 发送中（后台 PID={}）".format(email_pid))
        else:
            lines.append("- 候选人邮箱未记录，无法自动发送邀请邮件")

    cal_pid = _spawn_calendar_bg(talent_id, new_time, interviewer, candidate_email, old_event_id)
    if old_event_id:
        lines.append("- 飞书日历: 删除旧事件并创建新事件（后台 PID={}）".format(cal_pid))
    else:
        lines.append("- 飞书日历: 创建新事件（后台 PID={}）".format(cal_pid))

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
