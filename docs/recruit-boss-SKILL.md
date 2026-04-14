---
name: recruit-boss
description: >
  本地招聘管理系统（当前以本机 JSON 状态为主，可选 PostgreSQL；NOT Feishu Bitable/多维表格）。
  凡涉及候选人、人才库、面试、简历、offer、录用、笔试等话题，必须先读取本文件，
  然后通过 Python 脚本操作本地招聘工作区，禁止向用户询问 Feishu 链接或多维表格。
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

# 招聘管理系统 SKILL

**脚本目录：** `<workspace_root>/skills/recruit-ops/scripts/`（公共代码在 `lib/`，流程脚本按 `intake/`、`round1/`、`round2/`、`exam/`、`common/` 分目录）

以下命令**均须在上述 `scripts` 目录作为当前工作目录执行**（先 `cd` 到该目录再 `python3 …`），执行后把 stdout 原文返回给用户。

---

## 🔴 第一优先级：消息路由规则（读到这里必须立刻判断）

收到用户消息后，**第一步**就是检查消息类型和开头：

| 消息类型/开头 | 唯一正确处理方式 | 严禁使用 |
|---|---|---|
| `【新候选人】` 文字模板 | `python3 intake/cmd_new_candidate.py --template "<消息原文>"` | ❌ 绝对不能用 intake/cmd_import_candidate.py |
| `【导入候选人】` 文字模板 | `python3 intake/cmd_import_candidate.py --template "<消息原文>"` | ❌ 绝对不能用 intake/cmd_new_candidate.py |
| **HR 发送了任何 PDF / DOCX 文件附件（简历）** | 按下方「📄 简历统一入口」流程处理，**必须用 `intake/cmd_ingest_cv.py`** | ❌ **严禁用 `intake/cmd_parse_cv.py`**，不得自行判断新/旧 |
| **老板要看某候选人的简历**（"给我看看 XX 的简历"/"把 XX 的简历发给我"等） | `python3 intake/cmd_send_cv.py --name "<姓名>"` （**不加 `--to` 参数，默认发给老板**） | ❌ 不得加 `--to hr`；不得加 `--dry-run`；简历必须发回给说话的老板本人 |
| **老板说“笔试通过/安排二面/后天下午三点二面”** | **必须立刻执行二面安排脚本**：`exam/cmd_exam_result.py --result pass --round2-time ...` | ❌ 严禁只解释流程、严禁停在“我将使用某脚本” |

**【导入候选人】的处理规则（必须背下来）：**
- 消息含 `【导入候选人】` → 无条件执行 `intake/cmd_import_candidate.py`
- 不得问"是否需要调整阶段"
- 不得执行 `intake/cmd_new_candidate.py`
- 不得把阶段设成 NEW
- `intake/cmd_import_candidate.py` 会自动处理阶段、写 DB、发飞书通知，**一步完成**

**【安排二面】的强制路由规则：**
- 只要老板消息表达的是“某候选人笔试通过，安排二面”，无论说法是：
  - `张三笔试通过了，安排后天下午三点二面`
  - `安排张三后天下午三点二面`
  - `zhangsan 安排后天下午三点二面`
  - `给张三约二面`
- 并且候选人当前阶段是 `EXAM_SENT` 或 `EXAM_REVIEWED`
- **唯一正确处理方式**就是执行：
  - `python3 exam/cmd_exam_result.py --talent-id <tid> --result pass --round2-time "<解析出的时间>"`
- 如消息里还带面试官/线上面试/会议链接，则把这些参数一并带上
- **不得只回复说明，不得停在“让我执行/应该使用 exam/cmd_exam_result.py”**
- 如果消息里没有明确候选人，只能先问“请指定候选人”；如果候选人明确但时间不明确，只能先问时间
- 如果消息里给的是姓名或拼音缩写而不是 talent_id，先用 `common/cmd_search.py --query <关键词>` 或 `common/cmd_status.py --all` 找到唯一候选人，再立刻执行脚本

