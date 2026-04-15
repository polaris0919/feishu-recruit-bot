#!/usr/bin/env python3
"""
让 recruit cron 任务在指定秒数后立即触发（不重启 gateway）。
用法:
  uv run python3 scripts/trigger_cron_now.py      # 默认 10 秒后触发
  uv run python3 scripts/trigger_cron_now.py 30   # 30 秒后触发
"""
import json, os, sys, time

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))

delay_sec = int(sys.argv[1]) if len(sys.argv) > 1 else 10
jobs_path = os.environ.get("RECRUIT_CRON_JOBS_PATH",
                           os.path.expanduser("~/.openclaw/cron/jobs.json"))
target_ids = ("recruit-email-auto-scan", "recruit-interview-reminder")

now_ms = int(time.time() * 1000)
trigger_ms = now_ms + delay_sec * 1000

with open(jobs_path) as f:
    d = json.load(f)

for job in d["jobs"]:
    if job.get("id") in target_ids:
        if "state" not in job:
            job["state"] = {}
        job["state"]["nextRunAtMs"] = trigger_ms
        print("  {} -> {}秒后触发".format(job["id"], delay_sec))

with open(jobs_path, "w") as f:
    json.dump(d, f, indent=2, ensure_ascii=False)

print("已设置，gateway 将在约 {}s 后执行 cron 任务".format(delay_sec))
