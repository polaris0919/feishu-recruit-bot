# 飞书招聘管家 — recruit-ops

> **版本**：当前运行态（v3.8.x，2026-05）
> **运行环境**：Hermes Gateway · Python 3.10+ · PostgreSQL 状态真源 · 飞书 WebSocket
> **代码位置**：`/home/admin/recruit-workspace/skills/recruit-ops/`

> **架构概念**：写操作只通过 **atomic CLI**（每个命令对应一个写动作 + 自验证 + 飞书告警）。
> 多步流程（如"安排一面" = 发邮件 + 推 stage + 落字段）由 agent 读
> [`docs/AGENT_RULES.md`](docs/AGENT_RULES.md) 决策，调 `lib.run_chain` 串原子 CLI 完成。

> **补充文档**
> - [`docs/AGENT_RULES.md`](docs/AGENT_RULES.md)：Agent 决策规则手册（intent → chain 矩阵 + 典型 chain 范式 + 失败处理）
> - [`docs/CLI_REFERENCE.md`](docs/CLI_REFERENCE.md)：CLI 命令总参考
> - [`docs/COMPLEX_NEGOTIATION_REGRESSION.md`](docs/COMPLEX_NEGOTIATION_REGRESSION.md)：复杂协商真实回归清单

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
| **飞书日历** | 候选人确认后先推飞书给老板；老板明确授权后才创建日历并推进到已确认阶段 |
| **二面确认护栏** | 二面必须先进入 `ROUND2_SCHEDULING`，候选人确认后再由老板授权建日历；禁止从笔试 / 一面 / WAIT_RETURN 等阶段直达 `ROUND2_SCHEDULED` |
| **超时确认提醒** | 候选人 48 小时未回复邮件，系统转入 `boss_confirm_pending` 并提醒老板手动决定，不自动建日历 |
| **状态持久化** | PostgreSQL 为唯一数据源；自动化测试通过内存 fake `talent_db` 注入隔离 |
| **批量导入** | 支持导入已有候选人并指定当前阶段，适合系统上线初期迁移历史数据 |

### 招聘阶段总览

```
NEW → ROUND1_SCHEDULING → ROUND1_SCHEDULED → EXAM_SENT → EXAM_REVIEWED
                                  │              │             │
                                  ▼              ▼             ▼
                        (reject_delete=物理删)  (EXAM_REJECT_KEEP=留池) ROUND2_SCHEDULING
                                                                     │
                                                                     ▼
                                                              ROUND2_SCHEDULED
                                                              │     │     │
                                                              ▼     ▼     ▼
                                                            (pass)(reject_keep)(reject_delete=物理删)
                                                              │
                                                              ▼
                                                       POST_OFFER_FOLLOWUP
                                                       （等发 Offer / 沟通入职）
```

| 阶段 | 含义 |
|------|------|
| `NEW` | 新建，等待安排一面 |
| `ROUND1_SCHEDULING` | 一面邀请邮件已发，等待候选人确认时间 |
| `ROUND1_SCHEDULED` | 一面时间已确认，飞书日历已创建（老板可一直停在此态等待面试 / 决定结果） |
| `EXAM_SENT` | 笔试已发送，等待候选人提交答案 |
| `EXAM_REVIEWED` | 笔试已审阅，等待决定是否进入二面 |
| `EXAM_REJECT_KEEP` | 笔试未通过，但**保留**在人才库（可未来重新激活） |
| `WAIT_RETURN` | 候选人暂时不在国内/上海，待回国后按记录轮次恢复排期 |
| `ROUND2_SCHEDULING` | 二面邀请已发出，等候选人确认；老板日历尚未落盘 |
| `ROUND2_SCHEDULED` | 二面时间已确认，等待面试 / 等老板出结果（无独立"待定"状态，老板可一直停在此态） |
| `ROUND2_DONE_REJECT_KEEP` | 二面**面试**未通过，保留在人才库（v3.8.2 起严格只承载"我们 say no"语义） |
| `OFFER_DECLINED_KEEP` | 候选人拿到 Offer 后**主动拒绝**，但保留在人才库（v3.8.2 拆出；语义上是"候选人 say no"，区别于 `ROUND2_DONE_REJECT_KEEP`） |
| `POST_OFFER_FOLLOWUP` | 二面已通过，HR 已收到飞书通知准备发 Offer；老板/HR 与候选人通过邮件/Hermes 智能体沟通入职 |
| `ONBOARDED` | 候选人已完成入职流程（叶子终态，v3.8 新增） |

