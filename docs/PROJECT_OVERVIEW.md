# `recruit-workspace` 项目总览


---


## 一、架构

```text
┌──────────────────────────────────────────────────────────────────┐
│                       Feishu (HR + 老板)                          │
└──────────────────────────────────┬───────────────────────────────┘
                                   │ 自然语言消息 / 模板 / CV 附件
                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│             Hermes Gateway + recruit-ops SKILL.md                │
│   （路由：触发词 + 安全规则 + 命令映射；mutating 命令需 propose）   │
└──────────────────────────────────┬───────────────────────────────┘
                                   │ 解析为精确 CLI 调用
                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│         recruit-ops CLI（Python，~20.6k 行，39 个 cmd_*.py）       │
│ intake / interview / round1+2 / exam / followup / auto_reject /   │
│ common / email_templates / lib                                     │
└──┬───────────┬────────────┬───────────┬─────────────────┬────────┘
   │           │            │           │                 │
   ▼           ▼            ▼           ▼                 ▼
┌─────────┐ ┌──────┐ ┌────────┐ ┌────────────┐ ┌──────────────────┐
│PostgreSQL│ │ IMAP │ │  SMTP  │ │ Feishu IM  │ │   DashScope LLM  │
│(唯一真源)│ │(收信)│ │(发信)  │ │ + Calendar │ │ (qwen3-max)      │
└─────────┘ └──────┘ └────────┘ └────────────┘ └──────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  cron_runner.py（每 30 min） + cron_health.py（每 1h）            │
│   失败 → Feishu 告警 / 心跳缺失 → Feishu 告警 / flock 互斥         │
└──────────────────────────────────────────────────────────────────┘
```

**核心设计原则**：

| # | 原则 | 体现 |
|---|---|---|
| 1 | **PG 是唯一状态真源** | 任何"从内存中总结状态"的回答都是错的；都必须 `cmd_status.py` 兜底 |
| 2 | **Skill 决定路由，CLI 决定状态** | Agent 只是把消息翻译成命令，业务逻辑完全在 Python 里 |
| 3 | **状态机由 CHECK + ensure_stage_transition 双层约束** | 数据库层 + 应用层都禁止非法跃迁 |
| 4 | **Audit 全留痕** | 所有状态变更写 `talent_events` 表，actor + payload + 时间戳 |
| 5 | **mutating 命令必 propose** | Agent 提出命令 → 老板说 yes 才执行 |
| 6 | **side-effect guard 默认拦** | `enable_dry_run()` 一键拦 SMTP/Feishu/日历/DB 写 |

---

## 二、仓库结构

```text
recruit-workspace/
├── README.md                       # 工作区首页
├── AGENTS.md                       # 通用 agent 规范
├── LICENSE
├── config/                         # 运行时配置（不提交真实凭据）
│   ├── openclaw.json              # Feishu app_id / app_secret / open_id
│   ├── talent-db-config.json      # PostgreSQL 连接
│   ├── recruit-email-config.json  # IMAP（收信）
│   ├── email-send-config.json     # SMTP（发信）
│   └── dashscope-config.json      # 阿里 LLM
├── data/                           # 运行时数据
│   ├── exam_txt/                  # 笔试归档（候选人提交的代码 / 邮件正文）
│   ├── standard_answer/           # 老板提供的笔试参考答案
│   ├── followup_pending/          # 飞书推送失败时短暂保留（供 --replay 补推），不再当"待办"用
│   ├── followup_archive/          # 所有跟进邮件归档（按 YYYY-MM 分片，scanner 推送成功即落此处）
│   ├── media/                     # CV 等附件落地
│   └── .cron_heartbeat            # cron 心跳文件
├── docs/
│   ├── recruit-ops-SKILL.md       # Hermes 主 SKILL（route + safety + 651 行）
│   └── PROJECT_OVERVIEW.md        # 本文档
└── skills/
    └── recruit-ops/                # 主产品
        ├── README.md
        ├── pyproject.toml + uv.lock
        ├── docs/
        │   ├── CLI_REFERENCE.md   # 全部 CLI 详细参考
        │   └── COMPLEX_NEGOTIATION_REGRESSION.md
        ├── exam_files/
        │   ├── rubric.json        # 笔试评分细则
        │   └── followup_prompt.json # followup LLM prompt
        ├── scripts/                # 全部 Python 代码
        │   ├── intake/             # CV 录入
        │   ├── interview/          # 一面 / 二面统一逻辑
        │   ├── round1/ round2/     # 薄 wrapper
        │   ├── exam/               # 笔试 + 笔试超时扫描
        │   ├── followup/           # Offer 后跟进
        │   ├── auto_reject/        # 笔试超时即触即拒删（仅 cmd_scan_exam_timeout 一个脚本）
        │   ├── email_templates/    # 候选人邮件模板（含 rejection_*.txt）
        │   ├── common/             # 跨阶段公共
        │   ├── lib/                # 共享基础设施
        │   ├── cron_runner.py      # cron 总入口（5 任务）
        │   ├── cron_health.py      # deadman watcher
        │   └── tests/              # 174 个测试函数
        └── .venv/                  # uv 管理的 Python 环境
```

| 数字 | 值 |
|---|---|
| Python 总行数（`scripts/`） | 20,628 |
| `cmd_*.py` CLI 入口 | 39 个 |
| 数据库表 | 3（`talents` + `talent_events` + `talent_emails`） |
| 测试函数数 | 174 |
| 候选人 stage 数 | 14 |

---

## 三、候选人状态机

### 3.1 11 个 stage（v3.6 收口后）

| `current_stage` | 中文显示 | 含义 |
|---|---|---|
| `NEW` | 新建 | 候选人刚录入，尚未排面 |
| `ROUND1_SCHEDULING` | 一面排期中 | 已发一面邀请，等候选人确认 |
| `ROUND1_SCHEDULED` | 一面已安排 | 候选人确认 + 飞书日历已建（老板可一直停在此态等待面试 / 决定结果） |
| `EXAM_SENT` | 笔试已发送 | 等候选人交答案 |
| `EXAM_REJECT_KEEP` | 笔试未通过（保留） | 笔试不通过但保留人才池，未来可重新激活 |
| `EXAM_REVIEWED` | 笔试已审阅 | AI 预审 + 老板确认 |
| `WAIT_RETURN` | 待回国后再约 | 候选人暂在国外 |
| `ROUND2_SCHEDULING` | 二面排期中 | 已发二面邀请 |
| `ROUND2_SCHEDULED` | 二面已确认 | 飞书日历已建（无独立"待定"状态，老板想拖延就停在此态） |
| `ROUND2_DONE_REJECT_KEEP` | 二面未通过（保留） | 进人才池 |
| `POST_OFFER_FOLLOWUP` | 已结束面试流程，等待发放 Offer / 沟通入职 | 二面通过后直接进入此 stage；Hermes 接管邮件跟进，HR 发 offer / 谈入职 |

> **v3.6（2026-04-27/28）变更**：
> - 删除 `OFFER_HANDOFF`：它只是 `round2 pass` 通知 HR 后 1-tick 的瞬时态，从不持久化。现在 `interview.cmd_result --round 2 --result pass` 直接一步推到 `POST_OFFER_FOLLOWUP`（HR Feishu 通知不变）。
> - 删除 `ROUND1_DONE_REJECT_DELETE` / `ROUND2_DONE_REJECT_DELETE`：`reject_delete` 从来都是"发拒信 + `talent_db.delete_talent()`"物理删除，不经停任何 stage。这两个"占位枚举"留着只会让 agent/boss 以为"删了还能查到"。

### 3.2 状态流转图

