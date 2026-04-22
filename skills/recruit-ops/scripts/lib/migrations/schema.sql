-- schema.sql: 完整数据库终态定义（幂等，可重复执行）
-- 所有语句均带 IF NOT EXISTS / IF EXISTS 保护，不会重复创建或报错。
-- 历史迁移已于 2026-04-14 手动执行完毕，此文件仅保留终态 DDL。

-- ─── 主表：候选人 ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS talents (
    talent_id        TEXT PRIMARY KEY,
    candidate_email  TEXT,
    candidate_name   TEXT,
    current_stage    TEXT DEFAULT 'NEW',
    wait_return_round INTEGER,
    exam_id          TEXT,

    -- 一面
    round1_confirm_status     TEXT DEFAULT 'UNSET',    -- UNSET / PENDING / CONFIRMED
    round1_time               TIMESTAMPTZ,              -- 当前唯一有效时间
    round1_invite_sent_at     TIMESTAMPTZ,
    round1_calendar_event_id  TEXT,
    round1_reminded_at        TIMESTAMPTZ,              -- 催老板看结果（面试后）
    round1_confirm_prompted_at TIMESTAMPTZ,             -- 催老板确认时间（排期时）

    -- 二面
    round2_confirm_status     TEXT DEFAULT 'UNSET',    -- UNSET / PENDING / CONFIRMED
    round2_time               TIMESTAMPTZ,              -- 当前唯一有效时间
    round2_invite_sent_at     TIMESTAMPTZ,
    round2_calendar_event_id  TEXT,
    round2_reminded_at        TIMESTAMPTZ,              -- 催老板看结果（面试后）
    round2_confirm_prompted_at TIMESTAMPTZ,             -- 催老板确认时间（排期时）

    -- 笔试
    exam_sent_at          TIMESTAMPTZ,
    -- v3.5.2 (2026-04-21)：以下字段全部下线，迁移见
    -- 20260421_v35_drop_dead_columns.sql：
    --   exam_last_email_id, round1_last_email_id, round2_last_email_id
    --     —— talent_emails 表 (talent_id, message_id) UNIQUE 已接管去重
    --   followup_last_email_id, followup_entered_at, followup_status,
    --     followup_snoozed_until
    --     —— followup_scanner 与整个 followup/ 模块在 v3.5 下线，
    --       后续邮件流由 inbox.cmd_scan/cmd_analyze 接管

    -- 个人信息
    source           TEXT,
    position         TEXT,
    education        TEXT,
    work_years       INTEGER,
    experience       TEXT,
    school           TEXT,
    phone            TEXT,
    wechat           TEXT,
    cv_path          TEXT,

    -- v3.5.7 (2026-04-25)：CV 解析得到的「是否会 C++」，用于 §5.11 一面派单
    -- true / false / NULL（未知）。intake.cmd_route_interviewer 读这个字段。
    has_cpp          BOOLEAN,

    -- 时间追踪
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- ─── CHECK 约束：current_stage 合法值 ─────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_current_stage' AND conrelid = 'talents'::regclass
    ) THEN
        ALTER TABLE talents DROP CONSTRAINT chk_current_stage;
    END IF;
    -- v3.6 (2026-04-27/28)：合并 OFFER_HANDOFF → POST_OFFER_FOLLOWUP（瞬时态下线）
    -- v3.6 (2026-04-28)：删除 ROUND1_DONE_REJECT_DELETE / ROUND2_DONE_REJECT_DELETE
    --   这两个"名义 stage"从不持久化——reject_delete 直接走 talent_db.delete_talent。
    -- 见 20260427_v36_drop_offer_handoff.sql、20260428_v36_drop_done_reject_delete.sql
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

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_wait_return_round' AND conrelid = 'talents'::regclass
    ) THEN
        ALTER TABLE talents DROP CONSTRAINT chk_wait_return_round;
    END IF;
    ALTER TABLE talents ADD CONSTRAINT chk_wait_return_round
        CHECK (wait_return_round IN (1, 2) OR wait_return_round IS NULL);
END $$;

