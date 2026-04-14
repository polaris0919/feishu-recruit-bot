# recruit-ops CLI 参考手册

> **执行目录**：所有命令均在 `scripts/` 目录下执行，例如：
> ```bash
> cd /home/admin/recruit-workspace/skills/recruit-ops/scripts
> python3 common/cmd_status.py --talent-id t_xxx
> ```

---

## 目录

1. [招聘流水线概览](#招聘流水线概览)
2. [简历入库（intake）](#简历入库-intake)
3. [一面（round1）](#一面-round1)
4. [笔试（exam）](#笔试-exam)
5. [二面（round2）](#二面-round2)
6. [面试统一操作（interview）](#面试统一操作-interview)
7. [通用管理（common）](#通用管理-common)
8. [定时任务（cron）](#定时任务-cron)

---

## 招聘流水线概览

```
简历进库
  ↓  cmd_ingest_cv / cmd_import_candidate
NEW
  ↓  cmd_round1_schedule
ROUND1_SCHEDULING（等候选人确认）
  ↓  cmd_round1_confirm（候选人回复确认后）
ROUND1_SCHEDULED（一面已安排）
  ↓  cmd_round1_result --result pass
ROUND1_DONE_PASS
  ↓  cmd_exam_result 前：发笔试邮件
EXAM_SENT
  ↓  daily_exam_review 自动扫描
EXAM_REVIEWED
  ↓  cmd_exam_result --result pass --round2-time "..."
ROUND2_SCHEDULING（等候选人确认）
  ↓  cmd_round2_confirm
ROUND2_SCHEDULED（二面已确认）
  ↓  cmd_round2_result --result pass
ROUND2_DONE_PASS → OFFER_HANDOFF
  ↘  cmd_round1_defer / cmd_round2_defer
WAIT_RETURN（待回国后再约）
  ↓  cmd_wait_return_resume
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

### `cmd_import_candidate.py` — 导入已有候选人

使用飞书模板格式将候选人直接导入到指定阶段（适合补录历史数据）。

```bash
python3 intake/cmd_import_candidate.py --template "$(cat template.txt)"
```

**飞书模板格式示例：**
```
姓名：张三
邮箱：zhangsan@example.com
阶段：EXAM_SENT
岗位：量化研究员
学历：硕士
学校：复旦大学
工作年限：3
手机：138xxxxxxxx
来源：内推
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--template` | 是 | 飞书模板原文（包含姓名、邮箱等字段） |

---

### `cmd_send_cv.py` — 发送简历给老板/HR

将候选人简历 PDF 通过飞书文件消息发送。

```bash
# 发给老板（默认）
python3 intake/cmd_send_cv.py --talent-id t_xxx

# 发给 HR
python3 intake/cmd_send_cv.py --talent-id t_xxx --to hr

# 按姓名模糊查找
python3 intake/cmd_send_cv.py --name 张三
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 二选一 | 候选人 talent_id |
| `--name` | 二选一 | 候选人姓名（模糊匹配） |
| `--to` | 否 | `boss`（默认）或 `hr` |

---

## 一面 round1

### `cmd_round1_schedule.py` — 安排一面

提议一面时间，发邮件给候选人，状态变为 `ROUND1_SCHEDULING`（等候选人确认）。

```bash
python3 round1/cmd_round1_schedule.py --talent-id t_xxx --time "2026-05-10 14:00"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--time` | 是 | 一面时间，格式 `YYYY-MM-DD HH:MM` |
| `--actor` | 否 | 操作人（默认 `boss`，用于审计） |

---

### `cmd_round1_confirm.py` — 确认一面时间

候选人回复邮件确认后，将状态推进到 `ROUND1_SCHEDULED`，并创建飞书日历事件。

```bash
python3 round1/cmd_round1_confirm.py --talent-id t_xxx --round 1

# 超时默认确认（cron 调用）
python3 round1/cmd_round1_confirm.py --talent-id t_xxx --round 1 --auto
```

> 实际转发到 `interview/cmd_confirm.py --round 1`，参数相同。

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--round` | 是 | 面试轮次，填 `1` |
| `--auto` | 否 | 超时默认确认模式 |

---

### `cmd_round1_result.py` — 记录一面结果

> 转发到 `interview/cmd_result.py --round 1`，参数完全相同，见 [面试统一操作](#面试统一操作-interview)。

```bash
# 一面通过，发笔试
python3 round1/cmd_round1_result.py --talent-id t_xxx --result pass --email zhangsan@example.com

# 一面通过，直接安排二面（跳过笔试）
python3 round1/cmd_round1_result.py --talent-id t_xxx --result pass_direct --round2-time "2026-05-15 15:00"

# 一面未通过，保留档案
python3 round1/cmd_round1_result.py --talent-id t_xxx --result reject_keep

# 一面未通过，删除档案
python3 round1/cmd_round1_result.py --talent-id t_xxx --result reject_delete
```

---

### `cmd_round1_defer.py` — 暂缓一面

候选人暂时不在国内/上海时，进入统一 `WAIT_RETURN` 状态，删除日历，待回国后再恢复一面排期。

```bash
python3 round1/cmd_round1_defer.py --talent-id t_xxx
python3 round1/cmd_round1_defer.py --talent-id t_xxx --reason "候选人近期在海外"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--reason` | 否 | 暂缓原因 |
| `--actor` | 否 | 操作人（默认 `system`） |

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
| `--round2-time` | result=pass 时强烈建议 | 建议二面时间，格式 `YYYY-MM-DD HH:MM` |
| `--notes` | 否 | 备注（写入审计日志） |
| `--actor` | 否 | 执行人（默认 `system`） |

---

### `daily_exam_review.py` — 邮件自动扫描（cron）

每隔一定时间自动扫描邮箱，处理：笔试回复、一面/二面确认、已确认候选人的改期请求。结果通过飞书推送给老板。

```bash
# 手动触发（完整扫描，打印结果并推飞书）
python3 exam/daily_exam_review.py

# cron 模式（无结果时静默）
python3 exam/daily_exam_review.py --auto

# 只扫笔试回复（8h cron 专用）
python3 exam/daily_exam_review.py --auto --exam-only

# 只扫面试时间确认（6h cron 专用）
python3 exam/daily_exam_review.py --auto --interview-confirm-only

# 只扫已确认候选人的改期请求（2h cron 专用）
python3 exam/daily_exam_review.py --auto --reschedule-scan-only
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--auto` | 否 | cron 静默模式，无结果不输出 |
| `--exam-only` | 否 | 只扫笔试回复 |
| `--interview-confirm-only` | 否 | 只扫面试时间确认 |
| `--reschedule-scan-only` | 否 | 只扫改期请求 |

---

## 二面 round2

### `cmd_round2_confirm.py` — 确认二面时间

> 转发到 `interview/cmd_confirm.py --round 2`，参数相同。

```bash
python3 round2/cmd_round2_confirm.py --talent-id t_xxx

# 超时默认确认
python3 round2/cmd_round2_confirm.py --talent-id t_xxx --auto
```

---

### `cmd_round2_reschedule.py` — 二面改期（默认已确认）

> 转发到 `interview/cmd_reschedule.py --round 2 --confirmed`，参数相同。

```bash
# 改期并立刻确认新时间（默认行为）
python3 round2/cmd_round2_reschedule.py --talent-id t_xxx --time "2026-05-25 15:00"

# 改期但等候选人再次确认
python3 round2/cmd_round2_reschedule.py --talent-id t_xxx --time "2026-05-25 15:00" --no-confirm
```

---

### `cmd_round2_defer.py` — 暂缓二面

候选人暂时不在国内/上海，进入统一 `WAIT_RETURN` 状态，删除日历，发通知邮件。

```bash
python3 round2/cmd_round2_defer.py --talent-id t_xxx

python3 round2/cmd_round2_defer.py --talent-id t_xxx --reason "候选人近期不在国内"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--reason` | 否 | 暂缓原因（默认：候选人暂时不在国内/上海） |
| `--actor` | 否 | 操作人（默认 `system`） |

---

### `cmd_round2_result.py` — 记录二面结果

> 转发到 `interview/cmd_result.py --round 2`，参数相同，见 [面试统一操作](#面试统一操作-interview)。

```bash
# 二面通过
python3 round2/cmd_round2_result.py --talent-id t_xxx --result pass

# 结论待定（暂保留）
python3 round2/cmd_round2_result.py --talent-id t_xxx --result pending

# 未通过，保留
python3 round2/cmd_round2_result.py --talent-id t_xxx --result reject_keep

# 未通过，删除
python3 round2/cmd_round2_result.py --talent-id t_xxx --result reject_delete
```

---

## 面试统一操作 interview

> 这三个脚本是实际实现；`round1/round2` 目录下的同名脚本都是转发包装。

### `interview/cmd_confirm.py` — 确认面试时间

候选人回复确认后调用，推进阶段并创建飞书日历。

```bash
python3 interview/cmd_confirm.py --talent-id t_xxx --round 1
python3 interview/cmd_confirm.py --talent-id t_xxx --round 2
python3 interview/cmd_confirm.py --talent-id t_xxx --round 2 --auto  # 超时自动确认
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--round` | 是 | `1` 或 `2` |
| `--auto` | 否 | 超时默认确认 |

---

### `interview/cmd_result.py` — 记录面试结果

```bash
# 一面通过，发笔试邀请
python3 interview/cmd_result.py --talent-id t_xxx --round 1 --result pass --email zhangsan@example.com

# 一面通过，直接二面（跳过笔试）
python3 interview/cmd_result.py --talent-id t_xxx --round 1 --result pass_direct --round2-time "2026-05-15 15:00"

# 二面通过
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
| `--result` | 是 | `pass` / `pass_direct` / `pending` / `reject_keep` / `reject_delete` |
| `--email` | 否 | 候选人邮箱（一面 pass 时发笔试用） |
| `--round2-time` | 否 | pass_direct 时的二面时间 |
| `--notes` | 否 | 备注（审计日志） |
| `--skip-email` | 否 | 跳过自动发邮件 |
| `--actor` | 否 | 执行人（默认 `system`） |

---

### `interview/cmd_reschedule.py` — 面试改期

更新面试时间，删除旧日历，可选择是否立刻确认新时间。

```bash
# 改期并确认（老板已拍板）
python3 interview/cmd_reschedule.py --talent-id t_xxx --round 2 --time "2026-05-25 15:00" --confirmed

# 改期但等候选人再次确认
python3 interview/cmd_reschedule.py --talent-id t_xxx --round 2 --time "2026-05-25 15:00" --no-confirm
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--round` | 是 | `1` 或 `2` |
| `--time` | 是 | 新的面试时间，格式 `YYYY-MM-DD HH:MM` |
| `--confirmed` | 否 | 老板明确确认该时间（默认 `False`） |
| `--no-confirm` | 否 | 明确等候选人确认（与 `--confirmed` 互斥） |
| `--actor` | 否 | 操作人（默认 `boss`） |

---

## 通用管理 common

### `cmd_status.py` — 查询候选人状态

```bash
# 查单人详情（含审计历史）
python3 common/cmd_status.py --talent-id t_xxx

# 显示更多审计条目
python3 common/cmd_status.py --talent-id t_xxx --audit-lines 10

# 列出所有候选人
python3 common/cmd_status.py --all
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 二选一 | 查指定候选人 |
| `--all` | 二选一 | 列出全部候选人（简略） |
| `--audit-lines` | 否 | 显示最近 N 条审计，默认 5 |

---

### `cmd_wait_return_resume.py` — 恢复统一暂缓状态

把 `WAIT_RETURN` 候选人恢复到对应轮次的排期阶段。

```bash
python3 common/cmd_wait_return_resume.py --talent-id t_xxx
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--actor` | 否 | 操作人（默认 `system`） |

---

### `cmd_search.py` — 搜索候选人

```bash
# 按 talent_id / 邮箱 / 姓名模糊搜索
python3 common/cmd_search.py --query 张三

# 搜索并过滤阶段
python3 common/cmd_search.py --query 张三 --stage EXAM_SENT

# 列出所有进行中的候选人
python3 common/cmd_search.py --all-active
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--query` / `-q` | 二选一 | 搜索关键词 |
| `--all-active` | 二选一 | 列出所有进行中候选人 |
| `--stage` | 否 | 按阶段过滤（英文阶段代码） |

---

### `cmd_remove.py` — 物理删除候选人

**不可恢复**，必须加 `--confirm` 才会真正执行。

```bash
python3 common/cmd_remove.py --talent-id t_xxx --confirm
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--confirm` | 是 | 确认执行（缺少则拒绝） |

---

### `cmd_reschedule_request.py` — 处理候选人主动发起的改期请求

候选人主动发邮件申请改期时，手动调用此命令撤销已确认状态并通知老板。

```bash
# 候选人只申请改期，未提新时间
python3 common/cmd_reschedule_request.py --talent-id t_xxx --round 2 --reason "临时有事"

# 候选人提出了新时间
python3 common/cmd_reschedule_request.py --talent-id t_xxx --round 1 --reason "出差" --new-time "2026-05-12 10:00"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--round` | 是 | `1` 或 `2` |
| `--reason` | 否 | 改期原因摘要 |
| `--new-time` | 否 | 候选人提出的新时间（可选） |
| `--actor` | 否 | 操作人（默认 `system`） |

---

### `cmd_finalize_interview_time.py` — 老板最终确认面试时间

当老板收到飞书催认通知后，调用此命令正式落定面试时间。

```bash
# 自动判断待确认轮次并直接确认
python3 common/cmd_finalize_interview_time.py --talent-id t_xxx

# 明确指定轮次
python3 common/cmd_finalize_interview_time.py --talent-id t_xxx --round 2

# 同时更新时间（改时间后确认）
python3 common/cmd_finalize_interview_time.py --talent-id t_xxx --round 2 --time "2026-05-20 15:00"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--round` | 否 | `1` 或 `2`（默认自动判断） |
| `--time` | 否 | 最终确认时间（若与已有时间不同则触发改期逻辑） |

---

### `cmd_interview_reminder.py` — 面试结束后催问评价（cron）

无参数，由 cron 自动触发。检查一面/二面是否已过时间但还未记录结果，通过飞书提醒老板。

```bash
python3 common/cmd_interview_reminder.py
```

---

## 定时任务 cron

### `cron_runner.py` — 统一 cron 入口

无参数，每 5 分钟由系统 cron 调用，依次执行：

1. `daily_exam_review.py --auto`（扫邮件）
2. `cmd_interview_reminder.py`（催评价）

有输出时自动推送飞书给老板。

```bash
python3 cron_runner.py
```

---

## 阶段代码速查

| 阶段代码 | 中文含义 |
|---------|---------|
| `NEW` | 新建 |
| `ROUND1_SCHEDULING` | 一面排期中（等候选人确认） |
| `ROUND1_SCHEDULED` | 一面已安排 |
| `ROUND1_DONE_PASS` | 一面通过 |
| `ROUND1_DONE_REJECT_KEEP` | 一面未通过（保留档案） |
| `ROUND1_DONE_REJECT_DELETE` | 一面未通过（已删除） |
| `EXAM_SENT` | 笔试已发送 |
| `EXAM_REVIEWED` | 笔试已审阅 |
| `WAIT_RETURN` | 待回国后再约 |
| `ROUND2_SCHEDULING` | 二面排期中（等候选人确认） |
| `ROUND2_SCHEDULED` | 二面已确认 |
| `ROUND2_DONE_PENDING` | 二面结束，结论待定 |
| `ROUND2_DONE_PASS` | 二面通过 |
| `ROUND2_DONE_REJECT_KEEP` | 二面未通过（保留档案） |
| `ROUND2_DONE_REJECT_DELETE` | 二面未通过（已删除） |
| `OFFER_HANDOFF` | 等待发放 Offer |
