#!/usr/bin/env python3
"""ops/cmd_health_check.py —— v3.3 系统健康体检。

【用途】
  对关键外部依赖 / 内部状态做一次「连通+基础可用」检查，输出：
    - DB（PostgreSQL）能连、talents 能查
    - IMAP 能登录
    - SMTP 能连 TCP（不真发邮件）
    - DashScope 有 API key、能 ping（轻量分类）
    - Feishu 有 app_id/app_secret（不真发消息）
    - talent_emails 是否有待分析积压 / stage 是否有异常分布

【退出码】
  0 = 全部 OK 或只有 info 级问题
  1 = 至少 1 个硬依赖不通（DB/IMAP/SMTP/DashScope/Feishu 有 fail）
  2 = 参数错误等用户错误

【调用示例】
  PYTHONPATH=scripts python3 -m ops.cmd_health_check
  PYTHONPATH=scripts python3 -m ops.cmd_health_check --json
  PYTHONPATH=scripts python3 -m ops.cmd_health_check --skip dashscope --skip imap
"""
from __future__ import print_function

import argparse
import json
import socket
import sys
import time
from datetime import datetime

from lib import config as _cfg
from lib.cli_wrapper import UserInputError


_CHECKS = ["db", "imap", "smtp", "dashscope", "feishu", "talent_emails_backlog"]


def _result(name, ok, detail, level="hard"):
    return {
        "name": name,
        "ok": bool(ok),
        "level": level,  # hard | soft | info
        "detail": detail,
    }


# ─── 各检查 ─────────────────────────────────────────────────────────────────

