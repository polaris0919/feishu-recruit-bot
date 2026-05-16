#!/usr/bin/env python3
"""
后台子进程公共模块：统一封装邮件发送和飞书日历操作的 Popen 逻辑。
所有 cmd_*.py 脚本通过此模块发起后台任务，避免 Popen 样板代码重复。

═══════════════════════════════════════════════════════════════════════════════
分层（v3.8.x 后）
═══════════════════════════════════════════════════════════════════════════════
- 同步 atomic CLI 调度统一走 lib.cli_subprocess.run_module()，本文件不再
  自己手写 subprocess.run。`send_outbound_template()` 是它的邮件语义包装：
  fake / RECRUIT_DISABLE_SIDE_EFFECTS 短路、模板参数装配、JSON 字段提取
  都留在本层，run_module() 是哑执行器不感知这些。
- 后台 fire-and-forget 仍由本文件自己 Popen（spawn_calendar /
  delete_calendar / send_bg_email），因为契约是"立即返回 PID"，与
  run_module() 的同步等待语义不同。后续若再多一个 async 调用方，应抽
  cli_subprocess.popen_module()，而不是把 run_module 改成支持
  background=True 的双形态函数。
"""
import os
import subprocess
import sys
import time
from typing import Dict, Iterable, List, Optional

from lib.cli_subprocess import build_subprocess_env, run_module
from lib.recruit_paths import scripts_dir
from lib.side_effect_guard import fake_pid, side_effects_disabled

_HERE = os.path.dirname(os.path.abspath(__file__))


# B3 (v3.8.7): SSOT 在 lib.cli_subprocess.build_subprocess_env, 这里保留
# 同名私有别名仅为给老 caller 一个稳定的引用路径(三处 Popen 都用它)。
# 新代码请直接 import build_subprocess_env, 这个名字会留到 v4.0 评估。
_recruit_subprocess_env = build_subprocess_env


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


def send_outbound_template(talent_id, template, vars=None, context=None,
                           attachments=None, timeout=120):
    # type: (str, str, Optional[Dict[str, str]], Optional[str], Optional[Iterable[str]], int) -> Dict[str, object]
    """通过 `outbound.cmd_send` atomic CLI 发送候选人模板邮件。

    业务 CLI 不应直接调用 `send_bg_email` 给候选人发信；统一走这个 helper，
    让模板渲染、SMTP、`talent_emails` 入库和 self-verify 都经过同一个命令边界。

    内部走 lib.cli_subprocess.run_module()。本函数只负责"邮件语义"：
      - side_effects_disabled() 短路返回 fake message_id（cli_subprocess 是
        哑执行器，不感知该开关，所以短路必须留在本层）。
      - 装配 cmd_send 的 CLI 参数（--template / --context / --vars / --attach）。
      - 把 run_module 的 json 字段（cmd_send stdout 末尾的 JSON）提到顶层，
        与历史调用方约定（直接读 message_id / email_id）兼容。
    """
    if side_effects_disabled():
        return {
            "ok": True,
            "returncode": 0,
            "message_id": "<side-effects-disabled@local>",
            "email_id": None,
            "dry_run": True,
            "stdout": "",
            "stderr": "",
            "cmd": ["<side-effects-disabled>", "outbound.cmd_send"],
        }

    args = [
        "--talent-id", talent_id,
        "--template", template,
        "--json",
    ]
    if context:
        args += ["--context", context]
    if vars:
        args.append("--vars")
        for key, value in vars.items():
            args.append("{}={}".format(key, value))
    for attachment in (attachments or []):
        if attachment:
            args += ["--attach", str(attachment)]

    res = run_module("outbound.cmd_send", args, timeout=timeout, parse_json=True)

    # 与历史返回结构保持兼容：把解析到的 JSON 字段（message_id / email_id 等）
    # 平铺到顶层，再覆盖 ok/returncode/stdout/stderr/cmd 五个执行字段。
    # 这样老调用方 `res["message_id"]` 不需要任何改动。
    parsed = dict(res.get("json") or {})  # type: Dict[str, object]
    parsed.update({
        "ok": res["ok"],
        "returncode": res["returncode"],
        "stdout": res["stdout"],
        "stderr": res["stderr"],
        "cmd": res["cmd"],
    })
    return parsed


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