```text
                          ┌───────┐
                          │  NEW  │
                          └───┬───┘
                              │ cmd_round1_schedule
                              ▼
                  ┌──────────────────────┐
                  │   ROUND1_SCHEDULING  │◀──┐
                  └─┬────────┬──────────┬┘   │ candidate reschedule
                    │        │          │    │ request
                    │        │          ▼    │
                    │        │  ┌───────────┐│
                    │        │  │ROUND1_SCH-││
                    │        │  │  EDULED   ││
                    │        │  └─────┬─────┘│
                    │        │        │ cmd_result
                    │        │  ┌─────┴───────────────┐
                    │        │  │                     │
       ┌────────────┘    ┌───┴──┴───┐  ┌──────┐  ┌───┴────┐
       │ defer           │EXAM_SENT │  │ pass │  │REJECT_*│
       ▼                 │          │  │direct│  └────────┘
 ┌───────────┐           └────┬─────┘  │      │
 │WAIT_RETURN│                │       │      │
 └─────┬─────┘                │ daily_exam_review
       │ resume               ▼      │
       │              ┌─────────────┐│
       │              │EXAM_REVIEWED││
       │              └────┬────────┘│
       │                   │ cmd_exam_result
       │                   ▼         ▼
       │            ┌──────────────────┐
       └───────────▶│  ROUND2_SCHEDULING│◀── reschedule
                    └────────┬──────────┘
                             ▼
                    ┌──────────────────┐
                    │ ROUND2_SCHEDULED │ ◀── 想拖延就停在这里
                    └────────┬─────────┘    （没有独立的 PENDING 状态）
                             │ cmd_result --round 2
                  ┌──────────┼─────────────┐
                  ▼                        ▼
         ┌──────────────────────┐   ┌─────────────────┐
         │  POST_OFFER_FOLLOWUP │   │ REJECT_KEEP /    │
         │  （二面通过，等发 Offer│   │ DELETE（物理删人）│
         │   + HR Feishu 通知） │   └─────────────────┘
         └──────────────────────┘
```

**关键约束**：
- DB 层：`schema.sql` 用 `CHECK` 把所有非法 stage 字符串拒掉（v3.6 收口到 11 个枚举）
- 应用层：`core_state.ensure_stage_transition` 在每个 mutating CLI 入口校验「from-set → to」是否合法
- Audit：每次状态变更都写 `talent_events`，含 actor / event_id / payload

#### 3.2.1 状态机收口历史

**v3.3（2026-04-22）**：消除冗余中间态

| 删除的状态 | 删除原因 | 替代路径 |
|---|---|---|
| `ROUND1_DONE_PASS` | 一面通过 = 直接发笔试，没人在该状态停留 | `ROUND1_SCHEDULED` → `EXAM_SENT` |
| `ROUND2_DONE_PASS` | 二面通过 = 直接进入 OFFER 流程 | `ROUND2_SCHEDULED` → `POST_OFFER_FOLLOWUP` |
| `ROUND2_DONE_PENDING` | 老板想拖延决策时，候选人停在 `ROUND2_SCHEDULED` 即可 | 不需要独立"待定"中间态 |
| `ROUND1_DONE_REJECT_KEEP` | 一面没通过的人统一删除（不再保留人才池） | 改用 `--result reject_delete` |

新增独立状态：
- **`EXAM_REJECT_KEEP`**：笔试未通过但保留人才池。原本笔试 `reject_keep` 借用的是 `ROUND1_DONE_REJECT_KEEP`，命名误导；现在独立命名，语义清晰。

**v3.6（2026-04-27/28）**：再次收口，从 14 个 stage 压到 11 个

| 删除的状态 | 删除原因 | 替代路径 |
|---|---|---|
| `OFFER_HANDOFF` | 只是 round2_pass → 通知 HR 后 1-tick 的瞬时态，从不持久化。线上 0 行。 | `ROUND2_SCHEDULED` → `POST_OFFER_FOLLOWUP`（一步；HR Feishu 通知依然在 `interview.cmd_result` 里发） |
| `ROUND1_DONE_REJECT_DELETE` | `_handle_reject_delete` 直接走 `talent_db.delete_talent()` 物理删除，从来不进这个 stage。线上 0 行。 | 无（物理删除前已发拒信） |
| `ROUND2_DONE_REJECT_DELETE` | 同上 | 无 |

迁移脚本：
- `20260427_v36_drop_offer_handoff.sql`：UPDATE 兜底 + DROP/ADD CHECK 约束去掉 `OFFER_HANDOFF`
- `20260428_v36_drop_done_reject_delete.sql`：UPDATE 兜底 + DROP/ADD CHECK 约束去掉两个 `*_DONE_REJECT_DELETE`

老板手动操作影响：
- 一面后输入 `--result reject_keep` → 报错，提示改用 `reject_delete`
- 二面后输入 `--result pending` → argparse 直接拒绝（已不在 `--result` 候选项里）
- 笔试后 `--result reject_keep` → 状态变为 `EXAM_REJECT_KEEP`
- 二面后 `--result pass` → 直接到 `POST_OFFER_FOLLOWUP`（不再经停 `OFFER_HANDOFF`）
- `--result reject_delete`（一面/笔试/二面）→ 发拒信 + 物理删除，DB 里查不到这个人了

#### 3.2.2 `talents.pending_rejection_id`（已下线）

> 4 月 22 日加入、4 月 23 日删除。配合 `auto_reject` 软自动化的 12h 缓冲队列存在，新版即触即拒删后此字段不再需要。迁移文件：`lib/migrations/20260423_drop_pending_rejection_id.sql`（DROP COLUMN IF EXISTS）。详见 §13 重写后的自动拒架构。

---

## 四、主要需要讨论的问题

### 4.1 邮件审阅机制（`talent_emails` 表 + 唯一约束去重）

**背景**：邮件是与候选人沟通的唯一通道，系统必须持续扫描候选人来信，识别意图、推进流程。系统覆盖 4 路扫描：

- 笔试回信扫描（exam）
- 一/二面确认扫描（round1/round2）
- 改期请求扫描（round1/round2 reschedule）
- POST_OFFER_FOLLOWUP 跟进扫描（followup）

cron 每 30 min 触发一次。

**当前架构（2026-04-20 起）**：所有候选人邮件统一落入 `talent_emails` 表，以 `UNIQUE (talent_id, message_id)` 作为物理去重底线，scanner 调用 `tdb.insert_email_if_absent(...)` —— 返回 `email_id` 即新邮件，返回 `None` 即 ON CONFLICT 命中，直接 `continue`。

`talent_emails` 关键字段：

| 字段 | 作用 |
|---|---|
| `email_id` (UUID PK) | 内部主键 |
| `(talent_id, message_id)` UNIQUE | 物理去重 source of truth |
| `direction` (`inbound`/`outbound`) | 来信 / 老板回信 |
| `context` (`exam`/`round1`/`round2`/`followup`/`intake`) | 业务上下文 |
| `status` | 状态机：`received` → `pending_boss` / `auto_processed` → `replied` / `dismissed` / `snoozed` |
| `body_full` / `body_excerpt` | 全文 + 去引用片段 |
| `ai_summary` / `ai_intent` / `ai_payload` | LLM 分析结果 |
| `reply_id` | 飞书卡片回执 ID（仅 followup 走交互式回复时填） |
| `replied_by_email_id` | 反向链接：哪封 outbound 邮件回了我 |

**写表时机**（重要设计抉择）：所有 scanner **先做只读探测**（`get_processed_message_ids`）→ **再跑 LLM** → **成功后才落表**。这避免"LLM 失败 / 异常退出 → 邮件却被记成已处理 → 永远不会再被扫到"的死锁。状态在落表那一刻就被设为最终态（`auto_processed` 或 `pending_boss`），不存在中间态。

