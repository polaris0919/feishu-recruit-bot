"""ops/ — 跨 sink 的运维脚本（v3.5）。

  cmd_db_migrate.py        —— 应用 lib/migrations/*.sql（幂等）
  cmd_health_check.py      —— DB / IMAP / SMTP / DashScope / 飞书连通性自检
  cmd_replay_notifications.py —— 重新推送某个时间窗口内的失败通知

  v3.5 起 cmd_push_alert 已搬到 feishu/cmd_notify（飞书 sink 自有 atomic CLI 集合）。
"""
