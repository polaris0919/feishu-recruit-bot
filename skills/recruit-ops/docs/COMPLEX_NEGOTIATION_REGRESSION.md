# 复杂协商回归清单

> 适用场景：在当前本地真库、当前 Feishu 配置和两个固定测试邮箱下，验证候选人协商流程的真实行为，而不是只验证单个命令能否跑通。
>
> 执行约定：除非特别说明，下文所有 Python 命令都默认在仓库根目录 `<RECRUIT_WORKSPACE>/skills/recruit-ops` 执行，并使用 `uv run python3 -m <module>` 作为前缀。
>
> 固定测试邮箱：
>
> - `fake-test@example.com`
> - `candidate-k@example.com`

---

## v3.5 命令映射（先读这一节）

v3.5 起 `exam/daily_exam_review.py`、`round1/cmd_round1_schedule.py`、
`common/cmd_finalize_interview_time.py`、`interview/cmd_{confirm,defer,reschedule}.py`
**全部下线**。本文中保留旧命令仅为突出"原本对应的扫描/动作分支"，实际执行请按下表替换。
完整决策规则与 chain 范式见 [`docs/AGENT_RULES.md`](AGENT_RULES.md)。

| 旧命令（本文中仍出现） | v3.5 等价做法 |
|------------------------|---------------|
| `python3 exam/daily_exam_review.py --interview-confirm-only` | `python3 -m inbox.cmd_scan && python3 -m inbox.cmd_analyze`（agent 看到 `confirm`/`reschedule`/`defer_until_return`/`request_online` intent 后按规则推下一步） |
| `python3 exam/daily_exam_review.py --reschedule-scan-only` | 同上。已确认候选人收到 reschedule/defer/request_online 邮件时，由 agent 推飞书 + 视情况调 `feishu.cmd_calendar_delete` |
| `python3 exam/daily_exam_review.py --exam-only` | 同上。笔试提交邮件由 `inbox.cmd_analyze` 走 `prompts/inbox_general.json` 识别为 `exam_submission`，agent 再调 `exam.cmd_exam_ai_review` |
| `python3 round1/cmd_round1_schedule.py --talent-id T --time X` | `python3 -m outbound.cmd_send --talent-id T --template round1_invite --vars '{"round1_time":"X"}'` 然后 `python3 -m talent.cmd_update --talent-id T --stage ROUND1_SCHEDULING --set round1_time="X" --set round1_invite_sent_at=__NOW__ --set round1_confirm_status=PENDING` |
| `python3 common/cmd_finalize_interview_time.py --talent-id T --round 1` | `python3 -m talent.cmd_update --talent-id T --stage ROUND1_SCHEDULED --set round1_confirm_status=CONFIRMED` 然后 `python3 -m feishu.cmd_calendar_create --talent-id T --round 1` |
| `python3 common/cmd_finalize_interview_time.py --talent-id T --round 2` | 同上把 `round 1` 换成 `round 2`、stage 换成 `ROUND2_SCHEDULED` |
| `interview/cmd_defer.py --round N` | `python3 -m talent.cmd_update --talent-id T --stage WAIT_RETURN --set wait_return_round=N --set roundN_time=__NULL__` |
| `talent_events 中出现 exam_prereview` | v3.5 改为 `talent_emails.ai_payload` 中带 `intent=exam_submission` + `exam_review_summary`（由 `exam.cmd_exam_ai_review` 写入） |

---

## 一、先理解真实扫描路径

这个项目里，“候选人协商阶段”和“已确认后反悔”不是同一条扫描链路。

### 路径 A：待确认协商阶段

入口：

```bash
python3 exam/daily_exam_review.py --interview-confirm-only
```

这条路径负责处理：

- `confirm`
- `reschedule`
- `timeout`
- `defer_until_shanghai`

它**不会**直接最终确认，而是把候选人推进到“老板待确认”状态：

