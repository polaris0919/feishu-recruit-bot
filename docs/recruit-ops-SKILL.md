---
name: recruit-ops
description: >
  飞书招聘运营 skill，服务 HR 与 Boss 两侧。HR 侧：CV 附件自动识别、解析、去重与录入；
  Boss 侧：候选人查询、面试安排、笔试流转、结果记录、Offer 交接。PostgreSQL 为唯一数据源，
  所有操作通过 recruit-ops 仓库的稳定 CLI 完成。任何涉及候选人、简历、面试、笔试、Offer、
  人才库、招聘进度的消息都必须优先读本 skill 后再路由。
triggers:
  # 招聘核心话题
  - 招聘
  - 候选人
  - 人才库
  - 招人
  - 录用
  - 录入
  - 新候选人
  - 导入候选人
  # 阶段
  - 面试
  - 一面
  - 二面
  - 笔试
  - offer
  - 安排一面
  # 笔试 AI 评审（rubric-driven，advisory only）
  - 审阅
  - 评审
  - AI 审
  - AI 评
  - 笔试预审
  - 笔试评分
  - 笔试评审
  - 看一下笔试
  # 结果 / 自然语言判断
  - 通过了
  - 拒了
  - 不合适
  - 面了个
  - 面了一
  - 面完了
  # 简历 / 附件
  - 简历
  - 发简历
  - 附件简历
  - 简历文件
  - 简历.pdf
  - 简历.docx
  - .pdf
  - .docx
  # 常见岗位与场景关键词
  - 应届生
  - 实习生
  - 量化研究员
  - 量化研究实习
  - 元_天
  - 元/天
  # 查询短语
  - 查一下
  - 看看候选
  - 加个候选
  - 有个人
---
# Recruit Ops

> **Local deployment mapping** (remove or re-map when distributing open-source):
> - `<workspace_root>` = `/home/admin/recruit-workspace`
> - Scripts directory: `<workspace_root>/skills/recruit-ops/scripts/`
> - Runtime interpreter: `<workspace_root>/skills/recruit-ops/.venv/bin/python3` (or `uv run python3`, both equivalent)
> - Hermes Gateway loads this skill from `~/.hermes/skills/openclaw-imports/recruit-ops/`
> - File paths inside Hermes messages are already absolute (`/home/admin/...`) — pass them through verbatim to CLI `--file-path` args; do **not** manually substitute `<workspace_root>`.

Operate the `recruit-ops` workflow through stable CLI commands in the repository. PostgreSQL is the single source of truth; never summarize state from memory.

This skill is an **agent contract** used by two distinct actors:
- **HR** sends CV attachments and `【新候选人】` / `【导入候选人】` templates in the Feishu group. The skill auto-identifies CVs (§4.1.1), parses them (§2.1.5), and walks HR through a deduped register flow.
- **Boss** uses natural language to query status, drive schedule, record results, and request hand-offs.

The skill is designed to be:
- strict about routing, side-effects, and result presentation
- portable in body text (use `<workspace_root>` placeholders everywhere except the deployment-mapping block above)
- consistent with the code — when body and code disagree, code wins

---

## 1. Execution Contract

### 1.1 Canonical invocation form

Every command in this skill uses exactly one form:

```bash
uv run python3 scripts/<group>/<command>.py ...
```

Always run from the repo root:

```bash
<workspace_root>/skills/recruit-ops
```

