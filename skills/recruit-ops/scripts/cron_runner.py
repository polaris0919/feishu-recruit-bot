#!/usr/bin/env python3
"""
独立 Cron 运行器 — 绕过 OpenClaw Gateway，直接执行扫描并推送飞书通知。
由系统 crontab 通过 `.venv/bin/python scripts/cron_runner.py` 调用，无需 Cursor 连接。

包含两个任务：
  1. exam.daily_exam_review --auto     笔试回信 + 二面确认扫描
  2. common.cmd_interview_reminder     二面结束未出结果催问
"""
import subprocess
import sys

def run_module(module_name, *args):
    """通过已安装模块运行子任务并捕获 stdout，失败静默。"""
    cmd = [sys.executable, "-m", module_name] + list(args)
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
    try:
        import feishu
    except ImportError:
        return

    # ── 任务 1：笔试回信 + 二面确认扫描 ──────────────────────────────
    out1 = run_module("exam.daily_exam_review", "--auto")
    if out1:
        feishu.send_text(out1)

    # ── 任务 2：二面结束催问 ─────────────────────────────────────────
    out2 = run_module("common.cmd_interview_reminder")
    if out2:
        feishu.send_text(out2)


if __name__ == "__main__":
    main()
