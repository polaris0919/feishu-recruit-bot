# recruit-ops CLI 参考手册

> **【v3.5 重要变更（2026-04-21）】**：所有"业务剧本"类 wrapper 已彻底删除：
> - `round1/cmd_round1_schedule` / `round2/cmd_round2_*` / 整个 `round1/` `round2/` `followup/` 目录
> - `interview/cmd_confirm` / `cmd_defer` / `cmd_reschedule`
> - `common/cmd_reschedule_request` / `cmd_finalize_interview_time` / `cmd_wait_return_resume`
> - `exam/exam_prereview` / `exam_ai_reviewer` / `daily_exam_review` / `llm_analyzer`
> - `ops/cmd_push_alert` 改名为 `feishu/cmd_notify`
>
> 这些剧本的功能由 **agent + `lib.run_chain` 串原子 CLI** 接管。规则手册见
> [`docs/AGENT_RULES.md`](AGENT_RULES.md)。本参考手册仍保留对应小节但加 ⚠️ DELETED 标注，
> 以便老用户查到旧命令名时能快速定位等价路径。
>
> **推荐执行方式**：在 `skills/recruit-ops` 仓库根目录使用 `uv run python3 scripts/...`；如果是系统 cron，使用 `PYTHONPATH=scripts ./.venv/bin/python scripts/...`（`scripts/` 下的模块互相靠相对顶层包 import，例如 `from core_state import ...`，必须把它加到 `PYTHONPATH`）。
> ```bash
> cd <RECRUIT_WORKSPACE>/skills/recruit-ops
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

0. [v3.3 解耦命令体系（推荐路径）](#v33-解耦命令体系推荐路径)
1. [招聘流水线概览](#招聘流水线概览)
2. [简历入库（intake）](#简历入库-intake)
3. [一面（round1）](#一面-round1)
4. [笔试（exam）](#笔试-exam)
5. [二面（round2）](#二面-round2)
6. [面试统一操作（interview）](#面试统一操作-interview)
7. [通用管理（common）](#通用管理-common)
8. [Offer 后跟进（followup）](#offer-后跟进-followup)
9. [自动拒绝（auto_reject）](#自动拒绝-auto_reject)
10. [定时任务（cron）](#定时任务-cron)

---

## v3.3 解耦命令体系（推荐路径）

> v3.3（2026-04-21）首次落地高度原子化命令组：每个写操作只通过一个专用脚本，
> 且每个脚本带自验证（self-verify）+ 飞书告警包装。**v3.5（2026-04-21）** 把
> 所有剧本类 wrapper（`round1/cmd_round1_*` / `interview/cmd_{confirm,defer,reschedule}`
> / `followup/cmd_followup_*` / `common/cmd_reschedule_request` 等）一次性删除，
> 编排改由 agent 在线读 [`AGENT_RULES.md`](AGENT_RULES.md) + `lib.run_chain` 完成。

### 整体架构

```
入站邮件路径：  IMAP → inbox/cmd_scan → talent_emails (analyzed_at IS NULL)
                                        ↓
                inbox/cmd_analyze (LLM 分类) → 推飞书 (need_boss_action) → set analyzed_at

老板看完决定 →  outbound/cmd_send（模板/自由文本，唯一发邮件出口，零状态副作用）
              + talent/cmd_update（唯一改 stage / 字段出口）
              + talent/cmd_delete（唯一删候选人出口，自动归档）

cron 周期：    cron/cron_runner（互斥锁 + heartbeat + 失败必报警）
                ├─ inbox/cmd_scan / inbox/cmd_analyze
                ├─ common/cmd_interview_reminder
                ├─ auto_reject/cmd_scan_exam_timeout（笔试 ≥3 天未交 → 即触发拒信 + 推 EXAM_REJECT_KEEP 留池）
                └─ ops/cmd_health_check（每天 09 点）
              # v3.5：原 followup/followup_scanner 已下线，inbox/* 接管所有 stage

ops 工具：     ops/cmd_db_migrate（增量迁移）
              ops/cmd_health_check（DB/IMAP/SMTP/LLM/Feishu 5 项体检）
              feishu/cmd_notify（统一飞书消息推送，v3.5 起，前身为 ops/cmd_push_alert）
              ops/cmd_replay_notifications（回放遗漏的入站分析卡片）

可视化：       inbox/cmd_review --talent-id   候选人邮件 timeline
              talent/cmd_show --talent-id    候选人完整快照
              talent/cmd_list --stage X      按 stage 筛选
              template/cmd_preview --list    所有邮件模板