**遗留与降级**：
- `talents.<ctx>_last_email_id` 列保留双写以兼容旧代码路径，标记 `[DEPRECATED 2026-04-20]`，下个 release 删除。
- `pending_store.seen_message_ids()` 仅在 DB 抛异常时作 fallback，正常路径不再触达。

**2026-04-22 跟进语义重构 —— 不再跟踪"老板是否回信"**：

老板线下/IM 也会处理候选人邮件，系统不该假设"必须在系统内回信才算闭环"。旧实现把 `data/followup_pending/` 当待办列表，结果每天 review 显示一个虚高的"待处理 N 封"，里面大半是老板早就线下处理过的邮件。

调整后的 followup 数据流：

1. **scanner 推完飞书立即归档**：新邮件落 `talent_emails` → 推飞书卡片（含 `reply_id`）→ 立即把 `data/followup_pending/<reply_id>.json` 搬到 `data/followup_archive/<YYYY-MM>/`，`outcome=pushed`。`pending/` 目录只在飞书推送失败时短暂保留供 `--replay-pending-to-feishu` 补推。
2. **`cmd_followup_reply` 双源查找**：`pending_store.load_any(reply_id)` 先查 `pending/`、找不到去 `archive/<月>/` 翻；老板用任何 `reply_id` 都能回信，对老板透明。
3. **archive 文件的 outcome 原地更新**：老板回信时通过 `pending_store.update_archive_outcome(reply_id, "replied", ...)` 直接覆盖 archive 文件的 `outcome` 字段，不挪文件，原 `outcome=pushed` 入 `outcome_history`。
4. **同一封邮件不再被分配多个 reply_id**：`scanner._process_candidate` 在 `insert_email_if_absent` 返回 None（ON CONFLICT 命中）时，会调 `tdb.find_email_by_message_id(...)` 反查；若该邮件已经分配过 `reply_id` 就直接 `continue`；若只落了 talent_emails 但还没分配过 `reply_id`（典型场景：backfill 写入），则复用已有的 `email_id` 走完飞书推送 + archive 流程。
5. **删除 `cmd_followup_list.py`**：旧实现把"pending 数 = 待办数"这个误导指标外露给老板；统一改用 `common/cmd_email_thread.py --talent-id <id>` 查任意候选人的全量邮件时间线（in/out 全在 `talent_emails` 表里）。

**关联故障（4-20 候选人L / 万峰睿同邮件多 reply_id）**：4-20 上午老 scanner 跑过一次写了一批 `pending/fr_xxx.json`；下午引入 `talent_emails` 物理去重后又跑了一次 —— 因为 pending 文件名是随机 `reply_id` 不能反查 `message_id`，scanner 不知道"我之前为这封邮件已经分配过 reply_id 了"，于是又生成一份 `pending/fr_yyy.json`。结果 7 条 pending 残留里有 6 条是同邮件重复 + 1 条是老板线下已处理。`find_email_by_message_id` 二次去重 + scanner 推完即归档双管齐下，杜绝再次发生。

**历史故障（2026-04-20 候选人L/万峰睿事件）**：旧"单游标"机制只能挡"等于游标那一封"，挡不住任何比游标老但未处理过的邮件。每轮 `for msg in inbox: if msg.id != cursor:` 把游标推走后，下轮入口重新读 DB 还是初始游标，于是同一封历史邮件被反复识别。现场 2 封邮件被放大成 6 张飞书卡片 / 6 条 `followup_received` 事件；同期 `t_ib6vnn` 3 封邮件被放大成 9 张。新架构下 `(talent_id, message_id)` UNIQUE 在 DB 层面物理保证"同一封邮件最多落一行"，从根本上杜绝重放。

---
## 五、模块职责

### 5.1 `scripts/intake/` — 候选人录入

| 文件 | 职责 |
|---|---|
| `cmd_ingest_cv.py` | HR 发 CV 附件 → 自动 OCR / LLM 解析 → 预览 |
| `cmd_attach_cv.py` | 把 CV 路径关联到已存在的 talent |
| `cmd_new_candidate.py` | 处理「【新候选人】」模板 → 写 PG（`stage=NEW`） |
| `cmd_import_candidate.py` | 处理「【导入候选人】」模板 + 历史阶段 |
| `cmd_send_cv.py` | 把 CV PDF 通过飞书发给老板 / HR |

### 5.2 `scripts/interview/` — 一面 / 二面统一逻辑

| 文件 | 职责 |
|---|---|
| `cmd_confirm.py` | 候选人或老板确认时间 → `mark_confirmed` + 飞书日历 |
| `cmd_result.py` | 录入面试结果（pass / pass_direct / pending / reject_*） |
| `cmd_reschedule.py` | 老板主动改期，发邮件 + 改 stage |
| `cmd_defer.py` | 候选人在国外暂缓 → `WAIT_RETURN` |

`scripts/round1/`、`scripts/round2/` 是上面 4 个文件的薄 wrapper，用 `--round 1/2` 透传。

### 5.3 `scripts/exam/` — 笔试

| 文件 | 职责 |
|---|---|
| `daily_exam_review.py` | **1783 行**单文件 cron 入口；扫笔试 + 扫二面确认 + LLM 意图分析 |
| `fetch_exam_submission.py` | 拉取候选人提交内容（IMAP + 解码） |
| `exam_prereview.py` | 启发式预审（语言 / 长度 / 格式） |
| `exam_ai_reviewer.py` | rubric-driven LLM 评审 |
| `cmd_exam_ai_review.py` | AI 审 CLI（两步 propose：先 preview 再 `--feishu --save-event`） |
| `cmd_exam_result.py` | 老板拍板结果（pass → ROUND2_SCHEDULING / reject） |

### 5.3.1 `scripts/auto_reject/` — 笔试超时即触即拒删（2026-04-23 重写）

| 文件 | 职责 |
|---|---|
| `cmd_scan_exam_timeout.py` | 唯一 CLI；扫 `EXAM_SENT` ≥3 天且无 inbound 的候选人，命中即调 `executor._send_rejection_email` + `executor._delete_talent`，最后推飞书事后通知 |
| `executor.py` | 子进程包装：`_run_cmd` / `_send_rejection_email`（`subprocess` 调 `outbound.cmd_send --template rejection_exam_no_reply`） / `_delete_talent`（`subprocess` 调 `talent.cmd_delete`），自带 v3.3 self-verify |

**触发源**：
- `cron.cron_runner` 任务 5：`auto_reject.cmd_scan_exam_timeout --auto`

**已下线**：`pending_store.py` / `llm_classify.py` / `cmd_propose.py` / `cmd_execute_due.py` / `cmd_cancel.py` / `cmd_list.py`、12h 缓冲队列、`data/auto_reject_pending|archive/`、`talents.pending_rejection_id` 字段全部删除。改期请求改回 `common.cmd_reschedule_request` 由老板手动决定。详见 §13（已重写）。

### 5.4 `scripts/followup/` — Offer 后跟进

| 文件 | 职责 |
|---|---|
| `followup_scanner.py` | 扫 `POST_OFFER_FOLLOWUP` 候选人 IMAP；双重去重（时间 + Message-ID） |
| `followup_analyzer.py` | LLM 一句话意图 + AI 草稿（薪资沟通 / 入职时间 / 等等） |
| `pending_store.py` | `data/followup_pending/<reply_id>.json` 原子读写 |
| `smtp_sender.py` | 发回信 + 维护邮件线程头（`In-Reply-To` / `References` 折叠） |
| `cmd_followup_reply.py` | 回信 / snooze / dismiss / close（支持 `--use-draft` / `--dry-run`，`reply_id` 自动从 pending+archive 双源查找） |
| `cmd_followup_close.py` | 关闭整个 followup |

### 5.4.1 `scripts/exam/cmd_exam_timeout_scan.py` — 已下线（2026-04-23）

