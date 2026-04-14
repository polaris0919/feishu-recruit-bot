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
    exam_last_email_id    TEXT,         -- 笔试阶段最后一封已处理邮件的 Message-ID

    -- 面试确认游标（替代全局 processed_emails 表）
    round1_last_email_id  TEXT,         -- 一面阶段最后一封已处理邮件的 Message-ID
    round2_last_email_id  TEXT,         -- 二面阶段最后一封已处理邮件的 Message-ID

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

    -- 时间追踪
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE talents ADD COLUMN IF NOT EXISTS wait_return_round INTEGER;

-- ─── CHECK 约束：current_stage 合法值 ─────────────────────────────────────────
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
        'ROUND1_DONE_PASS', 'ROUND1_DONE_REJECT_KEEP', 'ROUND1_DONE_REJECT_DELETE',
        'EXAM_SENT', 'EXAM_REVIEWED', 'WAIT_RETURN',
        'ROUND2_SCHEDULING', 'ROUND2_SCHEDULED', 'ROUND2_DONE_PENDING',
        'ROUND2_DONE_PASS', 'ROUND2_DONE_REJECT_KEEP', 'ROUND2_DONE_REJECT_DELETE',
        'OFFER_HANDOFF'
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
    talent_id TEXT NOT NULL,
    at        TIMESTAMPTZ DEFAULT NOW(),
    actor     TEXT,
    action    TEXT,
    payload   JSONB DEFAULT '{}'::jsonb
);

-- ─── 邮件游标列迁移（新装环境由 CREATE TABLE 自动创建，存量数据库需执行以下语句）────
ALTER TABLE talents ADD COLUMN IF NOT EXISTS exam_last_email_id   TEXT;
ALTER TABLE talents ADD COLUMN IF NOT EXISTS round1_last_email_id TEXT;
ALTER TABLE talents ADD COLUMN IF NOT EXISTS round2_last_email_id TEXT;
ALTER TABLE talents ADD COLUMN IF NOT EXISTS wait_return_round INTEGER;

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

-- ─── 事件去重唯一约束 ──────────────────────────────────────────────────────────
ALTER TABLE talent_events DROP CONSTRAINT IF EXISTS talent_events_talent_id_at_action_key;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_talent_events_dedup') THEN
        ALTER TABLE talent_events
            ADD CONSTRAINT uq_talent_events_dedup
            UNIQUE (talent_id, at, actor, action);
    END IF;
END $$;
