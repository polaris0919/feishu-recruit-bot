<!--
audience: agent
read_when: 决定要写 / 跑 chain 时；查 stage × intent → chain 映射时
do_not_put_here: CLI 参数语法（→ CLI_REFERENCE.md）/ 安全协议 / confirm 语义（→ SKILL.md §2）/ 部署（→ OPERATIONS.md）/ 事故故事（→ INCIDENT_RULES.md）/ 设计动因（→ PROJECT_OVERVIEW.md）
sibling_docs: SKILL.md, CLI_REFERENCE.md, INCIDENT_RULES.md, OPERATIONS.md, INDEX.md
last_updated: 2026-05-10
-->

# Agent 决策规则手册

> agent 跑 chain / 决定下一步 atomic CLI 时的**唯一规则源**。
> 这份文件回答：**当前 stage + intent 应该走哪条 chain？**
> 它不回答：何时该 confirm（→ `SKILL.md §2`）、CLI 参数细节（→ `CLI_REFERENCE.md`）、为什么这样设计（→ `PROJECT_OVERVIEW.md`）、历史事故复盘（→ `INCIDENT_RULES.md`）。

---

## 1. Overview

agent 的职责只有一件事：**把入站邮件 / 老板指令翻译成 atomic CLI chain**。

三条原则：

1. **动作 = atomic CLI**。改世界只能通过 §3 表里的 CLI；禁止裸 SQL / SMTP / IMAP。凡是招聘语境，或邮件 sender / recipient 可能命中 `talents.candidate_email`，都必须先按 recruit-ops 路由；候选人邮件必须经 `outbound.cmd_send` 或封装它的业务 CLI，不能用通用 `email-send` skill 代替。
2. **判断 = agent**。读 `talent_emails` / `talents` / `talent_events` / `ai_payload` 决定下一步，不藏内存状态。
3. **拿不准就推飞书**。规则未覆盖、`confidence < 0.6`、intent=`unknown` / `other`、出现 `ambiguous` / `config_error` 一律 STOP，调 `feishu.cmd_notify --severity warn` 让老板介入，**不做任何写动作**。若老板 / HR 给的是一个 §4 / §5 都未覆盖的 workflow 请求，进入 §3.5 的 uncovered workflow planner：先拆 atomic CLI 草案、逐步请人确认，确认前不执行写动作。

> chain 任意一步失败 → 立即停止后续步骤，把错误推飞书。**不自动回滚**（发邮件、删日历都不可逆）。

### 1.0 公司常量（v3.8.6 强约束）

**`company` / `location` 是公司常量**（值在 `email_templates/constants.py`），由 `outbound.cmd_send` 自动注入。

- **不要**在 `--vars` 里传 `company=...` 或 `location=...`——cmd_send v3.8.6 起 fail-loud 拒掉，cli_wrapper 会推飞书 critical。
- **不要凭"常识"补全**——本 skill 里出现的所有 chain pseudocode 都已经去掉 `f"location={loc}"`，agent 抄 chain 时严格照抄。
- 公司搬家 / 改名只动 `constants.py` 一处，不需要改 docs / chain / 模板。
- 同理：`position` / `position_suffix` 不知道时直接省略（默认空，模板渲染成"一面邀请"而不是"一面邀请（量化研究员）"），**不要凭对话推断或随便填**——具体岗位是 model fact，模糊就 stop and ask。

### 1.1 角色边界（v3.8.1 明文化 — 白名单口径）

agent 不是"任何人都能给我下命令"——下面三个角色 + 候选人邮件路径的触发面 agent 必须固化记住：

| 角色 / 来源 | 可触发的 §4 范围 | 备注 |
|---|---|---|
| **HR** | §4.1（一面排期）+ §4.3（**仅一面改期**：4.3.2 给新时间 / 4.3.3 跑改期 chain / 4.3.4 SCHEDULING 二次改期）+ §4.11 **仅一面行**（**含一面不过的 round1 `reject_delete`**——见下） + §4.10 末步（接力被通知"准备入职"） | HR 实际工作场景：把面试官的"X 通过/不过"和"改到 X 时间"在飞书转述给 bot——这是真实运营约束。**HR 可触发的唯一删档动作**：`interview.cmd_result --round 1 --result reject_delete --confirm-reject-delete X`（一面不过——CLI 内部已处理拒信 + 物理删档 + 审计；HR 转述面试官反馈本就是这条路径的设计触发源）。**严格禁止**HR 触发的：二面任意结果 / 笔试任意结果 / offer 发放 / `talent.cmd_delete`（裸删档）/ §4.13 任何删除路径 / `--result reject_delete --round 2` / force-jump / §4.15 标记入职等高风险动作（HR 在这些范围说话 → agent **忽略并请 HR 转告老板**）。 |
| **老板** | §4.2 ~ §4.15 全部 | 中间所有决策都是老板拍板。老板的指令通过**飞书消息** → Hermes Gateway 接收 → 路由到 recruit-ops agent runtime；agent 跑完 chain 通过 `feishu.cmd_notify` 把结果推回老板飞书（**唯一**飞书消息出口，闭环）。**飞书消息是纯文本无按钮**——但**有完整的入站通道**,老板就发普通文字消息即可。 |
| **面试官** | **不直接触发任何 chain** | 面试官的反馈（"X 答得不错" / "X 没来"）走老板/HR 转述。`interviewer-bachelor` 只表示本科候选人的一面面试官，不再承担长期同步职责。 |
| **候选人 inbound** | **白名单内的低风险写动作** + **永远只能产生通知**两类 | 详见下方"候选人 inbound 白名单" |

#### 候选人 inbound 白名单（v3.8.1）

候选人 inbound 邮件**默认仅产生** `inbox.cmd_analyze` 的飞书通知 + 写 `ai_payload`,**不直接驱动 chain**——除非命中以下白名单（intent 名以 [`prompts/inbox_general.json::valid_intents`](../scripts/prompts/inbox_general.json) 为准；同一 intent 在不同 stage 行为可能不同,以"触发条件"列为准）：

| intent | 触发条件（含 stage 限制） | 允许触发的写动作 | 风险等级 / 理由 |
|---|---|---|---|
| `confirm_interview` | stage = `ROUND{N}_SCHEDULING` | **不直接触发建日历**——只推 warn 卡问老板（v3.8.4 分权修订；旧版 v3.8.1~v3.8.3 自动跑 §4.2 chain 建日历）。老板**在飞书**对 bot 说"OK 建日历" / "X 时间确认了" / "给 X 安排日历"等显式安排指令后，agent 才走 §4.2 chain | **中等风险**：建日历是面试时间的**最终确认**动作（且日历邀请会再发一封参会邮件给候选人邮箱），候选人 confirm 邮件可能与老板/HR 已知的其他安排冲突（同一时段已约其他人 / 老板临时有会 / 改期未告知 agent 等）——必须老板拍板,不能让候选人单方面 confirm 推 agent 写动作。**实现**：`inbox.cmd_analyze` 内对 `(intent=confirm_interview, stage∈{ROUND{N}_SCHEDULING})` 强制 override `need_boss_action=true`,LLM 输出的低风险标签不生效。 |
| `defer_until_shanghai` | **stage ∈ `{ROUND1_SCHEDULING, ROUND1_SCHEDULED, ROUND2_SCHEDULING, ROUND2_SCHEDULED}`** | §4.4 chain（按 had_calendar 分支删旧日历 + 发"暂缓"邮件 + stage→`WAIT_RETURN` + 字段清零） | **低风险**：候选人主动声明"我不在国内", agent 仅按 §4.4 的固定模板回复 + 把候选人挂起；**不**做任何不可逆的拒类操作 |
| `defer_until_shanghai` | **stage ∈ `{EXAM_SENT, EXAM_REVIEWED, POST_OFFER_FOLLOWUP, ONBOARDED, NEW, WAIT_RETURN}`**（不在 §4.4 适用 stage 内） | **仅推 warn 卡问老板**（不进 §4.4 chain；这些 stage 的字段语义不同,§4.4 的 `--set round{N}_time=__NULL__` 不适用） | 中等风险：超出标准暂缓适用范围,需老板拍板 |
| `exam_submitted` | stage = `EXAM_SENT` | `inbox.cmd_analyze` 自动调用 §5.3：`exam.cmd_exam_ai_review --feishu --save-event`（写评审 + 自动推 stage→`EXAM_REVIEWED`，v3.8.1） | **低风险**：仅评分 + 推卡给老板拍板,不做任何决策 |
| `reschedule_request` | 任意 SCHEDULING/SCHEDULED stage | **不直接触发改期 chain**——只走 §4.3.1 推飞书通知（warn），等老板/HR 飞书给新时间后再走 §4.3.2 → §4.3.3（SCHEDULED 删旧日历）/ §4.3.4（SCHEDULING 二次改期）/ §4.12.2（SCHEDULING 续邀请） | 改期是有"承诺过候选人时间"的反向修正,需要老板拍板 |
| `decline_withdraw` | 任意非终态 stage | **不直接删档/留池**——只走 §4.13 chain 推 warn 决策卡给老板;老板**第二条飞书消息**显式批准后才执行删除/留池 | **高风险**：删档不可逆,事故源 [INCIDENT_RULES.md §12 / §13](INCIDENT_RULES.md) 已两次发生误删 |
| `request_online` | 任意 stage | **永远只推 warn 卡问老板**——agent 不得自动改面试形式 / 改日历 / 发任何邮件 | 改形式涉及面试官安排,需老板拍板 |
| `question_boss` / `thanks_fyi` / `other` | 任意 stage | 仅推飞书 info | 非决策性内容,只是知会 |

**全局硬规则**：
- 候选人 inbound 邮件**永远不能**触发 §4.10（发 offer）、§4.11（拍板一/二面/笔试结果）、§4.13 中的删除/留池实际执行、§4.9（force-jump）、§4.15（标记 ONBOARDED）——这些路径的写动作**必须由老板/HR 飞书消息触发**（HR 仅限其角色行允许的范围）。
- 任意 inbound 出现 `confidence < 0.6` 或 `intent=other` 或 `intent=unknown` → 一律走 §5.21 推 warn 卡,**不**进白名单。

唯一例外是 cron 自动化路径（`auto_reject.cmd_scan_exam_timeout` / `cmd_review_reminder` / `cmd_interview_reminder`）—— 这些**系统性、规则确定**的兜底动作不需要人触发。

**关于 Cursor**：Cursor IDE **不在生产消息路径上**,仅供开发者调试 chain / debug DB 时使用。日常运营老板/HR 都只用飞书；agent 也按"飞书消息"作为唯一意图触发源（除候选人邮件白名单 + cron 外）。本文档之前个别条目错误地写过"老板在 Cursor 里给 agent 下指令"——已在 v3.8 一致性 patch 中统一改为"老板在飞书发消息"。

---

## 2. Stages

13 个 stage（v3.8.2；v3.8 时为 12 个，v3.8.2 拆出 `OFFER_DECLINED_KEEP`），状态机详图见 [`PROJECT_OVERVIEW.md` §3](PROJECT_OVERVIEW.md)。

| stage | 含义 | 入口 | 出口 |
|---|---|---|---|
| `NEW` | 候选人刚入库 | `intake.cmd_ingest_cv` | §4.1（HR 给一面时间） / §4.13（候选人 NEW 阶段就放弃） |
| `ROUND1_SCHEDULING` | 一面已发邀请，等候选人 confirm（WAIT_RETURN 回归路径首次进入；改期回退也进） | §4.12（WAIT_RETURN 老板恢复）/ §4.3.3（改期回退） | §4.2 / §4.4 / §4.13 |
| `ROUND1_SCHEDULED` | 一面时间已确认 + 日历已建（HR-channel 默认直达） | §4.1（HR 给时间） / §4.2（候选人 confirm） | §4.3（改期回退到 SCHEDULING）/ `interview.cmd_result --round 1` / §4.13 / §4.14（no-show） |
| `EXAM_SENT` | 笔试已发 | `cmd_result --round 1 --result pass` | §5（exam_submitted → AI 评审）/ §4.13 / cron `auto_reject`（v3.8.3 起 → 物理删档,不进 stage） |
| `EXAM_REVIEWED` | 笔试已审，等老板拍板 | `exam.cmd_exam_ai_review` 后老板拍板 | §4.5（通过）/ §4.6（不过留池）/ §4.11（atomic 等价路径）/ §4.13 / cron `cmd_review_reminder`（3h 催老板，仍是 EXAM_REVIEWED） |
| `EXAM_REJECT_KEEP` | 笔试不过，留人才池（**叶子态**——可被 §4.9 force-jump 反向激活） | §4.6（老板手动）/ §4.11 / §4.13（`EXAM_*` / `WAIT_RETURN` 撤回）。**注**：cron `auto_reject` 自 v3.8.3 起**不再**进入本 stage,改为物理删档（详见 INCIDENT_RULES.md §15） | 终态（不主动出，但可被 force-jump 拉回 ROUND-stage / NEW） |
| `WAIT_RETURN` | 候选人在国外暂缓 | §4.4（候选人 defer） | §4.12（老板恢复）/ §4.13（候选人撤回） |
| `ROUND2_SCHEDULING` | 二面已发邀请，等候选人 confirm（首次：§4.5；改期回退也进） | §4.5（笔试通过 → 二面）/ §4.3.3（改期回退） | §4.2（round=2） / §4.4 / §4.13 |
| `ROUND2_SCHEDULED` | 二面时间已确认 + 日历已建 | §4.2（round=2） | §4.3（改期回退到 SCHEDULING）/ `interview.cmd_result --round 2` / §4.13 / §4.14（no-show） |
| `ROUND2_DONE_REJECT_KEEP` | 二面**面试**未通过，留人才池（**叶子态**）。**严格只承载** `ROUND2_SCHEDULED → reject_keep` 一条入边——拒 offer 类不要再混入此态（已分流到 `OFFER_DECLINED_KEEP`，v3.8.2） | `cmd_result --round 2 --result reject_keep` | 终态（不主动出，但可被 §4.9 force-jump 反向激活） |
| `OFFER_DECLINED_KEEP` | 候选人**已拒 Offer**，但保留人才库（**叶子态**，v3.8.2 新增）。语义上区别于 `ROUND2_DONE_REJECT_KEEP`：那个是"我们 say no"，这个是"候选人 say no"。**v3.8.4 起 `inbox.cmd_scan` 不再扫此 stage 的候选人邮件**（终态分权——agent 不再因为已拒 offer 的人回信而打扰老板；要重新激活必须先 §4.9 force-jump 回 `POST_OFFER_FOLLOWUP`，详见 INCIDENT_RULES.md §16） | §4.13（POST_OFFER_FOLLOWUP 分支：候选人拒 offer 但留池） | 终态（不主动出，但可被 §4.9 force-jump 反向激活——例：半年后候选人回心转意，老板想重新发 offer） |
| `POST_OFFER_FOLLOWUP` | 等发 offer / 谈入职 | `cmd_result --round 2 --result pass` | §4.8（cached draft 跟进，不动 stage）/ §4.10（发 offer，不动 stage）/ §4.13（候选人拒 offer：`OFFER_DECLINED_KEEP` 留池 / `talent.cmd_delete` 删除二选一,代码层 hard guard 兜底）/ §4.15（入职完成 → ONBOARDED） |
| `ONBOARDED` | 候选人已完成入职流程（**叶子态**）。**v3.8.4 起 `inbox.cmd_scan` 不再扫此 stage 的候选人邮件**（终态分权——已入职候选人后续沟通走 HR / 同事通道,不再由 agent 介入;详见 INCIDENT_RULES.md §16） | §4.15（老板确认入职完成） | 终态；老板可手动 `talent.cmd_delete --talent-id X --confirm-delete-talent X --reason 'archived after onboarding'` 归档（v3.8.1 hard guard） |