-- ─── CHECK 约束：confirm_status 合法值 ────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_round1_confirm_status') THEN
        ALTER TABLE talents ADD CONSTRAINT chk_round1_confirm_status
            CHECK (round1_confirm_status IN ('UNSET', 'PENDING', 'CONFIRMED'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_round2_confirm_status') THEN
        ALTER TABLE talents ADD CONSTRAINT chk_round2_confirm_status
            CHECK (round2_confirm_status IN ('UNSET', 'PENDING', 'CONFIRMED'));
    END IF;
END $$;

-- ─── 审计事件表 ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS talent_events (
    id        SERIAL PRIMARY KEY,
    event_id  TEXT,
    talent_id TEXT NOT NULL,
    at        TIMESTAMPTZ DEFAULT NOW(),
    actor     TEXT,
    action    TEXT,
    payload   JSONB DEFAULT '{}'::jsonb
);

-- ─── 邮件游标 / followup 字段（v3.5.2 全部下线，详见 20260421 migration）────────
-- 历史保留：原本 ALTER TABLE ADD COLUMN exam_last_email_id / round1_last_email_id /
-- round2_last_email_id / followup_last_email_id / followup_entered_at /
-- followup_status / followup_snoozed_until 与 chk_followup_status CHECK 都在这里，
-- 已由 20260421_v35_drop_dead_columns.sql DROP；此终态文件不再 ADD。

-- ─── auto_reject 字段历史（2026-04-22 引入，2026-04-23 删除）─────────────────
-- 软自动化拒删 (propose / cancel / execute_due) 已下线，缓冲队列指针不再使用。
ALTER TABLE talents DROP COLUMN IF EXISTS pending_rejection_id;

-- ─── 面试时间单字段迁移（兼容存量数据库）────────────────────────────────────────────
ALTER TABLE talents ADD COLUMN IF NOT EXISTS round1_time TIMESTAMPTZ;
ALTER TABLE talents ADD COLUMN IF NOT EXISTS round2_time TIMESTAMPTZ;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'talents' AND column_name = 'round1_confirmed_time'
    ) THEN
        EXECUTE 'UPDATE talents
                 SET round1_time = COALESCE(round1_time, round1_confirmed_time, round1_proposed_time)';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'talents' AND column_name = 'round2_confirmed_time'
    ) THEN
        EXECUTE 'UPDATE talents
                 SET round2_time = COALESCE(round2_time, round2_confirmed_time, round2_proposed_time)';
    END IF;
END $$;

ALTER TABLE talents DROP COLUMN IF EXISTS round1_proposed_time;
ALTER TABLE talents DROP COLUMN IF EXISTS round1_confirmed_time;
ALTER TABLE talents DROP COLUMN IF EXISTS round2_proposed_time;
ALTER TABLE talents DROP COLUMN IF EXISTS round2_confirmed_time;

-- ─── 二面状态机语义修正（兼容存量数据库）───────────────────────────────────────
UPDATE talents
SET current_stage = 'ROUND2_SCHEDULING'
WHERE current_stage = 'ROUND2_SCHEDULED'
  AND round2_confirm_status = 'PENDING';

-- ─── WAIT_RETURN 历史暂缓数据迁移（兼容存量数据库）─────────────────────────────
UPDATE talents t
SET current_stage = 'WAIT_RETURN',
    wait_return_round = 2
WHERE t.current_stage = 'EXAM_REVIEWED'
  AND EXISTS (
      SELECT 1
      FROM talent_events te
      WHERE te.talent_id = t.talent_id
        AND te.action = 'round2_deferred_until_shanghai'
  );

-- ─── 废弃旧全局去重表（如存在则删除）────────────────────────────────────────────
DROP TABLE IF EXISTS processed_emails;

-- ─── 索引 ──────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_talent_events_talent_id ON talent_events(talent_id);

-- ─── 外键约束 ──────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_talent_events_talent') THEN
        ALTER TABLE talent_events
            ADD CONSTRAINT fk_talent_events_talent
            FOREIGN KEY (talent_id) REFERENCES talents(talent_id) ON DELETE CASCADE;
    END IF;
END $$;