- `roundN_confirm_status = PENDING`
- `roundN_time = 当前候选人同意或提议的新时间`
- `roundN_confirm_prompted_at != null`
- `current_stage` 仍是 `ROUND1_SCHEDULING` 或 `ROUND2_SCHEDULING`

### 路径 B：已确认后改期 / 暂缓 / 线上请求

入口：

```bash
python3 exam/daily_exam_review.py --reschedule-scan-only
```

这条路径只扫描**已经确认**的候选人，处理：

- `reschedule`
- `defer_until_shanghai`
- `request_online`

预期行为：

- `reschedule`：撤销确认，回退到 scheduling
- `defer_until_shanghai`：进入 `WAIT_RETURN`
- `request_online`：**不回退**，只留下邮件游标和提示

### 最终确认不是扫描完成的

真正的最终敲定时间，v3.5 由老板手动触发 agent，agent 执行：

```bash
# 时间不变：直接确认 + 创日历
python3 -m talent.cmd_update --talent-id <talent_id> --stage ROUND1_SCHEDULED \
  --set round1_confirm_status=CONFIRMED
python3 -m feishu.cmd_calendar_create --talent-id <talent_id> --round 1

# 时间改了：先改 round1_time，再确认 + 创日历
python3 -m talent.cmd_update --talent-id <talent_id> --stage ROUND1_SCHEDULED \
  --set round1_time="2026-05-12 10:00" --set round1_confirm_status=CONFIRMED
python3 -m feishu.cmd_calendar_create --talent-id <talent_id> --round 1
```

所以你想测的"协商 3 轮后才最终确认"，正确流程就是：

1. 候选人连续发 3 轮邮件
2. 每轮之后跑 `inbox.cmd_scan && inbox.cmd_analyze`
3. 看状态始终保持 `SCHEDULING + PENDING`
4. 最后由老板触发 agent，按上面 chain 把 stage 推到 `ROUND1_SCHEDULED` + 创日历

---

## 二、统一检查命令

### 1. 人类可读状态

```bash
cd <RECRUIT_WORKSPACE>/skills/recruit-ops/scripts
python3 common/cmd_status.py --talent-id <talent_id> --audit-lines 30
```

### 2. 完整 DB 视图

```bash
cd <RECRUIT_WORKSPACE>/skills/recruit-ops/scripts
python3 common/cmd_debug_candidate.py --talent-id <talent_id> --event-limit 30
```

### 3. 直接查 SQL

```bash
psql "$DATABASE_URL" -c "
SELECT talent_id, current_stage, round1_confirm_status, round2_confirm_status,
       round1_time, round2_time, wait_return_round
FROM talents
WHERE talent_id = '<talent_id>';
"

# v3.5.2 起 *_last_email_id 字段已 DROP，邮件证据改查 talent_emails：
psql "$DATABASE_URL" -c "
SELECT email_id, direction, context, status, ai_intent, sent_at, subject
FROM talent_emails
WHERE talent_id = '<talent_id>'
ORDER BY sent_at DESC
LIMIT 20;
"
```

### 4. 删除后复核

```bash
psql "$DATABASE_URL" -c "
SELECT talent_id, current_stage
FROM talents
WHERE talent_id = '<talent_id>';
"
```

---

## 三、关键观察点

每轮扫描之后，不要只看 `current_stage`，要一起看下面这些字段。

### 核心状态

- `current_stage`
- `round1_confirm_status`
- `round2_confirm_status`
- `round1_time`
- `round2_time`
- `wait_return_round`

### 协商 / 邮件证据

- `talent_emails.email_id` / `message_id` / `in_reply_to`（v3.5.2 起这是邮件去重的 source-of-truth）
- `talent_emails.context` ∈ {`exam`, `round1`, `round2`, `followup`, `intake`, `unknown`}
- `talent_emails.status` ∈ {`received`, `pending_boss`, `replied`, `dismissed`, `snoozed`, `auto_processed`, `duplicate_skipped`, `error`}
- `round1_confirm_prompted_at`
- `round2_confirm_prompted_at`

### 审计与回溯