旧入口在 v3.3 已删除，等价命令 `auto_reject.cmd_scan_exam_timeout`（行为同时简化为即触即拒删，参见 §5.3.1）。

### 5.5 `scripts/common/` — 跨阶段公共

| 文件 | 职责 |
|---|---|
| `cmd_status.py` | DB 查询：单人详情 / 全量列表 / 按状态过滤 |
| `cmd_search.py` | 候选人搜索（姓名 / 邮箱 / 学校） |
| `cmd_today_interviews.py` | 今日面试列表 |
| `cmd_interview_reminder.py` | cron 任务 2：面试结束未出结果催问 |
| `cmd_finalize_interview_time.py` | 老板敲定时间 |
| `cmd_reschedule_request.py` | 候选人发邮件改期请求 → mark pending |
| `cmd_wait_return_resume.py` | 从 `WAIT_RETURN` 恢复 |
| `cmd_remove.py` | 删除候选人（高危） |
| `cmd_email_preview.py` | **新**：渲染任意邮件模板到 stdout（不发邮件、零副作用，给老板/AI review 话术用） |
| `cmd_debug_candidate.py` | 调试 dump |

### 5.6 `scripts/email_templates/` — 候选人邮件模板（2026-04-22 新增）

把 6 封候选人邮件正文从 `_send_xxx_email` 函数里的硬编码字符串拼接，迁出到独立的纯文本模板，实现"模板与代码分离"。修改话术不需要 review Python diff，直接 review 模板文件即可。

**目录结构**（2026-04-22 起按用途分组；模板名仍是全局唯一扁平名，调用方 `renderer.render("rejection_generic", ...)` 不变）：

```
email_templates/
├── renderer.py / constants.py / __init__.py    # 引擎与常量
├── _fragments/                                 # 共享片段（include 用）
├── invite/                                     # 面试邀请
├── exam/                                       # 笔试邀请
├── reschedule/                                 # 改期 / 暂缓
└── rejection/                                  # 拒信
```

| 路径 | 职责 |
|---|---|
| `renderer.py` | string.Template 渲染 + fragment `$$include(name)$$` 展开 + 子目录递归查找 + fail-fast 变量校验（缺变量直接 `KeyError`，防止 `$candidate_name` 字符串发出去） |
| `constants.py` | `COMPANY` / `LOCATION` / `round_label(n)`：候选人语言里 round=1→"第一轮"、round=2→"第三轮" |
| `invite/round1_invite.txt` | 一面邀请：实习要求靠前（≥3 个月、每周 ≥4 天）+ 完整三轮流程介绍（一面线下/二面笔试/三面线下） |
| `invite/round2_invite.txt` | 二面邀请（笔试通过后约线下复试） |
| `exam/exam_invite.txt` | 笔试邀请（一面通过后发题） |
| `reschedule/reschedule.txt` | 老板主动改期通知 |
| `reschedule/reschedule_ack.txt` | 候选人改期请求的回执 |
| `reschedule/defer.txt` | 候选人在国外，暂缓本轮 |
| `rejection/rejection_generic.txt` | 通用拒信（warm 口吻；`_handle_reject_delete` 复用此模板） |
| `rejection/rejection_exam_no_reply.txt` | 笔试 3 天未提交自动拒（honest 口吻，明说理由） |
| `_fragments/process_overview.txt` | "完整面试流程" 共享片段（在 round1_invite / exam_invite 中 include） |
| `_fragments/intern_requirements.txt` | "实习要求" 共享片段 |
| `_fragments/footer.txt` | 公司落款 + `TALENT_ID:` 标记（被 followup_scanner 反向定位候选人）|

**调用方式**：

```python
from email_templates import renderer
from email_templates.constants import COMPANY, LOCATION
subject, body = renderer.render(
    "round1_invite",
    candidate_name="张三", round1_time="2026-04-25 14:00", ...
)
```

**Review 模板**：`python3 -m common.cmd_email_preview --template round1_invite --demo`

**候选人语言 vs 系统命名**：候选人收到的邮件用"三轮制"语言（一面=第一轮、笔试=第二轮、二面=第三轮），但系统内部状态机仍是 `ROUND1_* / EXAM_* / ROUND2_*`。`round_label(n)` 把系统侧的 `round_num` 翻译成候选人语言，封装在 `constants.py`，不要在调用点散写硬编码。

### 5.8 `scripts/lib/` — 共享基础设施

| 文件 | 职责 |
|---|---|
| `talent_db.py` | PostgreSQL 全部读写（986 行）；含 `_update` 守护、follow-up 字段维护 |
| `core_state.py` | `STAGES` / `STAGE_LABELS` / `ensure_stage_transition` |
| `config.py` | 多文件 JSON + env 合并加载 |
| `recruit_paths.py` | 路径常量（workspace_root / config_dir / exam_archive_dir） |
| `feishu/__init__.py` | 飞书 IM + 日历 SDK 封装；最近接入重试 |
| `feishu/calendar_cli.py` | 日历独立 CLI |
| `dashscope_client.py` | **新**：统一 LLM HTTP 入口（含重试） |
| `http_retry.py` | **新**：指数退避通用重试 |
| `file_lock.py` | **新**：fcntl 锁 + 原子 JSON 写 |
| `side_effect_guard.py` | `RECRUIT_DISABLE_SIDE_EFFECTS` / `RECRUIT_DISABLE_DB_WRITES` 闸门 |
| `bg_helpers.py` | 后台进程发邮件 / 建日历（避免阻塞 CLI） |
| `migrations/schema.sql` | 唯一 DDL 源 |

### 5.9 `scripts/tests/` — 测试

- 146 个 `def test_*` 函数（含 17 个新增的 email_templates 测试）
- 域覆盖：candidate / intake / round1 / round2 / exam / common / infra / followup / email_templates
- 入口：`PYTHONPATH=scripts python3 -m tests.run_all`（需要从 `scripts/` 目录跑）
- 当前 146/146 全部通过

---

## 六、数据流详解

### 6.1 数据持久化

| 介质 | 存什么 | 备份策略 |
|---|---|---|
| **PostgreSQL** | 候选人状态机（`talents`）+ 全部审计事件（`talent_events`） | 标准 PG 备份 |
| **`data/followup_pending/`** | 仅"飞书推送失败"的来信短暂停留（供 `--replay-pending-to-feishu`），不再代表"待老板回信" | 文件备份 |
| **`data/followup_archive/YYYY-MM/`** | 所有 followup 来信的归档（scanner 推送成功立即落此处；老板回信 / snooze / dismiss / close 后原地更新 outcome） | 文件备份 |
| **`data/exam_txt/`** | 候选人提交的笔试代码 / 邮件正文落地 | 文件备份 |
| **`data/standard_answer/`** | 老板提供的笔试参考答案 | 一次性 |

### 6.2 数据库 schema

```sql
-- talents（候选人状态机）
talent_id PK, candidate_email, candidate_name,
current_stage CHECK IN (...17 个...),
round1_*  / round2_*（confirm_status / time / invite_sent_at / calendar_event_id / ...）,
exam_*（exam_id / exam_sent_at / last_email_id），
followup_*（status / entered_at / snoozed_until / last_email_id），
created_at, updated_at

-- talent_events（审计）
event_id UUID PK, talent_id FK, action, actor, payload JSONB, at TIMESTAMPTZ
ON CONFLICT (event_id) DO NOTHING
```

### 6.3 三路 IMAP 扫描

| 扫描器 | 触发 | 目的 |
|---|---|---|
| `daily_exam_review`（exam 段） | cron | 笔试答案邮件 → AI 预审 + 飞书提示 |
| `daily_exam_review`（interview confirm 段） | cron | 候选人对面试邀请的回信 → LLM 意图 → confirm/reschedule/defer/timeout |
| `followup_scanner` | cron | POST_OFFER_FOLLOWUP 候选人来信 → AI 意图 + 草稿 + 飞书 reply_id 卡片 |

