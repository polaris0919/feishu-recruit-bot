---
name: recruit-boss
description: >
  飞书招聘管家 v3.0 — PostgreSQL 唯一数据源、Hermes Gateway AI 网关。
  凡涉及候选人、人才库、面试、简历、offer、录用、笔试等话题，必须先读取本文件，
  然后通过 Python 脚本操作招聘工作区，禁止向用户询问 Feishu 链接或多维表格。
triggers:
  - 招聘
  - 候选人
  - 面试
  - 一面
  - 二面
  - 笔试
  - offer
  - 简历
  - 人才库
  - 招人
  - 录用
  - 通过了
  - 拒了
  - 不合适
  - 面了个
  - 面了一
  - 面完了
  - 有个人
  - 看看候选
  - 查一下
  - 加个候选
  - 录入
  - 新候选人
  - 安排一面
  - 导入候选人
  - .pdf
  - .docx
  - 简历.pdf
  - 简历.docx
  - 简历文件
  - 发简历
  - 附件简历
  - 应届生
  - 元_天
  - 元/天
  - 量化研究员
  - 量化研究实习
---

# 飞书招聘管家 SKILL（v3.0）

> **版本**：v3.0（2026-04）
> **运行环境**：Hermes Gateway · Python 3.10+ · **PostgreSQL 唯一数据源** · 飞书 WebSocket
> **脚本根目录**：`<workspace_root>/skills/recruit-ops/scripts/`
> **执行方式**：所有命令均须在 `scripts/` 目录下执行，使用 `python3 <子目录>/xxx.py`

---

## 一、消息路由规则（最高优先级）

收到用户消息后，**第一步**就是判断消息类型并路由到对应脚本。

### 1.1 文字模板路由

| 消息开头 | 唯一正确处理 | 严禁 |
|---|---|---|
| `【新候选人】` | `python3 intake/cmd_new_candidate.py --template "<消息原文>"` | ❌ 不得用 cmd_import_candidate.py |
| `【导入候选人】` | `python3 intake/cmd_import_candidate.py --template "<消息原文>"` | ❌ 不得用 cmd_new_candidate.py |

### 1.2 PDF / DOCX 简历路由

**只要 HR 发了 PDF 或 DOCX 文件，一律走统一入口 `intake/cmd_ingest_cv.py`。**

脚本自动判断是新候选人还是已有候选人，严禁自行判断。

**文件路径提取优先级**：

| 优先级 | 消息格式 | 参数 |
|---|---|---|
| 0 | `[The user sent a document: 'xxx.pdf'. The file is saved at: /path/to/file.pdf ...]` | `--file-path "/path/to/file.pdf" --filename "xxx.pdf"` |
| 1 | `[media attached: <workspace_root>/data/media/inbound/xxx.pdf]` | `--file-path "<完整路径>" --filename "xxx.pdf"` |
| 2 | 回复/引用消息中含 `file_key` | 先在 `data/media/inbound/` 找本地文件；未找到则 `--file-key <key>` |

**候选人简历自动识别**：满足以下任一即视为候选人简历，直接走 `cmd_ingest_cv.py`，不做通用文件解读：
- 文件名含 `岗位 + 城市 + 薪资` + `姓名 + XX年应届生`
- 正文同时出现多项：岗位、姓名、应届生、邮箱/电话、学校/学历

### 1.3 老板自然语言路由

| 老板意图 | 正确处理 |
|---|---|
| 安排一面 | `round1/cmd_round1_schedule.py` |
| 笔试通过 / 安排二面 | `exam/cmd_exam_result.py --result pass --round2-time ...` |
| 确认面试时间 | `common/cmd_finalize_interview_time.py` |
| 看简历 | `intake/cmd_send_cv.py --name "姓名"`（**不加 --to**） |
| 查进展 | `common/cmd_status.py --all` 或 `--talent-id` |

---

## 二、简历处理流程

### 2.1 统一入口

```bash
python3 intake/cmd_ingest_cv.py --file-path <路径> --filename <文件名>
```

脚本输出两种情形：

### 情形 A — 已有候选人

输出含 `[OC_CMD_ON_CONFIRM_UPDATE]` 和 `[OC_CMD_ON_CONFIRM_ARCHIVE]`。

