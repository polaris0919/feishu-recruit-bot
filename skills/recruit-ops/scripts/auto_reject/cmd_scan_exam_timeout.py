#!/usr/bin/env python3
"""auto_reject/cmd_scan_exam_timeout.py —— 笔试超时：拒信 + 留池（v3.5.11）。

═══════════════════════════════════════════════════════════════════════════════
触发条件（全部满足）
═══════════════════════════════════════════════════════════════════════════════
1. current_stage = EXAM_SENT
2. exam_sent_at 距今 ≥ --threshold-days（默认 3 天，从 exam_sent_at 起算）
3. 双重 check：talent_emails 里没有 exam_sent_at 之后的 inbound 邮件
   （防止候选人刚交卷 / 刚提问还没被 cmd_analyze 处理就被误拒）
4. 三重 check（v3.5.11 新增）：talent_emails 里没有任何 context=rejection 的
   outbound 邮件（即便 stage 因任何原因没改成功，只要拒信已发就不再发第二封）

═══════════════════════════════════════════════════════════════════════════════
行为（v3.5.11：拒+留池替代拒+物理删）
═══════════════════════════════════════════════════════════════════════════════
- 对每位命中候选人：
  a. 调 outbound.cmd_send 发 rejection_exam_no_reply 模板拒信；
  b. 调 executor._mark_exam_rejected_keep：把 stage 推到 EXAM_REJECT_KEEP，
     写一条 talent_event 审计（包含 message_id / from_stage / trigger）；
  c. 推一条飞书事后通知（"已自动拒，已留池 t_xxx"）。

候选人留在人才库：CV / 笔试题 / 邮件 / talent_events 全保留，HR 后续
还能 talent.cmd_show 查档；如果觉得误判，cmd_update --set current_stage=NEW。

═══════════════════════════════════════════════════════════════════════════════
设计取舍（2026-04-22 v3.5.11 重设计）
═══════════════════════════════════════════════════════════════════════════════
- v3.4 旧版：扫描 → 立刻发拒信 + 物理删档（`talent.cmd_delete`，进 deleted_archive）。
  失败模式：cmd_send 崩在 DB 写入校验（`非法 context: 'rejection'`），SMTP
  已发出但 cmd_delete 没触发 → 人留在 EXAM_SENT → 下个 cron tick 再发一封。
  2026-04-22 11:30 cron tick 真出现了（见 docs/CHANGELOG.md）。
- v3.5.11 新版：扫描 → 发拒信 + 改 stage 留池。
  * 候选人不丢档：HR 抱怨"3 天没回复就被删了能不能留个底"早期需求复活。
  * 幂等：stage 一改就再也扫不到；即便 mark 那步崩了，还有"已发过 outbound
    rejection 就跳过"的兜底防护。
  * 审计完整：talent_events 写明 from_stage / trigger / rejection_message_id。
  * 损失可逆：误判时 `talent.cmd_update --set current_stage=NEW` 一条命令搞回来。

═══════════════════════════════════════════════════════════════════════════════
CLI
═══════════════════════════════════════════════════════════════════════════════
  python3 -m auto_reject.cmd_scan_exam_timeout [--auto] [--threshold-days 3] [--dry-run] [--no-feishu]
    --auto              cron 模式，无命中时静默
    --threshold-days N  超时阈值天数（默认 3）
    --dry-run           只列名单，不发邮件不改 stage
    --no-feishu         不推单条事后通知（cron_runner 会另推汇总）
"""
from __future__ import print_function

import argparse
import sys
from typing import Any, Dict, List

from lib import talent_db
from lib.cli_wrapper import run_with_self_verify, UserInputError
from auto_reject import executor


def find_timeout_candidates(threshold_days=3):
    # type: (int) -> List[Dict[str, Any]]
    """返回符合超时条件的候选人列表。三重 check：
    1) get_exam_timeout_candidates(threshold_days) ：DB 层 stage=EXAM_SENT 且超时
    2) has_inbound_email_after(exam_sent_at) ：候选人没有交卷之后的来信
    3) has_outbound_rejection(tid) ：还没发过拒信（v3.5.11 兜底，防重发）
    """
    raw = talent_db.get_exam_timeout_candidates(threshold_days=threshold_days)
    out = []
    for row in raw:
        tid = row.get("talent_id")
        if not tid:
            continue
        sent_at = row.get("exam_sent_at")
        if sent_at and talent_db.has_inbound_email_after(tid, sent_at):
            continue
        if talent_db.has_outbound_rejection(tid):
            # 已经发过拒信了，要么是上次 mark stage 的步骤崩了（v3.5.11 之前的事故
            # 残留），要么是别的路径手动发的拒信。无论如何不再发第二封——把它的
            # stage 修一下交给 HR 就行。
            print("[scan_exam_timeout] 跳过 {}：已存在 outbound rejection 邮件，"
                  "请人工把 stage 改成 EXAM_REJECT_KEEP".format(tid),
                  file=sys.stderr)
            continue
        out.append(row)
    return out