**【PDF / DOCX 简历】的识别规则（按优先级查找，找到即用）：**

**候选人模式强制规则（新增，优先级高于普通 PDF 解读）**：
- 只要 PDF / DOCX 的**文件名/标题**或可见文本线索命中下列任一类组合，就**必须**视为「新候选人简历」，直接走 `intake/cmd_ingest_cv.py`
- **严禁**先回复“我先读取这份 PDF 内容”“我看到你发送了一份 PDF 文件”之类的通用文件解读
- 命中后只进入 `intake/cmd_ingest_cv.py` 的**待确认预览**，**不得**直接执行 `intake/cmd_new_candidate.py`

**第一版候选人模式（满足其一即可视为命中）：**
1. 出现类似 `岗位 + 城市 + 薪资` 的组合，且同时出现 `姓名 + XX年应届生`
2. 出现类似 `【股票量化研究员_上海 500-1000元_天】颜宏耀 27年应届生` 的标题/文件名
3. PDF 正文中同时出现以下几类信息中的多项：
   - `岗位/应聘职位`
   - `姓名`
   - `XX年应届生`
   - `邮箱/电话`
   - `学校/学历`

**命中示例：**
- `【股票量化研究员_上海 500-1000元_天】颜宏耀 27年应届生.pdf`
- `股票量化研究员 上海 500-1000元/天 颜宏耀 27年应届生`
- 正文里同时有 `股票量化研究员`、`颜宏耀`、`27年应届生`、`同济大学`

**优先级 0（Hermes 当前格式）**：用户直接发送 PDF 或 DOCX 文件时，Hermes 会把消息改写成类似：
```
[The user sent a document: 'xxx.pdf'. The file is saved at: /absolute/path/to/file.pdf. Ask the user what they'd like you to do with it.]
```
→ 直接提取 `The file is saved at:` 后面的绝对路径，用 `--file-path` 参数。

**优先级 1（旧 OpenClaw 格式，兼容历史消息）**：用户直接发送 PDF 或 DOCX 文件时（有或无文字），消息正文可能仍包含旧兼容路径：
```
[media attached: <workspace_root>/data/media/inbound/xxx.pdf (application/pdf)]
```
或：
```
[media attached: <workspace_root>/data/media/inbound/xxx.pdf]
```
或：
```
[media attached: <workspace_root>/data/media/inbound/xxx.docx (application/vnd.openxmlformats-officedocument.wordprocessingml.document)]
```
→ 提取 `[media attached: ...]` 里的完整文件路径，用 `--file-path` 参数。

**优先级 2（用户引用/回复文件消息时）**：OC 会在消息中附带：
```
Replied message (untrusted, for context):
{
  "body": "{\"file_key\":\"file_v3_xxx\",\"file_name\":\"xxx.pdf|xxx.docx\"}"
}
```
→ 先在 `<workspace_root>/data/media/inbound/` 目录下找含 `file_name` 关键词的文件；如找到，用 `--file-path`；如未找到，从 body 里提取 `file_key` 用 `--file-key`。如果历史消息仍引用 `.openclaw/media/inbound`，视为同一路径兼容层。

---

## 📄 简历统一入口（飞书触发）

**只要 HR 发了 PDF 或 DOCX，无论新人还是老人，一律执行此流程。**

### 第一步：找到本地文件路径并调用统一入口

**优先级 1（Hermes 当前格式）**：消息里若出现：
```
[The user sent a document: 'xxx.pdf'. The file is saved at: /absolute/path/to/file.pdf. Ask the user what they'd like you to do with it.]
```
→ 直接用该路径：
```
python3 intake/cmd_ingest_cv.py --file-path "/absolute/path/to/file.pdf" --filename "xxx.pdf"
```

