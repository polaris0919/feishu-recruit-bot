#!/usr/bin/env python3
"""
auto_reject/executor.py —— cmd_scan_exam_timeout 用的子流程 helper。

═══════════════════════════════════════════════════════════════════════════════
为什么用 subprocess 而不是直接 import
═══════════════════════════════════════════════════════════════════════════════
v3.3 的核心架构原则：
  - 每个"写"操作都必须通过唯一专用脚本入口；
  - cmd_send = 唯一发邮件入口（带自验证 + 入 talent_emails）
  - talent.cmd_delete = 唯一物理删档入口（带 v3.8.1 hard guard 自动归档 +
    self-verify + 审计事件）
  - 不允许别的脚本绕过它们直接 send_bg_email / 直接 SQL DELETE，否则
    self-verify / 一致性保证都绕过了。

═══════════════════════════════════════════════════════════════════════════════
v3.8.3 (2026-05-11) 行为回退：拒+物理删档（用户产品决策）
═══════════════════════════════════════════════════════════════════════════════
旧 v3.4：  scan 命中 → _send_rejection_email + _delete_talent（物理删 + 归档）
v3.5.11：  scan 命中 → _send_rejection_email + _mark_exam_rejected_keep
           （改 stage=EXAM_REJECT_KEEP 留池，CV / 笔试 / 邮件全保留）
v3.8.3：   scan 命中 → _send_rejection_email + _delete_talent
           （回到物理删档；v3.5.11 的安全护栏全部保留——见下方"为什么这次回退是安全的"）

设计回退动因（2026-05-11 用户决策）：
  - 业务侧：3 天未交即视为流程自然结束，候选人留池价值有限；
  - 池子膨胀：留池叶子态从未被自动清理，HR 抱怨"几个月前的 keep 还看得到"；
  - 数据保护：deleted_archive 仍保留完整 snapshot + 邮件 timeline，需要时可恢复。

═══════════════════════════════════════════════════════════════════════════════
为什么这次回退是安全的（v3.5.11 事故面如何兜住）
═══════════════════════════════════════════════════════════════════════════════
2026-04-22 11:30 cron tick 事故链路（INCIDENT_RULES §1 衍生）：
  1) cmd_send 写 talent_emails 时 _EMAIL_VALID_CONTEXTS 漏 'rejection' → SMTP
     已发但 DB 写库 raise；
  2) executor._run_cmd 看到 rc≠0 误判"发拒信失败"；
  3) 误判 → cmd_delete 没触发 → 人留 EXAM_SENT；
  4) 下个 tick 重发拒信 → 候选人收到第二封。

这次回退仍然兜得住,因为 v3.5.11 加进来的两层防线**没动**：
  - lib.talent_db._EMAIL_VALID_CONTEXTS 收录 'rejection' (v3.5.11 migration
    20260422_v3511_talent_emails_context_rejection.sql, v3.8.7 已删档; schema.sql
    chk_te_context 终态已内联 'rejection')
    → 根因 bug 已闭合；
  - cmd_scan_exam_timeout.find_timeout_candidates 内仍调
    talent_db.has_outbound_rejection(tid) 二次过滤
    → 即便本次 _delete_talent 抛错让人留 EXAM_SENT，下个 tick 也不会再发第二封。

换句话说：现在的 _delete_talent 失败 = 候选人留在 EXAM_SENT 等 HR 介入，
而不是"持续被发拒信"——和 v3.5.11 的"mark 失败 / 留池失败"对等。

═══════════════════════════════════════════════════════════════════════════════
talent.cmd_delete 的调用方式（v3.8.1 hard guard 适配）
═══════════════════════════════════════════════════════════════════════════════
talent.cmd_delete 必须带 --confirm-delete-talent <talent_id>（事故源
INCIDENT_RULES §12 / §13）。这里我们作为 system / cron 调用方,显式传一致的值
表明"知情授权"——cmd_delete docstring 明确把这种 case 写进了"何时合法传"。

详见 cmd_scan_exam_timeout 模块文档头 + INCIDENT_RULES.md §15。
"""
from __future__ import print_function

from typing import Any, Dict, Optional

from lib import talent_db  # noqa: F401  保留 import：未来 helper 可能复用
from lib.bg_helpers import send_outbound_template
from lib.cli_subprocess import run_module


# ─── 子进程调用 ─────────────────────────────────────────────────────────────
#
# v3.8.x 分层复用：本文件不再自己手写 subprocess 调度。
#   - 发拒信 → lib.bg_helpers.send_outbound_template（候选人邮件语义边界）
#   - 物理删档 → lib.cli_subprocess.run_module（通用 atomic CLI 调度层）
# 删档不能借邮件 helper（语义不对），但可以共享通用 sync 调度。详见
# plan: subprocess_helper_分层复用 + lib/cli_subprocess.py 模块头注释。