-- ─── 事件身份迁移：补 event_id 并回填存量数据 ─────────────────────────────────────
ALTER TABLE talent_events ADD COLUMN IF NOT EXISTS event_id TEXT;

UPDATE talent_events
SET event_id = md5(
    COALESCE(talent_id, '') || '|' ||
    COALESCE(at::text, '') || '|' ||
    COALESCE(actor, '') || '|' ||
    COALESCE(action, '') || '|' ||
    COALESCE(payload::text, '')
)
WHERE event_id IS NULL;

ALTER TABLE talent_events ALTER COLUMN event_id SET NOT NULL;

-- ─── 事件去重唯一约束 ──────────────────────────────────────────────────────────
ALTER TABLE talent_events DROP CONSTRAINT IF EXISTS talent_events_talent_id_at_action_key;
ALTER TABLE talent_events DROP CONSTRAINT IF EXISTS uq_talent_events_dedup;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_talent_events_dedup') THEN
        ALTER TABLE talent_events
            ADD CONSTRAINT uq_talent_events_dedup
            UNIQUE (event_id);
    END IF;
END $$;

-- ═══════════════════════════════════════════════════════════════════════════════
-- 候选人邮件表（2026-04-20 引入）
--
-- 设计目标：把候选人收/发的每一封邮件做成 SQL 一等实体，
--   - 唯一约束 (talent_id, message_id) 物理阻止重复识别（替换原先的
--     talents.<ctx>_last_email_id 单游标 + data/followup_pending JSON 文件兜底）；
--   - 用 status 状态机记录处理进度（received/pending_boss/replied/dismissed/...）；
--   - LLM 摘要/意图作为字段入表，未来可做趋势统计；
--   - inbound + outbound 同表，按 sent_at 排序即是完整对话线程。
-- 历史 talents.<ctx>_last_email_id 列保留为兜底（双写），未来 phase 删除。
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS talent_emails (
    -- ── 主键 + 候选人 ──
    email_id           UUID PRIMARY KEY,
    talent_id          TEXT NOT NULL REFERENCES talents(talent_id) ON DELETE CASCADE,

    -- ── 邮件本体 ──
    message_id         TEXT NOT NULL,
    in_reply_to        TEXT,
    references_chain   TEXT,

    -- ── 收发方向 + 元数据 ──
    direction          TEXT NOT NULL,
    sender             TEXT NOT NULL,
    recipients         TEXT[],
    subject            TEXT,

    -- ── 时间 ──
    sent_at            TIMESTAMPTZ NOT NULL,
    received_at        TIMESTAMPTZ,
    processed_at       TIMESTAMPTZ DEFAULT NOW(),

    -- ── 业务上下文 ──
    context            TEXT NOT NULL,
    stage_at_receipt   TEXT,

    -- ── 处理状态 ──
    status             TEXT NOT NULL DEFAULT 'received',

    -- ── 内容 ──
    body_full          TEXT,
    body_excerpt       TEXT,

    -- ── AI 摘要/意图 ──
    ai_summary         TEXT,
    ai_intent          TEXT,
    ai_payload         JSONB,
    analyzed_at        TIMESTAMPTZ,    -- v3.3: inbox/cmd_analyze 完成时间

    -- ── outbound 邮件模板名 ──
    -- v3.3: outbound/cmd_send 写入；模板模式填模板名（如 'round1_invite'），
    -- 自由文本模式填 'freeform'。仅对 direction='outbound' 行有意义。
    template           TEXT,

    -- ── inbound 附件元数据 ──
    -- v3.5.6: inbox.cmd_scan 在 insert 成功后通过 lib.email_attachments
    -- 把附件落到 ATTACHMENT_ROOT (data/candidate_answer/)，并把元数据数组写到这里。
    -- 每个元素：{name,size,mime,path（相对 ATTACHMENT_ROOT）,sha256,saved,note}
    -- NULL = 无附件或 v3.5.6 之前落盘的历史行。
    attachments        JSONB,

    -- ── 反向索引到飞书交互层 ──
    reply_id           TEXT,
    replied_by_email_id UUID REFERENCES talent_emails(email_id) ON DELETE SET NULL,

    -- ── 审计 ──
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (talent_id, message_id)
);