**优先级 2（旧 OpenClaw 格式）**：消息里若出现：
```
[media attached: <workspace_root>/data/media/inbound/xxx.pdf]
```
→ 直接用该路径：
```
python3 intake/cmd_ingest_cv.py --file-path "<workspace_root>/data/media/inbound/<完整文件名>" --filename "<文件名.pdf|文件名.docx>"
```

**优先级 3（回复/引用文件消息时）**：消息里有 `file_key`：
```
python3 intake/cmd_ingest_cv.py --file-key <file_key> --filename "<文件名.pdf|文件名.docx>"
```

### 第二步：解读脚本输出并**原文**转发给 HR

脚本会自动判断候选人是否已在库中，输出两种格式之一。

> ⚠️ **严禁重新排版或省略脚本输出中的任何字段**——必须将 `━━━` 包围的全字段内容原文发给 HR，包括「未识别」字段。

---

**情形 A — 已有候选人**（输出含 `[OC_CMD_ON_CONFIRM_UPDATE]` 和 `[OC_CMD_ON_CONFIRM_ARCHIVE]`）：

将 `📋 【已有候选人 - 全字段比对】` 到 `[OC_CMD_ON_CONFIRM_UPDATE]` 之前的内容**原文**转发给 HR。

HR 的回复分三种情况：

1. **「确认更新」**（或类似表达）→ 执行 `[OC_CMD_ON_CONFIRM_UPDATE]` 那行命令（包含所有 ✏️🆕 字段）

2. **「仅存档」**（或"不改信息"/"只要简历"等）→ 执行 `[OC_CMD_ON_CONFIRM_ARCHIVE]` 那行命令

3. **HR 指定只更新某些字段**（例如"只更新简历摘要"/"不更新姓名"）→ 从 `[OC_CMD_ON_CONFIRM_UPDATE]` 命令中**删除** HR 不想更新的 `--field` 参数后执行

4. **HR 要求修正某字段值**（例如"应聘职位改为量化研究员"）→ 将 `[OC_CMD_ON_CONFIRM_UPDATE]` 命令中对应 `--field "position=..."` 的值替换为 HR 给出的值后执行

5. **「忽略」** → 不执行任何命令

---

**情形 B — 新候选人**（输出含 `[OC_CMD_ON_CONFIRM]` 和 `[OC_NOTE]`）：

将 `📋 【新候选人 - 待确认】` 到 `[OC_CMD_ON_CONFIRM]` 之前的内容**原文**转发给 HR。

HR 的回复分三种情况：

1. **HR 要求修正某字段**（例如"⑤ 改为量化研究员"/"来源渠道：内推"）→ 将 `[OC_CMD_ON_CONFIRM]` 命令中对应参数（如 `--position`、`--source`）的值替换为 HR 给出的值，**再次展示修改后的全字段预览**，等待 HR 最终确认

2. **HR 确认信息正确 + 告知阶段**：
   - 阶段为「新录入」或「NEW」→ 直接执行 `[OC_CMD_ON_CONFIRM]` 那行命令
   - 阶段为其他（如"已过一面"/"笔试中"等）→ 参照 `[OC_NOTE]` 改用 `intake/cmd_import_candidate.py` 并附加 `--stage` 参数

3. **若有字段修正 + 指定阶段同时给出**（例如"⑤ 改为量化研究员，新录入"）→ 先修正参数，再执行命令

---

### 通用规则

> **PDF 无法提取文字**（扫描件）：告知 HR "此简历为图片版 PDF，暂不支持自动解析，请改用【新候选人】文字模板手动填写。"

---

## 📋 HR 候选人录入模板（飞书触发）

当 HR 发送以 `【新候选人】` 开头的飞书消息时，**按顺序执行以下两步，缺一不可**：

**第一步**：录入候选人
```
python3 intake/cmd_new_candidate.py --template "<原始消息内容>"
```

