-- 2026-04-27 v3.6  合并 OFFER_HANDOFF 到 POST_OFFER_FOLLOWUP
--
-- 背景（见 CHANGELOG v3.6）：
--   OFFER_HANDOFF 语义上只是「interview/cmd_result.py --round 2 --result pass」
--   通知 HR 后 1-tick 的瞬时态，set_current_stage 立刻把它推到 POST_OFFER_FOLLOWUP。
--   它从没作为"持久态"存在过（线上历史数据库里也从未停留过任何候选人），
--   只是状态机枚举里的累赘。v3.6 把它从 STAGES / STAGE_LABELS / CHECK 约束里拿掉：
--     - interview/cmd_result.py round2 pass 直接一步推到 POST_OFFER_FOLLOWUP（HR 通知不变）
--     - POST_OFFER_FOLLOWUP 纳入 common.cmd_search.ACTIVE_STAGES
--     - inbox.analyzer._FOLLOWUP_STAGES / outbound.cmd_send._STAGE_TO_CONTEXT 去掉 OFFER_HANDOFF
--
-- 幂等：
--   - UPDATE 兜底（线上此刻 0 行，真有存量会被迁到 POST_OFFER_FOLLOWUP）
--   - CHECK 约束 DROP + ADD 重建

-- 1) 兜底迁移存量数据（当前生产 0 行；线下快照可能有）
UPDATE talents
SET current_stage = 'POST_OFFER_FOLLOWUP'
WHERE current_stage = 'OFFER_HANDOFF';

-- 2) 重建 CHECK 约束，去掉 OFFER_HANDOFF
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_current_stage' AND conrelid = 'talents'::regclass
    ) THEN
        ALTER TABLE talents DROP CONSTRAINT chk_current_stage;
    END IF;
    ALTER TABLE talents ADD CONSTRAINT chk_current_stage CHECK (current_stage IN (
        'NEW',
        'ROUND1_SCHEDULING', 'ROUND1_SCHEDULED',
        'ROUND1_DONE_REJECT_DELETE',
        'EXAM_SENT', 'EXAM_REVIEWED', 'EXAM_REJECT_KEEP',
        'WAIT_RETURN',
        'ROUND2_SCHEDULING', 'ROUND2_SCHEDULED',
        'ROUND2_DONE_REJECT_KEEP', 'ROUND2_DONE_REJECT_DELETE',
        'POST_OFFER_FOLLOWUP'
    ));
END $$;
