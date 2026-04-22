-- ═══════════════════════════════════════════════════════════════════════════════
-- v3.3 解耦架构 schema 扩展（2026-04-17）
--
-- 背景：v3.3 把 outbound 邮件统一交给 outbound/cmd_send，需要在 talent_emails
-- 表里记录这封邮件用了哪个模板（用于 cmd_review / 一致性查询 / 后续审计）。
-- LLM 分析也需要一个独立的时间戳字段，区分 "邮件入库时间" 和 "LLM 分析时间"。
--
-- 仅追加两列，幂等可重跑：
--   1. talent_emails.template       —— outbound 邮件用的模板名（freeform 时为字符串 'freeform'）
--   2. talent_emails.analyzed_at    —— inbox/cmd_analyze 完成 LLM 分析的时间戳
--
-- 不会动现有列，回滚仅需 DROP COLUMN。
-- ═══════════════════════════════════════════════════════════════════════════════

ALTER TABLE talent_emails
    ADD COLUMN IF NOT EXISTS template    TEXT;

ALTER TABLE talent_emails
    ADD COLUMN IF NOT EXISTS analyzed_at TIMESTAMPTZ;

-- 出站邮件按模板查询（cmd_review / consistency 排查用）
CREATE INDEX IF NOT EXISTS idx_te_outbound_template
    ON talent_emails (talent_id, template, sent_at DESC)
    WHERE direction = 'outbound';

-- 待 LLM 分析的入站邮件（cmd_analyze 主队列）
CREATE INDEX IF NOT EXISTS idx_te_pending_analyze
    ON talent_emails (sent_at)
    WHERE direction = 'inbound' AND analyzed_at IS NULL;