def _build_parser():
    p = argparse.ArgumentParser(description="扫描笔试超时未回复候选人，发拒信+留池")
    p.add_argument("--auto", action="store_true", help="cron 模式，无命中时静默")
    p.add_argument("--threshold-days", type=int, default=3)
    p.add_argument("--dry-run", action="store_true",
                   help="只列名单，不真正发邮件 / 改 stage")
    p.add_argument("--no-feishu", action="store_true",
                   help="不推单条事后通知（cron_runner 会另推汇总）")
    return p


def _format_feishu_notice(name, tid, sent_str, message_id, template):
    # type: (str, str, str, str, str) -> str
    return (
        "[笔试超时 · 已自动拒+留池]\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "候选人：{} ({})\n"
        "笔试发送时间：{}\n"
        "已发送模板：{}\n"
        "拒信 message_id：{}\n"
        "stage：EXAM_SENT → EXAM_REJECT_KEEP（留人才库）\n"
        "如系误判：talent.cmd_update --talent-id {} --set current_stage=NEW"
    ).format(name, tid, sent_str, template, message_id or "?", tid)


def _do_scan(args):
    if not talent_db._is_enabled():
        raise UserInputError("talent_db 未启用，无法扫描。")

    candidates = find_timeout_candidates(threshold_days=args.threshold_days)

    if not candidates:
        if not args.auto:
            print("[scan_exam_timeout] 当前没有 EXAM_SENT 超过 {} 天且无回复的候选人。".format(
                args.threshold_days))
        return 0

    rejected = 0
    skipped = 0
    failed = 0
    template = "rejection_exam_no_reply"
    lines = ["[笔试超时扫描] 发现 {} 位候选人 EXAM_SENT 已超 {} 天且未提交。".format(
        len(candidates), args.threshold_days)]

    for row in candidates:
        tid = row["talent_id"]
        name = row.get("candidate_name") or tid
        sent_at = row.get("exam_sent_at")
        sent_str = str(sent_at)[:16] if sent_at else "?"

        if args.dry_run:
            lines.append("  · {} ({}) 笔试发于 {} —— [dry-run] 将拒+留池".format(
                name, tid, sent_str))
            rejected += 1
            continue

        # 步骤 1: 发拒信（cmd_send 自带 self-verify + 写 talent_emails；
        # v3.5.11 修了 _EMAIL_VALID_CONTEXTS 缺 'rejection' 的根因 bug。）
        send_res = executor._send_rejection_email(tid, template, "exam_no_reply")
        if not send_res["ok"]:
            failed += 1
            lines.append("  · {} ({}) ⚠ 发拒信失败: {}".format(
                name, tid, send_res.get("detail")))
            continue

        message_id = send_res.get("message_id")

        # 步骤 2: 改 stage 留池（v3.5.11 替代旧的 cmd_delete）
        mark_res = executor._mark_exam_rejected_keep(tid, message_id, "exam_no_reply")
        if not mark_res["ok"]:
            # 拒信已发出，但 stage 没改成功——下次扫描会被 has_outbound_rejection
            # 拦下来，不会重发。这里仍然标 failed 让 HR 介入修 stage。
            failed += 1
            lines.append(
                "  · {} ({}) ⚠ 拒信已发 (msg_id={}) 但 mark stage 失败: {} —— "
                "请手工 talent.cmd_update --set current_stage=EXAM_REJECT_KEEP".format(
                    name, tid, message_id, mark_res.get("detail")))
            continue

        rejected += 1
        lines.append("  · {} ({}) 笔试发于 {} → 已拒+留池 (msg_id={})".format(
            name, tid, sent_str, message_id or "?"))

        if not args.no_feishu:
            try:
                from lib import feishu
                feishu.send_text(_format_feishu_notice(
                    name, tid, sent_str, message_id, template))
            except Exception as e:
                print("⚠ 飞书事后通知失败: {}".format(e), file=sys.stderr)

    lines.append("")
    lines.append("汇总：rejected={}, skipped={}, failed={}".format(
        rejected, skipped, failed))
    print("\n".join(lines))
    return 0 if failed == 0 else 1


def main(argv=None):
    args = _build_parser().parse_args(argv)
    return _do_scan(args)


if __name__ == "__main__":
    run_with_self_verify("auto_reject.cmd_scan_exam_timeout", main)