去重统一靠：`*_last_email_id`（Message-ID 游标）+ 时间窗口下限（`*_invite_sent_at` / `followup_entered_at`）

### 6.4 LLM 调用

全部走 `lib/dashscope_client.chat_completion`：

| 调用方 | 用途 | timeout | retries |
|---|---|---|---|
| `exam/exam_ai_reviewer._call_dashscope` | rubric-driven 笔试评分 | 90s | 2 |
| `exam/daily_exam_review._llm_analyze_reply` | 面试回信意图（confirm/reschedule/...） | 30s | 2 |
| `followup/followup_analyzer.analyze` | followup 意图 + AI 草稿 | 30s | 2 |

模型：默认 `qwen3-max-2026-01-23`。

### 6.5 Feishu 集成

| 通道 | 用途 |
|---|---|
| `send_text(boss_open_id, ...)` | 推消息给老板（最常见） |
| `send_text_to_hr(...)` | 推消息给 HR（Offer 处理通知等） |
| `create_calendar_event(...)` | 一面 / 二面确认后建日历 |
| `delete_calendar_event_by_id(...)` | 改期时删旧日历 |

`send_text` 最近接入指数退避重试（瞬态 5xx / 限流 → 重试 2 次）。

---

## 七、Hermes Skill 集成

### 7.1 SKILL.md 结构

`docs/recruit-ops-SKILL.md`（651 行）由 Hermes Gateway 加载，定义：

```yaml
---
name: recruit-ops
description: ...
triggers:
  - 招聘
  - 候选人
  - 一面 / 二面 / 笔试
  - 审阅 / 评审 / AI 审
  - 通过了 / 拒了 / 不合适
  - .pdf / .docx
  - ...
---
```

正文包含：
1. **§2 安全规则**：mutating 命令必须先 propose；`<`/`>` 占位符必须替换；`PYTHONPATH=scripts` 必带
2. **§3 SoP**：先读 PG 再答 / 不能从内存总结
3. **§4 命令路由表**：boss 自然语言 → 精确 CLI（含一面 / 二面 / 笔试 / followup / Offer）
4. **§8 故障处理**：常见错误的诊断 + 下一步动作
5. **§12 路由示例**：典型对话片段

### 7.2 安装路径

| 路径 | 角色 |
|---|---|
| `docs/recruit-ops-SKILL.md` | 源 |
| `recruit-workspace-public/docs/recruit-ops-SKILL.md` | 公共仓副本 |
| `~/.hermes/skills/openclaw-imports/recruit-ops/SKILL.md` | Hermes 实际加载位置 |

更改后必须**三处同步**，否则 Hermes 不会感知。

### 7.3 路由示例

```
老板：审阅候选人A的笔试邮件

Hermes：（识别 "审阅" + "笔试" 触发词）
       → 命中 SKILL §4.3 笔试 AI 评审
       → mutating 命令需 propose
       → 提议命令：
           uv run python3 scripts/exam/cmd_exam_ai_review.py --candidate "候选人A" --preview-only
       → 等老板说 "yes" 才执行
```

---

## 八、自动化与可观测性

### 8.1 cron 拓扑

```text
crontab:
*/10 * * * *   → python -m cron.cron_runner   (v3.3, 替代旧 scripts/cron_runner.py)
0    * * * *   → cron_health.py --alert
```

**`cron.cron_runner`** 串行触发若干子任务，每个 240s 超时（实际任务表见 `scripts/cron/cron_runner.py::_TASKS`）：

1. `inbox.cmd_scan` — IMAP → `talent_emails`（v3.3）
2. `inbox.cmd_analyze` — LLM 分类未读入站邮件（v3.3）
3. `followup.followup_scanner --auto` — Offer 后跟进扫描
4. `common.cmd_interview_reminder` — 面试结束催问
5. `auto_reject.cmd_scan_exam_timeout --auto` — 笔试 ≥3 天未交 → 即触即拒删（无缓冲，2026-04-23 简化）
6. `ops.cmd_health_check --skip dashscope`（每天 09 点） — 系统体检

> 注：旧任务 `exam.daily_exam_review` 已**不再**由 cron_runner 自动触发（v3.3 inbox/cmd_scan + cmd_analyze 已覆盖入站邮件分流）。若需手动跑笔试 AI 评审，单独调 `exam.cmd_exam_ai_review`。

### 8.2 安全机制（最近加固）

| 机制 | 实现 | 文件 |
|---|---|---|
| **互斥锁** | `fcntl.flock` on `/tmp/recruit-cron-runner.lock`，已运行实例 → 跳过 | `cron_runner.py:27-55` |
| **失败告警** | 任一子任务非零退出 / 异常 / 超时 → 推 `[CRON FAIL]` 给老板（含 stderr 末段） | `cron_runner.py:145-159` |
| **心跳** | 每次成功跑完写 `data/.cron_heartbeat`；启动时检查若上次成功 ≥ 25h 前 → 推 `[CRON HEARTBEAT GAP]` | `cron_runner.py:175-195` |
| **Deadman watcher** | 独立 `cron_health.py --alert` 也检查同一心跳文件 | `cron_health.py:1-69` |
| **Feishu 投递失败检测** | `cron_runner._run_and_report` 检查 `send_text` 返回值，失败也告警 | `cron_runner.py:158-159` |

### 8.3 dry-run 模型

```python
from side_effect_guard import enable_dry_run
enable_dry_run()  # 一键拦下：SMTP / Feishu / 日历 / DB 写
```

| 环境变量 | 拦截范围 |
|---|---|
| `RECRUIT_DISABLE_SIDE_EFFECTS=1` | SMTP / Feishu / 日历 / 后台邮件 |
| `RECRUIT_DISABLE_DB_WRITES=1` | `talent_db._update` / `upsert_one` / `sync_state_to_db` |

### 8.4 日志

- 全部 `print(file=sys.stderr)`（无 `logging` 模块）
- BG 任务日志写 `/tmp/email_*.log` / `/tmp/feishu_cal_*.log`
- cron_runner 的 stderr 会被告警自动附带给老板

---

## 九、配置与部署

### 9.1 配置文件

`<RECRUIT_WORKSPACE>/config/`（5 文件，**不进 git**）：

| 文件 | 关键字段 |
|---|---|
| `openclaw.json` | `feishu.app_id` / `app_secret` / `boss_open_id` / `hr_open_id` / `calendar_id` |
| `talent-db-config.json` | PG `host` / `port` / `dbname` / `user` / `password` |
| `recruit-email-config.json` | IMAP `host` / `port` / `user` / `pass`（QQ 邮箱） |
| `email-send-config.json` | SMTP（同上 QQ 邮箱） |
| `dashscope-config.json` | `api_key` / `model` / `url` |

### 9.2 安装

```bash
# 1. 安装依赖
cd <RECRUIT_WORKSPACE>/skills/recruit-ops
uv sync

# 2. 初始化 DB schema（幂等）
psql "$DATABASE_URL" -f scripts/lib/migrations/schema.sql

# 3. 启动 cron
crontab -e
# 加入：
*/10 * * * * cd <RECRUIT_WORKSPACE>/skills/recruit-ops && PYTHONPATH=scripts .venv/bin/python -m cron.cron_runner
0 * * * *    cd <RECRUIT_WORKSPACE>/skills/recruit-ops && .venv/bin/python scripts/cron_health.py --alert
```

### 9.3 验证

```bash
# 看候选人状态
PYTHONPATH=scripts python3 scripts/common/cmd_status.py --all

# 看心跳
python3 scripts/cron_health.py --threshold 999

# 跑测试
PYTHONPATH=scripts python3 scripts/tests/run_all.py
```