```

### inbox/ — 入站邮件三件套

| 脚本 | 用途 | 副作用 |
|------|------|--------|
| `inbox/cmd_scan.py` | IMAP 拉所有候选人新邮件，去重写入 `talent_emails`（`direction='inbound'`, `analyzed_at IS NULL`） | 写 `talent_emails` |
| `inbox/cmd_analyze.py` | 取 `analyzed_at IS NULL` 的入站邮件，LLM 分意图 + 推飞书（仅当 `need_boss_action`） | 更新 `talent_emails.ai_*` |
| `inbox/cmd_review.py --talent-id X` | 只读，打印某候选人完整邮件 timeline（含 AI 摘要 / 模板名 / 已分析标记） | 无 |

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
# 模板模式（自动注入 candidate_name / company / talent_id 默认值）
PYTHONPATH=scripts python3 -m outbound.cmd_send \
    --talent-id t_xxx --template round1_invite \
    --vars round1_time="2026-04-25 14:00" position="量化研究员" \
           position_suffix="（量化研究员）" location="某地"

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
| `talent/cmd_add.py` | 新建候选人（替代 `intake/cmd_new_candidate.py`） | `--name --email` 必填 |
| `talent/cmd_show.py --talent-id X` | 只读，打印候选人快照 + 邮件统计 + 审计 | 无 |
| `talent/cmd_list.py [--stage X] [--search Y]` | 只读，按条件列出候选人 | 无 |
| `talent/cmd_update.py --talent-id X --stage NEW_STAGE` | 唯一改 stage / 字段出口 | 非自然跳转必须 `--force --reason "..."` |
| `talent/cmd_delete.py --talent-id X --reason "..."` | 唯一删候选人出口 | 自动归档到 `data/deleted_archive/<YYYY-MM>/` |

`cmd_update` 的「自然跳转白名单」（无需 `--force`）：`NEW → ROUND1_SCHEDULING → ROUND1_SCHEDULED → EXAM_SENT → EXAM_REVIEWED → ROUND2_SCHEDULING → ROUND2_SCHEDULED → POST_OFFER_FOLLOWUP`（v3.6 起 `OFFER_HANDOFF` 瞬时态已合并入 `POST_OFFER_FOLLOWUP`），以及 `EXAM_REJECT_KEEP` / `ROUND2_DONE_REJECT_KEEP` / `WAIT_RETURN` 等分支。其他跳转都强制要 `--force`。

### ops/ — 运维 / 一致性 / 告警

| 脚本 | 用途 |
|------|------|
| `ops/cmd_db_migrate.py [--status / --apply / --force-file FOO.sql]` | 跑 `lib/migrations/*.sql` 增量迁移；用 `recruit_migrations` 表记账 |
| `ops/cmd_health_check.py [--skip dashscope]` | DB / IMAP / SMTP / DashScope / Feishu / 邮件积压 6 项体检 |
| `feishu/cmd_notify.py --title T --body B [--severity warn]` | （v3.5：原 `ops/cmd_push_alert`）任意脚本想推飞书告警都走它（不走 cli_wrapper，避免死循环） |
| `ops/cmd_replay_notifications.py --talent-id X` | 回放某候选人 / 时间窗的入站分析飞书卡片 |

### template/ — 邮件模板

| 脚本 | 用途 |
|------|------|
| `template/cmd_preview.py --list` | 按目录分组列出所有模板 |
| `template/cmd_preview.py --template T --demo` | 用 demo 变量渲染单个模板，不发不写 |

### exam/cmd_exam_ai_review — 笔试 AI 评审（已下线 v3.3 包装）

`exam/cmd_review_submission.py`（v3.3 薄包装）已删除。直接用 `exam/cmd_exam_ai_review.py`：拉 IMAP 提交 → LLM 评审 → 落盘 → 推飞书 → 写审计。详细参数见下方 `exam` 章节，老板看完结果后自己 `talent/cmd_update` 推进 stage。

### auto_reject/ — 笔试超时即触即拒删（已大幅简化，2026-04-23 起）

只剩一个脚本：`auto_reject/cmd_scan_exam_timeout.py`。
- **触发条件**：`current_stage='EXAM_SENT'` 且 `exam_sent_at` 距今 ≥ `--threshold-days`（默认 3），且 `talent_emails` 没有 `exam_sent_at` 之后的 inbound 记录，且 `talent_emails` 没有任何 `context='rejection'` 的 outbound 记录（v3.5.11 加的二次幂等防护）。
- **执行动作**（v3.5.11 起）：调子进程 `outbound.cmd_send --template rejection_exam_no_reply --context rejection` 发拒信 → 调 `lib.talent_db.set_current_stage(tid, 'EXAM_REJECT_KEEP')` 把候选人推到留池终态 + 写一行 `talent_events` 审计 → 推一张飞书"已拒+留池"通知卡片给老板（事后告知，不需要老板按按钮）。
- **关键去除**：原来的 `cmd_propose` / `cmd_cancel` / `cmd_execute_due` / `cmd_list` / `pending_store` / `llm_classify` / `data/auto_reject_pending|archive/` / `talents.pending_rejection_id` 字段全部删除。
- **v3.5.11 (2026-04-22) 设计变更**：从"拒+物理删档"改成"拒+留池 EXAM_REJECT_KEEP"。事故触发：cmd_send 在 SMTP 已发完后才校验 `context`，DB CHECK 约束又漏 `'rejection'`，导致写库 raise → executor 误判失败 → cmd_delete 没触发 → 候选人留 EXAM_SENT → 下个 cron tick 重发。改成"留池"后即便 mark stage 那步崩了也有 has_outbound_rejection 兜底，绝不重发。详细修复见 `lib/migrations/20260422_v3511_talent_emails_context_rejection.sql`。
- **临近改期**不再走自动拒：扫到的改期请求统一交给 `common/cmd_reschedule_request.py`（`daily_exam_review._run_reschedule_scan` 推飞书 + 写入 `talent_emails`），由老板手动决定是否拒。

### cron/cron_runner.py — v3.3 编排器

替代旧 `scripts/cron_runner.py`。任务表见脚本头注释；新增 `inbox.cmd_scan` / `inbox.cmd_analyze` / `ops.cmd_health_check`（每天 09 点）三项。失败任务统一走 `_alert_boss`。

```bash
# 完整一轮
PYTHONPATH=scripts python3 -m cron.cron_runner

# 只跑一项调试
PYTHONPATH=scripts python3 -m cron.cron_runner --task inbox_scan --no-lock

# 只看任务表，不真跑
PYTHONPATH=scripts python3 -m cron.cron_runner --dry-run
```

### lib/ — 基础库（v3.3 新增）

- `lib/cli_wrapper.py`：所有写脚本的统一入口包装；遇 `SelfVerifyError` 推飞书 + exit 3；遇 `UserInputError` 仅 stderr + exit 1（不告警，避免骚扰老板）。
- `lib/self_verify.py`：post-action 断言库（`assert_email_sent` / `assert_emails_inserted` / `assert_email_analyzed` / `assert_talent_state` / `assert_talent_deleted`）。
- `lib/smtp_sender.py`：SMTP 发送底层；`send_email_with_threading` 加了 `normalize_subject` 开关给 `cmd_send` 用。
- `lib/talent_db.py`：扩展了 `set_email_analyzed`、`list_unanalyzed_inbound`、`get_full_talent_snapshot`、`update_talent_field`、`set_current_stage`、`talent_exists` 等 helper。

---

---

## 招聘流水线概览

> v3.5：流水线的"前进 / 倒车 / 暂缓"动作不再有 1:1 的 wrapper 脚本。下面流程图标的箭头
> 全部由 **agent 调 atomic CLI（详见 [`AGENT_RULES.md`](AGENT_RULES.md)）** 完成。

```
简历进库
  ↓  intake/cmd_ingest_cv 或 intake/cmd_import_candidate
NEW
  ↓  agent: outbound.cmd_send round1_invite + talent.cmd_update --stage ROUND1_SCHEDULING
ROUND1_SCHEDULING（等候候选人确认）
  ↓  agent: talent.cmd_update --stage ROUND1_SCHEDULED + feishu.cmd_calendar_create
ROUND1_SCHEDULED（一面已安排）
  ↓  interview/cmd_result --round 1 --result pass
EXAM_SENT（一面通过 = 直接发笔试，无独立"一面通过"中间态）
  ↓  inbox.cmd_scan + inbox.cmd_analyze（自动扫候选人提交） + exam.cmd_exam_ai_review
EXAM_REVIEWED
  ↓  exam/cmd_exam_result --result pass --round2-time "..."
ROUND2_SCHEDULING（等候候选人确认）
  ↓  agent: talent.cmd_update --stage ROUND2_SCHEDULED + feishu.cmd_calendar_create
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
    --field education=博士 --field school="示例大学"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--cv-path` | 否 | 简历本地路径（会写入 `cv_path` 字段） |
| `--confirm` | 是 | 显式确认才会执行写入 |
| `--field key=value` | 否 | 同步更新白名单字段，可重复。允许的 key：`candidate_name` / `candidate_email` / `phone` / `wechat` / `position` / `education` / `school` / `work_years` / `source` / `experience` |

---

### `cmd_parse_cv.py` — 已废弃

> ⚠️ 此脚本 `main()` 已弃用，直接执行会返回非零并打印迁移提示。**请统一使用 `cmd_ingest_cv.py`**，它会自动判断候选人是否已在库中并分支处理（新候选人走解析+预览，老候选人走字段比对+差异预览）。
>
> 脚本内部仍有 `_llm_parse_cv_fields` / `_extract_text_from_pdf` 等工具函数，供 `cmd_ingest_cv.py` import 使用，不再作为 CLI 入口暴露。

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
    --position 量化研究员 --school 示例大学 --feishu-notify
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

## 一面 round1 ⚠️ 整个目录 v3.5 已删除

> v3.5（2026-04-21）一次性删除整个 `scripts/round1/` 目录。
> `cmd_round1_schedule.py` / `cmd_round1_defer.py` / `cmd_round1_confirm.py` /
> `cmd_round1_result.py` / `cmd_round1_reschedule.py` 全部下线。
>
> **等价路径**（agent 自行调，不要再写 wrapper）：
>
> | 旧 wrapper | 新写法 |
> |-----------|--------|
> | `cmd_round1_schedule --time T` | `outbound.cmd_send --template round1_invite --vars round1_time=T` 然后 `talent.cmd_update --stage ROUND1_SCHEDULING --set round1_time=T --set round1_invite_sent_at=__NOW__ --set round1_confirm_status=PENDING` |
> | `interview.cmd_confirm --round 1` | `talent.cmd_update --stage ROUND1_SCHEDULED --set round1_confirm_status=CONFIRMED` 然后 `feishu.cmd_calendar_create --round 1 ...` 再 `talent.cmd_update --set round1_calendar_event_id=<event_id>` |
> | `interview.cmd_defer --round 1` | `feishu.cmd_calendar_delete --event-id <id>` 然后 `talent.cmd_update --stage WAIT_RETURN --set wait_return_round=1 --set round1_calendar_event_id=__NULL__` |
> | `interview.cmd_reschedule --round 1` | `feishu.cmd_calendar_delete` →（如有旧日历）`outbound.cmd_send --template round1_reschedule` → `talent.cmd_update --set round1_time=NEW` |
>
> 完整规则（哪种 intent 触发哪条 chain）见 [`docs/AGENT_RULES.md`](AGENT_RULES.md)。
> 一面 / 二面 **结果** 仍由 `interview/cmd_result.py` 处理（保留），见 § "面试统一操作 interview"。

---

## 笔试 exam

### `cmd_exam_result.py` — 记录笔试结果

```bash
# 笔试通过，安排二面
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
| `--round2-time` | result=pass 时**必填** | 二面时间，格式 `YYYY-MM-DD HH:MM`；脚本会拒绝复用旧二面时间 |
| `--notes` | 否 | 备注（写入审计日志） |
| `--actor` | 否 | 执行人（默认 `system`） |

---

### `cmd_exam_ai_review.py` — AI 笔试评审（rubric 驱动，自带 IMAP 拉取）

按 `exam_files/rubric.json` 对单个候选人提交跑一次 AI 评审，输出结构化打分 + 理由 + 给老板的可执行下一步建议。**不修改候选人状态机字段**；最终通过/不通过仍由老板使用 `cmd_exam_result.py` 决定。

**默认行为**：只要传 `--talent-id`，会**自动**从 IMAP 拉取该候选人最新笔试回复邮件（缓存到 `/tmp/exam_submissions/<talent_id>/`），自动从 `_email_body.txt` 读取邮件正文、从 `_email_meta.txt` 的 `Date` 推断 `submitted_at`，自动跳过 `data/raw/原始数据` 等输入数据子目录里的 CSV。无需先手动跑 `fetch_exam_submission.py`。

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
| `--cache-dir` | 否 | IMAP / 评审结果的本地缓存根目录（默认 `/tmp/exam_submissions`） |
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

### `daily_exam_review.py` ⚠️ v3.5 已删除

> v3.5 删除整个 `daily_exam_review.py`，邮件扫描职责由 `inbox.cmd_scan` + `inbox.cmd_analyze`
> 接管（已在 cron 表中替换）。LLM 评审能力搬到 `lib/exam_grader.py`，IMAP 工具搬到
> `lib/exam_imap.py`（仅供 `inbox.cmd_scan` / `exam.cmd_exam_ai_review` / 历史回填脚本
> 等内部 import）。
>
> 改期请求 / 暂缓请求由 agent 在收到 `inbox.cmd_analyze` 输出后按
> [`AGENT_RULES.md`](AGENT_RULES.md) §3 决策矩阵处理：所有 reschedule 意图都先
> `feishu.cmd_notify` 推卡片让老板手动判断；不再有"临近改期自动拒"的本地决策。

---

### `cmd_exam_timeout_scan.py` — 笔试超时扫描（已下线）

> v3.3 后该脚本已删除。新版统一走 [`auto_reject/cmd_scan_exam_timeout.py`](#自动拒绝-auto_reject)，行为已**简化为即触即终**（无 12h 缓冲；命中即发拒信 + 推 stage 到 `EXAM_REJECT_KEEP` 留池 + 飞书事后通知；v3.5.11 起从"拒+删档"改成"拒+留池"），CLI 参数为 `--auto` / `--dry-run` / `--threshold-days` / `--no-feishu`。cron 任务表也已切换到新路径。

---

## 二面 round2 ⚠️ 整个目录 v3.5 已删除

> v3.5 删除整个 `scripts/round2/` 目录。`cmd_round2_confirm` / `cmd_round2_result`
> / `cmd_round2_reschedule` / `cmd_round2_defer` 全部下线。
>
> **二面 result** 仍由 `interview/cmd_result.py --round 2` 处理（保留）。
> **二面 confirm / defer / reschedule** 由 agent 调原子 CLI（参考一面那张映射表，把
> `--round 1` 换成 `--round 2`，`round1_*` 字段换成 `round2_*`）。
>
> 完整决策规则见 [`docs/AGENT_RULES.md`](AGENT_RULES.md)。

---

## 面试统一操作 interview

> **v3.5 目录大瘦身**：原 `interview/` 下的 `cmd_confirm.py` / `cmd_defer.py` /
> `cmd_reschedule.py` 三个剧本 wrapper **已全部删除**，编排回到 agent 手里。
> 本目录现在**只剩** `interview/cmd_result.py`（一面/二面结果 → 推下一 stage 的 atomic CLI）。
>
> 旧的 confirm / defer / reschedule 等价路径见 [`docs/AGENT_RULES.md`](AGENT_RULES.md)
> §3 决策矩阵 与 §5 典型 chain 范式。

### `interview/cmd_confirm.py` ⚠️ v3.5 已删除

> 等价 chain：`talent.cmd_update --stage ROUND{N}_SCHEDULED --set round{N}_confirm_status=CONFIRMED`
> → `feishu.cmd_calendar_create --round N --time T ...` →
> `talent.cmd_update --set round{N}_calendar_event_id=<event_id>`。

### `interview/cmd_result.py` — 记录面试结果（保留）

```bash
# 一面通过，发笔试邀请
python3 interview/cmd_result.py --talent-id t_xxx --round 1 --result pass --email zhangsan@example.com

# 一面通过，直接二面（跳过笔试）
python3 interview/cmd_result.py --talent-id t_xxx --round 1 --result pass_direct --round2-time "2026-05-15 15:00"

# 二面通过（自动给 HR 推送 Offer 处理通知，并立刻把候选人推进到 POST_OFFER_FOLLOWUP）
python3 interview/cmd_result.py --talent-id t_xxx --round 2 --result pass

# 二面结论待定
python3 interview/cmd_result.py --talent-id t_xxx --round 2 --result pending

# 未通过，保留档案
python3 interview/cmd_result.py --talent-id t_xxx --round 1 --result reject_keep

# 未通过，删除档案
python3 interview/cmd_result.py --talent-id t_xxx --round 2 --result reject_delete
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--round` | 是 | `1` 或 `2` |
| `--result` | 是 | round 1：`pass` / `pass_direct` / `reject_keep` / `reject_delete`；round 2：`pass` / `pending` / `reject_keep` / `reject_delete` |
| `--email` | round1 + `pass` 时**必填** | 候选人邮箱（发笔试用），其他场景可选，会覆盖库中邮箱 |
| `--round2-time` | round1 + `pass_direct` 时**必填** | 二面时间，格式 `YYYY-MM-DD HH:MM` |
| `--notes` | 否 | 备注（写入审计日志） |
| `--skip-email` | 否 | round1 + `pass` 时跳过实际发笔试邮件（仅改状态） |
| `--actor` | 否 | 执行人（默认 `system`） |

---

### `interview/cmd_reschedule.py` ⚠️ v3.5 已删除

> 等价 chain：（如有旧日历）`feishu.cmd_calendar_delete --event-id <旧>` →
> `outbound.cmd_send --template round{N}_reschedule --vars round{N}_time=NEW` →
> `talent.cmd_update --stage ROUND{N}_SCHEDULING --set round{N}_time=NEW --set round{N}_confirm_status=PENDING --set round{N}_calendar_event_id=__NULL__`
> （老板已拍板想立刻 `CONFIRMED`：把上面那条改成 `--stage ROUND{N}_SCHEDULED --set round{N}_confirm_status=CONFIRMED`，
> 然后再 chain 一步 `feishu.cmd_calendar_create`）。

### `interview/cmd_defer.py` ⚠️ v3.5 已删除

> 等价 chain：（如有旧日历）`feishu.cmd_calendar_delete --event-id <旧>` →
> `talent.cmd_update --stage WAIT_RETURN --set wait_return_round=N --set round{N}_calendar_event_id=__NULL__`
> →（可选）`outbound.cmd_send --template defer_ack`。

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

### `cmd_email_preview.py` — 邮件模板渲染预览（已下线）

> v3.3 后该脚本已删除，等价命令为 [`template/cmd_preview.py`](#v33-解耦命令体系推荐路径)，参数完全相同（`--template` / `--demo` / `--var KEY=VALUE` / `--list`）。

---

### `cmd_email_thread.py` — 查看候选人完整邮件时间线（已下线）

> v3.3 后该脚本已删除，等价命令为 [`inbox/cmd_review.py`](#v33-解耦命令体系推荐路径)（额外显示 AI intent / template / analyzed_at）：
>
> ```bash
> PYTHONPATH=scripts python3 -m inbox.cmd_review --talent-id t_demo07
> ```
>
> 模糊查改用 `talent/cmd_list.py --search "候选人L"`。

输出示例（保留作为格式参考）：

```
================================================================
候选人: 候选人L (t_demo07)  邮箱: candidate-l@example.com  当前阶段: POST_OFFER_FOLLOWUP
跟进状态: ACTIVE  entered_at: 2026-04-17 13:35:03+08:00
共 7 封邮件

────────────────────────────────────────────────────────────────
[2026-03-26T12:16:31] ← 候选人发 | exam     | 状态:已自动处理
  主题: Re:【笔试邀请】示例科技公司 技术岗位笔试
  AI:  候选人反馈附件无法解压，请求重发笔试文件  [intent=logistics]
  正文摘要: 您好，这个附件显示为空，无法解压，能不能请您重发一份…
...
```

---

### `cmd_weekday.py` — 日期 → 周几查证（v3.5.14 / 2026-04-22）

> **强约束**：起草任何含"X月X日（周X）" / "下周X" / "周X X点"的邮件 / 飞书草稿 /
> 日历事件标题之前 agent 必须先调一次本脚本，把 `weekday_cn` 字段照抄进 body。
> 详见 `docs/AGENT_RULES.md §7.9`。事故源点：候选人A `t_demo01` 邮件 `msg_demo_*`
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

### `cmd_reschedule_request.py` ⚠️ v3.5 已删除

> 候选人发"想改期"邮件时，`inbox.cmd_analyze` 会输出 `intent=reschedule_request`。
> agent 按 [`AGENT_RULES.md`](AGENT_RULES.md) §3 推一条 `feishu.cmd_notify` 卡片让老板拍板，
> 不再需要专用 wrapper。

### `cmd_wait_return_resume.py` ⚠️ v3.5 已删除

> 等价：候选人主动来信 → `inbox.cmd_notify` 提示 → 老板手动
> `talent.cmd_update --stage ROUND{N}_SCHEDULING --force --reason "WAIT_RETURN resume"`
> （N 取 `wait_return_round`），随后照"安排面试"chain 走。

### `cmd_finalize_interview_time.py` ⚠️ v3.5 已删除

> 等价：直接 `talent.cmd_update --set round{N}_time=...`。

---

### `cmd_interview_reminder.py` — 二面结束后催老板出结果

```bash
python3 common/cmd_interview_reminder.py
```

---

## Offer 后跟进 followup

> **设计目的**：候选人通过二面、HR 已发 Offer 之后，邮件沟通（入职时间、薪资细则、五险一金等）原本会湮没在 HR 邮箱。  
> Hermes 接管后：扫描这些候选人的来信 → LLM 提取一句话意图 + 草拟回信 → 飞书推送给老板 → 老板用 `cmd_followup_reply.py` 一键回信，邮件线程严格保留（`In-Reply-To` / `References`）。  
> AI 永远不出最终决定：薪资数字、入职日期等具体承诺都会被强制脱敏成"等老板/HR 确认后正式回复"。

阶段：`POST_OFFER_FOLLOWUP`（中文标签：已结束面试流程，等待发放 Offer / 沟通入职）。v3.6 起 `OFFER_HANDOFF` 瞬时态已删除——`interview/cmd_result.py --result pass --round 2` 现在一步就把 stage 推到 `POST_OFFER_FOLLOWUP`（HR Feishu 通知依然发）。v3.5.2 起 `enter_post_offer_followup()` 与 `followup_*` 字段一并下线。

### v3.4 Phase 1–3：followup_scanner / followup_analyzer / pending_store 已删除

v3.4 起，所有候选人（含 POST_OFFER_FOLLOWUP）入站邮件由 `inbox.cmd_scan` + `inbox.cmd_analyze` 统一接管。`inbox.cmd_analyze` 是 **stage-aware** 的：

- `POST_OFFER_FOLLOWUP` → 加载 `prompts/post_offer_followup.json`，输出含 `draft` 字段（已 `_scrub_draft` 过滤 banned_phrases），写 `talent_emails.ai_payload`。
- 其他阶段 → 加载 `prompts/inbox_general.json`，仅出 intent/summary/urgency，不生成草稿。

历史脚本删除清单：

| 已删除 | 替代 |
|---|---|
| `followup/followup_scanner.py` | `inbox.cmd_scan` + `inbox.cmd_analyze`（cron 中已替换） |
| `followup/followup_analyzer.py` | `inbox/analyzer.py`（stage-aware，加载 `prompts/post_offer_followup.json`） |
| `followup/pending_store.py` | `talent_emails` 表本身（`ai_payload` / `status` / `replied_by_email_id`） |
| `followup/cmd_followup_list.py`（2026-04-22 已删） | `PYTHONPATH=scripts python3 -m inbox.cmd_review --talent-id ...` |

老板对一封 followup 邮件的所有动作改用 `--email-id`（即 `talent_emails.email_id`）入参，旧 `reply_id` 入参已废弃。

<!-- v3.5.2 (2026-04-21) 注：旧 followup_scanner 整套设计已彻底删除。这里保留两个历史断面只为
"为什么这样设计 inbox.cmd_scan + talent_emails" 的来龙去脉：
- 多层去重最终收敛到 talent_emails (talent_id, message_id) UNIQUE 一层，
  followup_last_email_id / *_last_email_id 单游标 v3.5.2 已 DROP（迁移见
  scripts/lib/migrations/20260421_v35_drop_dead_columns.sql）。
- POST_OFFER_FOLLOWUP 阶段的"按 followup_entered_at 时间过滤"也已不再做：
  inbox.cmd_scan 直接全表扫候选人邮箱，duplicate_skipped 由 message_id UNIQUE 兜底；
  followup_entered_at 字段同样 DROP。
完整入站邮件链路见 §scripts/inbox。
-->

### 已移除：`followup/cmd_followup_list.py`（2026-04-22）

旧实现把 `data/followup_pending/` 当"待办列表"展示，会误报很多老板线下已经处理过的邮件。
所有"邮件流水 / 历史"查询统一改走 `inbox/cmd_review.py`（v3.3）：

```bash
# 看一个候选人完整时间线（in / out 全在 talent_emails 表里）
PYTHONPATH=scripts python3 -m inbox.cmd_review --talent-id t_xxx
```

candidate 维度查询（谁在 POST_OFFER_FOLLOWUP）直接 SQL：
`SELECT talent_id, candidate_name FROM talents WHERE current_stage='POST_OFFER_FOLLOWUP'`。
（v3.5.2：原 `followup_status` / `followup_last_email_id` 字段已 DROP，
是否「待处理」改看 `talent_emails.status` —— 通常是 `pending_boss`。）

### `followup/cmd_followup_reply.py` ⚠️ v3.5 已删除

> v3.5 把整个 `followup/` 目录下架。`cmd_followup_reply` / `cmd_followup_close`
> 全部退化为 agent 调原子 CLI，无须 wrapper：
>
> | 旧 wrapper 动作 | 等价原子 CLI |
> |----------------|------------|
> | `--use-draft` | `outbound.cmd_send --use-cached-draft EMAIL_ID --talent-id ...` |
> | `--message "..."` | `outbound.cmd_send --talent-id ... --subject "Re: ..." --body-file /tmp/x.txt --in-reply-to <inbound-msgid>` |
> | `--snooze 24h` | `talent_db.mark_email_status(EMAIL_ID, 'snoozed')` 标 inbound 邮件（v3.5.2：原 `followup_status=SNOOZED + followup_snoozed_until` 字段已 DROP，snooze 语义只在邮件层面保留） |
> | `--dismiss` | `talent_db.mark_email_status(EMAIL_ID, 'dismissed')` |
> | `--close` | `talent_db.mark_email_status(EMAIL_ID, 'dismissed')` —— 不再改 `followup_status`，stage 保持 `POST_OFFER_FOLLOWUP`，因为 v3.5 没有「followup 已结案」终态（详见 `AGENT_RULES.md` §3.2 不对称设计） |
>
> 完整决策（什么 intent → 哪条 chain）见 [`AGENT_RULES.md`](AGENT_RULES.md) §3 与 §5。

### `followup/cmd_followup_close.py` ⚠️ v3.5 已删除

> 等价：`talent_db.mark_email_status(EMAIL_ID, 'dismissed')`（v3.5.2：原 `followup_status=CLOSED` 字段已 DROP，结案信息只写在 inbound 邮件 status + audit 事件里）。

---

## 自动拒绝 auto_reject

> **设计目的**：只覆盖一种"系统可以主动结束面试"的场景：
> - **笔试 3 天未交**：发题后 ≥3 天候选人没有任何回信，且 `talent_emails` 也没有 `exam_sent_at` 之后的 inbound 邮件 → 发拒信 + 推 stage 到 `EXAM_REJECT_KEEP`（留人才库）+ 飞书事后通知。
>
> **临近改期**不再自动拒；扫到的改期请求一律走 `common/cmd_reschedule_request.py`，由老板手动决定。
>
> **核心架构**（v3.5.11 / 2026-04-22 重设计）：即触即终。`cmd_scan_exam_timeout` 扫到符合条件的候选人后，**立即**串行调子进程 `outbound.cmd_send --template rejection_exam_no_reply --context rejection` 发拒信 → in-process 调 `lib.talent_db.set_current_stage(tid, 'EXAM_REJECT_KEEP')` 改 stage + 写审计行；无 12h 缓冲窗口、无 `cmd_propose / cmd_cancel / cmd_execute_due / cmd_list`、无 `data/auto_reject_pending|archive/`、无 `talents.pending_rejection_id`、不再调 `talent.cmd_delete`。
>
> **v3.5.11 设计变更前因**：v3.4 旧版扫到就发拒信 + `talent.cmd_delete` 删档进 `data/deleted_archive/`。2026-04-22 11:30 cron tick 触发事故——`outbound.cmd_send` 在 SMTP 已发出后才校验 `context`，但 Python 白名单和 DB CHECK 约束都漏了 `'rejection'`，写库 raise → executor 误判失败 → `cmd_delete` 没触发 → 候选人留 `EXAM_SENT` → 下个 cron tick 重发。改成"留池"后即便 mark stage 那步崩了也有 `talent_db.has_outbound_rejection` 兜底，绝不重发。修复 commit：`lib/migrations/20260422_v3511_*.sql` + `_EMAIL_VALID_CONTEXTS` + `_mark_exam_rejected_keep`。

### 关键设计原则

| 原则 | 体现 |
|---|---|
| 唯一触发场景 | EXAM_SENT 阶段 + `exam_sent_at` 距今 ≥ `--threshold-days`（默认 3） + `talent_emails` 中 `exam_sent_at` 之后无 inbound |
| 写动作只走 v3.3 唯一出口 | `executor.py` 现在两个 helper：`_send_rejection_email`（subprocess 调 `outbound.cmd_send`）+ `_mark_exam_rejected_keep`（v3.5.11，in-process 调 `talent_db.set_current_stage` 推 stage 留池——它本身就是 v3.3 的 stage 写入唯一入口、自带审计），不再有自实现的 SQL/SMTP 路径，也不再调 `talent.cmd_delete` |
| 失败不留半 | 拒信发送失败 → **不**改 stage，记到本轮 `failed` 计数并飞书告警，下一轮 cron 再扫；mark stage 失败 → 拒信已发，本轮算 failed 等人工把 stage 改成 `EXAM_REJECT_KEEP`，下一轮 `has_outbound_rejection` 兜底拦截不再重发 |
| 事后通知，不要按钮 | 飞书卡片标题为"[笔试超时 · 已自动拒+留池]"，纯告知；老板看到时候选人 stage 已经在 `EXAM_REJECT_KEEP` |
| 改期决策回到老板 | v3.5：所有 reschedule 意图由 agent 看 `inbox.cmd_analyze` 输出后调 `feishu.cmd_notify` 推卡片，老板看原文后自己拍板 |

### `cmd_scan_exam_timeout.py` — 笔试超时扫描 + 即时拒+留池

```bash
# cron 模式（cron_runner 调）
python3 -m auto_reject.cmd_scan_exam_timeout --auto

# 手动 dry-run（看哪些会被拒，但不真发不真删）
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

### 拒信模板

| 场景 | 模板 | 口吻 |
|--------|------|------|
| `cmd_scan_exam_timeout` 自动拒 | `email_templates/rejection/rejection_exam_no_reply.txt` | honest（明说"未在约定时间内提交"） |
| `interview/cmd_result.py --result reject_delete` 手动拒删 | `email_templates/rejection/rejection_generic.txt` | warm（通用拒信，含"已保留至我们公司人才库"） |

> **历史变更**：原 `rejection/rejection_late_reschedule.txt` 已并入 `rejection_generic.txt`。原 `cmd_propose --reason late_reschedule|exam_no_reply|manual` / `cmd_cancel` / `cmd_execute_due` / `cmd_list` / `pending_store` / `llm_classify` / `talents.pending_rejection_id` 字段全部下线，相关迁移 `lib/migrations/20260423_drop_pending_rejection_id.sql`。

> **`_handle_reject_delete` 拒信补全**：`interview/cmd_result.py::_handle_reject_delete`（手动 `--result reject_delete`）现在会先发 `rejection_generic` 拒信再删人，`--skip-email` 可绕过（如老板线下已发）。

---

## 飞书日历 feishu（v3.4 Phase 5）

> v3.4 Phase 5 把日历相关写动作从 `lib/feishu/calendar_cli.py`（已删除）解耦为两个 atomic CLI：`feishu.cmd_calendar_create` 与 `feishu.cmd_calendar_delete`。
> `interview/cmd_confirm.py` / `cmd_defer.py` / `cmd_reschedule.py` 仍通过 `lib.bg_helpers.spawn_calendar` / `delete_calendar` 调用日历，但 bg_helpers 内部现在 fork 的是新 atomic CLI（`python -m feishu.cmd_calendar_*`），便于单元测试与单独排障。

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
| `--candidate-email` / `--candidate-name` | 否 | 用于邀请参会人与日历标题 |
| `--old-event-id` | 否 | 若提供，先尝试删除旧事件 |
| `--dry-run` | 否 | 不真调飞书；JSON 仍输出 `dry_run=true` |
| `--json` | 否 | JSON 输出 `{ok, event_id, message, ...}`，便于上游回填 `talents.round{N}_calendar_event_id` |

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

## 定时任务 cron

### `cron/cron_runner.py` — 独立 cron 入口（v3.3）

> 替代旧 `scripts/cron_runner.py`（v3.3 Phase 9 已删除）。v3.5 起 `exam.daily_exam_review`
> 已下线，cron 任务表中只保留下表 5 项。**必须把 `scripts/` 加进 `PYTHONPATH`**，否则
> import 不到子模块。

**任务列表（按执行顺序）** — 完整定义见 `scripts/cron/cron_runner.py::_TASKS`：

| # | 子任务 | 用途 |
|---|--------|------|
| 1 | `inbox.cmd_scan` | IMAP → `talent_emails`（v3.3 新增；v3.4 起统一接管所有阶段，含 POST_OFFER_FOLLOWUP；v3.5 也接管原 daily_exam_review 的扫描职责） |
| 2 | `inbox.cmd_analyze` | LLM **stage-aware** 分类（POST_OFFER_FOLLOWUP 走 `prompts/post_offer_followup.json` 含草稿生成；其他阶段走 `prompts/inbox_general.json`，v3.5 把改期 / 暂缓 / 线上请求等 intent 也并入此 prompt）+ 推飞书 |
| 3 | `common.cmd_interview_reminder` | 面试结束未出结果催老板 |
| 4 | `auto_reject.cmd_scan_exam_timeout --auto` | 笔试 ≥3 天未交 → 即触发拒信 + 推 stage 到 `EXAM_REJECT_KEEP`（v3.5.11 起留池不删档）+ 事后飞书通知 |
| 5 | `ops.cmd_health_check`（每天 09 点） | DB / IMAP / SMTP / DashScope / Feishu 体检（v3.3 新增） |

> v3.4 Phase 3 起原 `followup.followup_scanner --auto` 已删除；
> v3.5 起原 `exam.daily_exam_review --auto` 也已删除。所有候选人入站邮件由 T1/T2 统一接管。

**飞书推送策略（v3.5.12 / 2026-04-22 静默改造）**

任一任务失败、超时或非零退出都会推 `[CRON FAIL]` 飞书告警给老板，并附带 stderr 末段。

但**成功**路径默认**不**把 stdout 整段推飞书 —— 因为：
- T2 `inbox.cmd_analyze` 内部对每封新邮件单独 `feishu.send_text`（一邮件一条卡）
- T3 `common.cmd_interview_reminder` 内部对每条催问单独 `feishu.send_text`
- T4 `auto_reject.cmd_scan_exam_timeout` 内部对每个被拒人 + 总结单独 `feishu.send_text`，noop 时 stdout 已空
- T1 `inbox.cmd_scan` / T5 `ops.cmd_health_check` 不需要在 noop 时打扰老板

如果新加的子任务**确实**希望让 cron_runner 把整段 stdout 推飞书，给 `_TASKS`
那条加 `"notify_stdout": True` 即可（默认 False）。stdout/stderr 始终通过本进程
转写到 systemd journal（`journalctl --user -u recruit-cron-runner.service`），
方便事后排查。

**事故背景**：v3.5.11 之前 cron_runner 是"成功 + stdout 非空 → 整段推飞书"，
导致老板每 10min 都收到"扫了 N 封 / 暂无需催问"等 noop 噪音 + 真事件双发。
v3.5.12 修正为各任务自己负责精准推送，runner 只在失败时报警。

```bash
cd <RECRUIT_WORKSPACE>/skills/recruit-ops

# 完整一轮
PYTHONPATH=scripts ./.venv/bin/python -m cron.cron_runner

# 只跑一项调试
PYTHONPATH=scripts ./.venv/bin/python -m cron.cron_runner --task inbox_scan --no-lock

# 只看任务表，不真跑
PYTHONPATH=scripts ./.venv/bin/python -m cron.cron_runner --dry-run
```

### 生产部署：systemd user timer（v3.5.10 起的官方部署方式）

不要用 system crontab，用 systemd user timer。两个单元文件实地放在：

```
~/.config/systemd/user/recruit-cron-runner.service
~/.config/systemd/user/recruit-cron-runner.timer
```

**.service**（每次触发执行什么）：

```ini
[Service]
Type=oneshot
WorkingDirectory=<RECRUIT_WORKSPACE>/skills/recruit-ops
Environment=PYTHONPATH=<RECRUIT_WORKSPACE>/skills/recruit-ops/scripts
ExecStart=<RECRUIT_WORKSPACE>/skills/recruit-ops/.venv/bin/python -m cron.cron_runner
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

