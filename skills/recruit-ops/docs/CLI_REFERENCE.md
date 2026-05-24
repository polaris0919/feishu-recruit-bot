# recruit-ops CLI 参考手册

> **架构约定**：写操作只通过 **atomic CLI**（每个命令对应一个写动作 + 自验证 + 飞书告警）。
> 多步流程（如「安排一面」= 发邮件 + 推 stage + 落字段）由 agent 读
> [`docs/AGENT_RULES.md`](AGENT_RULES.md) 决策，调 `lib.run_chain` 串原子 CLI 完成。
> 本手册只描述当前仍在的命令；旧包装脚本（`cmd_round1_schedule` / `interview.cmd_{confirm,defer,reschedule}` /
> `followup/cmd_followup_*` / `daily_exam_review` 等）的等价 chain 见 `AGENT_RULES.md` §3、§5。
>
> **推荐执行方式**：在 `skills/recruit-ops` 仓库根目录使用 `uv run python3 scripts/...`；如果是系统 cron，使用 `PYTHONPATH=scripts ./.venv/bin/python scripts/...`（`scripts/` 下的模块互相靠相对顶层包 import，例如 `from core_state import ...`，必须把它加到 `PYTHONPATH`）。
> ```bash
> cd /home/admin/recruit-workspace/skills/recruit-ops
> uv run python3 scripts/common/cmd_status.py --talent-id t_xxx
> ```
>
> **下文约定**：为避免每个代码块都重复同一长前缀，下文若看到 `python3 intake/...`、`python3 interview/...`、`python3 exam/...`、`python3 common/...` 这类写法，都等价于在仓库根目录执行 `uv run python3 scripts/...`。
>
> **talent-id 约定**：除 `common/cmd_remove.py` 同时兼容 `--talent_id` 外，所有其他脚本都**只**接受 `--talent-id`（带连字符）。
>
> **`--template` 多行参数**：bash 的双引号里 `\n` 是字面量、不会转义换行。请用 heredoc 或 `$'…'` 形式传入，例如：
> ```bash
> python3 intake/cmd_new_candidate.py --template "$(cat <<'EOF'
> 【新候选人】
> 姓名：张三
> 邮箱：zhangsan@example.com
> EOF
> )"
> ```

---

## 目录

