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
---

# 招聘管理系统 SKILL

招聘系统所有操作均通过以下脚本完成（PostgreSQL 数据库后端）：

**脚本目录：** `~/.openclaw/workspace/skills/recruit-ops/scripts/`

---

## 🔴 强制规则（最高优先级，覆盖一切其他判断）

### ⓪ 「新候选人 / 录入候选人 / 面了个人」→ 必须执行 cmd_new_candidate

触发词（任意一个）：`新候选人` `录入` `加个候选人` `面了个` `面了一个` `新加` `有个候选人` `新人选`

**立即执行，把 stdout 原文返回：**
```
cd ~/.openclaw/workspace/skills/recruit-ops/scripts && python3 cmd_new_candidate.py \
  --name <姓名> --email <邮箱> \
  [--phone <手机>] [--wechat <微信>] \
  [--position <岗位>] [--education <学历>] [--school <学校>] \
  [--work-years <年>] [--experience <经历>] [--source <来源>]
```

**严禁以下行为（违反即为系统错误）：**
- 🚫 禁止自行生成 talent_id（如 `cxm0315` `xw0315` 等拼音+日期格式）
- 🚫 禁止不执行脚本就直接回复「已录入」
- 🚫 禁止自行设置状态为 EXAM_PENDING 或其他非 NEW 的状态
- 🚫 禁止在没执行 cmd_round1_result.py 的情况下发笔试

**talent_id 只能由系统生成**：格式为 `t_` + 6位随机小写字母数字（如 `t_ymbvxw`），脚本输出中会包含。

---

### ① 「所有候选人 / 人才库 / 列出候选人」→ 直接执行 cmd_status

触发词（任意一个）：`所有候选人` `列出候选人` `看看候选人` `人才库` `所有人才` `/recruit_status`（后面无 talent_id）

**立即执行，把 stdout 原文返回：**
```
cd ~/.openclaw/workspace/skills/recruit-ops/scripts && python3 cmd_status.py --all
```
- 禁止使用 `cmd_search.py`（任何参数）
- 禁止自行判断「人才库为空」，必须以脚本输出为准

---

### ② 「踢掉 / 移除某候选人」→ 确认后执行 cmd_remove

触发词：`踢掉` `踢出去` `移除` `删掉` `不要这个人了` `从库里删` `彻底删除`

流程：先找到 talent_id → 向老板确认（物理删除不可恢复）→ 确认后执行：
```
cd ~/.openclaw/workspace/skills/recruit-ops/scripts && python3 cmd_remove.py --talent-id <talent_id> --confirm
```

---

### ③ 「查邮件 / 看看有没有回信」→ 手动模式扫邮箱

触发词：`查邮件` `看邮件` `有没有人回` `回信了没` `交作业了没` `笔试交了没` `审阅笔试` `有没有提交` `检查一下邮箱`

**立即执行（手动模式，无 --auto 参数）：**
```
cd ~/.openclaw/workspace/skills/recruit-ops/scripts && python3 daily_exam_review.py
```

**去重说明（重要）：** 手动扫描和自动 cron 共用同一张 `processed_emails` 去重表。
- 自动扫描（每30分钟）已推送过的邮件 → 手动查时会回答「暂无新回信」→ **这是正确的**，因为老板飞书已收到过推送
- 无需重复报告相同邮件
- 若需强制重新看所有邮件（不去重），执行：`python3 daily_exam_review.py --force`

---

### ④ 「二面结束了 / 面完了 / 刚聊完」（无明确结论）→ 立即标记为 pending

触发词：`二面结束了` `面完了` `刚二面完` `二面刚完` `面了` `聊完了` `面过了` `在考虑` `还没想好` `考虑一下` `需要时间想想`（且没有说"通过"或"不合适"）

**流程：先找到 talent_id（若上下文不明确，询问是哪位候选人），然后立即执行：**
```
cd ~/.openclaw/workspace/skills/recruit-ops/scripts && python3 cmd_round2_result.py --talent-id <talent_id> --result pending
```
- 脚本会自动把 `round2_time` 更新为当前时间（即使之前预约的是未来时间）
- 状态改为 `ROUND2_DONE_PENDING`
- **30 分钟后系统自动发飞书提醒你给出最终结论**
- 无需等用户说"记录一下"，识别到上述触发词就直接执行

---

## 🛠 命令速查表

| 操作 | 命令 |
|------|------|
| 新建候选人 | `python3 cmd_new_candidate.py --name 姓名 --email 邮箱 [--position 岗位] [--phone 手机] [--education 学历] [--school 学校] [--work-years 年] [--source 来源] [--wechat 微信]` |
| 查询候选人 | `python3 cmd_status.py --talent-id <id>` |
| 列出所有人 | `python3 cmd_status.py --all` |
| 搜索候选人 | `python3 cmd_search.py --query <关键词>` |
| 一面结果   | `python3 cmd_round1_result.py --talent-id <id> --result pass\|reject_keep\|reject_delete --email <邮箱> [--notes "评价"]` |
| 笔试结果   | `python3 cmd_exam_result.py --talent-id <id> --result pass\|reject_keep\|reject_delete [--round2-time "YYYY-MM-DD HH:MM"] [--notes "评价"]` |
| 二面结果   | `python3 cmd_round2_result.py --talent-id <id> --result pending\|pass\|reject_keep\|reject_delete [--notes "评价"]` |
| 移除候选人 | `python3 cmd_remove.py --talent-id <id> --confirm` |