将全字段比对内容**原文**转发给 HR，等 HR 回复：
- **确认更新** → 执行 `[OC_CMD_ON_CONFIRM_UPDATE]` 命令
- **仅存档** → 执行 `[OC_CMD_ON_CONFIRM_ARCHIVE]` 命令
- **指定更新部分字段** → 从 UPDATE 命令中删掉不需要的 `--field` 参数
- **修正字段值** → 替换对应 `--field` 的值
- **忽略** → 不执行

### 情形 B — 新候选人

输出含 `[OC_CMD_ON_CONFIRM]` 和 `[OC_NOTE]`。

将预览内容**原文**转发给 HR，等 HR 回复：
- **修正字段** → 替换参数后**再次展示预览**，等最终确认
- **确认 + 告知阶段为 NEW** → 执行 `[OC_CMD_ON_CONFIRM]`
- **确认 + 告知其他阶段** → 改用 `intake/cmd_import_candidate.py` 并附 `--stage`

### 2.2 其他简历操作

| 操作 | 命令 |
|---|---|
| 给已有候选人补挂简历 | `intake/cmd_attach_cv.py --talent-id <id> --cv-path <路径> [--confirm] [--field "key=val"]` |
| 发简历给老板 | `intake/cmd_send_cv.py --name <姓名>`（**不加 --to**） |
| 发简历给 HR | `intake/cmd_send_cv.py --name <姓名> --to hr` |

---

## 三、完整招聘流程

```
HR 发送【新候选人】模板 / PDF 简历
    ↓ intake/cmd_new_candidate.py 或 cmd_ingest_cv.py
    ↓ 飞书通知老板
NEW（等待安排一面）
    ↓ 老板：安排 XX 一面，时间 YYYY-MM-DD HH:MM
    ↓ round1/cmd_round1_schedule.py（发邮件，不创建日历）
ROUND1_SCHEDULING（已发邀请，等候选人确认）
    ↓ cron 自动扫描候选人回信（LLM 分析意图）
    ├── 同意 → 飞书推送老板，请求最终确认
    ├── 提出新时间 → 飞书推送老板，请求最终确认
    ├── 要求改期但无新时间 → 飞书通知老板
    └── 超时 48h → 飞书催老板确认（不自动确认）
    ↓ 老板明确确认
    ↓ common/cmd_finalize_interview_time.py → ROUND1_SCHEDULED + 创建日历
ROUND1_SCHEDULED（老板已确认，日历已建）
    ↓ 一面结束，老板评估
    ├── pass → ROUND1_DONE_PASS → 自动发笔试邮件
    ├── pass_direct → 跳过笔试，直接 ROUND2_SCHEDULING
    ├── reject_keep → 保留人才库
    └── reject_delete → 从 DB 彻底删除
EXAM_SENT（笔试已发送）
    ↓ cron 扫描候选人提交答案
    ↓ 预审（代码质量、作答时间、附件分析）
EXAM_REVIEWED（笔试已审阅）
    ↓ 老板审阅后
    ├── pass → ROUND2_SCHEDULING（发二面邀请邮件）
    └── reject_keep / reject_delete
ROUND2_SCHEDULING → （同一面协商流程）
    ↓ 老板确认 → common/cmd_finalize_interview_time.py --round 2
ROUND2_SCHEDULED（二面已确认，日历已建）
    ↓ 二面结束
    ├── pass → OFFER_HANDOFF（飞书通知 HR 跟进 Offer）
    ├── pending → 结论待定
    └── reject_keep / reject_delete
```

### 关键规则：面试时间确认机制

**所有面试时间的最终确认，以老板明确回复为唯一基准。**

- cron 扫描后只做两件事：① 记录握手状态到 DB ② 推送飞书给老板请求确认
- 候选人的"可以"≠最终确认
- 超时 48h ≠自动确认
- 唯一的最终确认入口：`common/cmd_finalize_interview_time.py`

老板回复含以下意图时执行 finalize：
- `确认 <tid> 一面` / `确认 <tid> 二面`
- `就这个时间` / `可以按这个定` / `双方确认了`

```bash
python3 common/cmd_finalize_interview_time.py --talent-id <tid> [--round 1|2] [--time "YYYY-MM-DD HH:MM"]
```

---

