---
name: recruit-boss
description: >
  本地招聘管理系统（PostgreSQL 数据库后端，NOT Feishu Bitable/多维表格）。
  凡涉及候选人、人才库、面试、简历、offer、录用、笔试等话题，必须先读取本文件，
  然后通过 Python 脚本操作本地数据库，禁止向用户询问 Feishu 链接或多维表格。
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
---

# 招聘管理系统 SKILL

**脚本目录：** `~/.openclaw/workspace/skills/recruit-ops/scripts/`

所有操作均通过下表脚本完成，执行后把 stdout 原文返回给用户。

---

## 🔴 第一优先级：消息路由规则（读到这里必须立刻判断）

收到用户消息后，**第一步**就是检查消息开头：

| 消息开头 | 唯一正确命令 | 严禁使用 |
|---|---|---|
| `【新候选人】` | `python3 cmd_new_candidate.py --template "<消息原文>"` | ❌ 绝对不能用 cmd_import_candidate.py |
| `【导入候选人】` | `python3 cmd_import_candidate.py --template "<消息原文>"` | ❌ 绝对不能用 cmd_new_candidate.py |

**【导入候选人】的处理规则（必须背下来）：**
- 消息含 `【导入候选人】` → 无条件执行 `cmd_import_candidate.py`
- 不得问"是否需要调整阶段"
- 不得执行 `cmd_new_candidate.py`
- 不得把阶段设成 NEW
- `cmd_import_candidate.py` 会自动处理阶段、写 DB、发飞书通知，**一步完成**

---

---

## 📋 HR 候选人录入模板（飞书触发）

当 HR 发送以 `【新候选人】` 开头的飞书消息时，**按顺序执行以下两步，缺一不可**：

**第一步**：录入候选人
```
python3 cmd_new_candidate.py --template "<原始消息内容>"
```

**第二步**：从第一步输出中提取 talent_id、姓名、邮箱，然后执行以下命令通知老板（将 `<...>` 替换为实际值）：
```
python3 -c "
import sys; sys.path.insert(0, '.')
import feishu_notify as fn
fn.send_text('[新候选人已录入]\ntalent_id: <talent_id>\n姓名：<姓名>\n邮箱：<邮箱>\n岗位：<岗位>\n\n请安排一面时间，对我说：\n  安排 <姓名> 一面，时间是 YYYY-MM-DD HH:MM')
"
```

脚本自动解析所有字段、校验必填项（姓名+邮箱），**两步都完成后才算录入成功**。

**模板格式**（HR 每次按此格式发送）：
```
【新候选人】
姓名：张三
邮箱：zhangsan@example.com
电话：13800138000
微信：zhangsan_wx
应聘职位：量化研究实习生
学历：硕士
毕业院校：复旦大学
工作年限：0
来源渠道：Boss直聘
简历摘要：金融工程背景，熟悉Python量化策略开发
```

---

## 📥 HR 导入已有候选人（飞书触发）

当 HR 发送以 `【导入候选人】` 开头的飞书消息时，直接执行（**脚本自动通知老板，无需第二步**）：

```
python3 cmd_import_candidate.py --template "<原始消息内容>"
```

**模板格式**（HR 每次按此格式发送，每条消息一位候选人）：
```
【导入候选人】
姓名：张三
邮箱：zhangsan@example.com
电话：13800000000（选填）
岗位：量化研究实习生（选填）
学历：硕士（选填）
院校：复旦大学（选填）
来源：猎头（选填）
当前阶段：笔试中
一面时间：2026-03-15 14:00（一面邀请中/已确认时必填）
二面时间：2026-03-25 14:00（二面邀请中/已确认时必填）
```

**阶段填写说明**（`当前阶段` 字段可填写以下关键词）：

| HR 填写 | 含义 | 额外必填 |
|---|---|---|
| 新候选人 / 待安排一面 | 刚收到简历，尚未安排面试 | — |
| 一面邀请中 | 已发一面邀请，等待候选人确认 | 一面时间 |
| 一面已确认 | 一面时间已确认，等待面试 | 一面时间 |
| 笔试中 | 一面通过，笔试已发出，等候提交 | 一面时间（建议填） |
| 待安排二面 | 笔试已审完，等待安排二面 | — |
| 二面邀请中 | 已发二面邀请，等待候选人确认 | 二面时间 |
| 二面已确认 | 二面时间已确认，等待面试 | 二面时间 |

脚本自动：生成 talent_id、设置阶段、写入 DB（round1/round2 confirmed 标记）、飞书通知老板。

> ⚠️ **重要**：`cmd_import_candidate.py` **不会发送任何邮件**（笔试/面试邀请均跳过）。这是补录已有候选人的正确方式。
> 禁止用 `cmd_round1_result.py` 来"录入已在笔试阶段的候选人"——那会重复发送笔试邮件。

---

## 🛠 命令速查表

