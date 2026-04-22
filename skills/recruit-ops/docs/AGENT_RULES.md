# Agent 决策规则手册（v3.5）

> 本文是 v3.5 重构的核心补充：所有「业务剧本」都已退化为 atomic CLI + agent rules。
> 一句话：**动作 = atomic CLI；判断 / 编排 = agent（LLM 拿规则推下一步）**。
>
> 本手册写给执行 `recruit-ops` 的 agent（Hermes 网关后面的 LLM 编排器），
> 同时也供老板 / 工程同学审阅"agent 在某个 stage 看到某个 intent 时究竟会做什么"。

---

## 目录

1. [架构定位](#1-架构定位)
2. [核心原则](#2-核心原则)
3. [入站邮件 → agent 决策矩阵](#3-入站邮件--agent-决策矩阵)
4. [可用 atomic CLI 速查](#4-可用-atomic-cli-速查)
5. [典型 chain 范式](#5-典型-chain-范式)
6. [失败处理与回退](#6-失败处理与回退)
7. [安全护栏](#7-安全护栏)

---

## 1. 架构定位

```
              ┌─────────────────────────────────────────────────────────┐
              │ cron / 飞书事件                                         │
              └────────────────────┬────────────────────────────────────┘
                                   │
                                   ▼
              ┌─────────────────────────────────────────────────────────┐
              │ inbox.cmd_scan      （IMAP → talent_emails，仅写入站行） │
              │ inbox.cmd_analyze   （LLM 分类，stage-aware；写 ai_*）  │
              └────────────────────┬────────────────────────────────────┘
                                   │ (intent + summary + need_boss_action)
                                   ▼
              ┌─────────────────────────────────────────────────────────┐
              │ Agent（本文档定义其规则）                               │
              │   1. 看 intent + 当前 stage + ai_payload                │
              │   2. 命中规则 → 选 chain：[Step(...), Step(...)]        │
              │   3. lib.run_chain 进程内串原子 CLI                     │
              │   4. 任意失败 → 推飞书告警 + 停手                        │
              └────────────────────┬────────────────────────────────────┘
                                   │
                                   ▼
              ┌─────────────────────────────────────────────────────────┐
              │ atomic CLIs（v3.3 + v3.5 增量）                         │
              │   talent.cmd_update / cmd_delete                        │
              │   outbound.cmd_send（含 --use-cached-draft）            │
              │   feishu.cmd_calendar_create / _delete / cmd_notify     │
              │   exam.cmd_exam_result                                  │
              │   interview.cmd_result（仅 result，不含 confirm/defer） │
              └─────────────────────────────────────────────────────────┘
```

## 2. 核心原则

| 原则 | 含义 |
|------|------|
| **单一意图 LLM** | 入站邮件意图分类只走 `inbox.cmd_analyze`（背后 `inbox/analyzer.py`，prompts/inbox_general 与 prompts/post_offer_followup 二选一）。**不再有 exam/llm_analyzer / followup_analyzer**。 |
| **单一扫描入口** | 所有候选人新邮件都由 `inbox.cmd_scan` 写入 `talent_emails`。无论 stage 是 EXAM_SENT 还是 POST_OFFER_FOLLOWUP，**不再有 daily_exam_review / followup_scanner**。 |
| **写动作必须 atomic** | 只通过 `talent.cmd_update` / `talent.cmd_delete` / `outbound.cmd_send` / `feishu.cmd_calendar_*` / `feishu.cmd_notify` 改世界。每个原子 CLI 自带 self-verify + 飞书告警包装。 |
| **agent 不藏状态** | 所有判断材料只能从：`talent_emails` 行、`talents` 行、`talent_events` 审计、`ai_payload` 中取。**禁止**把 LLM 推理结果暂存在内存或临时文件里。 |
| **chain 全失败短路** | 一个 chain 中任意一步失败：立即停止后续步骤，把错误 + 已成功步骤的 stdout 推飞书。**不试图自动 rollback**——发邮件 / 删日历不可逆。 |
| **拿不准就推飞书** | 任何规则未覆盖、stage/intent 组合超出表格、LLM `confidence < 0.6` → 不做任何写动作，调 `feishu.cmd_notify --severity warn` 让老板手动处理。 |

## 3. 入站邮件 → agent 决策矩阵

`inbox.cmd_analyze` 输出（写在 `talent_emails.ai_payload`）：
```
{ "intent": "<one of intents>",
  "summary": "<≤30 字>",
  "urgency": "low|normal|high",
  "need_boss_action": true|false,
  "details": { ... },          # intent-specific
  "draft": "..."               # 仅 POST_OFFER_FOLLOWUP（v3.6 起 OFFER_HANDOFF 已删）
}
```

### 3.1 Stage × intent 决策表（按 stage 顺序读，11 stage 全覆盖，v3.6）

> **「写动作」列里的 `→` 表示 chain 顺序，**字段名直接对得上 atomic CLI 的参数。
> chain 第一行 = §5 范式（如有），可直接搬。

| 当前 stage | 触发场景 | 写动作（agent chain） | 飞书侧 |
|------------|---------|----------------------|--------|
| **NEW** | 老板把简历入库后给了一个一面时间 | §5.1 全套：`outbound.cmd_send --template round1_invite` → `talent.cmd_update --stage ROUND1_SCHEDULING --set round1_time=… --set round1_invite_sent_at={send.sent_at} --set round1_confirm_status=PENDING` | 发送成功后推 info 卡 |
| **NEW** | **HR** 把候选人录入后**直接给一面时间**（已 ingest CV） | **§5.11 全套**（v3.5.7，学历感知派单）：`intake.cmd_route_interviewer` → `outbound.cmd_send --template round1_invite` → `feishu.cmd_calendar_create --round 1 --duration-minutes 30 --extra-attendee {route.interviewer_open_ids[*]}` → `talent.cmd_update --stage ROUND1_SCHEDULED --set round1_time=… --set round1_confirm_status=CONFIRMED --set round1_calendar_event_id={cal.event_id} --set round1_invite_sent_at={send.sent_at}` → `feishu.cmd_notify --to interviewer-{role}` ×N → `feishu.cmd_notify --to boss --severity info`。**ambiguous=true 或 config_error=true 时不要继续**，转 ASK_HR：`feishu.cmd_notify --to hr --title "一面派单需手动指派"`，等 HR 回复后重启 chain。 | route 输出消费 + 通知面试官 + boss 同步 |
| **NEW** | 候选人主动来信（intake 还没启动） | 不写。`feishu.cmd_notify --severity warn --title "NEW 阶段收到候选人邮件"` | 必推 |
| **ROUND1_SCHEDULING** | `confirm_time` | `talent.cmd_update --stage ROUND1_SCHEDULED --set round1_confirm_status=CONFIRMED` → `feishu.cmd_calendar_create --round 1 --time {round1_time}` → `talent.cmd_update --set round1_calendar_event_id={cal.event_id}`（§5.2） | 创建日历后通知 |
| **ROUND1_SCHEDULING** | `reschedule_request` | 老板还没建日历，无须删日历。`outbound.cmd_send --template reschedule --vars round_label=一面 …` → `talent.cmd_update --set round1_time=新时间 --set round1_confirm_status=PENDING` | 必推 |
| **ROUND1_SCHEDULING** | `defer_until_return` | `outbound.cmd_send --template defer --vars round_label=一面` → `talent.cmd_update --stage WAIT_RETURN --set wait_return_round=1 --set round1_time=__NULL__`（§5.4） | 必推「已暂缓」卡 |
| **ROUND1_SCHEDULED** | `reschedule_request` | §5.3 全套：`feishu.cmd_calendar_delete --event-id {round1_calendar_event_id}` → `outbound.cmd_send --template reschedule` → `talent.cmd_update --stage ROUND1_SCHEDULING --set …` | 必推 |
| **ROUND1_SCHEDULED** | `defer_until_return` | §5.4 + 删旧日历前置一步：`feishu.cmd_calendar_delete` → `outbound.cmd_send --template defer` → `talent.cmd_update --stage WAIT_RETURN --set wait_return_round=1 --set round1_time=__NULL__ --set round1_calendar_event_id=__NULL__` | 必推 |
| **ROUND1_SCHEDULED** | `confirm_time`（重复确认） | 不写。`feishu.cmd_notify --severity info --title "候选人重复确认一面时间"` | 必推 |
| **EXAM_SENT** | `exam_submission` | `exam.cmd_exam_ai_review --talent-id … --feishu --save-event`（评分卡推飞书；不改 stage）→ 老板基于卡片决定 pass / reject_keep（见下两行） | 评分卡片 |
| **EXAM_SENT** | `request_online`（请求改线上） | `feishu.cmd_notify --severity warn --title "候选人请求线上笔试"` | 必推 |
| **EXAM_SENT** | 其他 `logistics` | `feishu.cmd_notify --severity normal` 推老板手动判断 | 必推 |
| **EXAM_SENT** | 3 天内无回复 | **agent 不动手**。cron 的 `auto_reject.cmd_scan_exam_timeout` 会自动发拒信 + 推 stage 到 `EXAM_REJECT_KEEP`（**留池不删档**，v3.5.11 起）+ 飞书事后通知。 | （cron 自己推） |
| **EXAM_REVIEWED** | 老板拍板「过」 + 给二面时间 | §5.5：`outbound.cmd_send --template round2_invite` → `talent.cmd_update --stage ROUND2_SCHEDULING --set round2_time=… --set round2_invite_sent_at={send.sent_at} --set round2_confirm_status=PENDING` | 发送成功后推 info |
| **EXAM_REVIEWED** | 老板拍板「不过」 | §5.7：`outbound.cmd_send --template rejection_generic` → `talent.cmd_update --stage EXAM_REJECT_KEEP --reason "exam reject keep"` | 必推 info「已拒（保留池）」 |
| **EXAM_REVIEWED** | 候选人来信问结果 | `feishu.cmd_notify --severity normal --title "EXAM_REVIEWED 候选人催结果"` | 必推 |
| **WAIT_RETURN** | `return_to_shanghai` / 其他主动联系 | §5.8：**不自动恢复 stage**。`feishu.cmd_notify --severity warn --title "WAIT_RETURN 候选人主动联系"` 附「老板可执行」chain：①直接 `talent.cmd_update --stage ROUND{N}_SCHEDULING --force --reason "candidate returned"` 让 agent 接力安排 ②或 `outbound.cmd_send --template round{N}_invite` 起新 chain | 必推 warn |
| **ROUND2_SCHEDULING** | `confirm_time` | 与 ROUND1_SCHEDULING `confirm_time` 同构（§5.2，把 `1` 换成 `2`） | 同 ROUND1 |
| **ROUND2_SCHEDULING** | `reschedule_request` / `defer_until_return` | 与 ROUND1_SCHEDULING 同构（round=2） | 同上 |
| **ROUND2_SCHEDULED** | `reschedule_request` | §5.3 全套，把 `round1` 换成 `round2`、stage 回到 `ROUND2_SCHEDULING` | 必推 |
| **ROUND2_SCHEDULED** | `defer_until_return` / `defer_until_shanghai` | §5.4 + 删旧日历前置（round=2） | 必推 |
| **POST_OFFER_FOLLOWUP** | 任意 inbound | 由 `prompts/post_offer_followup.json` 自带 draft → `inbox.cmd_analyze` 推飞书卡（卡里带"一键发"按钮）→ 老板按按钮触发 §5.6 chain。v3.6 起此阶段涵盖原 OFFER_HANDOFF 语义（HR 发 offer、谈入职日 / 薪资）。 | `inbox.cmd_analyze` 自己推 |
| **`ROUND2_DONE_REJECT_KEEP` / `EXAM_REJECT_KEEP`**（任何终态） | 任意 inbound | **不写任何动作**。`talent_emails.status=dismissed`（cmd_analyze 后 agent 标记），仅当 LLM 判定 `urgency=high` 时才 `feishu.cmd_notify --severity info --title "终态候选人来信"` | 视 urgency |
| **任何 stage** | `need_boss_action=true` 但 intent 不在表中 | `feishu.cmd_notify --severity normal --title "未分类的需老板介入邮件" --body "{intent}: {summary}\n\n{body_excerpt}"` | 必推 |
| **任何 stage** | `confidence < 0.6` 或 intent 是 `unknown` | `feishu.cmd_notify --severity warn --title "需要人工分类" --body "talent={tid} email={email_id}\nintent={intent} confidence={conf}\n\n{body_excerpt}"` | 必推 |

### 3.2 stage 不对称设计（**这两条不是 bug，是有意为之，agent 不要试图"补齐对称"**）

| 不对称点 | 原因 |
|---------|------|
| 一面被拒**没有**对应 stage（直接物理删除） | 一面被拒一律删档：信号弱、留池子价值低。`interview.cmd_result --round 1 --result reject_delete` 直接发拒信 + `talent_db.delete_talent()`。v3.6 起 `ROUND1_DONE_REJECT_DELETE` 这个"占位 stage"已删，线上本来就是 0 行。如果老板想留，走 `interview.cmd_result --round 1 --result pass`。 |
| `EXAM_REJECT_KEEP` 存在但**没有** `EXAM_REJECT_DELETE` | 笔试被拒一律保留：能写出代码（或者收到题目）就有再用价值。两条路径殊途同归：**老板拍板**（人交了卷但不过）走 `outbound.cmd_send --template rejection_generic` + `talent.cmd_update --stage EXAM_REJECT_KEEP`（§5.7）；**系统判定**（3 天不交卷的失约）走 `auto_reject.cmd_scan_exam_timeout` —— 它现在也推到 `EXAM_REJECT_KEEP`（v3.5.11 起；之前是物理删档，2026-04-22 cron 事故触发改设计，详见 lib/migrations/20260422_v3511_*.sql）。 |
| 二面被拒_delete 也**没有**对应 stage（v3.6 起） | 与一面同理，`interview.cmd_result --round 2 --result reject_delete` 发拒信 + 物理删除。保留池走 `reject_keep` → `ROUND2_DONE_REJECT_KEEP`。 |
| 没有 `POST_OFFER_FOLLOWUP_DONE` 终态 | 入职前一切都还可能反悔。结案靠老板手动 `talent.cmd_delete --reason "offer accepted, onboarded"` 或让候选人停在 `POST_OFFER_FOLLOWUP` + 注在审计事件里；不引入额外 stage。 |

### 3.3 老板直发指令的 override（**最高优先级，先看这里再看 §3.1**）

§3.1 矩阵覆盖的是**入站邮件触发的 agent 自动决策**。当**老板直接通过飞书下指令**时，下面这条 override **优先于 §3.1 任何规则**：

| 老板指令模式 | 唯一允许的路径 | 绝对禁止 |
|--------------|---------------|---------|
| 包含「**直接跳到 X**」「**直接进 X**」「**略过 / 跳过中间步骤**」「**不要面 / 不要发邮件**」「**强制推到 X**」「**直接结束流程**」「**不走面试，直接 …**」等跨 stage 跳跃语义的请求 | **§5.9 force-jump 单步**：`talent.cmd_update --stage <target> --force --reason "boss原话: <老板原话>"`。**只有这一条**，不要做任何其他事。 | ① 拼任何 §5.1–§5.8 的「自然推进」chain ② 调 `exam.cmd_exam_result --result pass` 或 `interview.cmd_result --result pass`（这些会真发邮件给候选人）③ 编造时间 / 编造邮件 / 编造 confirm 状态去满足 atomic CLI 的必填参数 ④ 用多次 `cmd_update --set` 模拟「候选人 confirm 了」的中间状态 ⑤ 在 boss 没说面试的情况下创建任何日历 / 发任何候选人邮件 |

**如何识别「跨 stage 跳跃」意图**：

- 字面线索：老板原话里出现「直接」「跳到」「跳过」「略过」「不要 X」「强制」「跨过」「不需要 X」「直接进」「直接发 offer」「直接结束」中的**任意一个**。
- 语义线索：老板要求的目标 stage 与候选人当前 stage **跨过 ≥ 2 个自然推进步骤**（如 `EXAM_SENT → POST_OFFER_FOLLOWUP` 跨过 `EXAM_REVIEWED / ROUND2_SCHEDULING / ROUND2_SCHEDULED` 3 个 stage）。
- 反推线索：如果你正在拼 chain 但其中**某一步要你编造业务数据才能跑通**（如必须给 `--round2-time` 但老板根本没提面试），**几乎可以确定**这是个跨 stage 跳跃意图，而你选错路径了——回头看 §5.9。

**老板真实可能说的话**（每一条都走 §5.9）：

| 老板原话 | 当前 stage | 目标 stage | 命令 |
|---------|----------|----------|------|
| 「候选人A笔试通过，直接进 offer 阶段」 | `EXAM_SENT` / `EXAM_REVIEWED` | `POST_OFFER_FOLLOWUP` | `talent.cmd_update --stage POST_OFFER_FOLLOWUP --force --reason "boss原话: 笔试通过，直接进 offer 阶段，跳过二面"` |
| 「李四不用面了，直接发 offer 让 HR 跟进」 | 任意 | `POST_OFFER_FOLLOWUP` | `talent.cmd_update --stage POST_OFFER_FOLLOWUP --force --reason "boss原话: …"`（v3.6：OFFER_HANDOFF 已合并） |
| 「张三笔试不过，但保留人才池」（没有等老板看 AI 评审就拍板） | `EXAM_SENT` | `EXAM_REJECT_KEEP` | `talent.cmd_update --stage EXAM_REJECT_KEEP --force --reason "boss原话: …"` ⚠ **不**发拒信。如果老板要发拒信，他会另外说，agent 走 §5.7。 |
| 「王五不在国内了，强制暂缓二面」 | `ROUND2_SCHEDULED` | `WAIT_RETURN` | `talent.cmd_update --stage WAIT_RETURN --set wait_return_round=2 --force --reason "boss原话: …"`（注意：跨自然路径，所以用 force；但若老板让发邮件 + 删日历，走 §5.4 chain 而不是单步 force-jump） |

**模糊判定的兜底**：如果你不确定老板是想走「正常推进 + 发邮件」还是「force-jump 单步」，**stop 并问老板**：

> 「老板这是要 (a) 走正常流程：发 round2 邀请邮件 + 推到 ROUND2_SCHEDULING（chain §5.5），还是 (b) 跨 stage 跳到 X 阶段（不发邮件、不创建日历）？」

**绝不**默认按 (a) 走——因为 (a) 会发不可撤销的候选人邮件。

## 4. 可用 atomic CLI 速查

| 模块 | 写入对象 | 关键参数 |
|------|---------|----------|
| `talent.cmd_update` | `talents` 行 + `talent_events` 审计 | `--talent-id` `--stage` `--set FIELD=VALUE`（可重复，支持 `__NULL__` / `__NOW__`）`--force` `--reason` `--json` |
| `talent.cmd_delete` | 物理删 + 归档 | `--talent-id` `--reason` |
| `outbound.cmd_send` | 发邮件 + 写 `talent_emails(direction='outbound')` | `--talent-id` `--template T --vars K=V` 或 `--subject --body-file` 或 `--use-cached-draft EMAIL_ID`；`--in-reply-to` / `--cc` / `--attach FILE`（附件，可重复，单文件 ≤ 20MB）/ `--json` |
| `inbox.cmd_scan` | IMAP → `talent_emails(direction='inbound', analyzed_at=NULL)`；同步把候选人邮件附件按 `talent_emails.context` 分流落到 `data/candidates/<tid>/exam_answer/em_<eid>/`（context='exam'）或 `data/candidates/<tid>/email/em_<eid>/`（其他），元数据写到 `talent_emails.attachments` JSONB（v3.5.8） | `--since YYYY-MM-DD` |
| `inbox.cmd_analyze` | 写 `ai_*` 字段 + 推飞书 | `--limit N` |
| `feishu.cmd_calendar_create` | 飞书日历 event | `--talent-id --time --round --candidate-email --candidate-name --json`；v3.5.7 新增 `--extra-attendee OPEN_ID`（可重复，把面试官加进 attendees）`--duration-minutes N`（默认 60；§5.11 一面用 30） |
| `feishu.cmd_calendar_delete` | 删除飞书日历 | `--event-id --reason` |
| `feishu.cmd_notify` | 推一张飞书消息 / 卡片 | `--title --body --severity {info,warn,error,critical} --to {boss,hr,interviewer-master,interviewer-bachelor,interviewer-cpp}`（v3.5.7 新增 3 个 interviewer-* 角色，open_id 来自 `lib.config['feishu']['interviewer_*_open_id']`） |
| `intake.cmd_route_interviewer` | **零副作用**纯查询：根据 `talents.{education,has_cpp}` + 配置算出该派给哪/些面试官 | `--talent-id --json` → `{interviewer_roles, interviewer_open_ids, ambiguous, ambiguous_reason, config_error, config_error_detail}`。v3.5.7 §5.11 chain 必跑的第一步；ambiguous=true 时 chain 必须转 ASK_HR。 |
| `exam.cmd_exam_result` | 推 stage（`pass`→ROUND2_SCHEDULING；`reject_*`→相应终态）+ 发拒信 | `--talent-id --result {pass,reject_keep,reject_delete} --round2-time` |
| `exam.cmd_exam_ai_review` | 仅评审，不写候选人字段 | `--talent-id [--rerun --feishu --save-event]` |
| `interview.cmd_result` | 一/二面结果 → 下一 stage | `--talent-id --round {1,2} --result {pass,pass_direct,reject_keep,reject_delete}` |
| `auto_reject.cmd_scan_exam_timeout` | EXAM_SENT 超时 → 拒+留池 EXAM_REJECT_KEEP | `--auto --threshold-days 3` |

> 注意：`auto_reject.cmd_scan_exam_timeout` 也是 atomic CLI（一次性扫描 + 原子拒+留池），
> 但**只跑在 cron 里**，agent 不要在响应单封邮件时调它。

## 5. 典型 chain 范式

`lib.run_chain` 让 agent 把多个 atomic CLI 串起来；前一步 `--json` 输出可作为后一步占位符
（语法：`{step_name.field}`）。

> **本节列的 chain（§5.1–§5.10）都被 `scripts/tests/test_agent_chain.py` 端到端回归**。
> agent 在线上务必**照样调**——参数名 / `--set` 字段 / 占位符传递都已经被测试钉死，
> 偏离任何一条都会导致 CI 红灯（也就意味着 self-verify 在生产侧会反复推飞书告警）。
> 修改 chain 必须同步改对应的测试用例。

### 5.1 安排一面（`outbound.cmd_send` → `talent.cmd_update`）

| 触发场景 | 老板在飞书里给候选人定了一个 round1 时间。 |
|---------|-------------------------------------------|
| 关键字段 | `round1_time` / `round1_invite_sent_at` / `round1_confirm_status=PENDING` |
| 退出条件 | stage = `ROUND1_SCHEDULING`；候选人邮箱已发 round1_invite |
| 测试源点 | `tests/test_agent_chain.py::TestRound1ScheduleChain` |

```python
from lib.run_chain import Step, run_chain

run_chain([
    Step("send", "outbound.cmd_send", args=[
        "--talent-id", tid,
        "--template", "round1_invite",
        "--vars",
        "round1_time={}".format(round1_time),
        "position_suffix={}".format(position_suffix),
        "location={}".format(office_location),
    ]),
    Step("update", "talent.cmd_update", args=[
        "--talent-id", tid,
        "--stage", "ROUND1_SCHEDULING",
        "--set", "round1_time={}".format(round1_time),
        "--set", "round1_invite_sent_at={send.sent_at}",
        "--set", "round1_confirm_status=PENDING",
        "--set", "round1_calendar_event_id=__NULL__",
        "--set", "wait_return_round=__NULL__",
        "--reason", "agent: schedule round 1",
    ]),
])
```

> ⚠️ 一面发出 != 候选人已确认。**这一步过后 stage 仍是 `ROUND1_SCHEDULING`**——
> 等候选人回 `confirm_time`，agent 才走 §5.2 风格的「升级到 SCHEDULED + 建日历」chain。

### 5.2 候选人确认 → 建日历 → 回写日历 ID（`talent.cmd_update` → `feishu.cmd_calendar_create` → `talent.cmd_update`）

| 触发场景 | `inbox.cmd_analyze` 在 `ROUND{N}_SCHEDULING` 邮件上分类出 `confirm_time`。 |
|---------|-----------------------------------------------------------------------------|
| 关键字段 | stage 推到 `ROUND{N}_SCHEDULED`；`round{N}_calendar_event_id` 由 step `cal` 回填 |
| 退出条件 | 飞书日历事件创建成功，`event_id` 已写回 talents 表 |
| 测试源点 | 与 §5.1 同一文件中的覆盖（一面 `cal_create` 链，跨 round 同构） |

```python
run_chain([
    Step("u1", "talent.cmd_update", args=[
        "--talent-id", tid,
        "--stage", "ROUND2_SCHEDULED",
        "--set", "round2_confirm_status=CONFIRMED",
    ]),
    Step("cal", "feishu.cmd_calendar_create", args=[
        "--talent-id", tid, "--round", "2",
        "--time", round2_time,
        "--candidate-email", email,
        "--candidate-name", name,
    ]),
    Step("u2", "talent.cmd_update", args=[
        "--talent-id", tid,
        "--set", "round2_calendar_event_id={cal.event_id}",
    ]),
])
```

### 5.3 候选人改期（`feishu.cmd_calendar_delete` → `outbound.cmd_send` → `talent.cmd_update`）

| 触发场景 | 已经 `ROUND{N}_SCHEDULED`、有日历事件的候选人来信要求挪时间，老板给了新时间。 |
|---------|--------------------------------------------------------------------------------|
| 关键字段 | 删掉旧 `round{N}_calendar_event_id`；`round{N}_time` 改为新时间；`confirm_status` 回到 `PENDING` |
| 退出条件 | 旧日历事件已删；候选人收到改期邮件；候选人需要再次回 `confirm_time` 走 §5.2 |
| 测试源点 | `tests/test_agent_chain.py::TestRound1RescheduleChain` |

```python
run_chain([
    Step("cal_del", "feishu.cmd_calendar_delete", args=[
        "--event-id", round1_event_id,
        "--reason", "候选人改期",
    ]),
    Step("send", "outbound.cmd_send", args=[
        "--talent-id", tid,
        "--template", "reschedule",
        "--vars",
        "round_label=一面",
        "old_time={}".format(old_time),
        "new_time={}".format(new_time),
        "location={}".format(office_location),
    ]),
    Step("update", "talent.cmd_update", args=[
        "--talent-id", tid,
        "--stage", "ROUND1_SCHEDULING",       # 回到 SCHEDULING
        "--set", "round1_time={}".format(new_time),
        "--set", "round1_confirm_status=PENDING",
        "--set", "round1_calendar_event_id=__NULL__",
        "--set", "round1_invite_sent_at={send.sent_at}",
        "--reason", "candidate reschedule confirmed slot",
    ]),
])
```

> ⚠️ **必须先删旧日历再发新时间邮件**。顺序反了会出现「候选人收到新时间但旧日历还在」的不一致。

### 5.4 候选人在国外暂缓（`outbound.cmd_send` → `talent.cmd_update`）

| 触发场景 | 已 `ROUND{N}_SCHEDULED` 的候选人来信说在国外/不在上海，希望回来再约。 |
|---------|------------------------------------------------------------------------|
| 关键字段 | stage → `WAIT_RETURN`；`wait_return_round=N`；`round{N}_time` / `_calendar_event_id` 清空 |
| 退出条件 | 候选人收到「暂缓」告知；DB 里 `WAIT_RETURN`；候选人下次主动来信由老板手动恢复（见 §3 备注） |
| 测试源点 | `tests/test_agent_chain.py::TestDeferUntilReturnChain` |

```python
run_chain([
    Step("send", "outbound.cmd_send", args=[
        "--talent-id", tid,
        "--template", "defer",
        "--vars", "round_label=一面",
    ]),
    Step("update", "talent.cmd_update", args=[
        "--talent-id", tid,
        "--stage", "WAIT_RETURN",
        "--set", "wait_return_round=1",
        "--set", "round1_time=__NULL__",
        "--set", "round1_calendar_event_id=__NULL__",
        "--reason", "candidate defer until return to shanghai",
    ]),
])
```

> 如果候选人原来还有飞书日历事件，**先**插一步 `feishu.cmd_calendar_delete`
> （结构同 §5.3 第一步），再走上面这两步。

### 5.5 笔试通过 → 直接发二面邀请（`outbound.cmd_send` → `talent.cmd_update`）

| 触发场景 | `EXAM_REVIEWED` 候选人，老板拍板通过且给了二面时间。 |
|---------|-------------------------------------------------------|
| 关键字段 | stage → `ROUND2_SCHEDULING`；`round2_time` / `round2_invite_sent_at` 写入；`round2_confirm_status=PENDING` |
| 退出条件 | 候选人收到 `round2_invite`；DB 推到 `ROUND2_SCHEDULING`；等回 `confirm_time` 再走 §5.2 |
| 测试源点 | `tests/test_agent_chain.py::TestExamPassToRound2Chain` |

```python
run_chain([
    Step("send", "outbound.cmd_send", args=[
        "--talent-id", tid,
        "--template", "round2_invite",
        "--vars",
        "round2_time={}".format(round2_time),
        "location={}".format(office_location),
    ]),
    Step("update", "talent.cmd_update", args=[
        "--talent-id", tid,
        "--stage", "ROUND2_SCHEDULING",
        "--set", "round2_time={}".format(round2_time),
        "--set", "round2_invite_sent_at={send.sent_at}",
        "--set", "round2_confirm_status=PENDING",
        "--set", "round2_calendar_event_id=__NULL__",
        "--set", "wait_return_round=__NULL__",
        "--reason", "agent: exam passed → schedule round 2",
    ]),
])
```

> 这条 chain 是**手工 agent 路径**：用于「老板已经在飞书里手动决定通过 + 拍了二面时间」的场景。
> 与之并行的全自动通道是 `exam.cmd_exam_result --result pass --round2-time ...`，会一步打包 stage + 邀请；
> 两条路径不要在同一封邮件上叠加触发。

### 5.6 老板「一键发草稿」（`outbound.cmd_send --use-cached-draft` → `feishu.cmd_notify`）

| 触发场景 | `POST_OFFER_FOLLOWUP` 候选人来信，`inbox.cmd_analyze` 已把 LLM 草稿写进 `talent_emails.ai_payload.draft`；老板在飞书卡片上点「发送」。 |
|---------|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| 关键字段 | 用 `email_id` 拉对应 `ai_payload.draft`；不改 stage |
| 退出条件 | 草稿邮件已发；老板收到一张 info 卡通知确认 |
| 测试源点 | `tests/test_agent_chain.py::TestPostOfferOneClickSendChain` |

```python
run_chain([
    Step("send", "outbound.cmd_send", args=[
        "--talent-id", tid,
        "--use-cached-draft", email_id,
    ]),
    Step("notify", "feishu.cmd_notify", args=[
        "--severity", "info",
        "--title", "已发送 Offer 跟进回复",
        "--body", "候选人 {} 的 follow-up 草稿已通过一键发送。".format(tid),
    ]),
])
```

> ⚠️ **draft 不存在时第一步必失败**（rc=2，stderr：`没有 draft 字段`），第二步因 chain 短路不会执行——
> 这个反向路径在 `TestPostOfferOneClickSendChain.test_one_click_send_fails_when_draft_missing` 里钉死。
> 老板收不到 notify ≠ bug，意味着草稿确实没准备好，agent 应改推 `--severity warn`「草稿缺失」卡片。

### 5.7 笔试不过 → 拒信保留池（`outbound.cmd_send` → `talent.cmd_update`）

| 触发场景 | `EXAM_REVIEWED` 候选人，老板基于 `cmd_exam_ai_review` 的评分卡片决定不过（但保留人才池）。 |
|---------|-----------------------------------------------------------------------------------------------|
| 关键字段 | stage → `EXAM_REJECT_KEEP`；不删档（v3.5.11 起 `auto_reject` 路径同样落到这里——殊途同归） |
| 退出条件 | 候选人收到 `rejection_generic` 模板邮件；DB 推到 `EXAM_REJECT_KEEP`；后续来信走 §3 表的「终态 inbound」规则 |
| 测试源点 | `tests/test_agent_chain.py::TestExamRejectKeepChain` |

```python
run_chain([
    Step("send", "outbound.cmd_send", args=[
        "--talent-id", tid,
        "--template", "rejection_generic",
    ]),
    Step("update", "talent.cmd_update", args=[
        "--talent-id", tid,
        "--stage", "EXAM_REJECT_KEEP",
        "--reason", "agent: exam reject keep (per boss decision)",
    ]),
])
```

> ⚠️ 与 `auto_reject.cmd_scan_exam_timeout` 的区别（v3.5.11 起两条都进 `EXAM_REJECT_KEEP`）：
> - **本 chain（§5.7）**：人交了卷、老板审完拍板"不过"——人工路径。
> - **`auto_reject` 路径**：3 天没交卷的失约——系统自动判定，cron 触发，发拒信 + 推 stage。
> 模板不同（本 chain 用 `rejection_generic`，warm 口吻 + 明示"已留人才库"；
> `auto_reject` 用 `rejection_exam_no_reply`，honest 口吻直说"3 天没交卷"），
> 终态 stage 一致（都是 `EXAM_REJECT_KEEP`）。

### 5.8 WAIT_RETURN 候选人主动联系 → 推老板（`feishu.cmd_notify`）

| 触发场景 | `WAIT_RETURN` 候选人主动来信（`return_to_shanghai` 或泛主动联系）。 |
|---------|----------------------------------------------------------------------|
| 关键字段 | **不写任何字段**。agent 不自动恢复 stage（候选人是否真的可约由老板拍） |
| 退出条件 | 老板看到飞书 warn 卡片，手动二选一：①`talent.cmd_update --stage ROUND{N}_SCHEDULING --reason "candidate returned"` 让 agent 之后接 §5.1（`WAIT_RETURN → ROUND{N}_SCHEDULING` 是 natural transition，**不需要** `--force`）②直接发新一轮邀请 |
| 测试源点 | `tests/test_agent_chain.py::TestWaitReturnPokeChain` |

```python
wait_round = cand["wait_return_round"]   # 1 or 2
run_chain([
    Step("notify", "feishu.cmd_notify", args=[
        "--severity", "warn",
        "--title", "WAIT_RETURN 候选人主动联系",
        "--body",
        "talent={tid} round={round}\nintent={intent} summary={summary}\n\n"
        "建议下一步：\n"
        "  1) talent.cmd_update --talent-id {tid} --stage ROUND{round}_SCHEDULING "
        "--reason \"candidate returned\"\n"
        "  2) outbound.cmd_send --talent-id {tid} --template round{round}_invite "
        "--vars round{round}_time=… location=…".format(
            tid=tid, round=wait_round,
            intent=ai["intent"], summary=ai["summary"]),
    ]),
])
```

> 这是**纯通知 chain**，没有写动作；目的是把决策权交给老板而不是让 agent 猜「候选人到底回来没」。
> 即便 LLM `confidence>0.9`，本规则也**不**升级为自动恢复 stage——候选人「回来了」的语义太模糊。

### 5.9 老板强制跨 stage 跳（`talent.cmd_update --force` 单步）

| 触发场景 | 老板的指令是「**直接跳到 X**」「**直接进 X 阶段**」「**略过 / 跳过中间步骤**」「**不要面 / 不要发邮件，直接结束流程**」「**强制推到 X**」等带「跳」「直接」「略过」「跳过」「强制」字样的请求。 |
|---------|----|
| 关键字段 | `current_stage` 跳到老板指定的目标 stage（**不**更新任何业务字段，如 `round*_time` / `*_invite_sent_at` / `*_confirm_status`） |
| 退出条件 | DB 里 `current_stage` = 目标 stage；`talent_events` 留下一条 `--reason` 引用老板原话的审计；**没有任何邮件 / 日历 / 飞书副作用** |
| 测试源点 | `tests/test_agent_chain.py::TestForceJumpChain`（待补——见维护指引） |

```python
run_chain([
    Step("jump", "talent.cmd_update", args=[
        "--talent-id", tid,
        "--stage", target_stage,           # 例：POST_OFFER_FOLLOWUP
        "--force",                          # 跨 stage 必须
        "--reason", "boss原话: " + boss_quote,  # 必须引用老板的原话
    ]),
])
```

**这是 v3.5.4 之后**唯一**允许跨多个 stage 跳转的路径**。规则非常硬：

1. **不要拼任何 chain 模拟自然流程**。例如老板说「候选人A笔试通过，直接进 offer 阶段」时：
   - ❌ **错**：`exam.cmd_exam_result --result pass --round2-time <编造时间>` → `talent.cmd_update --set round2_confirm_status=CONFIRMED` → `interview.cmd_result --round 2 --result pass`（这是 2026-04-21 17:06 真实事故的复现路径——会真发两封二面邀请邮件给候选人）
   - ✅ **对**：`talent.cmd_update --talent-id <id> --stage POST_OFFER_FOLLOWUP --force --reason "boss原话: 笔试通过，直接进 offer 阶段，跳过二面"`

2. **不要为了过 CLI 的 stage-gate 而伪造下游字段**。`cmd_update --force` 就是为了绕过 stage 推进的合法性检查；不要用 `--set round2_confirm_status=CONFIRMED` 之类的方式"哄"门禁。

3. **不要为了满足某个 CLI 的必填参数而编造业务数据**。如果一条 chain 让你需要编时间 / 编邮箱 / 编结果，那就是路径选错了——回到本 §5.9 用 force-jump。

4. **`--reason` 必须引用老板原话**。审计事件里要能直接看到「为什么这一跳是 boss 授权的」。

5. **必须先按 §2.2.1 propose 整条命令给老板 confirm**。老板说"直接跳到 X"是声明意图，不是预授权——agent 仍然要把 resolved 命令贴出来，等老板回 `执行` / `ok` / `好` 之类的明确确认。

> ⚠️ **典型目标 stage 速查**（不穷举，以 `core_state.py::STAGE_LABELS` 为准）：`POST_OFFER_FOLLOWUP`（直接发 offer / HR 接手）、`EXAM_REJECT_KEEP`（保留池）、`ROUND2_SCHEDULING`（直接进二面排期）、`WAIT_RETURN`（强制暂缓）。v3.6 起 `OFFER_HANDOFF` 已合并入 `POST_OFFER_FOLLOWUP`。
>
> 如果老板的目标 stage 不在 `STAGE_LABELS` 里，**stop**——`feishu.cmd_notify --severity warn --title "未知目标 stage"` 让老板澄清，不要自己猜。

### 5.10 发放 onboarding offer（POST_OFFER_FOLLOWUP 阶段，老板拍板要发）

| 触发场景 | `POST_OFFER_FOLLOWUP` 阶段，老板已和候选人在飞书谈好薪资 + 入职时间，明确说「给 X 发 onboarding offer」「发录用通知」「发入职邮件」。 |
|---------|----|
| 关键字段 | **不**改 stage（保持 `POST_OFFER_FOLLOWUP`）；**不**写任何 `talents` 业务字段。状态以 `talent_emails(template_used='onboarding_offer', direction='outbound')` 行为准。 |
| 退出条件 | 候选人收到带附件的 offer 邮件；HR 收到飞书通知（含候选人名 / 入职日期 / 薪资） |
| 测试源点 | `tests/test_agent_chain.py::TestOnboardingOfferChain` |

```python
# v3.5.10：onboarding offer 的两份附件（《示例科技实习协议》+《实习生入职信息登记表》）
# 由 email_templates.auto_attachments 自动追加，agent 不再手动 --attach。
# 文件被删 / 改名时 cmd_send 会 fail-fast 拒发；维护见 §10。

run_chain([
    Step("send", "outbound.cmd_send", args=[
        "--talent-id", tid,
        "--template", "onboarding_offer",
        "--vars",
        "position_title={}".format(position_title),
        "interview_feedback={}".format(interview_feedback),  # 老板补充 / 默认套话
        "daily_rate={}".format(daily_rate),                  # 老板确认 / 默认 350
        "onboard_date={}".format(onboard_date),              # 老板必填，YYYY-MM-DD
        "location={}".format(office_location),
        "evaluation_criteria={}".format(evaluation_criteria),# 默认套话或老板补充
    ]),
    Step("notify", "feishu.cmd_notify", args=[
        "--to", "hr",
        "--severity", "info",
        "--title", "新候选人 offer 已发，请准备入职",
        "--body",
        "candidate={tid} name={name}\n入职日期={onboard_date}\n薪资={daily_rate} 元/天\n岗位={position}\n"
        "（候选人已收到带附件的 onboarding 邮件，附件：实习协议 + 入职登记表）".format(
            tid=tid, name=candidate_name, onboard_date=onboard_date,
            daily_rate=daily_rate, position=position_title),
    ]),
])
```

**变量来源对照**：

| 变量 | 默认 / 来源 | 飞书咨询老板？ |
|------|------------|--------------|
| `candidate_name` / `company` / `talent_id` | 自动填充（`outbound.cmd_send` 内置） | ❌ |
| `position_title` | `talents.position`（或老板原话） | ❌（DB 已有） |
| `interview_feedback` | 默认套话：「您在面试中展现出扎实的专业基础与良好的沟通能力，与团队气质契合，期待您加入后共同成长。」 | ✅ 老板可补充亮点 |
| `daily_rate` | **默认 350** | ✅ 老板可改 |
| `onboard_date` | **无默认，必须老板提供** | ✅ 必填 |
| `location` | 复用 `round1_invite` 用的 office_location | ❌ |
| `evaluation_criteria` | 默认套话：「实习期前 1 个月为试用期，期间表现作为是否转正 / 续签的参考；具体考核细则由直属导师入职后沟通。」 | ⚠ 可选 |

**硬规则**：

1. **附件由系统自动追加（v3.5.10）**。模板正文里承诺了《示例科技实习协议》+《实习生入职信息登记表》两份附件——`outbound.cmd_send` 走 `email_templates.auto_attachments` 自动 `--attach`，**agent 不要手动 `--attach`**。文件不在了 / 路径漂移时 `cmd_send` 会 fail-fast 拒发（rc 非零 + 错误信息含「默认附件文件缺失」）；agent 应原样把错误回 boss + 推飞书 `severity=warn`，而不是绕开校验裸发。
2. **必须 `feishu.cmd_notify --to hr` 同步 HR**。HR 不在邮件 cc 里（HR 走飞书通道，由 `lib.config["feishu"]["hr_open_id"]` 路由），但 chain 第二步**必须**给 HR 推一张飞书卡片，否则 HR 不知道有新人入职。
3. **必须老板给出 `onboard_date` 和 `daily_rate` 才能 propose**。如果老板的指令里没明说这两项，agent 应**先 stop and ask**（飞书回老板："入职日期 / 薪资还没确认，麻烦确认一下"），不要自作主张填默认值就 propose。`daily_rate` 默认 350 是「老板确认 350」时用的快捷路径，不是「老板没说时的兜底」。
4. **不动 stage / 不动业务字段**。这条 chain 是终态后的「副作用合规通知」，不是 stage 推进——`current_stage` 保持 `POST_OFFER_FOLLOWUP`，agent 不要顺手加 `talent.cmd_update --stage X` 之类的步骤。
5. **重发判定**：发新 offer 前先 `talent_emails` 查一下是否已有 `template_used='onboarding_offer'` + `status<>'error'` 的 outbound 行；有则 stop 并问老板「之前已发过 onboarding offer，是否重发？」。

> ⚠️ **不要把本 chain 拼到 §5.5 / §5.9 后面凑成一条「自动 offer」流水线**。每次发 onboarding offer 都是独立、需要老板明确确认的离散事件——前一步是 §5.9 force-jump 也好、是 §5.5 走完正常 round2 也好，都**不**自动触发本 chain。

### 5.11 学历感知一面排期（HR 触发，v3.5.7）

| 触发场景 | **HR**（不是老板）在飞书说「t_xxx 一面时间是 4-25 14:00」/「安排 张三 一面，时间 …」/同义。候选人**已经过 §5.10 (cmd_ingest_cv) 入库**，`talents.education` 非空。 |
|---------|-----|
| 关键字段 | `talents.has_cpp`（True/False/None，由 `cmd_parse_cv` 解析得出）/ `talents.education`（本科/硕士/博士）。**不**新加 `round1_interviewer_*` 列；面试官归属由 `round1_calendar_event_id` 反查飞书日历的 attendees。 |
| 退出条件 | `current_stage=ROUND1_SCHEDULED`、`round1_confirm_status=CONFIRMED`、`round1_calendar_event_id` 非空、3 位接收方都收到了通知（候选人邮件 + 面试官飞书 + 老板飞书 info） |
| 测试源点 | `tests/test_agent_chain.py::TestRound1DispatchByHr` |

**与 §5.1 的差异（必读）**：

| 维度 | §5.1（老板触发，老路径） | §5.11（HR 触发，新路径） |
|---|---|---|
| 触发人 | boss | HR |
| 一进来就建日历？ | ❌ 不建（等候选人 confirm） | ✅ 直接建（HR 通常已线下沟通过时间） |
| 派单 | 不派（老板默认自己面） | ✅ 走 `cmd_route_interviewer` 派给面试官 |
| 退出 stage | `ROUND1_SCHEDULING`（等候选人 confirm 才进 §5.2 建日历） | `ROUND1_SCHEDULED`（一气呵成） |
| 候选人 confirm 后回信 → reschedule_request | 走 §5.3（删日历重排） | **同样走 §5.3**（chain 出口对齐，无需复制一份） |

**Step 0：派单路由（必跑第一步）**

```python
ROUTE_RESULT = run_atomic("intake.cmd_route_interviewer",
    args=["--talent-id", tid, "--json"])

if ROUTE_RESULT["ambiguous"]:
    # cpp_first 路由表无法判断（has_cpp=null 且 education 不可识别）
    run_atomic("feishu.cmd_notify", args=[
        "--to", "hr",
        "--severity", "warn",
        "--title", "一面派单需 HR 手动指派 t_{}".format(tid),
        "--body", (
            "候选人 {name} ({tid})\n"
            "学历={edu} has_cpp={cpp}\n"
            "原因：{reason}\n\n"
            "请回复：派给 master / bachelor / cpp"
        ).format(
            name=candidate_name, tid=tid,
            edu=ROUTE_RESULT["education"],
            cpp=ROUTE_RESULT["has_cpp"],
            reason=ROUTE_RESULT["ambiguous_reason"]),
    ])
    STOP   # 等 HR 回复后由 agent 重启 §5.11，把 role 当指定值传入

if ROUTE_RESULT["config_error"]:
    # interviewer_*_open_id 没配齐 / 仍是占位符
    run_atomic("feishu.cmd_notify", args=[
        "--to", "hr",
        "--severity", "error",
        "--title", "面试官 open_id 配置缺失，无法派单",
        "--body", ROUTE_RESULT["config_error_detail"],
    ])
    STOP   # 等运维补齐 lib.config 后重试
```

**Step 1–5：本派单 chain（route ok 后跑）**

```python
INTERVIEWER_OPEN_IDS = ROUTE_RESULT["interviewer_open_ids"]   # 当前为单元素列表
INTERVIEWER_ROLES    = ROUTE_RESULT["interviewer_roles"]      # ["cpp"] / ["master"] / ["bachelor"]

run_chain([
    Step("send", "outbound.cmd_send", args=[
        "--talent-id", tid,
        "--template", "round1_invite",
        "--vars", "round1_time={}".format(round1_time),
        "--json",
    ]),
    Step("cal", "feishu.cmd_calendar_create", args=[
        "--talent-id", tid,
        "--time", round1_time,
        "--round", "1",
        "--duration-minutes", "30",
        "--candidate-name", candidate_name,
        "--candidate-email", candidate_email,
        # 把所有派到的面试官都拉进日历（老板 lib.feishu 内部自动加）
        *[arg for oid in INTERVIEWER_OPEN_IDS for arg in ("--extra-attendee", oid)],
        "--json",
    ]),
    Step("update", "talent.cmd_update", args=[
        "--talent-id", tid,
        "--stage", "ROUND1_SCHEDULED",
        "--set", "round1_time={}".format(round1_time),
        "--set", "round1_invite_sent_at={send.sent_at}",
        "--set", "round1_confirm_status=CONFIRMED",
        "--set", "round1_calendar_event_id={cal.event_id}",
    ]),
    # 给每个被派到的面试官发飞书消息（带候选人名）
    *[
        Step("notify_iv_{}".format(role), "feishu.cmd_notify", args=[
            "--to", "interviewer-{}".format(role),
            "--severity", "info",
            "--title", "一面安排：{}（{}）".format(candidate_name, _role_label(role)),
            "--body", (
                "候选人：{name} ({tid})\n"
                "时间：{time}（30 分钟）\n"
                "学历：{edu}\n"
                "是否会 C++：{cpp}\n"
                "邮箱：{email}\n"
                "日历事件：{cal_eid}\n"
                "由 HR 指派 / agent 自动派单（v3.5.7 §5.11）"
            ).format(
                name=candidate_name, tid=tid, time=round1_time,
                edu=ROUTE_RESULT["education"],
                cpp=ROUTE_RESULT["has_cpp"],
                email=candidate_email,
                cal_eid="{cal.event_id}"),
        ])
        for role in INTERVIEWER_ROLES
    ],
    Step("notify_boss", "feishu.cmd_notify", args=[
        "--to", "boss",
        "--severity", "info",
        "--title", "一面已排：{} {}".format(candidate_name, round1_time),
        "--body", (
            "talent={tid} 学历={edu} 会C++={cpp}\n"
            "面试官：{roles}\n"
            "日历已建：{cal_eid}\n"
            "（HR 通过 §5.11 自动派单）"
        ).format(
            tid=tid, edu=ROUTE_RESULT["education"], cpp=ROUTE_RESULT["has_cpp"],
            roles="、".join(INTERVIEWER_ROLES), cal_eid="{cal.event_id}"),
    ]),
])
```

**路由表（cpp_first 优先，与 `cmd_route_interviewer` 内部实现保持一致）**：

| `has_cpp` | `education` | role | 给谁 |
|---|---|---|---|
| `True`  | 任意 | `cpp`      | 面试官3（C++） |
| `False`/`None` | 硕士/博士 | `master`   | 面试官1 |
| `False`/`None` | 本科 | `bachelor` | 面试官2 |
| `True`  | `null` | `cpp`      | 面试官3（C++ 优先，仍可派） |
| `False`/`None` | `null`/不可识别 | — | **ambiguous → ASK_HR** |

**硬规则**：

1. **第一步必须是 `intake.cmd_route_interviewer`**。绝对不允许 agent 自己根据 `talents.education` / `has_cpp` 在脑子里算 open_id；必须经原子 CLI，让派单逻辑可审计、可测试、可改。
2. **`extra-attendee` 列表只能来自 `route.interviewer_open_ids`**。不允许 agent 拼任何 hardcode 的 open_id，包括「拍脑袋觉得这场该带上某某」。
3. **`--duration-minutes 30` 必须显式带上**。一面默认 30 分钟，与二面（60 分钟，§5.2）不同；漏了会变成默认 60，挤占老板下一档。
4. **stage 必须直接进 `ROUND1_SCHEDULED`**（不是 `_SCHEDULING`）。这是 §5.11 与 §5.1 最大的差异点 —— HR 触发的语义是「时间已确认、面试官已派、日历已建」，没有「等候选人 confirm」这一步。候选人邀请邮件仍照发，但 chain 不卡在 confirm 上。
5. **候选人若回信 reschedule_request → 走 §5.3**（先 `feishu.cmd_calendar_delete --event-id {round1_calendar_event_id}`）。chain 出口对齐 §5.1，无需另写。
6. **ambiguous / config_error 都必须 STOP**。绝对不允许 fallback 到「随便派一个」或「派给老板自己面」。route CLI 已经实现 fail closed（`fallback_used` 永远为 false），agent 不要绕过它。

> 📌 配置依赖：`lib.config["feishu"]["interviewer_master_open_id"]` / `interviewer_bachelor_open_id` / `interviewer_cpp_open_id` 必须配齐真实 open_id（来自 env `FEISHU_INTERVIEWER_{MASTER,BACHELOR,CPP}_OPEN_ID` 或 openclaw config 的 `interviewer{Master,Bachelor,Cpp}OpenId`）。未配齐会被 sentinel `ou_PLACEHOLDER_*` 占位，`cmd_route_interviewer` 直接 `config_error=true`，`feishu.send_text_to_interviewer_*` / `cmd_calendar_create --extra-attendee` 也会 fail closed。

## 6. 失败处理与回退

| 失败位置 | run_chain 行为 | agent 应做 |
|----------|---------------|-----------|
| 第一步 `outbound.cmd_send` 失败 | 短路。无副作用 | 推飞书 `feishu.cmd_notify --severity error --title "邮件发送失败"`，附 stderr |
| `cmd_send` 成功但 `talent.cmd_update` 失败 | 短路。**邮件已发出**，DB 未推进 | 推飞书 `severity=critical --title "邮件已发但状态未更新"`，附 talent_id + sent_at；老板手动 `talent.cmd_update` 补救 |
| `feishu.cmd_calendar_create` 成功但回写 `event_id` 失败 | 短路 | 推飞书 `severity=critical --title "日历已建但 event_id 未回写"`，附 cal.event_id；老板手动 update |
| `inbox.cmd_analyze` 上游 LLM 限流 | 该邮件 `ai_*` 留空 | 不做写动作；下一轮 cron 自动重试 |
| 任意 atomic CLI 抛 `UserInputError` | run_chain 把 UserInputError 透传 | **不**推飞书（人类输入错），stderr + 终止 chain |

`lib.cli_wrapper.run_with_self_verify` 已经帮每个 atomic CLI 做"crash → 飞书告警"。
agent 只需关心 chain 结果对象 `chain_result["ok"]` / `chain_result["failed_step"]`。

## 7. 安全护栏

1. **禁止裸调 SQL / SMTP / IMAP**。这些被 `lib/talent_db.py` / `lib/smtp_sender.py` /
   `lib/exam_imap.py` 封装；agent 写代码时只能 import atomic CLI。
2. **`RECRUIT_DISABLE_SIDE_EFFECTS=1`** 环境变量下所有 sink（SMTP / IMAP / 飞书 / DB 写）都被
   `lib/side_effect_guard.py` 拦截。测试与 dry-run 必须设置。agent 在生产环境不可设置。
3. **chain 不超过 5 步**。如果某个业务需要 ≥ 5 步，先回头看是不是漏了一个 atomic CLI。
4. **`--force` 必须带 `--reason`**。所有"非自然 stage 跳转"必须显式说明原因，方便老板审计。
5. **不要 `talent.cmd_update --set current_stage=...`**。`current_stage` 必须用 `--stage` 推。
6. **POST_OFFER_FOLLOWUP 阶段 agent 不自动回信**。一律生成 draft + 推飞书让老板按按钮。
7. **拒类操作的两条路径**：
   - **物理删档**：`interview.cmd_result --result reject_delete`（一/二面被拒 + 不留池）——
     自带"先发拒信再 `talent_db.delete_talent()`"逻辑。**不要**直接 `talent.cmd_delete` 跳过拒信。
   - **拒+留池**：`auto_reject.cmd_scan_exam_timeout`（笔试 3 天不交）+ §5.7（笔试人工不过）
     —— 都推到 `EXAM_REJECT_KEEP`，候选人留人才库可后续复用。
   v3.5.11 起 `auto_reject` 不再 `cmd_delete`；如果你看到旧的 doc/code 还说"拒+删档"，那是过期描述。
8. **`outbound.cmd_send` 自动归一化 body（v3.5.13 / 2026-04-22）**。
   `--body` / `--body-file` / `--use-cached-draft` / `--template` 渲染结果都会过一道兜底
   `_normalize_body`：解码字面 `\n` / `\r\n` / `\t` 转义，剥 `**粗体**` / `__粗体__`、行首
   `# / ## / ###` 标题前缀。**不**碰 `*斜体*` / 反引号代码 / `- 列表项`（中文邮件易误伤）。
   - **agent 含义**：起草 body 时可以放心写自然换行（无论字面 `\n` 还是真换行都行），
     可以加 `**重点**`方便老板在飞书卡上一眼看到，cmd_send 会自动转成"邮件级"的真换行 +
     无星号纯文本。**不要**为这个手动 `printf` 转义或 `body.replace("\\n","\n")`。
   - 生效时 cmd_send stderr 会打 `body 正规化（freeform）：解码 \n×N, 剥粗体×M ...`。
   - 极端情况想原样发字面 `\n`：`outbound.cmd_send --no-body-normalize`（几乎从来用不上）。
   - 事故源点：候选人A / t_demo01 2026-04-22 16:08 邮件 `msg_demo_*`，body_excerpt 里
     是字面 `\\n\\n` + `**5月6日**`。测试钉死见 `tests/test_outbound_body_normalize.py`。
9. **任何"X月X日（周X）" / "下周X" / "周X X点" 必须先调 `common.cmd_weekday` 查证（v3.5.14 / 2026-04-22）**。
   LLM 心算 weekday 经常错——同事故源点的同一封邮件还把"5月6日"写成"周二"
   （实际是周三）。规则：
   - 起草任何含星期几表述的 body / 飞书草稿 / 日历事件标题之前，agent **必须**先：
     ```bash
     PYTHONPATH=scripts python3 -m common.cmd_weekday <DATE> [--json]
     ```
     然后**照抄**返回的 `weekday_cn`（"周一" ... "周日"）。
   - 输入超宽容：`2026-05-06` / `5-6` / `5月6日` / `today` / `tomorrow` / `+3` 全都吃；
     无年份时默认 auto 策略（今年该日没过用今年，已过自动跳明年，避免误回去年）。
   - 一次可传多个日期，候选时间段排查很方便：
     `python3 -m common.cmd_weekday 5-6 5-13 5-20 --json`
   - 时区固定 Asia/Shanghai。
   - 测试钉死见 `tests/test_common_weekday.py`，含 `2026-05-06=周三` 这类已知日期回归基线。
10. **候选人资料目录约定（v3.5.8）**。
   - 每个候选人在 `$RECRUIT_DATA_ROOT/candidates/<talent_id>/` 下有专属目录，
     由 `lib.candidate_storage.ensure_candidate_dirs()` 在 `intake.cmd_new_candidate`
     录入候选人时自动创建（warn-continue：mkdir 失败不阻断录入，只飞书 warn）。
   - 三个固定子目录：
       * `cv/`           候选人 CV 原件（由 `intake.cmd_attach_cv` 调
                         `candidate_storage.import_cv` 自动 move 进来）
       * `exam_answer/`  笔试答案（`talent_emails.context='exam'` 的邮件附件 +
                         `exam/fetch_exam_submission.py` 的 `legacy_fetch/` 缓存）
       * `email/`        其他邮件附件（context!='exam'）
   - 落盘走 `lib.candidate_storage.attachment_dir(tid, context, eid)` →
     `candidates/<tid>/{exam_answer|email}/em_<email_id>/<filename>`。
     旧 `data/candidate_answer/t_t_<tid>/em_<eid>/` 的 `t_t_` bug 已修。
   - 文件权限固定 `0o600`，目录 `0o700`（仅 owner 可读，含 simple sandbox / cron）。
   - 元数据写到 `talent_emails.attachments JSONB`，**path 字段是相对** `data_root()`
     的路径（如 `candidates/t_xxx/exam_answer/em_yyy/file.zip`）。agent 拿到要
     看附件请用 `Path(data_root()) / row.path` 还原。
   - `talents.cv_path` 字段存 CV 的**绝对路径**（在 `candidates/<tid>/cv/` 下）。
   - 单文件硬上限 25MB、单封邮件 ≤ 20 个附件，超过都写一行 `saved=false` 元数据
     而不是抛异常 —— **agent 不要**因为附件落盘失败就重试 `cmd_scan` 整个邮件。
   - 测试 / dry-run：用 `RECRUIT_DATA_ROOT` 注入临时根；`RECRUIT_DISABLE_SIDE_EFFECTS=1`
     时 `ensure_candidate_dirs` / `import_cv` 不动盘但路径照算（供 echo 审计）。
   - `data/candidates/` 已在 `.gitignore` 屏蔽，绝对禁止 commit 候选人简历 / 笔试代码。
   - 不解压压缩包（zip/rar 原样存）。需要看 zip 内容仍走 `exam.fetch_exam_submission`，
     该脚本现在直接落到 `candidates/<tid>/exam_answer/legacy_fetch/`（v3.5.8 之前
     老的 `/tmp/exam_submissions/<tid>/` 已迁过来）。
   - **v3.5.9 by_name 软链层**：`data/candidates/by_name/<姓名>__<tid>/` 是
     `lib.candidate_aliases.rebuild_alias_for()` 维护的 symlink → `../<tid>/`。
     HR 在文件管理器里按姓名找资料用这个目录；**所有代码 / DB / Agent 决策仍以
     `t_xxx/` 为唯一规范路径**，不要去 readlink、不要去拼 by_name 路径。
     由 `cmd_new_candidate`、`cmd_attach_cv`、`talent.cmd_update`（改
     `candidate_name` 时）和 `talent.cmd_delete` 自动维护；线上修复跑
     `talent.cmd_rebuild_aliases`（含 `--dry-run`，幂等）。alias 失败一律
     warn-continue，**不影响 DB 写入成功**。
9. **一面派单约定（v3.5.7 §5.11）**。
   - **任何**一面派单（HR 触发的 §5.11）必须**先**调 `intake.cmd_route_interviewer`，
     绝对禁止 agent 在脑子里根据 `talents.education` / `has_cpp` 算 open_id。
   - 三个面试官的 open_id **只能**来自 `lib.config["feishu"]["interviewer_*_open_id"]`；
     hardcode 的 `ou_xxx` 字符串、占位符 `ou_PLACEHOLDER_*`、或运行时拼出的字符串
     一律禁止——`lib.feishu.send_text_to_interviewer_*` 与 `cmd_calendar_create
     --extra-attendee` 的占位符检测会 fail closed，但靠这层兜底很难看懂日志。
   - `cmd_route_interviewer` 输出 `ambiguous=true` 或 `config_error=true` 时**必须 STOP**，
     转 ASK_HR 分支（`feishu.cmd_notify --to hr`）。**不允许** fallback 到「随便派一个」
     「派给老板自己」「派给上次的面试官」。
   - HR 触发场景只走 §5.11，老板触发场景仍走 §5.1（不派单、stage 进 `ROUND1_SCHEDULING`
     等候选人 confirm）。两条 chain 共享 §5.3（reschedule）/ §5.4（defer）出口，不要复制。
10. **模板默认附件（v3.5.10）**。
    - `outbound.cmd_send --template <T>` 会按 `email_templates.auto_attachments._REGISTRY`
      查表，自动追加固定附件。当前注册的：
        * `onboarding_offer` → `data/onoffer_data/模板-示例科技实习协议-2026年4月版.docx`
                              + `data/onoffer_data/示例科技-实习生入职信息登记表-2026年版.docx`
    - **agent 不要再手动 `--attach`** 这两份文件；同名文件去重，`--attach` 重复传也安全，
      但徒增噪音。
    - 文件不在了 → `cmd_send` **fail-fast 拒绝发送**（合同漏发是法律事故，宁可不发也不能裸发 offer）。
    - 加 / 改注册条目的流程：先把新文件提交到 `data/<sub>/`（**不能**在 `.gitignore` 里），
      再改 `_REGISTRY`，跑 `tests/test_auto_attachments.py`，最后通知老板 review 模板正文。
    - 模板正文里凡是提到「附件是 ...」必须和注册表一致；`tests/test_email_templates.py::
      test_onboarding_offer_renders_all_required_vars` 同时锁定正文关键词，话术漂移会 CI 红。

---

## 维护指引

- 新增一个 atomic CLI：在 §4 速查表里加一行；如果它会进入 agent 决策路径，再在 §3 决策矩阵里加规则。
- 删一个 atomic CLI：先在本文 §4 划掉，再去删脚本，最后跑 `tests/run_all.py` 验证。
- 改 intent 集合：先改 `prompts/inbox_general.json` 里的 `valid_intents`；再改 `inbox/analyzer.py` 的
  `_NEED_BOSS_INTENTS`；最后在本文 §3 矩阵补一行规则。
