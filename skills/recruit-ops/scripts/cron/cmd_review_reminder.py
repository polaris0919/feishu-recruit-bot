#!/usr/bin/env python3
"""cron/cmd_review_reminder.py —— v3.8 笔试已评审但老板长期未拍板提醒。

【背景 / AGENT_RULES.md §3】
  EXAM_SENT 阶段候选人 3 天未交,由 auto_reject.cmd_scan_exam_timeout 兜底（拒信+删档归档）。
  EXAM_REVIEWED 阶段（候选人已交,AI 已评审,等老板拍板"通过 / 留池 / 删档")
  之前**没有任何兜底机制** —— 老板很久不拍板的话候选人会以为公司鸽他。
  v3.8 起本 cron 任务每 3h 给老板推一次飞书提醒,直到老板拍板让候选人脱离
  EXAM_REVIEWED stage 为止。

【触发逻辑】
  对每个 current_stage='EXAM_REVIEWED' 的候选人:
    last_at = MAX(at) from talent_events
              where action IN ('exam_ai_review', 'exam_review_reminder_sent')
    （即:最近一次 AI 评审落地时间,或最近一次本 cron 推过提醒的时间）
    如果 last_at 距今 >= REMINDER_INTERVAL_HOURS（默认 3h)
      → 推一条飞书消息给老板（含候选人姓名 / 笔试 AI 评分 / 推荐 atomic CLI)
      → 写 talent_events 一行 action='exam_review_reminder_sent'(事后可审计 + 下一轮幂等)

【与 cron_runner 的衔接】
  本任务在 cron_runner._TASKS 里独立列一行（v3.8 注册),notify_stdout=False
  —— 任务内部对每个被提醒的候选人单独 feishu.send_text,所以 cron_runner 不要重推。
  与其他 cron 任务一样:失败 → cron_runner 自动 [CRON FAIL] 报警;成功静默进 journal。

【运维 / 调试】
  python3 -m cron.cmd_review_reminder              # 完整跑一轮
  python3 -m cron.cmd_review_reminder --dry-run    # 只打印将推谁,不真发
  python3 -m cron.cmd_review_reminder --interval-hours 6   # 临时改阈值（默认 3h）

【设计取舍】
  - 用 talent_events 做幂等,而**不**在 talents 表加 exam_review_reminded_at 字段
    —— 不想为这一个 cron 改 schema;3h 间隔精度足够。
  - 不主动推送 candidate_email/AI 评分等敏感细节给候选人——这是给老板的内部催问。
  - 不限定推送次数上限——只要老板还没拍板,就一直推（每 3h 一次）。如果 EXAM_REVIEWED
    持续 24h 没动,会推 ~8 次,这是设计意图（不让"忘了拍板"无限拖)。

【相关文档】
  - AGENT_RULES.md §3 / §5.8
  - cron/cron_runner.py（任务编排）
  - lib/talent_db.py::save_audit_event（事件写入)
"""
from __future__ import print_function

import argparse
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from lib import feishu
from lib import talent_db


REMINDER_INTERVAL_HOURS_DEFAULT = 3.0


def _list_stale_exam_reviewed(interval_hours):
    # type: (float) -> List[Dict[str, Any]]
    """查 EXAM_REVIEWED stage 且最近 `interval_hours` 内没"刚评审 / 刚被提醒"的候选人。

    用一条 SQL 拿到候选人 + 最后相关事件时间,在 Python 侧过滤。

    SQL 设计：
      - JOIN talent_events 取最近的 exam_ai_review / exam_review_reminder_sent；
      - 没有任何相关事件时（理论上不该有 EXAM_REVIEWED 但没 exam_ai_review 事件
        的情况,除非历史数据 / 手工 force-jump 进的 EXAM_REVIEWED）也算 stale —
        让老板看到这种异常状态。
    """
    sql = """
        SELECT
            t.talent_id,
            t.candidate_name,
            t.candidate_email,
            (
                SELECT MAX(at)
                FROM talent_events e
                WHERE e.talent_id = t.talent_id
                  AND e.action IN ('exam_ai_review', 'exam_review_reminder_sent')
            ) AS last_relevant_at
        FROM talents t
        WHERE t.current_stage = 'EXAM_REVIEWED'
        ORDER BY t.talent_id ASC
    """
    rows = talent_db._query_all(sql)
    out = []
    now = datetime.now()
    for r in rows:
        last_at = r.get("last_relevant_at")
        if last_at is None:
            elapsed_h = float("inf")
        else:
            try:
                if hasattr(last_at, "timestamp"):
                    last_dt = datetime.fromtimestamp(last_at.timestamp())
                else:
                    last_dt = datetime.fromisoformat(str(last_at).replace(" ", "T"))
                elapsed_h = (now - last_dt).total_seconds() / 3600.0
            except Exception:
                elapsed_h = float("inf")
        if elapsed_h >= interval_hours:
            out.append({
                "talent_id": r["talent_id"],
                "candidate_name": r.get("candidate_name") or "",
                "candidate_email": r.get("candidate_email") or "",
                "elapsed_hours": elapsed_h,
            })
    return out


