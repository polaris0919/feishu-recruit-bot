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
- 元/天

# 注：CV 文件名常见 token（如 "500元_天-张三-..."）由 §4.2 检测规则匹配，

# 不需要塞进 triggers——triggers 只匹配自由文本消息

# 查询短语

- 查一下
- 看看候选
- 加个候选
- 有个人

# 运维 / 系统操作（v3.8 新增 — 通过飞书触发 ops/cmd_* 类写动作）

- recruit-ops
- pending migration
- 应用 migration
- 应用迁移
- migration apply
- 体检
- health check
- replay 通知
- 回放飞书

---

# Recruit Ops

## 0.0 必须先遵守的加载规则

1. 任何招聘相关消息都必须使用本 skill，包括但不限于：候选人、简历/CV、面试、一面/二面、笔试、Offer、飞书招聘 bot、候选人邮件回信、`talent_id`、`round1`/`round2`、人才库、PostgreSQL talent DB。
2. 执行任何写动作前，必须先读取 `docs/AGENT_RULES.md`，按其中 stage × intent / workflow 规则选择 atomic CLI 或确认门；找不到精确场景时走 `AGENT_RULES.md §3.5 Uncovered Workflow Planner`。
3. 查询 CLI 参数、flags、输出字段或命令格式前，必须读取 `docs/CLI_REFERENCE.md` 的对应章节；不要凭历史记忆拼命令。
4. 本文件、`docs/*.md` 与代码冲突时，以代码和 CLI 自验证为准；不要用旧聊天记录或 Hermes 本地桥接 skill 里的过期示例。
5. **二面时间缺失时的话术硬规则**：如果老板只说“候选人笔试通过 / 安排二面”但没有给二面时间，只能询问“请提供二面候选邀请时间”。不要承诺“确认后创建日历 / 更新为 `ROUND2_SCHEDULED`”。收到时间后的第一步也只是发送二面邀请并进入 `ROUND2_SCHEDULING`；候选人回信确认后，还必须等老板再次明确授权，才允许建老板 + Polaris 日历并进入 `ROUND2_SCHEDULED`。

> 本 skill 是 agent 处理招聘对话的**入口契约**。每条消息开头先按 §0 决策主循环分诊，再按相关章节执行。**业务规则 / chain 代码** 在 [AGENT_RULES.md](docs/AGENT_RULES.md)；**部署 / cron / symlink** 在 [docs/OPERATIONS.md](docs/OPERATIONS.md)；**事故规则录** 在 [docs/INCIDENT_RULES.md](docs/INCIDENT_RULES.md)。

通过仓库里稳定的 CLI 命令操作 `recruit-ops` 工作流。PostgreSQL 是唯一数据真源；永远不要凭记忆总结状态。

本 skill 服务两类使用者：

- **HR**：在飞书群里发 CV 附件和 `【新候选人】` / `【导入候选人】` 模板。skill 自动识别 CV（§4.2），解析（§2.2），并带 HR 走一条去重的录入流程。
- **Boss**：用自然语言查状态、安排日程、记录结果、交接后续。

设计**分两层**：

- **本文件（路由层）**——只管"怎么和人对话"：分诊、安全协议、confirm 协议、结果措辞、PII。CV 入库（§4）是 SKILL 唯一独占的业务路由（因为它走"附件触发预览 → HR 自然语言 confirm"的非 chain 模式）。
- **[AGENT_RULES.md](docs/AGENT_RULES.md)（决策层）**——所有"给定 DB 状态 + 入站事件 → 拼哪条 atomic CLI chain"的决策规则。本文件路由到任何"按 chain 处理"的意图后，agent **必须先 fetch** AGENT_RULES 才能拿到具体 chain 代码。

正文保持**可移植**：用 `<workspace_root>` 占位符，**不**写宿主机绝对路径；具体本地路径只在 [docs/OPERATIONS.md](docs/OPERATIONS.md) 里配置。Hermes 给的附件绝对路径**原样**传给 CLI `--file-path`，**不要**改写成 `<workspace_root>`-相对形式。

正文与代码冲突时，以代码为准。

---

## 0. Agent 决策主循环

> agent 处理任何招聘对话**必须**先把消息分到 **5 类**（A-E），再按下面 **6 步**主循环执行；分类错了后面所有规则就跑偏。

每条消息开头：

1. **分类**这条消息属于哪一种：
  - **A. CV 附件入库** — 飞书消息带 PDF / DOCX，且符合 §4.2 检测规则。
  - **B. 只读查询** — 状态查询、列表请求、搜索；不需要改 DB / 不发外部消息。
  - **C. 写 / 动作请求** — 安排面试、记录结果、发邮件、改 stage、**运维写动作**（如 `ops.cmd_db_migrate --apply` 应用 pending migration、`ops.cmd_replay_notifications` 回放遗漏卡片）等任何会改 DB / 发外部消息的操作。
  - **D. 破坏性请求** — 删档、`reject_delete`、永久销毁数据。
  - **E. 模糊 / 未知** — 无法确定意图、信息不全、命中多名候选人。
2. **A → §4** CV 检测；命中则跑 `cmd_ingest_cv.py` 预览（§2.2 自动触发，不走 confirm 协议）。
3. **B → §5** 选**最窄**的只读命令；不要用更宽的命令再自己派生视图。
4. **C 或 D**：
  - 先按 §3 锁定**唯一** `talent-id` + 所有必需参数；**例外**：运维写动作（`ops.cmd_db_migrate --apply` / `ops.cmd_replay_notifications` 等）**没有 talent-id**——直接跳过 §3 身份解析，按 §2.3.1 atomic 档 propose 命令、等用户飞书 confirm 后执行。
  - 若需走 chain，**先 fetch** [AGENT_RULES.md §4 / §5](docs/AGENT_RULES.md) 拿到 chain 代码；
  - 按 §2.3.1 propose（atomic 或 declared chain）+ 等显式 confirm；
  - **未拿到 confirm 之前一律不执行**；
  - 破坏性命令额外要求 §2.4 指名破坏动作。