### 9.4 Hermes Skill 同步

```bash
cp docs/recruit-ops-SKILL.md \
   /home/admin/recruit-workspace-public/docs/ \
   /home/admin/.hermes/skills/openclaw-imports/recruit-ops/SKILL.md
```

三处 MD5 必须一致，否则 Hermes 行为偏离文档。

---

## 十、最近一轮稳定性加固（4 月 20 日）

针对工程评审发现的 P0/P1 风险，本轮完成：

| 模块 | 改动 | 价值 |
|---|---|---|
| `cron_runner.py` | 重写：lockfile + Feishu 失败告警 + 心跳 + 调用返回值检查 | 「cron 静默死掉」→「死了系统会喊我」 |
| `cron_health.py` | 新增独立 deadman watcher | cron_runner 自身死掉的兜底 |
| `lib/file_lock.py` | 新增 fcntl 锁 + 原子 JSON 写 | followup pending 不再撕裂 |
| `lib/http_retry.py` | 新增指数退避通用工具 | LLM / Feishu 偶发 5xx 不再让全流程作废 |
| `lib/dashscope_client.py` | 新增统一 DashScope 入口 | 三处重复的 urllib 代码收敛 + retry 统一 |
| `lib/side_effect_guard.py` | 新增 `db_writes_disabled()` + `enable_dry_run()` | `--dry-run` 真正全无副作用（含 DB 写） |
| `pending_store.py` | 重写：原子写 + flock 锁 | scanner 与 CLI 不会再因为同时写 JSON 撕坏 |
| `lib/feishu/__init__.py` | `send_text` 接重试 | 飞书瞬态错误自动重试 2 次 |
| `tests/test_followup.py` | 新增 15 个测试（quote stripping / header flatten / pending round-trip / retry / dry-run） | followup 模块从 0 测试到 15 测试 |
| `README.md` §五 | 修正「48h 自动确认」误导文案 | 避免老板根据错误文档做决策 |

烟测全过：lockfile 互斥 / 失败告警 / 心跳缺口 / dry-run DB 拦截 全部按预期工作。

---

## 十之二、邮件 Source-of-Truth 重构（4 月 21 日）

针对 §4.1 中 4-20 候选人L / 万峰睿事件的根因（单游标只能挡一封），把所有候选人邮件纳入一张正式的 `talent_emails` 表统一管理：

| 模块 | 改动 | 价值 |
|---|---|---|
| `lib/migrations/schema.sql` | 新增 `talent_emails` 表 + `(talent_id, message_id) UNIQUE` + 状态/方向/上下文 CHECK + 5 个索引 + `updated_at` 自动维护触发器 | DB 层面物理保证「同一封邮件最多落一行」 |
| `lib/talent_db.py` | 新增 6 个 API：`insert_email_if_absent` / `mark_email_status` / `get_processed_message_ids` / `list_emails_by_status` / `get_email_by_reply_id` / `get_email_thread` | 统一 scanner / reply / monitoring 三种调用方式 |
| ~~`lib/migrations/backfill_talent_emails.py`~~ | 当时新增的一次性回填脚本，已于后续清理中删除 | 历史 15 封邮件无损迁入（任务已完成） |
| `followup/followup_scanner.py` | 主路改 `tdb.insert_email_if_absent` 去重，旧 `seen_message_ids` 仅作 DB 异常 fallback | 新邮件 1 封只能产生 1 张 reply_id 卡片 |
| `followup/cmd_followup_reply.py` | 老板回信 / snooze / dismiss / close 都同步写 `talent_emails.status` + `replied_by_email_id` | 邮件状态机端到端可追溯 |
| `exam/daily_exam_review.py` 三路扫描 | 都先做只读探测 → 跑 LLM → 成功才落表（`status='auto_processed'` 或 `'pending_boss'`） | 关掉「LLM 失败 → 邮件状态半吊子」窗口 |
| `tests/test_talent_emails.py` | 新增 21 个测试：契约校验 / ON CONFLICT / 状态机部分更新 / dry-run 写保护 / 回填幂等 | DB 去重路径全覆盖 |
| `docs/PROJECT_OVERVIEW.md` §4.1 / `CLI_REFERENCE.md` / `README.md` | 同步更新去重描述 | 文档与代码一致 |

**烟测**：169 个单测全过（4 个 pre-existing 失败与本次无关）；`talent_emails` 当前 15 行（6 followup + 7 round2 + 1 round1 + 1 exam），按 status 分布合理（9 auto_processed + 3 dismissed + 2 replied + 1 pending_boss）。

**已规划下一步**（不在本次范围）：
- 删除 `talents.<ctx>_last_email_id` 列（先 deprecate 一个 release 周期，确认无老路径依赖再删）
- 老板查"某候选人完整邮件时间线"的 CLI（已有 `get_email_thread` API，缺包装）

---

## 十之三、自动拒删模块（4 月 22 日加入 → 4 月 23 日大幅简化）

### 当前实现（2026-04-23 起）

只覆盖一个场景，且**即触即终**，不再有缓冲队列、撤销窗口、`pending_rejection_id` 字段。

| 场景 | 触发源 | 命中后 |
|---|---|---|
| **笔试 3 天未交** | `auto_reject.cmd_scan_exam_timeout`（cron 任务 5） | 立即调子进程 `outbound.cmd_send --template rejection_exam_no_reply` 发拒信 → `talent.cmd_delete` 删人 → 推飞书事后通知 |
| **临近改期** | `daily_exam_review._run_reschedule_scan` | **不**自动拒。所有 reschedule 意图统一走 `common.cmd_reschedule_request`，飞书推卡片让老板看原文后手动决定 |

### 决策原则

1. **写动作只走 v3.3 唯一出口**：`executor.py` 只剩两个 helper：`_send_rejection_email`（subprocess 调 `outbound.cmd_send`）+ `_delete_talent`（subprocess 调 `talent.cmd_delete`）。所有写都自动套上 v3.3 的 self-verify 机制。
2. **拒信发不出去就不删人**：`_send_rejection_email` 失败 → 本轮不调 `_delete_talent`，记到 `failed=N` 计数 + 飞书告警 + 留候选人在 `EXAM_SENT`，下一轮 cron 再扫。
3. **改期决策回归人**：原"24h 内 + LLM=casual → 12h 缓冲队列 → 自动拒"链路全部移除（场景偏复杂、误伤代价高、白名单运营成本高）。
4. **拒信模板**：
   - 笔试超时拒：`rejection/rejection_exam_no_reply.txt`（honest 口吻，明说"未在约定时间内提交"）
   - 老板手动 `interview/cmd_result.py --result reject_delete`：`rejection/rejection_generic.txt`（warm，含"已保留至我们公司人才库"）；`--skip-email` 可绕过

### 数据流

```text
┌─────────────────────────────────────────────────────┐
│ auto_reject.cmd_scan_exam_timeout (cron task 5)    │
│   ├── current_stage='EXAM_SENT'                     │
│   ├── exam_sent_at ≥ --threshold-days (default 3)   │
│   └── talent_emails 无 exam_sent_at 之后的 inbound  │
└──────────────────────┬──────────────────────────────┘
                        │ for each candidate:
                        ▼
            ┌──────────────────────────────────┐
            │ executor._send_rejection_email   │
            │ subprocess: outbound.cmd_send    │
            │   --template rejection_exam_no_reply
            └──────────┬───────────────────────┘
                        │
                  ┌─────┴─────┐
                  ▼           ▼
              succeeded     failed
                  │           │
                  ▼           ▼
   ┌────────────────────┐  ┌─────────────────────────────┐
   │ executor._delete_  │  │ failed += 1                 │
   │ talent (subproc)   │  │ 飞书告警 + 候选人留在 EXAM_SENT │
   │ + 飞书事后通知       │  │ 下一轮 cron 再扫            │
   └────────────────────┘  └─────────────────────────────┘
```