Rules:
- Do **not** mix `uv run python3 scripts/...` and bare `python3 <group>/...`. Pick the canonical form above.
- Do **not** rely on shell aliases or machine-local absolute paths (e.g. `/home/admin/...`).
- For cron / systemd usage that needs imports, set `PYTHONPATH=scripts` explicitly (see [CLI_REFERENCE.md](../skills/recruit-ops/docs/CLI_REFERENCE.md#cron_runnerpy)).

### 1.2 Source of truth

- The PostgreSQL-backed `recruit-ops` state is canonical. All stage names, counts, and schedules must come from a command, not from the model's memory.
- When a query command already exposes a view, do **not** reconstruct that view by hand. The canonical DB-backed views are:
  - `common/cmd_status.py` — full candidate list, or detail for one candidate
  - `common/cmd_search.py` — keyword search, or the active-only view
  - `common/cmd_today_interviews.py` — day-scoped interview schedule
- Canonical stage labels live in `scripts/lib/core_state.py` (`STAGE_LABELS`). This skill mirrors them in §6; if code and skill disagree, code wins and the skill must be updated.

### 1.3 Primary reference

- [skills/recruit-ops/docs/CLI_REFERENCE.md](../skills/recruit-ops/docs/CLI_REFERENCE.md) — 完整 CLI 参数表 / edge cases / cron notes。
- [skills/recruit-ops/docs/AGENT_RULES.md](../skills/recruit-ops/docs/AGENT_RULES.md) — **agent 决策规则手册（v3.5）**：入站邮件 stage × intent 决策矩阵（§3）、典型 chain 范式（§5，6 条端到端钉死）、可用 atomic CLI 速查（§4）。**凡是本 SKILL.md §4 路由表里出现「按 chain 处理 → 见 AGENT_RULES.md §5.x」的，必须先 fetch 这份手册再行动。**

### 1.3.1 v3.5 atomic CLI architecture（**当前架构，no legacy fallback**）

自 2026-04-21 起，所有「业务剧本」脚本（`cmd_round1_schedule` / `cmd_followup_reply` / `followup_scanner` / `daily_exam_review` / `cmd_reschedule` / `cmd_defer` / `cmd_finalize_interview_time` / `cmd_wait_return_resume` / `cmd_reschedule_request` / `exam_ai_reviewer` / `ops/cmd_push_alert` 等共 13 个）已**全部删除**。当前架构只剩两层：原子 CLI + agent chain。

**核心哲学**（详见 [AGENT_RULES.md §2](../skills/recruit-ops/docs/AGENT_RULES.md)）：
> 动作 = atomic CLI；判断 / 编排 = agent（LLM 拿 §3 决策矩阵推下一步）。

#### v3.5 全量 atomic CLI 清单

| 模块 | 脚本 | 唯一职责 |
|---|---|---|
| `talent.cmd_add` | `talent/cmd_add.py` | 创建候选人（带 self-verify） |
| `talent.cmd_show` / `cmd_list` | `talent/cmd_show.py` / `cmd_list.py` | 读：单候 / 列表；`--json` |
| `talent.cmd_update` | `talent/cmd_update.py` | **唯一**写 `talents` 字段 + stage 推进路径；自然跳转免 `--force`，跨 stage 必须 `--force --reason "boss原话"` |
| `talent.cmd_delete` | `talent/cmd_delete.py` | **唯一**删档路径（自动归档 snapshot + emails 到 `data/deleted_archive/<YYYY-MM>/`；v3.5.9 同时把 `data/candidates/<tid>/` 整目录搬到 `deleted_archive/<YYYY-MM>/<tid>__dir_<ts>/` 并撤销 `by_name` 软链） |
| `talent.cmd_normalize_cv_filenames` | `talent/cmd_normalize_cv_filenames.py` | **v3.5.10 一次性维护**：剥掉 `talents.cv_path` 中飞书 Gateway 留下的 `doc_<hex>_` 前缀；同时移动文件并去重（同 size 副本删带前缀那份）。`--dry-run` 安全预览。日常 import_cv 已自动剥前缀，此 CLI 用于历史数据补救。|
| `talent.cmd_rebuild_aliases` | `talent/cmd_rebuild_aliases.py` | **v3.5.9** 全量重建 `data/candidates/by_name/<姓名>__<tid>/ → ../<tid>` 软链；幂等，含 `--dry-run` / `--json`；HR 浏览友好层，**所有代码 / DB / Agent 决策仍以 `t_xxx/` 为唯一规范路径** |
| `outbound.cmd_send` | `outbound/cmd_send.py` | **唯一**发候选人邮件路径：`--template T --vars k=v` 或 `--subject S --body-file F` 或 `--use-cached-draft EMAIL_ID`；自动写 `talent_emails(direction='outbound')` |
| `inbox.cmd_scan` | `inbox/cmd_scan.py` | **唯一**入站邮件抓取路径（IMAP → `talent_emails(direction='inbound', analyzed_at=NULL)`）；同时把候选人邮件附件按 `talent_emails.context` 分流落到 `data/candidates/<tid>/exam_answer/em_<eid>/`（context='exam'）或 `data/candidates/<tid>/email/em_<eid>/`（其他），元数据写 `talent_emails.attachments` JSONB（v3.5.8） |
| `inbox.cmd_analyze` | `inbox/cmd_analyze.py` | **唯一**入站邮件 LLM 分类（按 stage 选 prompt：`prompts/inbox_general` vs `prompts/post_offer_followup`），写 `ai_payload` + 推飞书卡 |
| `inbox.cmd_review` | `inbox/cmd_review.py` | 读：单候邮件时间线（含 AI intent / template / analyzed_at） |
| `feishu.cmd_calendar_create` / `_delete` | `feishu/cmd_*.py` | 创建 / 删除飞书日历事件；v3.5.7 `cmd_calendar_create` 新增 `--extra-attendee OPEN_ID`（可重复，把面试官加进 attendees）`--duration-minutes N`（默认 60；§5.11 一面用 30） |
| `feishu.cmd_notify` | `feishu/cmd_notify.py` | **唯一**飞书通知路径（替代旧 `ops/cmd_push_alert.py`）；`--severity {info,warn,error,critical} --to {boss,hr,interviewer-master,interviewer-bachelor,interviewer-cpp}`（v3.5.7 新增 3 个 interviewer-* 角色，open_id 来自 `lib.config['feishu']['interviewer_*_open_id']`） |
| `intake.cmd_route_interviewer` | `intake/cmd_route_interviewer.py` | **v3.5.7 §5.11 一面派单**：根据 `talents.{education,has_cpp}` 算出该派给哪个面试官，输出 `{interviewer_roles, interviewer_open_ids, ambiguous, config_error}`。**零副作用**纯查询；`ambiguous=true` / `config_error=true` 时 chain 必须 STOP 转 ASK_HR 分支 |
| `exam.cmd_exam_result` | `exam/cmd_exam_result.py` | 笔试结果 → stage 推进（含拒信发送） |
| `exam.cmd_exam_ai_review` | `exam/cmd_exam_ai_review.py` | LLM 笔试评审（advisory only，绝不输出 pass/fail token） |
| `interview.cmd_result` | `interview/cmd_result.py` | 一/二面结果 → stage 推进（含拒信） |
| `intake.cmd_ingest_cv` / `cmd_attach_cv` / `cmd_new_candidate` / `cmd_import_candidate` / `cmd_send_cv` | `intake/cmd_*.py` | CV intake 五件套（详见 §4.1） |
| `common.cmd_status` / `cmd_search` / `cmd_today_interviews` / `cmd_remove` / `cmd_interview_reminder` / `cmd_debug_candidate` | `common/cmd_*.py` | 旧式查询；首选 v3.5 `talent.cmd_show` / `cmd_list` |
| `template.cmd_preview` | `template/cmd_preview.py` | 模板预览（无副作用） |
| `ops.cmd_health_check` / `cmd_db_migrate` / `cmd_replay_notifications` | `ops/cmd_*.py` | 运维 |
| `auto_reject.cmd_scan_exam_timeout` | `auto_reject/cmd_scan_exam_timeout.py` | **cron 专用**——agent 不调（`--dry-run` 除外） |

#### 不再存在的脚本（agent 任何场景都不能提议它们）

`round1/cmd_round1_schedule.py`、`round2/`（整个目录）、`followup/`（整个目录，含 `cmd_followup_reply` / `cmd_followup_close` / `followup_scanner` / `followup_analyzer`）、`interview/cmd_reschedule.py`、`interview/cmd_defer.py`、`exam/daily_exam_review.py`、`exam/exam_ai_reviewer.py`、`common/cmd_finalize_interview_time.py`、`common/cmd_wait_return_resume.py`、`common/cmd_reschedule_request.py`、`ops/cmd_push_alert.py`。

如果在 prompt 历史里看到这些脚本被提及，那是 v3.4 之前的残留——**忽略并按本表的 atomic CLI 重新规划**。

#### Operational rules（v3.5）

- **`outbound.cmd_send` 是 SMTP 唯一出口**。Free-text 邮件流程：agent 起草 → boss verbatim 确认 → 写到 `/tmp/draft_xxx.txt` → `outbound/cmd_send.py --subject S --body-file /tmp/draft_xxx.txt --in-reply-to '<id>'`。脚本自动清临时文件（`--cleanup-body-file` 默认开）。
- **POST_OFFER_FOLLOWUP 一键发**：`outbound.cmd_send --talent-id <id> --use-cached-draft <email_id>`，从 `talent_emails.ai_payload.draft` 取 LLM 草稿（`inbox.cmd_analyze` 已经写好）。（v3.6 起 `OFFER_HANDOFF` 已合并入 `POST_OFFER_FOLLOWUP`。）
- **`talent.cmd_update` 是 `talents.current_stage` 唯一推进路径**。自然跳转免 flag；跨 stage 必须 `--force --reason "boss原话"`，不要默认加 `--force`。
- **`talent.cmd_delete` 是删人唯一路径**，自动归档；`--no-backup` 必须有 boss 明示。
- **入站邮件统一管线**：所有候选人入站邮件**只**经 `inbox.cmd_scan` → `inbox.cmd_analyze` 两步——不再有 `daily_exam_review` / `followup_scanner` 各自扫一遍。
- **飞书通知统一管线**：`feishu.cmd_notify` 是 agent 推飞书的唯一出口；不要 import `lib.feishu`。
- **chain 编排**：agent 串多步动作时用 `lib/run_chain.py` 的 `Step(...)`；前一步 `--json` 输出可作占位符（语法 `{step.field}`，如 `--set round1_invite_sent_at={send.sent_at}`）。
- **chain 失败模型**：任意一步失败 → 短路 + `feishu.cmd_notify --severity critical`，**不**自动 rollback（发邮件 / 删日历不可逆）。
- **失败 vs 输入错**：write 类脚本 crash → `lib/cli_wrapper.py` 自动飞书告警；`UserInputError`（缺 `--force`、talent_id 不存在、template 变量缺失）→ 只 stderr 不告警。
- **6 条钉死的 chain 范式**：详见 [AGENT_RULES.md §5](../skills/recruit-ops/docs/AGENT_RULES.md)（§5.1 一面排期 / §5.2 confirm+建日历 / §5.3 改期 / §5.4 暂缓 / §5.5 笔试转二面 / §5.6 一键发草稿 / §5.7 笔试拒保留 / §5.8 WAIT_RETURN 推老板）。每条都被 `tests/test_agent_chain.py` 端到端回归——agent 必须**照着抄**，参数名 / `--set` 字段 / 占位符传递都已固化。

### 1.4 Who talks to this skill

Two actors, two distinct message shapes:

| Actor | Typical message | Skill's job |
|---|---|---|
| **HR** | CV attachment (PDF/DOCX), `【新候选人】` / `【导入候选人】` text templates, status-fix requests ("笔试已手工发出"), candidate reschedule forwards | Identify, parse, dedup, produce a preview for HR to confirm, then execute the confirmed command |
| **Boss** | Natural-language queries and directives ("今天谁有面试", "张三一面改到明天下午三点", "候选人D一面被拒保留") | Resolve identity + time, propose the command, wait for confirmation, execute |

The skill does not track "who is speaking" explicitly — the message shape is enough to disambiguate. Both actors are trusted; the §2.2.1 confirmation protocol applies to both.

**Identities**: the deployment configures two Feishu identities via env var — `FEISHU_BOSS_OPEN_ID` (boss) and `FEISHU_HR_OPEN_ID` (HR). `intake/cmd_send_cv.py --to boss|hr` routes on these. The skill never reads or echoes these open_ids — it only uses the abstract `boss` / `hr` labels.

---

## 2. Safety Model

Every command falls into one of three safety classes. A command is classified by its **maximum possible side-effect**, not by the common case.

### 2.1 Read-only (safe to run speculatively)

Strictly no DB writes and no outbound email / Feishu / calendar calls under any flag combination.

- `common/cmd_status.py`
- `common/cmd_search.py`
- `common/cmd_today_interviews.py`

### 2.1.5 Auto-triggered preview (CV intake)

A narrow intermediate class: these commands are triggered automatically when the user (typically HR) drops a CV-shaped message, they **do not mutate state**, but they incur real cost (LLM parse, DB read, file I/O) and produce a structured preview that is itself a proposal for the next mutating step.

- `intake/cmd_ingest_cv.py` — the canonical example. See §4.1.1 for when/how to trigger.

Rules:
- **Run automatically** without §2.2.1 confirmation when the incoming message matches the CV-detection rules in §4.1.1. The command's own output is the "proposal" shown to HR.
- **Forward the output verbatim** to HR. Do not paraphrase field diffs or summarize them.
- The output carries one or more `[OC_CMD_ON_CONFIRM*]` payloads that are **proposals for §2.2 mutating commands** (`cmd_attach_cv.py` / `cmd_new_candidate.py` / `cmd_import_candidate.py`). Those downstream commands **do** require the full §2.2.1 confirmation protocol; HR's natural-language reply to the preview counts as that confirmation.
- Never invoke `cmd_ingest_cv.py` speculatively on non-CV messages, and never invoke it on the Boss side unless the Boss explicitly asks to ingest a file.

### 2.2 Mutating (changes DB state and/or sends external messages)

Any command in this class writes DB state, sends an outbound message, or both. **Every mutating command requires a pre-execution confirmation from the user — without exception.**

Commands in this class（v3.5 atomic CLI 列表，全部经 `lib/cli_wrapper.py` 包裹 + self-verify + 失败飞书告警）：

- **DB only**: `talent/cmd_add.py`, `talent/cmd_update.py`
- **DB + IMAP/SMTP/Feishu (agent chain step)**: `outbound/cmd_send.py`, `inbox/cmd_scan.py`, `inbox/cmd_analyze.py`, `feishu/cmd_calendar_create.py`, `feishu/cmd_calendar_delete.py`, `feishu/cmd_notify.py`
- **DB + external (CV intake)**: `intake/cmd_ingest_cv.py`, `intake/cmd_attach_cv.py` (requires `--confirm`), `intake/cmd_new_candidate.py`, `intake/cmd_import_candidate.py`
- **External only (sends Feishu / email)**: `intake/cmd_send_cv.py`, `common/cmd_interview_reminder.py`
- **DB + external (面试 / 笔试结果)**: `interview/cmd_result.py`, `exam/cmd_exam_result.py`
- **DB + external (destructive)**: `talent/cmd_delete.py`, `common/cmd_remove.py` — see §2.3

**多步 chain 的 confirm 语义**：当一个老板请求需要拼 chain（如 §5.1 安排一面 = `outbound.cmd_send` + `talent.cmd_update`），按 §2.2.1 propose **整条 chain**（所有 Step 列出来）；老板一次 confirm = 授权整条 chain（chain 是一个语义单元，不再每步单独 confirm）。但**跨场景**仍要分开 confirm：例如「安排一面」chain 跑完后，老板再说「再发简历给老板」是新指令，必须重新 propose。

### 2.2.1 Pre-execution confirmation protocol (mandatory)

Before running **any** command from §2.2, follow this loop:

1. **Resolve** a unique `talent-id` (via §3) and all required args, including `--time` in `YYYY-MM-DD HH:MM` Asia/Shanghai.
2. **Present** the fully-resolved command to the user, verbatim, in a code block. Include:
   - the exact `uv run python3 scripts/...` invocation with every arg filled in,
   - the candidate's name + `talent-id` + current stage (from a prior read-only query if needed),
   - a one-line description of what will happen (DB change, email sent, Feishu message, etc.).
3. **Wait** for an explicit user confirmation in the next turn. Acceptable affirmatives: `yes` / `ok` / `go` / `执行` / `确认` / `好` (plus paraphrases). Silence, clarifying questions, or any change of subject count as "not confirmed".
4. **Execute only after confirmation**, and only the command that was shown. If the user changes a parameter in their reply, re-present the updated command and go back to step 3.
5. **Never batch**. One confirmation authorizes one command. Multi-step flows (e.g. search → finalize → result) require a fresh confirmation at each mutating step.
6. **Chain-fallback rule (v3.5.4)**：如果老板的指令需要拼 chain，但你**找不到匹配的 §5.x 范式**，**绝不**自己拼一个新 chain 凑合上。正确做法是 **stop and ask**——在飞书里回报老板：「我没找到匹配的 chain，是否走 §5.9 force-jump 一步推到 stage X？或者您能澄清一下需要发什么邮件 / 走哪一轮？」。**禁止** (a) 拿 §5.x 里某个 chain "改改参数" 凑合 (b) 用多个 atomic CLI 试错式拼接 (c) 通过看 CLI 错误信息迭代修正参数。错的 chain 一旦执行，邮件 / 日历是不可逆的。

Exceptions: **none**. This protocol applies even for "obvious" cases like `cmd_result.py ... --result pass`, even inside long-running recruiting workflows, and even when the user's request already contained the full command.

**关键纠错（v3.5.4，由 2026-04-21 17:06 事故触发）**：当老板说「直接跳到 X」「直接进 X 阶段」「略过 / 跳过 / 强制」之类**带跨 stage 跳跃语义**的指令时，**唯一**正确路径是 [AGENT_RULES.md §5.9 force-jump 单步 chain](../skills/recruit-ops/docs/AGENT_RULES.md)（`talent.cmd_update --stage <target> --force --reason "boss原话: …"`），**不发邮件、不建日历、不更新业务字段**。识别规则见 [AGENT_RULES.md §3.3](../skills/recruit-ops/docs/AGENT_RULES.md)。**绝不**走「先按正常流程推到 X」的路径——那会真发候选人邮件，无法撤回。

### 2.3 Destructive (strict superset of §2.2 confirmation)

These commands permanently destroy data or move a candidate into a terminal rejection state. They require all of §2.2.1 **plus**:

- A generic "yes / ok / 好" is **not sufficient**. The confirmation must explicitly name the destructive action — e.g. `"是，删除 t_xxx"`, `"confirm reject_delete for t_xxx"`, `"yes, remove 张三 (t_xxx)"`.
- The confirmation must be in the same turn as the proposed command; never rely on prior-turn intent.
- If the user's reply is affirmative but does not name the destructive action, re-ask.

Commands in this class:

- `talent/cmd_delete.py` — v3.5 唯一物理删档路径；自动归档完整 snapshot + emails。
- `common/cmd_remove.py` — 历史删档命令；与 `talent/cmd_delete.py` 等价，agent 优先用后者。
- `interview/cmd_result.py ... --result reject_delete` — rejection with removal from talent pool. **Side effect (since 2026-04-22)**: 自动先发 `rejection_generic.txt` 拒信再删人。`--skip-email` 仅在 boss 已线下手发拒信时使用。
- `exam/cmd_exam_result.py ... --result reject_delete` — same, exam branch.
- `intake/cmd_attach_cv.py` — requires `--confirm` on the CLI AND a same-turn natural-language confirmation of the match.

**Rule (single-valued)**: without an explicit delete instruction from the user, the only permissible rejection is `reject_keep` (or in round 1's case, leaving the candidate in the current stage). `reject_delete` is never chosen by default.

### 2.4 Auto-rejection (system-driven, exam-timeout only)

> **2026-04-23 simplification**: the previous "soft-automated 12h buffer + boss-cancellable queue" architecture has been removed. The agent should **not** invoke any auto_reject command itself — there is now exactly one cron-driven script and no boss-facing commands.

Single trigger, immediate action:

- `auto_reject.cmd_scan_exam_timeout` (cron task 5) runs every cron tick. For each candidate in `EXAM_SENT` with `exam_sent_at` ≥ `--threshold-days` (default 3) and no inbound email after `exam_sent_at`, it immediately:
  1. subprocess-calls `outbound.cmd_send --template rejection_exam_no_reply` to send the rejection email,
  2. subprocess-calls `talent.cmd_delete` to remove the candidate, and
  3. pushes a Feishu **after-the-fact** notification card ("[自动拒删 · 已执行]") to boss.

If step 1 fails the candidate is NOT deleted; failure count is reported and a Feishu alert fires. Late-reschedule auto-rejection is fully removed: every reschedule intent now flows through §4.2 / §5.3 chain（`feishu.cmd_calendar_delete` → `outbound.cmd_send --template reschedule` → `talent.cmd_update`），boss 决策。

Removed (do not propose any of these — they no longer exist):
- `auto_reject/cmd_propose.py`, `auto_reject/cmd_cancel.py`, `auto_reject/cmd_execute_due.py`, `auto_reject/cmd_list.py`
- `auto_reject/pending_store.py`, `auto_reject/llm_classify.py`
- `data/auto_reject_pending/`, `data/auto_reject_archive/`
- `talents.pending_rejection_id` column (dropped via `lib/migrations/20260423_drop_pending_rejection_id.sql`)
- "legitimate reschedule whitelist" / 12h buffer / boss-cancellable queue

If boss wants to manually reject + delete a candidate, use the §2.3 destructive class command `interview/cmd_result.py ... --result reject_delete` (which now also sends `rejection_generic.txt` before deleting; pass `--skip-email` only if the rejection email was already sent manually).

---

## 3. Ambiguity Resolution Rules

Most production errors come from acting on an under-specified request. Apply these rules **before** choosing a mutating command.

| Missing / ambiguous | Required resolution |
|---|---|
| Identity (name only, no `talent-id`) | Run `common/cmd_search.py --query <name>`, then use the returned `talent-id`. |
| Multiple search hits | Show the matches to the user and ask which one. Never pick by alphabetical order, recency, or vibes. |
| Referential phrases (`他`, `她`, `上周那个候选人`, `那个女生`) | Only accept if a unique candidate was already established earlier in the same turn. Otherwise, search. |
| Round unspecified for interview ops | Ask. Do not infer from stage alone unless the stage uniquely determines the round (`ROUND1_*` → 1, `ROUND2_*` → 2). |
| Time given as natural language (`明天下午三点`) | Resolve to an explicit `YYYY-MM-DD HH:MM` in **Asia/Shanghai (+08:00)** — the hardcoded server timezone used by `scripts/lib/core_state.py` when it stamps times. Echo the resolved time back to the user in the reply. |
| `--result` unspecified (pass / reject_keep / reject_delete / pass_direct) | Ask. Do not default. |
| Reject without a keep/delete hint | See §2.3: `reject_delete` requires explicit same-turn user confirmation; otherwise use `reject_keep`. |

**Hard rule**: Any mutating command must (a) target a **unique** `talent-id` and (b) pass the confirmation protocol in §2.2.1 before execution. If the input doesn't already provide a unique `talent-id`, resolve identity first — then present the resolved command and wait.

---

## 4. Intent Routing

One unified table. Groups (`intake/`, `round1/`, `interview/`, `exam/`, `common/`) are directories under `scripts/`.

### 4.1 Candidate intake

| Intent | Command |
|---|---|
| `【新候选人】` text template from HR | `uv run python3 scripts/intake/cmd_new_candidate.py --template "<raw multi-line message>"` |
| `【导入候选人】` historical candidate | `uv run python3 scripts/intake/cmd_import_candidate.py --template "<raw multi-line message>"` |
| CV attachment (PDF/DOCX) — **auto-triggered**, see §4.1.1 | `uv run python3 scripts/intake/cmd_ingest_cv.py --file-path <path> --filename <filename>` |
| Attach CV to an existing candidate (after `cmd_ingest_cv` preview) | `uv run python3 scripts/intake/cmd_attach_cv.py --talent-id <id> --cv-path <path> --confirm [--field key=value ...]` |
| Send CV PDF to Boss (default) or HR | `uv run python3 scripts/intake/cmd_send_cv.py --name "<name>" [--to boss\|hr]` *(default: `boss`; `--to hr` sends to HR)* |

Notes:
- For multi-line `--template`, pass a real newline-bearing string. In bash, use heredoc: `--template "$(cat <<'EOF' ... EOF)"`, or `$'line1\nline2'`. Do **not** pass a double-quoted `"\n"` literal — bash will not expand it.
- `cmd_parse_cv.py` is **deprecated**. Do not use it.
- When Boss says "看简历 / 把某某的简历发过来" — use `cmd_send_cv.py` **without** `--to` (default is boss). Only pass `--to hr` when explicitly asked to send to HR.

### 4.1.1 CV auto-detection & routing

When an inbound message in the Feishu group contains a file attachment, decide whether it is a candidate CV **before** deciding what to run.

**Step 1 — Is this a CV?** Treat the file as a candidate CV if **any** of the following holds:

- The filename matches the CV shape: job title / city / salary + candidate name + `XX年应届生` or `实习生` (e.g. `量化研究员实习-上海-500元_天-张三-2026年应届生.pdf`).
- The file body (after opening) contains multiple of: job title, candidate name, `应届生` / `实习生`, an email address, a phone number, school / education.
- The message context clearly frames the file as a resume (HR says "这是简历" / "新候选人" / "请入库" alongside the attachment).

If none of the above holds, do **not** route to `cmd_ingest_cv.py`. Ask HR to clarify, or fall through to generic file handling.

**Step 2 — Extract the file path from the Hermes Gateway message** (priority-ordered):

| Priority | Inbound message shape | Args to pass |
|---|---|---|
| 0 | `[The user sent a document: 'xxx.pdf'. The file is saved at: /.../xxx.pdf ...]` | `--file-path "/.../xxx.pdf" --filename "xxx.pdf"` |
| 1 | `[media attached: <absolute-path>.pdf]` | `--file-path "<absolute-path>.pdf" --filename "<basename>"` |
| 2 | Reply / quote referring to a message with a `file_key` | First look in `<workspace_root>/data/media/inbound/` for a local file by key/name; if found use `--file-path`; otherwise `--file-key <key>` |

The path produced by the Hermes message is already absolute. Pass it through **verbatim**; do not rewrite with `<workspace_root>`.

**Step 3 — Run `cmd_ingest_cv.py`** (auto-triggered, see §2.1.5). Forward its output to HR verbatim, then wait for HR's reply before any further action (§9.3 describes the two possible output shapes).

### 4.2 Interview operations

| Intent | 路由 |
|---|---|
| 老板拍了一个一面时间（agent 安排） | **chain（[AGENT_RULES.md §5.1](../skills/recruit-ops/docs/AGENT_RULES.md)）**：`outbound.cmd_send --template round1_invite --vars round1_time=… position_suffix=… location=…` → `talent.cmd_update --stage ROUND1_SCHEDULING --set round1_time=… --set round1_invite_sent_at={send.sent_at} --set round1_confirm_status=PENDING --set round1_calendar_event_id=__NULL__ --set wait_return_round=__NULL__ --reason "agent: schedule round 1"`。propose 时把整条 chain 全列出来等 boss confirm。 |
| **HR** 说「t_xxx 一面时间是 …」/「安排 X 一面，时间 …」（HR 触发，agent 自动派单） | **chain（[AGENT_RULES.md §5.11](../skills/recruit-ops/docs/AGENT_RULES.md)，v3.5.7）**：(1) `intake.cmd_route_interviewer --talent-id <id> --json`（先派单，**绝不**自己算 open_id；ambiguous=true 或 config_error=true 必须 STOP 转 ASK_HR）→ (2) `outbound.cmd_send --template round1_invite --vars round1_time=… --json` → (3) `feishu.cmd_calendar_create --talent-id <id> --time "…" --round 1 --duration-minutes 30 --candidate-name … --candidate-email … --extra-attendee {route.interviewer_open_ids[*]} --json` → (4) `talent.cmd_update --stage ROUND1_SCHEDULED --set round1_time=… --set round1_invite_sent_at={send.sent_at} --set round1_confirm_status=CONFIRMED --set round1_calendar_event_id={cal.event_id}` → (5) `feishu.cmd_notify --to interviewer-{role} …` ×N → (6) `feishu.cmd_notify --to boss --severity info --title "一面已排"`。**与 §5.1 的关键差异**：HR 触发**直接进 `ROUND1_SCHEDULED`**（不是 `_SCHEDULING`），日历直接建（含面试官），时长固定 30 分钟。 |
| HR 说「t_xxx 派给 master/bachelor/cpp」（§5.11 ambiguous 后 HR 手动指派回话） | 重启 §5.11 chain，跳过 step 1 的派单自动决策，把 HR 指定的 role 对应的 `open_id` （从 `lib.config['feishu']['interviewer_<role>_open_id']`）当作 `route.interviewer_open_ids` 喂给 step 2 起步的 chain。**仍然不允许** agent 写 hardcode 的 `ou_xxx` 字符串。 |
| 候选人回信 confirm 一面/二面时间 | **chain（§5.2）**：`talent.cmd_update --stage ROUND{N}_SCHEDULED --set round{N}_confirm_status=CONFIRMED` → `feishu.cmd_calendar_create --talent-id <id> --round N --time … --candidate-email … --candidate-name …` → `talent.cmd_update --set round{N}_calendar_event_id={cal.event_id}` |
| 候选人 / 老板请求改期 | **chain（§5.3）**：`feishu.cmd_calendar_delete --event-id <round{N}_calendar_event_id> --reason "候选人改期"` → `outbound.cmd_send --template reschedule --vars round_label=… old_time=… new_time=… location=…` → `talent.cmd_update --stage ROUND{N}_SCHEDULING --set round{N}_time=新时间 --set round{N}_confirm_status=PENDING --set round{N}_calendar_event_id=__NULL__ --set round{N}_invite_sent_at={send.sent_at}`。**顺序不可换**——先删旧日历再发新邮件。 |
| 候选人在国外暂缓 | **chain（§5.4）**：（如已建日历，先 `feishu.cmd_calendar_delete`）→ `outbound.cmd_send --template defer --vars round_label=…` → `talent.cmd_update --stage WAIT_RETURN --set wait_return_round=N --set round{N}_time=__NULL__ --set round{N}_calendar_event_id=__NULL__` |
| WAIT_RETURN 候选人主动联系 | **chain（§5.8）**：纯通知 `feishu.cmd_notify --severity warn --title "WAIT_RETURN 候选人主动联系" --body "talent={tid} round={round}\nintent=… summary=…\n建议下一步：①talent.cmd_update --stage ROUND{N}_SCHEDULING --reason 'candidate returned'（natural transition，免 --force）②outbound.cmd_send --template round{N}_invite --vars round{N}_time=… location=…"`。**不自动改 stage** —— 候选人是否真能约由老板判。 |
| 记录面试结果（一/二面统一） | `uv run python3 scripts/interview/cmd_result.py --talent-id <id> --round 1\|2 --result pass\|pass_direct\|reject_keep\|reject_delete [--email …] [--round2-time "…"]`。⚠ **真发候选人邮件**（`--round 1 --result pass` 发笔试邀请；`--round 1 --result pass_direct` 发二面邀请；`--result reject_*` 发拒信）。**仅在老板真的走完了那一轮面试**时使用——若老板说「跳过 / 直接进 X 阶段」，**绝不**用本命令，改用下面 §5.9 force-jump。 |
| 一面已通过、笔试邮件已手工发出（仅状态推进） | `uv run python3 scripts/interview/cmd_result.py --talent-id <id> --round 1 --result pass --skip-email` |
| 老板说「直接跳到 X / 略过中间步骤 / 直接进 offer / 强制推到 Y」（跨 stage 跳跃） | **唯一路径：§5.9 force-jump 单步**（[AGENT_RULES.md §5.9](../skills/recruit-ops/docs/AGENT_RULES.md)）：`talent.cmd_update --talent-id <id> --stage <target> --force --reason "boss原话: <原话>"`。**绝不**调 `cmd_result --result pass` 或 `cmd_exam_result --result pass`（会真发邮件给候选人）。识别规则见 [AGENT_RULES.md §3.3](../skills/recruit-ops/docs/AGENT_RULES.md)。 |
| 预览候选人邮件模板 | `uv run python3 -m template.cmd_preview --template <name> --demo` *(or `--var key=value …`; `--list` 列全部模板：`round1_invite`, `exam_invite`, `round2_invite`, `reschedule_ack`, `reschedule`, `defer`, `rejection_generic`, `rejection_exam_no_reply`)* |

**Email templates**：6 个候选人模板（`round1_invite` / `exam_invite` / `round2_invite` / `reschedule_ack` / `reschedule` / `defer`）+ 2 个拒信（`rejection_generic` / `rejection_exam_no_reply`）渲染源在 `scripts/email_templates/*.txt`。**v3.5 起所有 chain 通过 `outbound.cmd_send --template <name> --vars k=v …` 调用**，不再有任何脚本里的 `_send_xxx_email()` thin wrapper（这些 wrapper 在 v3.5 全部删除，连同它们所属的 `cmd_round1_schedule` / `cmd_reschedule` / `cmd_defer` / `cmd_reschedule_request` 脚本一起）。改文案直接编辑 .txt；变量缺失会 `KeyError`（fail-fast，防 2026-04-20 那次「字面量 `$candidate_name` 漏发」事故再现）。`round1_invite` 模板里的 3 轮流程概述（一面线下 / 二面笔试 / 三面线下）+ 实习要求（≥3 个月、每周 ≥4 天）放在排期细节**前面**是有意为之，让候选人在双方投入时间前自筛。Round 数字翻译（`round_num=1→"第一轮"`，`round_num=2→"第三轮"`）在 `email_templates/constants.py::round_label()`，**不要**在 caller 里 inline `"第一轮" if round==1 else "第二轮"`。

Conditional-required args (from [CLI_REFERENCE.md](../skills/recruit-ops/docs/CLI_REFERENCE.md)):
- `cmd_result.py --round 1 --result pass` → `--email` is **required** and must be **the candidate's email address** (用作 exam invite 的 SMTP 收件人；同时覆盖 `talents.candidate_email`)。It is NOT the email body. The value MUST match the regex `^[^\s@]+@[^\s@]+\.[^\s@]+$`. 找不到合法邮箱时**不要**编造或填占位字符——停下来问。`--skip-email` 仅在 boss 已线下手发笔试邮件时使用。
- `cmd_result.py --round 1 --result pass_direct` → `--round2-time` is **required**。
- `cmd_result.py --round 2 --result pass` → 无额外参数；**一步**推到 `POST_OFFER_FOLLOWUP` + 同步通知 HR 飞书（v3.6 起 `OFFER_HANDOFF` 这个瞬时态已合并下线）。
- `--skip-email` 只对 `--round 1 --result pass` 有效；语义是「operator 已线下发笔试邮件，只推进 state」。

### 4.3 Exam operations

| Intent | 路由 |
|---|---|
| 笔试通过 → 安排二面（CLI 一步打包） | `uv run python3 scripts/exam/cmd_exam_result.py --talent-id <id> --result pass --round2-time "YYYY-MM-DD HH:MM"`。⚠ **真发 round2_invite 邮件给候选人**——**仅在老板**明确给出二面时间且要求安排二面**时用。若老板说「直接进 offer」「不要二面，直接发 offer」，**绝不**用本命令（也绝不为了过 `--round2-time` 必填校验而编一个时间），改走 §5.9 force-jump。 |
| 笔试通过 → 安排二面（agent chain，与上等价的手工路径） | **chain（[AGENT_RULES.md §5.5](../skills/recruit-ops/docs/AGENT_RULES.md)）**：`outbound.cmd_send --template round2_invite --vars round2_time=… location=…` → `talent.cmd_update --stage ROUND2_SCHEDULING --set round2_time=… --set round2_invite_sent_at={send.sent_at} --set round2_confirm_status=PENDING --set round2_calendar_event_id=__NULL__ --set wait_return_round=__NULL__`。**两条路径不要同一封邮件叠加触发**。同样**仅当老板要安排二面时**才用——跨 stage 跳跃走 §5.9。 |
| 老板说「笔试通过，直接进 offer」/「跳过二面」/「不需要二面，直接结束流程」 | **唯一路径：§5.9 force-jump 单步**（[AGENT_RULES.md §5.9](../skills/recruit-ops/docs/AGENT_RULES.md)）：`talent.cmd_update --talent-id <id> --stage POST_OFFER_FOLLOWUP --force --reason "boss原话: …"`。**绝不**调 `exam.cmd_exam_result --result pass`（会真发二面邀请），**绝不**为了过 stage-gate 而拼「先安排二面 → 假装 confirm → 二面 pass」的伪 chain（这是 2026-04-21 17:06 真实事故）。 |
| 笔试不过（保留人才池）— CLI 一步 | `uv run python3 scripts/exam/cmd_exam_result.py --talent-id <id> --result reject_keep` |
| 笔试不过（保留池）— agent chain | **chain（§5.7）**：`outbound.cmd_send --template rejection_generic` → `talent.cmd_update --stage EXAM_REJECT_KEEP --reason "agent: exam reject keep (per boss decision)"` |
| 笔试拒 + 删档 | `uv run python3 scripts/exam/cmd_exam_result.py --talent-id <id> --result reject_delete` *(§2.3 destructive；自动先发 `rejection_generic` 再删人)* |
| AI 笔试评审（advisory only） | `uv run python3 scripts/exam/cmd_exam_ai_review.py --talent-id <id> [--feishu --save-event] [--rerun]` *(自动从 IMAP 拉最新提交并缓存 `/tmp/exam_submissions/<id>/`)* |
| Boss 说「审阅/评审/看一下 X 的笔试」 | resolve unique `talent-id`，**两步 propose**：(1) `cmd_exam_ai_review.py --talent-id <id>`（无 `--feishu`/`--save-event`，纯终端预览）→ wait for confirm → 执行 (2) Boss 看完报告后，**再** propose 同一条加 `--feishu --save-event` 推飞书 + 写 `talent_events.action='exam_ai_review'`。CLI 自动从 IMAP 拉最新提交，无需先跑别的脚本。 |
| 候选人邮件时间线（inbound + outbound） | `uv run python3 -m inbox.cmd_review --talent-id <id>` *(`talent_emails` 是 single source of truth；显示 AI intent / template / analyzed_at)* |
| Boss 在 POST_OFFER_FOLLOWUP 飞书卡片上点「一键发」 | **chain（§5.6）**：`outbound.cmd_send --talent-id <id> --use-cached-draft <email_id>` → `feishu.cmd_notify --severity info --title "已发送 Offer 跟进回复"`。draft 不存在时第一步必失败（rc=2，stderr `没有 draft 字段`），整条 chain 短路 —— agent 应改推 `--severity warn` 「草稿缺失」卡。 |
| 让候选人 follow-up 邮件 snooze / dismiss | 直接 `talent_db.mark_email_status(email_id, status='dismissed'\|'snoozed', snoozed_until=...)` 改 `talent_emails.status`（无独立 CLI——这是 v3.5 简化的部分）。 |
| Boss 说 "给 X 发 onboarding offer / 录用通知 / 入职邮件" | **chain（[AGENT_RULES.md §5.10](../skills/recruit-ops/docs/AGENT_RULES.md) + §10）**：`outbound.cmd_send --template onboarding_offer --vars position_title=… interview_feedback=… daily_rate=… onboard_date=… location=… evaluation_criteria=…` → `feishu.cmd_notify --to hr --severity info --title "新候选人 offer 已发，请准备入职" --body "candidate=… 入职日期=… 薪资=… 已附：实习协议+登记表"`。**v3.5.10 起两份附件（《示例科技实习协议》+《实习生入职信息登记表》）由 `email_templates.auto_attachments` 自动追加，agent 不要再手动 `--attach`**；文件缺失会 fail-fast 拒发。**HR 走飞书不在邮件 cc 里**。`onboard_date` / `daily_rate` 老板没明说时**先 stop and ask**，不要默认 350 自作主张。 |
| 笔试 timeout 自动拒删（**不用 agent 介入**） | cron 自动跑 `auto_reject.cmd_scan_exam_timeout`：发 `rejection_exam_no_reply` → `talent.cmd_delete` → 推飞书事后告知。Agent 仅 `--dry-run` 预览：`uv run python3 -m auto_reject.cmd_scan_exam_timeout --dry-run` |

**入站邮件统一管线**（v3.5 起）：
- `inbox.cmd_scan` 一次扫所有候选人，写入 `talent_emails(direction='inbound', analyzed_at=NULL)`
- `inbox.cmd_analyze` 按 stage 选 prompt（`prompts/inbox_general.json` vs `prompts/post_offer_followup.json`），写 `ai_payload` 并推飞书卡
- 不再有 `daily_exam_review.py` / `followup_scanner.py` 各自扫一遍——也就**没有** `data/followup_pending/` 这个文件队列了，消息状态全在 `talent_emails.status` 里

**POST_OFFER_FOLLOWUP 简化**（v3.5 → v3.6）：
- v3.5：候选人通过二面 → `interview/cmd_result.py --round 2 --result pass` 通过 1-tick transient `OFFER_HANDOFF` + `set_current_stage()` 推到 `POST_OFFER_FOLLOWUP`。**不再调** `enter_post_offer_followup` 函数；`followup_status` / `followup_entered_at` / `followup_last_email_id` / `followup_snoozed_until` 字段已 DROP（v3.5.2 migration `20260421_v35_drop_dead_columns.sql`）。
- v3.6：`OFFER_HANDOFF` 这个瞬时 stage 已彻底删除——`interview/cmd_result.py --round 2 --result pass` 现在一步（`ensure_stage_transition` allowed_from={ROUND2_SCHEDULED} → POST_OFFER_FOLLOWUP）推到最终态，HR Feishu 通知照旧发。见 migration `20260427_v36_drop_offer_handoff.sql`。
- 候选人后续来信由 `inbox.cmd_scan` + `inbox.cmd_analyze` 处理，AI 草稿写在 `talent_emails.ai_payload.draft`。
- Boss 在飞书卡片上一键发 → §5.6 chain。
- 关闭/snooze/dismiss 不再有独立 CLI：直接 `talent_db.mark_email_status(...)` 改 `talent_emails.status`。

**Notes**:
- `--round2-time` is **mandatory** when `cmd_exam_result.py --result pass`. The script rejects reusing old times.
- `cmd_scan_exam_timeout` cron-only：agent 不要在响应单封邮件时跑（会和 cron 撞车，双发拒信）。
- **AI exam review (rubric-based) is advisory only**. The reviewer:
  - reads `skills/recruit-ops/exam_files/rubric.json` 输出结构化 score + reasons + next-step；
  - 通过 `lib/exam_grader.py` 与 `lib/exam_imap.py` 实现（v3.5 起替代旧 `exam/exam_ai_reviewer.py`）；
  - 自动从 IMAP 拉最新提交并缓存 `/tmp/exam_submissions/<id>/`，自动填 `submitted_at` from email `Date` header；
  - **缓存 LLM verdict** 在 `/tmp/exam_submissions/<id>/_ai_review_result.json`——推荐的两步流程（先终端预览，再 `--feishu --save-event`）只付一次 LLM 钱。`--rerun` 强制重跑（候选人重交时）。`--refetch` 强制重拉 IMAP；`--code-dir <path> --no-fetch` 用本地目录跳 IMAP；
  - **永远不**推进 stage，**永远不**输出 `pass / fail / 录取 / 拒绝 / 淘汰 / 建议通过 / 建议拒绝`——这些 token 被 `lib/exam_grader.py` 后处理 scrub 掉；
  - 结果写 `talent_events` action `="exam_ai_review"`（手动跑 actor=`manual_review`）。
- When the boss reads an AI review report, **the agent must not paraphrase the AI score into a pass/fail recommendation**. Always defer the decision back to the boss; next step is `cmd_exam_result.py` 或 §5.5/§5.7 chain。

### 4.4 Auto-rejection (system-driven, exam-timeout only)

| Intent | Command |
|---|---|
| "看看有没有谁会被自动拒" / "auto_reject 会拒谁" | read-only: `uv run python3 -m auto_reject.cmd_scan_exam_timeout --dry-run` |
| "调一下自动拒的天数阈值" (e.g. 改成 5 天) | read-only preview: `uv run python3 -m auto_reject.cmd_scan_exam_timeout --dry-run --threshold-days 5` (生产阈值在 cron 任务参数里改) |
| "为什么 X 被自动拒了" | read-only: open `inbox/cmd_review --talent-id <id>` to see his email timeline (no inbound after `exam_sent_at` ≥ 3d) + `talent/cmd_show --talent-id <id>` to see audit history including the deletion archive at `data/deleted_archive/<YYYY-MM>/`. |
| "把 X 手动拒了，删人" | §2.3 destructive class — propose `uv run python3 scripts/interview/cmd_result.py --talent-id <id> --round <N> --result reject_delete` (sends `rejection_generic.txt` before deletion; pass `--skip-email` only if rejection email was already sent manually). Do **not** invent a `cmd_propose --reason manual` — that command no longer exists. |

Notes:
- The agent **never invokes** `auto_reject.cmd_scan_exam_timeout` outside `--dry-run`. The cron runner is the only authoritative caller; manual real runs would race with cron and double-send rejection emails.
- There is **no boss cancellation window** anymore — once cron runs the script, candidates whose criteria match are deleted in the same tick. If boss wants to prevent an automatic rejection, the operational lever is to pause cron task 5 (comment out the `exam_timeout_scan` entry in `cron/cron_runner.py::_TASKS`) before the next cron tick.
- Late reschedules **never** trigger auto-reject. Reschedule emails 通过 §5.3 chain 流转：`inbox.cmd_analyze` 分类出 `reschedule_request` → `feishu.cmd_notify` 推老板 → 老板拍新时间 → §5.3 chain（`feishu.cmd_calendar_delete` + `outbound.cmd_send --template reschedule` + `talent.cmd_update`）执行。

### 4.5 Queries

| Intent | Command |
|---|---|
| All candidates | `uv run python3 scripts/common/cmd_status.py --all` |
| One candidate's full detail | `uv run python3 scripts/common/cmd_status.py --talent-id <id>` |
| Keyword search | `uv run python3 scripts/common/cmd_search.py --query <keyword>` |
| Active / in-progress candidates only | `uv run python3 scripts/common/cmd_search.py --all-active` |
| Today's interviews | `uv run python3 scripts/common/cmd_today_interviews.py` |
| Specific day | `uv run python3 scripts/common/cmd_today_interviews.py --date YYYY-MM-DD` |
| Confirmed-only schedule | `uv run python3 scripts/common/cmd_today_interviews.py --confirmed-only` |
| Pending interview-result reminders | `uv run python3 scripts/common/cmd_interview_reminder.py` |
| 看 X 的简历 / 笔试附件 / 候选人发的文件（v3.5.8） | (a) **CV** 直接看 `talents.cv_path`（绝对路径，`data/candidates/<tid>/cv/<原文件名>`）。(b) **邮件附件 / 笔试答案**：`psql` 查 `talent_emails.attachments WHERE talent_id=<id>`，得到 `path`（相对 `data_root()`），完整路径 = `data/<path>`，常见落点 `data/candidates/<tid>/exam_answer/em_<eid>/...` 或 `data/candidates/<tid>/email/em_<eid>/...`。(c) `exam.fetch_exam_submission` 的**手动下载缓存**（含解压 + AI 评审 JSON）位于 `data/candidates/<tid>/exam_answer/legacy_fetch/`。**不要**主动再跑 `exam.fetch_exam_submission`——`inbox.cmd_scan` 已自动落盘。 |

---

## 5. Query Rules

Match the user's intent to the narrowest canonical command. Do not compose a derived view from a broader command.

| User intent | Command | Do not |
|---|---|---|
| "Show me everyone" / "所有候选人" | `cmd_status.py --all` | Regroup into custom buckets. Change counts. Infer active/inactive from stage text. |
| "Who's still in progress?" / "活跃候选人" | `cmd_search.py --all-active` | Run `cmd_status.py --all` and manually guess which stages are active. |
| "When is X's interview?" | `cmd_search.py --query <name>` → if unique, `cmd_status.py --talent-id <id>` | Skip the search step and guess the ID. |
| "Any interviews today/tomorrow/on <date>?" | `cmd_today_interviews.py [--date ...]` | Reconstruct from `--all-active`. |

**Grouping guardrail for `cmd_status.py --all`**:

- Default behavior: return the flat list exactly as the command returned it.
- If the user explicitly asks for a grouped summary, group only by the **exact current stage label**, not by a hand-invented broader bucket.
- Never place any `*_DONE_*` stage under an in-progress bucket such as `一面阶段`, `二面阶段`, or `笔试阶段`.
- Concretely: `ROUND2_DONE_REJECT_KEEP / 二面未通过（保留）` is **not** part of `二面阶段`; `EXAM_REJECT_KEEP / 笔试未通过（保留）` is **not** part of `笔试阶段` (it is a terminal state for keeping in talent pool).
- If unsure whether a stage is in-progress or terminal, quote the exact stage label and stop. Do not improvise a bucket.

---

## 6. Stage Interpretation

Canonical source: `scripts/lib/core_state.py` (`STAGE_LABELS`). Mirror below for quick reference.

### 6.1 Active stages

| Stage | Label | Meaning |
|---|---|---|
| `NEW` | 新建 | Candidate created, no action taken yet |
| `ROUND1_SCHEDULING` | 一面排期中 | Round 1 invite sent, awaiting candidate confirmation |
| `ROUND1_SCHEDULED` | 一面已安排 | Round 1 confirmed |
| `EXAM_SENT` | 笔试已发送 | Exam sent, awaiting submission |
| `EXAM_REVIEWED` | 笔试已审阅 | Exam reviewed, awaiting next step |
| `WAIT_RETURN` | 待回国后再约 | Paused until candidate is back |
| `ROUND2_SCHEDULING` | 二面排期中 | Round 2 being coordinated |
| `ROUND2_SCHEDULED` | 二面已确认 | Round 2 confirmed |
| `POST_OFFER_FOLLOWUP` | 已结束面试流程，等待发放 Offer / 沟通入职 | v3.6 合并了原 `OFFER_HANDOFF` 的语义：`cmd_result.py --result pass --round 2` 一步把 stage 推到此处 + 通知 HR 发 offer。Boss 通过 Hermes 与候选人沟通 offer / 入职日 / 薪资。`inbox.cmd_scan` + `inbox.cmd_analyze` 自动抓邮件并按 `prompts/post_offer_followup.json` 生成草稿（写 `talent_emails.ai_payload.draft`），boss 在飞书卡片上一键发触发 §5.6 chain（`outbound.cmd_send --use-cached-draft …`）。**v3.5 起 `followup_*` 字段已 DROP**——此 stage 没有任何 followup 状态机，只看 `talent_emails.status`。 |

### 6.2 Terminal / done stages — **not in-progress**

These are finished states. Never present them as "still in that stage".

| Stage | Label | What it means |
|---|---|---|
| `EXAM_REJECT_KEEP` | 笔试未通过（保留） | Exam failed but candidate **kept** in pool for future re-activation |
| `ROUND2_DONE_REJECT_KEEP` | 二面未通过（保留） | Round 2 failed, kept in pool |

> Removed stages (do not invent these): `ROUND1_DONE_PASS`, `ROUND2_DONE_PASS`, `ROUND2_DONE_PENDING`, `ROUND1_DONE_REJECT_KEEP`, `OFFER_HANDOFF`, `ROUND1_DONE_REJECT_DELETE`, `ROUND2_DONE_REJECT_DELETE`.
> - Pass (round 1/2) = direct jump to next phase, no intermediate done state.
> - Round 2 "pending" = stays in `ROUND2_SCHEDULED` until boss decides.
> - `OFFER_HANDOFF` (v3.6)：merged into `POST_OFFER_FOLLOWUP`；`cmd_result --round 2 --result pass` goes straight there + HR Feishu.
> - `*_DONE_REJECT_DELETE` (v3.6)：`--result reject_delete` is **physical delete**（发拒信 + `talent_db.delete_talent()`）—— candidate is **gone**, no residual stage.

**Rule**: a candidate in `ROUND2_DONE_REJECT_KEEP` is **not** "still in round 2". They are finished and retained in the talent pool.

### 6.3 `pending_rejection_id` is gone (2026-04-23)

The `talents.pending_rejection_id` column and the entire 12h auto-reject buffer have been removed (see §2.4 / §4.4). There is no longer any "queued for auto rejection" intermediate state — `auto_reject.cmd_scan_exam_timeout` either deletes the candidate the same cron tick (success) or leaves the candidate in `EXAM_SENT` and alerts boss (failure). Do not reference `pending_rejection_id` or "缓冲窗口" / "12h 自动拒" / `cmd_cancel` / `cmd_list` / `cmd_propose` in any reply — those scripts and that field no longer exist.

---

## 7. Mutation Preconditions

Before running any command in §2.2 or §2.3, verify:

1. A **unique** `talent-id` has been resolved (via §3).
2. Required conditional args are present (see §4.2 notes, §4.3 notes).
3. Times are in `YYYY-MM-DD HH:MM`, Asia/Shanghai (+08:00), not in the past, and not a rehash of a previously-rejected time.
4. The candidate's current stage allows the operation.
5. **The §2.2.1 confirmation protocol has been completed** — the resolved command was shown to the user in a prior turn and the user replied with an explicit affirmative (for §2.3 commands, the affirmative must also name the destructive action).

### 7.1 Stage-gate policy — code is canonical

The CLI itself enforces stage-transition rules. The skill does **not** reimplement the state machine. The rough guide below is a **hint set**, not an authoritative specification:

| Operation | Typical allowed stages (code wins) |
|---|---|
| Round 1 schedule | `NEW`, `ROUND1_SCHEDULING` |
| Round 1 result | `ROUND1_SCHEDULING`, `ROUND1_SCHEDULED` |
| Exam result | `EXAM_SENT`, `EXAM_REVIEWED` |
| Round 2 schedule / finalize | `EXAM_REVIEWED`, or after `pass_direct` from round 1 |
| Round 2 result | `ROUND2_SCHEDULING`, `ROUND2_SCHEDULED` |

Rules:
- When unsure whether the current stage allows the action, run `common/cmd_status.py --talent-id <id>` first and let the CLI's own validator reject invalid transitions.
- If code and this table disagree, code is canonical. Update the skill, not the other way around.
- Never invent a "status-only" transition outside of what the CLI explicitly supports (e.g. `--skip-email` in §4.2 is a supported status-only transition; there is no equivalent for arbitrary stages).

If any precondition fails, **do not run the command**. Echo the CLI diagnostic verbatim and suggest the next valid step (typically "先 `cmd_status.py --talent-id ...` 看当前阶段").

---

## 8. Failure Handling

Classify every non-zero exit or error into one of four buckets and respond accordingly.

| Class | Detection | Correct next action |
|---|---|---|
| **Not found** | `ERROR: 未找到候选人` / empty search result | Ask the user to clarify identity. Offer a `cmd_search.py` query. |
| **Ambiguous** | Search returns >1 candidate | List the matches (name + `talent-id` + stage) and ask the user to choose. Do not act. |
| **Invalid state / args** | `ERROR: 当前阶段不允许` / `argparse` usage error / constraint violations | Report the error verbatim. Suggest the correct command or the query that would reveal current state. Never retry blindly with altered args. |
| **Infra / transient** | DB connection error, Feishu API error, IMAP error, traceback with network keywords | Report the failure as infrastructure-level. Do **not** suggest retrying a business command; suggest checking config / connectivity instead. |
| **chain 中间一步失败** | run_chain 短路；`chain_result["ok"]=False`；`chain_result["failed_step"]=…` | 详见 [AGENT_RULES.md §6](../skills/recruit-ops/docs/AGENT_RULES.md)。常见模式：`outbound.cmd_send` 成功但 `talent.cmd_update` 失败 → 邮件已发出、DB 未推进 → `feishu.cmd_notify --severity critical --title "邮件已发但状态未更新"` 附 talent_id + sent_at；老板手动 `talent.cmd_update` 补救。**不**自动 rollback（不可逆）。 |
| **`outbound.cmd_send --use-cached-draft` 失败：没有 draft 字段** | rc=2；stderr `没有 draft 字段` | 这是 `inbox.cmd_analyze` 在该 stage 没生成草稿（intent 不在 `prompts/post_offer_followup.json` valid_intents 里，或 LLM 限流），改推 `feishu.cmd_notify --severity warn --title "草稿缺失，需手动起草"`。 |
| **auto_reject scan: send failed** | `auto_reject.cmd_scan_exam_timeout` stderr 出现 `⚠ 发拒信失败: ...` 且 `failed=N` | 候选人**没**被删（故意——failure isolation）。`cli_wrapper` 已推飞书告警。排查 SMTP / 模板，让 cron 下个 tick 重试。**不**要手动跑 scanner 真跑（会和 cron 撞车）。 |

Never wrap a failure in an optimistic summary. If the command failed, the user's state did not advance.

---

## 9. Result Presentation

Preserve command semantics. Prefer verbatim command wording over paraphrase when precision matters.

**Universal rule**: only include fields the command actually returned. Never fill in "last action", "next action", "pending action", or any derived signal that the command did not output. If a caller asks for something the command did not return, run a more specific command (e.g. `cmd_status.py --talent-id <id>`) — do not infer.

### 9.1 Candidate list

For each row in a list response, include exactly the fields the query command returned. At minimum those are:
- Display name
- `talent-id`
- Exact stage label (bilingual acceptable: `ROUND2_DONE_REJECT_KEEP / 二面未通过（保留）`)

Additional fields (scheduled time, confirmation status, email, etc.) are included **only if** the command returned them for that row. Do not group by custom buckets unless the user explicitly asked.

If the user explicitly asks for grouping, the safe order is:

1. group by exact stage label;
2. only merge labels into a broader bucket when the command itself already returned that bucket, or the skill explicitly defines the bucket as lossless;
3. for any `*_DONE_*` stage, prefer a terminal bucket such as `已结束`, `保留人才池`, or `其他状态` — never an in-progress round bucket.

### 9.2 Single candidate detail

Reply with the fields returned by `cmd_status.py --talent-id <id>`. Do not synthesize a "next action" from the stage. If the user asked "what's next", either:
- quote the stage and let them decide, or
- offer the candidate's stage-matching commands from §4 as options to choose from.

For PII, see §10.

### 9.2.1 Time phrasing guardrail

Do **not** add any unchecked natural-language calendar interpretation to a time field returned by a command.

This includes:

- `本周日`
- `下周一`
- `明天下午`
- `周几`
- and any similar natural-language date phrasing

These phrases are allowed **only if**:

1. the command itself already returned that phrasing or field, or
2. the agent first verified it with a deterministic date calculation / real calendar lookup

Otherwise, repeat only the raw returned timestamp, e.g.:

- `面试时间：2026-04-20 09:30`

Not allowed:

- `面试时间：2026-04-20 09:30（本周日）`
- `面试时间：2026-04-20 09:30（下周一上午）`
- `面试时间：明天下午 09:30`

If a verified natural-language rendering is added, keep the original timestamp visible as the primary source of truth.

### 9.3 Mutation result

After a successful mutating command, echo back only what the command itself reported: candidate name, `talent-id`, the new stage as printed by the command, and any scheduling / notification confirmations the command explicitly logged.

### 9.4 CV ingest preview (the two branches)

`cmd_ingest_cv.py` (§2.1.5) emits one of two structured preview shapes. **Forward the preview body to HR verbatim** and treat the embedded payloads as proposals for the next §2.2 mutating command.

**Branch A — existing candidate (DB match)**: the preview contains a full-field diff table and two payload markers:

- `[OC_CMD_ON_CONFIRM_UPDATE]` — the `cmd_attach_cv.py ... --confirm --field key=value ...` command that will write all detected changes and archive the CV.
- `[OC_CMD_ON_CONFIRM_ARCHIVE]` — the `cmd_attach_cv.py ... --confirm` command that will archive the CV without changing any fields.

HR's reply selects one of:
- **"确认更新"** → execute the `UPDATE` payload.
- **"仅存档"** → execute the `ARCHIVE` payload.
- **"只更新X / Y / ..."** → take the `UPDATE` payload, remove the `--field` args that HR excluded, execute the trimmed command.
- **"把X改成..."** → take the `UPDATE` payload, replace the value of that `--field`, re-show the preview, wait again.
- **"忽略"** → do nothing.

**Branch B — new candidate (no match)**: the preview contains the parsed fields and two markers:

- `[OC_CMD_ON_CONFIRM]` — the `cmd_new_candidate.py --template ...` command ready to run if HR confirms the candidate is genuinely new and the starting stage is `NEW`.
- `[OC_NOTE]` — a note prompting HR to confirm both the fields and the intended starting stage.

HR's reply selects one of:
- **"修正 X=..."** → edit the relevant field, **re-show the preview**, wait again. Never execute until HR confirms after seeing the edited preview.
- **"确认 + 阶段 NEW"** (or the equivalent) → execute the `[OC_CMD_ON_CONFIRM]` payload.
- **"确认 + 阶段 <其他>"** (e.g. `ROUND1_SCHEDULED`, `EXAM_SENT`) → switch to `intake/cmd_import_candidate.py --template ... --stage <stage>` and execute.
- **"不是候选人 / 忽略"** → do nothing.

Either branch: the downstream `cmd_attach_cv.py` / `cmd_new_candidate.py` / `cmd_import_candidate.py` execution still goes through the §2.2.1 protocol — HR's explicit reply to the preview **is** that confirmation; do not ask for a second one unless HR changes any parameter.

---

## 10. Privacy / PII

Recruitment data is personal data. Apply minimum-necessary disclosure.

- Default list views: `name + talent-id + stage [+ next action]`. No email, phone, or WeChat unless the user specifically asked.
- When a single candidate's contact is needed (e.g. for the boss to write directly), disclose **one** channel at a time, preferably the one implied by the task.
- Never paste full CV text, ID numbers, or bank-adjacent fields into a reply unless the user explicitly requested them.
- For search results, one-line-per-candidate is the default. Expand only on follow-up.

---

## 11. Anti-Patterns

Do not do any of the following:

- Ask the user for Feishu links, Bitable links, or spreadsheet links. The repo owns the workflow.
- Regroup command output into custom buckets that the command did not return.
- Present a `*_DONE_*` stage as "still in that stage".
- Put `ROUND2_DONE_REJECT_KEEP` under a bucket named `二面阶段` (it is terminal, not in-progress).
- Put `EXAM_REJECT_KEEP` under a bucket named `笔试阶段` (it is terminal, not in-progress).
- Reference removed stages (`ROUND1_DONE_PASS`, `ROUND2_DONE_PASS`, `ROUND2_DONE_PENDING`, `ROUND1_DONE_REJECT_KEEP`, `OFFER_HANDOFF`, `ROUND1_DONE_REJECT_DELETE`, `ROUND2_DONE_REJECT_DELETE`) — they no longer exist (v3.6 sweep).
- Append `周几` / `本周几` / `明天` / `下周一` style calendar wording to a returned timestamp without a deterministic date check.
- Use `cmd_parse_cv.py` — it is deprecated. Use `cmd_ingest_cv.py`.
- Hardcode local absolute paths into the skill or the suggested command.
- Mix `uv run python3 scripts/...` with bare `python3 <group>/...` in the same reply.
- Run a mutating command without a resolved unique `talent-id`.
- **Run any §2.2 mutating command without first presenting the resolved command and receiving an explicit user confirmation (§2.2.1). This applies to every mutating command, no exceptions.**
- Treat a user's original request as pre-authorization; always present the resolved command and wait for a fresh affirmative before executing.
- Batch multiple mutating commands under a single confirmation. One confirmation = one command. **Exception**: a multi-step chain（§5.x，e.g. §5.1 安排一面 = `outbound.cmd_send` + `talent.cmd_update`）作为一个语义单元 propose 给 boss，一次 confirm 授权整条 chain。但**跨场景**仍要分开 confirm。
- Run a §2.3 destructive command on a generic "yes"; require the affirmative to name the destructive action.
- Choose `reject_delete` without explicit same-turn user confirmation. The only permissible default for "reject" is `reject_keep` (see §2.3).
- Wrap a command failure in an optimistic "done" phrasing.
- Trigger `cmd_ingest_cv.py` on a file that does not match §4.1.1 detection rules. Ask HR if uncertain.
- Paraphrase or summarize `cmd_ingest_cv.py` output. Forward the preview body verbatim to HR.
- Execute both `[OC_CMD_ON_CONFIRM_UPDATE]` and `[OC_CMD_ON_CONFIRM_ARCHIVE]` from the same preview — they are mutually exclusive branches; run exactly one based on HR's reply (§9.4).
- Rewrite the absolute file path from a Hermes Gateway message into a `<workspace_root>`-relative form. Pass the path through verbatim.
- Pass `--to hr` on `cmd_send_cv.py` when the Boss just said "看简历" or "把简历发过来". Default (no `--to`) sends to Boss, which is correct.
- Translate an AI exam review score into a pass/fail recommendation. The AI report is advisory; always defer the decision back to the boss and propose `cmd_exam_result.py` as the next step.
- Re-run `cmd_exam_ai_review.py` without an explicit user request just because a prior AI score looked low; treat AI scores as a single data point, not as a trigger for further automation.
- Reference `pending_rejection_id` / "12h 自动拒缓冲窗口" / `cmd_propose` / `cmd_cancel` / `cmd_execute_due` / `cmd_list` / `llm_classify` in any reply or proposal — these were removed on 2026-04-23. The only remaining auto_reject script is `auto_reject.cmd_scan_exam_timeout` (cron-only; agent only `--dry-run`).
- Propose `auto_reject.cmd_scan_exam_timeout` without `--dry-run` from the agent. Real runs are cron's job; manual real runs would race and double-send rejection emails.
- Suggest a "legitimate reschedule whitelist" or LLM-classified late-reschedule auto-rejection. That logic was removed; reschedule emails 走 §5.3 chain（`feishu.cmd_calendar_delete` + `outbound.cmd_send --template reschedule` + `talent.cmd_update`），boss 决策。
- Skip the rejection email by recommending `--skip-email` "for safety". `--skip-email` 仅在 boss 已线下手发拒信时使用；否则候选人会无通知被删（这是 2026-04-22 修过的 bug）。
- **Reference any of these v3.5-deleted scripts in any reply or proposal**: `cmd_round1_schedule`, `cmd_reschedule`, `cmd_defer`, `cmd_followup_reply`, `cmd_followup_close`, `followup_scanner`, `followup_analyzer`, `daily_exam_review`, `exam_ai_reviewer`, `cmd_finalize_interview_time`, `cmd_wait_return_resume`, `cmd_reschedule_request`, `ops/cmd_push_alert`. 它们都已删除——按 §1.3.1 的 atomic CLI 表 + AGENT_RULES.md §5 chain 重新规划。
- **Reference any of these v3.5.2-dropped DB columns**: `talents.followup_status`, `talents.followup_entered_at`, `talents.followup_last_email_id`, `talents.followup_snoozed_until`, `talents.exam_last_email_id`, `talents.round1_last_email_id`, `talents.round2_last_email_id`. 全部已 DROP——邮件去重和状态由 `talent_emails` 表承担。
- **Reference `data/followup_pending/` / `data/followup_archive/` 目录**：v3.5 下线了文件队列，邮件状态全在 `talent_emails.status` 列里。
- **❌ 编造业务数据去满足 atomic CLI 的必填参数**（v3.5.4）。例：老板说「直接进 offer」，CLI 报「`--round2-time` required」时，**绝不**编一个时间（如「明天 10:00」）让命令跑通。`--round2-time required` 是 CLI 在告诉你「你以为这是 schedule round 2，但你的输入里根本没二面时间」——99% 是路径选错了。正确反应：abort，回头看 [AGENT_RULES.md §3.3 + §5.9](../skills/recruit-ops/docs/AGENT_RULES.md)，改用 `talent.cmd_update --force` force-jump。**发出去的候选人邮件不可撤回**——这条规则用 2026-04-21 17:06 给两位候选人错发的二面邀请换来的。
- **❌ 通过 `talent.cmd_update --set` 伪造下游字段「哄」过 stage-gate**（v3.5.4）。例：CLI 报「ROUND2_SCHEDULING 不允许 round2 pass」，**绝不**用 `cmd_update --set round2_confirm_status=CONFIRMED --stage ROUND2_SCHEDULED` 假装「候选人 confirm 了」绕过门禁。`cmd_update --force` 才是合法的「越权推 stage」工具，但**它**只动 `current_stage`、不动业务字段——若你想动业务字段，先回头问自己为什么。同样适用：`round1_confirm_status=CONFIRMED` / `exam_sent_at=…` / `round{N}_invite_sent_at=…` 等任何会让"系统以为某事真发生过"的字段。
- **❌ CLI 报 pre-condition error（`必须提供 X` / `阶段 Y 不允许 Z` / `阶段 Y 不允许 W`）后绕路或迭代试错**（v3.5.4）。这种错误是 CLI 的 stage-machine 在告诉你「你的整体路径选错了」。正确反应：**stop the chain，重新评估意图**——大概率应该走 [AGENT_RULES.md §5.9](../skills/recruit-ops/docs/AGENT_RULES.md) force-jump 或先问老板澄清。**禁止**：(a) 改个参数再试 (b) 加 `--force` 硬上 (c) 用 `cmd_update --set` 把卡住的字段提前填好 (d) 把多个 atomic CLI 拼成「绕开门禁」的伪 chain。
- **❌ 把老板的「直接跳到 X」原始指令当成「按正常流程推到 X」执行**（v3.5.4）。「直接 / 跳到 / 略过 / 跳过 / 强制 / 不要面 / 直接进」中任何一个字眼出现 = §3.3 stage-jump override 触发 = **唯一**走 §5.9 单步 force-jump = **不**发任何候选人邮件、**不**创建任何日历、**不**更新任何业务字段。识别规则见 [AGENT_RULES.md §3.3](../skills/recruit-ops/docs/AGENT_RULES.md)。
- **❌ 发 onboarding offer 时漏附件 / 漏 HR 飞书通知 / 自作主张填薪资和入职日期**（v3.5.5；附件部分 v3.5.10 转为系统自动）。`onboarding_offer` 模板正文里**写死**了「附件是 ...《示例科技实习协议》+《实习生入职信息登记表》」——v3.5.10 起两份附件由 `email_templates.auto_attachments` 自动追加，**agent 不要再手动 `--attach`**；如果文件被删 / 改名，cmd_send 会 fail-fast 拒发，不会裸发 offer。HR **必须**通过 `feishu.cmd_notify --to hr` 同步（HR 不在邮件 cc 里）。`onboard_date` 与 `daily_rate` 老板没明说**必须先 stop and ask**——`daily_rate` 默认 350 是「老板已确认 350」时的快捷路径，不是兜底。详细规则见 [AGENT_RULES.md §5.10 + §10](../skills/recruit-ops/docs/AGENT_RULES.md)。
- **❌ 一面派单 hardcode 面试官 open_id / 让 LLM 直接判定面试官 / 漏调 `intake.cmd_route_interviewer`**（v3.5.7）。`§5.11` chain 的**第一步必须**是 `intake.cmd_route_interviewer`，输出的 `interviewer_open_ids` 才能作为 `feishu.cmd_calendar_create --extra-attendee` 与 `feishu.cmd_notify --to interviewer-*` 的依据。**绝不允许**：(a) agent 在脑子里看 `talents.education` / `has_cpp` 后自己挑面试官；(b) 把 `ou_xxxxx` 字符串直接拼进命令；(c) `ambiguous=true` 时 fallback 到「随便派一个」或「派给老板」；(d) `config_error=true` 时拿占位符 `ou_PLACEHOLDER_*` 当真账号发飞书。正确反应：转 ASK_HR 分支（`feishu.cmd_notify --to hr` 报告原因，等 HR 显式指派）。详细见 [AGENT_RULES.md §5.11 + §7.9](../skills/recruit-ops/docs/AGENT_RULES.md)。
- **❌ 把 §5.1（老板触发）和 §5.11（HR 触发）的 chain 混用**（v3.5.7）。两条 chain **触发人不同、出口 stage 不同、是否派单不同、是否当场建日历不同**——boss 的「我安排 X 一面」走 §5.1，HR 的「t_xxx 一面时间 …」走 §5.11。识别提示：消息明确出现 "HR" / "招聘助理" / 飞书 sender 是 hr_open_id，或语气是「**已**和候选人沟通好时间」「请安排面试官」，几乎都是 §5.11；老板侧通常是「我下周二下午面 X」这种自己面的语气，是 §5.1。混了会出现「HR 触发但漏派面试官」或「老板触发却把老板自己当 extra-attendee 重复邀请」。
- **❌ 回答「完整信息 / 档案 / 全部资料」时只发"📋 候选人档案 / 📂 文件状态"两个空标题再贴一个 cv_path 就声称"信息已同步"**（v3.5.10 真实事故）。"完整信息" = `talent.cmd_show` 输出里所有非空字段一项不漏。如果你只想给 cv 路径就别打"完整信息"四个字；要打就把所有字段（candidate_name / email / phone / position / education / school / work_years / source / experience / current_stage / cv_path / 笔试与一面二面时间 / round*_confirm_status / 最近审计事件）都列出来。详细见 §12 路由表对应行。
- **❌ 把飞书 Gateway 落盘时给附件加的 `doc_<hex>_` 前缀当作真实文件名留在 `cv/` 目录或 `talents.cv_path` 里**（v3.5.10）。`lib.candidate_storage.import_cv` 已自动剥；历史数据用 `talent.cmd_normalize_cv_filenames` 一次性补救。**绝不**：(a) 手动在文档 / 飞书回复里贴带 `doc_<hex>_` 前缀的路径而不提示这是脏数据；(b) 自己写脚本去 mv 这些文件而不用 `cmd_normalize_cv_filenames`（它会同步 DB + 处理重复副本）。
- **❌ 用 `exam.fetch_exam_submission` 重拉候选人简历 / 笔试附件**（v3.5.8）。`inbox.cmd_scan` 已在每次扫到候选人新邮件时**自动**把附件按 `context` 分流落到 `data/candidates/<tid>/{exam_answer|email}/em_<eid>/`，元数据写到 `talent_emails.attachments` JSONB 数组（含 `path` / `size` / `sha256`，`path` 字段是**相对 `data_root()`** 的路径，例如 `candidates/t_xxx/exam_answer/em_yyy/file.zip`）。boss 说「看下 X 的简历」**先**查 `talents.cv_path`（CV 是绝对路径） / `talent_emails.attachments`（附件是相对路径，前面拼 `data/`），**禁止**为了拉一份附件调 IMAP 重下载。同样禁止：(a) 引用旧 `data/candidate_answer/t_t_<tid>/em_<eid>/` 路径（v3.5.8 已迁完并清空，旧路径不存在）(b) 把 `attachments[*].path` 拼成绝对路径硬编码，要走 `Path(data_root()) / row.path` (c) 用 `outbound.cmd_send --attach` 时漏写 `data/` 前缀。

---

## 12. Routing Examples (eval cases)

These are canonical input → route mappings. Use them as sanity checks and as regression test anchors.

Read-only queries are shown as a single step. Mutating commands are shown as two steps: **(a) propose** the resolved command to the user, **(b) execute** on explicit confirmation (§2.2.1). Never collapse (a) and (b) into one turn.

| User says | Correct route |
|---|---|
| "今天有谁有面试" | read-only: `cmd_today_interviews.py` |
| "看看所有候选人" | read-only: `cmd_status.py --all` |
| "还在进行中的候选人" | read-only: `cmd_search.py --all-active` |
| "查一下张三现在到哪一步了" | read-only: `cmd_search.py --query 张三` → if unique, `cmd_status.py --talent-id <id>` |
| Boss / HR 说 "看看候选人B" / "把 X 在人才库的完整信息给我" / "X 的档案" / "X 的全部资料" | read-only：resolve unique id → `uv run python3 -m talent.cmd_show --talent-id <id>` → **forward 输出原样**（含 `候选人档案 / 邮件统计 / 审计事件` 三段所有非空字段）。**严禁**：(a) 自己捏造 "📋 候选人档案" / "📂 文件状态" 这类标题然后只填一段（v3.5.10 真实事故：飞书回复留了空 "候选人档案" 标题就把 cv_path 贴出来了）；(b) 省略 `cmd_show` 输出里任何**非空字段**（candidate_name / email / phone / position / education / school / work_years / source / experience / current_stage / cv_path / created_at / updated_at / 笔试与面试时间 / round*_confirm_status / 审计事件）。可以用 markdown 排版，但**字段一项也不能漏**。如果输出超长（>4 KB），可以裁掉 experience 长摘要并标注 "…（experience 已截断，全文见 talents.experience）"。|
| HR sends PDF named `量化研究员-上海-500元_天-李四-2026年应届生.pdf` | §4.1.1 matches → auto-triggered `cmd_ingest_cv.py --file-path <path> --filename <name>` → forward preview verbatim → wait for HR reply → execute selected `[OC_CMD_ON_CONFIRM*]` payload per §9.4. |
| HR sends a generic PDF `会议纪要.pdf` with no candidate context | §4.1.1 does not match → **do not** run `cmd_ingest_cv.py`; ask HR to confirm it's a CV, or fall through to generic file handling. |
| "候选人A笔试已发，只改状态" | resolve id → **propose** `interview/cmd_result.py --talent-id <id> --round 1 --result pass --skip-email` → wait for confirm → execute. Only status-only transition supported; do not invent others. |
| "候选人D一面被拒，保留人才库" | resolve id → **propose** `interview/cmd_result.py --talent-id t_vxunkj --round 1 --result reject_keep` → wait for confirm → execute. |
| "把张三一面改到明天下午三点" | resolve id → resolve time → **propose §5.3 chain 整条**：(1) `feishu.cmd_calendar_delete --event-id <round1_calendar_event_id> --reason "候选人改期"` (2) `outbound.cmd_send --talent-id <id> --template reschedule --vars round_label=一面 old_time=<old> new_time=<new> location=<office>` (3) `talent.cmd_update --talent-id <id> --stage ROUND1_SCHEDULING --set round1_time=<new> --set round1_confirm_status=PENDING --set round1_calendar_event_id=__NULL__ --set round1_invite_sent_at={send.sent_at} --reason "candidate reschedule"` → wait for confirm → 一次性执行整条 chain。 |
| "新候选人张三 t_xxx，安排明天下午三点一面" | resolve time → **propose §5.1 chain 整条**：(1) `outbound.cmd_send --talent-id t_xxx --template round1_invite --vars round1_time=<resolved> position_suffix=<职位> location=<office>` (2) `talent.cmd_update --talent-id t_xxx --stage ROUND1_SCHEDULING --set round1_time=<resolved> --set round1_invite_sent_at={send.sent_at} --set round1_confirm_status=PENDING --set round1_calendar_event_id=__NULL__ --set wait_return_round=__NULL__ --reason "agent: schedule round 1"` → wait for confirm → 一次性执行整条 chain。 |
| "李四在国外，下个月再约一面" | resolve id → **propose §5.4 chain**：（如已建日历，先 `feishu.cmd_calendar_delete`）→ `outbound.cmd_send --talent-id <id> --template defer --vars round_label=一面` → `talent.cmd_update --stage WAIT_RETURN --set wait_return_round=1 --set round1_time=__NULL__ --set round1_calendar_event_id=__NULL__ --reason "candidate defer until return"` → wait for confirm → 整条执行。 |
| "发简历给老板 张三" | resolve unique candidate → **propose** `intake/cmd_send_cv.py --name 张三` (or `--talent-id`) → wait for confirm → execute. |
| "删掉这个候选人" | §2.3 destructive: resolve id → **propose** `talent/cmd_delete.py --talent-id <id> --reason "<原话>"` (或 `common/cmd_remove.py --talent-id <id>`，等价) with candidate name and current stage → wait for a confirmation that names the destructive action (e.g. "是，删除 t_xxx") → execute. Generic "yes" is not sufficient. |
| "他二面通过" with no prior unique candidate in turn | **do not act**; ask who "他" refers to. Never propose or execute. |
| "审阅候选人A的笔试邮件" / "评审一下张三的笔试" / "看一下李四的笔试" | resolve unique `talent-id` (search if needed) → **two-step propose**: (1) **propose** `exam/cmd_exam_ai_review.py --talent-id <id>` for terminal preview (no `--feishu` / `--save-event`); wait for confirm → execute. (2) After Boss reads the report, **propose** the same command with `--feishu --save-event` to push and audit; wait for confirm → execute. The CLI auto-pulls the latest 笔试 submission from IMAP and caches at `/tmp/exam_submissions/<id>/`；**v3.5 起没有任何前置脚本要跑**（`daily_exam_review.py` 已删；`fetch_exam_submission.py` 是 helper 由 `cmd_exam_ai_review` 内部调用）。 |
| "看看 auto reject 会拒谁" / "今天有没有人会被自动拒" | read-only: `uv run python3 -m auto_reject.cmd_scan_exam_timeout --dry-run` |
| "为什么 X 被自动拒了" | read-only：`uv run python3 -m inbox.cmd_review --talent-id <id>`（看他 `exam_sent_at` 之后是不是真没回信）+ `uv run python3 scripts/talent/cmd_show.py --talent-id <id>`（看他归档 / 删除 audit）。**不要**改写或猜系统判定，只引用查询结果。|
| Boss 看到飞书 "[自动拒删 · 已执行]" 卡片，说 "好" / "ok" / "知道了" | **不需要任何操作**。卡片是事后告知，候选人已删除（不可逆）。Agent 不要提议恢复或撤销。|
| Boss 看到飞书 "[自动拒删 · 失败]" 卡片 | 候选人**没**被删，留在 `EXAM_SENT`。下次 cron 会再扫；如果反复失败，看 SMTP / 模板 / DB 排查。Agent 不要手动跑 `cmd_scan_exam_timeout` 真跑（会和 cron 撞车）。|
| Boss 说 "把 X 拒了，删人" | §2.3 destructive: **propose** `interview/cmd_result.py --talent-id <id> --round <N> --result reject_delete` → wait for affirmative naming the destructive action → execute. 该命令会自动发 `rejection_generic` 再删人；`--skip-email` 仅在 boss 已线下发拒信时使用。|
| Boss 说 "候选人F笔试通过，直接进 offer 阶段" / "候选人A不需要二面，直接结束流程" / "跳过二面，直接发 offer" | **§5.9 force-jump 单步**（[AGENT_RULES.md §5.9](../skills/recruit-ops/docs/AGENT_RULES.md)）。resolve unique `talent-id` → **propose 单步 chain**：`talent.cmd_update --talent-id <id> --stage POST_OFFER_FOLLOWUP --force --reason "boss原话: <逐字引用老板原话>"` → 提示老板"这一步**不会**发邮件、**不会**建日历、**不会**更新 round2_time / confirm_status 等业务字段，只把 stage 推到 POST_OFFER_FOLLOWUP；如果你希望先发二面邀请走正常流程，请改说『安排二面，时间 …』" → wait for explicit confirm → execute. **绝不**调 `exam.cmd_exam_result --result pass` 或 `interview.cmd_result --round 2 --result pass`（这些会真发候选人邮件）。|
| Boss 说 "王五先放进笔试阶段" / "把张三强制推到 EXAM_REVIEWED" | **§5.9 force-jump 单步**：resolve id → **propose** `talent.cmd_update --talent-id <id> --stage <target> --force --reason "boss原话: …"` → 提示「不发邮件、不动业务字段」 → wait for confirm → execute. 注意：若老板的目标 stage 是 `STAGE_LABELS` 之外的字符串，**stop and ask**（[AGENT_RULES.md §5.9](../skills/recruit-ops/docs/AGENT_RULES.md) 末尾），不要自己猜。|
| Boss 说 "笔试通过 + 安排二面，时间是明天上午 10 点" | **chain §5.5**（不是 §5.9）：resolve id + 时间 → propose 完整 chain `outbound.cmd_send --template round2_invite --vars round2_time=… location=…` + `talent.cmd_update --stage ROUND2_SCHEDULING --set round2_time=… …` → wait for confirm → execute. **关键差异**：boss 此处**明确要求安排二面**（给了时间、提了 invite），所以走正常 chain；而上面三行的 boss 是**明确要求跳过二面**——不要混淆。如果 boss 的指令两可，**stop and ask**。|
| Boss 说 "给候选人A发 onboarding offer，5 月 6 日入职，薪资 350 / 天" | **chain §5.10**（[AGENT_RULES.md §5.10 + §10](../skills/recruit-ops/docs/AGENT_RULES.md)）：resolve unique `talent-id` → 检查 `talent_emails` 是否已发过 onboarding_offer（重发要 boss 二次确认）→ propose 完整 chain：(1) `outbound.cmd_send --talent-id <id> --template onboarding_offer --vars position_title=<DB.position> interview_feedback=<默认套话或老板原话> daily_rate=350 onboard_date=2026-05-06 location=<office> evaluation_criteria=<默认套话>`（v3.5.10：**不要**再加 `--attach`，《示例科技实习协议》+《实习生入职信息登记表》两份 docx 由 `auto_attachments` 自动追加）(2) `feishu.cmd_notify --to hr --severity info --title "新候选人 offer 已发，请准备入职" --body "candidate=<id> name=<name> 入职日期=2026-05-06 薪资=350 元/天 岗位=<position>（附件：实习协议 + 入职登记表）"` → wait for confirm → 一次性执行整条 chain。**关键校验**：`onboard_date` / `daily_rate` 必须 boss 明确给出；缺任一项**先 stop and ask**，不要 propose。|
| Boss 说 "给候选人A发个 offer 通知"（**没**说入职日期 / 薪资） | **不直接 propose §5.10**。回飞书：「老板，发 onboarding offer 需要 (1) 入职日期 (2) 实习日薪（默认 350 元/天）；麻烦确认一下，确认后我会同时把 HR 抄进飞书。」拿到答复后再走上面那行的完整流程。|
| Boss 说 "看下候选人F发过来的简历 / 笔试附件 / 文件"（v3.5.8） | **不**调 `exam.fetch_exam_submission`。read-only：resolve unique id → (a) **CV** 直接报 `talents.cv_path`（绝对路径）。(b) **其他附件**：查 `SELECT subject, sent_at, context, attachments FROM talent_emails WHERE talent_id=<id> AND attachments IS NOT NULL ORDER BY sent_at DESC LIMIT 5;` → 拿 `attachments[*].path`，文件**绝对路径**= `data/<path>`（典型如 `data/candidates/t_xxx/exam_answer/em_yyy/file.zip`）→ 在飞书回复里列出文件名 + 大小 + 落盘路径，让老板自己 `cat` / `cp` 出去看。(c) `data/candidates/<id>/exam_answer/legacy_fetch/` 下还有 `_ai_review_result.json` 等历史评审产出（v3.5.8 之前手动 fetch 留下的），可一并提示。如果 `attachments IS NULL` 但 boss 坚持有附件，可能是 `inbox.cmd_scan` 还没扫到，建议等下一次 cron tick（5 分钟），不要手动 retrigger。|
| **HR** 说 "安排张三一面，时间是 4-25 14:00" / "t_xxxxx 一面 4 月 25 日 14:00"（v3.5.7） | **§5.11** chain（[AGENT_RULES.md §5.11](../skills/recruit-ops/docs/AGENT_RULES.md)）：resolve unique id（HR 通常已 `cmd_ingest_cv` 过；若 `talents.education` 为空 / 候选人不存在 → stop and ask HR 先 ingest CV）→ propose 完整 chain：(1) `intake.cmd_route_interviewer --talent-id <id> --json` （**必跑第一步**，输出 `interviewer_open_ids`、`ambiguous`、`config_error`）(2) `outbound.cmd_send --template round1_invite --vars round1_time="2026-04-25 14:00" --json` (3) `feishu.cmd_calendar_create --talent-id <id> --time "2026-04-25 14:00" --round 1 --duration-minutes 30 --candidate-name <name> --candidate-email <email> --extra-attendee {route.interviewer_open_ids[0]} --json` (4) `talent.cmd_update --stage ROUND1_SCHEDULED --set round1_time="2026-04-25 14:00" --set round1_invite_sent_at={send.sent_at} --set round1_confirm_status=CONFIRMED --set round1_calendar_event_id={cal.event_id}` (5) `feishu.cmd_notify --to interviewer-{role} --severity info --title "一面安排：<name>" --body "候选人/时间/学历/邮箱/cal_eid"` (6) `feishu.cmd_notify --to boss --severity info --title "一面已排：<name>"` → wait for HR confirm → 一次性执行。**关键校验**：route step `ambiguous=true` 或 `config_error=true` 时**绝不**继续，改推 `feishu.cmd_notify --to hr --severity warn` 报告原因（HR 显式回 `master/bachelor/cpp` 后再重启）。|
| **HR** 说 "t_xxx 一面派给 cpp" / "派给硕士面试官" / "派给本科面试官"（§5.11 ambiguous 后 HR 回话） | resolve unique id + role → 重启 §5.11 但跳过 step 1：把 HR 指定的 `interviewer_<role>_open_id` （从 `lib.config['feishu']`）当作 `route.interviewer_open_ids` 喂给 step 2 起步的 chain → propose 余下 5 步 → wait for HR confirm → execute. **依然不允许** agent 写 hardcode `ou_xxx`；从 config 读不到（占位符）就直接 stop and ask 运维。|

---

## 13. Runtime

### 13.1 Where this skill runs

This skill is loaded by **Hermes Gateway** and is not an independent process. It reads Feishu messages over WebSocket and routes them to the `recruit-ops` CLI in the workspace. It assumes the runtime is already configured; it does not install dependencies, create the DB, or set env vars — if any of those are missing, report the failure (§8 Infra class) and stop.

### 13.2 Hard prerequisites (assumed present)

- **Python**: 3.10+
- **Dependencies installed**: either `uv sync` has been run under `<workspace_root>/skills/recruit-ops/`, or the project's `.venv` is present. `uv run python3` and `<workspace_root>/skills/recruit-ops/.venv/bin/python3` are equivalent — pick one and use it consistently in a single reply.
- **Database**: a configured `talent-db` is reachable; `scripts/lib/talent_db.py` resolves connection from `scripts/lib/talent-db-config.json` or equivalent env vars.
- **Feishu identities**: `FEISHU_BOSS_OPEN_ID` and `FEISHU_HR_OPEN_ID` env vars (or openclaw account fields `ownerOpenId` / `hrOpenId`) are set.
- **Cron / systemd**: `cron/cron_runner.py` 调度（v3.5 起统一入口，串 `inbox.cmd_scan` + `inbox.cmd_analyze` + `auto_reject.cmd_scan_exam_timeout` + `ops.cmd_health_check`），外部调度时设 `PYTHONPATH=scripts`。**v3.5.10 起官方部署 = systemd user timer**：`~/.config/systemd/user/recruit-cron-runner.{timer,service}`，每 10 分钟一轮；改时间 / 排障运维命令见 [CLI_REFERENCE.md §定时任务](../skills/recruit-ops/docs/CLI_REFERENCE.md#cron_runnerpy)。**禁止再用** `recruit-{exam-scan,interview-confirm,reschedule-scan}.{timer,service}`（已在 v3.5.10 删除，原指向已下线的 `daily_exam_review.py`，导致历史上 systemd 单元静默 failed 多日没人发现）。

### 13.2.1 SKILL.md 同步路径（**改完必须执行**）

`recruit-workspace/docs/recruit-ops-SKILL.md` 与 `~/.hermes/skills/openclaw-imports/recruit-ops/SKILL.md` 是**两份独立副本**（不是软链）。任何对前者的改动必须同步到后者，并重启 Hermes 让它重读：

```bash
cp <RECRUIT_WORKSPACE>/docs/recruit-ops-SKILL.md \
   /home/admin/.hermes/skills/openclaw-imports/recruit-ops/SKILL.md
# 然后重启 Hermes（具体命令依部署方式）
```

本机长期单一部署时，建议直接做软链以一劳永逸：

```bash
ln -sf <RECRUIT_WORKSPACE>/docs/recruit-ops-SKILL.md \
       /home/admin/.hermes/skills/openclaw-imports/recruit-ops/SKILL.md
```

### 13.3 Open-source / plug-and-play target

For future distribution, the skill body stays portable by design:

- No machine-local absolute paths in the body; use `<workspace_root>` placeholders.
- The only place with a concrete local path is the **Local deployment mapping** block at the top of this file — remove or re-map it per deployment.
- Hermes Gateway loading path (`~/.hermes/skills/openclaw-imports/recruit-ops/`) is also deployment-specific and lives only in that top block.
- Point at CLI commands, never at operator shell aliases or private scripts.
- Deployment-specific overrides belong in a **separate** ops note, not in this skill.

---

## 14. Summary Checklist

Before replying, confirm:

- [ ] Intent is mapped to a real CLI command listed in §4.
- [ ] The command's safety class (§2) has been respected: read-only run freely; auto-triggered previews (§2.1.5) only on §4.1.1 CV matches; **every mutating command was presented to the user first and executed only after an explicit same-turn affirmative (§2.2.1)**; §2.3 destructive commands received an affirmative that named the destructive action.
- [ ] For CV intake: `cmd_ingest_cv.py` was auto-triggered only on a §4.1.1 match; its output was forwarded verbatim; exactly one of `UPDATE` / `ARCHIVE` / `CONFIRM` was executed based on HR's reply (§9.4).
- [ ] A unique `talent-id` has been resolved for any mutating command.
- [ ] One confirmation authorized exactly one command (no batching).
- [ ] Any `--time` is in `YYYY-MM-DD HH:MM` Asia/Shanghai (+08:00) and has been echoed back to the user.
- [ ] Stage labels used in the reply match §6 (and therefore `core_state.py`).
- [ ] Query requests use the canonical query command, not a derived view.
- [ ] No `*_DONE_*` stage is described as in-progress.
- [ ] No reference to removed concepts/scripts: `pending_rejection_id` / `cmd_propose` / `cmd_cancel` / `cmd_execute_due` / `cmd_list` / `llm_classify` / "12h 缓冲窗口" / "soft_auto" / "legitimate reschedule whitelist" (§2.4 / §4.4 / §6.3).
- [ ] No agent-side real run of `auto_reject.cmd_scan_exam_timeout` (cron-only; agent only `--dry-run`).
- [ ] No field was added to the reply that the command did not explicitly return (§9).
- [ ] PII is disclosed at minimum-necessary level (§10).
- [ ] Command form is `uv run python3 scripts/...` throughout the reply.

---

## Appendix: Primary reference

- [skills/recruit-ops/docs/CLI_REFERENCE.md](../skills/recruit-ops/docs/CLI_REFERENCE.md) — full CLI reference with argument tables, conditional-required args, cron notes, and per-command examples.
- `skills/recruit-ops/scripts/lib/core_state.py` — canonical `STAGE_LABELS`.
