"""cron/ — 周期任务编排（v3.3）。

  cron_runner.py        —— 单一入口，按时间表调度各 cmd_*。
                           替代旧 scripts/cron_runner.py。
  cmd_review_reminder.py —— v3.8 新增。EXAM_REVIEWED 持续 N 小时未拍板时催老板。
                           仅 cron 调用,不应被 chain / agent 直接 invoke。
"""
