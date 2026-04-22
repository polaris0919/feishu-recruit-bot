#!/usr/bin/env python3
"""
自动拒（auto_reject）模块 — 笔试超时自动拒+留池（v3.5.11 起；v3.4 起单脚本简化版）。

═══════════════════════════════════════════════════════════════════════════════
模块结构
═══════════════════════════════════════════════════════════════════════════════
- executor.py                  helpers：
                                 _send_rejection_email → subprocess outbound.cmd_send
                                 _mark_exam_rejected_keep → in-process talent_db.set_current_stage
- cmd_scan_exam_timeout.py     扫超 N 天未交卷 → 立刻拒信 + 推 EXAM_REJECT_KEEP 留池 + 飞书事后通知

═══════════════════════════════════════════════════════════════════════════════
v3.5.11 (2026-04-22) 重设计：拒+留池替代拒+物理删档
═══════════════════════════════════════════════════════════════════════════════
事故触发：cron tick 11:30 报警 [CRON FAIL] failed=3，3 个候选人收到拒信但
stage 没改、talent_emails 没记，下个 tick 会重发——根因是 cmd_send 在 SMTP 已发后
才 hit DB context 校验，Python 白名单和 DB CHECK 约束都漏了 'rejection'。

新设计的好处：
  - 候选人 CV / 笔试 / 邮件历史全保留，HR 后续仍可 talent.cmd_show 查档
  - stage 一改就再也扫不到（天然幂等）；即便 mark stage 那步崩了，
    has_outbound_rejection 兜底拦截绝不重发
  - 误判可逆：talent.cmd_update --stage NEW 一条命令救回来

═══════════════════════════════════════════════════════════════════════════════
2026-04-23 简化备注（v3.4 沿用至今）
═══════════════════════════════════════════════════════════════════════════════
本模块过去支持「软自动化拒删」：
  - propose → 12h 缓冲队列 → cron execute_due → 老板可 cancel
  - 改期请求 < 24h + LLM 判 casual → 入队拒删
经过 review 决策回归到两件事各自最简：
  - 临近改期：交还给老板裁决（普通 reschedule 报告，不再 LLM 判合理性）
  - 笔试 ≥3 天无回复：立刻拒+留池（无 12h 缓冲、无撤销、双重 check 已在 scan 阶段做）

如果将来需要恢复"缓冲 + 撤销"机制，参考 git 历史中的 propose / cancel /
execute_due / pending_store 实现。
"""