> **状态机收口历史**：
> - **v3.3（4 月 22 日）**：删除 `ROUND1_DONE_PASS` / `ROUND2_DONE_PASS`（通过 = 直接进下一阶段，不需中间态），删除 `ROUND2_DONE_PENDING`（老板想拖延就停在 `ROUND2_SCHEDULED`），删除 `ROUND1_DONE_REJECT_KEEP`（一面未通过 = 直接物理删），笔试"未通过保留"独立成 `EXAM_REJECT_KEEP`。
> - **v3.6（4 月 27/28 日）**：删除 `OFFER_HANDOFF`（瞬时态，合并入 `POST_OFFER_FOLLOWUP`），删除 `ROUND1_DONE_REJECT_DELETE` / `ROUND2_DONE_REJECT_DELETE`（`reject_delete` 直接物理删除，不经停 stage）。状态机从 14 个 stage 压到 11 个。
> - **v3.8（5 月 10 日）**：新增 `ONBOARDED` 终态（招聘流程胜利收尾）。
> - **v3.8.2（5 月 11 日）**：新增 `OFFER_DECLINED_KEEP`，从 `ROUND2_DONE_REJECT_KEEP` 拆出"拒 Offer 留池"独立终态（事故源 [docs/INCIDENT_RULES.md §14](docs/INCIDENT_RULES.md)）。状态机扩展到 13 个。
> - **二面确认硬规则**：老板第一次给出的二面时间只是候选人邀请时间；任何上游路径都必须先进入 `ROUND2_SCHEDULING`，候选人确认后再由老板明确授权创建日历并写入 `ROUND2_SCHEDULED`。

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
    agent 调 lib.run_chain 串：
      outbound.cmd_send --template round1_invite + talent.cmd_update --stage ROUND1_SCHEDULING
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
ROUND1_SCHEDULED   agent: feishu.cmd_calendar_delete    自动默认确认
飞书日历已创建      + outbound.cmd_send 改期             飞书日历已创建
                  + talent.cmd_update --set round1_time
           ↓
    老板面试完成后：记录一面结果
           ↓
  ┌────────┴────────────┐
  通过                  拒绝（reject_delete）
  │                     │
  ▼                     ▼
EXAM_SENT          发拒信 + 从DB彻底删除（物理删除，不经停 stage）
（直接发笔试，无独立"一面通过"中间态；
 一面未通过即移除人才库，不保留）
           ↓
    候选人回复提交答案（自动扫描，每 12h）
    预审：代码质量、答题时间、附件分析
           ↓
    老板审阅后：记录笔试结果
           ↓
  ┌────────┼────────────┐
通过       拒绝（保留）  拒绝（删除）
  │           │             │
  ▼           ▼             ▼
ROUND2_SCHEDULING  EXAM_REJECT_KEEP  发拒信 + 物理删除

【旁路】候选人 ≥3 天未交答卷 → cron auto_reject.cmd_scan_exam_timeout 自动：
  发 rejection_exam_no_reply 拒信 + talent.cmd_delete 物理删档（归档可恢复，v3.8.3）
→ 后台发送二面邀请邮件给候选人
→ 等候候选人确认
           ↓
    候选人回复确认（自动扫描，每 8h）
           ↓
    推飞书提醒老板确认是否建日历
           ↓
    老板明确授权创建二面日历
           ↓
