"""outbound/ — 出站邮件统一入口（v3.3）。

唯一脚本 cmd_send.py 支持两种模式：
  --template T [--vars k=v]   模板模式（推荐，文案集中维护）
  --subject S --body "..." | --body-file F   自由文本模式（agent 起草后老板确认）

零业务副作用：不动 talents.current_stage、不动任何业务字段，仅写 talent_emails 一行。
状态机变更必须显式调用 talent/cmd_update.py。
"""