-- CHECK 约束（先 DROP 后 ADD，便于演化）
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_te_direction') THEN
        ALTER TABLE talent_emails DROP CONSTRAINT chk_te_direction;
    END IF;
    ALTER TABLE talent_emails ADD CONSTRAINT chk_te_direction
        CHECK (direction IN ('inbound','outbound'));
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_te_context') THEN
        ALTER TABLE talent_emails DROP CONSTRAINT chk_te_context;
    END IF;
    -- v3.5.11 (2026-04-22) 加入 'rejection'，配合 auto_reject.cmd_scan_exam_timeout
    -- 直发拒信路径。详见 20260422_v3511_talent_emails_context_rejection.sql。
    ALTER TABLE talent_emails ADD CONSTRAINT chk_te_context
        CHECK (context IN ('exam','round1','round2','followup','intake',
                           'rejection','unknown'));
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_te_status') THEN
        ALTER TABLE talent_emails DROP CONSTRAINT chk_te_status;
    END IF;
    ALTER TABLE talent_emails ADD CONSTRAINT chk_te_status
        CHECK (status IN ('received','pending_boss','replied','dismissed',
                          'snoozed','auto_processed','duplicate_skipped','error'));
END $$;

-- 索引
CREATE INDEX IF NOT EXISTS idx_te_status     ON talent_emails (talent_id, status);
CREATE INDEX IF NOT EXISTS idx_te_context    ON talent_emails (talent_id, context, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_te_msg_global ON talent_emails (message_id);
CREATE INDEX IF NOT EXISTS idx_te_thread     ON talent_emails (in_reply_to)
    WHERE in_reply_to IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_te_pending    ON talent_emails (status, processed_at)
    WHERE status = 'pending_boss';

-- v3.3: outbound 按模板查询（cmd_review / 一致性排查）
CREATE INDEX IF NOT EXISTS idx_te_outbound_template
    ON talent_emails (talent_id, template, sent_at DESC)
    WHERE direction = 'outbound';

-- v3.3: inbox/cmd_analyze 待处理队列
CREATE INDEX IF NOT EXISTS idx_te_pending_analyze
    ON talent_emails (sent_at)
    WHERE direction = 'inbound' AND analyzed_at IS NULL;

-- updated_at 自动维护（轻量触发器，避免每条 UPDATE 手写）
CREATE OR REPLACE FUNCTION trg_te_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS te_touch_updated_at ON talent_emails;
CREATE TRIGGER te_touch_updated_at
    BEFORE UPDATE ON talent_emails
    FOR EACH ROW EXECUTE FUNCTION trg_te_touch_updated_at();

-- v3.5.6: inbound 附件元数据（兼容存量数据库）
-- 见 20260424_v356_talent_emails_attachments.sql
ALTER TABLE talent_emails ADD COLUMN IF NOT EXISTS attachments JSONB;
COMMENT ON COLUMN talent_emails.attachments IS
    'v3.5.6: 附件元数据数组，由 inbox.cmd_scan 落盘后填写。'
    '每个元素 {name,size,mime,path,sha256,saved,note}，path 相对 lib.email_attachments.ATTACHMENT_ROOT。'
    'NULL = 无附件或在 v3.5.6 之前落盘的历史行。';

-- v3.5.7: talents.has_cpp（兼容存量数据库）
-- 见 20260425_v357_talents_has_cpp.sql
ALTER TABLE talents ADD COLUMN IF NOT EXISTS has_cpp BOOLEAN;
COMMENT ON COLUMN talents.has_cpp IS
    'v3.5.7: LLM 从 CV 解析的「是否会 C++」。'
    'true=明确写了 C++ 技能或用 C++ 做过项目；'
    'false=明确没提 C++ 或只用其他语言；'
    'NULL=未知/未判断（cmd_parse_cv 返回 null 时直接落地为 NULL）。'
    '由 intake.cmd_route_interviewer 用于 §5.11 一面派单（cpp_first 优先级）。';