0. [Atomic CLI 总览](#atomic-cli-总览)
1. [招聘流水线概览](#招聘流水线概览)
2. [简历入库（intake）](#简历入库-intake)
3. [笔试（exam）](#笔试-exam)
4. [面试结果（interview）](#面试结果-interview)
5. [出站邮件（outbound）](#出站邮件-outbound) — `cmd_send` 完整参数
6. [候选人 CRUD（talent）](#候选人-crud-talent) — `cmd_add` / `cmd_show` / `cmd_list` / `cmd_update` / `cmd_delete`
7. [通用管理（common）](#通用管理-common)
8. [Offer 后跟进（followup）](#offer-后跟进-followup)
9. [自动拒绝（auto_reject）](#自动拒绝-auto_reject)
10. [飞书日历 / 消息（feishu）](#飞书日历-feishu) — `cmd_calendar_create` / `cmd_calendar_delete` / `cmd_notify`
11. [定时任务（cron）](#定时任务-cron)

---

## Atomic CLI 总览

### 整体架构

```
入站邮件路径：  IMAP → inbox/cmd_scan → talent_emails (analyzed_at IS NULL)
                                        ↓
                inbox/cmd_analyze (LLM 分类) → 推飞书 (need_boss_action) → set analyzed_at

老板看完决定 →  outbound/cmd_send（模板/自由文本，唯一发邮件出口，零状态副作用）
              + talent/cmd_update（唯一改 stage / 字段出口）
              + talent/cmd_delete（唯一删候选人出口，自动归档）

cron 周期：    cron/cron_runner（互斥锁 + 心跳 + 失败必报警）
                ├─ inbox/cmd_scan / inbox/cmd_analyze（所有 stage 候选人邮件）
                ├─ common/cmd_interview_reminder
                ├─ auto_reject/cmd_scan_exam_timeout（笔试 ≥3 天未交 → 即触发拒信 + 物理删档归档）
                └─ ops/cmd_health_check（每天 09 点）

ops 工具：     ops/cmd_db_migrate（增量迁移）
              ops/cmd_health_check（DB/IMAP/SMTP/LLM/Feishu 5 项体检）
              feishu/cmd_notify（统一飞书消息推送）
              ops/cmd_replay_notifications（回放遗漏的入站分析卡片）

可视化：       inbox/cmd_review --talent-id   候选人邮件时间线
              talent/cmd_show --talent-id    候选人完整快照
              talent/cmd_list --stage X      按 stage 筛选
              template/cmd_preview --list    所有邮件模板
```

### inbox/ — 入站邮件三件套

| 脚本 | 用途 | 副作用 |
|------|------|--------|
| `inbox/cmd_scan.py` | IMAP 拉所有候选人新邮件，去重写入 `talent_emails`（`direction='inbound'`, `analyzed_at IS NULL`） | 写 `talent_emails` |
| `inbox/cmd_analyze.py` | 取 `analyzed_at IS NULL` 的入站邮件，LLM 分意图 + 推飞书（仅当 `need_boss_action`） | 更新 `talent_emails.ai_*` |
| `inbox/cmd_review.py --talent-id X` | 只读，打印某候选人完整邮件时间线（含 AI 摘要 / 模板名 / 已分析标记） | 无 |

```bash
# 拉新邮件
PYTHONPATH=scripts python3 -m inbox.cmd_scan --since "2026-04-15"
# LLM 分析积压
PYTHONPATH=scripts python3 -m inbox.cmd_analyze --limit 20
# 看某人时间线
PYTHONPATH=scripts python3 -m inbox.cmd_review --talent-id t_xxx
```

### outbound/ — 出站邮件唯一入口

| 脚本 | 用途 | 模式 |
|------|------|------|
| `outbound/cmd_send.py` | 发送任意邮件给候选人；自动写一行 `talent_emails(direction='outbound')`；零业务副作用 | 模板模式 / 自由文本模式 |

```bash
# 模板模式（自动注入 candidate_name / company / location / talent_id 默认值）
#
# v3.8.6 起：company / location 是 email_templates/constants.py 里的公司常量
# （"致邃投资" / "丁香国际商业中心西塔21楼致邃投资"），**不要**在 --vars 里覆盖,
# CLI 会 fail-loud 拒掉。位置变了请改 constants.py。
#
# 必传变量：round1_time（model 从对话里抓的"我们 X 月 X 日 X 点见"）。
# 可传变量：position / position_suffix（候选人投的具体岗位；不知道时直接省略,
#   模板会渲染成空 / "一面邀请"。**不要凭空编**, model 没"猜"的资格）。
PYTHONPATH=scripts python3 -m outbound.cmd_send \
    --talent-id t_xxx --template round1_invite \
    --vars round1_time="2026-04-25 14:00" \
           position="量化研究员" position_suffix="（量化研究员）"

# 自由文本模式（agent 起草 → 老板确认 → 你把全文写到 /tmp/draft.txt）
PYTHONPATH=scripts python3 -m outbound.cmd_send \
    --talent-id t_xxx \
    --subject "Re: 关于薪资的疑问" \
    --body-file /tmp/draft.txt \
    --in-reply-to '<abc@mail.example.com>'
# --cleanup-body-file 默认 ON，发送后自动删除 /tmp/draft.txt
```

### talent/ — 候选人 CRUD

| 脚本 | 用途 | 关键约束 |
|------|------|----------|
| `talent/cmd_add.py` | 新建候选人（v3.3 通用入口；CV 入库链路仍走 `intake/cmd_new_candidate.py`） | `--name --email` 必填 |
| `talent/cmd_show.py --talent-id X` | 只读，打印候选人快照 + 邮件统计 + 审计 | 无 |
| `talent/cmd_list.py [--stage X] [--search Y]` | 只读，按条件列出候选人 | 无 |
| `talent/cmd_update.py --talent-id X --stage NEW_STAGE` | 唯一改 stage / 字段出口 | 非自然跳转必须 `--force --reason "..."` |
| `talent/cmd_delete.py --talent-id X --reason "..."` | 唯一删候选人出口 | 自动归档到 `data/deleted_archive/<YYYY-MM>/` |

`cmd_update` 的「自然跳转白名单」（无需 `--force`）：`NEW → ROUND1_SCHEDULING → ROUND1_SCHEDULED → EXAM_SENT → EXAM_REVIEWED → ROUND2_SCHEDULING → ROUND2_SCHEDULED → POST_OFFER_FOLLOWUP → ONBOARDED`（v3.6 起 `OFFER_HANDOFF` 瞬时态已合并入 `POST_OFFER_FOLLOWUP`；v3.8 末端加 `ONBOARDED`），以及 `EXAM_REJECT_KEEP` / `ROUND2_DONE_REJECT_KEEP` / `OFFER_DECLINED_KEEP`（v3.8.2 拆桶后的拒 offer 留池终态）/ `WAIT_RETURN` 等分支。其他跳转都强制要 `--force`。

### ops/ — 运维 / 一致性 / 告警

| 脚本 | 用途 |
|------|------|
| `ops/cmd_db_migrate.py [--status / --apply / --force-file FOO.sql]` | 跑 `lib/migrations/*.sql` 增量迁移；用 `recruit_migrations` 表记账 |
| `ops/cmd_health_check.py [--skip dashscope]` | DB / IMAP / SMTP / DashScope / Feishu / 邮件积压 6 项体检 |
| `feishu/cmd_notify.py --title T --body B [--severity warn]` | 任意脚本想推飞书告警都走它（不走 cli_wrapper，避免死循环） |
| `feishu/cmd_send_file.py --file FILE --to boss\|hr\|polaris` | 通用飞书发文件入口；也支持 `--open-id ou_xxx` |
| `ops/cmd_replay_notifications.py --talent-id X` | 回放某候选人 / 时间窗的入站分析飞书卡片 |

### template/ — 邮件模板

| 脚本 | 用途 |
|------|------|
| `template/cmd_preview.py --list` | 按目录分组列出所有模板 |
| `template/cmd_preview.py --template T --demo` | 用 demo 变量渲染单个模板，不发不写 |

### auto_reject/ — 笔试超时即触即拒+删档

只有一个脚本：`auto_reject/cmd_scan_exam_timeout.py`。详见下文 [§ 自动拒绝](#自动拒绝-auto_reject)。

- **触发条件**：`current_stage='EXAM_SENT'` 且 `exam_sent_at` 距今 ≥ `--threshold-days`（默认 3），且 `talent_emails` 没有 `exam_sent_at` 之后的 inbound 记录，且 `talent_emails` 没有任何 `context='rejection'` 的 outbound 记录（二次幂等防护）。
- **执行动作**：调子进程 `outbound.cmd_send --template rejection_exam_no_reply --context rejection` 发拒信 → 调 `talent.cmd_delete --confirm-delete-talent <tid>` 物理删档并归档到 `data/deleted_archive/` → 推一张飞书「已自动拒+删档」通知卡片给老板（事后告知，不需要老板按按钮）。
- **改期**不走自动拒：所有 reschedule 意图由 agent 看 `inbox.cmd_analyze` 输出后调 `feishu.cmd_notify` 推卡片，老板手动决定。

### cron/cron_runner.py — 编排器

任务表见脚本头注释。当前包含 `inbox.cmd_scan` / `inbox.cmd_analyze` / `common.cmd_interview_reminder` / `auto_reject.cmd_scan_exam_timeout` / `ops.cmd_health_check`（每天 09 点）。失败任务统一走 `_alert_boss`。

```bash
# 完整一轮
PYTHONPATH=scripts python3 -m cron.cron_runner

# 只跑一项调试
PYTHONPATH=scripts python3 -m cron.cron_runner --task inbox_scan --no-lock

# 只看任务表，不真跑
PYTHONPATH=scripts python3 -m cron.cron_runner --dry-run
```

### lib/ — 基础库

- `lib/cli_wrapper.py`：所有写脚本的统一入口包装；遇 `SelfVerifyError` 推飞书 + exit 3；遇 `UserInputError` 仅 stderr + exit 1（不告警，避免骚扰老板）。
- `lib/self_verify.py`：post-action 断言库（`assert_email_sent` / `assert_emails_inserted` / `assert_email_analyzed` / `assert_talent_state` / `assert_talent_deleted`）。
- `lib/smtp_sender.py`：SMTP 发送底层；`send_email_with_threading` 加了 `normalize_subject` 开关给 `cmd_send` 用。
- `lib/talent_db.py`：扩展了 `set_email_analyzed`、`list_unanalyzed_inbound`、`get_full_talent_snapshot`、`update_talent_field`、`set_current_stage`、`talent_exists` 等 helper。

---

---

## 招聘流水线概览

> 流水线的「前进 / 倒车 / 暂缓」动作没有 1:1 的包装脚本。下面流程图标的箭头
> 全部由 **agent 调 atomic CLI**（编排规则详见 [`AGENT_RULES.md`](AGENT_RULES.md)）完成。

```
简历进库
  ↓  intake/cmd_ingest_cv 或 intake/cmd_import_candidate
NEW
  ↓  agent: talent.cmd_update --set round1_proposed_time=... + feishu.cmd_notify 请 HR/老板确认
NEW（只记录 proposed 时间；不发邮件、不建日历）
  ↓  HR/老板飞书明确确认后：outbound.cmd_send + feishu.cmd_calendar_create + talent.cmd_update --stage ROUND1_SCHEDULED
ROUND1_SCHEDULED（一面已安排）
  ↓  interview/cmd_result --round 1 --result pass
EXAM_SENT（一面通过 = 直接发笔试，无独立"一面通过"中间态）
  ↓  inbox.cmd_scan + inbox.cmd_analyze（识别 exam_submitted 后自动触发 exam.cmd_exam_ai_review --feishu --save-event）
EXAM_REVIEWED
  ↓  exam/cmd_exam_result --result pass --round2-time "..."
ROUND2_SCHEDULING（等候候选人确认）
  ↓  候选人回信 confirm → inbox.cmd_analyze 通知老板 → 老板飞书明确授权
  ↓  agent: feishu.cmd_calendar_create + talent.cmd_update --stage ROUND2_SCHEDULED
ROUND2_SCHEDULED（二面已确认）
  ↓  interview/cmd_result --round 2 --result pass（v3.6：一步推到 POST_OFFER_FOLLOWUP + 通知 HR）
POST_OFFER_FOLLOWUP（等发 offer / 沟通入职）
  ↘  agent: feishu.cmd_calendar_delete + talent.cmd_update --stage WAIT_RETURN
WAIT_RETURN（待回国后再约）
  ↓  agent: talent.cmd_update --stage ROUND{N}_SCHEDULING --force
ROUND1_SCHEDULING / ROUND2_SCHEDULING
```

---

## 简历入库 intake

### `cmd_ingest_cv.py` — 简历统一入口（推荐）

从本地文件或飞书文件 key 解析简历，自动识别是新候选人还是已有候选人。

```bash
# 从本地 PDF/DOCX 解析
python3 intake/cmd_ingest_cv.py --file-path /path/to/resume.pdf

# 从飞书消息中的文件 key 解析
python3 intake/cmd_ingest_cv.py --file-key <feishu_file_key> --message-id <msg_id>

# 带文件名辅助 LLM 理解
python3 intake/cmd_ingest_cv.py --file-path resume.pdf --filename "张三_简历.pdf"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--file-path` | 否 | 本地简历路径（PDF / DOCX） |
| `--pdf-path` | 否 | 同上，向后兼容别名 |
| `--file-key` | 否 | 飞书文件 key，与 `--file-path` 二选一 |
| `--message-id` | 否 | 飞书消息 ID（辅助下载） |
| `--filename` | 否 | 附件文件名（辅助 LLM 解析） |

---

### `cmd_attach_cv.py` — 给已有候选人挂简历 / 更新字段

> 通常由 `cmd_ingest_cv.py` 的预览结果自动生成命令，HR 确认后执行；也可手动调用。
> **必须加 `--confirm`**，防止误写。

```bash
# 仅挂简历
python3 intake/cmd_attach_cv.py --talent-id t_xxx --cv-path /path/to/resume.pdf --confirm

# 挂简历 + 同步更新若干字段（每个字段一个 --field）
python3 intake/cmd_attach_cv.py --talent-id t_xxx --cv-path /path/to/resume.pdf --confirm \
    --field education=博士 --field school="复旦大学"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--cv-path` | 否 | 简历本地路径（会写入 `cv_path` 字段） |
| `--confirm` | 是 | 显式确认才会执行写入 |
| `--field key=value` | 否 | 同步更新白名单字段，可重复。允许的 key：`candidate_name` / `candidate_email` / `phone` / `wechat` / `position` / `education` / `school` / `work_years` / `source` / `experience` |

---

### `cmd_parse_cv.py` — 已删除 (A4.1, v3.8.7)

> ⛔ **脚本本体已删除**。原内部工具函数 `download_pdf_from_feishu` / `extract_text_from_pdf` / `extract_pdf_metadata` / `llm_parse_cv_fields` / `format_preview` 已搬到 `lib/cv_parser.py`，作为公开模块 API 给 `cmd_ingest_cv.py` 使用。**老 wrapper 仅有 5 个 utility 函数活着，没必要再保留一个 `main()` 只为打印"我已废弃"的 CLI 入口。**
>
> 若历史脚本 / 笔记里看到 `intake.cmd_parse_cv` 字样，直接当 `lib.cv_parser` 看（API 名 + 行为一致，仅前缀 `_` 私有变体仍保留过渡别名，v4.0 评估删）。

---

### `cmd_new_candidate.py` — 手工录入候选人

> ⚠️ 模板必须是**真正的多行文本**；bash 双引号里的 `\n` 不会转义，请用 heredoc 或 `$'…'`：

```bash
# 推荐：heredoc
python3 intake/cmd_new_candidate.py --template "$(cat <<'EOF'
【新候选人】
姓名：张三
邮箱：zhangsan@example.com
EOF
)"

# 或：$'...' 语法
python3 intake/cmd_new_candidate.py --template $'【新候选人】\n姓名：张三\n邮箱：zhangsan@example.com'

# 也可以用逐字段参数
python3 intake/cmd_new_candidate.py --name 张三 --email zhangsan@example.com \
    --position 量化研究员 --school 复旦大学 --feishu-notify
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--template` | 否 | 【新候选人】多行模板原文（自动解析字段） |
| `--name` | 是（或用 template） | 候选人姓名 |
| `--email` | 是（或用 template） | 候选人邮箱（发笔试用） |
| `--phone` / `--wechat` / `--position` / `--education` / `--school` | 否 | 可选字段 |
| `--work-years` | 否 | 整数 |
| `--source` / `--resume-summary` / `--experience` / `--cv-path` | 否 | 来源 / 摘要 / 简历路径 |
| `--feishu-notify` | 否 | 录入成功后飞书通知老板 |

---

### `cmd_import_candidate.py` — 导入历史候选人（可指定当前阶段）

```bash
python3 intake/cmd_import_candidate.py --template "$(cat <<'EOF'
【导入候选人】
姓名：李四
邮箱：lisi@example.com
当前阶段：笔试中
一面时间：2026-03-15 14:00
EOF
)"
```

> `当前阶段` 必填；若阶段为 `一面邀请中` / `一面已确认` 需要提供 `一面时间`；若阶段为 `二面邀请中` / `二面已确认` / `二面完成` 需要提供 `二面时间`。详细阶段词表见 `scripts/intake/cmd_import_candidate.py` 顶部 docstring。

---

## 笔试 exam

> 「安排一面 / 改期 / 暂缓」由 agent 调原子 CLI 完成（不再有 `cmd_round1_*` 包装脚本）。
> 编排规则与等价 chain 见 [`docs/AGENT_RULES.md`](AGENT_RULES.md) §3、§5。
> 一面 / 二面 **结果**仍由 `interview/cmd_result.py` 处理，见下方 [§ 面试结果](#面试结果-interview)。

### `cmd_exam_result.py` — 记录笔试结果

```bash
# 笔试通过，发送二面邀请并进入 ROUND2_SCHEDULING（不建日历、不确认二面）
python3 exam/cmd_exam_result.py --talent-id t_xxx --result pass --round2-time "2026-05-20 14:00"

# 笔试未通过，保留
python3 exam/cmd_exam_result.py --talent-id t_xxx --result reject_keep

# 笔试未通过，删除
python3 exam/cmd_exam_result.py --talent-id t_xxx --result reject_delete
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--result` | 是 | `pass` / `reject_keep` / `reject_delete` |
| `--round2-time` | result=pass 时**必填** | 二面候选邀请时间，格式 `YYYY-MM-DD HH:MM`；脚本会拒绝复用旧二面时间。该字段不是最终确认时间，命令只发邀请并进入 `ROUND2_SCHEDULING`，不建日历、不进入 `ROUND2_SCHEDULED` |
| `--notes` | 否 | 备注（写入审计日志） |
| `--actor` | 否 | 执行人（默认 `system`） |

---

### `cmd_exam_ai_review.py` — AI 笔试评审（rubric 驱动，自带 IMAP 拉取）

按 `exam_files/rubric.json` 对单个候选人提交跑一次 AI 评审，输出结构化打分 + 理由 + 给老板的可执行下一步建议。**不修改候选人状态机字段**；最终通过/不通过仍由老板使用 `cmd_exam_result.py` 决定。

**自动触发**：cron 先跑 `inbox.cmd_scan` / `inbox.cmd_analyze`。当 `inbox.cmd_analyze` 识别到 `intent=exam_submitted` 且候选人当前 `stage=EXAM_SENT` 时，会自动调用：

```bash
python3 exam/cmd_exam_ai_review.py --talent-id t_xxx --feishu --save-event
```

评审成功后会写入 `talent_events(action=exam_ai_review)`，并把候选人从 `EXAM_SENT` 推进到 `EXAM_REVIEWED`，随后 `cron.cmd_review_reminder` 才会按 3h 阈值催老板拍板。

**默认行为**：只要传 `--talent-id`，会**自动**从 IMAP 拉取该候选人最新笔试回复邮件（缓存到 `recruit-files/exam_submissions/<姓名>__<talent_id>/`），自动从 `_email_body.txt` 读取邮件正文、从 `_email_meta.txt` 的 `Date` 推断 `submitted_at`，自动跳过 `data/raw/原始数据` 等输入数据子目录里的 CSV。无需先手动跑 `fetch_exam_submission.py`。

**评审结果缓存**：首次跑会调一次 LLM 并把结果写到 `cache_dir/<talent_id>/_ai_review_result.json`。第二次跑（例如加 `--feishu --save-event`）会**复用缓存不再调 LLM**，避免重复扣费。需要重新评审时加 `--rerun`。这种"先终端预览，确认后再推飞书"的两步流程是推荐用法。

需要本地存在 `skills/recruit-ops/exam_files/rubric.json`（可从 `rubric.example.json` 复制改写）。LLM 走 `dashscope-config.json` 中的 DashScope。

```bash
# 步骤 1：终端预览（首次会调一次 LLM 并把评审结果落盘缓存）
python3 exam/cmd_exam_ai_review.py --talent-id t_xxx

# 步骤 2：你看完报告觉得 OK → 推飞书 + 写 talent_events 审计（自动复用缓存，不再调 LLM）
python3 exam/cmd_exam_ai_review.py --talent-id t_xxx --feishu --save-event

# 候选人重新提交，需要彻底重新评审：重拉邮件 + 重跑 LLM
python3 exam/cmd_exam_ai_review.py --talent-id t_xxx --refetch --rerun

# 用本地已有目录，不去 IMAP（离线 / 已手动整理过的场景）
python3 exam/cmd_exam_ai_review.py --talent-id t_xxx --code-dir /path/to/submission --no-fetch

# 干跑：构造 prompt 但不真调 LLM（用于自检 / 检查文件收集是否合理）
python3 exam/cmd_exam_ai_review.py --talent-id t_xxx --no-llm --save-prompt /tmp/p.txt
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 否 | 候选人 talent_id（用作 candidate_label，从 DB 拉 exam_sent_at + 邮箱；不传 `--code-dir` 时**必填**） |
| `--candidate-label` | 否 | 候选人显示名（默认 = `<姓名> (<talent_id>)`，姓名取自 DB；显式传以覆盖） |
| `--code-dir` | 否 | 本地已有的提交目录（指定后默认不再去 IMAP 拉；可与 `--refetch` 一起用但 refetch 会失效） |
| `--code-file` | 否 | 单个代码文件，可重复 |
| `--doc-file` | 否 | 单个说明文档，可重复 |
| `--output-file` | 否 | 单个输出文件（仅前 50KB 入 prompt），可重复 |
| `--email-body` | 否 | 候选人邮件正文；不传时自动从本地缓存的 `_email_body.txt` 读取 |
| `--exam-sent-at` | 否 | 题目发出时间（ISO），覆盖 DB 推断 |
| `--submitted-at` | 否 | 候选人首次提交时间（ISO）；不传时自动从邮件 `Date` 推断 |
| `--rubric` | 否 | 指定 rubric.json 路径（默认 `exam_files/rubric.json`） |
| `--cache-dir` | 否 | IMAP / 评审结果的本地缓存根目录；默认写入 `recruit-files/exam_submissions/<姓名>__<talent_id>/`。显式传入时仍使用 `<cache-dir>/<talent_id>/` |
| `--refetch` | 否 | 强制重新从 IMAP 拉，清掉缓存目录 |
| `--no-fetch` | 否 | 不去 IMAP 拉，仅用 `--code-dir` 给的本地目录 |
| `--max-msgs` | 否 | IMAP 最多拉最近 N 封匹配邮件（默认 3） |
| `--rerun` | 否 | 强制重新调 LLM；默认会复用 `_ai_review_result.json` 缓存避免重复扣费 |
| `--feishu` | 否 | 评审完成后把报告推到老板飞书 |
| `--save-event` | 否 | 把结果写入 `talent_events`（action=`exam_ai_review`，actor=`manual_review`） |
| `--json` | 否 | 输出原始 JSON 评审结果（机器可读） |
| `--no-llm` | 否 | 不真调 LLM，仅校验输入 + 构造 prompt |
| `--save-prompt` | 否 | 把构造的 prompt 写入指定文件，便于人工审阅 |

**文件收集规则**：
- 代码：递归收集 `.py/.cpp/.cc/.c/.h/.hpp/.ipynb`
- 文档：递归收集 `.md/.markdown/.txt/.rst`（`.pdf` 暂不支持文本提取）
- 输出：`.csv/.json/.tsv`，但**自动跳过名字含 `data/raw/input/原始数据/题目` 等的子目录**，且单文件 >500KB 直接跳过（候选人产出通常很小、官方输入数据很大）

> ⚠️ AI 评审结果**仅供参考**。报告里硬性禁用了 "建议通过 / 录取 / 拒绝 / 淘汰" 等结论性词汇，最终决策由老板使用 `cmd_exam_result.py` 推动。

---

---

## 面试结果 interview

> 一面 / 二面的「确认 / 改期 / 暂缓」全部由 agent 调原子 CLI 完成，本目录只保留**结果落库**入口
> `interview/cmd_result.py`。等价 chain（旧 `cmd_confirm` / `cmd_defer` / `cmd_reschedule` 之类）
> 见 [`docs/AGENT_RULES.md`](AGENT_RULES.md) §3 决策矩阵 与 §5 典型 chain 范式。

### `interview/cmd_result.py` — 记录面试结果

```bash
# 一面通过，发笔试邀请
python3 interview/cmd_result.py --talent-id t_xxx --round 1 --result pass --email zhangsan@example.com

# 一面通过，直接二面（跳过笔试）
python3 interview/cmd_result.py --talent-id t_xxx --round 1 --result pass_direct --round2-time "2026-05-15 15:00"

# 一面未通过，删除档案（先发 rejection_generic 拒信再删；--skip-email 可跳邮件）
python3 interview/cmd_result.py --talent-id t_xxx --round 1 --result reject_delete

# 二面通过（自动给 HR 推送 Offer 处理通知，并立刻把候选人推进到 POST_OFFER_FOLLOWUP）
python3 interview/cmd_result.py --talent-id t_xxx --round 2 --result pass

# 二面未通过，保留档案（推到 ROUND2_DONE_REJECT_KEEP 留池）
python3 interview/cmd_result.py --talent-id t_xxx --round 2 --result reject_keep

# 二面未通过，删除档案
python3 interview/cmd_result.py --talent-id t_xxx --round 2 --result reject_delete
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--round` | 是 | `1` 或 `2` |
| `--result` | 是 | round 1：`pass` / `pass_direct` / `reject_delete`（**round 1 不再支持 `reject_keep`**，未过即删）<br>round 2：`pass` / `reject_keep` / `reject_delete`（round 2 没有 `pass_direct`，也没有 `pending`） |
| `--email` | round1 + `pass` 时**必填** | 候选人邮箱（发笔试用），其他场景可选，会覆盖库中邮箱 |
| `--round2-time` | round1 + `pass_direct` 时**必填** | 二面时间，格式 `YYYY-MM-DD HH:MM` |
| `--notes` | 否 | 备注（写入审计日志） |
| `--skip-email` | 否 | round1 + `pass` 时跳过实际发笔试邮件；`reject_delete` 时跳过 `rejection_generic` 拒信（适用于已线下通知） |
| `--actor` | 否 | 执行人（默认 `system`） |

---

## 出站邮件 outbound

> **唯一**发邮件出口。所有 chain 里"发候选人邮件"都走这个；其他脚本（如 `interview/cmd_result.py`、`auto_reject/cmd_scan_exam_timeout.py`）是 subprocess 调它，不走自己的 SMTP。
> 零业务副作用：发完只写一行 `talent_emails(direction='outbound')` + 写审计；**不**改 `current_stage`、**不**改任何 round 字段。改 stage 由 chain 下一步的 `talent.cmd_update` 完成。

### `outbound/cmd_send.py` — 候选人邮件唯一入口

三种互斥模式（必须选一个，argparse 用 mutually exclusive group 强制）：

```bash
# 模式 1：模板模式（自动注入 candidate_name / company / location / talent_id 默认值）
#
# v3.8.6 起：company / location 是 email_templates/constants.py 里的公司常量,
# **不要**在 --vars 里覆盖, CLI 会 fail-loud 拒掉（防 agent 编"量化投资公司"
# 之类的假抬头）。如果公司真搬家/改名了, 改 constants.py 一处即可。
# position / position_suffix 不知道时直接省略, **不要凭空编**。
python3 outbound/cmd_send.py \
    --talent-id t_xxx --template round1_invite \
    --vars round1_time="2026-04-25 14:00" \
           position="量化研究员" position_suffix="（量化研究员）"

# 模式 2：自由文本（agent 起草 → 老板确认 → 把全文写到文件再传）
python3 outbound/cmd_send.py \
    --talent-id t_xxx \
    --subject "Re: 关于薪资的疑问" \
    --body-file /tmp/draft.txt \
    --in-reply-to '<abc@mail.example.com>'
# --cleanup-body-file 默认 ON，发送成功后自动 unlink /tmp/draft.txt

# 模式 3：缓存草稿（POST_OFFER_FOLLOWUP 一键发草稿，inbox.cmd_analyze 已写好 ai_payload.draft）
python3 outbound/cmd_send.py \
    --talent-id t_xxx \
    --use-cached-draft <inbound_email_id>
# --override-subject "..." 仅这个模式下生效，覆盖默认 'Re: 原 subject'
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--template T` | 三选一 | **模板模式**：`email_templates/<T>.txt`（不带 `.txt` 后缀） |
| `--subject S` | 三选一 | **自由文本模式**：邮件主题；必须配 `--body` 或 `--body-file` |
| `--use-cached-draft EMAIL_ID` | 三选一 | **缓存草稿模式**：从指定 inbound 邮件的 `ai_payload.draft` 读回信内容 |
| `--vars KEY=VAL ...` | 模板模式可选 | 模板变量（key=value 空格分隔多对）；不传时模板未填的占位会按 default 处理 |
| `--body B` / `--body-file FILE` | 自由文本模式二选一 | 邮件正文；推荐 `--body-file`（避免 shell 转义） |
| `--cleanup-body-file` / `--no-cleanup-body-file` | 否 | 发送后是否 unlink `--body-file`，默认 **ON** |
| `--body-normalize` / `--no-body-normalize` | 否 | 默认 **ON** = 兜底归一化（解码字面 `\n` `\t`、剥 markdown 粗体、剥行首标题）。极少用 `--no-body-normalize`（确实要发字面 `\n` 字符串时） |
| `--in-reply-to <Message-ID>` | 否 | 线程头：原邮件 Message-ID（保线程） |
| `--references <chain>` | 否 | 线程头：References 链 |
| `--cc <addr>` | 否 | 抄送 |
| `--attach FILE` | 否 | 附件路径，可重复；每个 ≤ 20MB。**两个模板已注册自动附件**，agent 不需手动传：`onboarding_offer`（实习协议 + 入职登记表）/ `exam_invite`（笔试题包，resolver 优先探测 `$RECRUIT_DATA_ROOT/exam_package/笔试题.{tar.gz,zip,tar}`，再 fallback 到 `exam_files/exam_package.zip`）；缺失会 fail-fast。 |
| `--override-subject S` | 否 | **仅 `--use-cached-draft` 模式生效**；模板/自由文本模式传它会直接报 `UserInputError`（v3.6 起） |
| `--from-name <name>` | 否 | 发件人显示名（默认 `config.email_smtp.from_name`） |
| `--context {round1,round2,exam,followup,rejection,intake,unknown}` | 否 | 覆盖按 stage 推断的 `talent_emails.context`；`rejection` 留给 auto_reject 用（参与 `has_outbound_rejection` 幂等检查） |
| `--dry-run` | 否 | 渲染 + 校验 + 模拟入库，但**不发邮件、不写 `talent_emails`** |
| `--json` | 否 | 结构化 JSON 输出 `{ok, talent_id, message_id, sent_at, template, ...}` |

**关键边界**：
- 三种模式互斥，且**至少**选一个；agent 想起草自由文本但又"顺便用模板"，必须分两次调。
- 模板模式下 `--override-subject` 会直接报错（v3.6 修复，之前是静默丢弃）。
- 模板渲染时未填的 `{KEY}` 占位会触发 `UserInputError`（防漏填）；不希望渲染的字面大括号请用 `{{` `}}` 转义。
- 失败重试：SMTP 失败抛 `SmtpSendError` exit 4；DB 写失败但邮件已发会 `SelfVerifyError` 推飞书 + exit 3，不要重发（候选人已收到）。

---

## 候选人 CRUD talent

> v3.3 五件套，全部走白名单字段 + 审计落库。`talent.cmd_update` 是 chain 改 stage / 字段的**唯一**写出口；`talent.cmd_delete` 是删候选人的**唯一**写出口（自动归档）。

### `talent/cmd_add.py` — 新建候选人（v3.3 通用入口）

```bash
# 推荐：飞书【新候选人】模板原文一次过（heredoc）
python3 talent/cmd_add.py --template "$(cat <<'EOF'
【新候选人】
姓名：张三
邮箱：zhangsan@example.com
学校：复旦大学
EOF
)" --feishu-notify

# 或：逐字段
python3 talent/cmd_add.py --name 张三 --email zhangsan@example.com \
    --position 量化研究员 --school 复旦大学 --education 硕士 --feishu-notify
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--template` | 否 | 飞书【新候选人】多行模板原文（自动解析字段，包含姓名 / 邮箱 / 学校 / 学历 / 工作年限 / 经历等） |
| `--name` | 是（或 template） | 候选人姓名 |
| `--email` | 是（或 template） | 候选人邮箱（`UNIQUE` 约束，已存在会拒） |
| `--phone` / `--wechat` / `--position` / `--education` / `--school` | 否 | 可选字段 |
| `--work-years` | 否 | 整数 |
| `--experience` | 否 | 工作经历 / 简历摘要 |
| `--source` | 否 | 来源（如 `referral` / `bilibili` / `xiaohongshu` 等） |
| `--actor` | 否 | 审计 actor（默认 `cli`） |
| `--feishu-notify` | 否 | 录入成功后推飞书卡片告知老板 |
| `--dry-run` / `--json` | 否 | 同其他 atomic CLI |

> **`talent/cmd_add` vs `intake/cmd_new_candidate` 的区别**：
> - `talent.cmd_add` 是 v3.3 的"通用 chain 入口"，**不依赖** CV 上下文；agent 在 chain 里显式新建用它。
> - `intake/cmd_new_candidate` 是 CV 入库专用：`intake/cmd_ingest_cv` 走完 LLM 解析后，会**生成** `[OC_CMD_ON_CONFIRM]` payload 提案 `intake/cmd_new_candidate.py --template ...`，HR confirm 后跑（见 SKILL §4）。
> - 两者**都仍在用**，不是替代关系。无 CV 的"逐字段 / 飞书【新候选人】文本"入库走 `talent.cmd_add`；有简历附件的入库走 SKILL §4 链路。

---

### `talent/cmd_show.py` — 候选人完整快照（只读）

```bash
python3 talent/cmd_show.py --talent-id t_xxx
python3 talent/cmd_show.py --talent-id t_xxx --json --audit-limit 50
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--audit-limit N` | 否 | 审计事件返回最近 N 条（默认 20） |
| `--json` | 否 | JSON 输出（含 `talent` snapshot、`emails_summary`、`recent_audit`） |

> chain 起步前的"探测"首选；比 `common/cmd_status.py --talent-id` 更结构化。

---

### `talent/cmd_list.py` — 候选人列表（只读）

```bash
# 列所有 EXAM_SENT 候选人
python3 talent/cmd_list.py --stage EXAM_SENT

# 多 stage 用逗号
python3 talent/cmd_list.py --stage ROUND1_SCHEDULED,ROUND2_SCHEDULED

# 模糊搜
python3 talent/cmd_list.py --search 张三

# 还有未分析 inbound 邮件的
python3 talent/cmd_list.py --has-unanalyzed
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--stage X` | 否 | stage 过滤，逗号分隔多个；**大小写敏感** |
| `--search Q` | 否 | 姓名 / 邮箱模糊匹配 |
| `--has-unanalyzed` | 否 | 只列还有 `analyzed_at IS NULL` inbound 邮件的候选人 |
| `--limit N` | 否 | 限制返回行数 |
| `--order {created,updated,stage}` | 否 | 排序字段（默认按 `updated_at` 倒序） |
| `--json` | 否 | JSON 输出 |

---

### `talent/cmd_update.py` — 改 stage / 字段唯一出口（chain 高频）

**核心**：所有 chain 改 stage / 字段都走这个。`current_stage` 必须用 `--stage`（**不要** `--set current_stage=...`，会被白名单拒）。

```bash
# 单字段示例（一面通过 = EXAM_SENT，发笔试邮件由 outbound.cmd_send 完成）
python3 talent/cmd_update.py --talent-id t_xxx --stage EXAM_SENT \
    --reason "agent: round1 pass"

# 多字段原子写（推荐 chain 用法；同一行写多 --set 一次性提交）
python3 talent/cmd_update.py --talent-id t_xxx --stage ROUND1_SCHEDULED \
    --set "round1_time=2026-04-25 14:00" \
    --set "round1_proposed_time=__NULL__" \
    --set "round1_invite_sent_at=__NOW__" \
    --set "round1_confirm_status=CONFIRMED" \
    --set "round1_calendar_event_id=evt_abc123"

# 一面轻量确认门：首次解析到时间只写 proposed，不发邮件、不建日历、不改 stage
python3 talent/cmd_update.py --talent-id t_xxx \
    --set "round1_proposed_time=2026-04-25 14:00" \
    --reason "agent: parsed HR proposed round1 time; waiting confirm"

# 清空字段（写 NULL）
python3 talent/cmd_update.py --talent-id t_xxx \
    --set "round1_calendar_event_id=__NULL__" \
    --reason "改期：旧日历已删"

# 老板"直接跳" force-jump（必须配 --force --reason，原话进审计）
python3 talent/cmd_update.py --talent-id t_xxx --stage POST_OFFER_FOLLOWUP \
    --force --reason "老板原话: 不用面了直接发 offer"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--stage NEW_STAGE` | 否 | 新 `current_stage`；**只有这个参数能改 stage** |
| `--set FIELD=VALUE` | 否 | 字段编辑（白名单内），可重复。同 FIELD 出现两次取后者并 stderr 警告 |
| `--field FIELD` / `--value V` | 否 | **[DEPRECATED]** 单字段写法，请用 `--set FIELD=VALUE` |
| `--force` | 否 | 允许 natural-transitions 之外的 stage 跳转，审计写 `forced=true` |
| `--reason "..."` | 强烈推荐 | 审计原因；`--force` 时**必填**（boss原话引用） |
| `--actor` | 否 | 审计 actor（默认 `cli`） |
| `--dry-run` / `--json` | 否 | 同其他 atomic CLI |

**`--set` 占位符**（`_resolve_token`）：
- `VALUE='__NULL__'` → 写 NULL
- `VALUE='__NOW__'` → 写当前 CST 时间（ISO 8601 `+08:00`）
- 其他原样作字符串 / 数字写入（DB 类型由列定义决定）

**Natural transitions 白名单**（无需 `--force`，`scripts/talent/cmd_update.py:_NATURAL_TRANSITIONS`）：

| from → to | 场景 |
|---|---|
| `NEW → ROUND1_SCHEDULING` | 老板/HR 给一面"待 confirm" |
| `NEW → ROUND1_SCHEDULED` | §5.11 HR 一步排一面（直达 SCHEDULED） |
| `ROUND1_SCHEDULING ↔ ROUND1_SCHEDULED` | confirm / 改期回调 |
| `ROUND1_SCHEDULED → EXAM_SENT` | 一面通过 → 发笔试 |
| `ROUND1_SCHEDULED → WAIT_RETURN` | 暂缓 |
| `EXAM_SENT → EXAM_REVIEWED` | 候选人提交笔试 |
| `EXAM_SENT → EXAM_REJECT_KEEP` | cron auto_reject 笔试 ≥3 天未交 |
| `EXAM_REVIEWED → ROUND2_SCHEDULING` | 笔试通过 → 二面 |
| `EXAM_REVIEWED → EXAM_REJECT_KEEP` | 笔试不过留池 |
| `ROUND2_SCHEDULING → ROUND2_SCHEDULED` | 仅候选人 confirm + 老板二次授权后建日历；不得从其他 stage 直达 |
| `ROUND2_SCHEDULED → ROUND2_SCHEDULING` | 二面改期回调 |
| `ROUND2_SCHEDULED → POST_OFFER_FOLLOWUP` | 二面通过 → 直接进 offer 沟通（v3.6 合并 OFFER_HANDOFF） |
| `ROUND2_SCHEDULED → ROUND2_DONE_REJECT_KEEP` | 二面**面试**不过留池（语义严格收紧，v3.8.2） |
| `ROUND2_SCHEDULED → WAIT_RETURN` | 二面前暂缓 |
| `POST_OFFER_FOLLOWUP → ONBOARDED` | 老板确认入职完成（v3.8） |
| `POST_OFFER_FOLLOWUP → OFFER_DECLINED_KEEP` | 候选人拒 offer 但留池（v3.8.2 新增；之前借用 `ROUND2_DONE_REJECT_KEEP --force`,v3.8.2 拆出独立 stage） |
| `WAIT_RETURN → ROUND{1,2}_SCHEDULING` | 候选人回国，按 `wait_return_round` 出口 |

**任何不在表里的转换**都需要 `--force`，飞书审计里会高亮 `forced=true`。

**字段白名单**（`scripts/lib/talent_db.py::_TALENT_UPDATABLE_FIELDS`）：
`candidate_email` / `candidate_name` / `phone` / `wechat` / `school` / `education` / `work_years` / `experience` / `source` / `position` / `cv_path` / `has_cpp` / `wait_return_round` / `exam_id` / `round{1,2}_time` / `round{1,2}_invite_sent_at` / `round{1,2}_calendar_event_id` / `round{1,2}_confirm_status` / `round{1,2}_reminded_at` / `round{1,2}_confirm_prompted_at` / `exam_sent_at`

> 不在白名单的字段会抛 `ValueError`；要写新字段先加到 `_TALENT_UPDATABLE_FIELDS`，并同步在 SQL 层面建好列。

---

### `talent/cmd_delete.py` — 候选人删除唯一出口（破坏性，自动归档）

```bash
# 默认归档到 data/deleted_archive/<YYYY-MM>/：DB 快照 JSON + 候选人文件目录
python3 talent/cmd_delete.py --talent-id t_xxx --reason "脏数据：测试用例残留"

# 极少：脏测试数据不写 JSON/timeline 归档；文件目录仍会归档
python3 talent/cmd_delete.py --talent-id t_xxx --reason "测试残留" --no-backup
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--reason` | 是 | 删除原因（写归档 + 审计事件，事后追溯靠它） |
| `--actor` | 否 | 默认 `cli` |
| `--no-backup` | 否 | 只跳过 DB 快照 JSON / 邮件 timeline 归档；CV、笔试提交、普通邮件附件目录仍会搬到 `deleted_archive` |
| `--dry-run` / `--json` | 否 | 同其他 atomic CLI |

删除候选人时，`cmd_delete` 会在删 DB 前归档当前正式资料目录：

- `candidate_cv/<姓名>__<talent_id>/`
- `exam_submissions/<姓名>__<talent_id>/`
- `candidates/<talent_id>/email/`

如果历史残留的 `candidates/<talent_id>/cv/` 或 `candidates/<talent_id>/exam_answer/` 又出现，也会一并归档。任一已存在文件目录归档失败时，命令会中止，不会删除 DB 行。

> 拒类操作**不要**直接 `talent.cmd_delete` 跳过拒信。人工物理删走 `interview.cmd_result --result reject_delete`（自带先发 `rejection_generic`）；人工留池走 `--result reject_keep`。cron `auto_reject` 是系统规则路径：先发 `rejection_exam_no_reply`，再用 `talent.cmd_delete` 归档删档。

---

## 通用管理 common

### `cmd_status.py` — 查看候选人状态

```bash
python3 common/cmd_status.py --all
python3 common/cmd_status.py --talent-id t_xxx
```

---

### `cmd_search.py` — 搜索候选人

```bash
python3 common/cmd_search.py --query 张三
python3 common/cmd_search.py --all-active
```

---

### `cmd_weekday.py` — 日期 → 周几查证

> **强约束**：起草任何含"X月X日（周X）" / "下周X" / "周X X点"的邮件 / 飞书草稿 /
> 日历事件标题之前 agent 必须先调一次本脚本，把 `weekday_cn` 字段照抄进 body。
> 详见 `docs/AGENT_RULES.md §7.9`。事故源点：冯屹哲 `t_59ej9u` 邮件 `f79581c1-*`
> 写"5月6日（周二）"，实际 2026-05-06 是周三。

```bash
# 验证一个日期
python3 -m common.cmd_weekday 2026-05-06
# → 2026-05-06 (周三 / Wednesday) | 距今 +14 天

# 一次验证多个候选时间段
python3 -m common.cmd_weekday today tomorrow 5-6 5月13日 +7

# JSON 给程序消费
python3 -m common.cmd_weekday 2026-05-06 --json

# 不传参 = 今天（上海时区）
python3 -m common.cmd_weekday
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `dates` (位置参数, 0+) | 否 | 不传 = 今天。支持 `2026-05-06` / `2026/5/6` / `2026.5.6` / `20260506` / `2026年5月6日` / `5-6` / `5月6日` / `today` / `tomorrow` / `+3` / `-7` / `2026-05-06 09:00`（时间忽略） |
| `--year-strategy {auto,this,next}` | 否 | 仅对**无年份**输入生效。`auto`（默认）= 今年该日没过用今年，已过自动跳明年；`this` 强制今年；`next` 强制明年 |
| `--json` | 否 | 结构化输出（多日期时是 list） |

**JSON schema**：`{date, weekday_index (0=周一/6=周日), weekday_cn, weekday_en, is_today, is_past, days_from_today, iso_with_dow, input}`

**退出码**：0 全部解析成功；2 至少一个输入解析失败（其余结果照常输出）。

**时区**：固定 `Asia/Shanghai`。

---

### `cmd_today_interviews.py` — 查看指定日期的面试安排

```bash
# 查看今天的面试安排
python3 common/cmd_today_interviews.py

# 查看指定日期
python3 common/cmd_today_interviews.py --date 2026-04-17

# 只看已确认面试
python3 common/cmd_today_interviews.py --confirmed-only

# 输出 JSON
python3 common/cmd_today_interviews.py --date 2026-04-17 --json
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--date` | 否 | 目标日期，格式 `YYYY-MM-DD`，默认今天 |
| `--confirmed-only` | 否 | 只显示已确认的面试 |
| `--json` | 否 | 输出 JSON，便于脚本调用 |

---

### `cmd_remove.py` — 物理删除候选人

```bash
python3 common/cmd_remove.py --talent-id t_xxx --confirm
```

---

### `cmd_interview_reminder.py` — 一/二面结束后催老板出结果

```bash
# cron 每轮调用，无参（一面、二面都扫；结束后缓冲 15 分钟仍未出结果就推飞书催问卡片给老板）
python3 common/cmd_interview_reminder.py
```

> 候选人维度的 `pending` 由 `lib.talent_db.get_pending_round1_reminders()` / `get_pending_interview_reminders()` 取，覆盖 round 1 的 ROUND1_SCHEDULED 和 round 2 的 ROUND2_SCHEDULED。触发时间：一面按 30 分钟计，二面按 60 分钟计，结束后再缓冲 15 分钟；若老板仍未给结果、stage 仍停留在 `ROUND{N}_SCHEDULED`，之后每 30 分钟重复提醒一次。一/二面分别推「🔔 一面结果催问提醒」「🔔 二面结果催问提醒」两类卡片。

---

## Offer 后跟进 followup

> **设计目的**：候选人通过二面后，先由老板确认是否发送入职前邮件；发送后，后续邮件沟通（入职时间、薪资细则、五险一金等）由 Hermes 接管。
> Hermes 接管后：扫描这些候选人的来信 → LLM 提取一句话意图 + 草拟回信 → 飞书推送给老板 → 老板用原子 CLI 一键回信，邮件线程严格保留（`In-Reply-To` / `References`）。
> AI 永远不出最终决定：薪资数字、入职日期等具体承诺都会被强制脱敏成「等老板/HR 确认后正式回复」。

**所属阶段**：`POST_OFFER_FOLLOWUP`（中文标签：已结束面试流程，等待发放 Offer / 沟通入职）。
`interview/cmd_result.py --result pass --round 2` 一步把 stage 推到 `POST_OFFER_FOLLOWUP`，并询问老板是否发送入职前邮件；不会立即通知 HR。

### `offer/cmd_send_onboarding_offer.py` — 发送入职前邮件并通知 HR

老板确认发送入职前邮件，并给出入职时间后使用。该命令内部调用 `outbound.cmd_send --template onboarding_offer`，模板会自动附带实习协议和入职登记表；邮件发送成功后才通知 HR。

```bash
python3 -m offer.cmd_send_onboarding_offer \
  --talent-id t_xxx \
  --onboard-date "2026-06-01"

python3 -m offer.cmd_send_onboarding_offer \
  --talent-id t_xxx \
  --onboard-date "2026-06-01" \
  --daily-rate 400 \
  --json
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--onboard-date` | 是 | 入职时间 / 日期，原样进入邮件模板 |
| `--daily-rate` | 否 | 日薪，默认 `350` |
| `--position-title` | 否 | 邮件中的岗位标题，默认 `量化研究` |
| `--interview-feedback` | 否 | 面试表现文案，有默认值 |
| `--evaluation-criteria` | 否 | 后续考核文案，有默认值 |
| `--dry-run` / `--json` | 否 | dry-run 会渲染和校验模板/附件，但不发邮件、不通知 HR |

硬规则：
- 仅允许 `POST_OFFER_FOLLOWUP` 阶段发送；其他 stage 直接拒绝。
- `daily_rate` 老板完全未提时可用默认 `350`；如果老板说“按谈好的薪资”，必须先让老板复述具体数字。
- 邮件失败时不通知 HR，避免 HR 误以为候选人已收到入职资料。
- `onboarding_offer` 自动带附件，agent 不要手动 `--attach`。

### 当前架构：`inbox.cmd_scan` + `inbox.cmd_analyze`（stage 感知）统一接管

所有候选人（含 POST_OFFER_FOLLOWUP）入站邮件走同一对扫描 / 分析脚本。`inbox.cmd_analyze` 是 **stage 感知**的：

- `POST_OFFER_FOLLOWUP` → 加载 `prompts/post_offer_followup.json`，输出含 `draft` 字段（已 `_scrub_draft` 过滤 banned_phrases），写 `talent_emails.ai_payload`。
- 其他阶段 → 加载 `prompts/inbox_general.json`，仅出 intent/summary/urgency，不生成草稿。

### 老板对一封 followup 邮件的回复 / snooze / dismiss

由 agent 调原子 CLI 完成（无专用包装脚本）：

| 动作 | 命令 |
|------|------|
| 用 AI 草稿回信 | `outbound.cmd_send --use-cached-draft EMAIL_ID --talent-id ...` |
| 自由文本回信 | `outbound.cmd_send --talent-id ... --subject "Re: ..." --body-file /tmp/x.txt --in-reply-to <inbound-msgid>` |
| 暂缓 / 忽略 | `talent_db.mark_email_status(EMAIL_ID, 'snoozed' / 'dismissed')` —— 只在邮件层面标记，stage 不变 |

完整决策（什么 intent → 哪条 chain）见 [`AGENT_RULES.md`](AGENT_RULES.md) §3 与 §5。

### 查询

```bash
# 看一个候选人完整时间线（in / out 全在 talent_emails 表里）
PYTHONPATH=scripts python3 -m inbox.cmd_review --talent-id t_xxx
```

候选人维度（谁在 POST_OFFER_FOLLOWUP）直接 SQL：
```sql
SELECT talent_id, candidate_name FROM talents WHERE current_stage='POST_OFFER_FOLLOWUP';
```
是否「待处理」改看 `talent_emails.status`（通常是 `pending_boss`）。

---

## 自动拒绝 auto_reject

> **设计目的**：只覆盖一种「系统可以主动结束面试」的场景：
> - **笔试 3 天未交**：发题后 ≥3 天候选人没有任何回信，且 `talent_emails` 也没有 `exam_sent_at` 之后的 inbound 邮件 → 发拒信 + 物理删档归档 + 飞书事后通知。
>
> **改期请求**不走自动拒：所有 reschedule 意图由 agent 看 `inbox.cmd_analyze` 输出后调 `feishu.cmd_notify` 推卡片，老板手动决定。
>
> **核心架构**：即触即终。`cmd_scan_exam_timeout` 扫到符合条件的候选人后，**立即**串行调子进程 `outbound.cmd_send --template rejection_exam_no_reply --context rejection` 发拒信 → 调 `talent.cmd_delete --confirm-delete-talent <tid>` 物理删档并归档。无缓冲窗口，不进入留池 stage。

### 关键设计原则

| 原则 | 体现 |
|---|---|
| 唯一触发场景 | EXAM_SENT 阶段 + `exam_sent_at` 距今 ≥ `--threshold-days`（默认 3） + `talent_emails` 中 `exam_sent_at` 之后无 inbound + `talent_emails` 中无 `context='rejection'` 的 outbound 记录（二次幂等防护） |
| 写动作只走唯一出口 | `executor.py` 两个 helper：`_send_rejection_email`（subprocess 调 `outbound.cmd_send`）+ `_delete_talent`（subprocess 调 `talent.cmd_delete`，带 `--confirm-delete-talent` hard guard），无自实现的 SQL/SMTP 路径 |
| 失败不重发 | 拒信发送失败 → **不**删档，记到本轮 `failed` 计数并飞书告警，下一轮 cron 再扫；删档失败 → 拒信已发，本轮算 failed 等人工 `talent.cmd_delete` 补清理，下一轮 `has_outbound_rejection` 兜底拦截不再重发 |
| 事后通知，不要按钮 | 飞书卡片标题为「[笔试超时 · 已自动拒+删档]」，纯告知；老板看到时 DB 行已经删除，归档路径在通知中 |

### `cmd_scan_exam_timeout.py` — 笔试超时扫描 + 即时拒+删档

```bash
# cron 模式（cron_runner 调）
python3 -m auto_reject.cmd_scan_exam_timeout --auto

# 手动干跑（看哪些会被拒，但不真发不真删）
python3 -m auto_reject.cmd_scan_exam_timeout --dry-run

# 调阈值（默认 3 天）
python3 -m auto_reject.cmd_scan_exam_timeout --threshold-days 5

# 真跑但不推飞书（手动验证用）
python3 -m auto_reject.cmd_scan_exam_timeout --no-feishu
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--auto` | 否 | cron 静默模式，无命中无输出 |
| `--threshold-days` | 否 | 超时阈值（默认 3 天） |
| `--dry-run` | 否 | 只打印将要拒删的候选人，不调 `cmd_send` / `cmd_delete` / 飞书 |
| `--no-feishu` | 否 | 真跑但不推飞书事后通知 |

返回：stdout 一行汇总 `rejected=X, skipped=Y, failed=Z`；非零 exit code 仅在严重 IO/DB 错误时出现，单条候选人发送失败不影响整轮。

### `exam/cmd_send_submission_to_feishu.py` — 发送最新笔试附件到飞书

```bash
PYTHONPATH=scripts python3 -m exam.cmd_send_submission_to_feishu \
    --talent-id t_xxx --to hr

PYTHONPATH=scripts python3 -m exam.cmd_send_submission_to_feishu \
    --talent-id t_xxx --open-id ou_xxx --dry-run --json
```

用途：查找候选人最新一封 `context='exam'` 且已有保存附件的 inbound 邮件，把附件通过飞书发送给 `boss|hr|polaris|interviewer-*` 或显式 `--open-id`。发送前会附带候选人、邮件时间、主题和 AI 摘要说明。

### 拒信模板

| 场景 | 模板 | 口吻 |
|--------|------|------|
| `cmd_scan_exam_timeout` 自动拒 | `email_templates/rejection/rejection_exam_no_reply.txt` | 直白（明说「未在约定时间内提交」） |
| `interview/cmd_result.py --result reject_delete` 手动拒删 | `email_templates/rejection/rejection_generic.txt` | 委婉（通用拒信，含「已保留至我们公司人才库」） |

> `interview/cmd_result.py::_handle_reject_delete`（手动 `--result reject_delete`）会先通过 `outbound.cmd_send --template rejection_generic --context rejection` 发拒信再删人，`--skip-email` 可绕过（如老板线下已发）。

---

## 飞书日历 feishu

> 日历相关写动作由两个 atomic CLI 提供：`feishu.cmd_calendar_create` 与 `feishu.cmd_calendar_delete`。
> `lib.bg_helpers.spawn_calendar` / `delete_calendar` 内部 fork 的就是这两个 CLI（`python -m feishu.cmd_calendar_*`），便于单元测试与单独排障。

### `feishu/cmd_calendar_create.py` — 创建一次面试日历事件（atomic）

```bash
PYTHONPATH=scripts python3 -m feishu.cmd_calendar_create \
    --talent-id t_xxx --time "2026-04-25 14:00" --round 2 \
    --candidate-email cand@example.com --candidate-name 张三 --json
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--time` | 是 | 面试时间 `YYYY-MM-DD HH:MM` 或 ISO；兼容历史别名 `--round2-time` |
| `--round` | 否 | `1` 或 `2`（默认 `2`） |
| `--duration-minutes` | 否 | 时长，默认 60；一面统一传 30 |
| `--candidate-email` / `--candidate-name` | 否 | 候选人邮箱 / 姓名（同时作为 attendee 加入） |
| `--extra-attendee` | 否（可重复） | 额外参会人 open_id；一面派单的非固定面试官（master / cpp）走这里 |
| `--old-event-id` | 否 | 若提供，先尝试删除旧事件 |
| `--dry-run` | 否 | 不真调飞书；JSON 仍输出 `dry_run=true` |
| `--json` | 否 | JSON 输出 `{ok, event_id, message, ...}`，便于上游回填 `talents.round{N}_calendar_event_id` |

**固定 attendee（v3.8 起，自动注入，无需 chain 显式传）**：

`feishu.cmd_calendar_create` 内部（`lib.feishu.create_interview_event`）会自动把以下 open_id 加为参会人：

1. `lib.config['feishu']['boss_open_id']`（老板）
2. `lib.config['feishu']['polaris_open_id']`（Polaris，固定日程安排者 / 运营观察者）
3. `lib.config['feishu']['hr_open_id']`（HR）

**去重保护**：若 `--extra-attendee` 里又传了老板 / Polaris / HR 的 open_id，或一面派单结果与固定参会人重复，会自动去重，不会重复邀请。`master` 表示硕士/博士候选人的一面面试官，不表示老板。
**占位符跳过**：若配置值以 `ou_PLACEHOLDER_` 开头（未配置真实账号的占位符），自动跳过并在返回消息里标注。

参见 `AGENT_RULES.md §3` 护栏与 §4.1 / §4.2 chain。

### `feishu/cmd_calendar_delete.py` — 删除一次面试日历事件（atomic）

```bash
PYTHONPATH=scripts python3 -m feishu.cmd_calendar_delete \
    --event-id evt_xxx --reason round2_defer --json
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--event-id` | 是 | 飞书日历 `event_id`（从 `talents.round{N}_calendar_event_id` 取） |
| `--reason` | 否 | 仅写日志（不传飞书） |
| `--dry-run` / `--json` | 否 | 同上 |

---

### `feishu/cmd_notify.py` — 飞书消息推送 atomic（v3.5）

> **唯一**飞书消息出口。所有"推飞书告警 / 通知卡片"都走它（chain 里的 `feishu.cmd_notify`、`auto_reject` 的事后通知卡、cron `_alert_boss` 全部 subprocess 调它）。
> 内部不走 `cli_wrapper`（避免飞书推送失败时它自己又想推飞书 → 死循环），出错只 stderr 不再二次推送。

```bash
# 推给老板（默认）
PYTHONPATH=scripts python3 -m feishu.cmd_notify \
    --severity warn --title "WAIT_RETURN 候选人主动联系" \
    --body "talent=t_xxx round=1 intent=return_to_shanghai"

# 推给 HR
PYTHONPATH=scripts python3 -m feishu.cmd_notify \
    --to hr --severity info \
    --title "新候选人 offer 已发，请准备入职" \
    --body "candidate=t_xxx 入职=2026-06-01"

# 推给一面面试官（§5.11 派单 chain 用）
PYTHONPATH=scripts python3 -m feishu.cmd_notify \
    --to interviewer-master --severity info \
    --title "一面安排：张三" \
    --body "talent=t_xxx 时间=2026-04-25 14:00 30min"

# 长正文从 stdin 读（避免 shell 转义换行 / 引号）
cat <<'EOF' | PYTHONPATH=scripts python3 -m feishu.cmd_notify --severity error \
    --title "改期 chain cal_del 失败" --stdin --source "agent.4.3.3"
talent=t_xxx round=1
原因：飞书日历 API 返回 4xx
建议：老板手动删旧日历后重试整条 chain
EOF
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--title T` | 是 | 一行标题（卡片头部） |
| `--body B` / `--stdin` | 二选一 | 正文。短的用 `--body`；多行 / 含特殊字符用 `--stdin` |
| `--severity {info,warn,error,critical}` | 否 | 严重等级，默认 `info`。`warn` / `error` / `critical` 会在卡片标题加 emoji 前缀 |
| `--to {boss,polaris,hr,interviewer-master,interviewer-bachelor,interviewer-cpp}` | 否 | 推给谁，默认 `boss`。`polaris` 使用 `lib.config['feishu']['polaris_open_id']`；`interviewer-*` 由 §5.11 派单 chain 用，open_id 来自 `lib.config['feishu']['interviewer_*_open_id']` |
| `--source S` | 否 | 调用方标识（追加在卡片尾部方便事后排查，例如 `agent.4.3.3` / `cron.cmd_interview_reminder`） |
| `--dry-run` | 否 | 不真调飞书；JSON 仍输出 `dry_run=true` |
| `--json` | 否 | 结构化 JSON 输出 |

**关键边界**：
- `cmd_notify` 是"角色路由"层，open_id 配置由 `lib.config` 集中管。临时给某个 open_id 发送**文件**时走 `feishu.cmd_send_file --open-id ou_xxx`；临时给某个 open_id 发送**文本**目前仍需新增专用 CLI，agent 不要直接 import `lib.feishu`。
- agent 在 chain 里推飞书**必须**用 `feishu.cmd_notify`（在 chain step 里）而不是 `lib.feishu.send_text`（直接 import）；前者有 self-verify + 失败计入 chain。
- `severity=critical` 不会触发额外通道（短信 / 电话），只是飞书卡片标题加红色 emoji；真要"叫醒老板"机制目前没有。

### `feishu/cmd_send_file.py` — 飞书发送本地文件

```bash
PYTHONPATH=scripts python3 -m feishu.cmd_send_file \
    --file /path/to/file.pdf --to hr --title "候选人 CV"

PYTHONPATH=scripts python3 -m feishu.cmd_send_file \
    --file /path/to/file.zip --open-id ou_xxx --dry-run --json
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--file FILE` | 是 | 本地文件路径 |
| `--to {boss,polaris,hr,interviewer-master,interviewer-bachelor,interviewer-cpp}` | 否 | 角色目标，默认 `boss` |
| `--open-id ou_xxx` | 否 | 显式目标 open_id；提供后覆盖 `--to` |
| `--title T` | 否 | 发文件前先发一条说明文本 |
| `--dry-run` / `--json` | 否 | 预览目标、文件名、大小，不上传不发送 |

业务快捷入口：

```bash
# 发送候选人 CV
PYTHONPATH=scripts python3 -m talent.cmd_send_cv_to_feishu --talent-id t_xxx --to hr

# 发送候选人最新笔试提交附件
PYTHONPATH=scripts python3 -m exam.cmd_send_submission_to_feishu --talent-id t_xxx --to boss
```

`talent.cmd_send_cv_to_feishu` 和 `exam.cmd_send_submission_to_feishu` 都支持 `--to ...` / `--open-id ou_xxx` / `--dry-run` / `--json`。二者都是外部文件发送动作，agent 必须先 dry-run 或只读查询确认文件名与目标，再按 `SKILL.md §2.3.1` propose-confirm 执行。

---

## 定时任务 cron

### `cron/cron_runner.py` — cron 入口

> **必须把 `scripts/` 加进 `PYTHONPATH`**，否则 import 不到子模块。

**任务列表（按执行顺序）** — 完整定义见 `scripts/cron/cron_runner.py::_TASKS`：

| # | 子任务 | 用途 |
|---|--------|------|
| 1 | `inbox.cmd_scan` | IMAP → `talent_emails`，所有 stage 候选人邮件统一入口 |
| 2 | `inbox.cmd_analyze` | LLM **stage 感知**分类（POST_OFFER_FOLLOWUP 走 `prompts/post_offer_followup.json` 含草稿生成；其他阶段走 `prompts/inbox_general.json`，覆盖确认 / 改期 / 暂缓 / 线上请求 / 笔试提交等 intent）+ 推飞书 |
| 3 | `common.cmd_interview_reminder` | 面试结束未出结果催老板 |
| 4 | `auto_reject.cmd_scan_exam_timeout --auto` | 笔试 ≥3 天未交 → 即触发拒信 + 物理删档归档 + 事后飞书通知 |
| 5 | `ops.cmd_health_check`（每天 09 点） | DB / IMAP / SMTP / DashScope / Feishu 体检 |

**飞书推送策略**

任一任务失败、超时或非零退出都会推 `[CRON FAIL]` 飞书告警给老板，并附带 stderr 末段。

但**成功**路径默认**不**把 stdout 整段推飞书 —— 因为：
- T2 `inbox.cmd_analyze` 内部对每封新邮件单独 `feishu.send_text`（一邮件一条卡）
- T3 `common.cmd_interview_reminder` 内部对每条催问单独 `feishu.send_text`
- T4 `auto_reject.cmd_scan_exam_timeout` 内部对每个被拒人 + 总结单独 `feishu.send_text`，空转时 stdout 已空
- T1 `inbox.cmd_scan` / T5 `ops.cmd_health_check` 不需要在空转时打扰老板

如果新加的子任务**确实**希望让 cron_runner 把整段 stdout 推飞书，给 `_TASKS`
那条加 `"notify_stdout": True` 即可（默认 False）。stdout/stderr 始终通过本进程
转写到 systemd journal（`journalctl --user -u recruit-cron-runner.service`），
方便事后排查。

```bash
cd /home/admin/recruit-workspace/skills/recruit-ops

# 完整一轮
PYTHONPATH=scripts ./.venv/bin/python -m cron.cron_runner

# 只跑一项调试
PYTHONPATH=scripts ./.venv/bin/python -m cron.cron_runner --task inbox_scan --no-lock

# 只看任务表，不真跑
PYTHONPATH=scripts ./.venv/bin/python -m cron.cron_runner --dry-run
```

### 生产部署：systemd user timer

不要用 system crontab，用 systemd user timer。两个单元文件实地放在：

```
~/.config/systemd/user/recruit-cron-runner.service
~/.config/systemd/user/recruit-cron-runner.timer
```

**.service**（每次触发执行什么）：

```ini
[Service]
Type=oneshot
WorkingDirectory=/home/admin/recruit-workspace/skills/recruit-ops
Environment=PYTHONPATH=/home/admin/recruit-workspace/skills/recruit-ops/scripts
ExecStart=/home/admin/recruit-workspace/skills/recruit-ops/.venv/bin/python -m cron.cron_runner
TimeoutStartSec=1800
StandardOutput=journal
StandardError=journal
```

**.timer**（多久跑一次 = 唯一要改的"时间"配置）：

```ini
[Timer]
OnCalendar=*:0/10          # 每 10 分钟（默认）
Persistent=true            # 关机/重启后下一轮立即补跑
RandomizedDelaySec=30      # 0-30s 抖动避免抢 IMAP/LLM
AccuracySec=30s

[Install]
WantedBy=timers.target
```

**改时间** = 改 `.timer` 里的 `OnCalendar=`，常用模板：

| `OnCalendar=` 写法 | 含义 |
|---|---|
| `*:0/5` | 每 5 分钟 |
| `*:0/10` | 每 10 分钟（当前默认） |
| `*-*-* 00,06,12,18:00:00` | 每 6 小时（00 / 06 / 12 / 18） |
| `*-*-* 09:00:00` | 每天上午 9:00 |
| `Mon..Fri *-*-* 08..20:00:00` | 工作日 8 点到 20 点每小时 |

**改完必须**：

```bash
systemctl --user daemon-reload
systemctl --user restart recruit-cron-runner.timer
systemctl --user list-timers --all | grep recruit   # 确认 next-run
```

**常用运维命令**：

```bash
# 查时间表（下次什么时候跑、上次什么时候跑过）
systemctl --user list-timers --all | grep recruit

# 立即手动触发一次（不等下个 tick）
systemctl --user start recruit-cron-runner.service

# 看最近一次执行的日志（含 stderr / 飞书告警）
journalctl --user -u recruit-cron-runner.service -n 200 --no-pager

# 临时停掉自动调度（比如生产事故时）
systemctl --user stop recruit-cron-runner.timer

# 永久禁用
systemctl --user disable --now recruit-cron-runner.timer

# 重新启用
systemctl --user enable --now recruit-cron-runner.timer
```

> **注意**：必须用 `systemctl --user`（不是 `sudo systemctl`），因为单元安装在用户目录。
> 用户登出 systemd 仍会跑（`loginctl enable-linger admin` 已开），不需要保持 SSH。

---

### `trigger_cron_now.py` — 手动提前触发 cron

```bash
uv run python3 scripts/trigger_cron_now.py
uv run python3 scripts/trigger_cron_now.py 30
```

---