def _format_reminder_message(item):
    # type: (Dict[str, Any]) -> str
    tid = item["talent_id"]
    name = item.get("candidate_name") or tid
    elapsed_h = item.get("elapsed_hours", 0.0)
    if elapsed_h == float("inf"):
        elapsed_str = "时长未知（无 exam_ai_review 事件）"
    elif elapsed_h < 1:
        elapsed_str = "{}分钟".format(int(elapsed_h * 60))
    elif elapsed_h < 24:
        elapsed_str = "约{:.1f}小时".format(elapsed_h)
    else:
        elapsed_str = "约{:.1f}天".format(elapsed_h / 24)
    return (
        "🔔 笔试评审待拍板提醒\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "候选人：{name}（{tid}）\n"
        "stage：EXAM_REVIEWED\n"
        "距上次评审 / 提醒：{elapsed_str}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "AI 评审已完成,等待您拍板。请直接在飞书回话给 bot：\n"
        "  · 通过 + 二面时间 → \"{name} 笔试通过,二面 YYYY-MM-DD HH:MM\"\n"
        "      （走 §4.5 chain 或 §4.11 atomic 等价路径）\n"
        "  · 不过留池      → \"{name} 笔试不过,留池\"\n"
        "      （走 §4.6 chain 或 §4.11 atomic 等价路径）\n"
        "\n"
        "Hermes Gateway 收到您的飞书消息后会路由给 agent 自动跑 chain。\n"
        "如已拍板但 stage 还是 EXAM_REVIEWED,在飞书回 \"查 {name} 状态\" 即可。"
    ).format(name=name, tid=tid, elapsed_str=elapsed_str)


def run(interval_hours, dry_run=False):
    # type: (float, bool) -> int
    if not talent_db._is_enabled():
        print("[review_reminder] DB 未配置,跳过", file=sys.stderr)
        return 0

    items = _list_stale_exam_reviewed(interval_hours)
    if not items:
        print("[review_reminder] 暂无 EXAM_REVIEWED ≥ {}h 的待拍板候选人".format(interval_hours))
        return 0

    pushed = 0
    failed = 0
    for item in items:
        msg = _format_reminder_message(item)
        if dry_run:
            print("[review_reminder][dry-run] 将推: {}".format(item["talent_id"]))
            print(msg)
            print("---")
            continue
        ok = feishu.send_text(msg)
        if ok:
            try:
                talent_db.save_audit_event(
                    item["talent_id"],
                    "exam_review_reminder_sent",
                    payload={"elapsed_hours": item["elapsed_hours"]},
                    actor="cron.cmd_review_reminder",
                )
            except Exception as e:
                print("[review_reminder] save_audit_event 失败 tid={}: {}".format(
                    item["talent_id"], e), file=sys.stderr)
            pushed += 1
            print("[review_reminder] 已提醒 {}".format(item["talent_id"]))
        else:
            failed += 1
            print("[review_reminder] 飞书推送失败 tid={}".format(
                item["talent_id"]), file=sys.stderr)

    print("[review_reminder] 本轮 pushed={} failed={} total={}".format(
        pushed, failed, len(items)))
    return 0 if failed == 0 else 1


def _build_parser():
    p = argparse.ArgumentParser(
        prog="cron.cmd_review_reminder",
        description="v3.8 EXAM_REVIEWED 持续 N 小时未拍板提醒老板（仅 cron 调用）",
    )
    p.add_argument("--interval-hours", type=float,
                   default=REMINDER_INTERVAL_HOURS_DEFAULT,
                   help="提醒阈值（小时,默认 {}）".format(REMINDER_INTERVAL_HOURS_DEFAULT))
    p.add_argument("--dry-run", action="store_true",
                   help="只打印将推送哪些候选人,不真发飞书也不写 audit")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    return run(args.interval_hours, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
