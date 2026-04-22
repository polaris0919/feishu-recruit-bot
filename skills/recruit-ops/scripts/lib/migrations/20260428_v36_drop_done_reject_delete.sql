-- 2026-04-28 v3.6  删除 ROUND1_DONE_REJECT_DELETE / ROUND2_DONE_REJECT_DELETE
--
-- 背景（见 CHANGELOG v3.6）：
--   interview/cmd_result.py 的 _handle_reject_delete 一直是「发拒信 → talent_db.delete_talent()」，
--   根本不会把候选人留在 *_DONE_REJECT_DELETE 这两个枚举上——线上历史数据也未出现过。
--   这两个是"概念上的占位 stage"，状态机里留着反而让 agent/boss 以为"删了还能查到"。
--   v3.6 彻底下线：
--     - STAGES / STAGE_LABELS / CHECK 约束去掉两个枚举
--     - talent/cmd_update.py _NATURAL_TRANSITIONS 去掉对应入边
--     - interview.cmd_result reject_delete / exam.cmd_exam_result reject_delete 语义不变
--       （直接发拒信 + talent_db.delete_talent()，属于物理删除，和 stage 无关）
--     - talent.cmd_delete 仍是物理删除的唯一出口，不受影响
--
-- 前置依赖：20260427_v36_drop_offer_handoff.sql 已 apply（CHECK 约束顺序）。
-- 幂等：UPDATE 兜底 + CHECK 约束 DROP + ADD。

-- 1) 兜底迁移存量数据（当前生产 0 行；若出现只能是陈旧快照残留，归并到 NEW 让老板再看）
UPDATE talents
SET current_stage = 'NEW'
WHERE current_stage IN ('ROUND1_DONE_REJECT_DELETE', 'ROUND2_DONE_REJECT_DELETE');

-- 2) 重建 CHECK 约束，去掉两个 *_DONE_REJECT_DELETE
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
        'EXAM_SENT', 'EXAM_REVIEWED', 'EXAM_REJECT_KEEP',
        'WAIT_RETURN',
        'ROUND2_SCHEDULING', 'ROUND2_SCHEDULED',
        'ROUND2_DONE_REJECT_KEEP',
        'POST_OFFER_FOLLOWUP'
    ));
END $$;
