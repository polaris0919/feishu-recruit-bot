"""inbox/ — 入站邮件收/分析/复盘三件套（v3.3）。

每个 cmd_* 脚本是单一职责：
  cmd_scan.py    —— IMAP 拉新邮件 → 入 talent_emails（无 LLM）
  cmd_analyze.py —— LLM 分析 pending 邮件 → 写 ai_intent / summary / analyzed_at
  cmd_review.py  —— 查询 + 渲染候选人完整邮件 timeline（read-only）
"""