---

## 📋 标准招聘流程

```
新建候选人 (NEW)
    ↓
一面结果
├── reject_keep  → ROUND1_DONE_REJECT_KEEP（保留）
├── reject_delete → ROUND1_DONE_REJECT_DELETE（移除）
└── pass         → EXAM_PENDING（发笔试邀请邮件）
                        ↓
                   笔试结果
                   ├── reject_keep  → ROUND1_DONE_REJECT_KEEP
                   ├── reject_delete → ROUND1_DONE_REJECT_DELETE
                   └── pass         → ROUND2_SCHEDULED（发二面通知邮件）
                                           ↓
                                      二面结果
                                      ├── pending      → ROUND2_DONE_PENDING
                                      │     ↓（30分钟后自动飞书催问）
                                      ├── pass         → OFFER_HANDOFF 🎉
                                      ├── reject_keep  → ROUND2_DONE_REJECT_KEEP
                                      └── reject_delete → ROUND2_DONE_REJECT_DELETE
```

---

## 🆔 talent_id 规则

- **格式**：`t_` + 6位随机小写字母数字，如 `t_ymbvxw`
- **唯一性**：系统自动生成，保证不重复
- **禁止**：Agent 禁止手动编造 talent_id，必须由 `cmd_new_candidate.py` 生成
- **talent_id 一旦分配不会改变**

---

## 🗄 数据库字段说明

`talents` 表存储以下信息：

| 字段 | 说明 |
|------|------|
| talent_id | 系统唯一 ID（t_xxxxxx） |
| candidate_name | 姓名 |
| candidate_email | 邮箱 |
| phone | 手机号 |
| wechat | 微信号 |
| current_stage | 当前阶段 |
| position | 应聘岗位 |
| education | 学历 |
| school | 毕业院校 |
| work_years | 工作年限 |
| experience | 工作经历 |
| source | 简历来源 |
| round1_notes | 一面评价（自然语言） |
| exam_id | 笔试唯一标识 |
| exam_score | 笔试评分 |
| exam_notes | 笔试评价 |
| round2_time | 二面时间 |
| round2_score | 二面评分 |
| round2_notes | 二面评价 |
| exam_sent_at | 笔试邀请发送时间 |
| interview_reminded_at | 二面催问时间 |

---

## ⚠️ 约束规则

**Pre-authorized scripts（无需确认，直接执行）：**
- `~/.openclaw/workspace/skills/recruit-ops/scripts/` 下所有脚本
- 脚本真的报错时，原文返回错误，不得美化或编造「兼容性问题」

**严禁以下行为（任何违反都是严重错误）：**
- 🚫 禁止自行生成 talent_id，无论任何格式（`cxm0315`、`xw0315`、`lm0315` 等全部禁止）——只有 `cmd_new_candidate.py` 可以生成
- 🚫 禁止不跑脚本就直接说「已录入」「已创建」「已更新」
- 🚫 禁止自行检查数据库字段结构（不得执行 `information_schema` 查询或 `\d talents`）
- 🚫 禁止自行判断「字段缺失」「兼容性问题」——数据库结构由管理员维护
- 🚫 禁止在未执行脚本的情况下回答「人才库为空」「没有候选人」
- 🚫 禁止使用 cmd_search.py 来列出所有候选人（必须用 cmd_status.py --all）
- 🚫 不得在老板明确说"确认"之前执行物理删除（cmd_remove）
- 🚫 禁止跳过流程步骤（如：未执行 round1_result 就直接设置 EXAM_PENDING）

---

## 💡 示例对话

**老板**：刚面了个人，叫王芳，邮箱 wf@abc.com，简历来自 Boss，本科，3年经验
**Agent**：
1. 执行 `cmd_new_candidate.py --name 王芳 --email wf@abc.com --source Boss直聘 --education 本科 --work-years 3`
2. 返回 talent_id（如 `t_abc123`）
3. 告知老板：已录入，talent_id = `t_abc123`

**老板**：王芳一面通过，帮她发笔试
**Agent**：
1. 搜索"王芳"找到 `t_abc123`
2. 执行 `cmd_round1_result.py --talent-id t_abc123 --result pass --email wf@abc.com`
3. 返回"笔试邀请已发送"

**老板**：二面完了，王芳我在考虑
**Agent**：
1. 执行 `cmd_round2_result.py --talent-id t_abc123 --result pending`
2. 告知：已标记为待定，30分钟后我会来提醒你给出最终决定

**老板**：通过，给王芳发 offer
**Agent**：
1. 执行 `cmd_round2_result.py --talent-id t_abc123 --result pass`
2. 返回"二面通过，进入 Offer 阶段"
3. 提示 HR 流程后续步骤