ROUND2_SCHEDULED
→ 飞书日历已创建（含邀请老板）
           ↓
  ┌────────┼────────────┐
确认       改期         48h 超时提醒老板
           ↓
    老板二面完成后：记录结果
    （二面没有独立"待定"状态，老板想拖延就让候选人停在 ROUND2_SCHEDULED）
           ↓
  ┌────────┼─────────────┐
通过       拒绝（保留）   拒绝（删除）
  │           │              │
  ▼           ▼              ▼
POST_OFFER_FOLLOWUP  ROUND2_DONE_REJECT_KEEP  发拒信 + 物理删除
（v3.6：原 OFFER_HANDOFF 瞬时态已合并，此 stage 同时承担"等 HR 发 offer"和"沟通入职"两个语义；
 同步触发 HR Feishu 通知准备发 offer）
           ↓
    候选人在 POST_OFFER_FOLLOWUP 期间也可能主动 decline（拿了 offer 又拒）：
       拒绝（保留）           拒绝（删除）
       │                       │
       ▼                       ▼
   OFFER_DECLINED_KEEP    发拒信 + 物理删除
   （v3.8.2 拆出，语义
    区别于 ROUND2_DONE_REJECT_KEEP）
→ 邮件由 inbox.cmd_scan + inbox.cmd_analyze 统一接管。
  老板通过飞书卡片或 Hermes 智能体回信，agent 直接调原子 CLI：
    outbound.cmd_send --use-cached-draft EMAIL_ID
    （或 --subject + --body-file + --in-reply-to 自由文本）
    + talent_db.mark_email_status(EMAIL_ID, 'snoozed'|'dismissed')
  （结案/snooze 语义在 talent_emails.status 层面维护。详见 docs/AGENT_RULES.md §3、§5）
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
| 一面完成 / 一面通过 | `EXAM_SENT`（自动跳过"一面通过"中间态，直接发笔试） |
| 笔试中 / 已发笔试 | `EXAM_SENT` |
| 笔试完成 / 笔试已审 | `EXAM_REVIEWED` |
| 待安排二面 | `EXAM_REVIEWED` |
| 待回国后一面 | `WAIT_RETURN`（`wait_return_round=1`） |
| 待回国后二面 | `WAIT_RETURN`（`wait_return_round=2`） |
| 二面邀请中 | `ROUND2_SCHEDULING` |
| 二面已确认 / 二面完成 | `ROUND2_SCHEDULED`（无独立"待定"状态，由老板拍板下一步） |

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

系统通过系统级 cron 每隔一段时间自动执行以下任务（统一由 `scripts/cron/cron_runner.py` 触发，v3.3 起替代旧 `scripts/cron_runner.py`）：

| cron 任务 | 频率（推荐） | 说明 |
|---------|------|------|
| `cron.cron_runner` | 每 10–30 分钟 | 串行触发下列子任务，对失败 / 心跳缺失推送飞书告警 |
| · `inbox.cmd_scan` | （由 runner 触发） | IMAP → `talent_emails`，**v3.8.4 起**跳过两个终态 `ONBOARDED` + `OFFER_DECLINED_KEEP`（终态分权,详见 [docs/INCIDENT_RULES.md §16](docs/INCIDENT_RULES.md)）；其他 stage 候选人邮件统一入口 |
| · `inbox.cmd_analyze` | （由 runner 触发） | LLM **stage 感知**分类（POST_OFFER_FOLLOWUP 走 `prompts/post_offer_followup.json` 含草稿生成；其他阶段走 `prompts/inbox_general.json`，覆盖确认 / 改期 / 暂缓 / 线上请求 / 笔试提交等 intent）+ 推飞书。**v3.8.4 stage-aware override**：`ROUND{N}_SCHEDULING` 阶段的 `confirm_interview` 强制 `need_boss_action=true` → 推 warn 卡等老板拍板建日历（场景 8 分权） |
| · `common.cmd_interview_reminder` | （由 runner 触发） | 面试结束后未出结果催问 |
| · `auto_reject.cmd_scan_exam_timeout --auto` | （由 runner 触发） | 笔试 ≥3 天未交 → 即触发拒信 + `talent.cmd_delete` 物理删档（自动归档到 `data/deleted_archive/`）+ 飞书事后通知含 archive 路径（**v3.8.3** 回退到 v3.5 之前的删档行为；v3.5.11~v3.8.2 期间是"拒+留池 `EXAM_REJECT_KEEP`"，事故应激修复，详见 [docs/INCIDENT_RULES.md §15](docs/INCIDENT_RULES.md)） |
| · `ops.cmd_health_check` | 每天 09 点 | DB / IMAP / SMTP / DashScope / Feishu 体检 |
| `cron_health.py --alert` | 每 1 小时（独立 cron） | 心跳超过 26h 未更新时告警，作为 cron_runner 自身死掉的兜底 |