### 已下线

| 项 | 替代 / 去向 |
|---|---|
| `auto_reject/cmd_propose.py` | 删除 |
| `auto_reject/cmd_cancel.py` | 删除 |
| `auto_reject/cmd_execute_due.py` | 删除（cron 任务表也对应砍掉） |
| `auto_reject/cmd_list.py` | 删除（要看历史走 `inbox.cmd_review --talent-id X` + `talent.cmd_show`） |
| `auto_reject/pending_store.py` | 删除 |
| `auto_reject/llm_classify.py` | 删除 |
| `data/auto_reject_pending/` `data/auto_reject_archive/` | 不再写入；可保留作历史（不影响新流程） |
| `talents.pending_rejection_id` 列 | `lib/migrations/20260423_drop_pending_rejection_id.sql` DROP COLUMN |
| `daily_exam_review._try_propose_late_reschedule_auto_reject` | 函数删除；reschedule 一律 `cmd_reschedule_request` |
| `rejection/rejection_late_reschedule.txt` | 已合并到 `rejection_generic.txt` |

### 测试

`tests/test_auto_reject.py`（精简后）：
- `test_main_rejects_and_deletes_each_candidate` — happy path 真跑，mock executor 子进程
- `test_dry_run_does_not_call_executor` — `--dry-run` 不触发任何子进程
- `test_send_failure_keeps_candidate` — 拒信失败时候选人不被删
- `test_find_timeout_candidates_*` — 阈值 / inbound 过滤等条件覆盖

全套测试通过（231 / 233，2 个 PYTHONPATH 相关失败为已知 pre-existing）。

### 老板视角速查

| 操作 | 命令 |
|---|---|
| 看哪些会被自动拒（不真跑） | `PYTHONPATH=scripts python3 -m auto_reject.cmd_scan_exam_timeout --dry-run` |
| 调阈值（如改成 5 天） | `PYTHONPATH=scripts python3 -m auto_reject.cmd_scan_exam_timeout --dry-run --threshold-days 5` |
| 紧急停掉自动拒 | 临时把 `cron/cron_runner.py::_TASKS` 中的 `exam_timeout_scan` 注释掉，然后 `crontab -e` 重启 |
| 想给某人手动发拒信 + 删 | `interview/cmd_result.py --talent-id X --round N --result reject_delete`（自动发 `rejection_generic` 再删，`--skip-email` 可绕过） |