5. **命令失败 → §6** 四分类如实上报；**不要**乐观包装。
6. **找不到匹配的 chain / 信息不全 / 命令拒收 → §10** stop and ask，永远比硬撑要好。

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
- cron / systemd 调用需要 import 时，显式设置 `PYTHONPATH=scripts`（见 [CLI_REFERENCE.md](docs/CLI_REFERENCE.md#cron_runnerpy)）。

### 1.2 数据真源

- PostgreSQL 支撑的 `recruit-ops` 状态是规范真源。所有 stage 名、计数、时间安排都必须来自命令输出，不得来自模型记忆。
- 当一条查询命令已经提供某个视图时，**不要**自己手工重建这个视图。DB 支撑的规范视图有：
  - `common/cmd_status.py` — 完整候选人列表，或某一个候选人的详情
  - `common/cmd_search.py` — 关键字搜索，或仅活跃视图
  - `common/cmd_today_interviews.py` — 按日期范围查面试安排
- 规范 stage 标签定义在 `scripts/lib/core_state.py`（`STAGE_LABELS`）；速查表见 [AGENT_RULES.md §2](docs/AGENT_RULES.md#2-stages)。代码与文档冲突以代码为准。

### 1.3 主要参考文档


| 文件                                                   | 何时读                                                                                                                                      |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| [docs/AGENT_RULES.md](docs/AGENT_RULES.md)           | **决策手册**——stage 状态机（§2）、atomic CLI 速查（§3）、所有 stage × intent 场景 chain 代码（§4 + §5 速查表）。**唯一**业务规则源；任何"按 chain 处理"的意图都**必须先 fetch** 这份才能继续。 |
| [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md)       | 写 propose 之前要查参数 / flags / 输出 schema 时。                                                                                                  |
| [docs/INCIDENT_RULES.md](docs/INCIDENT_RULES.md)     | 命中事故标签（带版本号 / 日期的反模式）/ 想知道某条规则的来源时。                                                                                                      |
| [docs/OPERATIONS.md](docs/OPERATIONS.md)             | 部署 / cron / symlink / 故障排查。agent 在线上对话中**不需要**读，只在用户明确问运维时引用。                                                                            |
| [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md) | 新人 onboarding / 架构演进决策时。agent 不需要常读。                                                                                                     |
| [docs/INDEX.md](docs/INDEX.md)                       | 找不到答案该读哪份的兜底地图（一页指针）。                                                                                                                    |
| `scripts/lib/core_state.py` (`STAGE_LABELS`)         | 规范 stage 标签**代码真源**；与文档冲突以代码为准。                                                                                                          |


### 1.4 架构概览

两层：**原子 CLI**（每条命令对应一个写动作 + 自验证 + 飞书告警）+ **agent chain**（LLM 看 AGENT_RULES.md §4 / §5 选下一步，用 `lib.run_chain` 串原子 CLI）。**没有"业务剧本"包装脚本作为兜底**——所有多步流程都按 AGENT_RULES.md §4 chain 重新规划。

> 完整 atomic CLI 清单见 [AGENT_RULES.md §3](docs/AGENT_RULES.md) + [CLI_REFERENCE.md](docs/CLI_REFERENCE.md)。如果在 prompt 历史里看到 `cmd_round1_schedule` / `round2/` 整目录 / `followup/` / `cmd_reschedule` / `cmd_defer` / `daily_exam_review` / `cmd_finalize_interview_time` 一类旧脚本名——那是 v3.4 之前的残留，**忽略并按 AGENT_RULES.md §3 重新规划**。事故源见 [INCIDENT_RULES.md §7](docs/INCIDENT_RULES.md#7-v34--v35--业务剧本包装层全部移除)。

**chain 编排**：agent 串多步动作时用 `lib/run_chain.py` 的 `Step(...)`；前一步 `--json` 输出可作占位符（语法 `{step.field}`，如 `--set round1_invite_sent_at={send.sent_at}`）。任意一步失败 → 短路 + `feishu.cmd_notify --severity critical`，**不**自动回滚（发邮件 / 删日历不可逆）。每条 chain 的代码、占位符、`--set` 字段名见 [AGENT_RULES.md §4](docs/AGENT_RULES.md)，每条都被 `tests/test_agent_chain.py` 端到端回归。

> **`run_chain` vs `cli_subprocess.run_module`（B4, v3.8.7）**——两者**不可互换**：
> - **`run_chain`** = agent 串多 atomic CLI 的**进程内**剧本编排，前一步 JSON → 后一步占位符，短路即停。**99% 的 agent 写动作走这里**。
> - **`cli_subprocess.run_module`** = helper 模块**跨语义边界**调一个不该 import 的业务 CLI 时的哑执行器（典型：`auto_reject.executor` 调 `talent.cmd_delete`——删档不该出现在邮件模块的 import 拓扑里）。**不在 agent 主路径用**。
> - **`bg_helpers` Popen 路径** = 后台 fire-and-forget（发邮件 / 建日历，立即返回 PID）；与上面两者并列。
> 决策树详见 `docs/PROJECT_OVERVIEW.md §5.8`。

**failure vs UserInputError**：write 类脚本 crash → `lib/cli_wrapper.py` 自动飞书告警；`UserInputError`（缺 `--force`、talent_id 不存在、template 变量缺失）→ 只 stderr 不告警。

### 1.5 跟本 skill 对话的是谁

两类使用者，两种不同的消息形态：


| 使用者      | 典型消息                                                                | skill 的职责                         |
| -------- | ------------------------------------------------------------------- | --------------------------------- |
| **HR**   | CV 附件（PDF/DOCX）、`【新候选人】` / `【导入候选人】` 文本模板、状态修正请求（"笔试已手工发出"）、候选人改期转发 | 识别、解析、去重，给 HR 出预览确认，再执行确认后的命令     |
| **Boss** | 自然语言的查询与指令（"今天谁有面试"、"张三一面改到明天下午三点"、"李陆斌一面被拒保留"）                     | 解析身份 + 时间，propose 命令，等 confirm，执行 |


skill 不显式追踪"当前说话的是谁"——消息形态本身足够区分。两者都是可信方；§2.3.1 的 confirm 协议对双方一视同仁。

**身份标识**：部署层通过环境变量配置两个飞书身份——`FEISHU_BOSS_OPEN_ID`（老板）与 `FEISHU_HR_OPEN_ID`（HR）。`intake/cmd_send_cv.py --to {boss,hr}` 据此路由。skill **不**读也**不**回显这些 open_id——只用抽象的 `boss` / `hr` 标签。

---

## 2. 安全模型

每条命令都落在三个安全等级中的一个。分类依据是命令的**最大可能副作用**，不是常见情形。

### 2.1 只读（可以主动跑）

任何 flag 组合下都**绝不**写 DB、**绝不**发出站邮件 / 飞书 / 日历请求。按业务分组：

**候选人查询**（**v3.3 推荐**：`talent/` 三件套优先于 `common/cmd_status.py`）：

- `talent/cmd_show.py --talent-id X` — 单候选人完整快照（字段 + 邮件统计 + 最近审计），**v3.3 单人查询首选**
- `talent/cmd_list.py [--stage / --search / --has-unanalyzed / --order]` — 列表 + 多维过滤，**v3.3 列表首选**
- `common/cmd_status.py [--all | --talent-id X]` — 旧入口，仍可用；和 `talent/` 三件套等价
- `common/cmd_search.py --query <name> | --all-active` — 模糊搜索 / 活跃候选人列表
- `common/cmd_today_interviews.py [--date YYYY-MM-DD]` — 按日期范围查面试安排

**邮件 / 通信查询**：

- `inbox/cmd_review.py --talent-id X` — 候选人**完整邮件时间线**（含 AI 摘要 / 模板名 / 已分析标记）

**辅助查询**（agent 起草前应跑）：

- `template/cmd_preview.py --list | --template T --demo` — 模板列表 / 渲染预览（起草邮件前**必跑**）
- `intake/cmd_route_interviewer.py --talent-id X --json` — 一面派单纯查询（§5.11 chain 的第一步，单独跑也无副作用）
- `common/cmd_weekday.py <date>` — 日期 → 周几查证（§7.2.1 强约束：含"X月X日（周X）"措辞前**必跑**）
- `common/cmd_debug_candidate.py --talent-id X` — 调试 dump（仅排查时用）

**运维只读**：

- `ops/cmd_health_check.py [--only db|imap|smtp|dashscope|feishu]` — 5 项体检
- `ops/cmd_db_migrate.py --status` — DB 迁移状态（不带 `--apply`）
- `auto_reject/cmd_scan_exam_timeout.py --dry-run` — 笔试超时候选人预览（§2.5）

### 2.2 自动触发的预览类（CV 录入）

一类窄中间态：当某种触发消息（通常是 HR 发了 CV 形状的文件）出现时会**自动**跑，它**不改状态**，但确实有真实成本（LLM 解析、DB 读、磁盘 I/O），产出一份结构化预览，而这份预览本身就是下一步写操作的**提案**。

- `intake/cmd_ingest_cv.py` — 规范案例。触发时机与方式见 §4.2。

规则：

- 当消息符合 §4.2 的 CV 检测规则时，**自动**跑，不走 §2.3.1 的 confirm 协议。命令自身的输出就是给 HR 看的"提案"。
- **把输出原样转发**给 HR。**不要**转述或总结字段 diff。
- 输出里带一到多个 `[OC_CMD_ON_CONFIRM*]` payload，这些 payload 本身是 §2.3 写操作命令的**提案**（`cmd_attach_cv.py` / `cmd_new_candidate.py` / `cmd_import_candidate.py`）。这些下游命令**仍然**要走完整的 §2.3.1 confirm 协议；HR 针对预览的自然语言回复就作为那次 confirm。
- **绝不**对非 CV 消息主动跑 `cmd_ingest_cv.py`；Boss 侧除非明确要求录入某个文件，否则**绝不**跑。

### 2.3 写类（改 DB 状态 和 / 或 发外部消息）

这一类的任何命令要么写 DB、要么发出站消息、要么两者都做。**每一条写类命令都必须在执行前拿到用户的 confirm，没有例外**——具体语义见 §2.3.1（atomic / declared chain / ad-hoc 三档）。

**双轮硬规则（v3.8 新增,事故源见 [INCIDENT_RULES.md §12](docs/INCIDENT_RULES.md#12-2026-05-10--3-人误删事故post_offer_followup-confirm-跳过)）**：
propose-confirm 必须**跨两条用户消息**，绝**不**接受"用户单条消息已经包含意图 + 授权词"作为合体 confirm。
即使用户消息里写了「X / Y 拒了 offer，**从人才库删除**」「**清掉**这几个人」「**直接删了**」这种看似 self-contained 的指令，agent 仍需：

1. 先 **propose**：把完整 CLI（`uv run python3 scripts/...`）+ `talent-id` + 当前 stage + "这条命令会做什么"逐字写在代码块里给用户看;
2. **下一轮**等用户**显式** confirm（破坏性命令需指名,见 §2.4）才执行。

这条规则对**所有** §2.3 写动作成立（`outbound.cmd_send` 邮件 / `talent.cmd_update` 改 stage / `talent.cmd_delete` 删档 / `feishu.cmd_calendar_create` 建日历 / `interview.cmd_result --result reject_*` 等）；破坏性命令（§2.4）在此基础上进一步强化"confirm 必须指名 talent_id"。

写类命令清单（v3.5 atomic CLI，全部经 `lib/cli_wrapper.py` 包裹 + 自验证 + 失败飞书告警）：

- **仅 DB**：`talent/cmd_add.py`、`talent/cmd_update.py`、`exam/cmd_exam_ai_review.py --save-event`（写 `talent_events` 审计；不带 `--save-event` 时是只读，仍然要走 propose 因为带 `--feishu` 会推飞书）
- **DB + IMAP / SMTP / 飞书（agent chain 的一步）**：`outbound/cmd_send.py`、`inbox/cmd_scan.py`、`inbox/cmd_analyze.py`、`feishu/cmd_calendar_create.py`、`feishu/cmd_calendar_delete.py`、`feishu/cmd_notify.py`
- **CV 录入下游（写 DB + 归档 CV）**：`intake/cmd_attach_cv.py`（要求 `--confirm`）、`intake/cmd_new_candidate.py`、`intake/cmd_import_candidate.py`。注意：`intake/cmd_ingest_cv.py` **不在**此列——它是 §2.2 自动触发预览类，不写 DB / 不发外部消息。
- **仅外部（发飞书 / 邮件）**：`intake/cmd_send_cv.py`、`common/cmd_interview_reminder.py`
- **DB + 外部（面试 / 笔试结果）**：`interview/cmd_result.py`、`exam/cmd_exam_result.py`
- **DB + 外部（破坏性）**：`talent/cmd_delete.py`、`common/cmd_remove.py` — 见 §2.4
- **运维写**：`ops/cmd_db_migrate.py --apply`（DB schema 迁移；通常运维手动跑，agent **不应**主动 propose，除非用户明确说"应用 pending migration"）、`ops/cmd_replay_notifications.py`（回放遗漏的飞书卡片，`--dry-run` 时只读）、`talent/cmd_normalize_cv_filenames.py` / `talent/cmd_rebuild_aliases.py`（CV 文件归档维护，极少用）

### 2.3.1 执行前 confirm 协议（强制）

任何 §2.3 写类命令在执行前都要走这个回路。**confirm 的语义按命令打包形态分三档**——三档没有冲突，按场景对号入座。

**通用回路**（所有写命令必走）：

1. **解析**出唯一 `talent-id`（走 §3）以及所有必需参数，包括 `--time` 用 `YYYY-MM-DD HH:MM` Asia/Shanghai。
2. **Propose**：把完全解析后的命令逐字写在代码块里给用户看，包含：
  - `uv run python3 scripts/...` 完整调用（每个参数都填好）；
  - 候选人姓名 + `talent-id` + 当前 stage（必要时先跑一次只读查询）；
  - 一行说明：这条命令会做什么（改 DB、发邮件、推飞书等）。
  - **二面笔试通过 / 安排二面专门约束**：当当前 stage 是 `EXAM_SENT` / `EXAM_REVIEWED`，且老板给了二面时间时，propose 只能是 `exam.cmd_exam_result --result pass --round2-time ...` 或 AGENT_RULES §4.5 的发邀请 chain；说明只能写“发送二面邀请邮件，邮件成功后进入 `ROUND2_SCHEDULING`，等待候选人确认”。**严禁**在同一个 propose 中写“创建飞书日历 / 更新为 `ROUND2_SCHEDULED` / 二面已确认”。这些属于候选人回信 confirm 后、老板第二次明确授权才允许进入的 §4.2 chain。
3. **等待**用户在下一轮显式 confirm。可接受的肯定词：`yes` / `ok` / `go` / `执行` / `确认` / `好`（以及同义表述）。沉默、追问、切换话题都算"未确认"。
4. **收到 confirm 后再执行**，且只执行 propose 时展示过的那条命令 / 那条 chain。用户在回复里改了任何参数 → 重新 propose 更新后的命令，回到第 3 步。
5. 命令失败 → §6 失败处理。

**confirm 三档**（按 propose 时的命令形态选其一）：


| 档别                       | 何时适用                                                                                                      | 一次 confirm 授权范围                         |
| ------------------------ | --------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| **Atomic**               | propose 时只展示**一条** atomic CLI（例如 `talent.cmd_update --stage X --force`）                                   | 仅那一条命令                                  |
| **Declared chain**       | propose 时按 [AGENT_RULES.md §4](docs/AGENT_RULES.md) 完整列出该 chain 的**所有 Step**（每个 Step 的 atomic CLI 全部展示出来） | 整条 chain 作为一个语义单元（chain 内每步无需再 confirm） |
| **Ad-hoc multi-command** | （**禁止**）                                                                                                  | —                                       |


**Ad-hoc multi-command 禁止**意味着：

- **绝不**临时把多条 atomic CLI 拼成"打个套餐一次 confirm"；
- **绝不**把没有 declared chain 的多步流程（"search → 想想看 → finalize → result"）一次性 confirm 通过；
- **跨场景**必须分别 confirm：例如改期 chain 跑完后老板再说"再发简历给老板"，那是新指令，必须重新 propose。

**chain-兜底规则**（v3.7，扩展自 v3.5.4；事故来源见 [INCIDENT_RULES.md §3](docs/INCIDENT_RULES.md#3-2026-04-21-1706--跨-stage-跳跃误走正常流程事故)）：如果老板的指令需要走 chain，但你**找不到匹配的** [AGENT_RULES.md §4](docs/AGENT_RULES.md) chain，**绝不**自己拼一个新 chain。正确做法是 **stop and ask**，按以下三条路径**只选一条**回报老板：

- **(P1) 老板话里有跨 stage 跳跃语义**（`直接跳` / `跳过` / `略过` / `强制` / `不要 X` / 跨 ≥2 个自然步骤） → 走 [§4.9 force-jump 单步](docs/AGENT_RULES.md#49-老板说直接跳到-x--§59-force-jump-单步)。措辞示例：「您说的是**直接跳到 stage X** 吗？我用 `talent.cmd_update --stage X --force --reason 'boss原话: …'` 单步推过去，**不发**任何候选人邮件 / **不建**日历。」
- **(P2) chain 形状识别得出但缺参数 / 不明确**（比如知道是"安排面试"但不知道哪一轮、时间是什么） → 让老板补全单一参数。措辞示例：「您能澄清一下吗：要发哪个模板的邮件？是一面还是二面？时间是？候选人指的是哪一位？」
- **(P3) 这个场景 §4 / §5 都没见过 / 完全不在 AGENT_RULES 任何条目里** → 退化到**单步 confirm 模式**（即 [§2.3.1 第一档 Atomic](#231-执行前-confirm-协议强制)），把决策权完全交还老板。措辞示例：「这个场景之前没出现过，AGENT_RULES.md 里**没有**对应 chain。为了避免错的 chain 不可逆地发邮件 / 建日历，我建议我们**一个动作一个动作**走，每一步我都把完整命令写出来等您 confirm 后再跑。请告诉我**第一步**要做什么（例如：先发某模板邮件给候选人？先把 stage 推到 Y？先建 / 删一个面试日历？先推一张飞书卡片告知 HR？）。我会 propose 那一条 atomic CLI 等您 confirm，跑完再问下一步。」

**P3 路径下的硬规则**：

1. 每一步都按 §2.3.1 **第一档 Atomic** confirm（**不**走第二档 declared chain）——全新场景的"chain"本身就是**边走边定**的，不应该批量授权。
2. 每条 atomic CLI 跑完后，把结果（DB 写入字段 / 邮件 `message-id` / 日历 `event_id` / 飞书 `severity`）**原样回报**给老板，**再**问"下一步要做什么"。
3. 老板未明说"下一步是 X" → **绝不**自己脑补；停在原地等。
4. 同样的全新场景出现 **≥ 2 次** → 提醒老板（飞书 `severity=info`）"这个场景已出现 N 次，建议固化为新的 §4 chain"，由人沉淀文档，**不是** agent 自动学习。

**禁止**（无论 P1/P2/P3）：(a) 拿别的 chain 改改参数凑合；(b) 用多个 atomic CLI 试错式拼接；(c) 通过看 CLI 错误信息迭代修正参数。**错的 chain 一旦执行，邮件 / 日历不可逆**。

**关键纠错**（force-jump 路径）：当老板说出**带跨 stage 跳跃语义**的指令——`直接跳到 X` / `直接进 X 阶段` / `略过 / 跳过 / 强制` / `忽略前置`——**唯一**正确路径是 [AGENT_RULES.md §4.9 force-jump 单步 chain](docs/AGENT_RULES.md#49-老板说直接跳到-x--§59-force-jump-单步)（`talent.cmd_update --stage <target> --force --reason "boss原话: …"`）。**不发**邮件、**不建**日历、**不更新**业务字段。识别规则见 [AGENT_RULES.md §5 速查表](docs/AGENT_RULES.md#5-表外的常见-intent)；事故源见 [INCIDENT_RULES.md §3](docs/INCIDENT_RULES.md#3-2026-04-21-1706--跨-stage-跳跃误走正常流程事故)。**绝不**走"先按正常流程推到 X"——那会真发候选人邮件。

**例外**：**没有**。本协议对"显而易见"的场景（`cmd_result.py ... --result pass`）、长链路里的每一步、用户已给出完整命令的情况，**同样**适用。

### 2.4 破坏性（§2.3.1 confirm 协议的严格超集）
> **⚠️ CRITICAL WARNING (2026-05-10 Incident)**: `talent/cmd_delete.py` **permanently removes candidates from the active database**. While it archives data to `data/deleted_archive/`, **restoration requires manual intervention and is not supported by standard CLI tools**. **There is NO soft-delete option**.

> **⚠️ CRITICAL WARNING (2026-05-16 Discovery)**: There is **NO** `REJECTED` stage in the database schema. Attempting to set `--stage REJECTED` will fail with a database constraint violation.

> **✅ CORRECT WORKFLOW FOR ONE-ROUND REJECTIONS**: When candidates are rejected after a single interview round (e.g., ROUND1_SCHEDULED → rejection), **DO NOT** attempt to use `REJECTED`. Instead:
> 1. Use `talent/cmd_update.py --talent-id <ID> --stage EXAM_REJECT_KEEP --force --reason "interview failed"`
> 2. **`--force` is required** because `ROUND1_SCHEDULED → EXAM_REJECT_KEEP` is not in `_NATURAL_TRANSITIONS` whitelist
> 3. This preserves the candidate in the active database with appropriate semantics for potential future re-engagement

> **✅ CORRECT WORKFLOW FOR TWO-ROUND REJECTIONS**: When candidates are rejected after second round interviews, use `talent/cmd_update.py --talent-id <ID> --stage ROUND2_DONE_REJECT_KEEP --reason "second round interview failed"` (no `--force` needed as this transition is in natural transitions whitelist).

> **✅ CORRECT WORKFLOW FOR OFFER REJECTIONS** (v3.8.2 拆桶后): When candidates reject offers but should be retained in the talent pool, **DO NOT DELETE**. Instead:
> 1. Use `talent/cmd_update.py --talent-id <ID> --stage OFFER_DECLINED_KEEP --reason "candidate declined offer"`
> 2. **No `--force` needed** — `POST_OFFER_FOLLOWUP → OFFER_DECLINED_KEEP` is in `_NATURAL_TRANSITIONS` (v3.8.2). The old workaround that推到 `ROUND2_DONE_REJECT_KEEP --force` is **deprecated** — that stage is now reserved strictly for "二面面试不过" (interview failure), while `OFFER_DECLINED_KEEP` cleanly captures "候选人 say no after offer".
> 3. This preserves the candidate in the active database with appropriate semantics for future re-engagement (e.g., 半年后候选人回心转意 → `talent.cmd_update --stage POST_OFFER_FOLLOWUP --force --reason 'candidate reconsidered'`).

> **✅ CORRECT WORKFLOW FOR OFFER REJECTIONS** (v3.8.2 拆桶后): When candidates reject offers but should be retained in the talent pool, **DO NOT DELETE**. Instead:
> 1. Use `talent/cmd_update.py --talent-id <ID> --stage OFFER_DECLINED_KEEP --reason "candidate declined offer"`
> 2. **No `--force` needed** — `POST_OFFER_FOLLOWUP → OFFER_DECLINED_KEEP` is in `_NATURAL_TRANSITIONS` (v3.8.2). The old workaround that推到 `ROUND2_DONE_REJECT_KEEP --force` is **deprecated** — that stage is now reserved strictly for "二面面试不过" (interview failure), while `OFFER_DECLINED_KEEP` cleanly captures "候选人 say no after offer".
> 3. This preserves the candidate in the active database with appropriate semantics for future re-engagement (e.g., 半年后候选人回心转意 → `talent.cmd_update --stage POST_OFFER_FOLLOWUP --force --reason 'candidate reconsidered'`).

这一类命令要么永久销毁数据，要么把候选人推到拒收终态。它们要求满足 §2.3.1 的全部条件**外加**：

- 泛泛的"yes / ok / 好"**不够**。confirm 必须显式指名破坏动作——例如 `"是，删除 t_xxx"`、`"confirm reject_delete for t_xxx"`、`"yes, remove 张三 (t_xxx)"`。
- **confirm 必须晚于 propose（强制双轮，v3.8 强化）**：用户第一条消息表达意图 → agent propose（写出完整命令 + talent-id + 当前 stage + "这条命令会做什么"）→ 用户**下一条消息**显式指名破坏动作才执行。**绝不**把用户**单条消息**同时当作 propose + confirm 合体——即使该消息已经包含"删除 / remove / 清掉 / 不要了"等动词,agent 仍**必须**先 propose 让用户在下一轮看见完整 CLI 才能 confirm。
- confirm 必须与 propose **同一轮回应**（即"上一条 agent 消息是 propose,这一条用户消息是 confirm"）；**绝不**依赖再上一轮（即用户首次表达意图那轮）的意图残留。
- 如果用户回复是肯定的但没有指名破坏动作，重新问一遍。
- **多候选人破坏性操作必须分别 propose-confirm（v3.8 强化）**：如果用户一句话说"删除 X / Y / Z 三个候选人"——绝**不**允许打包跑成 3 条 cmd_delete。agent 必须**逐一**为每个 talent_id propose 一条独立的 cmd_delete,每一条等用户单独指名 confirm（"是,删除 t_xxx"），跑完一条**再**propose 下一条。这是 §2.3.1 "ad-hoc multi-command 禁止"在破坏性场景下的硬性强化。
- **意图陈述 ≠ 授权（v3.8 强化）**：用户原话里"X 拒了 offer,从人才库删除" / "这些人不要了" / "X / Y / Z 都不入职了,清掉" 等，**仅是**业务意图陈述，**不是** propose-confirm 流程的"已 confirm"信号。agent 必须按本节其余条款走完整流程；**绝不**把"自然语言里的删除动词"识别为已 confirm。事故规则录见 [INCIDENT_RULES.md §12](docs/INCIDENT_RULES.md#12-2026-05-10--3-人误删事故post_offer_followup-confirm-跳过)。

命令清单：

- `talent/cmd_delete.py` — v3.5 唯一物理删档路径；自动归档完整 snapshot + emails。
- `common/cmd_remove.py` — 历史删档命令；与 `talent/cmd_delete.py` 等价，agent 优先用后者。
- `interview/cmd_result.py ... --result reject_delete` — 拒信 + 从人才池移除。**副作用（自 2026-04-22）**：自动先经 `outbound.cmd_send --template rejection_generic` 发拒信再删人。`--skip-email` **仅在**老板已线下手发拒信时使用。
- `exam/cmd_exam_result.py ... --result reject_delete` — 同上，笔试分支。
- `intake/cmd_attach_cv.py` — 要求 CLI 上带 `--confirm` **并且**同一轮还要有一次自然语言的匹配 confirm。

**单值规则**：若用户**没有**明确说要删，唯一允许的"拒"是 `reject_keep`（一面场景则保留在当前 stage）。**绝不**默认选 `reject_delete`。

### 2.5 自动拒（系统驱动，仅笔试超时）

> 简化背景见 [INCIDENT_RULES.md §1](docs/INCIDENT_RULES.md#1-2026-04-23--自动拒12h-软缓冲队列全部移除)。agent **不应**自己**真跑**任何 auto_reject 命令——现在只剩一个 cron 驱动的脚本，没有任何面向老板的命令。

`auto_reject/` 当前**只有一个**脚本：`cmd_scan_exam_timeout.py`（cron 专用）。agent 唯一允许的调用是 `--dry-run` 预览（只读，列出哪些候选人会被自动拒；属 §2.1 只读类，不需要 confirm）。**没有** propose / cancel / execute_due / list / pending_store / llm_classify 一类「队列」概念，**没有** 12h 缓冲窗口或「合法改期白名单」。

cron 行为（每个 tick，**v3.8.3**）：在 `EXAM_SENT`、`exam_sent_at` ≥ `--threshold-days`（默认 3）且其后无入站邮件、且 `talent_emails` 中**没有**已发过的 outbound `rejection` 邮件的候选人——执行
  1. `outbound.cmd_send --template rejection_exam_no_reply --context rejection`（拒信）
  2. `talent.cmd_delete --confirm-delete-talent <tid> --actor auto_reject.cmd_scan_exam_timeout`（物理删档；自动归档到 `data/deleted_archive/<tid>_<UTC>.json` + `<tid>_emails.json` + 候选人 CV 目录）
  3. 推事后飞书通知卡给老板（含 archive 路径，方便人工 `talent.cmd_undelete` 恢复）

第 1 步失败则**不**删（candidate 留在 `EXAM_SENT`，`cli_wrapper` 自动飞书告警）。第 2 步失败时**也不**重发拒信——`find_timeout_candidates` 用第 0 项的 outbound-rejection 二次防护拦截下个 cron tick（v3.5.11 引入的幂等保护，v3.8.3 保留）；HR 看到飞书告警后人工 `talent.cmd_delete` 即可。

历史：v3.5.11~v3.8.2 期间这一步用 `EXAM_REJECT_KEEP` 留池而非物理删档（事故应激修复，详见 [INCIDENT_RULES.md §15](docs/INCIDENT_RULES.md#15-2026-05-11--cron-auto_reject-从留池回退到物理删档)）；v3.8.3 起回退到删档,但保留了 v3.5.11 的两项核心防护：(a) DB CHECK 接受 `'rejection'` context、(b) `has_outbound_rejection` 二次防护，因此事故面已堵死。

任何"迟到改期"意图都走 [AGENT_RULES.md §4.3 改期 chain](docs/AGENT_RULES.md#43-候选人改期hr老板驱动回到-scheduling-等候选人-confirm)，由老板决策。如果老板要**手动**拒 + 删候选人，用 §2.4 破坏性命令 `interview/cmd_result.py ... --result reject_delete`（自动先经 `outbound.cmd_send --template rejection_generic` 发拒信再删；只有老板已线下手发拒信时才传 `--skip-email` — 见 [INCIDENT_RULES.md §2](docs/INCIDENT_RULES.md#2-2026-04-22--reject_delete-默认必须发拒信)）。

---

## 3. 歧义解析规则

大多数线上事故来自对"信息不全的请求"直接动手。选任何写类命令**之前**，应用这组规则。


| 缺失 / 歧义                                                          | 要求的解析方式                                                                                                                      |
| ---------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| 身份（只有姓名，没有 `talent-id`）                                          | 跑 `common/cmd_search.py --query <name>`，用返回的 `talent-id`。                                                                    |
| 搜索结果多条命中                                                         | 把匹配项都列给用户，问他指哪一个。**绝不**按字母序、时间新鲜度、或直觉挑。                                                                                      |
| 代称（`他`、`她`、`上周那个候选人`、`那个女生`）                                     | 仅当同一轮里已经锁定唯一候选人时才接受。否则去 search。                                                                                              |
| 面试 round 未指定                                                     | 问。不要仅凭 stage 推断，除非 stage 唯一决定 round（`ROUND1_`* → 1，`ROUND2_*` → 2）。                                                          |
| 时间是自然语言（`明天下午三点`）                                                | 解析为显式的 `YYYY-MM-DD HH:MM`，时区 **Asia/Shanghai (+08:00)**——这是 `scripts/lib/core_state.py` 打时间戳时用的服务器硬时区。在回复里把解析后的时间原样 echo 回去。 |
| `--result` 未指定（pass / reject_keep / reject_delete / pass_direct） | 问。不要默认。                                                                                                                      |
| "拒" 但没有 keep / delete 指示                                         | 见 §2.4：`reject_delete` 要求同轮显式 confirm；否则用 `reject_keep`。                                                                     |


**硬规则**：任何写类命令必须 (a) 锁定**唯一** `talent-id`，(b) 通过 §2.3.1 的 confirm 协议才能执行。如果输入里没有给出唯一 `talent-id`，先解析身份——再 propose 并等待。

---

## 4. CV 入库路由（SKILL 独占）

> **本节是 SKILL 独占的业务路由**——CV 入库不属于 agent chain（不读 `talent_emails.ai_payload`、不基于 stage 拼 chain），而是一种"附件触发预览 → HR 自然语言 confirm"的交互模式，所以放在 SKILL 层而不是 AGENT_RULES。
>
> **其他所有意图**（面试 / 笔试 / 改期 / 暂缓 / 面试结果 / 笔试结果 / Offer / WAIT_RETURN / force-jump / 一面派单 / 候选人查询）的 stage × intent → chain 映射全部在 [AGENT_RULES.md §4 / §5](docs/AGENT_RULES.md)。本文件不再镜像。

### 4.1 候选人录入与流程推进


| 意图                                 | 命令                                                                                                                   |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| HR 发 `【新候选人】` 文本模板                 | `uv run python3 scripts/intake/cmd_new_candidate.py --template "<raw multi-line message>"`                           |
| `【导入候选人】` 历史候选人                    | `uv run python3 scripts/intake/cmd_import_candidate.py --template "<raw multi-line message>"`                        |
| CV 附件（PDF / DOCX）— **自动触发**，见 §4.2 | `uv run python3 scripts/intake/cmd_ingest_cv.py --file-path <path> --filename <filename>`                            |
| 把 CV 挂到已有候选人（`cmd_ingest_cv` 预览之后） | `uv run python3 scripts/intake/cmd_attach_cv.py --talent-id <id> --cv-path <path> --confirm [--field key=value ...]` |
| 把 CV PDF 发给老板（默认）或 HR              | `uv run python3 scripts/intake/cmd_send_cv.py --name "<name>" [--to {boss,hr}]` *（默认 `boss`；`--to hr` 发给 HR）*        |

### 4.2 面试通过后安排笔试（常见场景）

当候选人一面通过需要安排笔试时，使用以下两步流程：

1. **发送笔试邀请邮件**：
   ```bash
   uv run python3 scripts/outbound/cmd_send.py --talent-id <talent-id> --template exam_invite --context exam
   ```

2. **更新候选人状态**：
   ```bash
   uv run python3 scripts/talent/cmd_update.py --talent-id <talent-id> --stage EXAM_SENT
   ```

注意：没有单独的 `cmd_exam_invite.py` 命令，必须使用 `outbound/cmd_send.py` 配合 `exam_invite` 模板。


注意：

- 多行 `--template` 要传真带换行的字符串。bash 里用 heredoc：`--template "$(cat <<'EOF' ... EOF)"`，或 `$'line1\nline2'`。**不要**传双引号包裹的字面量 `"\n"`——bash 不会展开它。
- `cmd_parse_cv.py` 已**删除**（A4.1, v3.8.7）。解析逻辑搬到 `lib/cv_parser.py`，agent 主路径仍走 `intake.cmd_ingest_cv.py`，**不要**自己 import `cv_parser`——它是 helper 模块不是 CLI 入口。
- Boss 说"看简历 / 把某某的简历发过来"——用 `cmd_send_cv.py` **不带** `--to`（默认就是 boss）。只有明确要求发给 HR 时才带 `--to hr`。

### 4.2 CV 自动检测与路由

当飞书群里来了一条带附件的消息，先判断它是不是候选人 CV，**再**决定跑什么。

**Step 1 — 这是不是一份 CV？** 如果以下任一条件满足，就按 CV 处理：

- 文件名匹配 CV 形态：岗位名 / 城市 / 薪资 + 候选人姓名 + `XX年应届生` 或 `实习生`（例如 `量化研究员实习-上海-500元_天-张三-2026年应届生.pdf`）。
- 打开文件正文后，包含多项：岗位、候选人姓名、`应届生` / `实习生`、邮箱地址、电话、学校 / 学历。
- 消息上下文明确把这份文件定性为简历（HR 在附件旁边说"这是简历" / "新候选人" / "请入库"）。

以上都不满足则**不**路由到 `cmd_ingest_cv.py`。让 HR 澄清，或走通用附件处理。

**Step 2 — 从 Hermes Gateway 消息里拆出文件路径**（按优先级排）：


| 优先级 | 入站消息形态                                                                          | 传给命令的参数                                                                                               |
| --- | ------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| 0   | `[The user sent a document: 'xxx.pdf'. The file is saved at: /.../xxx.pdf ...]` | `--file-path "/.../xxx.pdf" --filename "xxx.pdf"`                                                     |
| 1   | `[media attached: <absolute-path>.pdf]`                                         | `--file-path "<absolute-path>.pdf" --filename "<basename>"`                                           |
| 2   | 回复 / 引用带 `file_key` 的消息                                                         | 先在 `<workspace_root>/data/media/inbound/` 下按 key / name 找本地文件；找到用 `--file-path`；否则 `--file-key <key>` |


Hermes 消息给的路径已经是绝对路径。**原样**传过去，不要改写成 `<workspace_root>`。

**Step 3 — 跑 `cmd_ingest_cv.py`**（自动触发，见 §2.2）。把输出原样转发给 HR，然后等 HR 回复再做下一步（§7.4 说明两种可能的输出形态）。

---

## 5. 查询规则

把用户意图映射到**最窄的**规范命令。不要用更宽的命令重新推导出一个派生视图。


| 用户意图                   | 命令                                                                                           | 不要这么做                                                                                              |
| ---------------------- | -------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| "把所有人都列一下" / "所有候选人"   | `talent/cmd_list.py`（v3.3 首选） 或 `common/cmd_status.py --all`                                 | 重新分桶。改计数。凭 stage 文本推测活跃 / 不活跃。                                                                     |
| "某 stage 的人有谁"         | `talent/cmd_list.py --stage <STAGE>`（精确过滤）                                                   | 跑 `--all` 再自己 grep。                                                                                |
| "有哪些候选人邮件还没分析"         | `talent/cmd_list.py --has-unanalyzed`                                                        | 自己拼 SQL。                                                                                           |
| "谁还在进行中？" / "活跃候选人"    | `common/cmd_search.py --all-active`                                                          | 跑 `--all` 再自己猜哪些 stage 算活跃。                                                                        |
| "查 X 这个人"              | `common/cmd_search.py --query <name>` → 若唯一，`talent/cmd_show.py --talent-id <id>`（v3.3 单人首选） | 跳过 search 直接猜 ID；或用 `cmd_status` 看不到邮件统计。                                                          |
| "X 和系统通信历史"            | `inbox/cmd_review.py --talent-id <id>`                                                       | 自己拼 `talent_emails` SQL。                                                                           |
| "今天 / 明天 / X 号有没有面试？"  | `common/cmd_today_interviews.py [--date YYYY-MM-DD]`                                         | 用 `--all-active` 重建。                                                                               |
| "X 月 Y 日是周几"           | `common/cmd_weekday.py <date>`（§7.2.1 强约束）                                                   | LLM 心算（已多次出错，见 [INCIDENT_RULES.md §11](docs/INCIDENT_RULES.md#11-v3513--默认时间格式硬规定-asia-shanghai)）。 |
| "看一下 round1_invite 模板" | `template/cmd_preview.py --template round1_invite --demo`                                    | 凭记忆复述模板文案。                                                                                         |
| "系统正常吗 / 体检"           | `ops/cmd_health_check.py`                                                                    | 凭主观判断。                                                                                             |


`**cmd_status.py --all` / `talent.cmd_list` 的分组护栏**：

- 默认行为：原样返回命令输出的平铺列表。
- 用户明确要求分组汇总时，只按**精确的 current stage 标签**分组，**不要**自创更宽的桶。
- 永远**不要**把任何 `*_DONE`_* stage 放到 `一面阶段`、`二面阶段`、`笔试阶段` 这类进行中桶里。
- 具体来说：`ROUND2_DONE_REJECT_KEEP / 二面未通过（保留）` **不是** `二面阶段` 的一部分；`EXAM_REJECT_KEEP / 笔试未通过（保留）` **不是** `笔试阶段` 的一部分；`OFFER_DECLINED_KEEP / 已拒 Offer（保留人才库）` **不是** `Offer 阶段` 的一部分（v3.8.2 起从 `ROUND2_DONE_REJECT_KEEP` 拆出的独立终态）。它们都是"保留在人才池"的叶子态。
- 不确定某个 stage 是进行中还是终态时，逐字引用 stage 标签然后停下。**不要**自创桶。

---

> **Stage 状态机** 和 **写操作前置条件**（含每个 atomic CLI 自带的 stage-gate）都在 [AGENT_RULES.md §2](docs/AGENT_RULES.md#2-stages) + §4 各 chain 的「硬规则」块。本文件不再镜像。

代码权威源：`scripts/lib/core_state.py::STAGE_LABELS` + 各 atomic CLI 的 `ensure_stage_transition()` 校验。

---

## 6. 失败处理

把每一次非零退出或错误归到四桶之一，对应回应。


| 分类                                                        | 识别                                                                        | 正确下一步                                                                                                                                                                                                                                                                  |
| --------------------------------------------------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Not found**                                             | `ERROR: 未找到候选人` / search 空结果                                              | 让用户澄清身份。建议一条 `cmd_search.py` 查询。                                                                                                                                                                                                                                       |
| **Ambiguous**                                             | search 返回 >1 条                                                            | 把匹配项列出来（姓名 + `talent-id` + stage）让用户选。**不要**动手。                                                                                                                                                                                                                        |
| **Invalid state / args**                                  | `ERROR: 当前阶段不允许` / `argparse` 用法错 / 约束违反                                  | 原样上报。给出正确命令或能揭示当前状态的查询。**永远不要**盲目改参数重试。                                                                                                                                                                                                                                |
| **Infra / transient**                                     | DB 连接错、飞书 API 错、IMAP 错、含网络关键词的 traceback                                  | 报成基础设施级故障。**不**建议重试业务命令；建议去查配置 / 连通性。                                                                                                                                                                                                                                  |
| **chain 中间一步失败**                                          | run_chain 短路；`chain_result["ok"]=False`；`chain_result["failed_step"]=…`   | 详见 [AGENT_RULES.md §1](docs/AGENT_RULES.md#1-overview)（chain 失败模型）。常见模式：`outbound.cmd_send` 成功但 `talent.cmd_update` 失败 → 邮件已发出、DB 未推进 → `feishu.cmd_notify --severity critical --title "邮件已发但状态未更新"` 附 talent_id + sent_at；老板手动 `talent.cmd_update` 补救。**不**自动回滚（不可逆）。 |
| `**outbound.cmd_send --use-cached-draft` 失败：没有 draft 字段** | rc=2；stderr `没有 draft 字段`                                                 | 这是 `inbox.cmd_analyze` 在该 stage 没生成草稿（intent 不在 `prompts/post_offer_followup.json` valid_intents 里，或 LLM 限流），改推 `feishu.cmd_notify --severity warn --title "草稿缺失，需手动起草"`。                                                                                              |
| **auto_reject scan: send failed**                         | `auto_reject.cmd_scan_exam_timeout` stderr 出现 `⚠ 发拒信失败: ...` 且 `failed=N` | 候选人**没**被删（故意——failure isolation）。`cli_wrapper` 已推飞书告警。排查 SMTP / 模板，让 cron 下个 tick 重试。**不要**手动跑 scanner 真跑（会和 cron 撞车）。                                                                                                                                                |
| **auto_reject scan: delete failed**（v3.8.3）                | `auto_reject.cmd_scan_exam_timeout` stderr 出现 `⚠ 拒信已发 ... 但 cmd_delete 失败: ...` 且 `failed=N` | 拒信**已发出**、候选人仍在 `EXAM_SENT`（DB 未推进）。下个 cron tick 被 `has_outbound_rejection` 二次防护拦下,**不会**重发拒信。HR 收到飞书告警后手动 `talent.cmd_delete --talent-id <tid> --confirm-delete-talent <tid> --reason 'auto_reject manual cleanup'` 即可。**不要**人工再发一遍拒信。 |


**永远不**把失败用乐观口吻包装。命令失败了，用户状态就是没前进。

---

## 7. 结果呈现

保留命令语义。精度要紧时，优先原样引用命令措辞，而不是转述。

**通用规则**：回复里只能包含命令**真正返回**的字段。**绝不**添加"上次动作"、"下一步"、"待办"或任何命令没输出的派生信号。调用方要的字段命令没返回时，跑一条更具体的命令（如 `cmd_status.py --talent-id <id>`）——不要推断。

### 7.1 候选人列表

列表响应里每一行，**精确**包含查询命令实际返回的字段。至少包括：

- 显示名
- `talent-id`
- 精确 stage 标签（中英双语可接受：`ROUND2_DONE_REJECT_KEEP / 二面未通过（保留）`、`OFFER_DECLINED_KEEP / 已拒 Offer（保留人才库）`）

附加字段（排期时间、确认状态、邮箱等）**仅在**命令对该行实际返回时才包含。除非用户明确要求，否则**不要**按自定义桶分组。

用户明确要求分组时，安全顺序：

1. 按精确 stage 标签分组；
2. 只在命令本身已返回该桶，或 skill 里明确定义为无损桶时，才合并成更宽的桶；
3. 任何 `*_DONE_`* stage 优先用 `已结束`、`保留人才池`、`其他状态` 这类终态桶——**绝不**放进进行中的 round 桶。

### 7.2 单候选人详情

用 `cmd_status.py --talent-id <id>` 返回的字段回复。**不要**从 stage 合成"下一步"。用户问"接下来呢"时：

- 引用 stage 让他自己定，或
- 把 [AGENT_RULES.md §4 / §5](docs/AGENT_RULES.md) 里与该 stage 匹配的 chain 作为选项列出来（仅 CV 路径见本文件 §4）。

PII 见 §8。

### 7.2.1 时间措辞护栏

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

### 7.3 写结果

写命令成功后，只回显命令本身报告的内容：候选人姓名、`talent-id`、命令打印出来的新 stage、以及命令明确日志过的排期 / 通知确认。

### Calendar Event Management Critical Parameters

When creating interview calendar events with `feishu.cmd_calendar_create.py`, **these parameters are commonly missed but critically important**:

### Required Parameters for First-Round Interviews:
- **`--round 1`** - **MUST be explicitly specified** (defaults to `--round 2` which creates "二面" events)
- **`--candidate-name "Full Name"`** - **MUST be provided** (without this, calendar title shows talent_id like "[一面] t_xxx" instead of "[一面] Candidate Name")
- **`--candidate-email "email@domain.com"`** - **MUST be provided** (required for proper calendar integration)
- **`--duration-minutes 30`** - **MUST be set for first-round** (defaults to 60 minutes, but first-round should be 30 minutes per skill specifications)

### Automatic Attendees:
- Boss (`feishu.boss_open_id`) and Polaris (`feishu.polaris_open_id`) are **automatically invited** to all interviews
- Use `--extra-attendee` for the assigned interviewer from routing (master / bachelor / cpp)

### Correct Command Template:
```bash
uv run python3 scripts/feishu/cmd_calendar_create.py \
  --talent-id t_xxx \
  --time "YYYY-MM-DD HH:MM" \
  --round 1 \
  --duration-minutes 30 \
  --attach-cv \
  --candidate-name "Candidate Full Name" \
  --candidate-email "candidate@email.com"
```

### Verification Checklist:
✅ Calendar title shows "[一面] Candidate Name" (not talent_id)  
✅ Event duration is 30 minutes (not 60)  
✅ Round is correctly labeled as "一面" (not "二面")  
✅ CV attachment is present  

**Common Failure Modes:**
- Missing `--round 1` → Creates "二面" calendar events incorrectly
- Missing candidate name/email → Shows confusing talent_id in calendar title
- Missing duration → Uses 60-minute default instead of 30-minute standard

### §5.11 Complete Workflow for Refreshing Interview Schedules

When refreshing existing interview schedules (e.g., to update CV attachments or fix incorrect calendar events), follow this complete workflow:

**Step 1: Get candidate information**
```bash
# Get full candidate details including name and email
uv run python3 scripts/talent/cmd_show.py --talent-id <TALENT_ID> --json
```

**Step 2: Get interviewer assignment**  
```bash
# Determine the correct interviewer for this candidate
uv run python3 scripts/intake/cmd_route_interviewer.py --talent-id <TALENT_ID> --json
```

**Step 3: Delete old calendar event**
```bash
# Remove the existing calendar event
uv run python3 scripts/feishu/cmd_calendar_delete.py --event-id <OLD_EVENT_ID>
```

**Step 4: Create new calendar event with complete parameters**
```bash
# Create new event with ALL required parameters
uv run python3 scripts/feishu/cmd_calendar_create.py \
  --talent-id <TALENT_ID> \
  --time "<ORIGINAL_TIME>" \
  --round 1 \
  --duration-minutes 30 \
  --attach-cv \
  --candidate-name "<CANDIDATE_FULL_NAME>" \
  --candidate-email "<CANDIDATE_EMAIL>" \
  --extra-attendee "<INTERVIEWER_OPEN_ID>"
```

**Critical Requirements:**
- Must extract `candidate-name` and `candidate-email` from Step 1 output
- Must extract `interviewer_open_ids` from Step 2 output for `--extra-attendee`
- All participant types will be invited: Boss (auto) + Polaris (auto) + Assigned Interviewer (via `--extra-attendee`)
- Never assume candidate information - always fetch from database
- Always verify the new calendar event shows proper candidate name in title

**Why this workflow matters:**
- Prevents creation of calendar events with confusing talent_id titles
- Ensures correct interviewer assignment based on candidate profile
- Maintains proper 30-minute duration for first-round interviews
- Guarantees CV attachment is properly linked to the calendar event

## 7.4 CV 录入预览（两种分支）

`cmd_ingest_cv.py`（§2.2）会产出两种结构化预览之一。**把预览正文原样转发给 HR**，并把嵌入的 payload 当作下一步 §2.3 写命令的 proposal。

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

两种分支下，下游 `cmd_attach_cv.py` / `cmd_new_candidate.py` / `cmd_import_candidate.py` 的执行依然要走 §2.3.1——HR 针对预览的显式回复**就是**那次 confirm；除非 HR 改了任何参数，否则**不再**要第二次 confirm。

---

## 8. 隐私 / PII

招聘数据是个人数据。按"最小必要披露"处理。

- 默认列表视图：`姓名 + talent-id + stage [+ 下一步]`。除非用户明确问，否则**不**附邮箱、电话、微信。
- 老板需要联系某位候选人时，**一次一个渠道**披露，优先选场景隐含的那个。
- **绝不**把完整 CV 文本、身份证号、银行相关字段贴进回复，除非用户显式要求。
- 搜索结果默认每位候选人**一行**。后续追问再展开。

---

## 9. 反模式（通用 12 条）

> 本节只列与**对话 / 路由 / confirm / 措辞 / PII** 相关的高危通用反模式。**业务 / chain 反模式**（force-jump 必走 §4.9 / NEW 阶段一面派单必走 §4.1 / 一面派单不许硬编码 open_id / 编造时间凑参数 / `--set` 伪造字段绕过 stage-gate 等）→ 各 chain 的「硬规则」块见 [AGENT_RULES.md §4](docs/AGENT_RULES.md#4-scenarios)。**事故型反模式**（带版本号 / 日期：v3.5.x 修过的 bug、`doc_<hex>_` 前缀、`--skip-email` 默认、`cmd_parse_cv` 废弃、`exam.fetch_exam_submission` 重拉、`_DONE_` 旧 stage 名、`data/followup_pending/` 文件队列、`auto_reject` 队列、"完整信息"两标题敷衍 等）→ [INCIDENT_RULES.md](docs/INCIDENT_RULES.md)。

下列 12 条**都不要**做：

1. **跑写命令前没解析唯一 `talent-id`**。任何 §2.3 写命令必须先按 §3 锁定唯一身份；多人重名时把匹配项列出来让用户选，**绝不**按字母序 / 时间 / 直觉挑。
2. **跑 §2.3 写命令前没 propose / 没拿到显式 confirm**（§2.3.1）。本规则对每条写命令、每个"显而易见"的场景都适用，无例外。
3. **把用户首次请求当作预授权**。即便用户在第一句已说出完整命令，仍要重新 propose 解析后的版本并等一次新的肯定。
4. **违反 §2.3.1 三档 confirm 协议**：临时把多条 atomic CLI 拼成"打个套餐一次 confirm"；或把没在 [AGENT_RULES.md §4](docs/AGENT_RULES.md#4-scenarios) declared 的多步流程一次性 confirm 通过。**禁止 ad-hoc multi-command**。
5. **对泛泛 "yes" 就跑 §2.4 破坏性命令**。confirm 必须**指名**破坏动作（如 `"是，删除 t_xxx"`、`"confirm reject_delete for 张三 (t_xxx)"`）。
6. **把 `reject_delete` 当默认**。用户没明确说要删时，唯一允许的"拒"是 `reject_keep`。
7. **把命令失败用乐观口吻包装**。命令失败 = 用户状态没前进。按 §6 四分类如实上报，不要"已为您处理 / 已搞定"这种措辞。
8. **不按原样转发 CV 预览**。`cmd_ingest_cv.py` 输出**逐字**给 HR，不要转述、不要总结字段 diff、不要从同一份预览里同时执行 `[OC_CMD_ON_CONFIRM_UPDATE]` 和 `[OC_CMD_ON_CONFIRM_ARCHIVE]`（互斥分支，见 §7.4）。
9. **把命令输出重分到命令没返回的自创桶**。特别是把 `*_DONE`_* stage（如 `ROUND2_DONE_REJECT_KEEP`、`EXAM_REJECT_KEEP`）或 `OFFER_DECLINED_KEEP` 放进进行中的"二面阶段" / "笔试阶段" / "Offer 阶段"桶里——这些都是终态叶子态。**特别警告**：v3.8.2 之前 `ROUND2_DONE_REJECT_KEEP` 同时承载"二面失败留池"与"拒 offer 留池"两类语义,自动 bucket 时把它们合并展示是**事故级错误**——那是文档已知的 squat,事故源见 [INCIDENT_RULES.md §14](docs/INCIDENT_RULES.md#14-2026-05-11--拒-offer-留池语义混桶offer_declined_keep-拆出)。stage 表见 [AGENT_RULES.md §2](docs/AGENT_RULES.md#2-stages) + `core_state.py::STAGE_LABELS`；旧 stage 名遗留见 [INCIDENT_RULES.md §10](docs/INCIDENT_RULES.md#10-v34-之前--旧-stage-名带-_done_--offer_handoff-已合并)。
10. **在命令返回的时间戳上追加未核对的自然语言日历措辞**（"周几"、"本周日"、"明天下午"）。详见 §7.2.1；只在命令自身返回该字段、或 agent 已用 `common.cmd_weekday` 等确定性查询核对过时才允许。
11. **路径处理违规**：(a) 把 Hermes Gateway 给的绝对路径改写成 `<workspace_root>`-相对形式（原样传过去）；(b) 反过来把宿主机绝对路径硬编码进 skill 正文 / 推荐命令；(c) 同一回复里 `uv run python3 scripts/...` 与裸 `python3 <group>/...` 混用。
12. **PII 过度披露**。默认列表只给 `姓名 + talent-id + stage`；邮箱 / 电话 / 微信 / 完整 CV 文本只在用户明确要求时才出（详见 §8）。**绝不**因为 LLM 想"显得专业"就主动贴联系方式。

**找不到匹配 chain / 信息不全 / 命令拒收时**：见 §10 stop and ask 规则。

---

## 10. 升级 / 停止规则（stop and ask）

agent 在以下任一情形**必须停下、不执行任何写命令**，并把情况按下面的措辞回报给老板 / HR，让人来决定：


| 情形                                                                | 回报措辞模板                                                                                                                                          |
| ----------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| 找不到匹配的 [§4](docs/AGENT_RULES.md#4-scenarios) chain — **P1 跨 stage 跳跃语义**         | "您说的是**直接跳到 stage X** 吗？我用 `talent.cmd_update --stage X --force --reason 'boss原话: …'` 单步推过去，**不发**任何候选人邮件 / **不建**日历。"                                                                              |
| 找不到匹配的 [§4](docs/AGENT_RULES.md#4-scenarios) chain — **P2 chain 形状对但缺参数**           | "您能澄清一下吗：要发哪个模板的邮件？是一面还是二面？时间是？候选人指的是哪一位？"                                                                                                                                                      |
| 找不到匹配的 [§4](docs/AGENT_RULES.md#4-scenarios) chain — **P3 全新场景**（§4 / §5 都没见过）       | "这个场景之前没出现过，AGENT_RULES.md 里**没有**对应 chain。为避免错的 chain 不可逆地发邮件 / 建日历，建议我们**一个动作一个动作**走，每一步我把完整命令写出来等您 confirm 后再跑。请告诉我**第一步**要做什么。" → 之后每条命令走 [§2.3.1 第一档 Atomic](#231-执行前-confirm-协议强制) 单步 confirm |
| 候选人身份不唯一 / 重名                                                     | 把所有匹配项列出（姓名 + `talent-id` + 当前 stage），问用户指哪一个。**不要**自己挑。                                                                                        |
| 必需参数缺失（`--time`、`--result`、`--round`）                             | 列出缺什么 + 每个允许值。**不要**默认填。                                                                                                                        |
| 命令返回 `Invalid state / args`                                       | 原样上报错误信息 + 跑一次 `cmd_status.py --talent-id <id>` 揭示当前真实状态。**绝不**盲目改参数重试。                                                                         |
| Infra / DB / 飞书 / IMAP 故障                                         | 报成基础设施级故障并停下；提示运维去查配置 / 连通性，引用 [docs/OPERATIONS.md §6](docs/OPERATIONS.md#6-故障排查速查)。                                                            |
| chain 中间一步失败（如邮件已发但 `talent.cmd_update` 失败）                       | 推 `feishu.cmd_notify --severity critical` 并把失败 step + 已成功的副作用如实告诉老板。**不**自动回滚（不可逆）。详见 §6 + [AGENT_RULES.md §1](docs/AGENT_RULES.md#1-overview)。 |


**部署 / cron / symlink / 环境变量** 等运维问题不在 SKILL.md，全部在 [docs/OPERATIONS.md](docs/OPERATIONS.md)。agent 在线上对话中**不需要**读 OPERATIONS.md；只在用户明确问"怎么部署 / 重启 / cron 怎么调 / 软链断了"时引用。

---

## 11. 总结清单

回复前确认：

- 已按 §0 决策主循环完成 5 步分诊（A. CV / B. 只读 / C. 写 / D. 破坏性 / E. 模糊）。
- 意图已映射到一个真实 CLI 命令（CV 入库 → §4；其他意图 → [AGENT_RULES.md §4 / §5](docs/AGENT_RULES.md)）。
- 安全等级（§2）已尊重：只读可随便跑；§2.2 自动触发预览仅在 §4.2 CV match 时跑；**每条 §2.3 写命令都先 propose、再等显式 confirm（§2.3.1）**；§2.4 破坏性命令拿到了**指名**破坏动作的肯定。
- §2.3.1 三档 confirm 协议没违反：没有 ad-hoc multi-command；declared chain 在 propose 时所有 Step 都展示了。
- 所有写命令都已解析出唯一 `talent-id`。
- CV 录入：`cmd_ingest_cv.py` 仅在 §4.2 match 时自动触发；输出原样转发；按 HR 回复从 `UPDATE` / `ARCHIVE` / `CONFIRM` 中**只**跑其一（§7.4）。
- 任何 `--time` 都是 `YYYY-MM-DD HH:MM` Asia/Shanghai (+08:00)，且已 echo 回给用户；自然语言日历措辞按 §7.2.1 护栏。
- 回复里的 stage 标签与 [AGENT_RULES.md §2](docs/AGENT_RULES.md#2-stages) / `core_state.py::STAGE_LABELS` 一致；没有把 `*_DONE`_* stage 当成进行中桶。
- 查询请求走规范查询命令，不重建派生视图（§5）。
- 提议的所有脚本都在 [AGENT_RULES.md §3](docs/AGENT_RULES.md#3-commandsatomic-cli) atomic CLI 清单里；没有 auto_reject 的「队列 / 缓冲窗口 / 合法改期白名单」一类概念（§2.5）。
- agent 侧没有真跑 `auto_reject.cmd_scan_exam_timeout`（cron 专用；agent 只跑 `--dry-run`）。
- 回复里没有添加命令没明确返回的字段（§7）；命令失败按 §6 如实上报，不乐观包装。
- PII 按最小必要级别披露（§8）。
- 命令形式在整份回复里都是 `uv run python3 scripts/...`。

---

## 附录 A：规范 CLI 调用形式

```bash
cd <workspace_root>/skills/recruit-ops
uv run python3 scripts/<group>/<command>.py <args>
```

- 每个回复里**只**用这一种形式；不要混 `uv run python3` 和裸 `python3 <group>/...`。
- `uv run python3` 与 `<workspace_root>/skills/recruit-ops/.venv/bin/python3` **行为等价**（同一个解释器、同一份依赖），但 `uv run` 每次会做一次依赖检查（首次冷启动 ~1s 开销，后续几乎无感）。agent 一律用 `uv run python3`；cron / systemd 在 OPERATIONS.md 里直接调 `.venv/bin/python` 省那一次检查。
- cron / systemd 调用 import 时显式设 `PYTHONPATH=scripts`（详见 [docs/OPERATIONS.md §4](docs/OPERATIONS.md#4-定时任务systemd-user-timer)）。

## 附录 B：sibling docs 速查


| 我想知道…                         | 去哪查                                                                                                 |
| ----------------------------- | --------------------------------------------------------------------------------------------------- |
| 这条消息我该不该处理 / 是不是要 confirm     | 本文件 §0–§2                                                                                           |
| 当前 stage + intent 应该走哪条 chain | [AGENT_RULES.md §4](docs/AGENT_RULES.md#4-scenarios) + [§5 速查表](docs/AGENT_RULES.md#5-表外的常见-intent) |
| 某个 `cmd_xxx.py` 的参数语法         | [CLI_REFERENCE.md](docs/CLI_REFERENCE.md)                                                           |
| 反模式 / 不该做什么                   | 本文件 §9（高危 12 条）→ [INCIDENT_RULES.md](docs/INCIDENT_RULES.md)（事故型）                                   |
| 部署 / symlink / cron / 环境变量    | [OPERATIONS.md](docs/OPERATIONS.md)                                                                 |
| 为什么这套架构是这样设计的                 | [PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md)                                                     |
| 兜底地图（找不到答案时）                  | [INDEX.md](docs/INDEX.md)                                                                           |


代码权威源：`scripts/lib/core_state.py::STAGE_LABELS` + 各 atomic CLI 的 `ensure_stage_transition()`。