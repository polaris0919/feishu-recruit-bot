#!/usr/bin/env python3
"""SMTP 投递 watcher：包一层 email_send.py，等子进程结束后根据 returncode 决定是否告警。

设计目的（04-22 加固）：
  历史教训 —— send_bg_email 只 spawn 后台进程就立刻返回 PID，主流程乐观地把
  "email_queued=True" 写入 audit。如果 SMTP 真的失败（地址非法 / 服务器
  拒收 / 鉴权错误 / 超时），没有任何人会被告知，只能事后翻 /tmp/email_*.log。

  本 watcher 自己被 send_bg_email 后台拉起，不阻塞主流程。它做三件事：
    1. 同步等 email_send.py 真正退出
    2. 失败时：发飞书消息给老板（含 to/subject/exit_code/log 路径）
    3. 失败时：如果带了 talent_id，在 talent_events 写一条 email_smtp_failed

  成功时只在 /tmp/email_bg.log 追加一行，不发飞书（避免噪音）。

CLI：
  python3 -m lib.email_watch \\
    --to a@b.com --subject "..." --body "..." --tag round1_invite \\
    [--talent-id t_xxx] [--candidate-name 张三] [--attachment /path/...] [--attachment ...]
"""
import argparse
import os
import subprocess
import sys
import time
from typing import List, Optional

from lib.recruit_paths import workspace_path

_EMAIL_SEND_SCRIPT_CANDIDATES = [
    str(workspace_path("skills", "email-send", "scripts", "email_send.py")),
    os.path.expanduser("~/.hermes/skills/openclaw-imports/email-send/scripts/email_send.py"),
]

_FAILURE_LOG = "/tmp/email_delivery_failures.log"
_BG_LOG = "/tmp/email_bg.log"


def _resolve_email_send_script():
    # type: () -> str
    for path in _EMAIL_SEND_SCRIPT_CANDIDATES:
        if os.path.isfile(path):
            return path
    return _EMAIL_SEND_SCRIPT_CANDIDATES[-1]


def _ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _append_log(path, line):
    # type: (str, str) -> None
    try:
        with open(path, "a") as f:
            f.write(line if line.endswith("\n") else line + "\n")
    except Exception as e:
        print("[email_watch] 写日志 {} 失败: {}".format(path, e), file=sys.stderr)


def _notify_boss_failure(to, subject, tag, talent_id, candidate_name, exit_code, log_path, stderr_tail):
    # type: (str, str, str, str, str, int, str, str) -> None
    try:
        from lib import feishu
    except Exception as e:
        print("[email_watch] 无法导入 feishu，跳过告警: {}".format(e), file=sys.stderr)
        return
    who = "{}（{}）".format(candidate_name or "?", talent_id) if talent_id else "(无 talent_id)"
    text = (
        "[邮件投递失败 ⚠]\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "候选人：{who}\n"
        "收件人：{to}\n"
        "主题：{subject}\n"
        "类型：{tag}\n"
        "退出码：{rc}\n"
        "日志：{log}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "错误尾段：\n{tail}"
    ).format(
        who=who, to=to, subject=subject or "(无)", tag=tag,
        rc=exit_code, log=log_path,
        tail=(stderr_tail or "(无 stderr)")[:800],
    )
    try:
        feishu.send_text(text)
    except Exception as e:
        print("[email_watch] 飞书告警失败: {}".format(e), file=sys.stderr)


def _record_failure_event(talent_id, to, subject, tag, exit_code, log_path):
    # type: (str, str, str, str, int, str) -> None
    if not talent_id:
        return
    try:
        from lib import talent_db
    except Exception as e:
        print("[email_watch] 无法导入 talent_db: {}".format(e), file=sys.stderr)
        return
    try:
        talent_db.save_audit_event(
            talent_id,
            "email_smtp_failed",
            payload={
                "to": to, "subject": subject, "tag": tag,
                "exit_code": exit_code, "log_path": log_path,
            },
            actor="email_watch",
        )
    except Exception as e:
        print("[email_watch] 写 talent_events 失败: {}".format(e), file=sys.stderr)


def _record_success_event(talent_id, to, subject, tag):
    # type: (str, str, str, str) -> None
    """成功路径默认不写 DB（避免 audit 噪音），仅 BG_LOG 记一行。
    需要 audit 时可设环境变量 RECRUIT_EMAIL_WATCH_AUDIT_SUCCESS=1 启用。"""
    if not talent_id:
        return
    if os.environ.get("RECRUIT_EMAIL_WATCH_AUDIT_SUCCESS") != "1":
        return
    try:
        from lib import talent_db
        talent_db.save_audit_event(
            talent_id,
            "email_smtp_delivered",
            payload={"to": to, "subject": subject, "tag": tag},
            actor="email_watch",
        )
    except Exception as e:
        print("[email_watch] 写 success 事件失败: {}".format(e), file=sys.stderr)


def _read_tail(path, max_chars=1000):
    # type: (str, int) -> str
    try:
        with open(path, "r") as f:
            content = f.read()
        return content[-max_chars:]
    except Exception:
        return ""


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="SMTP 投递 watcher：失败时回告警")
    p.add_argument("--to", required=True)
    p.add_argument("--subject", default="")
    p.add_argument("--body", default="")
    p.add_argument("--tag", default="email")
    p.add_argument("--talent-id", default="")
    p.add_argument("--candidate-name", default="")
    p.add_argument("--attachment", action="append", default=[])
    p.add_argument("--log-path", default="", help="email_send.py 的 stdout/stderr 重定向到此文件")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    # type: (Optional[List[str]]) -> int
    args = parse_args(argv)

    log_path = args.log_path or "/tmp/email_{}_{}_{}.log".format(
        args.tag, args.to.replace("@", "_"), int(time.time()))

    cmd = ["python3", _resolve_email_send_script(),
           "--to", args.to, "--subject", args.subject, "--body", args.body]
    for att in args.attachment:
        if att:
            cmd += ["--attachment", att]

    with open(log_path, "w") as fp:
        proc = subprocess.run(cmd, stdout=fp, stderr=subprocess.STDOUT)

    rc = proc.returncode
    if rc == 0:
        _append_log(_BG_LOG, "[{}] {} to={} OK log={}".format(
            _ts(), args.tag, args.to, log_path))
        _record_success_event(args.talent_id, args.to, args.subject, args.tag)
        return 0

    tail = _read_tail(log_path)
    _append_log(_FAILURE_LOG, "[{}] {} to={} rc={} talent={} log={}\n----\n{}\n".format(
        _ts(), args.tag, args.to, rc, args.talent_id or "-", log_path, tail))
    _append_log(_BG_LOG, "[{}] {} to={} FAILED rc={} log={}".format(
        _ts(), args.tag, args.to, rc, log_path))
    _notify_boss_failure(args.to, args.subject, args.tag,
                         args.talent_id, args.candidate_name, rc, log_path, tail)
    _record_failure_event(args.talent_id, args.to, args.subject, args.tag, rc, log_path)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