详细 CLI 见 [skills/recruit-ops/docs/CLI_REFERENCE.md#自动拒绝-auto_reject](../skills/recruit-ops/docs/CLI_REFERENCE.md#自动拒绝-auto_reject)。

---

## 十之四、v3.3 解耦命令体系（4 月 23 日重构）

针对老命令体系的两个根本痛点 ——「同一个写动作有多条入口」和「写动作完成后没有自验证，错误悄悄沉默」—— 做了一次架构重构。这一节解释设计原则、模块划分、与老命令的关系，以及如何在 Phase 9 完成后彻底切流量。

### 设计原则

| 原则 | 实现 |
|---|---|
| **单一职责**：每个 CLI 脚本只做一件事 | `outbound/cmd_send.py` 只发邮件、`talent/cmd_update.py` 只改 stage/字段、`talent/cmd_delete.py` 只删人 |
| **零隐藏副作用**：发邮件的不改 stage，改 stage 的不发邮件 | 老板/agent 必须**显式串联**：先 `cmd_send` 再 `cmd_update`；任一步失败下一步不会自动回滚，但飞书会告警 |
| **写后即验**：每个写脚本带 self-verify | `lib/self_verify.py` 5 个断言函数（email_sent / emails_inserted / email_analyzed / talent_state / talent_deleted）；失败抛 `SelfVerifyError` → `cli_wrapper` 推飞书 |
| **失败必告警**（D5）：写脚本任何 crash 都推飞书 | `lib/cli_wrapper.run_with_self_verify` 包裹所有 v3.3 写脚本；通过 `ops.cmd_push_alert` 推送 |
| **告警与业务报错分流** | 真 crash / SelfVerify 失败 → 推飞书；用户输入错（缺 `--force` / talent_id 不存在 / 模板缺变量）→ `UserInputError`，仅 stderr，不告警 |
| **跨 stage 须显式 `--force`**（D2） | `talent/cmd_update.py` 维护「自然流转」白名单，自然流转直接放行；非自然必须 `--force --reason "..."` |
| **每次写动作都走子进程 CLI**（D2） | `auto_reject/executor.py` 已重构为 subprocess 调用 `outbound.cmd_send` + `talent.cmd_delete`，不再绕过 self-verify |

### 模块划分（11 个新脚本 + 4 个新 lib）

| 模块 | 脚本 | 责任 |
|---|---|---|
| `inbox/` | `cmd_scan.py` | IMAP → `talent_emails`（`analyzed_at IS NULL`） |
| | `cmd_analyze.py` | LLM 分类未读入站 + 推飞书 + set `analyzed_at` |
| | `cmd_review.py` | 只读：候选人邮件 timeline（含 AI intent / template） |
| | `analyzer.py` | 通用 LLM 入站邮件分析器（覆盖所有 stage） |
| `outbound/` | `cmd_send.py` | **唯一**发邮件出口，模板/自由文本双模式，零业务副作用 |
| `talent/` | `cmd_add.py` | 创建候选人（template 解析 + 字段输入两种模式） |
| | `cmd_update.py` | **唯一**改 stage / 字段出口，带 natural-transitions + `--force` |
| | `cmd_delete.py` | **唯一**删候选人出口，自动归档到 `data/deleted_archive/` |
| | `cmd_show.py` | 只读：单个候选人完整快照（含邮件统计 + 审计） |
| | `cmd_list.py` | 只读：按 stage / search / has-unanalyzed 筛选 |
| `ops/` | `cmd_db_migrate.py` | 增量 SQL 迁移（用新表 `recruit_migrations` 跟踪） |
| | `cmd_health_check.py` | DB / IMAP / SMTP / DashScope / Feishu 5 项体检 + backlog |
| | `cmd_push_alert.py` | **唯一**飞书告警入口（`cli_wrapper` / cron / agent 都走这里） |
| | `cmd_replay_notifications.py` | 回放遗漏的入站分析卡片（不改 DB） |
| `template/` | `cmd_preview.py` | 模板列表 / 渲染预览（替代 `common/cmd_email_preview`） |
| `exam/` | `cmd_exam_ai_review.py` | 笔试 AI 评审（v3.3 包装 `cmd_review_submission.py` 已删除，复用本脚本） |
| `auto_reject/` | `cmd_scan_exam_timeout.py` | v3.3 笔试超时排队（替代 `exam/cmd_exam_timeout_scan`） |
| `cron/` | `cron_runner.py` | v3.3 编排器（互斥锁 + heartbeat + 失败必告警），新增 inbox 二件套 + health_check |
| `lib/` | `self_verify.py` | 5 个断言函数 + `SelfVerifyError` |
| | `cli_wrapper.py` | `run_with_self_verify` + `UserInputError` 分流 |
| | `smtp_sender.py`（迁出） | 从 `followup/` 迁来，`followup/smtp_sender.py` 改为 forwarder（Phase 9 删） |

### 与老命令的关系

- **没删任何老命令**：v3.3 是叠加，不是覆盖。Phase 9 才会清理掉重复脚本。
- **新流程一律走 v3.3**。SKILL.md §1.3.1 给出了 12 条「老 → 新」对照，agent 路由时优先 v3.3。
- **存量调用方（cron / executor）已切到 v3.3**：`cron/cron_runner.py` 使用 v3.3 模块；`auto_reject/executor.py` 已 subprocess 化到 `outbound.cmd_send` + `talent.cmd_delete`；笔试评审仍走 `exam/cmd_exam_ai_review.py`（v3.3 包装层 `cmd_review_submission.py` 已下线，节约一层薄壳）。
- **schema 改动**（D3 一次性）：`talent_emails` 加 `template TEXT` + `analyzed_at TIMESTAMPTZ`，加 2 个索引（`idx_te_outbound_template`、`idx_te_pending_analyze`）。`talents.pending_rejection_id` 列 4 月 22 日加入又在 4 月 23 日删除（`20260423_drop_pending_rejection_id.sql`，配合 auto_reject 简化）。所有迁移用新 `recruit_migrations` 表跟踪。

### 自验证（D5）

每个 v3.3 写脚本在做完核心动作后**立刻**调用对应断言：

| 脚本 | 自验证 |
|---|---|
| `outbound/cmd_send.py` | `assert_email_sent(talent_id, message_id)` — 确认 `talent_emails` 里有这行 outbound 记录 |
| `inbox/cmd_scan.py` | `assert_emails_inserted(talent_id, [message_ids])` — 确认新增的入站全部入表 |
| `inbox/cmd_analyze.py` | `assert_email_analyzed(email_id)` — 确认 `analyzed_at` 已设、AI 字段已写 |
| `talent/cmd_add.py` | `assert_talent_state(talent_id, expected_stage)` — 确认行存在且 stage 对 |
| `talent/cmd_update.py` | `assert_talent_state(talent_id, expected_stage, expected_fields)` — 确认 stage / 字段都已落 |
| `talent/cmd_delete.py` | `assert_talent_deleted(talent_id)` — 确认行已不在 |

任何一个失败 → 抛 `SelfVerifyError` → `cli_wrapper` 推飞书告警（含脚本名 / argv / talent_id / 错误详情），主流程也以非零状态退出。这取代了之前的「每日 consistency_check 漂移扫描」（cron 删除后由「每次写完即查」覆盖等价但更及时）。

### `UserInputError` 与 alert 分流

历史上「漏 `--force`」「talent_id 不存在」「模板少变量」会触发 Python 默认堆栈 → `cli_wrapper` 误判为 crash → 推一条无意义的飞书。v3.3 引入 `UserInputError`：

- 写脚本主动 raise → `cli_wrapper` 捕获 → 仅 stderr 打 `[INPUT ERROR]` + 非零退出 → **不**推飞书
- 仅在「真异常 / SelfVerify 失败」时推飞书
- `ops/cmd_push_alert.py` 故意**不**走 `cli_wrapper`（递归告警死循环）

### 决策点回顾（D1–D6）

| 决策 | 取值 | 落地点 |
|---|---|---|
| D1 老脚本去向 | 直接删（Phase 9） | 待办 |
| D2 跨阶段流转 | 自然放行 + `--force` 兜底 | `talent/cmd_update.py::_NATURAL_TRANSITIONS` |
| D3 talent_emails schema | Phase 0 一次性 | `lib/migrations/20260417_v33_talent_emails_extend.sql` |
| D4 自由文本临时文件清理 | 默认开 | `outbound/cmd_send.py --cleanup-body-file` 默认 True |
| D5 自验证失败告警策略 | 每次都推（最保守） | `lib/cli_wrapper.run_with_self_verify` |
| D6 `followup/cmd_draft_reply` | 砍掉，agent 自己起草 | 已删 |

### Phase 1 生产烟测（已完成）

在推 Phase 2-9 前，先在生产环境跑了 Phase 1 三件套（`cmd_send` / `cmd_update` / `cmd_delete`）的真实场景：发邮件、改 stage、删人、`--dry-run`、`--force`、缺变量、缺 talent_id……期间发现 4 个 bug 并就地修复（dry-run 仍写表 / `cli_wrapper` 误告警 / `assert_emails_inserted` 签名 / `cmd_db_migrate` 与既存 `schema_migrations` 列名冲突），再继续推 Phase 2-7。

详细 CLI 见 [skills/recruit-ops/docs/CLI_REFERENCE.md#v33-解耦命令体系推荐路径](../skills/recruit-ops/docs/CLI_REFERENCE.md#v33-解耦命令体系推荐路径)。

---

## 十一、已知限制与 Roadmap

### 11.1 已知工程债

| 项 | 严重度 | 计划 |
|---|---|---|
| `daily_exam_review.py` 单文件 1783 行 | 中 | 拆为 `email_scanner` + `llm_analyzer` + `pipeline`（待补集成测试再动） |
| 通知非原子（DB 写完 Feishu 失败 → 状态推进但通知漏） | 中 | 加 `notification_pending` 字段 + 重发任务 |
| 无 `logging` 体系 | 中 | 统一 `lib/logger.py` + 文件日志 + correlation ID |
| 无 PG 连接池 | 低 | 流量小时不痛，等并发上来再做 |
| 无 GitHub Actions CI | 低 | 待选型 |
| 无 dev / prod 配置分离 | 低 | 等需要 |

### 11.2 已规划新功能

- v3.3 Phase 9：跑全套测试 → 确认无回归 → 删除 v3.3 已替代的老脚本（`common/cmd_email_thread.py`、`common/cmd_email_preview.py`、`exam/cmd_exam_timeout_scan.py`、`followup/smtp_sender.py` forwarder、老 `cron_runner.py` 等）。

### 11.3 不在 Roadmap 内

- 多租户 / RBAC / 审批流后台
- Web UI（Feishu 就是 UI）
- 移动端（同上）

---

## 十二、汇报要点

如果你只能讲 5 分钟：

1. **是什么**：用 Feishu + 邮件 + LLM 的轻量招聘运营系统，PG 是真源，CLI 是肌肉
2. **解决什么**：状态散落 / 邮件协商人工 / Offer 后跟进无人接 / 死任务无人结 → 全自动 + 可审计
3. **架构亮点**：状态机双层约束 / Hermes 智能路由 + propose 安全规则 / cron 七任务 + 心跳告警 / v3.3 解耦命令体系 + self-verify / 笔试超时即触即拒删（无缓冲，事后告知）
4. **数据**：~20.6k 行 Python / 39 个 CLI / 14 个 stage / 174 个测试 / 3 张 PG 表（`talents` / `talent_events` / `talent_emails`）
5. **最近改进**：cron 失败告警 / followup 文件锁 / LLM 重试 / dry-run 全闸 / `talent_emails` 表统一邮件 source-of-truth / 邮件模板系统（6 模板 + fragment include） / **v3.3 解耦命令体系（11 个原子写脚本 + self-verify + UserInputError 告警分流，agent 路由优先 v3.3）** / auto_reject 模块大幅简化（4 月 23 日：删除 6 个脚本 + pending_store + llm_classify + `pending_rejection_id` 列，仅保留笔试 3 天未交即触即拒删；改期一律老板手动决定）
6. **下一步**：Phase 9 删除被 v3.3 替代的老脚本 / 删除已 deprecate 的 `*_last_email_id` 列 / 拆 1783 行单文件 / 通知去原子化 / 引入结构化日志

如果你能讲 15 分钟，加上：
- 状态流转图走一遍
- 选一个完整流程演示（CV 录入 → 一面 → 笔试 → 二面 → Offer → followup）
- Hermes propose 机制与 mutating 命令安全规则
- 最近一次故障（傅雨涵 HR 漏看 Feishu）的处置流程，演示「从故障 → 加 WARN → 加 cron 告警」的闭环

---

*文档由 cursor-agent 基于代码库实地扫描生成，所有数字均来自 `wc -l` / `grep -c` / `git log` 等可复现命令。*