- `talent_events`
- `talent_events.event_id`
- `cmd_status.py` 最近操作摘要
- Feishu 推送文案
- 实际邮件 `Message-ID`

### 重点判断规则

#### 候选人还在协商中

符合下面组合时，说明“候选人回信了，但老板还没最终确认”：

- `current_stage = ROUND1_SCHEDULING` 或 `ROUND2_SCHEDULING`
- `roundN_confirm_status = PENDING`
- `roundN_time = 当前候选人最后一封有效邮件对应的提议时间`
- `roundN_confirm_prompted_at != null`

#### 已确认后再次改期

- `current_stage` 从 `ROUNDN_SCHEDULED` 回到 `ROUNDN_SCHEDULING`
- `roundN_confirm_status = PENDING`
- 原 `roundN_time` 仍保留

#### 已确认后请求线上

- `current_stage` 不回退
- `roundN_confirm_status` 仍是 `CONFIRMED`
- `talent_emails` 增 1 行 inbound（v3.5.2：原 `roundN_last_email_id` 已 DROP）

---

## 四、邮箱角色安排

因为你现在只固定了两个测试邮箱，所以推荐这样使用：

| 用途 | 邮箱 |
|------|------|
| 当前场景目标候选人 | `fake-test@example.com` |
| 当前场景干扰候选人 | `candidate-k@example.com` |

下一组交换角色：

| 用途 | 邮箱 |
|------|------|
| 当前场景目标候选人 | `candidate-k@example.com` |
| 当前场景干扰候选人 | `fake-test@example.com` |

这样就能在只有两个真实邮箱的情况下，仍然模拟：

- 目标候选人邮件
- 另一个候选人的干扰邮件

如果你后面还能控制自动回复 / 退信邮箱，那可以把它们加入“增强版干扰池”；如果没有，就先用：

- 另一位候选人邮件
- 旧邮件
- 重复 `Message-ID`

---

## 五、场景 1：一面三轮协商后最终确认

### 目标

验证：

- 系统只识别目标候选人邮件
- 最新有效邮件优先
- 中间始终 `ROUND1_SCHEDULING + PENDING`
- 最终要靠老板收口命令进入 `ROUND1_SCHEDULED`

### 预设

- 候选人 A：`E2E_R1_NEGOTIATION_A`
- 邮箱：`fake-test@example.com`
- 干扰候选人 B：`E2E_R1_NEGOTIATION_B`
- 邮箱：`candidate-k@example.com`

### 执行骨架

#### 0. 创建候选人并安排一面

```bash
python3 intake/cmd_new_candidate.py --name "E2E_R1_NEGOTIATION_A" --email "fake-test@example.com" --position "量化研究实习生"
python3 round1/cmd_round1_schedule.py --talent-id <talent_id_a> --time "2026-05-10 14:00"
```

可选：同时创建干扰候选人 B，并安排其他时间。

#### 1. 第 1 轮协商

候选人 A 邮件内容建议：

```text
这个时间我不方便，能否改到 2026-05-11 15:00？
```

干扰邮件建议：

- 候选人 B 发一封无关确认邮件
- 或者同邮箱旧线程邮件

扫描：

```bash
python3 exam/daily_exam_review.py --interview-confirm-only
```

期望：

- A 仍是 `ROUND1_SCHEDULING`
- `round1_confirm_status = PENDING`
- `round1_time = 2026-05-11 15:00`
- `talent_emails` 增 1 行 inbound 记录（v3.5.2：原 round1_last_email_id 已 DROP，邮件证据改在 talent_emails 表）
- B 不影响 A

#### 2. 第 2 轮协商

候选人 A 再发：

```text
5 月 11 日下午也不方便，5 月 12 日上午 10:00 可以吗？
```

再次插入 B 的干扰邮件后，重新扫描：

```bash
python3 exam/daily_exam_review.py --interview-confirm-only
```

期望：