| 操作 | 命令 |
|------|------|
| 新建候选人（逐字段） | `python3 cmd_new_candidate.py --name 姓名 --email 邮箱 [--position 岗位] [--phone 手机] [--education 学历] [--school 学校] [--work-years 年] [--source 来源] [--wechat 微信]` |
| 新建候选人（模板） | `python3 cmd_new_candidate.py --template "<模板原文>"` |
| **导入已有候选人（指定阶段）** | `python3 cmd_import_candidate.py --template "<模板原文>"` |
| **安排一面时间** | `python3 cmd_round1_schedule.py --talent-id <id> --time "YYYY-MM-DD HH:MM"` |
| **重约一面时间** | `python3 cmd_round1_reschedule.py --talent-id <id> --time "YYYY-MM-DD HH:MM"` |
| **重约一面时间（老板最终拍板）** | `python3 cmd_round1_reschedule.py --talent-id <id> --time "YYYY-MM-DD HH:MM" --confirmed` ← 老板说"就定这个时间/不用候选人再确认"时用 |
| **确认一面时间** | `python3 cmd_round1_confirm.py --talent-id <id>` |
| 查单个候选人 | `python3 cmd_status.py --talent-id <id>` |
| 列出所有候选人 | `python3 cmd_status.py --all` |
| 搜索候选人 | `python3 cmd_search.py --query <关键词>` |
| 扫邮件（手动） | `python3 daily_exam_review.py` |
| 一面通过→发笔试 | `python3 cmd_round1_result.py --talent-id <id> --result pass --email <邮箱> [--notes "评价"]` |
| 一面通过→直接二面 | `python3 cmd_round1_result.py --talent-id <id> --result pass_direct --email <邮箱> --round2-time "YYYY-MM-DD HH:MM" [--interviewer 面试官] [--notes "评价"]` |
| 一面拒绝 | `python3 cmd_round1_result.py --talent-id <id> --result reject_keep\|reject_delete [--notes "评价"]` |
| 笔试结果→发二面 | `python3 cmd_exam_result.py --talent-id <id> --result pass --round2-time "YYYY-MM-DD HH:MM" [--notes "评价"]` |
| 笔试拒绝 | `python3 cmd_exam_result.py --talent-id <id> --result reject_keep\|reject_delete [--notes "评价"]` |
| 二面结束待定 | `python3 cmd_round2_result.py --talent-id <id> --result pending [--notes "评价"]` |
| 二面结果 | `python3 cmd_round2_result.py --talent-id <id> --result pass\|reject_keep\|reject_delete [--notes "评价"]` |
| 重新约二面时间 | `python3 cmd_round2_reschedule.py --talent-id <id> --time "YYYY-MM-DD HH:MM" [--interviewer 面试官]` |
| 移除候选人 | `python3 cmd_remove.py --talent-id <id> --confirm` |

---

## 📋 完整招聘流程

```
HR 发送【新候选人】模板
    ↓ cmd_new_candidate.py 自动录入
    ↓ 飞书通知老板
NEW（等待安排一面）
    ↓ 老板：安排 XX 一面，时间是 YYYY-MM-DD HH:MM
    ↓ cmd_round1_schedule.py
ROUND1_SCHEDULING（已发邀请，等候选人确认）
    ↓ 候选人确认（邮件扫描LLM分析）
    ├── 确认 → cmd_round1_confirm.py → ROUND1_SCHEDULED（创建老板日历）
    ├── 改期 → 老板执行 cmd_round1_reschedule.py → 重发邀请
    └── 超时48h → cmd_round1_confirm.py --auto → ROUND1_SCHEDULED
ROUND1_SCHEDULED（日历已建，等待一面）
    ↓ 一面结束，老板评估
    ├── reject_keep/reject_delete → 结束
    ├── pass → EXAM_PENDING（自动发笔试邮件）
    │               ↓ 候选人提交作业（每8h扫描）
    │               ↓ EXAM_REVIEWED
    │               ↓ 老板：笔试通过 → ROUND2_SCHEDULED（发二面邀请+日历）
    │               └── 笔试拒绝 → 结束
    └── pass_direct → ROUND2_SCHEDULED（跳过笔试，直接安排二面）
ROUND2_SCHEDULED
    ↓ 候选人确认（每8h扫描+LLM分析，48h超时自动确认）
    ↓ 二面结束
    ├── pass → OFFER_HANDOFF（飞书通知 HR 处理 Offer）
    └── reject → 结束
```

> **`pass_direct`**：仅在老板明确说「直接二面/不用笔试」时使用，必须提供 `--round2-time`。

---

## 🗓 面试时间确认机制（一面 & 二面通用）

cron 每8小时自动扫描（`daily_exam_review.py --auto`），扫描结果按以下规则处理：

**`[候选人回信]` intent=confirm** — 执行确认：
- 一面：`python3 cmd_round1_confirm.py --talent-id <tid>`
- 二面：`python3 cmd_round2_confirm.py --talent-id <tid>`

**`[候选人回信]` intent=reschedule + 有新时间** — 执行改期：
- 一面：`python3 cmd_round1_reschedule.py --talent-id <tid> --time "<新时间>"`
- 二面：`python3 cmd_round2_reschedule.py --talent-id <tid> --time "<新时间>"`

**`[候选人回信]` intent=reschedule + 无新时间** — 飞书告知老板，让老板联系候选人

**`[超时默认确认]`** — 直接执行：
- 一面：`python3 cmd_round1_confirm.py --talent-id <tid> --auto`
- 二面：`python3 cmd_round2_confirm.py --talent-id <tid> --auto`

---

## 🚫 禁止行为（违反即为系统错误）

- 禁止自行生成 talent_id（任何格式）——只有 `cmd_new_candidate.py` 可以生成
- 禁止不跑脚本就直接回复「已录入」「已创建」「已更新」
- 禁止回答「人才库为空」「没有候选人」——必须以 `cmd_status.py --all` 输出为准
- 禁止用 `cmd_search.py` 列出所有候选人——列表必须用 `cmd_status.py --all`
- 禁止跳过流程步骤（如未执行 round1_schedule 就直接设 ROUND1_SCHEDULING）
- 禁止在老板明确确认前执行 `cmd_remove`（物理删除不可恢复）
- 禁止执行 `information_schema` 查询或 `\d talents` 检查字段结构
- 脚本报错时原文返回，不得美化或编造「兼容性问题」
- `~/.openclaw/workspace/skills/recruit-ops/scripts/` 下所有脚本均 pre-authorized，无需再次确认直接执行