def _check_db():
    try:
        import psycopg2
        t0 = time.time()
        with psycopg2.connect(**_cfg.db_conn_params()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM talents")
                n_talents = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM talent_emails")
                n_emails = cur.fetchone()[0]
        dt = (time.time() - t0) * 1000
        return _result("db", True,
                       "talents={} emails={} ({:.0f}ms)".format(n_talents, n_emails, dt))
    except Exception as e:
        return _result("db", False, "connect/query 失败: {}".format(str(e)[:200]))


def _check_imap():
    # IMAP 配置走 lib.exam_imap._load_email_config（读 recruit-email-config.json
    # 或 email-daily-summary-config.json），不走 config.get("imap")。
    try:
        from lib.exam_imap import connect_imap
        import os as _os
        t0 = time.time()
        imap = connect_imap()
        try:
            imap.logout()
        except Exception:
            pass
        dt = (time.time() - t0) * 1000
        host = _os.environ.get("RECRUIT_EXAM_IMAP_HOST", "?")
        return _result("imap", True,
                       "host={} 登录 OK ({:.0f}ms)".format(host, dt))
    except Exception as e:
        return _result("imap", False, "登录失败: {}".format(str(e)[:200]))


def _check_smtp():
    try:
        from lib.smtp_sender import _smtp_cfg
        smtp = _smtp_cfg()
        host = smtp.get("host")
        port = int(smtp.get("port") or 465)
        t0 = time.time()
        s = socket.create_connection((host, port), timeout=5)
        s.close()
        dt = (time.time() - t0) * 1000
        return _result("smtp", True,
                       "TCP {}:{} 可达 ({:.0f}ms)".format(host, port, dt))
    except Exception as e:
        return _result("smtp", False, "SMTP 配置/连通失败: {}".format(str(e)[:200]))


def _check_dashscope():
    try:
        ds_cfg = _cfg.get("dashscope")
        api_key = (ds_cfg or {}).get("api_key", "")
        if not api_key:
            return _result("dashscope", False, "config 缺 dashscope.api_key")
        from lib.dashscope_client import chat_completion
        t0 = time.time()
        txt = chat_completion(
            messages=[{"role": "user", "content": "回复一个字：好"}],
            temperature=0,
            max_tokens=8,
        )
        dt = (time.time() - t0) * 1000
        if not txt or not txt.strip():
            return _result("dashscope", False,
                           "API 返回空（{:.0f}ms）".format(dt), level="hard")
        return _result("dashscope", True,
                       "ping OK: {!r} ({:.0f}ms)".format(txt.strip()[:30], dt))
    except Exception as e:
        return _result("dashscope", False, "调用失败: {}".format(str(e)[:200]))


def _check_feishu():
    try:
        feishu_cfg = _cfg.get("feishu") or {}
        app_id = feishu_cfg.get("app_id", "")
        app_secret = feishu_cfg.get("app_secret", "")
        boss_open_id = feishu_cfg.get("boss_open_id", "")
        missing = []
        if not app_id:
            missing.append("app_id")
        if not app_secret:
            missing.append("app_secret")
        if not boss_open_id:
            missing.append("boss_open_id")
        if missing:
            return _result("feishu", False, "config 缺 {}".format(", ".join(missing)))
        # 只校验 client 能初始化（不真发消息）
        from lib.feishu import _get_client
        client = _get_client()
        if client is None:
            return _result("feishu", False, "_get_client 返回 None（app_id/secret 无效？）")
        return _result("feishu", True,
                       "client 初始化 OK（app_id=***{}, open_id=***{}）".format(
                           app_id[-4:], boss_open_id[-4:]))
    except Exception as e:
        return _result("feishu", False, "初始化失败: {}".format(str(e)[:200]))


def _check_talent_emails_backlog(backlog_warn=50):
    try:
        import psycopg2
        with psycopg2.connect(**_cfg.db_conn_params()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM talent_emails "
                    "WHERE direction='inbound' AND analyzed_at IS NULL"
                )
                unanalyzed = cur.fetchone()[0]
                cur.execute(
                    "SELECT current_stage, COUNT(*) FROM talents "
                    "GROUP BY current_stage ORDER BY 2 DESC"
                )
                stage_hist = cur.fetchall()
        hist_s = ", ".join("{}={}".format(k, v) for k, v in stage_hist)
        if unanalyzed >= backlog_warn:
            return _result(
                "talent_emails_backlog", False,
                "未分析 inbound 邮件 {} 条，超过阈值 {}。请跑 inbox.cmd_analyze。stage 分布: {}".format(
                    unanalyzed, backlog_warn, hist_s),
                level="soft",
            )
        return _result(
            "talent_emails_backlog", True,
            "未分析 inbound={} (阈值 {})；stage 分布: {}".format(
                unanalyzed, backlog_warn, hist_s),
            level="info",
        )
    except Exception as e:
        return _result("talent_emails_backlog", False,
                       "检查失败: {}".format(str(e)[:200]), level="soft")


_CHECK_FNS = {
    "db": _check_db,
    "imap": _check_imap,
    "smtp": _check_smtp,
    "dashscope": _check_dashscope,
    "feishu": _check_feishu,
    "talent_emails_backlog": _check_talent_emails_backlog,
}


def _build_parser():
    p = argparse.ArgumentParser(description="系统健康体检")
    p.add_argument("--skip", action="append", default=[],
                   choices=_CHECKS, help="跳过某项检查（可多次）")
    p.add_argument("--only", action="append", default=[],
                   choices=_CHECKS, help="只跑某项检查（可多次；和 --skip 互斥）")
    p.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)

    if args.only and args.skip:
        raise UserInputError("--only 和 --skip 互斥")

    targets = args.only or [c for c in _CHECKS if c not in args.skip]

    results = []
    for name in targets:
        fn = _CHECK_FNS[name]
        try:
            results.append(fn())
        except Exception as e:
            results.append(_result(name, False,
                                   "check 本身 crash: {}".format(str(e)[:200])))

    hard_fail = [r for r in results if (not r["ok"]) and r["level"] == "hard"]
    soft_fail = [r for r in results if (not r["ok"]) and r["level"] == "soft"]
    ok_count = sum(1 for r in results if r["ok"])

    summary = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "ok": ok_count,
        "hard_fail": len(hard_fail),
        "soft_fail": len(soft_fail),
        "results": results,
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("Health check @ {}".format(summary["when"]))
        for r in results:
            marker = "✓" if r["ok"] else ("⚠" if r["level"] == "soft" else "✗")
            print("  [{}] {:<26}  {}".format(marker, r["name"], r["detail"]))
        print("\n  {} ok / {} hard-fail / {} soft-fail".format(
            summary["ok"], summary["hard_fail"], summary["soft_fail"]))

    return 1 if hard_fail else 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except UserInputError as e:
        print("[ops.cmd_health_check] INPUT ERROR: {}".format(e), file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        # health_check 本身 crash：stderr，不自动推飞书（体检尚未确认通道可用）
        print("[ops.cmd_health_check] CRASH: {}".format(e), file=sys.stderr)
        sys.exit(1)
