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

from lib.recruit_paths import scripts_dir, workspace_path
from lib.side_effect_guard import fake_pid, side_effects_disabled

_HERE = os.path.dirname(os.path.abspath(__file__))


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


def send_bg_email(to, subject, body, tag="email", attachments=None,
                  talent_id="", candidate_name=""):
    # type: (str, str, str, str, Optional[Iterable[str]], str, str) -> int
    """后台发送邮件，返回 watcher 的 PID。

    tag 用于区分日志文件名。watcher（lib.email_watch）会同步等 SMTP 子进程
    退出，失败时自动发飞书告警 + 写 talent_events email_smtp_failed 事件。
    传 talent_id / candidate_name 可让告警更可读，且失败事件能挂到对应候选人。"""
    if side_effects_disabled():
        return fake_pid()
    log_path = "/tmp/email_{}_{}_{}.log".format(tag, to.replace("@", "_"), int(time.time()))
    cmd = [sys.executable, "-m", "lib.email_watch",
           "--to", to, "--subject", subject, "--body", body,
           "--tag", tag, "--log-path", log_path]
    if talent_id:
        cmd += ["--talent-id", talent_id]
    if candidate_name:
        cmd += ["--candidate-name", candidate_name]
    for attachment in (attachments or []):
        if attachment:
            cmd += ["--attachment", attachment]
    watcher_log = "/tmp/email_watch_{}_{}.log".format(tag, int(time.time()))
    log_fp = open(watcher_log, "w")
    proc = subprocess.Popen(
        cmd,
        start_new_session=True,
        stdout=log_fp, stderr=log_fp, close_fds=True,
        cwd=scripts_dir(),
        env=_recruit_subprocess_env(),
    )
    log_fp.close()
    with open("/tmp/email_bg.log", "a") as f:
        f.write("[{}] {} to={} watcher_PID={} smtp_log={} watcher_log={}\n".format(
            time.strftime("%Y-%m-%d %H:%M:%S"), tag, to, proc.pid, log_path, watcher_log))
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
    """后台创建飞书日历事件，返回 PID。

    v3.4 Phase 5：内部走 `python -m feishu.cmd_calendar_create`（atomic CLI），
    替代旧 lib/feishu/calendar_cli.py。--time / --round 是 CLI 标准参数名。"""
    if side_effects_disabled():
        return fake_pid()
    cmd = [sys.executable, "-m", "feishu.cmd_calendar_create",
           "--talent-id", talent_id,
           "--time", event_time,
           "--round", str(event_round),
           "--json"]
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
    """后台删除飞书日历事件，返回 PID。

    v3.4 Phase 5：内部走 `python -m feishu.cmd_calendar_delete`（atomic CLI）。"""
    if side_effects_disabled():
        return fake_pid()
    cmd = [sys.executable, "-m", "feishu.cmd_calendar_delete",
           "--event-id", event_id, "--reason", tag, "--json"]
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