**第二步**：从第一步输出中提取 talent_id、姓名、邮箱，然后执行以下命令通知老板（将 `<...>` 替换为实际值）：
```
python3 -c "
import sys; sys.path.insert(0, '.')
import feishu_notify as fn
fn.send_text('[新候选人已录入]\ntalent_id: <talent_id>\n姓名：<姓名>\n邮箱：<邮箱>\n岗位：<岗位>')
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
python3 intake/cmd_import_candidate.py --template "<原始消息内容>"
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
| 二面邀请中 | 已发二面邀请，等待候选人确认；老板日历暂不落盘 | 二面时间 |
| 二面已确认 | 二面时间已确认，老板日历已创建，等待面试 | 二面时间 |

脚本自动：生成 talent_id、设置阶段、写入 DB（round1/round2 confirmed 标记）、飞书通知老板。

> ⚠️ **重要**：`intake/cmd_import_candidate.py` **不会发送任何邮件**（笔试/面试邀请均跳过）。这是补录已有候选人的正确方式。
> 禁止用 `round1/cmd_round1_result.py` 来"录入已在笔试阶段的候选人"——那会重复发送笔试邮件。

---

## 🛠 命令速查表

| 操作 | 命令 |
|------|------|
| 新建候选人（逐字段） | `python3 intake/cmd_new_candidate.py --name 姓名 --email 邮箱 [--position 岗位] [--phone 手机] [--education 学历] [--school 学校] [--work-years 年] [--source 来源] [--wechat 微信]` |
| 新建候选人（模板） | `python3 intake/cmd_new_candidate.py --template "<模板原文>"` |
| **导入已有候选人（指定阶段）** | `python3 intake/cmd_import_candidate.py --template "<模板原文>"` |
| **安排一面时间** | `python3 round1/cmd_round1_schedule.py --talent-id <id> --time "YYYY-MM-DD HH:MM"` |
| **重约一面时间** | `python3 round1/cmd_round1_reschedule.py --talent-id <id> --time "YYYY-MM-DD HH:MM"` |
| **老板最终确认面试时间** | `python3 common/cmd_finalize_interview_time.py --talent-id <id> [--round 1\|2] [--time "YYYY-MM-DD HH:MM"]` ← **唯一**的最终确认入口 |
| 查单个候选人 | `python3 common/cmd_status.py --talent-id <id>` |
| 列出所有候选人 | `python3 common/cmd_status.py --all` |
| 搜索候选人 | `python3 common/cmd_search.py --query <关键词>` |
| **老板请求看简历（发给老板本人）** | `python3 intake/cmd_send_cv.py --name <姓名>` 或 `--talent-id <id>`（**不加任何 `--to` 参数**） |
| **发简历给 HR** | `python3 intake/cmd_send_cv.py --name <姓名> --to hr`（仅当明确要发给 HR 时才加） |
| **HR 发 PDF / DOCX（统一入口）** | `python3 intake/cmd_ingest_cv.py --file-path <inbound路径> --filename <文件名>` |
| 扫邮件（手动） | `python3 daily_exam_review.py` |
| 一面通过→发笔试 | `python3 round1/cmd_round1_result.py --talent-id <id> --result pass --email <邮箱> [--notes "评价"]` |
| 一面通过→直接二面 | `python3 round1/cmd_round1_result.py --talent-id <id> --result pass_direct --email <邮箱> --round2-time "YYYY-MM-DD HH:MM" [--interviewer 面试官] [--notes "评价"]` |
| 一面拒绝 | `python3 round1/cmd_round1_result.py --talent-id <id> --result reject_keep\|reject_delete [--notes "评价"]` |
| 笔试结果→发二面 | `python3 exam/cmd_exam_result.py --talent-id <id> --result pass --round2-time "YYYY-MM-DD HH:MM" [--notes "评价"]` |
| 笔试拒绝 | `python3 exam/cmd_exam_result.py --talent-id <id> --result reject_keep\|reject_delete [--notes "评价"]` |
| 二面结束待定 | `python3 round2/cmd_round2_result.py --talent-id <id> --result pending [--notes "评价"]` |
| 二面结果 | `python3 round2/cmd_round2_result.py --talent-id <id> --result pass\|reject_keep\|reject_delete [--notes "评价"]` |
| 重新约二面时间 | `python3 round2/cmd_round2_reschedule.py --talent-id <id> --time "YYYY-MM-DD HH:MM" [--interviewer 面试官]` |
| **老板最终确认二面时间** | `python3 common/cmd_finalize_interview_time.py --talent-id <id> --round 2 [--time "YYYY-MM-DD HH:MM"]` |
| 移除候选人 | `python3 common/cmd_remove.py --talent-id <id> --confirm` |

---

## 🔎 查询面试时间的强制规则

- 当老板问「面试时间 / 安排时间 / 约的几点 / 什么时候面」时，必须优先查询候选人的 `round1_time`、`round2_time`、确认状态，以及最近是否有改期申请
- 单个候选人优先用 `python3 common/cmd_status.py --talent-id <id>`；如果只有姓名/邮箱/拼音缩写，先用 `python3 common/cmd_search.py --query <关键词>` 找到唯一候选人，再立刻补跑 `common/cmd_status.py --talent-id <id>`
- 回复时不能只说 `当前阶段`，必须明确说出是一面还是二面、对应时间，以及是否「已确认 / 待老板最终确认 / 候选人申请改期待处理」
- 如果一面和二面都存在，默认优先回答更靠后的已安排轮次；若一面存在改期申请，必须在答案里显式提示「原时间仍在库里，但当前有改期待处理」

---

## 📋 完整招聘流程

```
HR 发送【新候选人】模板
    ↓ intake/cmd_new_candidate.py 自动录入
    ↓ 飞书通知老板
