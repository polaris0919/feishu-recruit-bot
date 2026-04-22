-- 2026-04-22 v3.5.11  talent_emails.context CHECK 约束加入 'rejection'
--
-- 事故背景（2026-04-22 11:30 cron tick）：
--   auto_reject.cmd_scan_exam_timeout 一直以 --context rejection 调
--   outbound.cmd_send 发拒信。但：
--     1) Python 层 lib.talent_db._EMAIL_VALID_CONTEXTS 漏 'rejection'
--     2) DB 层 chk_te_context CHECK 约束也漏 'rejection'
--   cmd_send 在 SMTP send_email_with_threading() 之后才走到
--   insert_email_if_absent，结果"邮件已发 / DB 校验崩 / executor 误判失败 /
--   stage 没改 / 下个 cron tick 又重发"。这一坨第一次被触发，是 11:30
--   cron tick；同步 Feishu 报警 [CRON FAIL] 笔试超时直接拒删 failed=3。
--
-- 修复（v3.5.11）：
--   - lib.talent_db._EMAIL_VALID_CONTEXTS 加 'rejection'（已 commit）
--   - 本 migration：DB CHECK 约束加 'rejection'
--   - auto_reject 行为从"拒+删档"换成"拒+留池 EXAM_REJECT_KEEP"，
--     并加二次防护 has_outbound_rejection 防重发
--
-- 幂等：DROP IF EXISTS + ADD。
-- 回滚：把 'rejection' 从 ARRAY 拿掉（前提是没有任何已存在行用了它）。

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_te_context') THEN
        ALTER TABLE talent_emails DROP CONSTRAINT chk_te_context;
    END IF;
    ALTER TABLE talent_emails ADD CONSTRAINT chk_te_context
        CHECK (context IN ('exam','round1','round2','followup','intake',
                           'rejection','unknown'));
END $$;
