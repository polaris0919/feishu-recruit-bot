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

> **本地部署映射**（开源分发时请删除或重新映射）：
> - `<workspace_root>` = `/home/admin/recruit-workspace`
> - 脚本目录：`<workspace_root>/skills/recruit-ops/scripts/`
> - 运行时解释器：`<workspace_root>/skills/recruit-ops/.venv/bin/python3`（或 `uv run python3`，两者等价）
> - Hermes Gateway 从 `~/.hermes/skills/openclaw-imports/recruit-ops/` 加载本 skill
> - Hermes 消息里的文件路径已经是绝对路径（`/home/admin/...`）——直接原样传给 CLI 的 `--file-path` 参数；**不要**手动替换成 `<workspace_root>`。

通过仓库里稳定的 CLI 命令操作 `recruit-ops` 工作流。PostgreSQL 是唯一数据真源；永远不要凭记忆总结状态。

本 skill 是一份 **agent 契约**，服务两类使用者：
- **HR**：在飞书群里发 CV 附件和 `【新候选人】` / `【导入候选人】` 模板。skill 自动识别 CV（§4.1.1），解析（§2.1.5），并带 HR 走一条去重的录入流程。
- **Boss**：用自然语言查状态、安排日程、记录结果、交接后续。

本 skill 的设计原则：
- 对路由、副作用、结果呈现**严格**把关
- 正文保持**可移植**（除顶部「本地部署映射」块以外，一律使用 `<workspace_root>` 占位符）
- 与代码**保持一致**——正文与代码冲突时，以代码为准

---

## 1. 执行契约

### 1.1 规范调用形式

本 skill 里的每一条命令都只用这一种形式：

```bash
uv run python3 scripts/<group>/<command>.py ...
```

永远在仓库根目录下执行：

```bash
<workspace_root>/skills/recruit-ops
```

