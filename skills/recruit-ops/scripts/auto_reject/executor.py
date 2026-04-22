#!/usr/bin/env python3
"""
auto_reject/executor.py —— cmd_scan_exam_timeout 用的子流程 helper。

═══════════════════════════════════════════════════════════════════════════════
为什么用 subprocess 而不是直接 import
═══════════════════════════════════════════════════════════════════════════════
v3.3 的核心架构原则：
  - 每个"写"操作都必须通过唯一专用脚本入口；
  - cmd_send = 唯一发邮件入口（带自验证 + 入 talent_emails）
  - 不允许别的脚本绕过它们直接 send_bg_email，否则 self-verify / 一致性保证都绕过了。

stage 标记走 lib.talent_db.set_current_stage（in-process 调用）—— 因为
set_current_stage 本身已经是 v3.3 的"唯一 stage 写入入口（含审计）"，
talent/cmd_update.py 内部也是直接调它。这里再 fork 一个 cmd_update 子进程
反而会丢掉 actor 上下文。

═══════════════════════════════════════════════════════════════════════════════
v3.5.11 (2026-04-22) 设计变更：拒+留池替代拒+物理删
═══════════════════════════════════════════════════════════════════════════════
原 v3.4 行为：scan 命中 → _send_rejection_email + _delete_talent（物理删 + 归档）。
新行为：       scan 命中 → _send_rejection_email + _mark_exam_rejected_keep
              （set stage = EXAM_REJECT_KEEP，留在人才库，CV / 笔试 / 邮件历史全保留）。

驱动事故：2026-04-22 11:30 cron tick 因 _EMAIL_VALID_CONTEXTS 缺 "rejection"，
cmd_send SMTP 已发但写库 raise → executor 误判失败 → cmd_delete 未触发 →
人留在 EXAM_SENT → 下个 tick 会重发拒信。改成 "改 stage 留池" 后即便 mark 失败，
也只可能漏 stage 不会重发拒信（再加一道 "talent_emails 已有 outbound rejection
就跳过" 的防护，天然幂等）。

详见 cmd_scan_exam_timeout 模块文档头。
"""
from __future__ import print_function

import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

from lib import talent_db


# ─── 子进程调用 ─────────────────────────────────────────────────────────────

def _scripts_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def _run_cmd(module, args):
    # type: (str, List[str]) -> Dict[str, Any]
    """Run `python3 -m module args...` with PYTHONPATH=scripts."""
    scripts_root = _scripts_root()
    env = os.environ.copy()
    existing = env.get("PYTHONPATH") or ""
    env["PYTHONPATH"] = (
        scripts_root + (os.pathsep + existing if existing else ""))

    cmd = [sys.executable, "-m", module] + args
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", "replace") if proc.stdout else "",
            "stderr": proc.stderr.decode("utf-8", "replace") if proc.stderr else "",
            "cmd": cmd,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": "timeout", "cmd": cmd}
    except Exception as e:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": str(e), "cmd": cmd}


def _send_rejection_email(talent_id, template_name, reason, dry_run=False):
    # type: (str, str, str, bool) -> Dict[str, Any]
    """调 outbound.cmd_send 发模板模式拒信。返回 {ok, message_id, detail, raw}。"""
    args = [
        "--talent-id", talent_id,
        "--template", template_name,
        "--context", "rejection",
        "--json",
    ]
    if dry_run:
        args.append("--dry-run")

    res = _run_cmd("outbound.cmd_send", args)
    if not res["ok"]:
        return {
            "ok": False,
            "detail": "cmd_send failed: rc={} stderr={}".format(
                res["returncode"], (res["stderr"] or "")[:300]),
            "raw": res,
        }

    message_id = None
    try:
        lines = [ln for ln in (res["stdout"] or "").splitlines() if ln.strip()]
        for ln in reversed(lines):
            if ln.startswith("{"):
                out = json.loads(ln)
                message_id = out.get("message_id")
                break
    except Exception:
        pass
    return {
        "ok": True,
        "message_id": message_id,
        "detail": "cmd_send OK (template={})".format(template_name),
        "raw": res,
    }


def _mark_exam_rejected_keep(talent_id, message_id, reason, dry_run=False):
    # type: (str, Optional[str], str, bool) -> Dict[str, Any]
    """把候选人 stage 从 EXAM_SENT 推到 EXAM_REJECT_KEEP，写审计。

    用 talent_db.set_current_stage（v3.3 唯一 stage 写入入口，自带 talent_event 审计）。
    在审计 payload 里塞 message_id / reason 方便事后追溯。
    与旧 `_delete_talent` 不同的是：候选人留在人才库，CV / 笔试 / 邮件历史全保留，
    HR 后续仍可 talent.cmd_show 查档。

    返回 {ok, detail}。dry_run=True 时仅打印不写库。
    """
    if dry_run:
        return {"ok": True, "detail": "[dry-run] mark stage=EXAM_REJECT_KEEP"}

    audit_reason = "auto_reject:exam_no_reply"
    payload = {
        "from_stage": "EXAM_SENT",
        "trigger": "exam_timeout",
        "rejection_message_id": message_id,
        "reason": reason,
    }
    try:
        ok = talent_db.set_current_stage(
            talent_id=talent_id,
            new_stage="EXAM_REJECT_KEEP",
            actor="auto_reject.cmd_scan_exam_timeout",
            reason=audit_reason,
        )
    except Exception as e:
        return {
            "ok": False,
            "detail": "set_current_stage raised: {}".format(e),
            "raw": {"payload": payload},
        }
    if not ok:
        return {
            "ok": False,
            "detail": "set_current_stage returned False (DB disabled / dry-run blocked?)",
            "raw": {"payload": payload},
        }

    try:
        talent_db.save_audit_event(
            talent_id=talent_id,
            action="auto_reject.exam_timeout",
            payload=payload,
            actor="auto_reject.cmd_scan_exam_timeout",
        )
    except Exception as e:
        # 审计记不上不阻断主路径；上游 cron_runner 飞书 alert 仍会发
        print("[auto_reject] WARN: save_audit_event failed for {}: {}".format(
            talent_id, e), file=sys.stderr)

    return {
        "ok": True,
        "detail": "stage=EXAM_REJECT_KEEP marked (msg_id={})".format(message_id or "?"),
        "raw": {"payload": payload},
    }