### 超时确认机制

- 发送一面/二面邀请邮件后，**48 小时内**候选人若未回复
- 系统会**把候选人转入 boss_confirm_pending 状态**并推送飞书提醒老板（**不会自动确认**）
  - 老板需回复「确认 {tid} {round_label}」才会真正最终确认 + 创建飞书日历
- 通过环境变量 `INTERVIEW_CONFIRM_TIMEOUT_MINUTES` 可调整（默认 2880 分钟 = 48h）

### LLM 意图分析

使用 DashScope API（qwen3-max）分析候选人邮件回复：

| 意图 | 触发条件 | 系统行为 |
|------|---------|---------|
| `confirm_interview` | "可以"、"没问题"、"OK" 等 | 只推飞书提醒老板确认；老板明确授权后才创建日历并推进到 `ROUND{N}_SCHEDULED` |
| `reschedule` | "不方便"、"改一下"、"换个时间" 等 | 通知老板，附候选人建议时间 |
| `request_online` | "线上"、"视频面试"、"腾讯会议" 等 | 通知老板，记录候选人需求 |
| `defer_until_shanghai` | "暂时不在国内"、"之后再约" 等 | 自动暂缓本轮，等待候选人回上海再约 |
| `timeout` | 超过 48h 未回复 | 转入 boss_confirm_pending + 飞书提醒老板手动决定 |
| `unknown` | 意图不明 | 推送飞书请老板人工判断 |

### 可观测性