def _send_rejection_email(talent_id, template_name, reason, dry_run=False):
    # type: (str, str, str, bool) -> Dict[str, Any]
    """调 outbound.cmd_send 发模板模式拒信。返回 {ok, message_id, detail, raw}。

    复用 send_outbound_template()——它内部走 cli_subprocess.run_module()
    并自带 RECRUIT_DISABLE_SIDE_EFFECTS 短路 fake message_id 的语义。

    `dry_run` 参数当前在调用链上没人传 True（cmd_scan_exam_timeout 的 dry-run
    分支根本不会调本函数,而是更早就 continue 跳过）。保留参数只为接口稳定;
    若未来真要走 cmd_send --dry-run,需要给 send_outbound_template() 加一个
    dry_run=False 参数,而不是恢复本文件自己的 subprocess 路径。
    """
    res = send_outbound_template(
        talent_id=talent_id,
        template=template_name,
        context="rejection",
    )
    if not res["ok"]:
        return {
            "ok": False,
            "detail": "cmd_send failed: rc={} stderr={}".format(
                res.get("returncode"), (res.get("stderr") or "")[:300]),
            "raw": res,
        }
    return {
        "ok": True,
        "message_id": res.get("message_id"),
        "detail": "cmd_send OK (template={})".format(template_name),
        "raw": res,
    }


def _delete_talent(talent_id, message_id, reason, dry_run=False):
    # type: (str, Optional[str], str, bool) -> Dict[str, Any]
    """物理删档（含归档）：调 talent.cmd_delete 子进程。

    v3.8.3 起恢复使用（替代 v3.5.11 引入的 _mark_exam_rejected_keep 留池路径）。
    cmd_delete 内部已经做完：
      1) 完整 snapshot + 邮件 timeline 归档到 data/deleted_archive/<YYYY-MM>/
      2) candidate_dir（CV / 笔试题目目录）搬到 deleted_archive
      3) candidate alias 软链清理
      4) talent_events 写一条 'talent.deleted' 审计
      5) DELETE FROM talents（CASCADE 删 talent_emails / talent_events）
      6) self_verify.assert_talent_deleted 自验证

    所以本函数只负责把参数装好、跑子进程、解析返回。子进程调度走通用层
    lib.cli_subprocess.run_module()——它统一注入 PYTHONPATH /
    RECRUIT_WORKSPACE_ROOT,与历史本地 _run_cmd() 行为兼容（且补齐了
    _run_cmd 之前缺失的 RECRUIT_WORKSPACE_ROOT,对 cmd_delete 子进程是
    更对的环境）。

    --confirm-delete-talent 必须严格等于 --talent-id（v3.8.1 hard guard）；这里
    作为 system / cron 调用方显式传一致值表明知情授权。

    返回 {ok, detail, raw}：与原 _mark_exam_rejected_keep 保持同形。

    dry_run=True 时直接早返回不起子进程。这是 v3.5.11 留池路径就有的行为
    （只为 cmd_scan_exam_timeout 主流程不卡）,test_delete_dry_run_skips_subprocess
    在守这条契约——千万不要因为复用 helper 顺手把它改成"传 --dry-run 给子
    进程"，否则 dry-run 会真起 cmd_delete hard guard 校验,行为不再等价。
    """
    if dry_run:
        return {"ok": True, "detail": "[dry-run] would call talent.cmd_delete"}

    audit_reason = (
        "auto_reject:exam_no_reply (rejection_message_id={msg_id}, "
        "trigger=exam_timeout, reason={r})"
    ).format(msg_id=message_id or "?", r=reason)

    res = run_module("talent.cmd_delete", [
        "--talent-id", talent_id,
        "--confirm-delete-talent", talent_id,  # v3.8.1 hard guard：cron 显式授权
        "--reason", audit_reason,
        "--actor", "auto_reject.cmd_scan_exam_timeout",
        "--json",
    ], parse_json=True)
    if not res["ok"]:
        return {
            "ok": False,
            "detail": "cmd_delete failed: rc={} stderr={}".format(
                res["returncode"], (res["stderr"] or "")[:300]),
            "raw": res,
        }

    archive_path = None
    parsed = res.get("json") or {}
    if isinstance(parsed, dict):
        archive_path = parsed.get("archive_path")

    return {
        "ok": True,
        "detail": "cmd_delete OK (archive={})".format(archive_path or "?"),
        "archive_path": archive_path,
        "raw": res,
    }