- 仍是 `ROUND1_SCHEDULING`
- `round1_confirm_status = PENDING`
- `round1_time = 2026-05-12 10:00`
- `talent_emails` 又增 1 行 inbound（v3.5.2：原 round1_last_email_id 已 DROP）

#### 3. 第 3 轮候选人同意

候选人 A 再发：

```text
好的，5 月 12 日上午 10:00 我可以参加。
```

扫描：

```bash
python3 exam/daily_exam_review.py --interview-confirm-only
```

期望：

- 仍然不是 `ROUND1_SCHEDULED`
- 仍然是 `ROUND1_SCHEDULING`
- `round1_confirm_status = PENDING`
- `round1_time = 2026-05-12 10:00`
- `round1_confirm_prompted_at` 已刷新

#### 4. 老板最终确认

```bash
python3 common/cmd_finalize_interview_time.py --talent-id <talent_id_a> --round 1
```

最终期望：

- `current_stage = ROUND1_SCHEDULED`
- `round1_confirm_status = CONFIRMED`
- 日历创建逻辑已触发

---

## 六、场景 2：一面已确认后再次改期

### 目标

验证已确认状态被正确撤销。

### 流程

1. 先完成场景 1，让候选人 A 进入 `ROUND1_SCHEDULED`
2. 候选人 A 发邮件：

```text
我临时有事，想改到 2026-05-13 15:00。
```

3. 插入一封来自候选人 B 的干扰邮件
4. 扫描：

```bash
python3 exam/daily_exam_review.py --reschedule-scan-only
```

### 期望

- `ROUND1_SCHEDULED -> ROUND1_SCHEDULING`
- `round1_confirm_status = PENDING`
- 原 `round1_time` 仍保留
- `round1_calendar_event_id` 被清空或进入待删
- `talent_events` 出现 `round1_reschedule_requested`

---

## 七、场景 3：一面协商中候选人说在国外

### 目标

验证候选人在协商阶段就直接转入 `WAIT_RETURN`。

### 候选人邮件样本

```text
我最近都不在国内，等回国后再约可以吗？
```

### 扫描

```bash
python3 exam/daily_exam_review.py --interview-confirm-only
```

### 期望

- agent 调 `talent.cmd_update --stage WAIT_RETURN --set wait_return_round=1 --set round1_time=__NULL__`
- `current_stage = WAIT_RETURN`
- `wait_return_round = 1`
- `round1_time` 清空
- 审计里出现 stage 变更动作

---

## 八、场景 4：笔试混合邮箱验证

### 目标

验证：

- 只识别目标候选人的笔试作答邮件
- 正确估算作答时间
- 生成预审报告
- 干扰邮件不影响结果

### 预设

候选人需先进入 `EXAM_SENT`。

推荐准备这几类邮件：

1. 目标候选人的真实笔试作答邮件
2. 另一位候选人的无关邮件
3. 一封旧邮件（时间早于 `exam_sent_at`）
4. 一封自动回复或退信（如果能模拟）

### 作答邮件建议内容

- 正文说明
- 附件至少包含：
  - `answer.py`
  - `result.csv`
  - 可选 `README.txt`

### 扫描

```bash
python3 exam/daily_exam_review.py --exam-only
```

### 期望

- 命中目标候选人
- `talent_emails` 新增一行（`message_id` 唯一）；其 `ai_payload.intent = exam_submission`
- `current_stage = EXAM_REVIEWED`
- agent 调 `exam.cmd_exam_ai_review` 后 `ai_payload.exam_review_summary` 落库
- Feishu 报告里能看到：
  - 作答时间
  - 完整性
  - 代码质量摘要

### 作答时间重点

这个链路证明的是“预审时间估算”，不是绝对精确计时。重点看：

- 目标邮件是否被正确识别
- `exam_sent_at` 与回复时间是否产生合理区间
- 干扰邮件是否未污染时间判断

---

## 九、场景 5：二面三轮协商后最终确认

### 目标

完全复用一面场景，但验证二面。

### 推荐流程