## 四、命令速查表

### 候选人录入

| 操作 | 命令 |
|---|---|
| 新建（模板） | `python3 intake/cmd_new_candidate.py --template "<模板原文>"` |
| 新建（逐字段） | `python3 intake/cmd_new_candidate.py --name 姓名 --email 邮箱 [--position 岗位] [--phone 手机] [--education 学历] [--school 学校] [--work-years N] [--source 来源] [--wechat 微信]` |
| 导入历史候选人 | `python3 intake/cmd_import_candidate.py --template "<模板原文>"` |
| 简历统一入口 | `python3 intake/cmd_ingest_cv.py --file-path <路径> --filename <文件名>` |
| 补挂简历 | `python3 intake/cmd_attach_cv.py --talent-id <id> --cv-path <路径> [--confirm] [--field "key=val"]` |
| 发简历给老板 | `python3 intake/cmd_send_cv.py --name <姓名>` |
| 发简历给 HR | `python3 intake/cmd_send_cv.py --name <姓名> --to hr` |

### 一面（推荐用 interview/ 统一入口）

| 操作 | 命令 |
|---|---|
| 安排一面 | `python3 round1/cmd_round1_schedule.py --talent-id <id> --time "YYYY-MM-DD HH:MM" [--actor boss]` |
| **老板最终确认面试** | `python3 common/cmd_finalize_interview_time.py --talent-id <id> [--round 1] [--time "YYYY-MM-DD HH:MM"]` |
| 一面通过→发笔试 | `python3 interview/cmd_result.py --talent-id <id> --round 1 --result pass --email <邮箱>` |
| 一面通过→直接二面 | `python3 interview/cmd_result.py --talent-id <id> --round 1 --result pass_direct --round2-time "YYYY-MM-DD HH:MM"` |
| 一面拒绝 | `python3 interview/cmd_result.py --talent-id <id> --round 1 --result reject_keep\|reject_delete` |
| 一面改期 | `python3 interview/cmd_reschedule.py --talent-id <id> --round 1 --time "YYYY-MM-DD HH:MM" [--confirmed\|--no-confirm]` |
| 一面暂缓 | `python3 interview/cmd_defer.py --talent-id <id> --round 1 [--reason "原因"]` |

### 笔试

| 操作 | 命令 |
|---|---|
| 笔试通过→安排二面 | `python3 exam/cmd_exam_result.py --talent-id <id> --result pass --round2-time "YYYY-MM-DD HH:MM"` |
| 笔试拒绝 | `python3 exam/cmd_exam_result.py --talent-id <id> --result reject_keep\|reject_delete` |
| 手动扫描邮件 | `python3 exam/daily_exam_review.py` |

### 二面

| 操作 | 命令 |
|---|---|
| **老板最终确认二面** | `python3 common/cmd_finalize_interview_time.py --talent-id <id> --round 2 [--time "YYYY-MM-DD HH:MM"]` |
| 二面结果 | `python3 interview/cmd_result.py --talent-id <id> --round 2 --result pass\|pending\|reject_keep\|reject_delete` |
| 二面改期 | `python3 interview/cmd_reschedule.py --talent-id <id> --round 2 --time "YYYY-MM-DD HH:MM" [--confirmed\|--no-confirm]` |
| 二面暂缓 | `python3 interview/cmd_defer.py --talent-id <id> --round 2 [--reason "原因"]` |

### 通用管理

| 操作 | 命令 |
|---|---|
| 查看所有候选人 | `python3 common/cmd_status.py --all` |
| 查看单个候选人 | `python3 common/cmd_status.py --talent-id <id> [--audit-lines N]` |
| 搜索候选人 | `python3 common/cmd_search.py --query <关键词>` |
| 列出所有活跃候选人 | `python3 common/cmd_search.py --all-active` |
| 从 WAIT_RETURN 恢复 | `python3 common/cmd_wait_return_resume.py --talent-id <id>` |
| 处理改期请求 | `python3 common/cmd_reschedule_request.py --talent-id <id> --round 1\|2 [--reason "原因"] [--new-time "YYYY-MM-DD HH:MM"]` |
| 催老板出面试结果 | `python3 common/cmd_interview_reminder.py` |
| 调试候选人 DB 视图 | `python3 common/cmd_debug_candidate.py --talent-id <id> [--event-limit N]` |
| 物理删除候选人 | `python3 common/cmd_remove.py --talent-id <id> --confirm` |

