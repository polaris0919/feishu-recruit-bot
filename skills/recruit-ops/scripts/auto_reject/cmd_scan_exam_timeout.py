#!/usr/bin/env python3
"""auto_reject/cmd_scan_exam_timeout.py —— 笔试超时：拒信 + 物理删档（v3.8.3）。

═══════════════════════════════════════════════════════════════════════════════
触发条件（全部满足）
═══════════════════════════════════════════════════════════════════════════════
1. current_stage = EXAM_SENT
2. exam_sent_at 距今 ≥ --threshold-days（默认 3 天，从 exam_sent_at 起算）
3. 双重 check：talent_emails 里没有 exam_sent_at 之后的 inbound 邮件
   （防止候选人刚交卷 / 刚提问还没被 cmd_analyze 处理就被误拒）
4. 三重 check（v3.5.11 引入，v3.8.3 保留）：talent_emails 里没有任何
   context=rejection 的 outbound 邮件——即便上一轮 cmd_delete 没成功,只要拒信
   已发就不再发第二封。这是 v3.5.11 事故（2026-04-22）沉淀的关键防线,本次
   v3.8.3 回退到"拒+删"时**显式保留**。

═══════════════════════════════════════════════════════════════════════════════
行为（v3.8.3：回到"拒+物理删档"）
═══════════════════════════════════════════════════════════════════════════════
- 对每位命中候选人：
  a. 调 outbound.cmd_send 发 rejection_exam_no_reply 模板拒信；
  b. 调 executor._delete_talent → talent.cmd_delete（含 --confirm-delete-talent
     hard guard）：自动归档完整 snapshot + 邮件 timeline + candidate_dir 到
     data/deleted_archive/<YYYY-MM>/，再 DELETE FROM talents（CASCADE）+ self-verify；
  c. 推一条飞书事后通知（"已自动拒+删档 t_xxx, archive=..."）。

候选人**不在**活跃人才库（DB 已删），但 data/deleted_archive 里仍保留完整
snapshot + 邮件 timeline + CV 文件目录,需要时可以人工恢复（grep 归档目录拿
JSON 再 talent.cmd_add 重新建档）。

═══════════════════════════════════════════════════════════════════════════════
版本历史
═══════════════════════════════════════════════════════════════════════════════
- v3.4 旧版：扫描 → 拒信 + 物理删档。失败模式：cmd_send 崩在 DB 写库
  （_EMAIL_VALID_CONTEXTS 缺 'rejection'），SMTP 已发但 cmd_delete 没触发 →
  人留 EXAM_SENT → 下个 cron tick 再发一封。2026-04-22 11:30 真出过事故。
- v3.5.11（2026-04-22 当天）：根因 bug 修了（_EMAIL_VALID_CONTEXTS + DB CHECK
  都加 'rejection'），但**主动收紧**改成"拒+留池 EXAM_REJECT_KEEP"+ 加
  has_outbound_rejection 二次防护——既闭合根因又拿到幂等性。
- **v3.8.3（2026-05-11，本次）**：用户产品决策——3 天未交 = 流程自然结束，
  候选人留池价值有限，且 EXAM_REJECT_KEEP 叶子态从未被自动清理导致池子膨胀。
  回退到"拒+物理删档"，但 v3.5.11 加的两道防线（_EMAIL_VALID_CONTEXTS 闭合 +
  has_outbound_rejection 二次防护）**全部保留**——_delete_talent 失败时,候选人
  会留在 EXAM_SENT 等 HR 介入,而不是"持续被发拒信"。详见 INCIDENT_RULES.md §15。

═══════════════════════════════════════════════════════════════════════════════
CLI
═══════════════════════════════════════════════════════════════════════════════
  python3 -m auto_reject.cmd_scan_exam_timeout [--auto] [--threshold-days 3] [--dry-run] [--no-feishu]
    --auto              cron 模式，无命中时静默
    --threshold-days N  超时阈值天数（默认 3）
    --dry-run           只列名单，不发邮件不删档
    --no-feishu         不推单条事后通知（cron_runner 会另推汇总）
"""
from __future__ import print_function

import argparse
import sys
from typing import Any, Dict, List, Optional

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
            # 已经发过拒信了——要么是上一轮 cmd_delete 没成功（DB 异常 / 网络抖动），
            # 要么是别的路径手动发的拒信。无论如何不再发第二封；把候选人交给 HR
            # 介入决定（手动 talent.cmd_delete 或改 stage 重启流程）。
            # 这条防线在 v3.5.11 引入,v3.8.3 回退"拒+留池→拒+删档"时显式保留。
            print("[scan_exam_timeout] 跳过 {}：已存在 outbound rejection 邮件，"
                  "请人工 talent.cmd_delete --confirm-delete-talent {} 或改 stage".format(tid, tid),
                  file=sys.stderr)
            continue
        out.append(row)
    return out


def _build_parser():
    p = argparse.ArgumentParser(description="扫描笔试超时未回复候选人，发拒信+删档归档")
    p.add_argument("--auto", action="store_true", help="cron 模式，无命中时静默")
    p.add_argument("--threshold-days", type=int, default=3)
    p.add_argument("--dry-run", action="store_true",
                   help="只列名单，不真正发邮件 / 删档")
    p.add_argument("--no-feishu", action="store_true",
                   help="不推单条事后通知（cron_runner 会另推汇总）")
    return p


def _format_feishu_notice(name, tid, sent_str, message_id, template, archive_path):
    # type: (str, str, str, str, str, Optional[str]) -> str
    return (
        "[笔试超时 · 已自动拒+删档]\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "候选人：{} ({})\n"
        "笔试发送时间：{}\n"
        "已发送模板：{}\n"
        "拒信 message_id：{}\n"
        "stage：EXAM_SENT → 已物理删除（DB 行已不存在）\n"
        "归档位置：{}\n"
        "如系误判：从归档 JSON 取字段后 talent.cmd_add 重新建档"
    ).format(name, tid, sent_str, template, message_id or "?",
             archive_path or "(未返回 archive_path,请检查 deleted_archive/)")


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
            lines.append("  · {} ({}) 笔试发于 {} —— [dry-run] 将拒+删档".format(
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

        # 步骤 2: 物理删档（v3.8.3 回到 cmd_delete；v3.5.11 引入的
        # has_outbound_rejection 二次防护仍兜底防止"拒信已发但删档失败 → 下个
        # cron tick 重发"事故复发）
        del_res = executor._delete_talent(tid, message_id, "exam_no_reply")
        if not del_res["ok"]:
            # 拒信已发但 cmd_delete 失败 —— 下次扫描会被 has_outbound_rejection
            # 拦下，不会重发拒信。这里标 failed 让 HR 介入手工删档。
            failed += 1
            lines.append(
                "  · {} ({}) ⚠ 拒信已发 (msg_id={}) 但 cmd_delete 失败: {} —— "
                "请手工 talent.cmd_delete --talent-id {tid} --confirm-delete-talent {tid} "
                "--reason 'auto_reject manual cleanup'".format(
                    name, tid, message_id, del_res.get("detail"), tid=tid))
            continue

        archive_path = del_res.get("archive_path")
        rejected += 1
        lines.append("  · {} ({}) 笔试发于 {} → 已拒+删档 (msg_id={}, archive={})".format(
            name, tid, sent_str, message_id or "?", archive_path or "?"))

        if not args.no_feishu:
            try:
                from lib import feishu
                feishu.send_text(_format_feishu_notice(
                    name, tid, sent_str, message_id, template, archive_path))
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