**不存在的 stage**（设计有意为之，别试图补对称）：一面被拒（直接物理删）、`EXAM_REJECT_DELETE`（笔试删档=cron auto_reject 自 v3.8.3 起直接物理删,**不**经停 stage）、`POST_OFFER_FOLLOWUP_DONE`（v3.6 设计原意——入职前都可能反悔；v3.8 改为新增 `ONBOARDED` 终态明示已入职）。详见 `PROJECT_OVERVIEW.md`。

**关于"叶子态"vs"终态"语义**（v3.8 修订；v3.8.2 增补）：
- **叶子态**（`EXAM_REJECT_KEEP` / `ROUND2_DONE_REJECT_KEEP` / `OFFER_DECLINED_KEEP` / `ONBOARDED`）= "默认不主动转出，但可被 §4.9 force-jump 反向激活"。例：
  - 候选人当时被判 `EXAM_REJECT_KEEP` 留池，3 个月后老板想重新捞起来 → `talent.cmd_update --stage ROUND2_SCHEDULING --force --reason 'reactivate from talent pool'` 即可。
  - 候选人 `OFFER_DECLINED_KEEP` 半年后回心转意，老板想再发一次 offer → `talent.cmd_update --stage POST_OFFER_FOLLOWUP --force --reason 'candidate reconsidered offer'` 反向激活。
- **真终态**（不存在持久态——只有 `talent.cmd_delete` 物理删除是真终止）。
- §4.9 force-jump 适用于**所有**叶子态 ⇄ 任意非叶子 stage 的反向激活；agent 不需要为这种场景另写 chain。

---

## 3. Commands（atomic CLI）

完整参数见 [`CLI_REFERENCE.md`](CLI_REFERENCE.md)。这里只列模块和一句话职责。