- **心跳**：`$RECRUIT_DATA_ROOT/.cron_heartbeat`（本机 `/home/admin/recruit-files/.cron_heartbeat`）由 `cron.cron_runner` 每次成功跑完后更新
- **告警**：任一子任务非零退出 / 超时 / Feishu 投递失败 → 推 `[CRON FAIL] ...` 给老板
- **重入保护**：`flock /tmp/recruit-cron-runner.lock`，已有实例运行时本次跳过
- **手动健康检查**：`python3 scripts/cron_health.py --alert`（可独立部署到 cron）

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
scripts/                                # 全部为 atomic CLI（agent 用 lib.run_chain 编排）
  ├── tests/
  │   ├── conftest.py                  # pytest 基建：env 兜底 + fixture (mem_tdb / tmp_data_root) (v3.8.7 C3)
  │   ├── helpers.py                   # 内存 DB / call_main / wipe_state（unittest 风格测试用）
  │   └── test_*.py                    # 跑测试: 仓库根 `uv run pytest` 或 `.venv/bin/python -m pytest`
  ├── lib/                             # 公共模块（agent / atomic CLI 共享）
  │   ├── config.py                    # 统一配置加载（DB/飞书/邮件/DashScope）
  │   ├── core_state.py                # 状态机、阶段定义、审计
  │   ├── talent_db.py                 # PostgreSQL 读写（RealDictCursor、参数化 round）
  │   ├── exam_grader.py               # LLM 笔试评分
  │   ├── exam_imap.py                 # IMAP / MIME 工具
  │   ├── run_chain.py                 # lib.run_chain：进程内串原子 CLI
  │   ├── feishu/                      # 飞书 SDK 封装（IM + 日历）
  │   ├── bg_helpers.py                # 后台邮件 / 日历子进程封装
  │   ├── migrations/
  │   │   └── schema.sql               # 当前数据库终态定义（手动初始化用）
  │   └── …
  ├── intake/                          # 简历与候选人录入（atomic）
  ├── talent/                          # 候选人状态/字段唯一写入入口
  │   ├── cmd_update.py                # 状态机 + 字段（atomic, --stage / --set）
  │   └── cmd_delete.py                # 物理删除 + 归档（atomic）
  ├── outbound/                        # 出站邮件唯一入口
  │   └── cmd_send.py                  # 模板 / 自由文本 / --use-cached-draft（atomic）
  ├── inbox/                           # 入站三件套（atomic）
  │   ├── cmd_scan.py                  # IMAP → talent_emails（所有 stage 统一入口）
  │   ├── cmd_analyze.py               # stage 感知 LLM 分类 + 推飞书
  │   └── cmd_review.py                # 候选人邮件时间线（只读）
  ├── interview/
  │   └── cmd_result.py                # 面试结果（--round 1|2，atomic）
  ├── exam/                            # 笔试（atomic）
  │   ├── cmd_exam_result.py           # 笔试结果 → 推 stage（atomic）
  │   ├── cmd_exam_ai_review.py        # AI 笔试评审 CLI（调 lib.exam_grader）
  │   └── fetch_exam_submission.py     # 拉取候选人提交（用 lib.exam_imap）
  ├── feishu/                          # 飞书 sink atomic CLIs
  │   ├── cmd_calendar_create.py       # 创建日历事件
  │   ├── cmd_calendar_delete.py       # 删除日历事件
  │   └── cmd_notify.py                # 统一飞书消息推送
  ├── auto_reject/
  │   └── cmd_scan_exam_timeout.py     # 笔试 ≥3 天未交 → 拒信 + 物理删档（v3.8.3）
  ├── common/                          # 查询、删除、催问等横切命令
  ├── ops/                             # 跨 sink 运维（cmd_db_migrate / cmd_health_check / cmd_replay_notifications）
  ├── cron/                            # v3.3 cron 编排器（cron_runner.py）
  ├── prompts/                         # 所有 LLM prompt 配置 JSON
  └── trigger_cron_now.py              # 手动提前触发 cron

docs/
  ├── AGENT_RULES.md                   # Agent 决策矩阵 + 典型 chain
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
| 邮件去重 | `talent_emails` 表 `(talent_id, message_id) UNIQUE` 物理去重；`talent_events.event_id` 保证事件幂等（原 `talents.*_last_email_id` 单游标 v3.5.2 已 DROP, migration `20260421_v35_drop_dead_columns.sql` v3.8.7 已删档） |
| 时区处理 | 所有时间戳统一以本地 CST（UTC+8）存储和显示 |
| 状态持久化 | PostgreSQL 为唯一数据源；配置统一由 `lib/config.py` 管理 |
| 配置管理 | `lib/config.py` 统一加载 JSON 配置文件 + 环境变量，替代分散的配置逻辑 |
| DB 模式 | `RealDictCursor` 消除 `row[N]` 硬编码；数据库结构由 `lib/migrations/schema.sql` 维护 |
| 脚本合并 | round1/round2 同类脚本合并到 `interview/`，接受 `--round 1\|2` 参数；旧路径通过包装脚本保持兼容 |
| 模块间通信 | 关键路径由 `subprocess` 调用改为直接函数调用，减少 fork 开销和进程隔离问题 |

---

## 七、脚本速查

### 候选人管理（全部走 atomic CLI；多步编排见 docs/AGENT_RULES.md）

