#!/usr/bin/env python3
"""
面试完成后自动催问面试评价与决定（cron 触发）。
- 一面：ROUND1_SCHEDULED 且面试时间已过 → 催问一面结果
- 二面：ROUND2_SCHEDULED / ROUND2_DONE_PENDING 且面试时间已过 → 催问二面结果
"""
import sys
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import feishu_notify
import talent_db


def run():
    if not talent_db._is_enabled():
        print("[interview_reminder] DB 未配置，跳过", file=sys.stderr)
        return 0

    messages = []

    # ── 一面催问 ──────────────────────────────────────────────────
    round1_pending = talent_db.get_pending_round1_reminders()
    for item in round1_pending:
        tid = item["talent_id"]
        name = item.get("candidate_name", tid)
        r1time = item.get("round1_time", "")
        elapsed = item.get("elapsed_minutes", 30)

        elapsed_str = "{}分钟".format(elapsed) if elapsed < 60 else "约{:.1f}小时".format(elapsed / 60)
        msg = (
            "🔔 一面结果催问提醒\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "候选人：{name}（{tid}）\n"
            "预定一面时间：{r1time}\n"
            "已过去：{elapsed_str}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "请问一面结果如何？\n"
            "  · 如通过 → 回复：{name} 一面通过\n"
            "  · 如拒绝 → 回复：{name} 一面不通过\n"
            "  · 如直接进二面 → 回复：{name} 一面通过，直接安排二面"
        ).format(tid=tid, name=name, r1time=r1time, elapsed_str=elapsed_str)

        messages.append(("round1", tid, msg))

    # ── 二面催问 ──────────────────────────────────────────────────
    round2_pending = talent_db.get_pending_interview_reminders()
    for item in round2_pending:
        tid = item["talent_id"]
        name = item.get("candidate_name", tid)
        r2time = item.get("round2_time", "")
        elapsed = item.get("elapsed_minutes", 30)

        elapsed_str = "{}分钟".format(elapsed) if elapsed < 60 else "约{:.1f}小时".format(elapsed / 60)
        msg = (
            "🔔 二面结果催问提醒\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "候选人：{name}（{tid}）\n"
            "预定二面时间：{r2time}\n"
            "已过去：{elapsed_str}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "请问二面结果如何？\n"
            "  · 如通过 → 回复：{name} 二面通过\n"
            "  · 如拒绝 → 回复：{name} 二面不通过\n"
            "  · 如需再考虑 → 回复：{name} 二面待定"
        ).format(tid=tid, name=name, r2time=r2time, elapsed_str=elapsed_str)

        messages.append(("round2", tid, msg))

    if not messages:
        print("[interview_reminder] 暂无需催问的候选人")
        return 0

    sent_count = 0
    for round_type, tid, msg in messages:
        ok = feishu_notify.send_text(msg)
        if ok:
            if round_type == "round1":
                talent_db.mark_round1_reminded(tid)
            else:
                talent_db.mark_interview_reminded(tid)
            sent_count += 1
            print("[interview_reminder] 已催问候选人 {} ({})".format(tid, round_type))
        else:
            print("[interview_reminder] 发送失败: {}".format(tid), file=sys.stderr)

    return 0


def main(argv=None):
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