| 模块 | 职责 |
|---|---|
| `talent.cmd_add` | 候选人新建（v3.3 通用入口；支持飞书【新候选人】模板原文 / 逐字段两种入参）。**注意**：CV 入库链路（`intake/cmd_ingest_cv` 的 OC_CMD payload）仍调 `intake.cmd_new_candidate`，不在 chain 决策范围；本 CLI 用于 chain 显式新建（无 CV 上下文）。 |
| `talent.cmd_update` | 改 stage / 字段 + 写审计；非自然跳转必须 `--force --reason` |
| `talent.cmd_delete` | 物理删除 + 归档；**v3.8.1 硬要求** `--confirm-delete-talent <talent_id>`（值严格等于 `--talent-id`），缺失 / 不匹配直接 `UserInputError`（事故 [§12](INCIDENT_RULES.md#13-2026-05-10--3-人误删事故复发doc-修订对运行中-agent-失效)） |
| `outbound.cmd_send` | **唯一**发邮件出口（模板 / 自由文本 / `--use-cached-draft`） |
| `inbox.cmd_scan` | IMAP → `talent_emails(direction='inbound', analyzed_at=NULL)`。**v3.8.4 起跳过两个终态**：`ONBOARDED` / `OFFER_DECLINED_KEEP`（`_SKIP_STAGES` frozenset，详见 INCIDENT_RULES.md §16）。其他叶子态 `EXAM_REJECT_KEEP` / `ROUND2_DONE_REJECT_KEEP` / `WAIT_RETURN` 仍扫——理由：候选人没拒过我们,他们仍可能主动回头追问 / 表达回国信号,需让老板看到。 |
| `inbox.cmd_analyze` | LLM 分意图 + 写 `ai_*` + 推飞书 |
| `feishu.cmd_calendar_create` / `_delete` | 飞书日历 event |
| `feishu.cmd_notify` | **唯一**飞书消息出口 |
| `feishu.cmd_send_file` | 飞书发送本地文件（CV / 笔试附件 / 运维文件）；支持 `--to role` 或 `--open-id ou_xxx` |
| `talent.cmd_send_cv_to_feishu` | 按候选人发送 CV 到飞书（封装 `feishu.cmd_send_file`） |
| `exam.cmd_send_submission_to_feishu` | 按候选人发送最新笔试提交附件到飞书（封装 `feishu.cmd_send_file`） |
| `intake.cmd_route_interviewer` | 一面派单（纯查询，零副作用） |
| `interview.cmd_result` | 一/二面结果 → 下一 stage；候选人邮件内部调用 `outbound.cmd_send`，不直连 SMTP；**v3.8.1**：`--result reject_delete` 必须带 `--confirm-reject-delete <talent_id>` |
| `exam.cmd_exam_result` | 笔试结果 → 下一 stage；二面邀请内部调用 `outbound.cmd_send`，不直连 SMTP；**v3.8.1**：`--result reject_delete` 必须带 `--confirm-reject-delete <talent_id>` |
| `exam.cmd_exam_ai_review` | 笔试 AI 评审 + 写 `talent_events`；**v3.8.1 起**：当 `--save-event` 成功且当前 stage=`EXAM_SENT` 时，自动推 stage→`EXAM_REVIEWED`（natural transition）。最终通过/不通过仍需老板用 `exam.cmd_exam_result` 决定。 |
| `auto_reject.cmd_scan_exam_timeout` | **仅 cron** 调用；笔试 3 天未交 → 拒信 + 物理删档（v3.8.3 起；v3.5.11~v3.8.2 期间是"拒+留池 EXAM_REJECT_KEEP"，详见 INCIDENT_RULES.md §15） |
| `cron.cmd_review_reminder` | **仅 cron** 调用（v3.8）；候选人 stage=`EXAM_REVIEWED` 持续 ≥ 3h 未拍板 → 推飞书催老板 |
| `common.cmd_interview_reminder` | **仅 cron** 调用；扫描临近开始的 `ROUND{N}_SCHEDULED` 候选人 + 已结束待反馈面试 → 推面试官/老板提醒卡 |

护栏（agent 行为约束，非 CLI 用法）：

- `--force` 必带 `--reason`；老板的"直接跳"必须引用原话。
- `current_stage` 只能用 `--stage` 推，**不要** `--set current_stage=...`。
- `POST_OFFER_FOLLOWUP` 阶段 agent 不自动回信，一律生成 draft 写入 `talent_emails.ai_payload.draft` + 推飞书消息给老板看；老板**在飞书**对 bot 说"用 cached draft 给 X 回" / "OK 就发"等指令（Hermes Gateway 接到后路由给 agent）后才走 §4.8 chain（飞书消息是**纯文本**，**没有按钮**——但有完整入站通道）。
- chain 主业务写动作不超过 5 步（v3.8.1 修订：`outbound.cmd_send` / `feishu.cmd_calendar_create` / `feishu.cmd_calendar_delete` / `talent.cmd_update`）；末尾的 `feishu.cmd_notify` fanout 通知（面试官 / Polaris / boss / HR 多张卡）**不计入** 5 步限。超过 5 步主业务动作先回头看是不是漏了一个 atomic CLI。
- **飞书发文件是外部副作用**：`feishu.cmd_send_file` / `talent.cmd_send_cv_to_feishu` / `exam.cmd_send_submission_to_feishu` 必须按 §2.3.1 propose-confirm 执行。CV 和笔试答案都含候选人隐私；不得自动发送，不得猜收件人，不得把 `--open-id` 目标当成已确认。
- **二面时间确认硬规则**：任何二面时间（笔试通过、一面后跳过笔试直进二面、WAIT_RETURN 回归、二面改期、留池反向激活）都不能直接确认。老板第一次给出的二面时间只是候选人邀请时间；必须先进入 `ROUND2_SCHEDULING` 并发送二面邀请，候选人邮件确认后只推老板决策卡，老板再次明确下达“二面时间确认 / 建日历 / OK 安排”后才允许创建老板 + Polaris 日历并推进 `ROUND2_SCHEDULED`。`talent.cmd_update` 已 hard guard：任何非 `ROUND2_SCHEDULING → ROUND2_SCHEDULED` 的二面确认都会失败，即使加 `--force` 也不允许。
- 拒类操作三条路径：(1) **人工物理删**走 `interview.cmd_result --result reject_delete`（自带先发拒信）；(2) **人工留池**走 §4.6 / §5.7；(3) **cron 自动删档**走 `auto_reject.cmd_scan_exam_timeout`（v3.8.3 起：笔试 3 天未交 = 拒信 + cmd_delete 物理删）。**不要** 直接 `talent.cmd_delete` 跳过拒信。
- **日历固定 attendee（v3.8 修订）**：`feishu.cmd_calendar_create` 内部会自动把 **`feishu.boss_open_id` + `feishu.polaris_open_id` + `feishu.hr_open_id`** 加入所有面试日历参会人。这些都无需 chain 显式 `--extra-attendee`。Polaris 是固定日程安排者 / 运营观察者，不是面试官角色。一面必须把 `intake.cmd_route_interviewer` 路由出的真实面试官通过 `--extra-attendee` 传入；若上层漏传，`cmd_calendar_create` 会自动补派单。`master` 表示硕士/博士候选人的一面面试官，**不表示老板**。
- **Polaris 运营同步卡（v3.8 修订）**：Polaris 是固定日程安排者 / 运营观察者。除日历邀请外，**任何**会改变候选人 stage / 关键字段的 chain 末尾必须额外推一张 `feishu.cmd_notify --to polaris --severity info` 同步卡。**当前覆盖**：§4.1（一面初排）/ §4.2（confirm 升级）/ §4.3.3（改期）/ §4.4（暂缓）/ §4.5（笔试通过→二面）/ §4.6（笔试不过留池）/ §4.9（force-jump）/ §4.12（老板恢复）/ §4.12.2（v3.8.1 续邀请）/ §4.13（候选人撤回）/ §4.14（v3.8.1 no-show）/ §4.15（v3.8.1 已入职——胜利收尾）。
  - **身份边界**：`polaris` 使用 `feishu.polaris_open_id`；`interviewer-bachelor` 只用于本科候选人的一面派单通知，二者不能混用。
  - **不推 Polaris 卡的场景**（设计有意为之）：cron `auto_reject` 自动拒（非业务决策）；§4.7 候选人主动来信（仅信号，stage 没变，已由 `inbox.cmd_analyze` 同步老板 + Polaris）；§4.8 / §4.10 POST_OFFER_FOLLOWUP 阶段的 chat / offer 邮件（业务谈判过程，由 HR 接手）；`exam.cmd_exam_result` atomic 等价路径（不在 chain 内，Polaris 由后续 §4.2 升级时获知）。

---

## 3.5 Uncovered Workflow Planner（规则未覆盖时的人工确认计划）

> 适用：老板 / HR 给出一个**看起来合理但 §4 / §5 没有精确定义**的 workflow 请求。
> 目标：不让 agent 硬猜、不让 agent 直接拒绝；先把目标拆成 atomic CLI 草案，让人逐步确认。

### 触发条件

命中以下任一情况，必须进入 planner：

- 用户目标跨了多个 stage / 多个候选人，而 §4 没有对应 chain。
- 用户要求的顺序与 §4 既有 chain 不一致（例如先改状态再发邮件、先建日历再确认时间等）。
- 用户要求的动作需要组合多个 atomic CLI，但 agent 无法在 §4 / §5 找到完全匹配的剧本。
- 用户要求看起来像 force-jump、删档、重发邮件、补 event_id、补审计、手动修复脏数据，但没有直接落在已有场景。
- 任何一步的业务事实缺失（时间、面试轮次、候选人身份、邮件模板、是否删档 / 留池、是否发给候选人等）。

不适用：

- 候选人 inbound 邮件的低置信度 / unknown / other 仍优先走 §5.21。
- `need_boss_action=true` 但只是未分类邮件通知，仍走 §5.22。
- 已有 §4 精确覆盖的场景不得退化为 planner；应按 §4 的固定 chain 执行。

### Planner 输出格式

进入 planner 后，agent 只能先做**只读查询**收集事实（例如 `talent.cmd_show` / `talent.cmd_list` / `inbox.cmd_review`），然后用 `feishu.cmd_notify --severity warn` 推一张计划卡：

```text
[未覆盖 workflow，进入人工确认计划]
目标：<用户原话压缩版>
当前事实：
- talent_id：...
- stage：...
- 关键字段：...

拟执行步骤：
1. [READ] <只读查询，已执行 / 可执行>
2. [DB_WRITE] <atomic CLI 命令草案>
3. [EMAIL] <atomic CLI 命令草案>
4. [CALENDAR] <atomic CLI 命令草案>

风险点：
- ...

请确认是否执行第 N 步：<下一步写动作的一句话>
建议回复：确认执行第 N 步：<动作 + talent_id>
```

### 风险分级与确认规则

| 风险级别 | 包含动作 | 是否可自动执行 | 确认规则 |
|---|---|---|---|
| `READ` | `cmd_show` / `cmd_list` / `inbox.cmd_review` / 纯查询 route | 可以 | 可直接执行以生成计划 |
| `DB_WRITE` | `talent.cmd_update` 等只改 DB 的动作 | 不可以 | 人类确认该步后执行 |
| `EMAIL` | 任意 `outbound.cmd_send` | 不可以 | 必须单独确认；不得和 DB 写 / 日历绑定确认 |
| `CALENDAR` | `feishu.cmd_calendar_create` / `_delete` | 不可以 | 必须单独确认；确认文本须包含时间或 event_id |
| `DELETE` | `talent.cmd_delete` / `reject_delete` | 不可以 | 必须单独确认，并保留 CLI 自带 confirm 参数 |
| `FORCE` | `talent.cmd_update --force` | 不可以 | 必须老板确认，`--reason` 引用老板原话 |

**逐步执行协议**：

- 一次只请求确认**一个写动作**；邮件、日历、删除、force-jump 永远不能和其他写动作合并确认。
- 人类确认后，agent 只执行被确认的那一步，然后汇报结果和下一步计划。
- 人类改口（如"不要删，改留池" / "时间换成 15:00"）时，agent 必须重新生成剩余步骤，不得沿用旧计划。
- 模糊确认（"OK" / "可以"）只在上一条飞书里**唯一待确认步骤**存在时有效；否则继续问清楚。
- 每一步仍必须走 `lib.run_chain` / atomic CLI 的自验证；失败就按 §6 告警，停止后续步骤。

### 绝对禁止

- 禁止 HR 通过 planner 执行 §1.1 里标为老板专属的动作。
- 禁止绕过 `--confirm-delete-talent` / `--confirm-reject-delete` / `--force --reason` 等 CLI hard guard。
- 禁止裸 SQL、裸 SMTP、裸 IMAP、裸 Feishu 日历 API。
- 禁止用多个 `cmd_update --set` 拼出"候选人已确认"这类业务事实。
- 禁止为了让 natural transition 通过而编造时间、邮件、`event_id`、`confirm_status`。
- 禁止把已有 §4 高风险确认门降级成 planner 一次性执行（例如 §4.1 一面时间确认门、§4.2 候选人 confirm 后建日历、§4.13 删除 / 留池二选一）。

---

## 4. Scenarios

每段格式：**触发条件** → chain 代码 → 一两条硬规则。

### 4.1 NEW + HR 给一面时间（轻量确认门 → HR 确认后直达 ROUND1_SCHEDULED）

> NEW 阶段唯一 happy path。前置：`intake.cmd_ingest_cv` 已跑过、`talents.education` / `has_cpp` 已写。
>
> **v3.8.6 硬规则**：HR/老板第一次给时间时，agent 只能写
> `round1_proposed_time` + 推飞书确认文案；**不允许**立刻发邮件 / 建日历 /
> `ROUND1_SCHEDULED`。只有 HR/老板第二条飞书明确确认（如"确认安排" /
> "OK 按这个时间发" / "确认 2026-05-15 10:00"）后，才执行下面正式 chain。

#### 4.1.1 首次解析时间：只写 proposed，不产生外部副作用

```python
run_chain([
    Step("propose", "talent.cmd_update",
        ["--talent-id", tid,
         "--set", f"round1_proposed_time={t}",
         "--reason", "agent: parsed HR proposed round1 time; waiting explicit confirm"]),
    Step("ask_confirm", "feishu.cmd_notify",
        ["--to", "hr", "--severity", "warn",
         "--title", f"请确认一面时间：{name}",
         "--body", f"我解析到 talent={tid} 一面时间为 {t}。\n"
                   f"确认无误请回复：确认安排 {name} {t}\n"
                   f"在确认前不会发邮件、不会创建日历、不会改 stage。"]),
])
STOP
```

#### 4.1.2 HR/老板确认后：学历感知派单 → 发邮件 → 建日历 → ROUND1_SCHEDULED

```python
route = run_atomic("intake.cmd_route_interviewer", ["--talent-id", tid, "--json"])

if route["ambiguous"] or route["config_error"]:
    run_atomic("feishu.cmd_notify", ["--to", "hr", "--severity", "warn",
        "--title", "一面派单需 HR 手动指派",
        "--body", route["ambiguous_reason"] or route["config_error_detail"]])
    STOP

run_chain([
    Step("send", "outbound.cmd_send",
        ["--talent-id", tid, "--template", "round1_invite",
         "--vars", f"round1_time={t}", "--json"]),
    Step("cal", "feishu.cmd_calendar_create",
        ["--talent-id", tid, "--time", t, "--round", "1",
         "--duration-minutes", "30",
         "--candidate-name", name, "--candidate-email", email,
         *flatten(["--extra-attendee", oid] for oid in route["interviewer_open_ids"]),
         "--json"]),
    Step("update", "talent.cmd_update",
        ["--talent-id", tid, "--stage", "ROUND1_SCHEDULED",
         "--set", f"round1_time={t}",
         "--set", "round1_proposed_time=__NULL__",
         "--set", "round1_invite_sent_at={send.sent_at}",
         "--set", "round1_confirm_status=CONFIRMED",
         "--set", "round1_calendar_event_id={cal.event_id}"]),
    *[Step(f"notify_iv_{r}", "feishu.cmd_notify",
        ["--to", f"interviewer-{r}", "--severity", "info",
         "--title", f"一面安排：{name}",
         "--body", f"talent={tid} 时间={t} 30min"])
      for r in route["interviewer_roles"]],
    # Polaris 运营同步卡：独立于面试官派单，固定推送
    Step("notify_polaris", "feishu.cmd_notify",
        ["--to", "polaris", "--severity", "info",
         "--title", f"候选人状态变化：{name} 一面已排",
         "--body", f"talent={tid} 一面时间={t}\n"
                   f"派单角色={','.join(route['interviewer_roles'])}\n"
                   f"（你已作为固定 attendee 收到日历邀请）"]),
    Step("notify_boss", "feishu.cmd_notify",
        ["--to", "boss", "--severity", "info",
         "--title", f"一面已排：{name} {t}"]),
])
```

**硬规则**：
- 第一步必须是 `intake.cmd_route_interviewer`，**不允许** agent 自己看 `education` / `has_cpp` 算 open_id。
- `ambiguous` / `config_error` 必须 STOP 转 ASK_HR，**不允许**兜底"随便派一个 / 派给老板"。
- 一面 30 分钟（`--duration-minutes 30`），二面 60 分钟（默认）。
- 正式 chain 的 `t` 必须等于 DB 里的 `round1_proposed_time`（分钟精度一致）。如果 HR/老板确认时改了时间，先用 4.1.1 重写 proposed，再等待一次确认。

**chain 顺序设计**（与 §4.2 / §4.3.3 对照）：
- 顺序 `send → cal → update`：HR-channel 排期场景下"候选人邀请邮件"是**主输出**，日历事件是**副输出**（飞书 calendar_create 同时会再发一封参会邀请给候选人邮箱）。
- vs §4.2（候选人 confirm 时间）顺序 `cal → update`：那个场景是 "已发过邀请、现在候选人 confirm"，日历是**主输出**，所以 cal 失败必须 STOP（避免 stage 推到 SCHEDULED 但日历空）。
- vs §4.3.3（改期）顺序 `cal_del → send → update`：那个场景的"主输出"是消除旧日历的不一致，所以 cal_del 必须先于 send。
- 三个 chain 的不对称是**有意为之**——按"哪一步失败损失最小"来排，**不要**统一成一个顺序。

**失败分支处理**（v3.8.1 修订，原"少一份日历"措辞误导,实际 chain 短路语义如下）：

| 失败位置 | 候选人状态 / 副作用 | agent 应做 |
|---|---|---|
| `send` 失败 | 邮件未发 | `severity=error "一面邀请未发"`；STOP，候选人未受影响 |
| `cal` 失败（`send` 已成功） | **邮件已发，但 chain 短路 → `update` 不执行 → DB 仍是 NEW + 无 `round1_invite_sent_at`**（候选人收到邀请,但 DB 不知道 → 后续可能重发） | `severity=critical "邀请已发但日历/DB 未跟上"`；老板必须手动 `talent.cmd_update --talent-id X --stage ROUND1_SCHEDULING --set round1_time={t} --set round1_invite_sent_at=<send.sent_at> --set round1_confirm_status=PENDING`,并视情况手动建日历 |
| `update` 失败 | 邮件已发、日历已建，DB 字段没刷新 | `severity=critical "邀请 + 日历已建但状态未更新"`；老板手动 `talent.cmd_update` 补字段（含 `--stage ROUND1_SCHEDULED` + 4 个 set） |

### 4.2 候选人 confirm 时间 → 建日历

> **触发（v3.8.4 分权修订）**：**两条件全部满足**才进入本 chain——
>
> 1. 当前 stage = `ROUND{N}_SCHEDULING`，且 `inbox.cmd_analyze` 已为最新一封 inbound 邮件标过 `intent=confirm_interview`（以 [`prompts/inbox_general.json::valid_intents`](../scripts/prompts/inbox_general.json) 为准；v3.8.1：旧口语缩写 `confirm_time` 不是合法值）；
> 2. **老板**在飞书对 bot **显式**说出"OK 建日历" / "X 时间确认了" / "给 X 安排日历" / "OK 安排" 等明确指示安排动作的话（Hermes Gateway 接收后路由到 agent）。
>
> 条件 1 单独命中**仅**走 §5.X（推 warn 卡问老板，等老板拍板），**不**自动跑本 chain。理由：建日历会再发一封参会邮件给候选人邮箱（不可撤销）+ 升级 stage 到 `SCHEDULED`,候选人 confirm 邮件可能与老板/HR 已知的其他安排冲突,这一步必须老板**显式分权**拍板。
>
> **历史**：v3.8.1~v3.8.3 期间本 chain 由候选人 `confirm_interview` 邮件**单独**触发（agent 把它判作低风险写动作直接跑）；v3.8.4 撤回这条快捷路径,改为两段式（候选人 confirm 邮件 → warn 卡 → 老板飞书显式安排）。详见 INCIDENT_RULES.md §16。
>
> **进入本场景的三条上游路径**（agent **不需要**区分，老板拍板后处理完全一致）：
>
> 1. **WAIT_RETURN 回归** —— 候选人国外回来后老板按 §4.12 把 stage 推回 `ROUND{N}_SCHEDULING`，老板下一步给新时间发邀请，候选人 confirm 后等老板飞书安排即走这里。
> 2. **二面首次排期** —— 笔试通过后走 §4.5（发 `round2_invite` + 推 stage 到 `ROUND2_SCHEDULING`），等候选人 confirm 二面时间 + 老板飞书安排。
> 3. **任何 round 改期后回归** —— 已排期的面试（包括 §4.1 HR-channel 直达 `ROUND1_SCHEDULED` 的）走 §4.3.3 改期 chain 后 stage 回到 `ROUND{N}_SCHEDULING` + `confirm_status=PENDING` + `calendar_event_id=__NULL__`，等候选人 confirm 新时间 + 老板飞书安排——这就走到这里**升级回** `ROUND{N}_SCHEDULED` + 建新日历。
>
> **注**：HR-channel 一面**首次**排期（§4.1）走的是 `NEW → ROUND1_SCHEDULED` 直达路径，**不**经过本节；但 §4.1 之后任何改期都会经 §4.3.3 落回 `ROUND1_SCHEDULING`，再由本节升级回 `ROUND1_SCHEDULED`。
> **二面特别规则**：二面没有“老板给时间后直接确认”的快捷路径。无论上游是笔试通过、一面后跳过笔试、WAIT_RETURN 回归、二面改期还是留池反向激活，老板第一次给出的二面时间都只用于 `ROUND2_SCHEDULING` 邀请候选人；必须等候选人 confirm + 老板再次授权，才可建老板 + Polaris 日历并进入 `ROUND2_SCHEDULED`。

```python
# 一面路径：必须重新跑派单。原因——intake.cmd_route_interviewer 是纯查询零副作用，
# 不写 DB，所以 §4.1 派给的面试官 open_id 没有任何字段记录；改期后必须现场再算。
# 同时也防止改期间隔 candidates.has_cpp / 面试官 open_id 配置变化的情况。
if N == 1:
    route = run_atomic("intake.cmd_route_interviewer", ["--talent-id", tid, "--json"])
    if route["ambiguous"] or route["config_error"]:
        run_atomic("feishu.cmd_notify", ["--to", "hr", "--severity", "warn",
            "--title", f"改期 confirm 后派单失败：{name}",
            "--body", f"talent={tid} round=1\n"
                      f"原因：{route['ambiguous_reason'] or route['config_error_detail']}\n"
                      f"建议：HR 手动指派面试官；agent 不会自动建日历"])
        STOP

    extra_attendees = flatten(["--extra-attendee", oid] for oid in route["interviewer_open_ids"])
    duration_args = ["--duration-minutes", "30"]
    interviewer_roles = route["interviewer_roles"]
else:
    # 二面默认老板亲自面（与 §4.5 保持一致——§4.5 二面首次排期 chain 也没派单步骤）。
    # cmd_calendar_create 默认 60 分钟，不传 --extra-attendee（老板自己看飞书日历）。
    # 若未来二面要指派面试官，需先扩展 intake.cmd_route_interviewer 的 round 参数。
    extra_attendees = []
    duration_args = []
    interviewer_roles = []

run_chain([
    Step("cal", "feishu.cmd_calendar_create",
        ["--talent-id", tid, "--round", str(N), "--time", t,
         "--candidate-email", email, "--candidate-name", name,
         *duration_args, *extra_attendees, "--json"]),
    Step("u", "talent.cmd_update",
        ["--talent-id", tid, "--stage", f"ROUND{N}_SCHEDULED",
         "--set", f"round{N}_time={t}",                            # v3.8.1：必写,t = 候选人最终 confirm 的时间
         "--set", f"round{N}_confirm_status=CONFIRMED",
         "--set", f"round{N}_calendar_event_id={cal.event_id}"]),
    # 一面才推面试官通知；二面老板自己看日历不需要
    *([Step(f"notify_iv_{r}", "feishu.cmd_notify",
        ["--to", f"interviewer-{r}", "--severity", "info",
         "--title", f"一面安排（改期后已 confirm）：{name}",
         "--body", f"talent={tid} 时间={t} 30min"])
       for r in interviewer_roles] if N == 1 else []),
    # Polaris 运营同步卡：独立于面试官派单，固定推送
    Step("notify_polaris", "feishu.cmd_notify",
        ["--to", "polaris", "--severity", "info",
         "--title", f"候选人状态变化：{name} {('一面' if N==1 else '二面')}已 confirm + 建日历",
         "--body", f"talent={tid} round={N} 时间={t}\n"
                   f"（你已作为固定 attendee 收到日历邀请）"]),
    Step("notify_boss", "feishu.cmd_notify",
        ["--to", "boss", "--severity", "info",
         "--title", f"{('一面' if N==1 else '二面')}已 confirm：{name} {t}",
         "--body", f"talent={tid} stage→ROUND{N}_SCHEDULED 日历已建"]),
])
```

**硬规则**：
- **必须由老板飞书消息触发**（v3.8.4）——候选人 `confirm_interview` 邮件单独命中**只**推 warn 卡问老板。本 chain 入口现在等价于 §4.8 的"老板飞书命令一键"模型：候选人邮件做信号、老板做决策、agent 做执行三段分权。绝不允许 agent 自己判断"候选人确认了 = 该建日历"并直接跑本 chain。
- **顺序：cal → 单步 update**（v3.7 修订；旧版分两步 update 会留下"stage SCHEDULED 但 event_id NULL"的脏状态）。
  - cal 成功 → update 一次性写完 stage + confirm_status + event_id；
  - cal 失败 → stage 仍在 SCHEDULING，agent 推 `severity=error` 让老板手动重试或线下排期，**没有**部分成功的脏状态。
- 仅适用 `ROUND{N}_SCHEDULING → ROUND{N}_SCHEDULED` 自然 transition；其他 stage（`NEW` 直达 SCHEDULED 走 §4.1）不走这里。
- **二面更严格**：`ROUND2_SCHEDULED` 只能从 `ROUND2_SCHEDULING` 进入，且必须同时写 `round2_time`、`round2_confirm_status=CONFIRMED`、`round2_calendar_event_id=<event_id>`。禁止从 `EXAM_REVIEWED` / `ROUND1_SCHEDULED` / `WAIT_RETURN` / 任何留池态直接到 `ROUND2_SCHEDULED`；这类直达即使加 `--force` 也会被 `talent.cmd_update` 拒绝。
- **`t` = 候选人在最新邮件中 confirm 的那个时间**（不是 `talents.round{N}_time` 字段——那个字段在 §4.3.3 里被刷成"老板提议的新时间"，但候选人最终 confirm 的可能微调过；以候选人邮件原文为准，agent 拿不准就 STOP 问老板）。**v3.8.1 起 chain 必须把 `t` 同时写到日历和 `--set round{N}_time={t}`,否则会留下"日历是新时间但 DB 是旧时间"的脏状态**。老板若在飞书指令里给了不一样的时间（"X 那时间不太行,改成 4 点建日历"），以**老板原话**为准——这种情况实际是 mini 改期,agent 应转走 §4.3.3 而**不**是本 chain。
- **一面：必须先派单（v3.7）**——`intake.cmd_route_interviewer` 是纯查询零副作用，无字段记录上次派给谁；改期 confirm 后必须重新派。`ambiguous` / `config_error` 时 STOP 转 ASK_HR，**不允许**兜底。
- **一面时长 30 分钟**（与 §4.1 一致）；二面默认 60 分钟（`cmd_calendar_create` 默认值）。
- **二面默认老板自己面**——不传 `--extra-attendee`；若需要指派二面面试官，是产品层面新需求，要先扩 `intake.cmd_route_interviewer` 支持 `--round` 参数，再改本 chain。

### 4.3 候选人改期（HR/老板驱动，回到 SCHEDULING 等候选人 confirm）

> 设计原则：
> 1. **候选人邮件 = 信号，永远不直接驱动 chain**；真正触发改期的只能是 HR/老板给出的新时间（§4.3.2）。
> 2. 改期 = 候选人原日程已变；新时间走完 chain 后**回到 `ROUND{N}_SCHEDULING`**，发邮件请候选人在新时间上再 confirm 一次（§4.3.3），confirm 后由 §4.2 升级回 SCHEDULED + 建新日历。这条路径与 §4.1（HR 初始排期直达 SCHEDULED）刻意不对称。

#### 4.3.1 候选人来信请求改期 → 仅推飞书通知老板

> stage = `ROUND{N}_SCHEDULED`，`intent=reschedule_request`。**不**自动跑改期 chain。

按 `ai_payload.urgency` 分两档：

**A. 普通改期（>24h，`urgency=normal/low`）**：

```python
run_chain([
    Step("notify", "feishu.cmd_notify",
        ["--severity", "warn",
         "--title", f"候选人请求改期：{name}",
         "--body", f"talent={tid} round={N}\n"
                   f"原时间={cand[f'round{N}_time']}\n"
                   f"候选人原因：{ai['details'].get('reason', '未说明')}\n"
                   f"候选人提议新时间：{ai['details'].get('new_time') or '未指定'}\n\n"
                   f"建议下一步：老板/HR 拍板新时间后调 §4.3.3 chain"]),
])
```

**B. 临近改期（≤24h 内，`urgency=high`）→ 升级为决策卡**：

候选人在面试前 24h 内才提改期，可能是真有紧急事，也可能在鸽我们。把判断权完全交给老板，不做 agent 兜底。

```python
run_chain([
    Step("notify", "feishu.cmd_notify",
        ["--severity", "critical",
         "--title", f"⚠️ 临近改期（<24h）：{name}",
         "--body", f"talent={tid} round={N}\n"
                   f"原时间={cand[f'round{N}_time']}（距今 {hours_until} 小时）\n\n"
                   f"候选人改期原因（LLM 总结）：\n{ai['summary']}\n\n"
                   f"候选人邮件原文节选：\n{email['body_excerpt']}\n\n"
                   f"请老板三选一：\n"
                   f"  1) 给新时间 → 走 §4.3.3 chain（保留候选人）\n"
                   f"  2) 判定为鸽 → interview.cmd_result --talent-id {tid} --round {N} \\\n"
                   f"     --result reject_delete --confirm-reject-delete {tid}（发拒信 + 物理删档,v3.8.1 hard guard）\n"
                   f"  3) 留人才池 → interview.cmd_result --talent-id {tid} --round {N} --result reject_keep（发拒信 + 留池）"]),
])
```

#### 4.3.2 老板/HR 给出新时间 → 跑改期 chain

老板/HR 在飞书回话给一个具体新时间，**这才是真正的触发源**。
**HR 仅限一面（round=1）改期** —— 二面改期一律老板拍板（参 §1.1）；HR 在二面改期上说话 → 忽略并请 HR 转告老板。

#### 4.3.3 改期 chain（stage 回到 `ROUND{N}_SCHEDULING`，等候选人重新 confirm）



```python
run_chain([
    Step("cal_del", "feishu.cmd_calendar_delete",
        ["--event-id", cand[f"round{N}_calendar_event_id"], "--reason", "改期"]),
    Step("send", "outbound.cmd_send",
        # v3.8.6: company / location 由 cmd_send 从 constants.py 自动注入,
        # **不要**手动传 --vars location=... (传了会 fail-loud 拒掉)。
        ["--talent-id", tid, "--template", "reschedule",
         "--vars", f"round_label={'一面' if N==1 else '二面'}",
                   f"old_time={old}", f"new_time={new}"]),
    Step("update", "talent.cmd_update",
        ["--talent-id", tid, "--stage", f"ROUND{N}_SCHEDULING",
         "--set", f"round{N}_time={new}",
         "--set", f"round{N}_confirm_status=PENDING",
         "--set", f"round{N}_calendar_event_id=__NULL__",
         "--set", f"round{N}_invite_sent_at={send.sent_at}",
         "--reason", "candidate reschedule, awaiting confirm"]),
    # Polaris 运营同步卡：改期会让日历安排作废，必须同步
    Step("notify_polaris", "feishu.cmd_notify",
        ["--to", "polaris", "--severity", "info",
         "--title", f"候选人状态变化：{name} {('一面' if N==1 else '二面')}改期",
         "--body", f"talent={tid} round={N}\n"
                   f"原时间={old} → 新时间={new}\n"
                   f"stage→ROUND{N}_SCHEDULING（旧日历已删除，等候选人 confirm 后再建新日历）"]),
])
```

**之后**：候选人回信 confirm 新时间 → `inbox.cmd_analyze` 出 `intent=confirm_interview` → 走 §4.2
（`talent.cmd_update --stage ROUND{N}_SCHEDULED` + `feishu.cmd_calendar_create` + 回填 `event_id`），
完成"SCHEDULING → SCHEDULED"升级。

**硬规则**：

- **顺序：cal_del → send → update**。**先删旧日历再发新时间邮件**，否则会出现"候选人收到新时间但旧日历还在"的不一致。
- **stage 回到 `ROUND{N}_SCHEDULING`**，`confirm_status=PENDING`，`calendar_event_id=__NULL__`。建新日历这一步**不在本 chain 里**——等候选人 confirm 后由 §4.2 建。
- 用现有模板 `reschedule`（措辞：请候选人确认新时间是否合适）。**不需要**新模板。
- 候选人若在新时间上再次提改期 → 当时 stage 已是 `SCHEDULING`，没有日历可删 → 走 **§4.3.4** 简化 chain（不跑 `cal_del` 步骤）。

**失败处理**：

| 失败位置 | 候选人状态 | agent 应做 |
|---|---|---|
| `cal_del` 失败 | 旧日历未删 | `severity=error "旧日历未删除"`；STOP，老板手动删后重试整条 chain |
| `send` 失败（cal_del 已成功） | 旧日历已删，候选人不知道改期 | `severity=critical "改期邮件未发"`；老板线下通知候选人，并 `talent.cmd_update --set round{N}_calendar_event_id=__NULL__` 清字段 |
| `update` 失败 | 邮件已发、旧日历已删，DB 字段没刷新 | `severity=critical "字段未更新"`；老板 `talent.cmd_update` 手动补 |

#### 4.3.4 SCHEDULING 阶段二次改期 chain（候选人 / 老板再次提新时间，无日历可删）

> **触发**：stage = `ROUND{N}_SCHEDULING`（首次改期已经把日历删了，stage 已落回 SCHEDULING + `calendar_event_id=__NULL__`）；候选人或老板再提新时间。
> 与 §4.3.3 区别：**没有日历可删**——跳过 `cal_del` 步骤；与 §4.12.2 区别：用 `reschedule` 模板（"二次改期"措辞）而不是 `round{N}_invite`（首次邀请措辞）。

```python
# v3.8.6: location 由 cmd_send 自动注入 constants.LOCATION, 不再是 chain 输入,
# 也无需"缺地点告警"防御步骤; round_label / old_time / new_time 由调用方传入。
run_chain([
    Step("send", "outbound.cmd_send",
        ["--talent-id", tid, "--template", "reschedule",
         "--vars", f"round_label={'一面' if N==1 else '二面'}",
                   f"old_time={old}", f"new_time={new}"]),
    Step("update", "talent.cmd_update",
        ["--talent-id", tid,
         "--set", f"round{N}_time={new}",
         "--set", f"round{N}_invite_sent_at={send.sent_at}",
         "--set", f"round{N}_confirm_status=PENDING",
         "--reason", "candidate reschedule again, awaiting confirm"]),
    # Polaris 运营同步卡：二次改期也是状态变化
    Step("notify_polaris", "feishu.cmd_notify",
        ["--to", "polaris", "--severity", "info",
         "--title", f"候选人状态变化：{name} {('一面' if N==1 else '二面')}二次改期",
         "--body", f"talent={tid} round={N}\n"
                   f"原（已 SCHEDULING）时间={old} → 新时间={new}\n"
                   f"stage 仍是 ROUND{N}_SCHEDULING（无日历可删）"]),
])
```

**硬规则**：
- **不动 stage**——已经在 SCHEDULING；本 chain 仅刷字段 + 发邮件。
- **不跑 `cal_del`**——SCHEDULING 阶段 `calendar_event_id` 已是 `__NULL__`,没有日历可删（强行调用会 fail）。
- **不写 `calendar_event_id`**——保持 `__NULL__`,等候选人 confirm 后由 §4.2 建新日历时回填。
- 用 `reschedule` 模板（不是 `round{N}_invite`）——措辞强调"再次调整 / 抱歉重排"。
- 与 §4.12.2 区别：本节适用"已经改过一次,候选人/老板又改第二次"；§4.12.2 适用"WAIT_RETURN 恢复 / 二面初邀请之类首次进入 SCHEDULING 后老板给时间"。

### 4.4 候选人在国外暂缓

> **仅适用 stage = `ROUND1_SCHEDULING` / `ROUND1_SCHEDULED` / `ROUND2_SCHEDULING` / `ROUND2_SCHEDULED`**，`intent=defer_until_shanghai`（v3.8.1：原口语缩写 `defer_until_return` 不是合法值）。
> `EXAM_SENT` / `EXAM_REVIEWED` 阶段候选人说"国外暂缓" → STOP 问老板（走 SKILL.md §2.3.1 P3 单步 confirm 路径），因为这些 stage 的字段语义不同，本 chain 的 `--set round{N}_time=__NULL__` 不适用。

```python
# 当前 stage 是 SCHEDULED 才有日历可删；SCHEDULING 阶段没建过日历
had_calendar = bool(cand[f"round{N}_calendar_event_id"])
steps = []
if had_calendar:
    steps.append(Step("cal_del", "feishu.cmd_calendar_delete",
        ["--event-id", cand[f"round{N}_calendar_event_id"], "--reason", "candidate defer"]))

# Polaris 文案按是否曾有日历分支（v3.8.1：避免 SCHEDULING 阶段错说"原日历已删除"）
calendar_note = "原日历已删除" if had_calendar else "原本未建日历，无需删除"

steps += [
    Step("send", "outbound.cmd_send",
        ["--talent-id", tid, "--template", "defer",
         "--vars", f"round_label={'一面' if N==1 else '二面'}"]),
    Step("update", "talent.cmd_update",
        ["--talent-id", tid, "--stage", "WAIT_RETURN",
         "--set", f"wait_return_round={N}",
         "--set", f"round{N}_time=__NULL__",
         "--set", f"round{N}_calendar_event_id=__NULL__"]),
    # Polaris 运营同步卡：候选人暂缓 = 重要状态变化，必须告知
    Step("notify_polaris", "feishu.cmd_notify",
        ["--to", "polaris", "--severity", "info",
         "--title", f"候选人状态变化：{name} 暂缓（国外）",
         "--body", f"talent={tid} round={N}\n"
                   f"stage→WAIT_RETURN（wait_return_round={N}）\n"
                   f"{calendar_note}；候选人回国老板触发 §4.12 恢复"]),
]
run_chain(steps)
```

**硬规则**：
- **顺序：cal_del（如有）→ send → update**——先删旧日历再发"暂缓"邮件，避免候选人收到暂缓邮件后日历事件还挂着的不一致状态（参见 §4.3.3 失败处理表里的同类设计）。
- **`wait_return_round` 必填**（写当前的 N）；`talent.cmd_update --stage WAIT_RETURN` 没有 `wait_return_round` 时 §5.8 路径会回不来。
- 候选人回国后老板恢复流程走 §4.12（不是 §4.7——§4.7 是候选人主动来信触发推飞书；§4.12 是老板主动恢复触发改 stage）。

### 4.5 笔试通过 → 直接发二面邀请

> stage = `EXAM_REVIEWED`，老板拍板通过 + 给二面时间。
> 如果老板只说"笔试通过 / 安排二面"但没给时间，agent **只能问二面候选邀请时间**，不能输出"确认后建日历 / 更新 `ROUND2_SCHEDULED`"这类旧流程承诺。正确澄清话术必须说明：收到时间后只会发二面邀请并进入 `ROUND2_SCHEDULING`；候选人回信 confirm 后，还要老板再次明确授权才建日历并进入 `ROUND2_SCHEDULED`。
> 如果老板已经给了时间，agent 的 propose 也**只能**覆盖下面这条 §4.5：发送 `round2_invite` + 推到 `ROUND2_SCHEDULING`。确认回复（如"确认安排 黄琪 2026-05-20 15:00"）只授权发二面邀请，**不授权**建日历，也**不授权** `ROUND2_SCHEDULED`。禁止把 §4.5 和 §4.2 合并成一次 confirm。

```python
run_chain([
    Step("send", "outbound.cmd_send",
        # v3.8.6: location 由 cmd_send 自动注入, 不要手动传。
        ["--talent-id", tid, "--template", "round2_invite",
         "--vars", f"round2_time={t}"]),
    Step("update", "talent.cmd_update",
        ["--talent-id", tid, "--stage", "ROUND2_SCHEDULING",
         "--set", f"round2_time={t}",
         "--set", "round2_invite_sent_at={send.sent_at}",
         "--set", "round2_confirm_status=PENDING"]),
    # Polaris 运营同步卡：候选人进入二面 = 重要状态推进
    Step("notify_polaris", "feishu.cmd_notify",
        ["--to", "polaris", "--severity", "info",
         "--title", f"候选人状态变化：{name} 笔试通过 → 二面",
         "--body", f"talent={tid}\n"
                   f"二面提议时间={t}（候选人 confirm 后由 §4.2 chain 建日历）\n"
                   f"stage→ROUND2_SCHEDULING"]),
    Step("notify_boss", "feishu.cmd_notify",
        ["--to", "boss", "--severity", "info",
         "--title", f"二面邀请已发：{name} {t}",
         "--body", f"talent={tid} stage→ROUND2_SCHEDULING（等候选人 confirm）"]),
])
```

> 全自动等价路径：`exam.cmd_exam_result --result pass --round2-time ...`，两条路径不要叠加触发。该命令的 `--round2-time` 是**候选人邀请时间**，不是最终确认时间；命令只进入 `ROUND2_SCHEDULING`，不建日历、不进入 `ROUND2_SCHEDULED`。
> 注：`exam.cmd_exam_result` 内部目前**不**推 Polaris 运营同步卡——若走 atomic CLI 等价路径，Polaris 通过后续 §4.2 升级到 ROUND2_SCHEDULED 时获知。

### 4.6 笔试不过 → 拒信 + 留人才池

> stage = `EXAM_REVIEWED`，老板拍板不过但保留。

```python
run_chain([
    Step("send", "outbound.cmd_send",
        ["--talent-id", tid, "--template", "rejection_generic",
         "--context", "rejection"]),
    Step("update", "talent.cmd_update",
        ["--talent-id", tid, "--stage", "EXAM_REJECT_KEEP",
         "--reason", "agent: exam reject keep"]),
    # Polaris 运营同步卡：候选人进入终态留池，是重要状态变化
    Step("notify_polaris", "feishu.cmd_notify",
        ["--to", "polaris", "--severity", "info",
         "--title", f"候选人状态变化：{name} 笔试不过 → 留池",
         "--body", f"talent={tid}\n"
                   f"stage→EXAM_REJECT_KEEP（终态：留人才池，已发拒信）"]),
])
```

**硬规则**：
- `--context rejection`（v3.5.11 起）：让 `talent_emails` 中这封拒信的 `context` 字段统一标 `rejection`，与 cron `auto_reject` 路径保持一致；事后审计/查询/幂等保护时不需要区分"人工拒"和"自动拒"。
- Polaris 卡仅本 chain 推；cron `auto_reject` 路径目前**不**推 Polaris 卡（cron 是"3 天未交"自动拒，业务上不算人工主动决策——若需要补，改 `auto_reject.cmd_scan_exam_timeout` 内部加一行 `feishu.cmd_notify --to polaris` 调用）。
- 与 cron `auto_reject` 的模板 / 终态差别（v3.8.3 起）：人工路径（本 §4.6）用 `rejection_generic`（委婉，含"已保留至我们公司人才库"措辞）+ stage→`EXAM_REJECT_KEEP` 留池；cron 路径用 `rejection_exam_no_reply`（直白，明说"未在约定时间内提交"）+ `talent.cmd_delete` 物理删档（归档到 `data/deleted_archive/`，需要时可人工恢复）。

### 4.7 WAIT_RETURN 候选人主动联系 → 仅推飞书

> stage = `WAIT_RETURN`，`inbox.cmd_analyze` 输出任意 intent + LLM `summary` 含"回来 / 回国 / 想约 / 可以面试 / 在国内了"等回归信号（**v3.8.1 修订**：原文写的 `intent=return_to_shanghai` 不是合法 `valid_intents` 值；实际表现为 `question_boss` / `thanks_fyi` / `defer_until_shanghai`（候选人改主意又想暂缓）/ `other`,以 LLM `summary` 语义判断为准）。**不自动恢复 stage**。

```python
run_chain([
    Step("notify", "feishu.cmd_notify",
        ["--severity", "warn", "--title", "WAIT_RETURN 候选人主动联系",
         "--body", f"talent={tid} round={cand['wait_return_round']}\n"
                   f"intent={ai['intent']} summary={ai['summary']}\n\n"
                   f"建议老板执行：talent.cmd_update --stage ROUND{cand['wait_return_round']}_SCHEDULING --reason 'candidate returned'"]),
])
```

> 即便 LLM `confidence>0.9` 也不自动恢复 stage——"回来了"语义太模糊。

### 4.8 老板批准 cached draft 一键发出（POST_OFFER_FOLLOWUP）

> **触发**：老板**在飞书**对 bot 说"用 cached draft 给 X 回"/"OK 就发"/"用这个草稿回他"等指令（Hermes Gateway 接到飞书消息后路由到 recruit-ops agent runtime）。
> **前置**：`inbox.cmd_analyze` 已跑过 → `talent_emails.ai_payload.draft` 已写好（POST_OFFER_FOLLOWUP 阶段任意 inbound 触发）+ 老板已在飞书消息里看过 draft 预览。
> **不是飞书按钮触发**——飞书消息是 `feishu.cmd_notify` 推送的纯文本（含 draft 摘要 + email_id + 候选人邮件原文节选），**没有任何按钮**；老板看完决定要发就**在飞书直接回一条普通文字消息**给 bot（"OK 发" / "用这个草稿回" / "{email_id} 发出去"），Hermes Gateway 接到后由 agent 跑本节 chain。

```python
run_chain([
    Step("send", "outbound.cmd_send",
        ["--talent-id", tid, "--use-cached-draft", email_id]),
    Step("notify", "feishu.cmd_notify",
        ["--severity", "info", "--title", "已发送 Offer 跟进回复"]),
])
```

> draft 不存在时第一步会失败（rc=2，stderr `没有 draft 字段`），第二步因 chain 短路不会执行——agent 改推 `severity=warn`「草稿缺失」飞书消息让老板手动写回信。

### 4.9 老板说"直接跳到 X" → force-jump 单步

> **最高优先级，先看这条**。两个触发条件**任意一个**命中即走 force-jump：
> 1. **语义触发**：老板原话出现「直接」「跳到」「跳过」「略过」「不要 X」「强制」中任意一个；
> 2. **结构触发**：`(current_stage → target_stage)` **不在** [`scripts/talent/cmd_update.py::_NATURAL_TRANSITIONS`](../scripts/talent/cmd_update.py) 白名单里（agent 直接对照那 14 行 frozenset，**不**自己 BFS 算路径）。

```python
run_chain([
    Step("jump", "talent.cmd_update",
        ["--talent-id", tid, "--stage", target_stage, "--force",
         "--reason", f"boss原话: {boss_quote}"]),
    # Polaris 运营同步卡：force-jump 是非常规跨 stage，必须同步
    Step("notify_polaris", "feishu.cmd_notify",
        ["--to", "polaris", "--severity", "info",
         "--title", f"候选人状态变化（force-jump）：{name}",
         "--body", f"talent={tid}\n"
                   f"stage：{current_stage} → {target_stage}（老板强制跳转）\n"
                   f"老板原话：{boss_quote}"]),
])
```

**绝对禁止**：
- ❌ 拼任何"自然推进"chain（`exam.cmd_exam_result --result pass` / `interview.cmd_result --result pass` 都会真发候选人邮件）
- ❌ 编造时间 / 邮件 / confirm 状态去满足必填参数——这就是路径选错的信号
- ❌ 用多次 `cmd_update --set` 模拟"候选人 confirm 了"
- ❌ 在笔试通过给二面时间时，把"发送二面邀请"和"创建二面日历 / ROUND2_SCHEDULED"放进同一次确认。第一次确认只到 `ROUND2_SCHEDULING`。
- ❌ 在老板没说面试的情况下创建任何日历 / 发任何候选人邮件

**典型场景**：

| 老板原话 | 目标 stage |
|---|---|
| "X 笔试通过，直接进 offer 阶段" | `POST_OFFER_FOLLOWUP` |
| "X 不用面了，直接发 offer 让 HR 跟进" | `POST_OFFER_FOLLOWUP` |
| "X 笔试不过，但保留人才池"（没等 AI 评审） | `EXAM_REJECT_KEEP` |
| "X 不在国内了，强制暂缓" | `WAIT_RETURN`（带 `--set wait_return_round=N`） |

**模糊判定**：拿不准是"正常推进 + 发邮件"还是"force-jump 单步" → STOP 问老板，**不要**默认按"正常推进"走（会发不可撤销的候选人邮件）。

### 4.10 发放 onboarding offer（POST_OFFER_FOLLOWUP **谈判终点**）

> **位置**：本节是 POST_OFFER_FOLLOWUP 阶段的**最终一封邮件**——发**带合同附件的正式录用邮件**。
> POST_OFFER 阶段绝大多数邮件交互（薪资问答、入职日期协商、福利询问、客套答谢）走 §5 + §4.8 chat 模式：
>
> ```
> 老板说二面通过
>   → interview.cmd_result --round 2 --result pass
>   → stage 进入 POST_OFFER_FOLLOWUP
>   → agent/CLI 提示老板是否发送入职前邮件，并询问入职时间 + 日薪（默认 350）
>   → 老板二次确认："确认发送，{onboard_date} 入职，日薪 {daily_rate|350}"
>   → offer.cmd_send_onboarding_offer（本节，模板 + 自动追加合同 docx）
>   → 邮件发送成功后通知 HR
>   → 后续 POST_OFFER_FOLLOWUP 邮件仍按 §5 + §4.8 chat 模式处理
> ```

#### 触发条件（v3.8.1 修订口径：2 要素必填 + 1 要素可默认）

| # | 要素 | 来源 | 缺失时 |
|---|---|---|---|
| 1 | **明确动词**（**必填**） | 老板原话出现"发 offer" / "录用通知" / "入职邮件" / "发合同" / "下 offer" 之一 | STOP——`OK 回他`、`同意了`、`告诉他通过`等都**不算**，走 §4.8 cached draft |
| 2 | **`onboard_date`**（**必填**） | 老板原话明确给出"X 月 X 日入职"或同义表达 | STOP 问老板"具体哪天入职" |
| 3 | **`daily_rate`**（**可默认 350**） | 老板原话明确给出（"350/天"等）→ 用老板说的；老板未提 → 默认 **350**；老板说"按谈好的薪资发"/"按之前说的"等暗示已有具体数字 → STOP 让老板复述具体数字（不能默认 350） | 见 3 列规则 |

> **绝不**默认猜测要素 1 / 2。要素 3 仅在老板**完全没提**薪资时默认 350；只要老板说出了任何指向"已经谈过具体数字"的措辞（"按谈好的"/"按之前说的"），必须 STOP 让老板复述。

#### atomic CLI

```bash
PYTHONPATH=scripts python3 -m offer.cmd_send_onboarding_offer \
  --talent-id <tid> \
  --onboard-date "<date>" \
  --daily-rate "<rate-or-350>"
```

#### §4.10 vs §4.8 决策表（POST_OFFER 阶段必看）

| 维度 | §4.8（chat 模式 cached draft） | §4.10（谈判终点 onboarding offer） |
|---|---|---|
| **用途** | 答疑、客套、澄清"等老板/HR 确认" | 发**正式录用邮件**（已谈妥所有数字） |
| **触发** | 候选人来信 + 老板说"OK 用 draft 回 X" | 老板确认发送入职前邮件 + **`onboard_date`**（`daily_rate` 老板未提默认 350,明说则用老板的；老板若说"按谈好的薪资"则 STOP 让老板复述） |
| **正文来源** | LLM 生成 draft（prompt 严格约束**不含**具体薪资 / 入职日期 / 福利数字） | `email_templates/onboarding_offer.txt` 模板渲染（含老板填写的具体数字） |
| **附件** | **无**（`--use-cached-draft` 路径**不**触发 `auto_attachments`） | **强制**带 2 个合同 docx（实习协议 + 入职登记表，由 `email_templates.auto_attachments` 自动追加） |
| **频率** | POST_OFFER 期间反复 N 次 | 一个候选人**最多一次** |
| **stage 变化** | 不动（保持 POST_OFFER_FOLLOWUP） | 不动（保持 POST_OFFER_FOLLOWUP，等入职完成后 §4.15 → ONBOARDED） |

**绝对禁止**（合规风险）：

- ❌ **用 §4.8 cached draft 路径发"正式 offer"** —— `--use-cached-draft` 路径**不会**触发 `auto_attachments`（见 `outbound/cmd_send.py` L409 `if args.template:` 守卫），实习协议 + 入职登记表 docx **必然漏发**，是合规事故。正式 offer 必须走 `--template onboarding_offer`。
- ❌ 在 §4.10 chain 里手动 `--attach` 任何合同文件——`auto_attachments` 已经自动追加，重复 attach 会被 cmd_send 内部去重但浪费 IO；想换合同版本去改 `email_templates/auto_attachments.py::_REGISTRY`。
- ❌ 老板说"OK 回他"就触发 §4.10——这是 chat 模式（§4.8）的指令，**不是**正式 offer 触发词。

#### 硬规则

- **二次确认必填**：二面通过后的第一步只进入 `POST_OFFER_FOLLOWUP` 并询问老板；只有老板再次确认发送且给出 `onboard_date` 后才可调用 `offer.cmd_send_onboarding_offer`。
- `daily_rate` 完全未提则默认 350；老板明说"按谈好的"等暗示已有具体数字时必须 STOP 让老板复述。详见上方"触发条件"表。
- 附件由 `email_templates.auto_attachments` 自动追加，**不要** 手动 `--attach`；文件不在了 cmd_send 会 fail-fast 拒发（见 `auto_attachments.py::auto_attachments_for` 抛 RuntimeError）。
- HR 通知由 `offer.cmd_send_onboarding_offer` 在邮件发送成功后触发；邮件失败时不得通知 HR。
- **不动 stage**，保持 `POST_OFFER_FOLLOWUP`。entry/exit 由其他路径处理（入职后 `talent.cmd_delete` 归档；候选人 decline 走 §5 那行 + 老板拍板）。
- **重发幂等**：发前 SQL `SELECT 1 FROM talent_emails WHERE talent_id=? AND template='onboarding_offer' AND direction='outbound' LIMIT 1` 查一次，已有就 STOP 推 `severity=warn` 让老板确认是否真要重发（重发会再发一份合同附件给候选人，容易乱）。

### 4.11 一/二面 / 笔试结果落库（**单 atomic CLI，非 chain**）

> **触发（按角色权限,v3.8.1 收紧）**：
> - **HR 可触发**：仅"X 一面通过 / 一面不过 / 一面通过+直接二面"3 行（实务上由 HR 在飞书转述面试官反馈）。
> - **老板专属**：二面任意结果 / 笔试任意结果 / offer 阶段所有动作。HR 在这些范围内说话 → agent 应**忽略并请 HR 转告老板**（参 §1.1 角色边界硬规则）。
>
> 这些路径**不是 chain**——`interview.cmd_result` / `exam.cmd_exam_result` 内部已经编排了
> "必要候选人邮件（经 `outbound.cmd_send`）+ 改 stage + 写审计"，agent 直接调一条命令即可，**禁止**拆成多步 chain。

| 触发角色 | 原话场景 | 当前 stage | atomic CLI（**一步搞定**） |
|---|---|---|---|
| HR / 老板 | "X 一面通过 + 邮箱 Y" | `ROUND1_SCHEDULED` | `interview.cmd_result --talent-id X --round 1 --result pass --email Y` |
| HR / 老板 | "X 一面通过 + 直接二面 Z 时间"（跳过笔试） | `ROUND1_SCHEDULED` | `interview.cmd_result --talent-id X --round 1 --result pass_direct --round2-time Z` |
| HR / 老板 | "X 一面不过" | `ROUND1_SCHEDULED` | `interview.cmd_result --talent-id X --round 1 --result reject_delete --confirm-reject-delete X`（自带先发 `rejection_generic` + 物理删档归档；v3.8.1 hard guard：confirm 必须严格等于 `--talent-id`） |
| **仅老板** | "X 二面通过" | `ROUND2_SCHEDULED` | `interview.cmd_result --talent-id X --round 2 --result pass`（一步推到 `POST_OFFER_FOLLOWUP` + 询问老板是否发送入职前邮件；不通知 HR） |
| **仅老板** | "X 二面不过留池" | `ROUND2_SCHEDULED` | `interview.cmd_result --talent-id X --round 2 --result reject_keep`（推到 `ROUND2_DONE_REJECT_KEEP`） |
| **仅老板** | "X 二面不过删档" | `ROUND2_SCHEDULED` | `interview.cmd_result --talent-id X --round 2 --result reject_delete --confirm-reject-delete X`（v3.8.1 hard guard） |
| **仅老板** | "X 笔试通过 + 二面 Y 时间" | `EXAM_SENT` / `EXAM_REVIEWED` | `exam.cmd_exam_result --talent-id X --result pass --round2-time Y`（只进入 `ROUND2_SCHEDULING` + 发邀请；**不建日历、不确认时间**。与 §4.5 chain **二选一**，不要叠加） |
| **仅老板** | "X 笔试通过 / 安排二面"（缺时间） | `EXAM_SENT` / `EXAM_REVIEWED` | **STOP 问时间**：请老板提供二面候选邀请时间；禁止承诺建日历或 `ROUND2_SCHEDULED` |
| **仅老板** | "X 笔试不过留池"（已 AI 评审） | `EXAM_REVIEWED` | `exam.cmd_exam_result --talent-id X --result reject_keep`（与 §4.6 chain **二选一**） |

**绝对禁止**（INCIDENT_RULES.md §3 反复警告的反模式）：

- ❌ 拼"自然推进 chain"：例如老板说"X 一面通过"，agent 拼 `outbound.cmd_send --template exam_invite` + `talent.cmd_update --stage EXAM_SENT` + `talent.cmd_update --set exam_id=...`——这会**绕过结果 CLI 的审计与顺序约束**、**参数不一致**。
- ❌ 把 `interview.cmd_result` 拆成"先 send 后 update"——它的内部顺序由 CLI 自己保证，agent 不应该看见内部。
- ❌ 在 round 2 用 `--result pass_direct` / `pending`（CLI 不接受，参见 [CLI_REFERENCE.md](CLI_REFERENCE.md#interviewcmd_resultpy--记录面试结果)）。
- ❌ 老板给二面时间后直接 `ROUND2_SCHEDULED` / 建二面日历。任何二面时间确认都必须先 `ROUND2_SCHEDULING` 发邀请，等候选人 confirm，再等老板二次授权。

**与 §4.5 / §4.6 的关系**：

- §4.5 / §4.6 是 chain 形态（`outbound.cmd_send` + `talent.cmd_update` 两步），适用于 agent 已经把"判 pass/不过"的决策权交给老板（老板**在飞书**给 bot 下指令 → Hermes 路由到 agent；**不是**飞书按钮——飞书没按钮但**有入站通道**）、且需要按 chain 显式记录发送时间 / 状态字段的场景。`company` / `location` 是 `outbound.cmd_send` 自动注入的常量，不要在 `--vars` 里传。
- §4.11 的 atomic CLI 是 chain 形态的"等价捷径"，参数更少 / 没法自定义模板变量。
- **二选一**：同一个老板指令**不要**同时跑 §4.5 和 §4.11；以老板原话最具体的那条为准。

### 4.12 WAIT_RETURN 老板主动恢复 → 推回 SCHEDULING（不发邮件，等老板给新时间）

> **触发**：stage = `WAIT_RETURN`，老板原话出现"X 回来了" / "X 可以面试了" / "恢复 X 的流程"。
> 与 §4.7（候选人主动来信触发推飞书）刻意不对称：候选人来信 = 信号，老板拍板 = 触发源。

```python
# wait_return_round 决定回到哪一轮（在 §4.4 进入 WAIT_RETURN 时就已经写好）
target_round = cand["wait_return_round"]   # 1 或 2
target_stage = f"ROUND{target_round}_SCHEDULING"

run_chain([
    Step("update", "talent.cmd_update",
        ["--talent-id", tid, "--stage", target_stage,
         "--reason", "boss confirmed candidate returned"]),
    Step("notify_boss", "feishu.cmd_notify",
        ["--severity", "info",
         "--title", f"已恢复 {name} 的面试流程",
         "--body", f"talent={tid} stage→{target_stage}\n"
                   f"请告知新的{('一面' if target_round==1 else '二面')}时间和地点，"
                   f"我会按 §4.12.2 chain 发邀请并等候选人 confirm"]),
    # Polaris 运营同步卡：候选人从 WAIT_RETURN 恢复 = 重要状态变化
    Step("notify_polaris", "feishu.cmd_notify",
        ["--to", "polaris", "--severity", "info",
         "--title", f"候选人状态变化：{name} 从 WAIT_RETURN 恢复",
         "--body", f"talent={tid}\n"
                   f"stage：WAIT_RETURN → {target_stage}\n"
                   f"等老板给新时间后由 §4.12.2 chain 发邀请"]),
])
```

**硬规则**：
- 本 chain **不发**候选人邮件、**不建**日历——候选人在 SCHEDULING 阶段还没"被邀请"，老板下一步给新时间后由 §4.12.2 处理。
- `wait_return_round` 字段必须存在（§4.4 进入 WAIT_RETURN 时已写好）；如果为空 → STOP 问老板"恢复到一面还是二面？"，**不**默认猜。
- 这是 natural transition（`WAIT_RETURN → ROUND{N}_SCHEDULING`），**不需要** `--force`。
- 老板若同时给了新时间（"X 回来了，下周二 14 点一面"）→ 跑完本 chain 之后**紧接着**走 §4.12.2（**不是** §4.1——§4.1 要求 stage=NEW；本场景已经在 SCHEDULING）。

#### 4.12.2 ROUND{N}_SCHEDULING + 老板给新提议时间 → 发邀请等候选人 confirm

> **触发**：stage = `ROUND{N}_SCHEDULING`（来源：§4.12 恢复 WAIT_RETURN / §4.3.3 改期回退 / §4.5 二面发邀请后的同 stage 续行），老板飞书原话给出具体新时间。
> **不建日历、不直推 SCHEDULED**——日历由 §4.2（候选人 confirm 后）建。

```python
# 一面 fail-fast：如果 route 不可解析,提前 STOP（避免发邀请后候选人 confirm 走 §4.2 时才发现派不出去）。
# 注意：这里 route 结果**仅用于 fail-fast 验证**——本节不建日历,所以不传 extra-attendee；
# 真正派给面试官的日历邀请由 §4.2（候选人 confirm 后建日历）那一步重新跑 route 并使用结果。
# §4.5 二面默认老板自己面,无需 route。
if N == 1:
    route = run_atomic("intake.cmd_route_interviewer", ["--talent-id", tid, "--json"])
    if route["ambiguous"] or route["config_error"]:
        run_atomic("feishu.cmd_notify", ["--to", "hr", "--severity", "warn",
            "--title", f"派单 fail-fast：{name}",
            "--body", route["ambiguous_reason"] or route["config_error_detail"]])
        STOP

# v3.8.6: location 不再是 chain 输入, 由 cmd_send 自动注入 constants.LOCATION。
# 公司搬家 / 改地址 → 改 email_templates/constants.py 一处即可。
template = "round1_invite" if N == 1 else "round2_invite"

run_chain([
    Step("send", "outbound.cmd_send",
        ["--talent-id", tid, "--template", template,
         "--vars", f"round{N}_time={t}"]),
    Step("update", "talent.cmd_update",
        ["--talent-id", tid,
         "--set", f"round{N}_time={t}",
         "--set", f"round{N}_invite_sent_at={send.sent_at}",
         "--set", f"round{N}_confirm_status=PENDING"]),
    # Polaris 运营同步卡：候选人收到新邀请,Polaris 应知
    Step("notify_polaris", "feishu.cmd_notify",
        ["--to", "polaris", "--severity", "info",
         "--title", f"候选人状态变化：{name} {('一面' if N==1 else '二面')}邀请已发",
         "--body", f"talent={tid} round={N} 提议时间={t} 地点={loc}\n"
                   f"stage 仍是 ROUND{N}_SCHEDULING（候选人 confirm 后由 §4.2 升级 + 建日历）"]),
])
```

**硬规则**：
- **不动 stage**——已经在 ROUND{N}_SCHEDULING；本 chain 仅写邀请字段 + 发邮件。
- **不建日历**——等候选人 confirm 后由 §4.2 chain 建。
- **`location` 必传**——`round1_invite.txt` / `round2_invite.txt` 模板都包含 `$location` 占位符,缺失会导致渲染失败。老板未明示地点 → STOP 让老板补。
- **N=1 时 route 仅用于 fail-fast**——本 chain 不传 `--extra-attendee` 给任何 CLI（不建日历）；面试官的日历邀请等候选人 confirm 后由 §4.2 chain 重新跑 route 后建。
- 候选人 confirm 后 `intent=confirm_interview` → 走 §4.2 升级到 SCHEDULED + 建日历。
- 与 §4.5（笔试通过→二面初邀请）的关系：§4.5 是 `EXAM_REVIEWED → ROUND2_SCHEDULING` 的入口（含 `--stage ROUND2_SCHEDULING`）；本节是 stage **已经是** `ROUND{N}_SCHEDULING` 后的续行（不动 stage）。

### 4.13 候选人主动 withdraw / decline（**全 stage 覆盖**）

> **触发（统一入口，v3.8.1 简化）**：候选人放弃信号有两种来源,但 **chain 完全相同**——
> - **来源 1：候选人 inbound 邮件**：`inbox.cmd_analyze` 输出 `intent=decline_withdraw`（候选人来信明确"决定不参加 / 不来了 / 撤回 / 拒绝 offer / 不要这份 offer"）。**区别于** `defer_until_shanghai`（暂缓→§4.4）和 `reschedule_request`（改期→§4.3）。
> - **来源 2：老板飞书主动转告**：候选人通过非公司邮箱（私下微信 / 电话 / 领英 / 当面）告知老板已决定不入职时,老板飞书发"X 拒了 / X 不来了 / X 决定不入职了 / X 撤回了"。
>
> stage ∈ **任意非终态 stage**（`NEW`, `ROUND1_*`, `EXAM_SENT`, `EXAM_REVIEWED`, `WAIT_RETURN`, `ROUND2_*`, `POST_OFFER_FOLLOWUP`）。两种来源 chain 完全相同,只在飞书 `--body` 文案的"信号来源"行有差异（"候选人邮件原话" vs "老板转述"）。

```python
# v3.8.1 简化（事故源 INCIDENT_RULES.md §12 / §13）：菜单按 stage 三类划分
# - NEW: "删除（不发拒信） / 暂不处理"
# - ROUND1_*: "删除（含拒信） / 暂不处理"（v3.6 设计无 reject_keep——一面前期不留池）
# - ROUND2_* / EXAM_* / WAIT_RETURN / POST_OFFER_FOLLOWUP: "留人才池 / 删除"
# 所有"删除"路径都强制带代码层 hard guard 参数（参见硬规则）

if current_stage == "NEW":
    # NEW 阶段：候选人没"被招"过,删档不发拒信
    options = (
        f"  1) 删除（不发拒信） → talent.cmd_delete --talent-id {tid} \\\n"
        f"     --confirm-delete-talent {tid} --reason 'candidate withdrew before any interview'\n"
        f"  2) 暂不处理 → 不操作,保留 NEW 状态等候选人后续如有变化"
    )
elif current_stage in {"ROUND1_SCHEDULING", "ROUND1_SCHEDULED"}:
    # 一面阶段：v3.6 设计无 reject_keep,删除走 reject_delete + hard guard
    options = (
        f"  1) 删除（含拒信） → interview.cmd_result --talent-id {tid} --round 1 \\\n"
        f"     --result reject_delete --confirm-reject-delete {tid}\n"
        f"  2) 暂不处理（候选人可能反悔） → 不操作；面试时间到了候选人没出现走 §4.14 no-show 路径\n"
        f"     （**没有**自动 cron 处理面试 no-show / 撤回——只有 §4.14 老板飞书触发的人工路径）"
    )
elif current_stage in {"ROUND2_SCHEDULING", "ROUND2_SCHEDULED"}:
    options = (
        f"  1) 留人才池（含拒信） → interview.cmd_result --talent-id {tid} --round 2 \\\n"
        f"     --result reject_keep\n"
        f"  2) 删除（含拒信） → interview.cmd_result --talent-id {tid} --round 2 \\\n"
        f"     --result reject_delete --confirm-reject-delete {tid}"
    )
elif current_stage in {"EXAM_SENT", "EXAM_REVIEWED"}:
    # 笔试阶段撤回:已经发过笔试邀请,该发拒信
    options = (
        f"  1) 留人才池（含拒信） → outbound.cmd_send --template rejection_generic --context rejection\n"
        f"     之后 talent.cmd_update --stage EXAM_REJECT_KEEP --reason 'candidate withdrew during exam phase'\n"
        f"  2) 删除（含拒信） → outbound.cmd_send --template rejection_generic --context rejection\n"
        f"     之后 talent.cmd_delete --talent-id {tid} --confirm-delete-talent {tid} \\\n"
        f"            --reason 'candidate withdrew during exam phase'"
    )
elif current_stage == "WAIT_RETURN":
    # WAIT_RETURN 撤回:候选人在国外彻底放弃。和 §4.7"主动联系"区分:
    # §4.7 是"想回来",这里是"不想回来了"——前者推恢复建议,后者推 reject 二选一。
    options = (
        f"  1) 留人才池（含拒信） → outbound.cmd_send --template rejection_generic --context rejection\n"
        f"     之后 talent.cmd_update --stage EXAM_REJECT_KEEP --force --reason 'candidate withdrew while abroad'\n"
        f"     （WAIT_RETURN → EXAM_REJECT_KEEP 不在 _NATURAL_TRANSITIONS 白名单,必须 --force）\n"
        f"  2) 删除（含拒信） → outbound.cmd_send --template rejection_generic --context rejection\n"
        f"     之后 talent.cmd_delete --talent-id {tid} --confirm-delete-talent {tid} \\\n"
        f"            --reason 'candidate withdrew while abroad'"
    )
elif current_stage == "POST_OFFER_FOLLOWUP":
    # v3.8.1（事故源 INCIDENT_RULES.md §13）：撤销 v3.8 的 chain 层"禁删档"收紧,
    # 改由代码层 hard guard 兜底（talent.cmd_delete 强制 --confirm-delete-talent
    # 必须等于 --talent-id,见硬规则）。chain 层菜单恢复"留池 / 删除"二选一。
    # v3.8.2（拆桶）：留池路径推到 OFFER_DECLINED_KEEP（语义上是"候选人 say no"），
    # 不再借用 ROUND2_DONE_REJECT_KEEP（那个严格保留给"二面面试不过"）。
    # POST_OFFER_FOLLOWUP → OFFER_DECLINED_KEEP 已加入 _NATURAL_TRANSITIONS,无需 --force。
    options = (
        f"  1) 留人才池（含拒信） → outbound.cmd_send --template rejection_generic --context rejection\n"
        f"     之后 talent.cmd_update --stage OFFER_DECLINED_KEEP \\\n"
        f"            --reason 'candidate declined offer'\n"
        f"     （v3.8.2 起 POST_OFFER_FOLLOWUP → OFFER_DECLINED_KEEP 是 natural transition,无需 --force）\n"
        f"  2) 删除（含拒信） → outbound.cmd_send --template rejection_generic --context rejection\n"
        f"     之后 talent.cmd_delete --talent-id {tid} --confirm-delete-talent {tid} \\\n"
        f"            --reason 'candidate declined offer'"
    )

# 信号来源行：候选人邮件 / 老板飞书转告
if trigger == "candidate_email":
    source_line = (f"信号来源：候选人邮件\n"
                   f"原话节选：{email['body_excerpt']}\n"
                   f"LLM 总结：{ai['summary']}\n")
else:  # trigger == "boss_relay"
    source_line = (f"信号来源：老板飞书转告（候选人未给公司邮箱来信）\n"
                   f"老板原话：{boss_utterance}\n")

run_chain([
    Step("notify_boss", "feishu.cmd_notify",
        ["--severity", "warn",
         "--title", f"候选人主动撤回：{name}",
         "--body", f"talent={tid} stage={current_stage}\n"
                   + source_line + "\n"
                   + f"请老板选择：\n{options}"]),
    # Polaris 运营同步卡：候选人撤回是重要状态变化
    Step("notify_polaris", "feishu.cmd_notify",
        ["--to", "polaris", "--severity", "info",
         "--title", f"候选人状态变化：{name} 主动撤回",
         "--body", f"talent={tid} stage={current_stage}\n"
                   f"日历未删除（等老板拍板后由 interview.cmd_result 或老板手动统一处理）\n"
                   f"老板正在决定：留池 / 删除"]),
])
```

**硬规则**：
- 本 chain **永远不**自动跑删除/留池——候选人撤回语义模糊（可能真撤、误会、谈条件、offer 阶段试探还价），由老板拍板。
- 已建日历**先不删**——老板拍板后按下面分类处理日历：
  - **`ROUND1_*` / `ROUND2_*` 走 `interview.cmd_result`** —— CLI 内部会处理日历归档（含 `feishu.cmd_calendar_delete`）+ 拒信 + stage + 审计,**这条路径日历会被自动删**。
  - **`EXAM_*` / `WAIT_RETURN` / `POST_OFFER_FOLLOWUP` 走手工 chain `outbound.cmd_send` + `talent.cmd_delete` / `talent.cmd_update --force`** —— 注意：**`talent.cmd_delete` 不删飞书日历**（它只归档 DB + FS 目录 + 候选人 alias）。这些 stage 通常没有日历（笔试/暂缓/谈 offer 阶段无日历事件）;若 stage 残留日历事件（异常情况）必须**显式追加** `feishu.cmd_calendar_delete --event-id ...` 一步,否则飞书日历会残留。
- **代码层 hard guard 兜底**（v3.8.1 新增,事故源 [INCIDENT_RULES.md §13](INCIDENT_RULES.md#13-2026-05-10--3-人误删事故复发doc-修订对运行中-agent-失效)）：所有"删除"路径必须带与 `--talent-id` 严格相等的 confirm 参数,否则 CLI 直接 `UserInputError`：
  - `talent.cmd_delete` → `--confirm-delete-talent <talent_id>`（适用 NEW / EXAM_* / WAIT_RETURN / POST_OFFER_FOLLOWUP 删除路径）
  - `interview.cmd_result --result reject_delete` → `--confirm-reject-delete <talent_id>`（适用 ROUND1_* / ROUND2_* 删除路径）
  - 这层是物理护栏——即使 LLM 误解 "请帮我删 X" 为"立即跑 cmd_delete X"也会因缺 confirm 参数失败,无法绕过。
- **stage 分组拒类 CLI 选择**：
  - `NEW` 没发过流程邮件 → `talent.cmd_delete` 不发拒信（候选人没被招过）
  - `ROUND1_*` / `ROUND2_*` → 走 `interview.cmd_result`（CLI 内部已处理拒信 + stage + 审计 + 日历归档）
  - `EXAM_SENT` / `EXAM_REVIEWED` / `WAIT_RETURN` / `POST_OFFER_FOLLOWUP` → 无专用等价 CLI,必须 `outbound.cmd_send` 发拒信 + `talent.cmd_update --force` 推留池终态 / `talent.cmd_delete --confirm-delete-talent` 删档（手工 chain）。
- `WAIT_RETURN` 推留池终态时**必须 `--force`**——这条路径（`WAIT_RETURN → EXAM_REJECT_KEEP`）不在 `_NATURAL_TRANSITIONS` 白名单。
- `POST_OFFER_FOLLOWUP → OFFER_DECLINED_KEEP`（v3.8.2 拆桶后）**已是** natural transition,**不需要** `--force`；老的"`POST_OFFER_FOLLOWUP → ROUND2_DONE_REJECT_KEEP --force`"写法已废弃,语义错位（那是"二面面试不过"留池,这里是"拒 offer"留池）。
- Polaris 卡 severity 用 `info`（运营同步,不打扰）；老板那张才是 `warn`（要做决策）。
- **来源 2（老板飞书转告）严禁 LLM 总结**：老板的话本身就是事实陈述,agent 严禁拿老板原话喂 LLM "提炼"再展示——把老板原话原样附在 `--body` 里即可。
- **双轮 confirm 强制**（参 SKILL.md §2.3 / §2.4）：notify_boss 推完决策卡后必须等老板**第二条飞书消息**显式批准对应选项的具体 CLI（"我同意删除 t_xxx" / "我同意留池"）才能执行；老板第一条"删了 X"既是 intent 又是发起命令,**不**等同于 confirm,不能跳过决策卡直接跑删除链。

### 4.14 候选人 no-show（约定面试时间到了人没来）

> **触发**：**老板**在飞书发消息反馈"X 没来" / "X 缺席了" / "约的时间过了 X 没出现"（Hermes Gateway 接收后路由到 agent）。
> stage ∈ {`ROUND1_SCHEDULED`, `ROUND2_SCHEDULED`}，且 `cand[f"round{N}_time"]` 已过去。
> **不是 inbound 邮件触发**——候选人 no-show 不会主动来信告知；这条路径只有老板能触发（飞书消息）。

```python
run_chain([
    Step("notify_boss", "feishu.cmd_notify",
        ["--severity", "warn",
         "--title", f"候选人 no-show：{name}（{('一面' if N==1 else '二面')}）",
         "--body", f"talent={tid} round={N} stage={current_stage}\n"
                   f"约定时间={cand[f'round{N}_time']}（已过 {hours_overdue}h）\n\n"
                   f"请老板二选一：\n"
                   f"  1) 给候选人一次机会重排 → 走 §4.3.3 改期 chain（cal_del + 改期模板 + 回 SCHEDULING）\n"
                   f"     适用：候选人事后联系/解释（如紧急情况）；老板愿意再给一次机会\n"
                   f"  2) 直接判鸽（不留池）→ 跑 chain：\n"
                   f"     a) outbound.cmd_send --template rejection_no_show --context rejection\n"
                   f"     b) interview.cmd_result --talent-id {tid} --round {N} --result reject_delete \\\n"
                   f"        --confirm-reject-delete {tid} --skip-email\n"
                   f"        （--skip-email 已经存在于 cmd_result 的 argparse,因为 step a 已发过 no-show 专用模板,\n"
                   f"        不能让 reject_delete 内部再发一次 rejection_generic 拒信——口径会冲突；\n"
                   f"        --confirm-reject-delete 是 v3.8.1 hard guard,值必须严格等于 --talent-id）\n\n"
                   f"     备选：如果老板觉得 rejection_no_show 模板措辞不合适,跳过 step a,改用\n"
                   f"     outbound.cmd_send --freeform-body / --freeform-subject 手填,然后再跑 step b。"]),
    # Polaris 运营同步卡：no-show 是重要业务事件
    Step("notify_polaris", "feishu.cmd_notify",
        ["--to", "polaris", "--severity", "info",
         "--title", f"候选人状态变化：{name} {('一面' if N==1 else '二面')} no-show",
         "--body", f"talent={tid} round={N} stage={current_stage}\n"
                   f"约定时间={cand[f'round{N}_time']}（已过 {hours_overdue}h 候选人未出现）\n"
                   f"老板正在决定：再给一次机会 / 直接判鸽"]),
])
```

**硬规则**：
- **不存在 `ROUND1_NO_SHOW` / `ROUND2_NO_SHOW` stage**。no-show 是事件/决策场景，不是候选人阶段；禁止调用 `talent.cmd_update --stage ROUND{N}_NO_SHOW`。代码层 `talent.cmd_update` 会提前拒绝这两个常见幻觉 stage，避免触发 DB CHECK 告警。
- no-show 后只有两条路径：老板给一次机会重排，或发 no-show 专用拒信并删除归档；不允许“先落一个 no-show stage 等以后处理”。
- **no-show / 面试阶段结果禁止进入 `EXAM_REJECT_KEEP`**。`EXAM_REJECT_KEEP` 只表示笔试未通过或特定撤回/暂缓留池；一面 no-show 不能留池，二面未通过留池必须用 `interview.cmd_result --round 2 --result reject_keep`。代码层会拒绝 `ROUND1_*` / `ROUND2_* → EXAM_REJECT_KEEP`，即使加 `--force` 也不允许。
- **不能**直接跑 `interview.cmd_result --result reject_delete` 不发任何邮件——这会让候选人完全不知道自己被淘汰（即便鸽人也要走过场）。要么发 `rejection_no_show`（专用模板），要么老板手填 freeform。
- **不能**跑默认的 `rejection_generic` 拒信措辞——`rejection_generic` 含"感谢您参加 $company 的招聘流程"，但候选人**没有真的参加**，措辞错。所以新增了 `rejection_no_show` 模板（v3.8）。
- `--skip-email` 标志位早就存在（`scripts/interview/cmd_result.py:170`）；之前它的存在意义不明显，v3.8 起明确用于 no-show 场景"先 outbound 发自定义模板,再 cmd_result 删档但不让它二次发拒信"。
- 候选人事后才来信解释（intent=`reschedule_request` / `question_boss`）——按 §4.3.1 普通改期处理（A 档），把"已 no-show"信息附在飞书 body 里供老板参考。

### 4.15 候选人完成入职 → ONBOARDED 终态

> **触发**：**老板**在飞书发消息说"X 入职了" / "X 已完成入职流程" / "X 上岗了"等明确入职已完成的话（Hermes Gateway 接收后路由到 agent）。
> stage = `POST_OFFER_FOLLOWUP`（其他 stage 不可能"已入职"——老板说错了应 STOP）。

```python
run_chain([
    Step("update", "talent.cmd_update",
        ["--talent-id", tid, "--stage", "ONBOARDED",
         "--reason", f"boss confirmed onboarding completed: {boss_quote}"]),
    Step("notify_boss", "feishu.cmd_notify",
        ["--severity", "info",
         "--title", f"已记录入职完成：{name}",
         "--body", f"talent={tid} stage→ONBOARDED（终态）\n"
                   f"原话：{boss_quote}\n\n"
                   f"备注：ONBOARDED 是叶子终态;如果将来需要把候选人完全归档（清理后续 inbound 不再分析等）,\n"
                   f"可执行 talent.cmd_delete --talent-id {tid} --confirm-delete-talent {tid} \\\n"
                   f"           --reason 'archived after onboarding'（v3.8.1 hard guard：confirm 必须等于 talent-id）。\n"
                   f"在归档前,候选人邮件还会被 inbox 收下并被 LLM 分析,但 §5 表里 ONBOARDED 行只推 info 卡,不自动起 draft。"]),
    # Polaris 运营同步卡：入职完成 = 招聘流程胜利收尾
    Step("notify_polaris", "feishu.cmd_notify",
        ["--to", "polaris", "--severity", "info",
         "--title", f"候选人状态变化：{name} 已入职",
         "--body", f"talent={tid} stage：POST_OFFER_FOLLOWUP → ONBOARDED（终态）\n"
                   f"招聘流程胜利收尾"]),
])
```

**硬规则**：
- `POST_OFFER_FOLLOWUP → ONBOARDED` 是 v3.8 新增的 natural transition，**不需要** `--force`。
- 仅适用 `current_stage=POST_OFFER_FOLLOWUP`；其他 stage 老板说"X 入职了" → STOP 问老板"该候选人当前 stage 是 X,不可能直接入职;是否走 §4.9 force-jump 推到 POST_OFFER_FOLLOWUP 后再标 ONBOARDED?"——**不**默认猜测。
- 入职后候选人**仍可能**来信（咨询合同、问报到流程等）——保持 stage=ONBOARDED，按 §5 表 ONBOARDED 行处理（推 info 卡，不自动回信）。
- 如果老板要彻底清理这个候选人（不希望以后还被 LLM 分析新邮件） → 在 ONBOARDED 之上**再**跑 `talent.cmd_delete`（**这是手动归档动作,不在本 chain 里**）。

### 4.16 通过飞书发送候选人文件 / 本地文件

> 适用：Boss / HR 要求“把 X 的简历/CV 发给 Y”、“把 X 的笔试答案/附件发给 Y”、“把某个本地文件通过飞书发给 Y”。这是**外部文件发送**，必须走 §2.3.1 propose-confirm。

#### 4.16.1 发送候选人 CV

触发词：`发简历` / `发 CV` / `把 X 简历发给 HR/老板/Polaris/面试官/ou_xxx`。

流程：

1. 先解析唯一候选人；如姓名命中多名，STOP 问清楚。
2. 只读查询 `talent.cmd_show --talent-id <tid> --json`，确认 `cv_path` 存在。
3. propose 单条 atomic CLI，等下一轮 confirm：

```bash
uv run python3 scripts/talent/cmd_send_cv_to_feishu.py \
  --talent-id <tid> --to <boss|hr|polaris|interviewer-master|interviewer-bachelor|interviewer-cpp>
```

如果用户给的是显式 open_id：

```bash
uv run python3 scripts/talent/cmd_send_cv_to_feishu.py \
  --talent-id <tid> --open-id ou_xxx
```

propose 文案必须包含：候选人姓名 + `talent_id`、文件类型 `CV`、目标角色 / open_id、文件名（来自 dry-run 或 `cv_path` basename）。

#### 4.16.2 发送候选人最新笔试答案附件

触发词：`发笔试答案` / `发笔试附件` / `把 X 的笔试提交发给 Y`。

流程：

1. 先解析唯一候选人。
2. 可先跑 dry-run 确认最新 `context='exam'` inbound 是否已有保存附件：

```bash
uv run python3 scripts/exam/cmd_send_submission_to_feishu.py \
  --talent-id <tid> --to <role> --dry-run --json
```

3. propose 正式命令，等下一轮 confirm：

```bash
uv run python3 scripts/exam/cmd_send_submission_to_feishu.py \
  --talent-id <tid> --to <boss|hr|polaris|interviewer-master|interviewer-bachelor|interviewer-cpp>
```

显式 open_id 同理加 `--open-id ou_xxx`。propose 文案必须包含：候选人、最新笔试邮件时间、附件文件名、目标。

#### 4.16.3 发送任意本地文件

触发词：`把 /path/to/file 发给 HR` / `通过飞书发这个文件`。

流程：

1. 文件路径必须来自用户明确给出或 Hermes 附件上下文；不得猜路径。
2. 先 dry-run：

```bash
uv run python3 scripts/feishu/cmd_send_file.py \
  --file <absolute-path> --to <role> --dry-run --json
```

3. propose 正式命令，等下一轮 confirm：

```bash
uv run python3 scripts/feishu/cmd_send_file.py \
  --file <absolute-path> --to <role> --title "<说明>"
```

显式 open_id 用 `--open-id ou_xxx`。如果文件不存在 / 目标不明确 / open_id 未确认，STOP。

硬规则：

- 不要把 `--to hr`、`--to boss`、`--to polaris` 和显式 `--open-id` 混淆；显式 open_id 优先，但必须在 propose 中完整回显。
- 不要发送整个候选人目录；只能发送单个明确文件。
- 不要自动群发多个候选人的 CV / 笔试答案；多候选人逐个 propose-confirm。
- 失败时如实报告 `UserInputError` / 飞书上传失败；不要换目标重试。

---

## 5. 表外的常见 intent

下面这几种**不需要 chain，也不需要决策**，直接照做：

> ⚠️ **intent 命名口径**：本表用的 intent 名以 [`prompts/inbox_general.json::valid_intents`](../scripts/prompts/inbox_general.json) 为准（`confirm_interview` / `reschedule_request` / `request_online` / `defer_until_shanghai` / `question_boss` / `exam_submitted` / `thanks_fyi` / `decline_withdraw` / `other`）；§4 各小节的中文叙述里如出现 `confirm_time` / `withdraw` / `decline` / `defer_until_return` / `exam_submission` 等是行文缩写,实际 LLM 输出对应的是 `confirm_interview` / `decline_withdraw` / `defer_until_shanghai` / `exam_submitted`。
>
> v3.8 一致性修订：所有按 intent 分支的代码逻辑必须用 `valid_intents` 里的字符串字面量比较;老的 §4 chain 代码示例里 `intent='confirm_time'` 这种写法是**口语缩写,不是合法值**（agent 跑时会永远 false）,详细修订见后续 v3.8 一致性 patch。

| # | stage / 场景 | intent | 行为 |
|---|---|---|---|
| 5.1 | `NEW` | `decline_withdraw`（候选人在排面试前就主动放弃） | 走 §4.13（NEW 分支：`talent.cmd_delete` 不发拒信 / 暂不处理） |
| 5.2 | `NEW` | `question_boss` / `thanks_fyi` / `other`（候选人主动来信打招呼 / 询问） | `feishu.cmd_notify --severity info "NEW 阶段候选人主动联系（agent 未起任何流程）"`，附 LLM `summary` + 邮件原文节选；**不**自动回信，等老板拍板 |
| 5.3 | `EXAM_SENT` | `exam_submitted` | `exam.cmd_exam_ai_review --feishu --save-event`（评分卡推飞书 + 写 talent_events + v3.8.1 起：自动推 stage→`EXAM_REVIEWED`，进入老板待拍板状态） |
| 5.4 | `EXAM_SENT` | 3 天无回复 | **agent 不动**，cron `auto_reject.cmd_scan_exam_timeout` 自动处理（v3.8.3 起：发 `rejection_exam_no_reply` 拒信 + `talent.cmd_delete` 物理删档；详见 INCIDENT_RULES.md §15） |
| 5.5 | `EXAM_SENT` | `decline_withdraw` | 走 §4.13（EXAM 分支：`outbound.cmd_send rejection_generic` + `talent.cmd_update --stage EXAM_REJECT_KEEP` / `talent.cmd_delete`） |
| 5.6 | `EXAM_SENT` | 其他（`question_boss` / `reschedule_request` / `defer_until_shanghai` / `request_online`） | `feishu.cmd_notify --severity warn "笔试期间候选人来信（非提交）"`；**不**自动回，由老板决定（延期就走 §4.9 force-jump 给个新阈值或 §4.4 暂缓） |
| 5.7 | `EXAM_REVIEWED` | `decline_withdraw` | 走 §4.13（EXAM 分支） |
| 5.8 | `EXAM_REVIEWED` | 其他（候选人追问 / 询问） | `feishu.cmd_notify --severity warn "等拍板期间候选人来信"`，附 LLM 总结；**不**自动回。**注**：此 stage 老板长期不拍板时由 cron `cmd_review_reminder` 每 3h 催一次（v3.8）。 |
| 5.9 | `ROUND{N}_SCHEDULED` | `reschedule_request` / `request_online` | **不跑 chain**，按 §4.3.1 推飞书；老板回新时间后由 agent 触发 §4.3.3 |
| 5.9b | `ROUND{N}_SCHEDULING` | `confirm_interview`（候选人 confirm 时间）| **不直接触发建日历**（v3.8.4 分权修订）。`inbox.cmd_analyze` 内对 (intent, stage) 后置 override `need_boss_action=true` → 推 warn 卡给老板（含候选人 confirm 的时间 + 邮件原文摘要）。老板**在飞书**对 bot 回"OK 建日历" / "X 时间确认了" / "给 X 安排日历" 等显式指令后才走 §4.2 chain。老板若回"改成 X 时间" → 走 §4.3.3 改期；老板若回"等等再说" → STOP 不动。 |
| 5.10 | `ROUND{N}_SCHEDULED` | `confirm_interview`（重复确认） | `feishu.cmd_notify --severity info "重复确认"`，不改 DB |
| 5.11 | `ROUND{N}_SCHEDULED` / `_SCHEDULING` | `decline_withdraw` | 走 §4.13（ROUND-stage 分支：`interview.cmd_result --result reject_*`） |
| 5.12 | `ROUND{N}_SCHEDULING` | `reschedule_request` | 没建过日历，不需要 §4.3.3 的 cal_del 步骤；按 §4.3.1 推飞书让老板拍新时间，回话后只跑 send + update |
| 5.13 | `WAIT_RETURN` | `decline_withdraw` | 走 §4.13（WAIT_RETURN 分支：`outbound rejection_generic` + `--force` 推到留池 / 删档） |
| 5.14 | `WAIT_RETURN` | 其他（候选人来信） | 走 §4.7（推飞书；**不**自动恢复 stage，需要老板触发 §4.12） |
| 5.15 | `POST_OFFER_FOLLOWUP` | `decline_withdraw`（候选人拒 offer） | 走 §4.13（POST_OFFER_FOLLOWUP 分支：**留人才池 / 删除二选一**——v3.8.1 撤销 v3.8 chain 层"禁删档"收紧,改由代码层 hard guard 兜底,事故源 [INCIDENT_RULES.md §13](INCIDENT_RULES.md#13-2026-05-10--3-人误删事故复发doc-修订对运行中-agent-失效)；v3.8.2 起留池路径推到 `OFFER_DECLINED_KEEP`,不再借用 `ROUND2_DONE_REJECT_KEEP`,见 [INCIDENT_RULES.md §14](INCIDENT_RULES.md#14-2026-05-11--拒-offer-留池语义混桶offer_declined_keep-拆出)）。**触发**：候选人邮件 OR 老板飞书转告（如"X 拒了 offer"）都可进入,chain 一致 |
| 5.16 | `POST_OFFER_FOLLOWUP` | 任意非 `decline_withdraw` 的 inbound（chat 模式） | `inbox.cmd_analyze` 走 `prompts/post_offer_followup.json` 自动生成 `ai_payload.draft`（**不含**具体薪资/入职日期数字）+ 推一条**纯文本飞书消息**给老板看（含 draft 摘要 + email_id）；老板**在飞书直接回 bot** "用 cached draft 给 X 回"/"OK 发"等指令（Hermes Gateway 接到后路由给 agent）才走 §4.8 chain（**不是**飞书按钮——飞书没按钮但**有入站通道**）。agent **绝不**自动回信 |
| 5.17 | `POST_OFFER_FOLLOWUP` | 老板原话含「发 offer / 录用通知」+ `onboard_date`（`daily_rate` 老板未提默认 350；明说则用老板的；老板说"按谈好的"则 STOP 让老板复述具体数字） | **不**走本表 chat 路径，走 §4.10（带合同附件的正式录用邮件，全程一次）。判定不清 → STOP，**绝不**用 §4.8 cached draft 替代 §4.10（cached draft 路径不触发 `auto_attachments`，实习协议必漏发） |
| 5.18 | **任意 SCHEDULING/SCHEDULED stage**（`ROUND1_*` / `ROUND2_*` / `EXAM_*`） | `question_boss`（候选人非决策性咨询：问公司 / 流程 / 待遇 / 着装等） | **v3.8 期望行为**：扩展 `inbox.cmd_analyze` 让此场景也走 cached draft 模式（类似 §4.8）——LLM 起一份咨询回复 draft 写入 `ai_payload.draft`,推飞书给老板看,老板**在飞书**直接回 bot 批准后走 §4.8 chain 一键发。**当前实现限制**：`prompts/inbox_general.json::has_draft=false`,本场景 LLM 暂**不**起 draft;agent 退化为 `feishu.cmd_notify --severity info "候选人非决策咨询"`,等老板手动决定。代码侧改造（让本场景走 has_draft prompt）已开 ticket,见 maintainer 备注。 |
| 5.19 | `ONBOARDED` | 任意 inbound | **v3.8.4 起 `inbox.cmd_scan` 不再扫此 stage**——这一行只在历史邮件（stage 变 ONBOARDED 之前已经入库的 inbound）被 cmd_analyze 重跑时才适用,行为同旧版：`feishu.cmd_notify --severity info "已入职候选人来信"`，附 LLM 总结；**不**自动起 draft，**不**改 stage。新进 inbound 直接被扫描层 short-circuit 掉,不会到 cmd_analyze。如果老板未来想"重新激活"该候选人沟通,必须先 `talent.cmd_update --stage POST_OFFER_FOLLOWUP --force` 或类似显式动作。 |
| 5.20 | `EXAM_REJECT_KEEP` / `ROUND2_DONE_REJECT_KEEP` | 任意 inbound | 不写。仅当 `urgency=high` 才 `feishu.cmd_notify --severity info`。两个 keep-pool 叶子态语义不同（拒过笔试 / 二面没过）但邮件处理策略一致：等老板拍板是否再激活，agent 不主动起 chain。**注**：候选人没拒过我们,他们仍可能主动回头追问 / 重新投递,所以仍要扫邮件让老板看到。 |
| 5.20b | `OFFER_DECLINED_KEEP` | 任意 inbound | **v3.8.4 起 `inbox.cmd_scan` 不再扫此 stage**——候选人已明确拒了 offer,留池只是"将来万一回心转意可被 force-jump 反向激活",在 force-jump 发生之前 agent 不再扫他的邮件 / 不打扰老板。如老板未来想"重新激活"必须先 §4.9 force-jump 回 `POST_OFFER_FOLLOWUP`（"候选人回心转意,他刚发邮件说想重新谈"——老板把候选人邮件原文转告 agent + 跑 force-jump 即可,不依赖 `inbox.cmd_scan` 主动拉到这封邮件）。 |
| 5.21 | 任意 stage | LLM `confidence < 0.6` **或** `intent=other` | `feishu.cmd_notify --severity warn "需要人工分类"`（**优先级最高**：先匹配本行,绕过下一行的 normal 卡;避免低 confidence 输出被错误归类成 need_boss_action 从而走 normal 卡，丢掉警示信息） |
| 5.22 | 任意 stage | `need_boss_action=true` 但 intent 不在 §4 / §5 表 | `feishu.cmd_notify --severity normal "未分类的需老板介入邮件"`（仅当 5.21 不命中时才走本行） |
| 5.23 | 老板 / HR 飞书主动提出的 workflow 请求 | §4 / §5 没有精确场景 | 进入 §3.5 uncovered workflow planner：先只读查事实 + 拆 atomic CLI 草案 + 推计划卡；人类逐步确认前**不执行任何写动作**。本行只处理"人主动给 bot 下操作请求"；候选人 inbound 邮件仍按 5.21 / 5.22。 |
| 5.24 | 老板 / HR 飞书主动提出“发 CV / 发简历 / 发笔试答案 / 发附件 / 通过飞书发文件” | 文件发送请求 | 走 §4.16。先只读 / dry-run 确认候选人、文件名和目标，再 propose 单条 `talent.cmd_send_cv_to_feishu` / `exam.cmd_send_submission_to_feishu` / `feishu.cmd_send_file`，下一轮 confirm 后执行。 |
| 5.24 | 老板 / HR 飞书主动提出“发 CV / 发简历 / 发笔试答案 / 发附件 / 通过飞书发文件” | 文件发送请求 | 走 §4.16。先只读 / dry-run 确认候选人、文件名和目标，再 propose 单条 `talent.cmd_send_cv_to_feishu` / `exam.cmd_send_submission_to_feishu` / `feishu.cmd_send_file`，下一轮 confirm 后执行。 |

---

## 6. 失败处理速查

| 失败位置 | agent 应做 |
|---|---|
| 第一步 `outbound.cmd_send` 失败 | `feishu.cmd_notify --severity error "邮件发送失败"`（无副作用） |
| `cmd_send` 成功但 `talent.cmd_update` 失败 | `severity=critical "邮件已发但状态未更新"` + 老板手动 update |
| **拒信已发但 `talent.cmd_delete` 失败**（v3.8.1 新增；§4.13 EXAM/WAIT_RETURN/POST_OFFER 删档手工 chain 第二步失败） | `severity=critical "拒信已发但删档失败"`：**禁止重发拒信**（候选人已收到一份）；老板手动 `talent.cmd_delete --talent-id X --confirm-delete-talent X --reason ...` 补执行；候选人对拒信的回信（如有）按 §5.20 处理 |
| **拒信已发但 `interview.cmd_result --skip-email` 失败**（v3.8.1 新增；§4.14 no-show 删档 chain 第二步失败） | `severity=critical "拒信已发但结果未落库"`：**禁止重发拒信**;老板手动 `interview.cmd_result --talent-id X --round N --result reject_delete --confirm-reject-delete X --skip-email` 补执行 |
| `feishu.cmd_calendar_create` 成功但 `event_id` 回写失败 | `severity=critical "日历已建但 event_id 未回写"` + 老板手动 update |
| `inbox.cmd_analyze` LLM 限流 | `ai_*` 留空，下一轮 cron 重试 |
| 任意 CLI 抛 `UserInputError`（含 v3.8.1 hard guard 拒绝） | **不**推飞书（人类输入错），stderr + 终止；agent 应检查是否漏传 `--confirm-delete-talent` / `--confirm-reject-delete` |

`lib.cli_wrapper.run_with_self_verify` 已经帮每个 atomic CLI 做"crash → 飞书告警"，agent 只需关心 `chain_result["ok"]` / `chain_result["failed_step"]`。

---

## 维护

- 新增 atomic CLI → 在 §3 加一行；进入 agent 路径再在 §4 / §5 加规则。
- 改 intent 集合 → 改 `prompts/inbox_general.json::valid_intents` + `inbox/analyzer.py::_NEED_BOSS_INTENTS` + 本文 §5。
- chain 改动 → 同步改 `tests/test_agent_chain.py`，CI 红就是没改全。