```bash
cd /home/admin/recruit-workspace/skills/recruit-ops

# 推荐统一前缀：`PYTHONPATH=scripts uv run python3 -m <module>` （避免子模块 import 路径问题）

# 录入新候选人
PYTHONPATH=scripts uv run python3 -m intake.cmd_new_candidate --template "【新候选人】\n姓名：张三\n邮箱：zhangsan@example.com"

# 安排一面（agent 用 lib.run_chain 串以下两步原子 CLI）
PYTHONPATH=scripts uv run python3 -m outbound.cmd_send --talent-id t_xxxxx --template round1_invite \
  --vars '{"round1_time":"2026-03-25 14:00","interviewer":"老板"}'
PYTHONPATH=scripts uv run python3 -m talent.cmd_update --talent-id t_xxxxx --stage ROUND1_SCHEDULING \
  --set round1_time="2026-03-25 14:00" --set round1_invite_sent_at=__NOW__

# 记录一面结果（通过，进入笔试）
PYTHONPATH=scripts uv run python3 -m interview.cmd_result --talent-id t_xxxxx --round 1 --result pass --email zhangsan@example.com

# 笔试结果（通过，先发二面候选时间邀请；候选人确认后仍需老板授权建日历）
PYTHONPATH=scripts uv run python3 -m exam.cmd_exam_result --talent-id t_xxxxx --result pass \
  --round2-time "2026-04-01 14:00" --interviewer "老板"

# 二面结果
PYTHONPATH=scripts uv run python3 -m interview.cmd_result --talent-id t_xxxxx --round 2 --result pass --notes "技术扎实，沟通流畅"

# 查询状态
PYTHONPATH=scripts uv run python3 -m common.cmd_status                      # 列出所有候选人
PYTHONPATH=scripts uv run python3 -m common.cmd_status --talent-id t_xxxxx  # 查单人
```

### 邮件扫描 / 分析（统一走 inbox/）

```bash
# 扫所有候选人邮件（写 talent_emails；不调 LLM）
PYTHONPATH=scripts uv run python3 -m inbox.cmd_scan

# 对未分析邮件做 stage 感知 LLM 分类，并按规则推飞书
PYTHONPATH=scripts uv run python3 -m inbox.cmd_analyze

# 看某候选人邮件时间线
PYTHONPATH=scripts uv run python3 -m inbox.cmd_review --talent-id t_xxxxx
```

### 飞书通知

```bash
# 推送一条消息给老板
PYTHONPATH=scripts uv run python3 -m feishu.cmd_notify --to boss --text "需要确认 张三 一面改期"

# Python 内嵌测试
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

### 8.3.1 Skill 文件链接方式（v3.6 起：目录级软链）

当前本地部署采用**目录级软链接**：整个 `recruit-ops` skill 目录被软链到 Hermes 加载路径。

- 源目录：`/home/admin/recruit-workspace/skills/recruit-ops/`
- Hermes 运行时入口：`~/.hermes/skills/openclaw-imports/recruit-ops/`（指向上面的源目录）

规则：

- 日常只编辑 `skills/recruit-ops/SKILL.md` 和 `skills/recruit-ops/docs/*.md`
- **不要**直接覆盖 Hermes 那端的任何文件
- 改完任何 doc **不需要 `cp`**——Hermes 重启后直接读到新内容

如需重建链接：

```bash
rm -rf ~/.hermes/skills/openclaw-imports/recruit-ops
ln -sfn /home/admin/recruit-workspace/skills/recruit-ops \
        ~/.hermes/skills/openclaw-imports/recruit-ops
```

详细见 [docs/OPERATIONS.md §3](docs/OPERATIONS.md#3-hermes-加载与-skillmd-同步目录级软链)。

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
# 招聘系统 v3.3 — 推荐统一入口（cron/cron_runner 内部按任务表调度子任务）
*/10 * * * * cd /path/to/recruit-ops && PYTHONPATH=scripts ./.venv/bin/python -m cron.cron_runner >> /tmp/recruit-cron.log 2>&1
```

### 8.6 笔试附件

笔试邀请邮件的题包附件由 `email_templates.auto_attachments` 的 `exam_invite` resolver 自动注入（v3.8.4 起；之前在 `interview/cmd_result.py::_get_exam_attachments` 里硬编码，已下线）。

resolver 按以下优先级找题包，命中第一个非空文件即用，全部缺失会 **fail-fast 拒发邀请**（保证不会裸发"题目已作为附件"但漏附件）：

| 优先级 | 路径 | 用途 |
|---|---|---|
| 1 | `$RECRUIT_DATA_ROOT/exam_package/笔试题.tar.gz` | 推荐位置 |
| 2 | `$RECRUIT_DATA_ROOT/exam_package/笔试题.zip` | 同上目录的 zip 备选 |
| 3 | `$RECRUIT_DATA_ROOT/exam_package/笔试题.tar` | 同上目录的 tar 备选 |
| 4 | `skills/recruit-ops/exam_files/exam_package.zip` | 老路径（git-untracked），仅作向后兼容 |

> HR 想换题：直接用同名文件覆盖 `$RECRUIT_DATA_ROOT/exam_package/笔试题.tar.gz` 即可，无需改代码；想加额外候选格式去改 `email_templates/auto_attachments.py::_resolve_exam_invite_attachments`。

`skills/recruit-ops/exam_files/` 目录仍保留 git-tracked 的评分配置：

```
exam_files/
├── rubric.json             # 笔试评分细则（AI 预审用）
├── followup_prompt.json    # followup LLM prompt
└── exam_package.zip        # 兼容老路径的题包占位（可空，仅用于优先级 3）
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