规则：
- **不要**把 `uv run python3 scripts/...` 和裸 `python3 <group>/...` 混用。只用上面这一种规范形式。
- **不要**依赖 shell 别名或宿主机的绝对路径（例如 `/home/admin/...`）。
- cron / systemd 调用需要 import 时，显式设置 `PYTHONPATH=scripts`（见 [CLI_REFERENCE.md](../skills/recruit-ops/docs/CLI_REFERENCE.md#cron_runnerpy)）。

### 1.2 数据真源

- PostgreSQL 支撑的 `recruit-ops` 状态是规范真源。所有 stage 名、计数、时间安排都必须来自命令输出，不得来自模型记忆。
- 当一条查询命令已经提供某个视图时，**不要**自己手工重建这个视图。DB 支撑的规范视图有：
  - `common/cmd_status.py` — 完整候选人列表，或某一个候选人的详情
  - `common/cmd_search.py` — 关键字搜索，或仅活跃视图
  - `common/cmd_today_interviews.py` — 按日期范围查面试安排
- 规范 stage 标签定义在 `scripts/lib/core_state.py`（`STAGE_LABELS`）。本 skill 在 §6 做了镜像；若代码与 skill 不一致，以代码为准，skill 必须更新对齐。

### 1.3 主要参考文档

- [skills/recruit-ops/docs/CLI_REFERENCE.md](../skills/recruit-ops/docs/CLI_REFERENCE.md) — 完整 CLI 参数表 / edge cases / cron 备注。
- [skills/recruit-ops/docs/AGENT_RULES.md](../skills/recruit-ops/docs/AGENT_RULES.md) — **agent 决策规则手册（v3.5）**：入站邮件 stage × intent 决策矩阵（§3）、典型 chain 范式（§5，6 条端到端钉死）、可用 atomic CLI 速查（§4）。**凡是本 SKILL.md §4 路由表里出现"按 chain 处理 → 见 AGENT_RULES.md §5.x"的，必须先 fetch 这份手册再行动。**

### 1.3.1 atomic CLI 架构

当前架构只有两层：**原子 CLI**（每条命令对应一个写动作 + 自验证 + 飞书告警）+ **agent chain**（LLM 看 §3 决策矩阵选下一步，用 `lib.run_chain` 串原子 CLI）。**没有旧版『业务剧本』包装脚本作为兜底**——所有多步流程都必须按 §4 路由表 + AGENT_RULES.md §5 chain 重新规划。

**核心哲学**（详见 [AGENT_RULES.md §2](../skills/recruit-ops/docs/AGENT_RULES.md)）：
> 动作 = atomic CLI；判断 / 编排 = agent（LLM 拿 §3 决策矩阵推下一步）。

#### 全量 atomic CLI 清单

| 模块 | 脚本 | 唯一职责 |
|---|---|---|
| `talent.cmd_add` | `talent/cmd_add.py` | 创建候选人（带自验证） |
| `talent.cmd_show` / `cmd_list` | `talent/cmd_show.py` / `cmd_list.py` | 读：单候 / 列表；`--json` |
| `talent.cmd_update` | `talent/cmd_update.py` | **唯一**写 `talents` 字段 + stage 推进路径；自然跳转免 `--force`，跨 stage 必须 `--force --reason "boss原话"` |
| `talent.cmd_delete` | `talent/cmd_delete.py` | **唯一**删档路径（自动归档 snapshot + emails 到 `data/deleted_archive/<YYYY-MM>/`；v3.5.9 同时把 `data/candidates/<tid>/` 整目录搬到 `deleted_archive/<YYYY-MM>/<tid>__dir_<ts>/` 并撤销 `by_name` 软链） |
| `talent.cmd_normalize_cv_filenames` | `talent/cmd_normalize_cv_filenames.py` | **v3.5.10 一次性维护**：剥掉 `talents.cv_path` 中飞书 Gateway 留下的 `doc_<hex>_` 前缀；同时移动文件并去重（同 size 副本删带前缀那份）。`--dry-run` 安全预览。日常 import_cv 已自动剥前缀，此 CLI 用于历史数据补救。 |
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

#### 运行规则（v3.5）

- **`outbound.cmd_send` 是 SMTP 唯一出口**。自由文本邮件流程：agent 起草 → boss 逐字确认 → 写到 `/tmp/draft_xxx.txt` → `outbound/cmd_send.py --subject S --body-file /tmp/draft_xxx.txt --in-reply-to '<id>'`。脚本自动清临时文件（`--cleanup-body-file` 默认开）。
- **POST_OFFER_FOLLOWUP 一键发**：`outbound.cmd_send --talent-id <id> --use-cached-draft <email_id>`，从 `talent_emails.ai_payload.draft` 取 LLM 草稿（`inbox.cmd_analyze` 已经写好）。（v3.6 起 `OFFER_HANDOFF` 已合并入 `POST_OFFER_FOLLOWUP`。）
- **`talent.cmd_update` 是 `talents.current_stage` 唯一推进路径**。自然跳转免 flag；跨 stage 必须 `--force --reason "boss原话"`，不要默认加 `--force`。
- **`talent.cmd_delete` 是删人唯一路径**，自动归档；`--no-backup` 必须有 boss 明示。
- **入站邮件统一管线**：所有候选人入站邮件**只**经 `inbox.cmd_scan` → `inbox.cmd_analyze` 两步——不再有 `daily_exam_review` / `followup_scanner` 各自扫一遍。
- **飞书通知统一管线**：`feishu.cmd_notify` 是 agent 推飞书的唯一出口；不要 import `lib.feishu`。
- **chain 编排**：agent 串多步动作时用 `lib/run_chain.py` 的 `Step(...)`；前一步 `--json` 输出可作占位符（语法 `{step.field}`，如 `--set round1_invite_sent_at={send.sent_at}`）。
- **chain 失败模型**：任意一步失败 → 短路 + `feishu.cmd_notify --severity critical`，**不**自动回滚（发邮件 / 删日历不可逆）。
- **失败 vs 输入错**：write 类脚本 crash → `lib/cli_wrapper.py` 自动飞书告警；`UserInputError`（缺 `--force`、talent_id 不存在、template 变量缺失）→ 只 stderr 不告警。
- **钉死的 chain 范式**：详见 [AGENT_RULES.md §5](../skills/recruit-ops/docs/AGENT_RULES.md)（§5.1 安排一面 ⚠ v3.6 起仅 `WAIT_RETURN` 出口使用 / §5.2 confirm+建日历 / §5.3 改期 / §5.4 暂缓 / §5.5 笔试转二面 / §5.6 一键发草稿 / §5.7 笔试拒保留 / §5.8 WAIT_RETURN 推老板 / §5.9 force-jump 单步 / §5.10 onboarding offer / §5.11 学历感知一面派单 ★ NEW 阶段唯一 happy path）。每条都被 `tests/test_agent_chain.py` 端到端回归——agent 必须**照着抄**，参数名 / `--set` 字段 / 占位符传递都已固化。

### 1.4 跟本 skill 对话的是谁

两类使用者，两种不同的消息形态：

| 使用者 | 典型消息 | skill 的职责 |
|---|---|---|
| **HR** | CV 附件（PDF/DOCX）、`【新候选人】` / `【导入候选人】` 文本模板、状态修正请求（"笔试已手工发出"）、候选人改期转发 | 识别、解析、去重，给 HR 出预览确认，再执行确认后的命令 |
| **Boss** | 自然语言的查询与指令（"今天谁有面试"、"张三一面改到明天下午三点"、"李陆斌一面被拒保留"） | 解析身份 + 时间，propose 命令，等 confirm，执行 |

skill 不显式追踪"当前说话的是谁"——消息形态本身足够区分。两者都是可信方；§2.2.1 的 confirm 协议对双方一视同仁。

**身份标识**：部署层通过环境变量配置两个飞书身份——`FEISHU_BOSS_OPEN_ID`（老板）与 `FEISHU_HR_OPEN_ID`（HR）。`intake/cmd_send_cv.py --to boss|hr` 据此路由。skill **不**读也**不**回显这些 open_id——只用抽象的 `boss` / `hr` 标签。

---

## 2. 安全模型

每条命令都落在三个安全等级中的一个。分类依据是命令的**最大可能副作用**，不是常见情形。

### 2.1 只读（可以主动跑）

任何 flag 组合下都**绝不**写 DB、**绝不**发出站邮件 / 飞书 / 日历请求。

- `common/cmd_status.py`
- `common/cmd_search.py`
- `common/cmd_today_interviews.py`

### 2.1.5 自动触发的预览类（CV 录入）

一类窄中间态：当某种触发消息（通常是 HR 发了 CV 形状的文件）出现时会**自动**跑，它**不改状态**，但确实有真实成本（LLM 解析、DB 读、磁盘 I/O），产出一份结构化预览，而这份预览本身就是下一步写操作的**提案**。

- `intake/cmd_ingest_cv.py` — 规范案例。触发时机与方式见 §4.1.1。

规则：
- 当消息符合 §4.1.1 的 CV 检测规则时，**自动**跑，不走 §2.2.1 的 confirm 协议。命令自身的输出就是给 HR 看的"提案"。
- **把输出原样转发**给 HR。**不要**转述或总结字段 diff。
- 输出里带一到多个 `[OC_CMD_ON_CONFIRM*]` payload，这些 payload 本身是 §2.2 写操作命令的**提案**（`cmd_attach_cv.py` / `cmd_new_candidate.py` / `cmd_import_candidate.py`）。这些下游命令**仍然**要走完整的 §2.2.1 confirm 协议；HR 针对预览的自然语言回复就作为那次 confirm。
- **绝不**对非 CV 消息主动跑 `cmd_ingest_cv.py`；Boss 侧除非明确要求录入某个文件，否则**绝不**跑。

### 2.2 写类（改 DB 状态 和 / 或 发外部消息）

这一类的任何命令要么写 DB、要么发出站消息、要么两者都做。**每一条写类命令都必须在执行前拿到用户的 confirm，没有例外。**

写类命令清单（v3.5 atomic CLI，全部经 `lib/cli_wrapper.py` 包裹 + 自验证 + 失败飞书告警）：

- **仅 DB**：`talent/cmd_add.py`、`talent/cmd_update.py`
- **DB + IMAP / SMTP / 飞书（agent chain 的一步）**：`outbound/cmd_send.py`、`inbox/cmd_scan.py`、`inbox/cmd_analyze.py`、`feishu/cmd_calendar_create.py`、`feishu/cmd_calendar_delete.py`、`feishu/cmd_notify.py`
- **DB + 外部（CV 录入）**：`intake/cmd_ingest_cv.py`、`intake/cmd_attach_cv.py`（需要 `--confirm`）、`intake/cmd_new_candidate.py`、`intake/cmd_import_candidate.py`
- **仅外部（发飞书 / 邮件）**：`intake/cmd_send_cv.py`、`common/cmd_interview_reminder.py`
- **DB + 外部（面试 / 笔试结果）**：`interview/cmd_result.py`、`exam/cmd_exam_result.py`
- **DB + 外部（破坏性）**：`talent/cmd_delete.py`、`common/cmd_remove.py` — 见 §2.3

**多步 chain 的 confirm 语义**：当老板一个请求需要拼 chain（例如 §5.3 候选人改期 = `feishu.cmd_calendar_delete` + `outbound.cmd_send` + `talent.cmd_update`），按 §2.2.1 **propose 整条 chain**（所有 Step 都列出来）；老板一次 confirm = 授权整条 chain（chain 是一个语义单元，不再每步单独 confirm）。但**跨场景**仍要分开 confirm：例如"改期" chain 跑完后，老板再说"再发简历给老板"，那是新指令，必须重新 propose。

### 2.2.1 执行前 confirm 协议（强制）

跑任何 §2.2 命令前，按这个回路走：

1. **解析**出唯一 `talent-id`（走 §3）以及所有必需参数，包括 `--time` 用 `YYYY-MM-DD HH:MM` Asia/Shanghai。
2. **Propose**：把完全解析后的命令逐字写在代码块里给用户看，包含：
   - `uv run python3 scripts/...` 完整调用（每个参数都填好），
   - 候选人姓名 + `talent-id` + 当前 stage（必要时先跑一次只读查询），
   - 一行说明：这条命令会做什么（改 DB、发邮件、推飞书等）。
3. **等待**用户在下一轮显式 confirm。可接受的肯定词：`yes` / `ok` / `go` / `执行` / `确认` / `好`（以及同义表述）。沉默、追问、切换话题都算"未确认"。
4. **收到 confirm 后再执行**，且只执行展示过的那条命令。如果用户在回复里改了参数，重新 propose 更新后的命令，回到第 3 步。
5. **绝不批量**。一次 confirm 只授权一条命令。多步流程（如 search → finalize → result）的每一个写步骤都要新的 confirm。
6. **chain-兜底规则（v3.5.4）**：如果老板的指令需要拼 chain，但你**找不到匹配的 §5.x 范式**，**绝不**自己拼一个新 chain 凑合上。正确做法是 **stop and ask**——在飞书里回报老板："我没找到匹配的 chain，是否走 §5.9 force-jump 一步推到 stage X？或者您能澄清一下需要发什么邮件 / 走哪一轮？"**禁止**：(a) 拿 §5.x 里某个 chain "改改参数" 凑合；(b) 用多个 atomic CLI 试错式拼接；(c) 通过看 CLI 错误信息迭代修正参数。错的 chain 一旦执行，邮件 / 日历是不可逆的。

例外：**没有**。该协议即便对"显而易见"的场景（如 `cmd_result.py ... --result pass`）、长链路招聘流程中的每一步、用户请求里已经给出完整命令的情况，**同样**适用。

**关键纠错（v3.5.4，由 2026-04-21 17:06 事故触发）**：当老板说"直接跳到 X""直接进 X 阶段""略过 / 跳过 / 强制"之类**带跨 stage 跳跃语义**的指令时，**唯一**正确路径是 [AGENT_RULES.md §5.9 force-jump 单步 chain](../skills/recruit-ops/docs/AGENT_RULES.md)（`talent.cmd_update --stage <target> --force --reason "boss原话: …"`），**不发邮件、不建日历、不更新业务字段**。识别规则见 [AGENT_RULES.md §3.3](../skills/recruit-ops/docs/AGENT_RULES.md)。**绝不**走"先按正常流程推到 X"的路径——那会真发候选人邮件，无法撤回。

### 2.3 破坏性（§2.2 confirm 的严格超集）

这一类命令要么永久销毁数据，要么把候选人推到拒收终态。它们要求满足 §2.2.1 的全部条件**外加**：

- 泛泛的"yes / ok / 好"**不够**。confirm 必须显式指名破坏动作——例如 `"是，删除 t_xxx"`、`"confirm reject_delete for t_xxx"`、`"yes, remove 张三 (t_xxx)"`。
- confirm 必须与 propose 在**同一轮**；**绝不**依赖上一轮的意图残留。
- 如果用户回复是肯定的但没有指名破坏动作，重新问一遍。

命令清单：

- `talent/cmd_delete.py` — v3.5 唯一物理删档路径；自动归档完整 snapshot + emails。
- `common/cmd_remove.py` — 历史删档命令；与 `talent/cmd_delete.py` 等价，agent 优先用后者。
- `interview/cmd_result.py ... --result reject_delete` — 拒信 + 从人才池移除。**副作用（自 2026-04-22）**：自动先发 `rejection_generic.txt` 拒信再删人。`--skip-email` **仅在**老板已线下手发拒信时使用。
- `exam/cmd_exam_result.py ... --result reject_delete` — 同上，笔试分支。
- `intake/cmd_attach_cv.py` — 要求 CLI 上带 `--confirm` **并且**同一轮还要有一次自然语言的匹配 confirm。

**单值规则**：若用户**没有**明确说要删，唯一允许的"拒"是 `reject_keep`（一面场景则保留在当前 stage）。**绝不**默认选 `reject_delete`。

### 2.4 自动拒（系统驱动，仅笔试超时）

> **2026-04-23 简化**：此前"12h 软缓冲 + 老板可取消队列"那套架构已全部移除。agent **不应**自己调用任何 auto_reject 命令——现在只剩一个 cron 驱动的脚本，没有任何面向老板的命令。

单一触发，立即动作：

- `auto_reject.cmd_scan_exam_timeout`（cron 任务 5）每次 cron tick 都跑。对于每个在 `EXAM_SENT`、`exam_sent_at` ≥ `--threshold-days`（默认 3）且 `exam_sent_at` 之后无入站邮件的候选人，立即：
  1. subprocess 调 `outbound.cmd_send --template rejection_exam_no_reply` 发拒信；
  2. subprocess 调 `talent.cmd_delete` 删候选人；
  3. 推一条**事后**飞书通知卡（"[自动拒删 · 已执行]"）给老板。

若第 1 步失败则候选人**不**被删；失败计数上报，并触发飞书告警。"迟到改期"的自动拒已彻底移除：任何改期意图都走 §4.2 / §5.3 chain（`feishu.cmd_calendar_delete` → `outbound.cmd_send --template reschedule` → `talent.cmd_update`），由老板决策。

**`auto_reject/` 当前只有一个脚本**：`cmd_scan_exam_timeout.py`（cron 专用；agent 只跑 `--dry-run` 预览）。没有 propose / cancel / execute_due / list / pending_store / llm_classify 之类的「队列」概念，也没有 12h 缓冲窗口或「合法改期白名单」。

如果老板要手动拒 + 删候选人，用 §2.3 破坏性命令 `interview/cmd_result.py ... --result reject_delete`（现会自动先发 `rejection_generic.txt` 再删；仅在拒信已线下手发时传 `--skip-email`）。

---

## 3. 歧义解析规则

大多数线上事故来自对"信息不全的请求"直接动手。选任何写类命令**之前**，应用这组规则。

| 缺失 / 歧义 | 要求的解析方式 |
|---|---|
| 身份（只有姓名，没有 `talent-id`） | 跑 `common/cmd_search.py --query <name>`，用返回的 `talent-id`。 |
| 搜索结果多条命中 | 把匹配项都列给用户，问他指哪一个。**绝不**按字母序、时间新鲜度、或直觉挑。 |
| 代称（`他`、`她`、`上周那个候选人`、`那个女生`） | 仅当同一轮里已经锁定唯一候选人时才接受。否则去 search。 |
| 面试 round 未指定 | 问。不要仅凭 stage 推断，除非 stage 唯一决定 round（`ROUND1_*` → 1，`ROUND2_*` → 2）。 |
| 时间是自然语言（`明天下午三点`） | 解析为显式的 `YYYY-MM-DD HH:MM`，时区 **Asia/Shanghai (+08:00)**——这是 `scripts/lib/core_state.py` 打时间戳时用的服务器硬时区。在回复里把解析后的时间原样 echo 回去。 |
| `--result` 未指定（pass / reject_keep / reject_delete / pass_direct） | 问。不要默认。 |
| "拒" 但没有 keep / delete 指示 | 见 §2.3：`reject_delete` 要求同轮显式 confirm；否则用 `reject_keep`。 |

**硬规则**：任何写类命令必须 (a) 锁定**唯一** `talent-id`，(b) 通过 §2.2.1 的 confirm 协议才能执行。如果输入里没有给出唯一 `talent-id`，先解析身份——再 propose 并等待。

---

## 4. 意图路由

一张统一表。组名（`intake/`、`round1/`、`interview/`、`exam/`、`common/`）都是 `scripts/` 下的目录。

### 4.1 候选人录入

| 意图 | 命令 |
|---|---|
| HR 发 `【新候选人】` 文本模板 | `uv run python3 scripts/intake/cmd_new_candidate.py --template "<raw multi-line message>"` |
| `【导入候选人】` 历史候选人 | `uv run python3 scripts/intake/cmd_import_candidate.py --template "<raw multi-line message>"` |
| CV 附件（PDF / DOCX）— **自动触发**，见 §4.1.1 | `uv run python3 scripts/intake/cmd_ingest_cv.py --file-path <path> --filename <filename>` |
| 把 CV 挂到已有候选人（`cmd_ingest_cv` 预览之后） | `uv run python3 scripts/intake/cmd_attach_cv.py --talent-id <id> --cv-path <path> --confirm [--field key=value ...]` |
| 把 CV PDF 发给老板（默认）或 HR | `uv run python3 scripts/intake/cmd_send_cv.py --name "<name>" [--to boss\|hr]` *（默认 `boss`；`--to hr` 发给 HR）* |

注意：
- 多行 `--template` 要传真带换行的字符串。bash 里用 heredoc：`--template "$(cat <<'EOF' ... EOF)"`，或 `$'line1\nline2'`。**不要**传双引号包裹的字面量 `"\n"`——bash 不会展开它。
- `cmd_parse_cv.py` 已**废弃**。不要再用。
- Boss 说"看简历 / 把某某的简历发过来"——用 `cmd_send_cv.py` **不带** `--to`（默认就是 boss）。只有明确要求发给 HR 时才带 `--to hr`。

### 4.1.1 CV 自动检测与路由

当飞书群里来了一条带附件的消息，先判断它是不是候选人 CV，**再**决定跑什么。

**Step 1 — 这是不是一份 CV？** 如果以下任一条件满足，就按 CV 处理：

- 文件名匹配 CV 形态：岗位名 / 城市 / 薪资 + 候选人姓名 + `XX年应届生` 或 `实习生`（例如 `量化研究员实习-上海-500元_天-张三-2026年应届生.pdf`）。
- 打开文件正文后，包含多项：岗位、候选人姓名、`应届生` / `实习生`、邮箱地址、电话、学校 / 学历。
- 消息上下文明确把这份文件定性为简历（HR 在附件旁边说"这是简历" / "新候选人" / "请入库"）。

以上都不满足则**不**路由到 `cmd_ingest_cv.py`。让 HR 澄清，或走通用附件处理。

**Step 2 — 从 Hermes Gateway 消息里拆出文件路径**（按优先级排）：

| 优先级 | 入站消息形态 | 传给命令的参数 |
|---|---|---|
| 0 | `[The user sent a document: 'xxx.pdf'. The file is saved at: /.../xxx.pdf ...]` | `--file-path "/.../xxx.pdf" --filename "xxx.pdf"` |
| 1 | `[media attached: <absolute-path>.pdf]` | `--file-path "<absolute-path>.pdf" --filename "<basename>"` |
| 2 | 回复 / 引用带 `file_key` 的消息 | 先在 `<workspace_root>/data/media/inbound/` 下按 key / name 找本地文件；找到用 `--file-path`；否则 `--file-key <key>` |

Hermes 消息给的路径已经是绝对路径。**原样**传过去，不要改写成 `<workspace_root>`。

**Step 3 — 跑 `cmd_ingest_cv.py`**（自动触发，见 §2.1.5）。把输出原样转发给 HR，然后等 HR 回复再做下一步（§9.3 说明两种可能的输出形态）。

### 4.2 面试操作

| 意图 | 路由 |
|---|---|
| **HR**（v3.6 起 NEW 阶段一面派单的唯一 happy path）说"t_xxx 一面时间是 …" / "安排 X 一面，时间 …"（HR 已 ingest CV、`talents.education / has_cpp` 已填，agent 自动派单） | **chain（[AGENT_RULES.md §5.11](../skills/recruit-ops/docs/AGENT_RULES.md)，v3.5.7）**：(1) `intake.cmd_route_interviewer --talent-id <id> --json`（先派单，**绝不**自己算 open_id；ambiguous=true 或 config_error=true 必须 STOP 转 ASK_HR）→ (2) `outbound.cmd_send --template round1_invite --vars round1_time=… --json` → (3) `feishu.cmd_calendar_create --talent-id <id> --time "…" --round 1 --duration-minutes 30 --candidate-name … --candidate-email … --extra-attendee {route.interviewer_open_ids[*]} --json` → (4) `talent.cmd_update --stage ROUND1_SCHEDULED --set round1_time=… --set round1_invite_sent_at={send.sent_at} --set round1_confirm_status=CONFIRMED --set round1_calendar_event_id={cal.event_id}` → (5) `feishu.cmd_notify --to interviewer-{role} …` ×N → (6) `feishu.cmd_notify --to boss --severity info --title "一面已排"`。**关键语义**：直接进 `ROUND1_SCHEDULED`（不是 `_SCHEDULING`），日历直接建（含面试官），时长固定 30 分钟。 |
| 老板**绕开 HR**直接说"我安排 X 一面，时间 …"（NEW 阶段） | **不要自动跑 §5.1**（v3.6 起 §5.1 在 NEW 阶段已废）。先 stop and ask：「这是 HR 已 ingest 过的候选人吗？建议：① 让 HR 触发以走标准 §5.11 派单，或 ② 老板明示 'force-jump' 走 §5.9 单步。」候选人若**未** ingest CV → 先引导走 `intake.cmd_ingest_cv`。**绝不**自动拼 §5.1 chain 替老板"先发邀请等 confirm"——这会把候选人卡在 `ROUND1_SCHEDULING + PENDING` 但没派单。 |
| `WAIT_RETURN` 候选人回归后，老板说"安排 X 重新一面，时间 …"（v3.6 起 §5.1 chain 仅服务此场景） | **chain（[AGENT_RULES.md §5.1](../skills/recruit-ops/docs/AGENT_RULES.md)）**：`outbound.cmd_send --template round1_invite --vars round1_time=… position_suffix=… location=…` → `talent.cmd_update --stage ROUND1_SCHEDULING --set round1_time=… --set round1_invite_sent_at={send.sent_at} --set round1_confirm_status=PENDING --set round1_calendar_event_id=__NULL__ --set wait_return_round=__NULL__ --reason "agent: schedule round 1 (return from WAIT_RETURN)"`。propose 时把整条 chain 全列出来等 boss confirm。**注意**：进 `ROUND1_SCHEDULING + PENDING`，等候选人 confirm 后再走 §5.2 建日历。 |
| HR 说"t_xxx 派给 master/bachelor/cpp"（§5.11 ambiguous 后 HR 手动指派回话） | 重启 §5.11 chain，跳过 step 1 的派单自动决策，把 HR 指定的 role 对应的 `open_id`（从 `lib.config['feishu']['interviewer_<role>_open_id']`）当作 `route.interviewer_open_ids` 喂给 step 2 起步的 chain。**仍然不允许** agent 写硬编码的 `ou_xxx` 字符串。 |
| 候选人回信 confirm 一面 / 二面时间 | **chain（§5.2）**：`talent.cmd_update --stage ROUND{N}_SCHEDULED --set round{N}_confirm_status=CONFIRMED` → `feishu.cmd_calendar_create --talent-id <id> --round N --time … --candidate-email … --candidate-name …` → `talent.cmd_update --set round{N}_calendar_event_id={cal.event_id}` |
| 候选人 / 老板请求改期 | **chain（§5.3）**：`feishu.cmd_calendar_delete --event-id <round{N}_calendar_event_id> --reason "候选人改期"` → `outbound.cmd_send --template reschedule --vars round_label=… old_time=… new_time=… location=…` → `talent.cmd_update --stage ROUND{N}_SCHEDULING --set round{N}_time=新时间 --set round{N}_confirm_status=PENDING --set round{N}_calendar_event_id=__NULL__ --set round{N}_invite_sent_at={send.sent_at}`。**顺序不可换**——先删旧日历再发新邮件。 |
| 候选人在国外暂缓 | **chain（§5.4）**：（如已建日历，先 `feishu.cmd_calendar_delete`）→ `outbound.cmd_send --template defer --vars round_label=…` → `talent.cmd_update --stage WAIT_RETURN --set wait_return_round=N --set round{N}_time=__NULL__ --set round{N}_calendar_event_id=__NULL__` |
| WAIT_RETURN 候选人主动联系 | **chain（§5.8）**：纯通知 `feishu.cmd_notify --severity warn --title "WAIT_RETURN 候选人主动联系" --body "talent={tid} round={round}\nintent=… summary=…\n建议下一步：①talent.cmd_update --stage ROUND{N}_SCHEDULING --reason 'candidate returned'（自然推进，免 --force）②outbound.cmd_send --template round{N}_invite --vars round{N}_time=… location=…"`。**不自动改 stage**——候选人是否真能约由老板判。 |
| 记录面试结果（一 / 二面统一） | `uv run python3 scripts/interview/cmd_result.py --talent-id <id> --round 1\|2 --result pass\|pass_direct\|reject_keep\|reject_delete [--email …] [--round2-time "…"]`。⚠ **真发候选人邮件**（`--round 1 --result pass` 发笔试邀请；`--round 1 --result pass_direct` 发二面邀请；`--result reject_*` 发拒信）。**仅在老板真的走完了那一轮面试**时使用——若老板说"跳过 / 直接进 X 阶段"，**绝不**用本命令，改用下面 §5.9 force-jump。 |
| 一面已通过、笔试邮件已手工发出（仅状态推进） | `uv run python3 scripts/interview/cmd_result.py --talent-id <id> --round 1 --result pass --skip-email` |
| 老板说"直接跳到 X / 略过中间步骤 / 直接进 offer / 强制推到 Y"（跨 stage 跳跃） | **唯一路径：§5.9 force-jump 单步**（[AGENT_RULES.md §5.9](../skills/recruit-ops/docs/AGENT_RULES.md)）：`talent.cmd_update --talent-id <id> --stage <target> --force --reason "boss原话: <原话>"`。**绝不**调 `cmd_result --result pass` 或 `cmd_exam_result --result pass`（会真发邮件给候选人）。识别规则见 [AGENT_RULES.md §3.3](../skills/recruit-ops/docs/AGENT_RULES.md)。 |
| 预览候选人邮件模板 | `uv run python3 -m template.cmd_preview --template <name> --demo` *（或 `--var key=value …`；`--list` 列全部模板：`round1_invite`, `exam_invite`, `round2_invite`, `reschedule_ack`, `reschedule`, `defer`, `rejection_generic`, `rejection_exam_no_reply`）* |

**邮件模板**：6 个候选人模板（`round1_invite` / `exam_invite` / `round2_invite` / `reschedule_ack` / `reschedule` / `defer`）+ 2 份拒信（`rejection_generic` / `rejection_exam_no_reply`），渲染源在 `scripts/email_templates/*.txt`。**v3.5 起所有 chain 通过 `outbound.cmd_send --template <name> --vars k=v …` 调用**，不再有任何脚本里的 `_send_xxx_email()` 薄包装（它们在 v3.5 全部删除，连同它们所属的 `cmd_round1_schedule` / `cmd_reschedule` / `cmd_defer` / `cmd_reschedule_request` 脚本一起）。改文案直接编辑 .txt；变量缺失会 `KeyError`（立即失败，防 2026-04-20 那次"字面量 `$candidate_name` 漏发"事故再现）。`round1_invite` 模板里的 3 轮流程概述（一面线下 / 二面笔试 / 三面线下）+ 实习要求（≥3 个月、每周 ≥4 天）放在排期细节**前面**是有意为之，让候选人在双方投入时间前自筛。Round 数字翻译（`round_num=1→"第一轮"`，`round_num=2→"第三轮"`）在 `email_templates/constants.py::round_label()`，**不要**在调用方里内联 `"第一轮" if round==1 else "第二轮"`。

条件必填参数（出自 [CLI_REFERENCE.md](../skills/recruit-ops/docs/CLI_REFERENCE.md)）：
- `cmd_result.py --round 1 --result pass` → `--email` **必填**，且必须是**候选人的邮箱地址**（作 exam invite 的 SMTP 收件人；同时覆盖 `talents.candidate_email`）。它**不是**邮件正文。值必须匹配正则 `^[^\s@]+@[^\s@]+\.[^\s@]+$`。找不到合法邮箱时**不要**编造或填占位字符——停下来问。`--skip-email` **仅在**老板已线下手发笔试邮件时使用。
- `cmd_result.py --round 1 --result pass_direct` → `--round2-time` **必填**。
- `cmd_result.py --round 2 --result pass` → 无额外参数；**一步**推到 `POST_OFFER_FOLLOWUP` + 同步通知 HR 飞书（v3.6 起瞬时态 `OFFER_HANDOFF` 已合并下线）。
- `--skip-email` 只对 `--round 1 --result pass` 有效；语义是"operator 已线下发笔试邮件，只推进 state"。

### 4.3 笔试操作

| 意图 | 路由 |
|---|---|
| 笔试通过 → 安排二面（CLI 一步打包） | `uv run python3 scripts/exam/cmd_exam_result.py --talent-id <id> --result pass --round2-time "YYYY-MM-DD HH:MM"`。⚠ **真发 round2_invite 邮件给候选人**——**仅在老板**明确给出二面时间且要求安排二面**时用。若老板说"直接进 offer""不要二面，直接发 offer"，**绝不**用本命令（也**绝不**为了过 `--round2-time` 必填校验而编一个时间），改走 §5.9 force-jump。 |
| 笔试通过 → 安排二面（agent chain，与上等价的手工路径） | **chain（[AGENT_RULES.md §5.5](../skills/recruit-ops/docs/AGENT_RULES.md)）**：`outbound.cmd_send --template round2_invite --vars round2_time=… location=…` → `talent.cmd_update --stage ROUND2_SCHEDULING --set round2_time=… --set round2_invite_sent_at={send.sent_at} --set round2_confirm_status=PENDING --set round2_calendar_event_id=__NULL__ --set wait_return_round=__NULL__`。**两条路径不要同一封邮件叠加触发**。同样**仅当老板要安排二面时**才用——跨 stage 跳跃走 §5.9。 |
| 老板说"笔试通过，直接进 offer" / "跳过二面" / "不需要二面，直接结束流程" | **唯一路径：§5.9 force-jump 单步**（[AGENT_RULES.md §5.9](../skills/recruit-ops/docs/AGENT_RULES.md)）：`talent.cmd_update --talent-id <id> --stage POST_OFFER_FOLLOWUP --force --reason "boss原话: …"`。**绝不**调 `exam.cmd_exam_result --result pass`（会真发二面邀请），**绝不**为了过 stage-gate 而拼"先安排二面 → 假装 confirm → 二面 pass"的伪 chain（这是 2026-04-21 17:06 真实事故）。 |
| 笔试不过（保留人才池）— CLI 一步 | `uv run python3 scripts/exam/cmd_exam_result.py --talent-id <id> --result reject_keep` |
| 笔试不过（保留池）— agent chain | **chain（§5.7）**：`outbound.cmd_send --template rejection_generic` → `talent.cmd_update --stage EXAM_REJECT_KEEP --reason "agent: exam reject keep (per boss decision)"` |
| 笔试拒 + 删档 | `uv run python3 scripts/exam/cmd_exam_result.py --talent-id <id> --result reject_delete` *（§2.3 破坏性；自动先发 `rejection_generic` 再删人）* |
| AI 笔试评审（advisory only） | `uv run python3 scripts/exam/cmd_exam_ai_review.py --talent-id <id> [--feishu --save-event] [--rerun]` *（自动从 IMAP 拉最新提交并缓存 `/tmp/exam_submissions/<id>/`）* |
| Boss 说"审阅 / 评审 / 看一下 X 的笔试" | 解析唯一 `talent-id`，**两步 propose**：(1) `cmd_exam_ai_review.py --talent-id <id>`（无 `--feishu` / `--save-event`，纯终端预览）→ wait for confirm → 执行；(2) Boss 看完报告后，**再** propose 同一条加 `--feishu --save-event` 推飞书 + 写 `talent_events.action='exam_ai_review'`。CLI 自动从 IMAP 拉最新提交，无需先跑别的脚本。 |
| 候选人邮件时间线（inbound + outbound） | `uv run python3 -m inbox.cmd_review --talent-id <id>` *（`talent_emails` 是 single source of truth；显示 AI intent / template / analyzed_at）* |
| Boss 在 POST_OFFER_FOLLOWUP 飞书卡片上点"一键发" | **chain（§5.6）**：`outbound.cmd_send --talent-id <id> --use-cached-draft <email_id>` → `feishu.cmd_notify --severity info --title "已发送 Offer 跟进回复"`。draft 不存在时第一步必失败（rc=2，stderr `没有 draft 字段`），整条 chain 短路——agent 应改推 `--severity warn` "草稿缺失" 卡。 |
| 让候选人 follow-up 邮件 snooze / dismiss | 直接 `talent_db.mark_email_status(email_id, status='dismissed'\|'snoozed', snoozed_until=...)` 改 `talent_emails.status`（无独立 CLI——这是 v3.5 简化的部分）。 |
| Boss 说"给 X 发 onboarding offer / 录用通知 / 入职邮件" | **chain（[AGENT_RULES.md §5.10 + §10](../skills/recruit-ops/docs/AGENT_RULES.md)）**：`outbound.cmd_send --template onboarding_offer --vars position_title=… interview_feedback=… daily_rate=… onboard_date=… location=… evaluation_criteria=…` → `feishu.cmd_notify --to hr --severity info --title "新候选人 offer 已发，请准备入职" --body "candidate=… 入职日期=… 薪资=… 已附：实习协议+登记表"`。**v3.5.10 起两份附件（《致邃实习协议》+《实习生入职信息登记表》）由 `email_templates.auto_attachments` 自动追加，agent 不要再手动 `--attach`**；文件缺失会立即失败拒发。**HR 走飞书不在邮件 cc 里**。`onboard_date` / `daily_rate` 老板没明说时**先 stop and ask**，不要默认 350 自作主张。 |
| 笔试超时自动拒删（**不用 agent 介入**） | cron 自动跑 `auto_reject.cmd_scan_exam_timeout`：发 `rejection_exam_no_reply` → `talent.cmd_delete` → 推飞书事后告知。Agent 仅 `--dry-run` 预览：`uv run python3 -m auto_reject.cmd_scan_exam_timeout --dry-run` |

**入站邮件统一管线**（v3.5 起）：
- `inbox.cmd_scan` 一次扫所有候选人，写入 `talent_emails(direction='inbound', analyzed_at=NULL)`
- `inbox.cmd_analyze` 按 stage 选 prompt（`prompts/inbox_general.json` vs `prompts/post_offer_followup.json`），写 `ai_payload` 并推飞书卡
- 不再有 `daily_exam_review.py` / `followup_scanner.py` 各自扫一遍——也就**没有** `data/followup_pending/` 这个文件队列了，消息状态全在 `talent_emails.status` 里

**POST_OFFER_FOLLOWUP 简化**（v3.5 → v3.6）：
- v3.5：候选人通过二面 → `interview/cmd_result.py --round 2 --result pass` 通过 1-tick 瞬时态 `OFFER_HANDOFF` + `set_current_stage()` 推到 `POST_OFFER_FOLLOWUP`。**不再调** `enter_post_offer_followup` 函数；`followup_status` / `followup_entered_at` / `followup_last_email_id` / `followup_snoozed_until` 字段已 DROP（v3.5.2 migration `20260421_v35_drop_dead_columns.sql`）。
- v3.6：瞬时 stage `OFFER_HANDOFF` 已彻底删除——`interview/cmd_result.py --round 2 --result pass` 现在一步（`ensure_stage_transition` allowed_from={ROUND2_SCHEDULED} → POST_OFFER_FOLLOWUP）推到最终态，HR 飞书通知照旧发。见 migration `20260427_v36_drop_offer_handoff.sql`。
- 候选人后续来信由 `inbox.cmd_scan` + `inbox.cmd_analyze` 处理，AI 草稿写在 `talent_emails.ai_payload.draft`。
- Boss 在飞书卡片上一键发 → §5.6 chain。
- 关闭 / snooze / dismiss 不再有独立 CLI：直接 `talent_db.mark_email_status(...)` 改 `talent_emails.status`。

**注意事项**：
- `--round2-time` 在 `cmd_exam_result.py --result pass` 时**必填**。脚本拒绝复用旧时间。
- `cmd_scan_exam_timeout` 仅 cron 用：agent 不要在响应单封邮件时跑（会和 cron 撞车，双发拒信）。
- **AI 笔试评审（rubric-based）是 advisory only**。评审器：
  - 读 `skills/recruit-ops/exam_files/rubric.json`，输出结构化 score + reasons + next-step；
  - 由 `lib/exam_grader.py` 与 `lib/exam_imap.py` 实现（v3.5 起替代旧 `exam/exam_ai_reviewer.py`）；
  - 自动从 IMAP 拉最新提交并缓存 `/tmp/exam_submissions/<id>/`，自动从邮件 `Date` header 填 `submitted_at`；
  - **缓存 LLM verdict** 在 `/tmp/exam_submissions/<id>/_ai_review_result.json`——推荐的两步流程（先终端预览，再 `--feishu --save-event`）只付一次 LLM 钱。`--rerun` 强制重跑（候选人重交时）。`--refetch` 强制重拉 IMAP；`--code-dir <path> --no-fetch` 用本地目录跳 IMAP；
  - **永远不**推进 stage，**永远不**输出 `pass / fail / 录取 / 拒绝 / 淘汰 / 建议通过 / 建议拒绝`——这些 token 被 `lib/exam_grader.py` 后处理 scrub 掉；
  - 结果写 `talent_events` action `="exam_ai_review"`（手动跑 actor=`manual_review`）。
- 老板读 AI 评审报告时，**agent 不得**把 AI 分数转述成 pass/fail 建议。永远把决策权交还给老板；下一步是 `cmd_exam_result.py` 或 §5.5 / §5.7 chain。

### 4.4 自动拒（系统驱动，仅笔试超时）

| 意图 | 命令 |
|---|---|
| "看看有没有谁会被自动拒" / "auto_reject 会拒谁" | 只读：`uv run python3 -m auto_reject.cmd_scan_exam_timeout --dry-run` |
| "调一下自动拒的天数阈值"（如改成 5 天） | 只读预览：`uv run python3 -m auto_reject.cmd_scan_exam_timeout --dry-run --threshold-days 5`（生产阈值在 cron 任务参数里改） |
| "为什么 X 被自动拒了" | 只读：`inbox/cmd_review --talent-id <id>` 看他邮件时间线（`exam_sent_at` 后 ≥ 3d 无入站）+ `talent/cmd_show --talent-id <id>` 看审计历史，含 `data/deleted_archive/<YYYY-MM>/` 归档。 |
| "把 X 手动拒了，删人" | §2.3 破坏性——propose `uv run python3 scripts/interview/cmd_result.py --talent-id <id> --round <N> --result reject_delete`（会在删人前先发 `rejection_generic.txt`；仅在拒信已线下手发时传 `--skip-email`）。**不要**编造 `cmd_propose --reason manual`——该命令已不存在。 |

注意事项：
- Agent **永远不**在 `--dry-run` 之外调用 `auto_reject.cmd_scan_exam_timeout`。cron runner 是唯一合法调用方；手动真跑会和 cron 竞争，双发拒信。
- **不再有老板取消窗口**——cron 一跑，满足条件的候选人在同一 tick 就被删。老板若要阻止某次自动拒，运维操作是在下一次 cron tick 前把 `cron/cron_runner.py::_TASKS` 里的 `exam_timeout_scan` 注释掉。
- "迟到改期" **永远不**触发自动拒。改期邮件走 §5.3 chain：`inbox.cmd_analyze` 分类出 `reschedule_request` → `feishu.cmd_notify` 推老板 → 老板拍新时间 → §5.3 chain（`feishu.cmd_calendar_delete` + `outbound.cmd_send --template reschedule` + `talent.cmd_update`）执行。

### 4.5 查询

| 意图 | 命令 |
|---|---|
| 所有候选人 | `uv run python3 scripts/common/cmd_status.py --all` |
| 单个候选人完整详情 | `uv run python3 scripts/common/cmd_status.py --talent-id <id>` |
| 关键字搜索 | `uv run python3 scripts/common/cmd_search.py --query <keyword>` |
| 仅活跃 / 进行中候选人 | `uv run python3 scripts/common/cmd_search.py --all-active` |
| 今天的面试 | `uv run python3 scripts/common/cmd_today_interviews.py` |
| 指定某天 | `uv run python3 scripts/common/cmd_today_interviews.py --date YYYY-MM-DD` |
| 仅已确认的排期 | `uv run python3 scripts/common/cmd_today_interviews.py --confirmed-only` |
| 待补的面试结果催问 | `uv run python3 scripts/common/cmd_interview_reminder.py` |
| 看 X 的简历 / 笔试附件 / 候选人发的文件（v3.5.8） | (a) **CV** 直接看 `talents.cv_path`（绝对路径，`data/candidates/<tid>/cv/<原文件名>`）。(b) **邮件附件 / 笔试答案**：`psql` 查 `talent_emails.attachments WHERE talent_id=<id>`，得到 `path`（相对 `data_root()`），完整路径 = `data/<path>`，常见落点 `data/candidates/<tid>/exam_answer/em_<eid>/...` 或 `data/candidates/<tid>/email/em_<eid>/...`。(c) `exam.fetch_exam_submission` 的**手动下载缓存**（含解压 + AI 评审 JSON）位于 `data/candidates/<tid>/exam_answer/legacy_fetch/`。**不要**主动再跑 `exam.fetch_exam_submission`——`inbox.cmd_scan` 已自动落盘。 |

---

## 5. 查询规则

把用户意图映射到**最窄的**规范命令。不要用更宽的命令重新推导出一个派生视图。

| 用户意图 | 命令 | 不要这么做 |
|---|---|---|
| "把所有人都列一下" / "所有候选人" | `cmd_status.py --all` | 重新分桶。改计数。凭 stage 文本推测活跃 / 不活跃。 |
| "谁还在进行中？" / "活跃候选人" | `cmd_search.py --all-active` | 跑 `cmd_status.py --all` 再自己猜哪些 stage 算活跃。 |
| "X 的面试什么时候？" | `cmd_search.py --query <name>` → 若唯一，`cmd_status.py --talent-id <id>` | 跳过 search 直接猜 ID。 |
| "今天 / 明天 / X 号有没有面试？" | `cmd_today_interviews.py [--date ...]` | 用 `--all-active` 重建。 |

**`cmd_status.py --all` 的分组护栏**：

- 默认行为：原样返回命令输出的平铺列表。
- 用户明确要求分组汇总时，只按**精确的 current stage 标签**分组，**不要**自创更宽的桶。
- 永远**不要**把任何 `*_DONE_*` stage 放到 `一面阶段`、`二面阶段`、`笔试阶段` 这类进行中桶里。
- 具体来说：`ROUND2_DONE_REJECT_KEEP / 二面未通过（保留）` **不是** `二面阶段` 的一部分；`EXAM_REJECT_KEEP / 笔试未通过（保留）` **不是** `笔试阶段` 的一部分（它是"保留在人才池"的终态）。
- 不确定某个 stage 是进行中还是终态时，逐字引用 stage 标签然后停下。**不要**自创桶。

---

## 6. Stage 解释

规范来源：`scripts/lib/core_state.py`（`STAGE_LABELS`）。下表是速查镜像。

### 6.1 活跃 stage

| Stage | 标签 | 含义 |
|---|---|---|
| `NEW` | 新建 | 候选人已建档，尚未做任何动作 |
| `ROUND1_SCHEDULING` | 一面排期中 | 一面邀请邮件已发，等候选人确认 |
| `ROUND1_SCHEDULED` | 一面已安排 | 一面已确认 |
| `EXAM_SENT` | 笔试已发送 | 笔试已发，等提交 |
| `EXAM_REVIEWED` | 笔试已审阅 | 笔试已审，等下一步 |
| `WAIT_RETURN` | 待回国后再约 | 候选人回国前暂停 |
| `ROUND2_SCHEDULING` | 二面排期中 | 二面协商中 |
| `ROUND2_SCHEDULED` | 二面已确认 | 二面已确认 |
| `POST_OFFER_FOLLOWUP` | 已结束面试流程，等待发放 Offer / 沟通入职 | v3.6 合并了原 `OFFER_HANDOFF` 的语义：`cmd_result.py --result pass --round 2` 一步把 stage 推到此处 + 通知 HR 发 offer。Boss 通过 Hermes 与候选人沟通 offer / 入职日 / 薪资。`inbox.cmd_scan` + `inbox.cmd_analyze` 自动抓邮件并按 `prompts/post_offer_followup.json` 生成草稿（写 `talent_emails.ai_payload.draft`），boss 在飞书卡片上一键发触发 §5.6 chain（`outbound.cmd_send --use-cached-draft …`）。**v3.5 起 `followup_*` 字段已 DROP**——此 stage 没有任何 followup 状态机，只看 `talent_emails.status`。 |

### 6.2 终态 / done stage — **不算进行中**

这些是结束状态。**永远不要**把它们呈现成"还在那个 stage"。

| Stage | 标签 | 含义 |
|---|---|---|
| `EXAM_REJECT_KEEP` | 笔试未通过（保留） | 笔试未过但候选人**保留**在池内，供后续再激活 |
| `ROUND2_DONE_REJECT_KEEP` | 二面未通过（保留） | 二面未过，保留在池内 |

> 当前 stage 集合就是上方 §6.1 + §6.2 表所列的 11 个。如果你记忆里浮现 `*_DONE_PASS` / `*_DONE_PENDING` / `*_DONE_REJECT_KEEP` / `*_DONE_REJECT_DELETE` / `OFFER_HANDOFF` 这种带 `_DONE_` 或 `OFFER_HANDOFF` 字样的 stage 名，**那是过时的**——以 §6.1 / §6.2 为准。当前语义：
> - 一 / 二面 pass = 直接跳到下阶段，没有中间 done 态。
> - 二面「待定」= 就停在 `ROUND2_SCHEDULED`，等老板拍板。
> - 二面 pass = `cmd_result --round 2 --result pass` 一步推到 `POST_OFFER_FOLLOWUP` + 通知 HR 飞书。
> - `--result reject_delete` 就是物理删（发拒信 + `talent_db.delete_talent()`），不留残留 stage。

**规则**：处于 `ROUND2_DONE_REJECT_KEEP` 的候选人**不是**"还在二面"。他们已经结束，被保留在人才池里。

### 6.3 自动拒只有「即触即终」一种语义

`auto_reject.cmd_scan_exam_timeout` 要么在同一 cron tick 里完成「发拒信 + 推 `EXAM_REJECT_KEEP`」（成功），要么把候选人留在 `EXAM_SENT` 并告警（失败）。**没有「排队待自动拒」的中间态**，也没有缓冲窗口可以 cancel。详见 §2.4。

---

## 7. 写操作前置条件

跑任何 §2.2 或 §2.3 命令前，验证：

1. 已解析出**唯一** `talent-id`（走 §3）。
2. 条件必填参数齐全（见 §4.2 / §4.3 注释）。
3. 时间为 `YYYY-MM-DD HH:MM`、Asia/Shanghai (+08:00)、不是过去时间、也不是之前已被拒过的时间。
4. 候选人当前 stage 允许这个操作。
5. **已走完 §2.2.1 confirm 协议**——解析后的命令在上一轮已经展示过，用户在本轮显式给了肯定（对 §2.3 命令，肯定必须指名破坏动作）。

### 7.1 Stage-gate 策略——以代码为准

CLI 本身会强制 stage 转换规则。skill **不**重新实现状态机。下表是**提示集**，不是权威规范：

| 操作 | 典型允许 stage（以代码为准） |
|---|---|
| 一面排期 | `NEW`、`ROUND1_SCHEDULING` |
| 一面结果 | `ROUND1_SCHEDULING`、`ROUND1_SCHEDULED` |
| 笔试结果 | `EXAM_SENT`、`EXAM_REVIEWED` |
| 二面排期 / 最终化 | `EXAM_REVIEWED`，或一面 `pass_direct` 之后 |
| 二面结果 | `ROUND2_SCHEDULING`、`ROUND2_SCHEDULED` |

规则：
- 不确定当前 stage 是否允许动作时，先跑 `common/cmd_status.py --talent-id <id>`，让 CLI 自己的 validator 拦住非法转换。
- 代码与本表冲突时，**以代码为准**。应该去更新 skill，而不是反过来。
- **绝不**发明 CLI 未明确支持的"仅状态"转换（例如 §4.2 里的 `--skip-email` 是个被支持的仅状态转换；任意 stage 上没有类似出口）。

前置条件任一不满足，**不要**跑命令。原样回显 CLI 的诊断并建议下一步合法动作（通常是"先 `cmd_status.py --talent-id ...` 看当前阶段"）。

---

## 8. 失败处理

把每一次非零退出或错误归到四桶之一，对应回应。

| 分类 | 识别 | 正确下一步 |
|---|---|---|
| **Not found** | `ERROR: 未找到候选人` / search 空结果 | 让用户澄清身份。建议一条 `cmd_search.py` 查询。 |
| **Ambiguous** | search 返回 >1 条 | 把匹配项列出来（姓名 + `talent-id` + stage）让用户选。**不要**动手。 |
| **Invalid state / args** | `ERROR: 当前阶段不允许` / `argparse` 用法错 / 约束违反 | 原样上报。给出正确命令或能揭示当前状态的查询。**永远不要**盲目改参数重试。 |
| **Infra / transient** | DB 连接错、飞书 API 错、IMAP 错、含网络关键词的 traceback | 报成基础设施级故障。**不**建议重试业务命令；建议去查配置 / 连通性。 |
| **chain 中间一步失败** | run_chain 短路；`chain_result["ok"]=False`；`chain_result["failed_step"]=…` | 详见 [AGENT_RULES.md §6](../skills/recruit-ops/docs/AGENT_RULES.md)。常见模式：`outbound.cmd_send` 成功但 `talent.cmd_update` 失败 → 邮件已发出、DB 未推进 → `feishu.cmd_notify --severity critical --title "邮件已发但状态未更新"` 附 talent_id + sent_at；老板手动 `talent.cmd_update` 补救。**不**自动回滚（不可逆）。 |
| **`outbound.cmd_send --use-cached-draft` 失败：没有 draft 字段** | rc=2；stderr `没有 draft 字段` | 这是 `inbox.cmd_analyze` 在该 stage 没生成草稿（intent 不在 `prompts/post_offer_followup.json` valid_intents 里，或 LLM 限流），改推 `feishu.cmd_notify --severity warn --title "草稿缺失，需手动起草"`。 |
| **auto_reject scan: send failed** | `auto_reject.cmd_scan_exam_timeout` stderr 出现 `⚠ 发拒信失败: ...` 且 `failed=N` | 候选人**没**被删（故意——failure isolation）。`cli_wrapper` 已推飞书告警。排查 SMTP / 模板，让 cron 下个 tick 重试。**不要**手动跑 scanner 真跑（会和 cron 撞车）。 |

**永远不**把失败用乐观口吻包装。命令失败了，用户状态就是没前进。

---

## 9. 结果呈现

保留命令语义。精度要紧时，优先原样引用命令措辞，而不是转述。

**通用规则**：回复里只能包含命令**真正返回**的字段。**绝不**添加"上次动作"、"下一步"、"待办"或任何命令没输出的派生信号。调用方要的字段命令没返回时，跑一条更具体的命令（如 `cmd_status.py --talent-id <id>`）——不要推断。

### 9.1 候选人列表

列表响应里每一行，**精确**包含查询命令实际返回的字段。至少包括：
- 显示名
- `talent-id`
- 精确 stage 标签（中英双语可接受：`ROUND2_DONE_REJECT_KEEP / 二面未通过（保留）`）

附加字段（排期时间、确认状态、邮箱等）**仅在**命令对该行实际返回时才包含。除非用户明确要求，否则**不要**按自定义桶分组。

用户明确要求分组时，安全顺序：

1. 按精确 stage 标签分组；
2. 只在命令本身已返回该桶，或 skill 里明确定义为无损桶时，才合并成更宽的桶；
3. 任何 `*_DONE_*` stage 优先用 `已结束`、`保留人才池`、`其他状态` 这类终态桶——**绝不**放进进行中的 round 桶。

### 9.2 单候选人详情

用 `cmd_status.py --talent-id <id>` 返回的字段回复。**不要**从 stage 合成"下一步"。用户问"接下来呢"时：
- 引用 stage 让他自己定，或
- 把 §4 里和该 stage 匹配的命令作为选项列出来。

PII 见 §10。

### 9.2.1 时间措辞护栏

**不要**给命令返回的时间字段补任何没验证过的自然语言日历描述。

包括：

- `本周日`
- `下周一`
- `明天下午`
- `周几`
- 以及任何类似的自然语言日期措辞

这些措辞**只在**满足以下之一时才允许：

1. 命令自己已经返回了这个措辞 / 字段，或
2. agent 已经通过确定性日期计算 / 真实日历查询核对过

否则，**只**重复命令返回的原始时间戳，如：

- `面试时间：2026-04-20 09:30`

不允许：

- `面试时间：2026-04-20 09:30（本周日）`
- `面试时间：2026-04-20 09:30（下周一上午）`
- `面试时间：明天下午 09:30`

如果补上了经核对的自然语言描述，原始时间戳仍须作为**主要真源**保留可见。

### 9.3 写结果

写命令成功后，只回显命令本身报告的内容：候选人姓名、`talent-id`、命令打印出来的新 stage、以及命令明确日志过的排期 / 通知确认。

### 9.4 CV 录入预览（两种分支）

`cmd_ingest_cv.py`（§2.1.5）会产出两种结构化预览之一。**把预览正文原样转发给 HR**，并把嵌入的 payload 当作下一步 §2.2 写命令的 proposal。

**分支 A — 候选人已在库（DB 匹配）**：预览里含完整字段 diff 表和两个 payload marker：

- `[OC_CMD_ON_CONFIRM_UPDATE]` — `cmd_attach_cv.py ... --confirm --field key=value ...` 命令，会写入检测到的所有改动并把 CV 归档。
- `[OC_CMD_ON_CONFIRM_ARCHIVE]` — `cmd_attach_cv.py ... --confirm` 命令，仅把 CV 归档，不改任何字段。

HR 的回复选其一：
- **"确认更新"** → 执行 `UPDATE` payload。
- **"仅存档"** → 执行 `ARCHIVE` payload。
- **"只更新 X / Y / ..."** → 取 `UPDATE` payload，移除 HR 排除掉的 `--field`，执行裁剪后的命令。
- **"把 X 改成..."** → 取 `UPDATE` payload，替换那个 `--field` 的值，**重新展示**预览，再次等待。
- **"忽略"** → 什么都不做。

**分支 B — 新候选人（无匹配）**：预览含解析后的字段和两个 marker：

- `[OC_CMD_ON_CONFIRM]` — `cmd_new_candidate.py --template ...` 命令，HR confirm 候选人确实是新人且起始 stage 为 `NEW` 时可跑。
- `[OC_NOTE]` — 一段提示，让 HR 同时 confirm 字段与期望的起始 stage。

HR 的回复选其一：
- **"修正 X=..."** → 修改对应字段，**重新展示**预览，再次等待。在 HR 看到修改后的预览并 confirm 之前**绝不**执行。
- **"确认 + 阶段 NEW"**（或等价表述）→ 执行 `[OC_CMD_ON_CONFIRM]` payload。
- **"确认 + 阶段 <其他>"**（如 `ROUND1_SCHEDULED`、`EXAM_SENT`）→ 切到 `intake/cmd_import_candidate.py --template ... --stage <stage>` 执行。
- **"不是候选人 / 忽略"** → 什么都不做。

两种分支下，下游 `cmd_attach_cv.py` / `cmd_new_candidate.py` / `cmd_import_candidate.py` 的执行依然要走 §2.2.1——HR 针对预览的显式回复**就是**那次 confirm；除非 HR 改了任何参数，否则**不再**要第二次 confirm。

---

## 10. 隐私 / PII

招聘数据是个人数据。按"最小必要披露"处理。

- 默认列表视图：`姓名 + talent-id + stage [+ 下一步]`。除非用户明确问，否则**不**附邮箱、电话、微信。
- 老板需要联系某位候选人时，**一次一个渠道**披露，优先选场景隐含的那个。
- **绝不**把完整 CV 文本、身份证号、银行相关字段贴进回复，除非用户显式要求。
- 搜索结果默认每位候选人**一行**。后续追问再展开。

---

## 11. 反模式

下列行为**都不要**做：

- 向用户要飞书链接、Bitable 链接、电子表格链接。仓库拥有完整工作流。
- 把命令输出重分成命令没返回的自定义桶。
- 把 `*_DONE_*` stage 呈现成"还在那个 stage"。
- 把 `ROUND2_DONE_REJECT_KEEP` 放到叫 `二面阶段` 的桶里（它是终态，不是进行中）。
- 把 `EXAM_REJECT_KEEP` 放到叫 `笔试阶段` 的桶里（它是终态，不是进行中）。
- 引用 §6.1 / §6.2 表之外的 stage 名（特别是带 `_DONE_` 或 `OFFER_HANDOFF` 字样的——见 §6.2 末尾说明）。
- 在命令返回的时间戳上追加 `周几` / `本周几` / `明天` / `下周一` 这类日历措辞，却没做确定性日期核对。
- 用 `cmd_parse_cv.py`——已废弃。用 `cmd_ingest_cv.py`。
- 把宿主机绝对路径硬编码进 skill 或推荐命令。
- 同一个回复里把 `uv run python3 scripts/...` 和裸 `python3 <group>/...` 混用。
- 在没有解析出唯一 `talent-id` 的情况下跑写命令。
- **跑任何 §2.2 写命令但没先 propose 解析后的命令、也没拿到用户显式 confirm（§2.2.1）。此规则对每一条写命令都适用，无例外。**
- 把用户的最初请求当作预授权；永远重新 propose 解析后的命令并等待一次新的肯定。
- 把多条写命令打包在一次 confirm 下。一次 confirm = 一条命令。**例外**：多步 chain（§5.x，如 §5.3 候选人改期 = `feishu.cmd_calendar_delete` + `outbound.cmd_send` + `talent.cmd_update`）作为一个语义单元 propose 给 boss，一次 confirm 授权整条 chain。但**跨场景**仍要分开 confirm。
- 对泛泛 "yes" 就跑 §2.3 破坏性命令；要求肯定句里**指名**破坏动作。
- 不经同轮显式确认就选 `reject_delete`。"拒" 的唯一允许默认是 `reject_keep`（见 §2.3）。
- 把命令失败用乐观的 "done" 口吻包装。
- 对不符合 §4.1.1 检测规则的文件触发 `cmd_ingest_cv.py`。不确定就问 HR。
- 转述或总结 `cmd_ingest_cv.py` 输出。把预览正文原样转发给 HR。
- 从同一份预览里同时执行 `[OC_CMD_ON_CONFIRM_UPDATE]` 和 `[OC_CMD_ON_CONFIRM_ARCHIVE]`——它们是互斥分支；按 HR 回复**只**跑其中一条（§9.4）。
- 把 Hermes Gateway 消息里的绝对路径改写成 `<workspace_root>`-相对形式。原样传过去。
- Boss 刚说"看简历"或"把简历发过来"时给 `cmd_send_cv.py` 加 `--to hr`。默认（不加 `--to`）是发给 Boss，这是对的。
- 把 AI 笔试评审分数翻译成 pass/fail 推荐。AI 报告是 advisory；永远把决策权交还给老板，并把 `cmd_exam_result.py` 作为下一步建议。
- 仅因为上一轮 AI 分数低，就在用户没要求的情况下再跑一次 `cmd_exam_ai_review.py`；AI 分数是一个数据点，不是触发后续自动化的扳机。
- Agent 不带 `--dry-run` 去提议 `auto_reject.cmd_scan_exam_timeout`。真跑是 cron 的事；手动真跑会撞车并双发拒信。
- 提议任何不在 §1.3.1 atomic CLI 清单里的脚本。当前架构没有「业务剧本」包装层；多步流程必须按 §4 路由表 + AGENT_RULES.md §5 chain 拼 atomic CLI。如果记忆里冒出某个名字带 `cmd_round{1,2}_*` / `cmd_followup_*` / `daily_exam_review` / `exam_ai_reviewer` 等字眼，先到 §1.3.1 清单核对——清单里没有的就是没有。
- 在 SQL / 命令里用 `talents.followup_*` 或 `talents.*_last_email_id` 字段做查询条件 / set。邮件去重与 followup 状态全在 `talent_emails` 表（`status` / `ai_payload` / `replied_by_email_id`）。
- 引用 `data/followup_pending/` / `data/followup_archive/` 之类「文件队列」目录。邮件状态都在 `talent_emails.status` 列。
- "为了安全" 建议用 `--skip-email` 跳拒信。`--skip-email` **仅在**老板已线下手发拒信时使用；否则候选人会无通知被删（这是 2026-04-22 修过的 bug）。
- **❌ 编造业务数据去满足 atomic CLI 的必填参数**（v3.5.4）。例：老板说"直接进 offer"，CLI 报 "`--round2-time` required" 时，**绝不**编一个时间（如"明天 10:00"）让命令跑通。`--round2-time required` 是 CLI 在告诉你"你以为这是 schedule round 2，但你的输入里根本没二面时间"——99% 是路径选错了。正确反应：abort，回头看 [AGENT_RULES.md §3.3 + §5.9](../skills/recruit-ops/docs/AGENT_RULES.md)，改用 `talent.cmd_update --force` force-jump。**发出去的候选人邮件不可撤回**——这条规则用 2026-04-21 17:06 给两位候选人错发的二面邀请换来的。
- **❌ 通过 `talent.cmd_update --set` 伪造下游字段"哄"过 stage-gate**（v3.5.4）。例：CLI 报 "ROUND2_SCHEDULING 不允许 round2 pass"，**绝不**用 `cmd_update --set round2_confirm_status=CONFIRMED --stage ROUND2_SCHEDULED` 假装"候选人 confirm 了"绕过门禁。`cmd_update --force` 才是合法的"越权推 stage"工具，但**它**只动 `current_stage`、不动业务字段——若你想动业务字段，先回头问自己为什么。同样适用：`round1_confirm_status=CONFIRMED` / `exam_sent_at=…` / `round{N}_invite_sent_at=…` 等任何会让"系统以为某事真发生过"的字段。
- **❌ CLI 报 pre-condition error（"必须提供 X" / "阶段 Y 不允许 Z" / "阶段 Y 不允许 W"）后绕路或迭代试错**（v3.5.4）。这种错误是 CLI 的 stage-machine 在告诉你"你的整体路径选错了"。正确反应：**停住 chain，重新评估意图**——大概率应该走 [AGENT_RULES.md §5.9](../skills/recruit-ops/docs/AGENT_RULES.md) force-jump 或先问老板澄清。**禁止**：(a) 改个参数再试；(b) 加 `--force` 硬上；(c) 用 `cmd_update --set` 把卡住的字段提前填好；(d) 把多个 atomic CLI 拼成"绕开门禁"的伪 chain。
- **❌ 把老板的"直接跳到 X"原始指令当成"按正常流程推到 X"执行**（v3.5.4）。"直接 / 跳到 / 略过 / 跳过 / 强制 / 不要面 / 直接进" 中任何一个字眼出现 = §3.3 stage-jump override 触发 = **唯一**走 §5.9 单步 force-jump = **不**发任何候选人邮件、**不**创建任何日历、**不**更新任何业务字段。识别规则见 [AGENT_RULES.md §3.3](../skills/recruit-ops/docs/AGENT_RULES.md)。
- **❌ 发 onboarding offer 时漏附件 / 漏 HR 飞书通知 / 自作主张填薪资和入职日期**（v3.5.5；附件部分 v3.5.10 转为系统自动）。`onboarding_offer` 模板正文里**写死**了"附件是 ...《致邃实习协议》+《实习生入职信息登记表》"——v3.5.10 起两份附件由 `email_templates.auto_attachments` 自动追加，**agent 不要再手动 `--attach`**；如果文件被删 / 改名，cmd_send 会立即失败拒发，不会裸发 offer。HR **必须**通过 `feishu.cmd_notify --to hr` 同步（HR 不在邮件 cc 里）。`onboard_date` 与 `daily_rate` 老板没明说**必须先 stop and ask**——`daily_rate` 默认 350 是"老板已确认 350"时的快捷路径，不是兜底。详细规则见 [AGENT_RULES.md §5.10 + §10](../skills/recruit-ops/docs/AGENT_RULES.md)。
- **❌ 一面派单硬编码面试官 open_id / 让 LLM 直接判定面试官 / 漏调 `intake.cmd_route_interviewer`**（v3.5.7）。`§5.11` chain 的**第一步必须**是 `intake.cmd_route_interviewer`，输出的 `interviewer_open_ids` 才能作为 `feishu.cmd_calendar_create --extra-attendee` 与 `feishu.cmd_notify --to interviewer-*` 的依据。**绝不允许**：(a) agent 在脑子里看 `talents.education` / `has_cpp` 后自己挑面试官；(b) 把 `ou_xxxxx` 字符串直接拼进命令；(c) `ambiguous=true` 时兜底到"随便派一个"或"派给老板"；(d) `config_error=true` 时拿占位符 `ou_PLACEHOLDER_*` 当真账号发飞书。正确反应：转 ASK_HR 分支（`feishu.cmd_notify --to hr` 报告原因，等 HR 显式指派）。详细见 [AGENT_RULES.md §5.11 + §7.9](../skills/recruit-ops/docs/AGENT_RULES.md)。
- **❌ 在 NEW 阶段调用 §5.1 chain**（v3.5.7 引入区分，v3.6 收敛为唯一 happy path）。**v3.6 起 NEW 阶段一面派单一律走 §5.11**（HR 触发，先 `intake.cmd_route_interviewer` 派单，直接进 `ROUND1_SCHEDULED + CONFIRMED` 一气呵成）；§5.1 chain 在 v3.6 下**只**服务 `WAIT_RETURN` 候选人回归后老板手动重排（出口 `ROUND1_SCHEDULING + PENDING`，等候选人 confirm 再走 §5.2 建日历）。识别铁律：**stage=NEW 出现一面排期意图 ⇒ §5.11**（不论 HR 还是老板触发）；**stage=WAIT_RETURN 候选人已回归 + 老板给时间 ⇒ §5.1**。老板若**绕开 HR**直接对 NEW 候选人说"安排一面"——**不要**自动拼 §5.1，stop and ask 让老板转给 HR 触发 §5.11，或显式 §5.9 force-jump（详见 §4.2 NEW 决策表第 2 行）。混用的事故面：(a) NEW 候选人走 §5.1 后没派面试官、卡在 `ROUND1_SCHEDULING + PENDING`；(b) §5.11 漏调 `cmd_route_interviewer` 改用硬编码 open_id；(c) §5.11 在 stage=WAIT_RETURN 上被错误调用（候选人都还没确认能否回来）。
- **❌ 回答"完整信息 / 档案 / 全部资料"时只发"📋 候选人档案 / 📂 文件状态"两个空标题再贴一个 cv_path 就声称"信息已同步"**（v3.5.10 真实事故）。"完整信息" = `talent.cmd_show` 输出里所有非空字段一项不漏。如果你只想给 cv 路径就别打"完整信息"四个字；要打就把所有字段（candidate_name / email / phone / position / education / school / work_years / source / experience / current_stage / cv_path / 笔试与一面二面时间 / round*_confirm_status / 最近审计事件）都列出来。详细见 §12 路由表对应行。
- **❌ 把飞书 Gateway 落盘时给附件加的 `doc_<hex>_` 前缀当作真实文件名留在 `cv/` 目录或 `talents.cv_path` 里**（v3.5.10）。`lib.candidate_storage.import_cv` 已自动剥；历史数据用 `talent.cmd_normalize_cv_filenames` 一次性补救。**绝不**：(a) 手动在文档 / 飞书回复里贴带 `doc_<hex>_` 前缀的路径而不提示这是脏数据；(b) 自己写脚本去 mv 这些文件而不用 `cmd_normalize_cv_filenames`（它会同步 DB + 处理重复副本）。
- **❌ 用 `exam.fetch_exam_submission` 重拉候选人简历 / 笔试附件**（v3.5.8）。`inbox.cmd_scan` 已在每次扫到候选人新邮件时**自动**把附件按 `context` 分流落到 `data/candidates/<tid>/{exam_answer|email}/em_<eid>/`，元数据写到 `talent_emails.attachments` JSONB 数组（含 `path` / `size` / `sha256`，`path` 字段是**相对 `data_root()`** 的路径，例如 `candidates/t_xxx/exam_answer/em_yyy/file.zip`）。boss 说"看下 X 的简历"**先**查 `talents.cv_path`（CV 是绝对路径） / `talent_emails.attachments`（附件是相对路径，前面拼 `data/`），**禁止**为了拉一份附件调 IMAP 重下载。同样禁止：(a) 引用旧 `data/candidate_answer/t_t_<tid>/em_<eid>/` 路径（v3.5.8 已迁完并清空，旧路径不存在）；(b) 把 `attachments[*].path` 拼成绝对路径硬编码，要走 `Path(data_root()) / row.path`；(c) 用 `outbound.cmd_send --attach` 时漏写 `data/` 前缀。

---

## 12. 路由示例（eval cases）

这些是规范的"输入 → 路由"映射。既作为 sanity check，也作为回归测试锚点。

只读查询以单步展示。写命令以两步展示：**(a) propose** 解析后的命令给用户，**(b) execute** 在显式 confirm 后（§2.2.1）。**永远不要**把 (a) 和 (b) 压到同一轮。

| 用户说 | 正确路由 |
|---|---|
| "今天有谁有面试" | 只读：`cmd_today_interviews.py` |
| "看看所有候选人" | 只读：`cmd_status.py --all` |
| "还在进行中的候选人" | 只读：`cmd_search.py --all-active` |
| "查一下张三现在到哪一步了" | 只读：`cmd_search.py --query 张三` → 若唯一，`cmd_status.py --talent-id <id>` |
| Boss / HR 说"看看车光明" / "把 X 在人才库的完整信息给我" / "X 的档案" / "X 的全部资料" | 只读：resolve unique id → `uv run python3 -m talent.cmd_show --talent-id <id>` → **forward 输出原样**（含 `候选人档案 / 邮件统计 / 审计事件` 三段所有非空字段）。**严禁**：(a) 自己捏造 "📋 候选人档案" / "📂 文件状态" 这类标题然后只填一段（v3.5.10 真实事故：飞书回复留了空"候选人档案"标题就把 cv_path 贴出来了）；(b) 省略 `cmd_show` 输出里任何**非空字段**（candidate_name / email / phone / position / education / school / work_years / source / experience / current_stage / cv_path / created_at / updated_at / 笔试与面试时间 / round*_confirm_status / 审计事件）。可以用 markdown 排版，但**字段一项也不能漏**。如果输出超长（>4 KB），可以裁掉 experience 长摘要并标注"…（experience 已截断，全文见 talents.experience）"。 |
| HR 发 PDF 名为 `量化研究员-上海-500元_天-李四-2026年应届生.pdf` | §4.1.1 match → 自动触发 `cmd_ingest_cv.py --file-path <path> --filename <name>` → 原样转发预览 → 等 HR 回复 → 按 §9.4 执行所选 `[OC_CMD_ON_CONFIRM*]` payload。 |
| HR 发一份普通 PDF `会议纪要.pdf`，无候选人语境 | §4.1.1 **不** match → **不要**跑 `cmd_ingest_cv.py`；让 HR 确认是不是 CV，或走通用文件处理。 |
| "冯屹哲笔试已发，只改状态" | resolve id → **propose** `interview/cmd_result.py --talent-id <id> --round 1 --result pass --skip-email` → wait for confirm → execute。仅支持仅状态转换；不要发明其他。 |
| "李陆斌一面被拒，保留人才库" | resolve id → **propose** `interview/cmd_result.py --talent-id t_vxunkj --round 1 --result reject_keep` → wait for confirm → execute。 |
| "把张三一面改到明天下午三点" | resolve id → resolve time → **propose §5.3 chain 整条**：(1) `feishu.cmd_calendar_delete --event-id <round1_calendar_event_id> --reason "候选人改期"` (2) `outbound.cmd_send --talent-id <id> --template reschedule --vars round_label=一面 old_time=<old> new_time=<new> location=<office>` (3) `talent.cmd_update --talent-id <id> --stage ROUND1_SCHEDULING --set round1_time=<new> --set round1_confirm_status=PENDING --set round1_calendar_event_id=__NULL__ --set round1_invite_sent_at={send.sent_at} --reason "candidate reschedule"` → wait for confirm → 一次性执行整条 chain。 |
| "新候选人张三 t_xxx，安排明天下午三点一面" | **v3.6 起一律走 §5.11**：先 `talent.cmd_show --talent-id t_xxx --json` 校验前置（`current_stage=NEW`、`education` 非空、`has_cpp` 已写）。前置不全 → stop and ask HR 先跑 `intake.cmd_ingest_cv`。前置满足 → resolve time → **propose §5.11 chain 整条**：(1) `intake.cmd_route_interviewer --talent-id t_xxx --json`（必跑第一步；ambiguous=true / config_error=true 必须 STOP 转 ASK_HR） (2) `outbound.cmd_send --talent-id t_xxx --template round1_invite --vars round1_time=<resolved> --json` (3) `feishu.cmd_calendar_create --talent-id t_xxx --time "<resolved>" --round 1 --duration-minutes 30 --candidate-name <name> --candidate-email <email> --extra-attendee {route.interviewer_open_ids[*]} --json` (4) `talent.cmd_update --talent-id t_xxx --stage ROUND1_SCHEDULED --set round1_time=<resolved> --set round1_invite_sent_at={send.sent_at} --set round1_confirm_status=CONFIRMED --set round1_calendar_event_id={cal.event_id}` (5) `feishu.cmd_notify --to interviewer-{role} …` ×N (6) `feishu.cmd_notify --to boss --severity info --title "一面已排"` → wait for confirm → 一次性执行整条 chain。**绝不**回退到 §5.1 老路径（NEW 阶段已废）。 |
| "李四在国外，下个月再约一面" | resolve id → **propose §5.4 chain**：（如已建日历，先 `feishu.cmd_calendar_delete`）→ `outbound.cmd_send --talent-id <id> --template defer --vars round_label=一面` → `talent.cmd_update --stage WAIT_RETURN --set wait_return_round=1 --set round1_time=__NULL__ --set round1_calendar_event_id=__NULL__ --reason "candidate defer until return"` → wait for confirm → 整条执行。 |
| "发简历给老板 张三" | resolve 唯一候选人 → **propose** `intake/cmd_send_cv.py --name 张三`（或 `--talent-id`）→ wait for confirm → execute。 |
| "删掉这个候选人" | §2.3 破坏性：resolve id → **propose** `talent/cmd_delete.py --talent-id <id> --reason "<原话>"`（或 `common/cmd_remove.py --talent-id <id>`，等价），带候选人姓名 + 当前 stage → 等一个指名破坏动作的 confirm（如"是，删除 t_xxx"）→ execute。泛泛 "yes" 不够。 |
| "他二面通过"，本轮无唯一候选人上下文 | **不要动手**；问"他"指谁。绝不 propose 也绝不 execute。 |
| "审阅冯屹哲的笔试邮件" / "评审一下张三的笔试" / "看一下李四的笔试" | resolve 唯一 `talent-id`（必要时 search）→ **两步 propose**：(1) **propose** `exam/cmd_exam_ai_review.py --talent-id <id>` 做终端预览（无 `--feishu` / `--save-event`）；wait for confirm → execute；(2) Boss 看完报告后，**propose** 同一条加 `--feishu --save-event` 推飞书 + 写审计；wait for confirm → execute。CLI 自动从 IMAP 拉最新笔试提交并缓存 `/tmp/exam_submissions/<id>/`；**v3.5 起没有任何前置脚本要跑**（`daily_exam_review.py` 已删；`fetch_exam_submission.py` 是被 `cmd_exam_ai_review` 内部调用的 helper）。 |
| "看看 auto reject 会拒谁" / "今天有没有人会被自动拒" | 只读：`uv run python3 -m auto_reject.cmd_scan_exam_timeout --dry-run` |
| "为什么 X 被自动拒了" | 只读：`uv run python3 -m inbox.cmd_review --talent-id <id>`（看他 `exam_sent_at` 之后是不是真没回信）+ `uv run python3 scripts/talent/cmd_show.py --talent-id <id>`（看他归档 / 删除 audit）。**不要**改写或猜系统判定，只引用查询结果。 |
| Boss 看到飞书"[自动拒删 · 已执行]"卡片，说"好" / "ok" / "知道了" | **不需要任何操作**。卡片是事后告知，候选人已删除（不可逆）。Agent 不要提议恢复或撤销。 |
| Boss 看到飞书"[自动拒删 · 失败]"卡片 | 候选人**没**被删，留在 `EXAM_SENT`。下次 cron 会再扫；如果反复失败，看 SMTP / 模板 / DB 排查。Agent 不要手动跑 `cmd_scan_exam_timeout` 真跑（会和 cron 撞车）。 |
| Boss 说"把 X 拒了，删人" | §2.3 破坏性：**propose** `interview/cmd_result.py --talent-id <id> --round <N> --result reject_delete` → wait for 指名破坏动作的 confirm → execute。该命令会自动发 `rejection_generic` 再删人；`--skip-email` 仅在 boss 已线下发拒信时使用。 |
| Boss 说"李志鹏笔试通过，直接进 offer 阶段" / "冯屹哲不需要二面，直接结束流程" / "跳过二面，直接发 offer" | **§5.9 force-jump 单步**（[AGENT_RULES.md §5.9](../skills/recruit-ops/docs/AGENT_RULES.md)）。resolve 唯一 `talent-id` → **propose 单步 chain**：`talent.cmd_update --talent-id <id> --stage POST_OFFER_FOLLOWUP --force --reason "boss原话: <逐字引用老板原话>"` → 提示老板"这一步**不会**发邮件、**不会**建日历、**不会**更新 round2_time / confirm_status 等业务字段，只把 stage 推到 POST_OFFER_FOLLOWUP；如果你希望先发二面邀请走正常流程，请改说『安排二面，时间 …』" → wait for explicit confirm → execute。**绝不**调 `exam.cmd_exam_result --result pass` 或 `interview.cmd_result --round 2 --result pass`（这些会真发候选人邮件）。 |
| Boss 说"王五先放进笔试阶段" / "把张三强制推到 EXAM_REVIEWED" | **§5.9 force-jump 单步**：resolve id → **propose** `talent.cmd_update --talent-id <id> --stage <target> --force --reason "boss原话: …"` → 提示"不发邮件、不动业务字段" → wait for confirm → execute。注意：若老板的目标 stage 是 `STAGE_LABELS` 之外的字符串，**stop and ask**（[AGENT_RULES.md §5.9](../skills/recruit-ops/docs/AGENT_RULES.md) 末尾），不要自己猜。 |
| Boss 说"笔试通过 + 安排二面，时间是明天上午 10 点" | **chain §5.5**（不是 §5.9）：resolve id + 时间 → propose 完整 chain `outbound.cmd_send --template round2_invite --vars round2_time=… location=…` + `talent.cmd_update --stage ROUND2_SCHEDULING --set round2_time=… …` → wait for confirm → execute。**关键差异**：boss 此处**明确要求安排二面**（给了时间、提了 invite），所以走正常 chain；而上面三行的 boss 是**明确要求跳过二面**——不要混淆。如果 boss 的指令两可，**stop and ask**。 |
| Boss 说"给冯屹哲发 onboarding offer，5 月 6 日入职，薪资 350 / 天" | **chain §5.10**（[AGENT_RULES.md §5.10 + §10](../skills/recruit-ops/docs/AGENT_RULES.md)）：resolve 唯一 `talent-id` → 检查 `talent_emails` 是否已发过 onboarding_offer（重发要 boss 二次确认）→ propose 完整 chain：(1) `outbound.cmd_send --talent-id <id> --template onboarding_offer --vars position_title=<DB.position> interview_feedback=<默认套话或老板原话> daily_rate=350 onboard_date=2026-05-06 location=<office> evaluation_criteria=<默认套话>`（v3.5.10：**不要**再加 `--attach`，《致邃实习协议》+《实习生入职信息登记表》两份 docx 由 `auto_attachments` 自动追加）(2) `feishu.cmd_notify --to hr --severity info --title "新候选人 offer 已发，请准备入职" --body "candidate=<id> name=<name> 入职日期=2026-05-06 薪资=350 元/天 岗位=<position>（附件：实习协议 + 入职登记表）"` → wait for confirm → 一次性执行整条 chain。**关键校验**：`onboard_date` / `daily_rate` 必须 boss 明确给出；缺任一项**先 stop and ask**，不要 propose。 |
| Boss 说"给冯屹哲发个 offer 通知"（**没**说入职日期 / 薪资） | **不直接 propose §5.10**。回飞书："老板，发 onboarding offer 需要 (1) 入职日期 (2) 实习日薪（默认 350 元/天）；麻烦确认一下，确认后我会同时把 HR 抄进飞书。"拿到答复后再走上面那行的完整流程。 |
| Boss 说"看下李志鹏发过来的简历 / 笔试附件 / 文件"（v3.5.8） | **不**调 `exam.fetch_exam_submission`。只读：resolve 唯一 id → (a) **CV** 直接报 `talents.cv_path`（绝对路径）。(b) **其他附件**：查 `SELECT subject, sent_at, context, attachments FROM talent_emails WHERE talent_id=<id> AND attachments IS NOT NULL ORDER BY sent_at DESC LIMIT 5;` → 拿 `attachments[*].path`，文件**绝对路径** = `data/<path>`（典型如 `data/candidates/t_xxx/exam_answer/em_yyy/file.zip`）→ 在飞书回复里列出文件名 + 大小 + 落盘路径，让老板自己 `cat` / `cp` 出去看。(c) `data/candidates/<id>/exam_answer/legacy_fetch/` 下还有 `_ai_review_result.json` 等历史评审产出（v3.5.8 之前手动 fetch 留下的），可一并提示。如果 `attachments IS NULL` 但 boss 坚持有附件，可能是 `inbox.cmd_scan` 还没扫到，建议等下一次 cron tick（5 分钟），不要手动 retrigger。 |
| **HR** 说"安排张三一面，时间是 4-25 14:00" / "t_xxxxx 一面 4 月 25 日 14:00"（v3.5.7） | **§5.11** chain（[AGENT_RULES.md §5.11](../skills/recruit-ops/docs/AGENT_RULES.md)）：resolve 唯一 id（HR 通常已 `cmd_ingest_cv` 过；若 `talents.education` 为空 / 候选人不存在 → stop and ask HR 先 ingest CV）→ propose 完整 chain：(1) `intake.cmd_route_interviewer --talent-id <id> --json`（**必跑第一步**，输出 `interviewer_open_ids`、`ambiguous`、`config_error`）(2) `outbound.cmd_send --template round1_invite --vars round1_time="2026-04-25 14:00" --json` (3) `feishu.cmd_calendar_create --talent-id <id> --time "2026-04-25 14:00" --round 1 --duration-minutes 30 --candidate-name <name> --candidate-email <email> --extra-attendee {route.interviewer_open_ids[0]} --json` (4) `talent.cmd_update --stage ROUND1_SCHEDULED --set round1_time="2026-04-25 14:00" --set round1_invite_sent_at={send.sent_at} --set round1_confirm_status=CONFIRMED --set round1_calendar_event_id={cal.event_id}` (5) `feishu.cmd_notify --to interviewer-{role} --severity info --title "一面安排：<name>" --body "候选人/时间/学历/邮箱/cal_eid"` (6) `feishu.cmd_notify --to boss --severity info --title "一面已排：<name>"` → wait for HR confirm → 一次性执行。**关键校验**：route step `ambiguous=true` 或 `config_error=true` 时**绝不**继续，改推 `feishu.cmd_notify --to hr --severity warn` 报告原因（HR 显式回 `master/bachelor/cpp` 后再重启）。 |
| **HR** 说"t_xxx 一面派给 cpp" / "派给硕士面试官" / "派给本科面试官"（§5.11 ambiguous 后 HR 回话） | resolve 唯一 id + role → 重启 §5.11 但跳过 step 1：把 HR 指定的 `interviewer_<role>_open_id`（从 `lib.config['feishu']`）当作 `route.interviewer_open_ids` 喂给 step 2 起步的 chain → propose 余下 5 步 → wait for HR confirm → execute。**依然不允许** agent 写硬编码 `ou_xxx`；从 config 读不到（占位符）就直接 stop and ask 运维。 |

---

## 13. 运行时

### 13.1 本 skill 在哪里跑

本 skill 由 **Hermes Gateway** 加载，**不是**独立进程。它通过 WebSocket 读飞书消息，路由给工作区里的 `recruit-ops` CLI。它假设运行时已经配置好；**不负责**装依赖、建 DB、设 env var——上述任一缺失时按 §8 的 Infra 类故障上报并停下。

### 13.2 硬性前置条件（假设已就绪）

- **Python**：3.10+
- **依赖已安装**：`<workspace_root>/skills/recruit-ops/` 下已跑过 `uv sync`，或项目 `.venv` 已在位。`uv run python3` 与 `<workspace_root>/skills/recruit-ops/.venv/bin/python3` 等价——一个回复里选一个用，保持一致。
- **数据库**：`talent-db` 配置可达；`scripts/lib/talent_db.py` 从 `scripts/lib/talent-db-config.json` 或等价 env var 解析连接。
- **飞书身份**：`FEISHU_BOSS_OPEN_ID` 和 `FEISHU_HR_OPEN_ID` env var（或 openclaw 账户字段 `ownerOpenId` / `hrOpenId`）已设。
- **Cron / systemd**：`cron/cron_runner.py` 是统一调度入口，串 `inbox.cmd_scan` + `inbox.cmd_analyze` + `common.cmd_interview_reminder` + `auto_reject.cmd_scan_exam_timeout` + `ops.cmd_health_check`；外部调度时设 `PYTHONPATH=scripts`。**官方部署 = systemd user timer**：`~/.config/systemd/user/recruit-cron-runner.{timer,service}`，每 10 分钟一轮；改时间 / 排障运维命令见 [CLI_REFERENCE.md §定时任务](../skills/recruit-ops/docs/CLI_REFERENCE.md#cron_runnerpy)。

### 13.2.1 SKILL.md 同步路径（**改完无需额外复制**）

当前部署把 `~/.hermes/skills/openclaw-imports/recruit-ops/SKILL.md` **软链**到 `recruit-workspace/docs/recruit-ops-SKILL.md`：

```bash
ls -la ~/.hermes/skills/openclaw-imports/recruit-ops/SKILL.md
# -> /home/admin/recruit-workspace/docs/recruit-ops-SKILL.md
```

这意味着：

- **日常只编辑** `recruit-workspace/docs/recruit-ops-SKILL.md`；
- **不要**直接覆盖 Hermes 那端的 SKILL.md（会把软链变成普通文件副本，后续更新就分叉了）；
- 改完直接重启 Hermes 让它重读即可，无需 `cp`。

如果软链在某次意外中变成了普通文件副本，用下面一条命令重建：

```bash
ln -sf /home/admin/recruit-workspace/docs/recruit-ops-SKILL.md \
       /home/admin/.hermes/skills/openclaw-imports/recruit-ops/SKILL.md
```

### 13.3 开源 / 开箱即用目标

为将来分发，本 skill 正文保持可移植：

- 正文无宿主机绝对路径；用 `<workspace_root>` 占位符。
- 唯一放具体本地路径的地方是本文件顶部的 **「本地部署映射」** 块——按部署删除或重新映射。
- Hermes Gateway 加载路径（`~/.hermes/skills/openclaw-imports/recruit-ops/`）也属部署细节，只在顶部那个块里。
- 指向 CLI 命令，**绝不**指向运维者的 shell alias 或私人脚本。
- 部署特定的 override 应放在**独立**的运维笔记里，不进本 skill。

---

## 14. 总结清单

回复前确认：

- [ ] 意图已映射到 §4 里列出的一个真实 CLI 命令。
- [ ] 命令的安全等级（§2）已尊重：只读可随便跑；自动触发预览（§2.1.5）仅在 §4.1.1 CV match 时跑；**每一条写命令都已先展示给用户并在显式同轮肯定后才执行（§2.2.1）**；§2.3 破坏性命令拿到了指名破坏动作的肯定。
- [ ] CV 录入：`cmd_ingest_cv.py` 仅在 §4.1.1 match 时自动触发；输出原样转发；按 HR 回复从 `UPDATE` / `ARCHIVE` / `CONFIRM` 中**只**跑其一（§9.4）。
- [ ] 所有写命令都已解析出唯一 `talent-id`。
- [ ] 一次 confirm 只授权了一条命令（没有批量打包）。
- [ ] 任何 `--time` 都是 `YYYY-MM-DD HH:MM` Asia/Shanghai (+08:00)，且已 echo 回给用户。
- [ ] 回复里的 stage 标签与 §6 一致（亦即与 `core_state.py` 一致）。
- [ ] 查询请求走规范查询命令，不走派生视图。
- [ ] 没有把 `*_DONE_*` stage 当成进行中。
- [ ] 提议的所有脚本都在 §1.3.1 atomic CLI 清单里；没有 auto_reject 的「队列 / 缓冲窗口 / 合法改期白名单」一类概念（§2.4 / §6.3）。
- [ ] agent 侧没有真跑 `auto_reject.cmd_scan_exam_timeout`（cron 专用；agent 只跑 `--dry-run`）。
- [ ] 回复里没有添加命令没明确返回的字段（§9）。
- [ ] PII 按最小必要级别披露（§10）。
- [ ] 命令形式在整份回复里都是 `uv run python3 scripts/...`。

---

## 附录：主要参考

- [skills/recruit-ops/docs/CLI_REFERENCE.md](../skills/recruit-ops/docs/CLI_REFERENCE.md) — 完整 CLI 参考，含参数表、条件必填、cron 备注、按命令给的示例。
- `skills/recruit-ops/scripts/lib/core_state.py` — 规范 `STAGE_LABELS`。
