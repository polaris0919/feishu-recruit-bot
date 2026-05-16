#!/usr/bin/env python3
"""ops/cmd_preflight_release.py —— 内部上线前预检清单。

本命令聚合机器可自动判断的上线前检查，并打印仍需人工执行的 staging
验证项。它不发送邮件、不推飞书、不写 DB；真实外部连通性复用
ops.cmd_health_check。
"""
from __future__ import print_function

import argparse
import json
from datetime import datetime

from lib.cli_subprocess import run_module


_MANUAL_CHECKS = [
    "在 staging 候选人上跑 outbound.cmd_send --dry-run 验证模板渲染",
    "用测试候选人真实发送一封 SMTP 邮件并确认 talent_emails 入库",
    "用 feishu.cmd_notify --to hr --dry-run 预览，再人工确认真实飞书通知权限",
    "跑 inbox.cmd_scan --dry-run 核对 IMAP 可读取目标邮箱",
    "跑 auto_reject.cmd_scan_exam_timeout --dry-run 对账后再允许自动化",
]


def _run_pytest(skip_tests):
    if skip_tests:
        return {"name": "pytest", "ok": True, "skipped": True}
    try:
        import pytest
    except Exception as exc:
        return {
            "name": "pytest",
            "ok": False,
            "returncode": 1,
            "stderr_tail": "pytest import failed: {}".format(exc),
        }
    rc = pytest.main(["scripts/tests/", "-q", "--tb=short"])
    return {
        "name": "pytest",
        "ok": rc == 0,
        "returncode": rc,
    }


def _run_health(skip_dashscope):
    args = ["--json"]
    if skip_dashscope:
        args += ["--skip", "dashscope"]
    res = run_module("ops.cmd_health_check", args, timeout=180, parse_json=True)
    summary = res.get("json")
    if summary is None and res.get("stdout"):
        try:
            summary = json.loads(res["stdout"])
        except Exception:
            summary = None
    out = {
        "name": "health_check",
        "ok": bool(res.get("ok")),
        "returncode": res.get("returncode"),
        "summary": summary,
    }
    if not out["ok"]:
        out["stderr_tail"] = (res.get("stderr") or "")[-1200:]
        out["stdout_tail"] = (res.get("stdout") or "")[-1200:]
    return out


def _build_parser():
    p = argparse.ArgumentParser(description="内部上线前预检")
    p.add_argument("--skip-tests", action="store_true",
                   help="跳过 pytest，仅跑外部依赖健康检查")
    p.add_argument("--skip-dashscope", action="store_true",
                   help="健康检查跳过 DashScope ping，避免消耗 LLM 配额")
    p.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    checks = [
        _run_pytest(args.skip_tests),
        _run_health(args.skip_dashscope),
    ]
    ok = all(c.get("ok") for c in checks)
    payload = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "ok": ok,
        "checks": checks,
        "manual_checks": _MANUAL_CHECKS,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("Recruit Ops preflight @ {}".format(payload["when"]))
        for c in checks:
            suffix = " (skipped)" if c.get("skipped") else ""
            print("  [{}] {}{}".format("OK" if c.get("ok") else "FAIL", c["name"], suffix))
        print("\nManual staging checks before enabling unattended cron:")
        for idx, item in enumerate(_MANUAL_CHECKS, 1):
            print("  {}. {}".format(idx, item))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