# 手动扫所有候选人邮件
PYTHONPATH=scripts uv run python3 -m inbox.cmd_scan
PYTHONPATH=scripts uv run python3 -m inbox.cmd_analyze

# 或者直接走完整 cron（含 inbox.cmd_scan + analyze + 提醒 + 自动拒绝）
PYTHONPATH=scripts uv run python3 -m cron.cron_runner
```

### 查看当前候选人

```bash
cd /home/admin/recruit-workspace/skills/recruit-ops
PYTHONPATH=scripts uv run python3 -m common.cmd_status
```

或直接查数据库：
```bash
PGPASSWORD=your_password psql -h localhost -U recruit_app -d recruit \
  -c "SELECT talent_id, candidate_name, current_stage FROM talents ORDER BY updated_at DESC;"
```

### 处理邮件游标或审计去重异常

> **2026-04-20 起新架构**：`talent_emails` 表是去重的 source of truth。需要让 scanner 重新处理某封邮件时，要清的是 `talent_emails` 行，不是 `talents.*_last_email_id`：
> ```bash
> PGPASSWORD=$pw psql -h localhost -U recruit_app -d recruit -c "
>   DELETE FROM talent_emails WHERE talent_id='t_xxx' AND message_id='<...@example.com>';"
> ```
> 下一轮 cron 跑到时即会重新识别为新邮件并走完整流程。

```bash
# 查看候选人最近的邮件流水（邮件去重唯一真源在 talent_emails 表）
PGPASSWORD=your_password psql -h localhost -U recruit_app -d recruit \
  -c "SELECT email_id, direction, context, status, ai_intent, sent_at, subject \
      FROM talent_emails WHERE talent_id='t_xxxxx' \
      ORDER BY sent_at DESC LIMIT 20;"

# 让扫描器重新处理某封邮件：直接删 talent_emails 那一行，
# 下次 inbox.cmd_scan 跑到时会作为新邮件重新识别 + 推飞书
PGPASSWORD=your_password psql -h localhost -U recruit_app -d recruit \
  -c "DELETE FROM talent_emails WHERE talent_id='t_xxxxx' AND message_id='<...>';"

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
| 超时通知未发送 | 检查 `auto_reject/cmd_scan_exam_timeout.py`（笔试超时）与 `common/cmd_interview_reminder.py`（面试无结果催问）的 `TIMEOUT_MINUTES` 值和时区解析 |
