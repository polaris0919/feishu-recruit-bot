#!/usr/bin/env python3
"""
后台子进程公共模块：统一封装邮件发送和飞书日历操作的 Popen 逻辑。
所有 cmd_*.py 脚本通过此模块发起后台任务，避免 Popen 样板代码重复。
"""
import os
import subprocess
import sys
import time
from typing import Iterable, Optional

from recruit_paths import scripts_dir, workspace_path
from side_effect_guard import fake_pid, side_effects_disabled

_HERE = os.path.dirname(os.path.abspath(__file__))

_EMAIL_SEND_SCRIPT_CANDIDATES = [
    str(workspace_path("skills", "email-send", "scripts", "email_send.py")),
    os.path.expanduser("~/.hermes/skills/openclaw-imports/email-send/scripts/email_send.py"),
]


def _recruit_subprocess_env():
    # type: () -> dict
    env = os.environ.copy()
    scripts_path = scripts_dir()
    existing = (env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = (
        scripts_path if not existing else scripts_path + os.pathsep + existing
    )
    env.setdefault("RECRUIT_WORKSPACE_ROOT", str(workspace_path()))
    return env


def _email_send_script():
    # type: () -> str
    for path in _EMAIL_SEND_SCRIPT_CANDIDATES:
        if os.path.isfile(path):
            return path
    return _EMAIL_SEND_SCRIPT_CANDIDATES[-1]


def send_bg_email(to, subject, body, tag="email", attachments=None):
    # type: (str, str, str, str, Optional[Iterable[str]]) -> int
    """后台发送邮件，返回 PID。tag 用于区分日志文件名。"""
    if side_effects_disabled():
        return fake_pid()
    cmd = ["python3", _email_send_script(), "--to", to, "--subject", subject, "--body", body]
    for attachment in (attachments or []):
        if attachment:
            cmd += ["--attachment", attachment]
    log_path = "/tmp/email_{}_{}_{}.log".format(tag, to.replace("@", "_"), int(time.time()))
    log_fp = open(log_path, "w")
    proc = subprocess.Popen(
        cmd, start_new_session=True, stdout=log_fp, stderr=log_fp, close_fds=True,
    )
    log_fp.close()
    with open("/tmp/email_bg.log", "a") as f:
        f.write("[{}] {} to={} PID={} log={}\n".format(
            time.strftime("%Y-%m-%d %H:%M:%S"), tag, to, proc.pid, log_path))
    return proc.pid


def spawn_calendar(
    talent_id,
    event_time,
    event_round=2,
    candidate_email="",
    candidate_name="",
    old_event_id="",
    tag="cal",
):
    # type: (str, str, int, str, str, str, str) -> int
    """后台创建飞书日历事件，返回 PID。"""
    if side_effects_disabled():
        return fake_pid()
    script = os.path.join(_HERE, "feishu", "calendar_cli.py")
    cmd = [sys.executable, script, "--talent-id", talent_id, "--round2-time", event_time]
    if event_round != 2:
        cmd += ["--event-round", str(event_round)]
    if candidate_email:
        cmd += ["--candidate-email", candidate_email]
    if candidate_name:
        cmd += ["--candidate-name", candidate_name]
    if old_event_id:
        cmd += ["--old-event-id", old_event_id]

    log_path = "/tmp/feishu_cal_{}_{}_{}.log".format(tag, talent_id, int(time.time()))
    log_fp = open(log_path, "w")
    proc = subprocess.Popen(
        cmd,
        start_new_session=True,
        stdout=log_fp,
        stderr=log_fp,
        close_fds=True,
        cwd=scripts_dir(),
        env=_recruit_subprocess_env(),
    )
    log_fp.close()
    with open("/tmp/feishu_calendar_bg.log", "a") as f:
        f.write("[{}] {} PID={} log={}\n".format(
            time.strftime("%Y-%m-%d %H:%M:%S"), tag, proc.pid, log_path))
    return proc.pid


def delete_calendar(event_id, tag="cal_delete"):
    # type: (str, str) -> int
    """后台删除飞书日历事件，返回 PID。"""
    if side_effects_disabled():
        return fake_pid()
    script = os.path.join(_HERE, "feishu", "calendar_cli.py")
    cmd = [sys.executable, script, "--talent-id", "delete-only", "--delete-event-id", event_id]
    log_path = "/tmp/feishu_cal_delete_{}_{}.log".format(event_id[:16], int(time.time()))
    log_fp = open(log_path, "w")
    proc = subprocess.Popen(
        cmd,
        start_new_session=True,
        stdout=log_fp,
        stderr=log_fp,
        close_fds=True,
        cwd=scripts_dir(),
        env=_recruit_subprocess_env(),
    )
    log_fp.close()
    with open("/tmp/feishu_calendar_bg.log", "a") as f:
        f.write("[{}] {} PID={} log={}\n".format(
            time.strftime("%Y-%m-%d %H:%M:%S"), tag, proc.pid, log_path))
    return proc.pid
