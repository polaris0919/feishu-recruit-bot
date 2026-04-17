# 飞书招聘管家 — recruit-ops

> **版本**：v3.0（2026-04）  
> **运行环境**：Hermes Gateway · Python 3.10+ · PostgreSQL 状态真源 · 飞书 WebSocket  
> **代码位置**：`/home/admin/recruit-workspace/skills/recruit-ops/`

> **补充文档**
> - `docs/CLI_REFERENCE.md`：CLI 命令总参考
> - `docs/COMPLEX_NEGOTIATION_REGRESSION.md`：复杂协商真实回归清单

---

## 目录

1. [产品概述](#一产品概述)
2. [完整招聘流程](#二完整招聘流程)
3. [HR 使用指南](#三hr-使用指南)
4. [老板使用指南](#四老板使用指南)
5. [自动化机制](#五自动化机制)
6. [技术架构](#六技术架构)
7. [脚本速查](#七脚本速查)
8. [配置与部署](#八配置与部署)
9. [运维手册](#九运维手册)

---

## 一、产品概述

**飞书招聘管家**将 Hermes AI 网关接入飞书，让 HR 和老板在飞书里完成整条招聘闭环，无需切换任何其他系统。

### 核心特性

| 特性 | 说明 |
|------|------|
| **全流程自动化** | 从 HR 录入候选人，到一面邀请、笔试、二面、结果归档，全程自动流转 |
| **双角色设计** | HR 使用飞书消息模板；老板用自然语言（`recruit-ops` Skill）与 OC 对话；同一个 skill 同时服务两侧 |
| **邮件协商** | OC 自动向候选人发送面试邀请邮件，LLM 解析候选人回复意图（确认/改期/不明） |
| **飞书日历** | 仅在候选人最终确认面试时间后，才在老板飞书日历创建日程，老板收到邀请通知 |
| **超时自动确认** | 候选人 48 小时未回复邮件，系统默认确认，自动创建日历 |
| **状态持久化** | PostgreSQL 为唯一数据源；自动化测试通过内存 fake `talent_db` 注入隔离 |
| **批量导入** | 支持导入已有候选人并指定当前阶段，适合系统上线初期迁移历史数据 |

### 招聘阶段总览

```
NEW → ROUND1_SCHEDULING → ROUND1_SCHEDULED → ROUND1_DONE_PASS
                                                    ↓
                                                EXAM_SENT → EXAM_REVIEWED
                                                                  ↓
                            WAIT_RETURN ← ROUND1/ROUND2 暂缓
                                              ↑
                                              ROUND2_SCHEDULING → ROUND2_SCHEDULED → ROUND2_DONE_*
                                                                  ↓
                                                            OFFER_HANDOFF
```

| 阶段 | 含义 |
|------|------|
| `NEW` | 新建，等待安排一面 |
| `ROUND1_SCHEDULING` | 一面邀请邮件已发，等待候选人确认时间 |
| `ROUND1_SCHEDULED` | 一面时间已确认，飞书日历已创建 |
| `ROUND1_DONE_PASS` | 一面通过，进入笔试 |
| `ROUND1_DONE_REJECT_KEEP` | 一面未通过，保留在人才库 |
| `EXAM_SENT` | 笔试已发送，等待候选人提交答案 |
| `EXAM_REVIEWED` | 笔试已审阅，等待决定是否进入二面 |
| `WAIT_RETURN` | 候选人暂时不在国内/上海，待回国后按记录轮次恢复排期 |
| `ROUND2_SCHEDULING` | 二面邀请已发出，等候选人确认；老板日历尚未落盘 |
| `ROUND2_SCHEDULED` | 二面时间已确认，等待面试进行 |
| `ROUND2_DONE_PENDING` | 二面结束，等待出结果 |
| `ROUND2_DONE_PASS` | 二面通过 |
| `ROUND2_DONE_REJECT_KEEP` | 二面未通过，保留在人才库 |
| `OFFER_HANDOFF` | HR 手动跟进 Offer 发放 |

> **注意**：`*_REJECT_DELETE` 阶段会立即从数据库中彻底删除候选人记录。

---

## 二、完整招聘流程

```
HR 在飞书发送【新候选人】模板
           ↓
    OC 解析 + 录入数据库（NEW）
           ↓
    通知老板：请安排一面时间
           ↓
老板指令：安排一面（时间、面试官）
           ↓
    round1/cmd_round1_schedule.py
    → 候选人状态 → ROUND1_SCHEDULING
    → 后台发送一面邀请邮件给候选人
           ↓
    候选人回复邮件（自动扫描，每 8h）
    LLM 分析意图：确认 / 改期 / 不明
           ↓
  ┌────────┼────────────┐
确认       改期         48h 超时
  │         │              │
  ▼         ▼              ▼
ROUND1_SCHEDULED    cmd_round1_reschedule    自动默认确认
飞书日历已创建       重新发邮件              飞书日历已创建
           ↓
    老板面试完成后：记录一面结果
           ↓
  ┌────────┼────────────┐
通过       拒绝（保留）  拒绝（删除）
  │         │              │
  ▼         ▼              ▼
ROUND1_DONE_PASS   REJECT_KEEP         从DB彻底删除
发笔试邀请邮件
           ↓
    候选人回复提交答案（自动扫描，每 12h）
    预审：代码质量、答题时间、附件分析
           ↓
    老板审阅后：记录笔试结果
           ↓
  ┌────────┼────────────┐
通过       拒绝（保留）  拒绝（删除）
  │
  ▼
ROUND2_SCHEDULING
→ 后台发送二面邀请邮件给候选人
→ 等候候选人确认
           ↓
    候选人回复确认（自动扫描，每 8h）
           ↓
ROUND2_SCHEDULED
→ 飞书日历已创建（含邀请老板）
           ↓
  ┌────────┼────────────┐
确认       改期         48h 超时自动确认
           ↓
    老板二面完成后：记录结果
           ↓
通过 → OFFER_HANDOFF → HR 手动跟进发 Offer
```

---

## 三、HR 使用指南

HR 在飞书中与 OC 机器人对话，使用以下消息模板触发对应操作。

### 3.1 录入新候选人

发送以下模板给 OC（私聊或群聊均可）：

```
【新候选人】
姓名：张三
邮箱：zhangsan@example.com
手机：13800138000
岗位：量化研究实习生
来源：猎头推荐
学历：本科
学校：某某大学
工作年限：0
工作经历：无
微信：zhangsan_wx
```

**必填字段**：姓名、邮箱  
**选填字段**：手机、岗位、来源、学历、学校、工作年限、工作经历、微信

OC 收到后自动：
1. 校验并提取信息
2. 录入 PostgreSQL 人才库（状态：NEW）
3. 通知老板：新候选人已入库，请安排一面时间

---

### 3.2 批量导入历史候选人

系统上线时，可将已有候选人批量导入并指定当前阶段：

```
【导入候选人】
姓名：李四
邮箱：lisi@example.com
手机：13900139000
岗位：量化研究实习生
来源：内部推荐
学历：硕士
学校：某某大学
工作年限：2
当前阶段：笔试中
一面时间：2026-03-15 14:00
```

**`当前阶段` 可选值：**

| 填写内容 | 对应系统阶段 |
|---------|------------|
| 新候选人 / 待安排一面 | `NEW` |
| 一面邀请中 / 一面确认中 | `ROUND1_SCHEDULING` |
| 一面已确认 | `ROUND1_SCHEDULED` |
| 一面完成 / 一面通过 | `ROUND1_DONE_PASS` |
| 笔试中 / 已发笔试 | `EXAM_SENT` |
| 笔试完成 / 笔试已审 | `EXAM_REVIEWED` |
| 待安排二面 | `EXAM_REVIEWED` |
| 待回国后一面 | `WAIT_RETURN`（`wait_return_round=1`） |
| 待回国后二面 | `WAIT_RETURN`（`wait_return_round=2`） |
| 二面邀请中 | `ROUND2_SCHEDULING` |
| 二面已确认 | `ROUND2_SCHEDULED` |
| 二面完成 | `ROUND2_DONE_PENDING` |

---

## 四、老板使用指南

老板无需记任何命令格式，直接用自然语言与 OC 对话。

### 4.1 录入新候选人后安排一面

OC 通知老板后，老板指定时间即可：

```
安排 张三（t_xxxxx）一面，时间 2026-03-25 14:00，面试官：我
```

OC 自动发送邀请邮件给候选人，等待候选人回复。

### 4.2 一面改期

```
把 张三 的一面改到 2026-03-27 10:00
```

### 4.3 记录一面结果

```
张三一面通过了，进入笔试流程
张三一面没过，保留在人才库里
```

### 4.4 安排二面

```
张三笔试过了，下周三下午两点安排二面
```

### 4.5 查看招聘进展

```
招聘现在到哪步了？
有没有候选人交作业了？
```

---

## 五、自动化机制

系统通过系统级 cron 每隔一段时间自动执行以下任务：

| cron 任务 | 频率 | 说明 |
|---------|------|------|
| 笔试回复扫描 | 每 12 小时 | 扫描候选人提交的笔试答案，自动预审后推送飞书 |
| 面试确认扫描 | 每 8 小时 | LLM 分析候选人回复邮件意图（确认/改期），48h 超时自动默认确认 |
| 面试催问提醒 | 每 30 分钟 | 面试结束后若老板未给结果，自动催问 |

### 超时确认机制

- 发送一面/二面邀请邮件后，**48 小时内**候选人若未回复
- 系统自动执行 `confirm` 操作，状态推进，飞书通知老板
- 通过环境变量 `INTERVIEW_CONFIRM_TIMEOUT_MINUTES` 可调整（默认 2880 分钟 = 48h）

### LLM 意图分析

使用 DashScope API（qwen3-max）分析候选人邮件回复：

| 意图 | 触发条件 | 系统行为 |
|------|---------|---------|
| `confirm` | "可以"、"没问题"、"OK" 等 | 自动确认，创建飞书日历 |
| `reschedule` | "不方便"、"改一下"、"换个时间" 等 | 通知老板，附候选人建议时间 |
| `unknown` | 意图不明 | 推送飞书请老板人工判断 |

---

## 六、技术架构

```
飞书（HR / 老板）
      │  WebSocket（飞书 OpenAPI）
      ▼
Hermes Gateway（:17166）
      │  技能路由：recruit-ops
      ▼
LLM Agent（qwen3-max，阿里云 DashScope）
      │  exec tool 调用 Python 脚本
      ▼
scripts/
  ├── tests/
  │   ├── run_all.py                   # 测试聚合入口
  │   └── test_*.py
  ├── lib/                             # 公共模块
  │   ├── config.py                    # 统一配置加载（DB/飞书/邮件/DashScope）
  │   ├── core_state.py                # 状态机、阶段定义、审计
  │   ├── talent_db.py                 # PostgreSQL 读写（RealDictCursor、参数化 round）
  │   ├── feishu/                      # 飞书 SDK 封装（IM + 日历 + CLI）
  │   ├── bg_helpers.py                # 后台邮件 / 日历子进程封装
  │   ├── migrations/
  │   │   └── schema.sql               # 当前数据库终态定义（手动初始化用）
  │   ├── recruit_paths.py
  │   └── …
  ├── intake/                          # 简历与候选人录入
  ├── interview/                       # 统一面试操作（一面/二面合并）
  │   ├── cmd_confirm.py               # 面试确认（--round 1|2）
  │   ├── cmd_reschedule.py            # 面试改期（--round 1|2）
  │   └── cmd_result.py                # 面试结果（--round 1|2）
  ├── round1/                          # 向后兼容 wrapper → interview/
  ├── round2/                          # 向后兼容 wrapper → interview/
  ├── exam/                            # 笔试流程
  │   ├── email_scanner.py             # IMAP 邮件扫描模块
  │   ├── llm_analyzer.py              # LLM 意图分析模块
  │   └── …
  ├── common/                          # 查询、删除、催问等横切命令
  ├── cron_runner.py                   # crontab 入口
  ├── trigger_cron_now.py              # 手动提前触发 cron
  └── …

docs/
  ├── CLI_REFERENCE.md
  └── COMPLEX_NEGOTIATION_REGRESSION.md

外部依赖：
  /home/admin/recruit-workspace/config/recruit-email-config.json   # IMAP/SMTP 邮箱配置
  /home/admin/recruit-workspace/config/email-send-config.json      # SMTP 发信配置
  /home/admin/recruit-workspace/config/talent-db-config.json       # PostgreSQL 连接配置（可选）
  /home/admin/recruit-workspace/config/dashscope-config.json       # DashScope API Key
  /home/admin/recruit-workspace/config/openclaw.json               # 飞书 App 配置（app_id / app_secret）
```

### 关键技术决策

| 问题 | 方案 |
|------|------|
| exec 工具 ~3 秒超时 | 邮件/日历全部用 `subprocess.Popen(start_new_session=True)` 后台进程执行 |
| LLM 调用稳定性 | 直接调用 DashScope API，绕过 Gateway，避免 OOM 影响 |
| Python 3.10+ | 代码与测试已使用现代 typing/路径注解语法；时间解析仍统一走 `python-dateutil` |
| 邮件去重 | 每位候选人的 `*_last_email_id` 邮件游标配合 `talent_events.event_id` 幂等事件记录，避免重复扫描与重复落库 |
| 时区处理 | 所有时间戳统一以本地 CST（UTC+8）存储和显示 |
| 状态持久化 | PostgreSQL 为唯一数据源；配置统一由 `lib/config.py` 管理 |
| 配置管理 | `lib/config.py` 统一加载 JSON 配置文件 + 环境变量，替代分散的配置逻辑 |
| DB 模式 | `RealDictCursor` 消除 `row[N]` 硬编码；数据库结构由 `lib/migrations/schema.sql` 维护 |
| 脚本合并 | round1/round2 同类脚本合并到 `interview/`，接受 `--round 1\|2` 参数；旧路径通过 wrapper 保持兼容 |
| 模块间通信 | 关键路径由 `subprocess` 调用改为直接函数调用，减少 fork 开销和进程隔离问题 |

---

## 七、脚本速查

### 候选人管理

```bash
cd /home/admin/recruit-workspace/skills/recruit-ops

# 推荐统一前缀：`uv run python3 scripts/...`
# `interview/` 下的 confirm / result / reschedule 是主实现；
# `round1/round2` 同名脚本仅保留为兼容别名。

# 录入新候选人
uv run python3 scripts/intake/cmd_new_candidate.py --template "【新候选人】\n姓名：张三\n邮箱：zhangsan@example.com"

# 安排一面
uv run python3 scripts/round1/cmd_round1_schedule.py --talent-id t_xxxxx --time "2026-03-25 14:00" --interviewer "老板"

# 记录一面结果（通过，进入笔试）
uv run python3 scripts/interview/cmd_result.py --talent-id t_xxxxx --round 1 --result pass --email zhangsan@example.com

# 笔试结果（通过，先发候选人邀请；候选人确认后再创建老板日历）
uv run python3 scripts/exam/cmd_exam_result.py --talent-id t_xxxxx --result pass \
  --round2-time "2026-04-01 14:00" --interviewer "老板"

# 二面结果
uv run python3 scripts/interview/cmd_result.py --talent-id t_xxxxx --round 2 --result pass --notes "技术扎实，沟通流畅"

# 查询状态
uv run python3 scripts/common/cmd_status.py                      # 列出所有候选人
uv run python3 scripts/common/cmd_status.py --talent-id t_xxxxx  # 查单人
```

### 邮件扫描

```bash
# 扫笔试回复（输出到终端 + 推飞书）
uv run python3 scripts/exam/daily_exam_review.py

# 只扫笔试（cron 模式）
uv run python3 scripts/exam/daily_exam_review.py --auto --exam-only

# 只扫面试确认（cron 模式）
uv run python3 scripts/exam/daily_exam_review.py --auto --interview-confirm-only
```

### 飞书通知测试

```bash
uv run python3 -c "import feishu as fc; fc.send_text('测试消息')"
uv run python3 -c "import feishu as fc; fc.send_text_to_hr('发给HR的消息')"
```

---

## 八、配置与部署

### 8.1 前置条件

- Hermes Gateway 已安装并运行
- Python 3.10+，推荐先执行 `uv sync`
- PostgreSQL 数据库（版本 10+）
- 飞书企业自建应用（WebSocket 模式），已开通权限：
  - `im:message`（收发消息）
  - `calendar:calendar`、`calendar:event:write`（日历操作）
  - `contact:contact.base:readonly`（读取用户信息）

### 8.2 配置文件

所有配置文件统一放在 `/home/admin/recruit-workspace/config/`，**不提交到 Git**。参考 example 文件创建：

**`/home/admin/recruit-workspace/config/recruit-email-config.json`**（复制自 `config/recruit-email-config.example.json`）：
```json
{
  "RECRUIT_EXAM_IMAP_HOST": "imap.example.com",
  "RECRUIT_EXAM_IMAP_PORT": "993",
  "RECRUIT_EXAM_IMAP_USER": "recruit@example.com",
  "RECRUIT_EXAM_IMAP_PASS": "your_password"
}
```

**`/home/admin/recruit-workspace/config/talent-db-config.json`**（复制自 `config/talent-db-config.example.json`；仅在启用 PostgreSQL 时需要）：
```json
{
  "TALENT_DB_HOST": "localhost",
  "TALENT_DB_PORT": "5432",
  "TALENT_DB_NAME": "recruit",
  "TALENT_DB_USER": "recruit_app",
  "TALENT_DB_PASSWORD": "your_db_password"
}
```

**`/home/admin/recruit-workspace/config/dashscope-config.json`**（新建，不在 Git 中）：
```json
{
  "DASHSCOPE_API_KEY": "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

### 8.3 环境变量

在 Hermes Gateway 的启动配置中设置（或直接 export）：

```bash
export FEISHU_BOSS_OPEN_ID="ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"   # 老板飞书 open_id
export FEISHU_HR_OPEN_ID="ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"     # HR 飞书 open_id
export FEISHU_CALENDAR_ID="feishu.cn_xxxx@group.calendar.feishu.cn"  # 机器人日历 ID
export INTERVIEW_CONFIRM_TIMEOUT_MINUTES=2880  # 超时确认阈值（默认 48h）
```

**如何获取飞书 open_id：**
让对方在飞书向机器人发送任意消息，查看 Gateway 日志中的 `received message from <open_id>`。

### 8.3.1 Skill 文件链接方式

当前本地部署采用**单一源文件 + Hermes 软链接**的方式：

- 源文件：`/home/admin/recruit-workspace/docs/recruit-ops-SKILL.md`
- Hermes 运行时入口：`~/.hermes/skills/openclaw-imports/recruit-ops/SKILL.md`

其中 Hermes 路径下的 `SKILL.md` 是一个**软链接**，指向 workspace 中的源文件。

规则：

- 日常只编辑 `recruit-workspace/docs/recruit-ops-SKILL.md`
- 不要直接覆盖 `~/.hermes/skills/openclaw-imports/recruit-ops/SKILL.md`
- 如需重建链接，可执行：

```bash
rm ~/.hermes/skills/openclaw-imports/recruit-ops/SKILL.md
ln -s /home/admin/recruit-workspace/docs/recruit-ops-SKILL.md ~/.hermes/skills/openclaw-imports/recruit-ops/SKILL.md
```

### 8.4 数据库初始化

```sql
-- 创建数据库和用户
CREATE DATABASE recruit;
CREATE USER recruit_app WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE recruit TO recruit_app;
```

首次建库时，直接执行当前终态 schema 文件：

```bash
# 手动初始化 schema
psql "$DATABASE_URL" -f /home/admin/recruit-workspace/skills/recruit-ops/scripts/lib/migrations/schema.sql
```

已有数据库在 schema 变更后也应重复执行同一份 `schema.sql`。例如本轮 `talent_events.event_id` 的补列、回填和 `UNIQUE(event_id)` 切换，就是通过重复执行终态 schema 完成迁移。

### 8.5 Cron 配置

```bash
crontab -e
```

添加以下内容：

```cron
# 招聘系统 - 推荐统一入口（内部再按安装态模块调度子任务）
*/5 * * * * /path/to/recruit-ops/.venv/bin/python /path/to/recruit-ops/scripts/cron_runner.py >> /tmp/recruit-cron.log 2>&1
```

### 8.6 笔试附件

将笔试题目和数据文件放入 `exam_files/` 目录（此目录不提交到 Git）：

```
exam_files/
├── 实习生笔试题目.txt      # 笔试题目说明
├── STOCK_CODE.order.csv    # 示例数据文件
├── STOCK_CODE.transaction.csv
└── exam_package.zip        # 打包发给候选人的压缩包
```

---

## 九、运维手册

### 查看实时日志

```bash
# Hermes Gateway 运行日志
journalctl --user -u hermes-gateway -f

# 招聘 cron 日志
tail -f /tmp/recruit-cron.log

# 邮件发送日志
tail -f /tmp/email_bg.log

# 飞书日历创建日志
tail -f /tmp/feishu_calendar_bg.log
```

### 手动触发扫描

```bash
cd /home/admin/recruit-workspace/skills/recruit-ops

# 手动扫描所有（笔试 + 面试确认）
uv run python3 scripts/exam/daily_exam_review.py

# 只扫面试确认
uv run python3 scripts/exam/daily_exam_review.py --interview-confirm-only
```

### 查看当前候选人

```bash
cd /home/admin/recruit-workspace/skills/recruit-ops
uv run python3 scripts/common/cmd_status.py
```

或直接查数据库：
```bash
PGPASSWORD=your_password psql -h localhost -U recruit_app -d recruit \
  -c "SELECT talent_id, candidate_name, current_stage FROM talents ORDER BY updated_at DESC;"
```

### 处理邮件游标或审计去重异常

```bash
# 查看候选人的邮件游标
PGPASSWORD=your_password psql -h localhost -U recruit_app -d recruit \
  -c "SELECT talent_id, exam_last_email_id, round1_last_email_id, round2_last_email_id FROM talents WHERE talent_id='t_xxxxx';"

# 将某阶段邮件游标清空，让扫描器重新处理后续邮件
PGPASSWORD=your_password psql -h localhost -U recruit_app -d recruit \
  -c "UPDATE talents SET round2_last_email_id = NULL WHERE talent_id='t_xxxxx';"

# 查看该候选人最近的审计事件（现已带 event_id）
PGPASSWORD=your_password psql -h localhost -U recruit_app -d recruit \
  -c "SELECT event_id, at, actor, action FROM talent_events WHERE talent_id='t_xxxxx' ORDER BY at DESC LIMIT 20;"
```

### 常见问题

| 问题 | 排查方向 |
|------|---------|
| OC 收到 HR 消息但未录入候选人 | 检查模板格式（必须以 `【新候选人】` 或 `【导入候选人】` 开头） |
| 候选人没有收到邀请邮件 | 检查 `/tmp/email_bg.log` 和 SMTP 配置 |
| 飞书日历未创建 | 检查 `/tmp/feishu_calendar_bg.log`，确认 `FEISHU_BOSS_OPEN_ID` 已配置 |
| LLM 分析报 "未配置" | 检查 `/home/admin/recruit-workspace/config/dashscope-config.json` 中的 API Key |
| cron 扫描无输出 | 正常现象（`--auto` 模式下无新邮件时静默），查看日志确认 cron 有执行 |
| 超时通知未发送 | 检查 `exam/daily_exam_review.py` 中的 `TIMEOUT_MINUTES` 值和时区解析 |
