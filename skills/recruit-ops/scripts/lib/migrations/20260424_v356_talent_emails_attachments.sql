-- 2026-04-24 v3.5.6  inbox.cmd_scan 增加附件落盘能力
--
-- 历史背景：
--   - 在 v3.5.6 之前，inbox.cmd_scan 只把邮件正文 (body_full / body_excerpt)
--     塞进 talent_emails，附件 (attachment) 直接被 _extract_body 跳过。
--   - 候选人投递的简历 / 笔试代码 / 作品集附件一直只能由
--     exam/fetch_exam_submission 按需手工拉到 /tmp 临时目录，没有持久存档，
--     也没有元数据可被 SQL / cmd_review 检索。
--
-- 本次新增：
--   talent_emails.attachments JSONB
--     —— 每行一个数组，记录该邮件落盘的附件元信息：
--        [{"name": "候选人F_简历.pdf",
--          "size": 235123,
--          "mime": "application/pdf",
--          "path": "t_abc123/em_<email_id>/候选人F_简历.pdf",  -- 相对 ATTACHMENT_ROOT
--          "sha256": "...",
--          "saved": true,
--          "note": null},
--         ...]
--
--     约定：
--       - path 永远是相对路径（相对 lib.email_attachments.ATTACHMENT_ROOT）。
--         便于将来整体迁移、备份、或换机器跑。
--       - saved=false + note 用于"附件被 lib.email_attachments 主动 skip
--         （例如 winmail.dat、超过 25MB、命名失败）"的场景，仍写一行做证据。
--       - NULL = 该邮件没有附件 / cmd_scan 在引入附件支持之前已落盘的历史行。
--
-- 影响范围：
--   - lib/email_attachments.py（新建模块）
--   - lib/talent_db.py 新增 update_email_attachments(email_id, attachments)
--   - inbox/cmd_scan.py 在 insert_email_if_absent 成功后调用上述 API
--   - inbox/cmd_review.py 后续可在邮件时间线下额外打印附件清单（v3.5.6 暂不动）
--
-- 回滚：
--   该列纯增量、可空，回滚直接 ALTER TABLE talent_emails DROP COLUMN attachments;

ALTER TABLE talent_emails ADD COLUMN IF NOT EXISTS attachments JSONB;

COMMENT ON COLUMN talent_emails.attachments IS
    'v3.5.6: 附件元数据数组，由 inbox.cmd_scan 落盘后填写。'
    '每个元素 {name,size,mime,path,sha256,saved,note}，path 相对 lib.email_attachments.ATTACHMENT_ROOT。'
    'NULL = 无附件或在 v3.5.6 之前落盘的历史行。';
