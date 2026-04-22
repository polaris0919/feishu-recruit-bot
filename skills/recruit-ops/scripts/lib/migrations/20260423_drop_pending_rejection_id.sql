-- 2026-04-23 撤回 pending_rejection_id 字段
--
-- 历史：2026-04-22 引入 auto_reject 模块时新增此字段，作为"软自动化拒删"
-- 缓冲队列（data/auto_reject_pending/<id>.json）的指针。
--
-- 本次决策：去掉 12h 缓冲机制。
--   - 改期 < 24h：交还老板裁决，不再自动入队
--   - 笔试 ≥3 天无回复：立刻发拒信 + 删档（无撤销）
-- 该指针不再被任何代码读写，DROP 之以收紧 schema。

ALTER TABLE talents DROP COLUMN IF EXISTS pending_rejection_id;
