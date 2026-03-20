#!/usr/bin/env python3
"""
独立 Cron 运行器 — 绕过 OpenClaw Gateway，直接执行扫描并推送飞书通知。
由系统 crontab 每 5 分钟调用，无需 Cursor 连接。

包含两个任务：
  1. daily_exam_review.py --auto   笔试回信 + 二面确认扫描
  2. cmd_interview_reminder.py     二面结束未出结果催问
"""
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def run_script(script_name, *args):
    """运行脚本并捕获 stdout，失败静默。"""
    cmd = [sys.executable, os.path.join(SCRIPTS_DIR, script_name)] + list(args)
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        return (result.stdout or b"").decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def main():
    sys.path.insert(0, SCRIPTS_DIR)
    try:
        import feishu_notify
    except ImportError:
        return

    # ── 任务 1：笔试回信 + 二面确认扫描 ──────────────────────────────
    out1 = run_script("daily_exam_review.py", "--auto")
    if out1:
        feishu_notify.send_text(out1)

    # ── 任务 2：二面结束催问 ─────────────────────────────────────────
    out2 = run_script("cmd_interview_reminder.py")
    if out2:
        feishu_notify.send_text(out2)


if __name__ == "__main__":
    main()
