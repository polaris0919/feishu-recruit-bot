#!/usr/bin/env python3
"""
二面完成后 30 分钟自动催问二面评价与决定（cron 触发）。
只处理处于 ROUND2_SCHEDULED / ROUND2_DONE_PENDING 且未发过提醒的候选人。
"""
import sys
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import feishu_notify
import talent_db


def run():
    if not talent_db._is_enabled():
        print("[interview_reminder] DB 未配置，跳过", file=sys.stderr)
        return 0

    pendinglist = talent_db.get_pending_interview_reminders()
    if not pendinglist:
        return 0

    messages = []
    for item in pendinglist:
        tid = item["talent_id"]
        email = item.get("candidate_email", "")
        r2time = item.get("round2_time", "")
        elapsed = item.get("elapsed_minutes", 30)

        msg = (
            "🔔 **二面结果催问提醒**\n\n"
            "候选人 `{tid}` 的二面已在约 {elapsed} 分钟前结束（预定时间：{r2time}）。\n\n"
            "请问：\n"
            "1. 二面结果如何？（通过 / 拒绝 / 需再考虑）\n"
            "2. 有什么具体的面试反馈或评价吗？\n\n"
            "如已有决定，请告知，我会立即更新系统记录。"
        ).format(tid=tid, elapsed=elapsed, r2time=r2time, email=email)

        messages.append((tid, msg))

    sent_count = 0
    for tid, msg in messages:
        ok = feishu_notify.send_text(msg)
        if ok:
            talent_db.mark_interview_reminded(tid)
            sent_count += 1
            print("[interview_reminder] 已催问候选人 {}".format(tid))
        else:
            print("[interview_reminder] 发送失败: {}".format(tid), file=sys.stderr)

    return 0


def main(argv=None):
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