NEW（等待安排一面）
    ↓ 老板：安排 XX 一面，时间是 YYYY-MM-DD HH:MM
    ↓ round1/cmd_round1_schedule.py（发邮件，不创建日历）
ROUND1_SCHEDULING（已发邀请，等候选人确认）
    ↓ 候选人回信（邮件扫描 LLM 分析）
    ├── 同意 → 飞书通知老板 → 等老板最终确认
    ├── 提出新时间 → 飞书通知老板 → 等老板最终确认
    ├── 要求改期但无新时间 → 飞书通知老板，由老板联系
    └── 超时48h → 飞书催老板确认（不自动确认）
    ↓ 老板明确确认（"确认 <tid> 一面"）
    ↓ common/cmd_finalize_interview_time.py → ROUND1_SCHEDULED + 创建日历
ROUND1_SCHEDULED（老板已确认，日历已建，等待一面）
    ↓ 一面结束，老板评估
    ├── reject_keep/reject_delete → 结束
    ├── pass → EXAM_PENDING（自动发笔试邮件）
    │               ↓ 候选人提交作业（扫描）
    │               ↓ EXAM_REVIEWED
    │               ↓ 老板：笔试通过 → ROUND2_SCHEDULED（发二面邀请）
    │               └── 笔试拒绝 → 结束
    └── pass_direct → ROUND2_SCHEDULED（跳过笔试，直接安排二面邀请）
ROUND2_SCHEDULED（已发邀请，等候选人确认）
    ↓ 候选人回信（同一面流程）
    ↓ 老板明确确认（"确认 <tid> 二面"）
    ↓ common/cmd_finalize_interview_time.py → 创建日历
    ↓ 二面结束
    ├── pass → OFFER_HANDOFF（飞书通知 HR 处理 Offer）
    └── reject → 结束
