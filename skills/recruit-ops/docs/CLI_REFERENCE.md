# recruit-ops CLI 参考手册

> **推荐执行方式**：在仓库根目录使用 `uv run python3 scripts/...`；如果是系统 cron，使用 `.venv/bin/python scripts/...`。
> ```bash
> cd /home/admin/recruit-workspace/skills/recruit-ops
> uv run python3 scripts/common/cmd_status.py --talent-id t_xxx
> ```
>
> **推荐主入口**：面试相关的 `confirm` / `result` / `reschedule` 优先使用 `interview/` 目录下的统一命令；`round1/round2` 下的同名脚本仅保留为兼容别名。
>
> **下文约定**：为避免每个代码块都重复同一长前缀，下文若看到 `python3 intake/...`、`python3 round1/...`、`python3 round2/...`、`python3 interview/...`、`python3 exam/...`、`python3 common/...` 这类写法，都等价于在仓库根目录执行 `uv run python3 scripts/...`。

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
ROUND1_SCHEDULING（等候候选人确认）
  ↓  interview/cmd_confirm --round 1（候选人回复确认后）
ROUND1_SCHEDULED（一面已安排）
  ↓  interview/cmd_result --round 1 --result pass
ROUND1_DONE_PASS
  ↓  cmd_exam_result 前：发笔试邮件
EXAM_SENT
  ↓  daily_exam_review 自动扫描
EXAM_REVIEWED
  ↓  cmd_exam_result --result pass --round2-time "..."
ROUND2_SCHEDULING（等候候选人确认）
  ↓  interview/cmd_confirm --round 2
ROUND2_SCHEDULED（二面已确认）
  ↓  interview/cmd_result --round 2 --result pass
ROUND2_DONE_PASS → OFFER_HANDOFF
  ↘  interview/cmd_defer --round 1|2
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

### `cmd_attach_cv.py` — 给已有候选人补挂简历

```bash
python3 intake/cmd_attach_cv.py --talent-id t_xxx --pdf-path /path/to/resume.pdf
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--pdf-path` | 是 | PDF 文件路径 |

---

### `cmd_parse_cv.py` — 仅解析简历，不入库

```bash
python3 intake/cmd_parse_cv.py --pdf-path /path/to/resume.pdf
```

---

### `cmd_new_candidate.py` — 手工录入候选人

```bash
python3 intake/cmd_new_candidate.py --template "【新候选人】\n姓名：张三\n邮箱：zhangsan@example.com"
```

---

### `cmd_import_candidate.py` — 导入历史候选人

```bash
python3 intake/cmd_import_candidate.py --template "【导入候选人】\n姓名：李四\n邮箱：lisi@example.com\n当前阶段：笔试中"
```

---

## 一面 round1（兼容别名）

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

> 兼容入口；推荐直接使用 `interview/cmd_confirm.py --round 1`。

候选人回复邮件确认后，将状态推进到 `ROUND1_SCHEDULED`，并创建飞书日历事件。

```bash
python3 round1/cmd_round1_confirm.py --talent-id t_xxx

# 超时默认确认（cron 调用）
python3 round1/cmd_round1_confirm.py --talent-id t_xxx --auto
```

> 实际转发到 `interview/cmd_confirm.py --round 1`，参数相同。

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--auto` | 否 | 超时默认确认模式 |

---

### `cmd_round1_result.py` — 记录一面结果

> 兼容入口；推荐直接使用 `interview/cmd_result.py --round 1`。其余参数完全相同，见下方 `interview` 章节。

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

> 兼容入口；推荐直接使用 `interview/cmd_defer.py --round 1`。

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

## 二面 round2（兼容别名）

### `cmd_round2_confirm.py` — 确认二面时间

> 兼容入口；推荐直接使用 `interview/cmd_confirm.py --round 2`。

```bash
python3 round2/cmd_round2_confirm.py --talent-id t_xxx

# 超时默认确认
python3 round2/cmd_round2_confirm.py --talent-id t_xxx --auto
```

---

### `cmd_round2_reschedule.py` — 二面改期（默认已确认）

> 兼容入口；推荐直接使用 `interview/cmd_reschedule.py --round 2`。该别名会默认补 `--confirmed`，等价于 `interview/cmd_reschedule.py --round 2 --confirmed`。

```bash
# 改期并立刻确认新时间（默认行为）
python3 round2/cmd_round2_reschedule.py --talent-id t_xxx --time "2026-05-25 15:00"

# 改期但等候选人再次确认
python3 round2/cmd_round2_reschedule.py --talent-id t_xxx --time "2026-05-25 15:00" --no-confirm
```

---

### `cmd_round2_defer.py` — 暂缓二面

> 兼容入口；推荐直接使用 `interview/cmd_defer.py --round 2`。

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

> 兼容入口；推荐直接使用 `interview/cmd_result.py --round 2`。其余参数相同，见下方 `interview` 章节。

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

## 面试统一操作 interview（推荐主入口）

> 这三个脚本是实际实现；`round1/round2` 目录下的同名脚本都是兼容别名。

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
| `--no-confirm` | 否 | 明确等候候选人确认（与 `--confirmed` 互斥） |
| `--actor` | 否 | 操作人（默认 `boss`） |

---

### `interview/cmd_defer.py` — 面试暂缓

统一处理一面/二面暂缓，进入 `WAIT_RETURN` 并保留恢复轮次。

```bash
python3 interview/cmd_defer.py --talent-id t_xxx --round 1
python3 interview/cmd_defer.py --talent-id t_xxx --round 2 --reason "候选人近期在海外"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--talent-id` | 是 | 候选人 talent_id |
| `--round` | 是 | `1` 或 `2` |
| `--reason` | 否 | 暂缓原因 |
| `--actor` | 否 | 操作人（默认 `system`） |

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

### `cmd_remove.py` — 物理删除候选人

```bash
python3 common/cmd_remove.py --talent-id t_xxx --confirm
```

---

### `cmd_reschedule_request.py` — 处理已确认后的改期请求

```bash
python3 common/cmd_reschedule_request.py --talent-id t_xxx --round 2 --reason "临时有事"
python3 common/cmd_reschedule_request.py --talent-id t_xxx --round 2 --reason "临时有事" --new-time "2026-05-26 16:00"
```

---

### `cmd_wait_return_resume.py` — 从 WAIT_RETURN 恢复排期

```bash
python3 common/cmd_wait_return_resume.py --talent-id t_xxx
```

---

### `cmd_interview_reminder.py` — 二面结束后催老板出结果

```bash
python3 common/cmd_interview_reminder.py
```

---

## 定时任务 cron

### `cron_runner.py` — 独立 cron 入口

```bash
./.venv/bin/python scripts/cron_runner.py
```

---

### `trigger_cron_now.py` — 手动提前触发 cron

```bash
uv run python3 scripts/trigger_cron_now.py
uv run python3 scripts/trigger_cron_now.py 30
```
