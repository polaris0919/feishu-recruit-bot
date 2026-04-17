# 复杂协商回归清单

> 适用场景：在你自己的本地或 staging 环境、当前 Feishu 配置和两组专用测试邮箱下，验证 `daily_exam_review.py` 的真实协商行为，而不是只验证单个命令能否跑通。
>
> 执行约定：除非特别说明，下文所有 Python 命令都默认在仓库根目录 `<repo_root>/skills/recruit-ops` 执行，并使用 `uv run python3 scripts/...` 作为前缀。文中保留 `python3 exam/...`、`python3 common/...` 只是为了突出相对脚本路径。
>
> 固定测试邮箱：
>
> - `candidate-a@example.com`
> - `candidate-b@example.com`

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

真正的最终敲定时间，应该用：

```bash
python3 common/cmd_finalize_interview_time.py --talent-id <talent_id> --round 1
python3 common/cmd_finalize_interview_time.py --talent-id <talent_id> --round 2
```

这个命令会：

- 如果最终时间等于当前 pending 时间：走 `cmd_confirm`
- 如果老板改成其他时间：走 `cmd_reschedule --confirmed`

所以你想测的“协商 3 轮后才最终确认”，正确流程就是：

1. 候选人连续发 3 轮邮件
2. 每轮之后跑扫描
3. 看状态始终保持 `SCHEDULING + PENDING`
4. 最后由老板执行 `cmd_finalize_interview_time.py`

---

## 二、统一检查命令

### 1. 人类可读状态

```bash
cd <repo_root>/skills/recruit-ops/scripts
python3 common/cmd_status.py --talent-id <talent_id> --audit-lines 30
```

### 2. 完整 DB 视图

```bash
cd <repo_root>/skills/recruit-ops/scripts
python3 common/cmd_debug_candidate.py --talent-id <talent_id> --event-limit 30
```

### 3. 直接查 SQL

```bash
psql "$DATABASE_URL" -c "
SELECT talent_id, current_stage, round1_confirm_status, round2_confirm_status,
       round1_time, round2_time, wait_return_round,
       round1_last_email_id, round2_last_email_id, exam_last_email_id
FROM talents
WHERE talent_id = '<talent_id>';
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

- `round1_last_email_id`
- `round2_last_email_id`
- `exam_last_email_id`
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
- 只更新 `roundN_last_email_id`

---

## 四、邮箱角色安排

因为你现在只固定了两个测试邮箱，所以推荐这样使用：

| 用途 | 邮箱 |
|------|------|
| 当前场景目标候选人 | `candidate-a@example.com` |
| 当前场景干扰候选人 | `candidate-b@example.com` |

下一组交换角色：

| 用途 | 邮箱 |
|------|------|
| 当前场景目标候选人 | `candidate-b@example.com` |
| 当前场景干扰候选人 | `candidate-a@example.com` |

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
- 邮箱：`candidate-a@example.com`
- 干扰候选人 B：`E2E_R1_NEGOTIATION_B`
- 邮箱：`candidate-b@example.com`

### 执行骨架

#### 0. 创建候选人并安排一面

```bash
python3 intake/cmd_new_candidate.py --name "E2E_R1_NEGOTIATION_A" --email "candidate-a@example.com" --position "量化研究实习生"
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
- `round1_last_email_id` 更新到 A 的这封邮件
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
- `round1_last_email_id` 更新为第 2 轮邮件

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

- 自动执行 `interview/cmd_defer.py --round 1`
- `current_stage = WAIT_RETURN`
- `wait_return_round = 1`
- `round1_time` 清空
- 审计里出现暂缓动作

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
- `exam_last_email_id` 更新
- `current_stage = EXAM_REVIEWED`
- `talent_events` 中出现 `exam_prereview`
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
- `round2_last_email_id` 更新
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
- `round2_last_email_id` 更新
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
- roundN_last_email_id
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

- `<repo_root>/skills/recruit-ops/docs/CLI_REFERENCE.md`

如果你要验证真实的“多轮协商 + 干扰邮件 + pending 语义 + 改期 / 暂缓 / 线上 / 笔试混合邮箱”，优先使用本文件。