```

> **`pass_direct`**：仅在老板明确说「直接二面/不用笔试」时使用，必须提供 `--round2-time`。

---

## 🗓 面试时间确认机制（一面 & 二面通用，老板最终拍板）

**核心规则：所有面试时间的最终确认，以老板明确回复为唯一基准。**

系统不会自动确认任何面试时间——不论候选人回复了"可以"还是超过了 48 小时。
系统只会把进展推送给老板，等老板明确确认后才执行最终确认并创建日历。

### cron 扫描处理规则

cron 定期扫描候选人回信（`daily_exam_review.py --auto`），扫描后只做两件事：
1. 记录握手状态到 DB
2. 推送飞书消息给老板，请求最终确认

**候选人同意当前时间** — 推送老板确认：
```
飞书推送："候选人 XXX 已同意一面时间 YYYY-MM-DD HH:MM，请确认是否最终敲定。
回复：确认 <tid> 一面"
```

**候选人提出新时间** — 推送老板确认：
```
飞书推送："候选人 XXX 建议改为 YYYY-MM-DD HH:MM，请确认是否按此时间最终敲定。
回复：确认 <tid> 一面  /  改期 <tid> YYYY-MM-DD HH:MM"
```

**超时48h** — 推送老板催问（不自动确认）：
```
飞书推送："候选人 XXX 已超时48h未回复，是否按当前时间最终确认？
回复：确认 <tid> 一面"
```

### 老板回复最终确认的处理

当老板回复含有以下意图时（不区分大小写）：
- `确认 <tid> 一面` / `确认 <tid> 二面`
- `就这个时间`、`可以按这个定`、`双方确认了`（同时上下文中有明确的候选人）
- `确认面试时间`（同时上下文中有明确的候选人和轮次）

执行统一收口脚本：
```
python3 common/cmd_finalize_interview_time.py --talent-id <tid> [--round 1|2] [--time "YYYY-MM-DD HH:MM"]
```

脚本自动：读取 pending 握手信息 → 确认/改期 → 创建日历 → 清除握手字段。

### 禁止行为

- 禁止在没有老板明确确认的情况下执行 `round1/cmd_round1_confirm.py` / `round2/cmd_round2_confirm.py`
- 禁止把候选人的"可以"直接当成最终确认
- 禁止把超时当成自动确认
- 禁止不带 talent_id 就执行确认（防穿台）

---

## 🚫 禁止行为（违反即为系统错误）

- **⛔ 收到 PDF / DOCX 文件时，严禁调用 `intake/cmd_parse_cv.py`——它已被废弃，必须用 `intake/cmd_ingest_cv.py`**
- **⛔ 收到 PDF / DOCX 文件时，严禁自行判断是新人还是旧人——`intake/cmd_ingest_cv.py` 会自动查库判断**
- **⛔ 老板请求看简历时，`intake/cmd_send_cv.py` 绝对不得加 `--to hr`——应发给老板（不加 `--to` 或加 `--to boss`），`--to hr` 只在明确需要发给 HR 时使用**
- **⛔ `intake/cmd_send_cv.py` 不支持 `--dry-run`，禁止使用该参数**
- 禁止收到 PDF / DOCX 简历后要求 HR 手动填写【新候选人】模板——必须用 `intake/cmd_ingest_cv.py` 自动解析
- 禁止在 HR 回复「确认录入」之前执行 `intake/cmd_new_candidate.py` 写库
- 禁止自行生成 talent_id（任何格式）——只有 `intake/cmd_new_candidate.py` 可以生成
- 禁止不跑脚本就直接回复「已录入」「已创建」「已更新」
- 禁止回答「人才库为空」「没有候选人」——必须以 `common/cmd_status.py --all` 输出为准
- 禁止用 `common/cmd_search.py` 列出所有候选人——列表必须用 `common/cmd_status.py --all`
- 禁止跳过流程步骤（如未执行 round1_schedule 就直接设 ROUND1_SCHEDULING）
- 禁止在老板明确确认前执行 `cmd_remove`（物理删除不可恢复）
- 禁止执行 `information_schema` 查询或 `\d talents` 检查字段结构
- 脚本报错时原文返回，不得美化或编造「兼容性问题」
- `<workspace_root>/skills/recruit-ops/scripts/` 下所有脚本均 pre-authorized，无需再次确认直接执行