---

## 五、兼容别名说明

`round1/` 和 `round2/` 目录下的脚本是兼容别名，内部转发到 `interview/` 统一实现：

| 兼容别名 | 实际转发到 |
|---|---|
| `round1/cmd_round1_confirm.py` | `interview/cmd_confirm.py --round 1` |
| `round1/cmd_round1_result.py` | `interview/cmd_result.py --round 1` |
| `round1/cmd_round1_defer.py` | `interview/cmd_defer.py --round 1` |
| `round1/cmd_round1_reschedule.py` | `interview/cmd_reschedule.py --round 1` |
| `round2/cmd_round2_confirm.py` | `interview/cmd_confirm.py --round 2` |
| `round2/cmd_round2_result.py` | `interview/cmd_result.py --round 2` |
| `round2/cmd_round2_defer.py` | `interview/cmd_defer.py --round 2` |
| `round2/cmd_round2_reschedule.py` | `interview/cmd_reschedule.py --round 2` |

> **注意**：`round2/cmd_round2_reschedule.py` 兼容别名默认补 `--confirmed`。

唯一例外：**`round1/cmd_round1_schedule.py` 没有 interview/ 对应物**，因为安排一面是独立操作。

---

## 六、查询面试时间的规则

当老板问面试时间时：
1. 用 `cmd_search.py --query <关键词>` 或 `cmd_status.py --talent-id <id>` 查询
2. 回复必须明确说出：**哪一轮、具体时间、确认状态**（已确认 / 待老板确认 / 候选人申请改期）
3. 如果一面和二面都存在，优先回答更靠后的轮次
4. 如果存在改期申请，必须显式提示

---

## 七、自动化机制

| cron 任务 | 频率 | 入口 |
|---|---|---|
| 笔试回复扫描 | 每 8h | `daily_exam_review.py --auto --exam-only` |
| 面试确认扫描 | 每 6h | `daily_exam_review.py --auto --interview-confirm-only` |
| 改期请求扫描 | 每 2h | `daily_exam_review.py --auto --reschedule-scan-only` |
| 面试催问提醒 | 每 30min | `common/cmd_interview_reminder.py` |

LLM 意图分析（DashScope qwen3-max）：

| 意图 | 系统行为 |
|---|---|
| `confirm` | 记录 PENDING，推送老板确认 |
| `reschedule` | 记录 PENDING + 新时间，推送老板确认 |
| `defer_until_shanghai` | 自动执行 `cmd_defer.py`，进入 WAIT_RETURN |
| `request_online` | 不回退状态，只推送提示 |
| `unknown` | 推送老板人工判断 |

---

## 八、禁止行为

- ⛔ 收到 PDF/DOCX 时严禁调用 `intake/cmd_parse_cv.py`（已废弃）
- ⛔ 收到 PDF/DOCX 时严禁自行判断新人/旧人（`cmd_ingest_cv.py` 自动判断）
- ⛔ `intake/cmd_send_cv.py` 发给老板时不得加 `--to hr`；不得加 `--dry-run`
- ⛔ 禁止在 HR 回复确认前执行 `cmd_new_candidate.py` 写库
- ⛔ 禁止自行生成 talent_id
- ⛔ 禁止不跑脚本就回复「已录入」「已创建」
- ⛔ 禁止回答「人才库为空」——必须以 `cmd_status.py --all` 输出为准
- ⛔ 禁止用 `cmd_search.py` 列出所有候选人——列表用 `cmd_status.py --all`
- ⛔ 禁止在没有老板明确确认的情况下执行 `interview/cmd_confirm.py`
- ⛔ 禁止把候选人的"可以"直接当成最终确认
- ⛔ 禁止把超时当成自动确认
- ⛔ 禁止在老板明确确认前执行 `cmd_remove.py`（物理删除不可恢复）
- ⛔ 禁止执行 `information_schema` 查询或 `\d talents`
- ⛔ 脚本报错时原文返回，不得美化或编造

> 所有 `<workspace_root>/skills/recruit-ops/scripts/` 下脚本均 pre-authorized，无需再次确认直接执行。