1. 先把候选人推进到 `ROUND2_SCHEDULING`
2. 让候选人经历 3 轮：
   - 第 1 轮提议新时间
   - 第 2 轮再次修改
   - 第 3 轮同意最终时间
3. 每轮后都跑：

```bash
python3 exam/daily_exam_review.py --interview-confirm-only
```

4. 最终老板执行：

```bash
python3 common/cmd_finalize_interview_time.py --talent-id <talent_id> --round 2
```

### 期望

最终确认前：

- `current_stage = ROUND2_SCHEDULING`
- `round2_confirm_status = PENDING`
- `round2_time` 始终是最新有效邮件对应的时间

最终确认后：

- `current_stage = ROUND2_SCHEDULED`
- `round2_confirm_status = CONFIRMED`

---

## 十、场景 6：二面已确认后再次改期

### 目标

验证已确认后二面改期的真实回退。

### 候选人邮件样本

```text
我想改到 2026-05-20 14:00，可以吗？
```

### 扫描

```bash
python3 exam/daily_exam_review.py --reschedule-scan-only
```

### 期望

- `ROUND2_SCHEDULED -> ROUND2_SCHEDULING`
- `round2_confirm_status = PENDING`
- 原 `round2_time` 保留
- `talent_emails` 增 inbound 行（v3.5.2：原 round2_last_email_id 已 DROP）
- `round2_reschedule_requested` 出现在审计中

---

## 十一、场景 7：二面已确认后暂缓 / 转线上

### 子场景 A：已确认后二面暂缓

候选人邮件：

```text
我现在人在国外，等回国后再约吧。
```

扫描：

```bash
python3 exam/daily_exam_review.py --reschedule-scan-only
```

期望：

- `current_stage = WAIT_RETURN`
- `wait_return_round = 2`
- `round2_time` 被清空

### 子场景 B：已确认后二面请求线上

候选人邮件：

```text
我现在在外地，希望改成线上面试。
```

扫描：

```bash
python3 exam/daily_exam_review.py --reschedule-scan-only
```

期望：

- `current_stage` 保持 `ROUND2_SCHEDULED`
- `round2_confirm_status` 仍是 `CONFIRMED`
- `round2_time` 保持不变
- `talent_emails` 增 inbound 行（v3.5.2：原 round2_last_email_id 已 DROP）
- 系统只给出提示，不自动回退

---

## 十二、干扰邮件设计要求

为了验证扫描健壮性，每个复杂场景都至少混入 1 到 2 封干扰邮件。

### 最小干扰集

- 另一位候选人的正常回信
- 同一候选人的旧邮件

### 增强干扰集

- 自动回复
- 退信
- 重复 `Message-ID`
- 同一候选人的两封连续有效邮件，验证“最新有效邮件优先”

### 真正要验证的点

- 是否通过 `From` 头精确归因
- 是否跳过早于 invite 时间的旧邮件
- 是否跳过 auto-reply / postmaster / undeliverable
- 是否只吃最后一封有效邮件

---

## 十三、每一轮都要记录什么

建议你每一轮都按这个格式留下证据：

```text
场景名：
回合：
目标候选人：
干扰候选人：
扫描命令：

目标邮件：
- From:
- Subject:
- Message-ID:
- 预期 intent:

干扰邮件：
- From:
- Subject:
- Message-ID:
- 为什么应该被忽略:

扫描后状态：
- current_stage
- roundN_confirm_status
- roundN_time
- talent_emails 行（v3.5.2：原 roundN_last_email_id 已 DROP）
- roundN_confirm_prompted_at
- wait_return_round

审计：
- talent_events 最新动作

结论：
- Pass / Fail
```

---

## 十四、与基础版文档的关系

如果你只是想快速验证命令本身是否能跑，继续看：

- `<RECRUIT_WORKSPACE>/skills/recruit-ops/docs/CLI_REFERENCE.md`

如果你要验证真实的“多轮协商 + 干扰邮件 + pending 语义 + 改期 / 暂缓 / 线上 / 笔试混合邮箱”，优先使用本文件。
