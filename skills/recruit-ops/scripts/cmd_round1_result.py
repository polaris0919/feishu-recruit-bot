#!/usr/bin/env python3
"""
处理 /round1_result 命令：
  - result=pass          → EXAM_PENDING，自动发笔试邀请邮件
  - result=pass_direct   → ROUND2_SCHEDULED（跳过笔试，直接安排二面）
  - result=reject_keep   → ROUND1_DONE_REJECT_KEEP
  - result=reject_delete → ROUND1_DONE_REJECT_DELETE
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from typing import List, Optional


def _spawn_calendar_bg(talent_id, round2_time, interviewer, candidate_email):
    # type: (str, str, str, str) -> int
    """后台启动飞书日历脚本，失败不阻断主流程。"""
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
        start_new_session=True,
        stdout=log_fp,
        stderr=log_fp,
        close_fds=True,
    )
    log_fp.close()
    with open("/tmp/feishu_calendar_bg.log", "a") as f:
        f.write("[{}] calendar bg PID={} log={}\n".format(
            time.strftime("%Y-%m-%d %H:%M:%S"), proc.pid, log_path))
    return proc.pid

from core_state import (
    append_audit,
    ensure_stage_transition,
    get_candidate,
    load_state,
    normalize_for_save,
    save_state,
)


EXAM_FILES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "exam_files"
)
EXAM_FILES_DIR = os.path.normpath(EXAM_FILES_DIR)


def _get_exam_attachments():
    # type: () -> list
    """返回笔试附件路径列表，优先使用压缩包以控制邮件大小。"""
    if not os.path.isdir(EXAM_FILES_DIR):
        return []
    # 优先发 zip 压缩包（题目+数据，约6MB）
    zip_path = os.path.join(EXAM_FILES_DIR, "exam_package.zip")
    if os.path.isfile(zip_path):
        return [zip_path]
    # 回退：只发题目 docx
    docx_path = os.path.join(EXAM_FILES_DIR, "实习生笔试题目.docx")
    if os.path.isfile(docx_path):
        return [docx_path]
    return []


def _send_exam_email(talent_id, candidate_email, exam_id, company_name="", position_name=""):
    """调用 email-send 技能发笔试邀请邮件（含附件），失败不中断流程。"""
    script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "email-send", "scripts", "email_send.py"
    )
    script = os.path.normpath(script)
    if not os.path.isfile(script):
        print("[round1] email_send.py 未找到，跳过发邮件", file=sys.stderr)
        return False

    subject = "【笔试邀请】{}技术岗位笔试".format(company_name + " " if company_name else "")
    body = (
        "您好，\n\n感谢您参加我们的面试！\n\n"
        "您已通过一面，请完成以下笔试题目并回复本邮件（请附上您的代码文件）。\n\n"
        "笔试标识：{exam_id}\n\n"
        "附件为笔试题目及相关数据文件，请仔细阅读题目要求后作答。\n\n"
        "请在收到此邮件后 3 天内提交。\n\n祝好，\n招聘团队"
    ).format(exam_id=exam_id)

    attachments = _get_exam_attachments()
    cmd = ["python3", script, "--to", candidate_email, "--subject", subject, "--body", body]
    for fpath in attachments:
        cmd += ["--attachment", fpath]

    if attachments:
        print("[round1] 笔试附件: {}".format([os.path.basename(f) for f in attachments]))
    else:
        print("[round1] 未找到笔试附件目录 {}，发送无附件版本".format(EXAM_FILES_DIR), file=sys.stderr)

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        return result.returncode == 0
    except Exception as e:
        print("[round1] 发邮件失败: {}".format(e), file=sys.stderr)
        return False


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="处理 /round1_result 命令")
    p.add_argument("--talent-id", required=True)
    p.add_argument(
        "--result", required=True,
        choices=["pass", "pass_direct", "reject_keep", "reject_delete"],
    )
    p.add_argument("--email", default="", help="候选人邮箱（pass 时必填）")
    p.add_argument("--round2-time", default="", help="二面时间（pass_direct 时必填）")
    p.add_argument("--interviewer", default="", help="二面面试官")
    p.add_argument("--notes", default="", help="一面评价（自然语言，写入数据库 round1_notes）")
    p.add_argument("--company-name", default="", help="公司名称（邮件用）")
    p.add_argument("--actor", default="system")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    talent_id = args.talent_id.strip()
    result = args.result

    state = load_state()
    cand = get_candidate(state, talent_id)
    current_stage = cand.get("stage") or "NEW"

    allowed_from = {"NEW", "ROUND1_SCHEDULED"}

    if result == "pass":
        if not args.email:
            print("ERROR: --result pass 需要提供 --email", file=sys.stderr)
            return 1

        ok = ensure_stage_transition(cand, allowed_from, "EXAM_PENDING")
        if not ok:
            print("ERROR: 当前阶段 {} 不允许执行 round1_result=pass。".format(current_stage), file=sys.stderr)
            return 1

        cand["candidate_email"] = args.email.strip()
        if args.notes:
            cand["round1_notes"] = args.notes.strip()
        exam_id = "exam-{}-{}".format(talent_id, datetime.utcnow().strftime("%Y%m%d%H%M%S"))
        cand["exam_id"] = exam_id
        cand["exam_sent_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        email_ok = _send_exam_email(talent_id, args.email, exam_id, args.company_name)

        append_audit(cand, actor=args.actor, action="round1_pass_and_exam_invite_sent",
                     payload={"email": args.email, "exam_id": exam_id, "email_sent": email_ok,
                              "notes": args.notes})

        lines = [
            "[一面结果已记录]",
            "- talent_id: {}".format(talent_id),
            "- 结果: 一面通过",
            "- 笔试邀请: {}".format("已发送" if email_ok else "发送失败（请手动发送）"),
            "- 笔试 ID: {}".format(exam_id),
            "- 候选人邮箱: {}".format(args.email),
            "- 当前阶段: EXAM_PENDING（等待笔试回复）",
        ]
        if args.notes:
            lines.insert(3, "- 一面评价: {}".format(args.notes))

    elif result == "pass_direct":
        if not args.round2_time:
            print("ERROR: --result pass_direct 需要提供 --round2-time", file=sys.stderr)
            return 1

        ok = ensure_stage_transition(cand, allowed_from, "ROUND2_SCHEDULED")
        if not ok:
            print("ERROR: 当前阶段 {} 不允许执行 round1_result=pass_direct。".format(current_stage), file=sys.stderr)
            return 1

        cand["round2_time"] = args.round2_time.strip()
        if args.interviewer:
            cand["round2_interviewer"] = args.interviewer.strip()
        if args.email:
            cand["candidate_email"] = args.email.strip()
        if args.notes:
            cand["round1_notes"] = args.notes.strip()

        append_audit(cand, actor=args.actor, action="round1_pass_direct_round2_scheduled",
                     payload={"round2_time": args.round2_time, "interviewer": args.interviewer,
                              "notes": args.notes})

        # 后台创建飞书日历
        cal_pid = _spawn_calendar_bg(
            talent_id,
            args.round2_time.strip(),
            args.interviewer.strip() if args.interviewer else "",
            args.email.strip() if args.email else "",
        )

        lines = [
            "[一面结果已记录]",
            "- talent_id: {}".format(talent_id),
            "- 结果: 一面通过（直接二面）",
            "- 二面时间: {}".format(args.round2_time),
            "- 飞书日历: 创建中（PID={}）".format(cal_pid),
            "- 当前阶段: ROUND2_SCHEDULED",
        ]

    elif result == "reject_keep":
        ok = ensure_stage_transition(cand, allowed_from, "ROUND1_DONE_REJECT_KEEP")
        if not ok:
            print("ERROR: 当前阶段 {} 不允许执行 round1_result=reject_keep。".format(current_stage), file=sys.stderr)
            return 1

        if args.notes:
            cand["round1_notes"] = args.notes.strip()
        append_audit(cand, actor=args.actor, action="round1_result_reject_keep",
                     payload={"notes": args.notes})
        lines = [
            "[一面结果已记录]",
            "- talent_id: {}".format(talent_id),
            "- 结果: 未通过（保留人才库）",
            "- 候选人已保留，可在未来合适职位时重新激活。",
        ]
        if args.notes:
            lines.insert(3, "- 一面评价: {}".format(args.notes))

    else:  # reject_delete
        ok = ensure_stage_transition(cand, allowed_from, "ROUND1_DONE_REJECT_DELETE")
        if not ok:
            print("ERROR: 当前阶段 {} 不允许执行 round1_result=reject_delete。".format(current_stage), file=sys.stderr)
            return 1

        if args.notes:
            cand["round1_notes"] = args.notes.strip()
        append_audit(cand, actor=args.actor, action="round1_result_reject_delete",
                     payload={"notes": args.notes})
        lines = [
            "[一面结果已记录]",
            "- talent_id: {}".format(talent_id),
            "- 结果: 未通过（移除）",
            "- 候选人已标记移除。",
        ]
        if args.notes:
            lines.insert(3, "- 一面评价: {}".format(args.notes))

    print("\n".join(lines))
    state = normalize_for_save(state)
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
