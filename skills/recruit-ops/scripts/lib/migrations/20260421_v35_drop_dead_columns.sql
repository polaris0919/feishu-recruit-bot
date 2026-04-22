-- 2026-04-21 v3.5.2 schema cleanup
--
-- 历史背景：
--   - 2026-04-20 引入 talent_emails 表后，talents.<ctx>_last_email_id 单游标
--     不再是邮件去重的 source-of-truth，仅作"双写兼容"保留。
--   - v3.4 起 followup_scanner 下线；v3.5 进一步删除整个 followup/ 模块，
--     followup_status / followup_snoozed_until / followup_last_email_id
--     已无任何业务代码读写（enter_post_offer_followup 写的几个字段也都没人消费）。
--
-- 本次清理（无回滚路径，因为 talent_emails 表已经接管全部去重职责）：
--   1) 删 4 个 followup_* 字段 + chk_followup_status CHECK 约束
--   2) 删 3 个 *_last_email_id 字段
--
-- 影响范围：
--   - lib/talent_db.py 同步移除写路径（见 v3.5.2 配套代码改动）
--   - common/cmd_debug_candidate.py 不再 SELECT 这些列
--   - talent/cmd_show.py 不再展示 followup_status / followup_entered_at

-- ── 1) 卸掉 chk_followup_status 约束（先约束后字段，否则 PG 会抱怨依赖） ──
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_followup_status' AND conrelid = 'talents'::regclass
    ) THEN
        ALTER TABLE talents DROP CONSTRAINT chk_followup_status;
    END IF;
END $$;

-- ── 2) 删 followup_* 4 个字段 ──
ALTER TABLE talents DROP COLUMN IF EXISTS followup_last_email_id;
ALTER TABLE talents DROP COLUMN IF EXISTS followup_entered_at;
ALTER TABLE talents DROP COLUMN IF EXISTS followup_status;
ALTER TABLE talents DROP COLUMN IF EXISTS followup_snoozed_until;

-- ── 3) 删 *_last_email_id 3 个字段（v3.5 起 talent_emails 全权负责去重） ──
ALTER TABLE talents DROP COLUMN IF EXISTS exam_last_email_id;
ALTER TABLE talents DROP COLUMN IF EXISTS round1_last_email_id;
ALTER TABLE talents DROP COLUMN IF EXISTS round2_last_email_id;
