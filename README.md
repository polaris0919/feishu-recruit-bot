# recruit-workspace

`recruit-workspace` 是一个面向 **Feishu + 邮件协商场景** 的招聘运营工作区，当前核心产品为 `skills/recruit-ops`。

它把 HR 录入、候选人状态机、邮件往返、面试确认、笔试流转、Feishu 通知和日历创建收敛到一个可脚本化、可审计、可自动扫描的运行时里，适合想用 **自有工作流** 替代重型 ATS 的团队。

---

## 这是什么

这个仓库目前主要包含一个产品：

- `skills/recruit-ops/`：招聘运营引擎

`recruit-ops` 的设计目标是：

- 用 **PostgreSQL** 保存候选人状态，避免状态散落在聊天记录和表格里
- 用 **CLI** 承载所有关键流程，便于自动化、复盘和回归测试
- 用 **agent gateway + skill runtime** 接入 Feishu，让 HR 和老板直接在消息里驱动流程
- 用 **IMAP 扫描 + LLM 判意图** 处理候选人的确认、改期、笔试回复

如果你想要的是：

- 在 Feishu 里完成招聘闭环
- 自动处理“候选人回邮件确认时间 / 申请改期 / 提交笔试”
- 让 HR 和面试官不再手工维护多份状态

那这个项目就是为这类场景设计的。

---

## 🎯 适用场景

推荐场景：

- 小团队 / 创业团队 / 研究型团队的轻量 ATS 替代
- Founder-led / Hiring-manager-led 招聘，老板直接在 Feishu 里推进候选人
- 以邮件为主的面试时间协商流程
- 实习生 / 校招 /研究员等需要统一笔试与多轮面试的流程
- 需要自定义状态机、脚本化调度、可审计变更记录的内部系统

不太适合的场景：

- 已经深度依赖 Greenhouse / Lever / Workday 等现成 ATS
- 需要多租户隔离、复杂 RBAC、审批流后台 UI 的 SaaS 产品形态
- 不使用 Feishu，也不希望接入邮件扫描/自动化能力的团队

---

## ✨ 核心能力

- **候选人录入**
  - HR 模板录入
  - 历史候选人导入
  - PDF / DOCX 简历自动解析与去重
- **招聘状态机**
  - `NEW` → 一面 → 笔试 → 二面 → `POST_OFFER_FOLLOWUP` → `ONBOARDED`
  - 支持保留人才池（区分"二面失败留池"与"已拒 Offer 留池"，v3.8.2）、待回国恢复、删除等终态/分支
- **面试协商**
  - 发邀请邮件
  - 扫描候选人回信
  - LLM 识别确认 / 改期 / 不明确意图
  - 最终由老板确认后再创建 Feishu 日历
  - 二面确认有硬护栏：任何路径都必须先进入 `ROUND2_SCHEDULING`，候选人确认后再由老板明确授权建日历，不能从笔试 / 一面 / WAIT_RETURN 等阶段直达 `ROUND2_SCHEDULED`
- **笔试流转**
  - 自动识别候选人答题回复
  - 预审与结果流转
- **通知与提醒**
  - 飞书消息
  - Feishu 日历创建
  - 面试结果催问
- **可自动化、可测试**
  - 所有核心动作均有 CLI 入口
  - 测试覆盖关键扫描、确认、改期、状态变更逻辑

---

## 🧱 高层架构

```text
Feishu (HR / Boss)
        |
        v
Agent gateway + recruit-ops skill runtime
        |
        +--> recruit-ops CLI (Python)
        |        |
        |        +--> PostgreSQL (source of truth)
        |        +--> IMAP mailbox scan
        |        +--> SMTP / email-send skill
        |        +--> Feishu IM + Calendar API
        |        +--> DashScope / LLM reply analysis
        |
        +--> cron / systemd timers
```

原则上：

- **数据库** 是唯一状态真源
- **Skill** 决定消息如何路由到 CLI
- **CLI** 决定状态如何变更
- **自动扫描器** 只负责发现候选人回信并生成下一步动作/提醒

---

## 🗂️ 仓库结构

```text
recruit-workspace/
├── README.md                         # 你正在看的项目首页
├── config/                           # 本地配置（不应提交真实凭据）
└── skills/
    └── recruit-ops/                  # 自包含 skill 目录，可整体打包分发
        ├── SKILL.md                  # ⭐ Hermes Gateway 入口（agent 路由 + 安全协议 + 决策主循环）
        ├── README.md                 # 产品级详细文档
        ├── pyproject.toml + uv.lock
        ├── docs/
        │   ├── INDEX.md              # 一页地图（"我想知道 X 去哪查"）
        │   ├── AGENT_RULES.md        # 业务决策手册（stage × intent → chain）
        │   ├── CLI_REFERENCE.md      # 全部 CLI 详细参考
        │   ├── INCIDENT_RULES.md     # 事故型规则录
        │   ├── OPERATIONS.md         # 部署 / cron / symlink / 故障排查
        │   ├── PROJECT_OVERVIEW.md   # 设计动因 / 架构演进
        │   └── archive/              # 历史归档
        └── scripts/                  # 所有 CLI / runtime 入口
```

如果你只看一个子目录，请看：

- `skills/recruit-ops/`

---

## 🔧 依赖

### 系统依赖

- Python `>= 3.10`
- `uv`（推荐的 Python 依赖管理与运行方式）
- PostgreSQL `>= 10`
- 可访问 IMAP 邮箱（候选人回复扫描）
- Feishu 自建应用（消息 + 日历）

### Python 依赖

当前 `skills/recruit-ops/pyproject.toml` 中声明的核心依赖包括：

- `psycopg2-binary`
- `python-dateutil`
- `lark-oapi`
- `pdfminer.six>=20231228`

### 外部服务依赖

- **Agent gateway / skill host**（可选但推荐）
  - 作为 Feishu 消息入口与 skill runtime
  - 当前仓库默认对接 Hermes Gateway，但不强制绑定某一个特定宿主
- **DashScope / LLM**
  - 用于候选人回信意图分析、简历字段解析
- **SMTP / email-send**
  - 用于外发邮件

---

## 🚀 安装

### 1. 克隆仓库

```bash
git clone <your-fork-or-repo-url> recruit-workspace
cd recruit-workspace
```

### 2. 安装 Python 依赖

```bash
cd skills/recruit-ops
uv sync
```

如果你不用 `uv`，也可以手动创建虚拟环境后安装：

```bash
cd skills/recruit-ops
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 3. 初始化数据库

```bash
# 1) Fresh install：跑终态 DDL（含 recruit_migrations 记账表）
psql "$DATABASE_URL" -f skills/recruit-ops/scripts/lib/migrations/schema.sql

# 2) 把所有「已被 schema.sql 内联兼容」的历史迁移标记为 applied，
#    避免 cmd_db_migrate 重跑历史增量。schema.sql 末尾有 INSERT 模板，
#    打开注释执行即可；或直接：
psql "$DATABASE_URL" -c "INSERT INTO recruit_migrations (filename, notes) \
  SELECT regexp_replace(name,'^.*/',''), 'pre-seeded by schema.sql' \
  FROM (VALUES \
    ('20260417_v33_talent_emails_extend.sql'), \
    ('20260421_v35_drop_dead_columns.sql'), \
    ('20260422_v3511_talent_emails_context_rejection.sql'), \
    ('20260423_drop_pending_rejection_id.sql'), \
    ('20260424_v356_talent_emails_attachments.sql'), \
    ('20260425_v357_talents_has_cpp.sql'), \
    ('20260427_v36_drop_offer_handoff.sql'), \
    ('20260428_v36_drop_done_reject_delete.sql'), \
    ('20260510_v38_add_onboarded_stage.sql'), \
    ('20260511_v382_offer_declined_keep.sql') \
  ) AS t(name) ON CONFLICT (filename) DO NOTHING;"

# 3) 后续每次新增迁移：开发者放一个 YYYYMMDD_*.sql 到 lib/migrations/，
#    运维侧只需：
cd skills/recruit-ops
PYTHONPATH=scripts uv run python3 -m ops.cmd_db_migrate --status   # 查 pending
PYTHONPATH=scripts uv run python3 -m ops.cmd_db_migrate --apply    # 跑 pending
```

---

## ⚙️ 配置

所有本地配置统一放在仓库根目录的 `config/` 下。真实配置文件 **不要提交到 Git**。

最常见的配置文件如下：

- `config/openclaw.json`
  - 当前默认的 gateway / channel 配置文件名
  - 包含 Feishu 应用信息、Boss / HR open_id、Feishu calendar_id
  - 如果你接入的不是 OpenClaw / Hermes 生态，可以把同等配置接到你自己的配置源
- `config/talent-db-config.json`
  - PostgreSQL 连接信息
- `config/recruit-email-config.json`
  - IMAP 扫描配置
- `config/email-send-config.json`
  - SMTP 发信配置
- `config/dashscope-config.json`
  - LLM API key

### 最小可运行配置

#### `config/talent-db-config.json`

```json
{
  "TALENT_DB_HOST": "127.0.0.1",
  "TALENT_DB_PORT": "5432",
  "TALENT_DB_NAME": "recruit",
  "TALENT_DB_USER": "recruit_app",
  "TALENT_DB_PASSWORD": "your_db_password"
}
```

#### `config/recruit-email-config.json`

```json
{
  "RECRUIT_EXAM_IMAP_HOST": "imap.example.com",
  "RECRUIT_EXAM_IMAP_USER": "recruit@example.com",
  "RECRUIT_EXAM_IMAP_PASS": "your_password"
}
```

#### `config/dashscope-config.json`

```json
{
  "DASHSCOPE_API_KEY": "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

### 常用环境变量

```bash
export FEISHU_BOSS_OPEN_ID="ou_xxx"
export FEISHU_HR_OPEN_ID="ou_xxx"
export FEISHU_CALENDAR_ID="feishu.cn_xxx@group.calendar.feishu.cn"
export INTERVIEW_CONFIRM_TIMEOUT_MINUTES=2880
```

### 多环境配置（v3.8.5）

通过 `RECRUIT_ENV` 切换 dev / staging / prod 的 config 文件，避免在同一台机器上手动改 `config/openclaw.json` 来回切：

```bash
# 默认 = prod，读 config/<name>.json
unset RECRUIT_ENV

# dev：优先读 config/<name>.dev.json，找不到时 fallback 到 config/<name>.json
export RECRUIT_ENV=dev

# staging：同理读 config/<name>.staging.json
export RECRUIT_ENV=staging
```

查找顺序见 `skills/recruit-ops/scripts/lib/recruit_paths.py::config_candidates`：env 后缀文件优先，找不到再退回通用版本，因此只需为不同环境覆盖你真正想换的那几项（比如只换 `openclaw.dev.json` 指向测试群，DB 和 SMTP 沿用 prod）。

测试 / CI 不依赖任何真实 config，靠以下"全无副作用" env vars：

```bash
export RECRUIT_DISABLE_DB=1
export RECRUIT_DISABLE_SIDE_EFFECTS=1
export RECRUIT_DISABLE_DB_WRITES=1
export RECRUIT_SUPPRESS_SELF_VERIFY_ALERT=1
```

---

## Quick Start

### 1. 验证环境

```bash
cd skills/recruit-ops
PYTHONPATH=scripts uv run python3 -m common.cmd_status --all
```

如果数据库里还没有数据，至少应能看到脚本正常启动，而不是 import 错误。

### 2. 录入一个候选人

```bash
cd skills/recruit-ops
PYTHONPATH=scripts uv run python3 -m intake.cmd_new_candidate --template "$(cat <<'EOF'
【新候选人】
姓名：张三
邮箱：zhangsan@example.com
EOF
)"
```

### 3. 安排一面

```bash
PYTHONPATH=scripts uv run python3 -m outbound.cmd_send \
  --talent-id t_xxxxx \
  --template round1_invite \
  --vars round1_time="2026-04-20 09:30" interviewer="老板"

PYTHONPATH=scripts uv run python3 -m talent.cmd_update \
  --talent-id t_xxxxx \
  --stage ROUND1_SCHEDULING \
  --set round1_time="2026-04-20 09:30" \
  --set round1_invite_sent_at=__NOW__
```

### 4. 查看指定日期面试安排

```bash
PYTHONPATH=scripts uv run python3 -m common.cmd_today_interviews --date 2026-04-20
```

### 5. 扫描候选人回复

```bash
PYTHONPATH=scripts uv run python3 -m inbox.cmd_scan
PYTHONPATH=scripts uv run python3 -m inbox.cmd_analyze
```

---

## 🧪 常用命令

更多命令见 `skills/recruit-ops/docs/CLI_REFERENCE.md`。这里给出最常用的一组：

```bash
cd skills/recruit-ops

# 查看全部候选人
PYTHONPATH=scripts uv run python3 -m common.cmd_status --all

# 查单个候选人
PYTHONPATH=scripts uv run python3 -m common.cmd_status --talent-id t_xxxxx

# 搜索候选人
PYTHONPATH=scripts uv run python3 -m common.cmd_search --query 张三

# 查看今天/某天面试
PYTHONPATH=scripts uv run python3 -m common.cmd_today_interviews
PYTHONPATH=scripts uv run python3 -m common.cmd_today_interviews --date 2026-04-20

# 记录一面结果
PYTHONPATH=scripts uv run python3 -m interview.cmd_result \
  --talent-id t_xxxxx --round 1 --result pass --email zhangsan@example.com

# 记录笔试结果
PYTHONPATH=scripts uv run python3 -m exam.cmd_exam_result \
  --talent-id t_xxxxx --result pass --round2-time "2026-04-22 14:00"

# 记录二面结果
PYTHONPATH=scripts uv run python3 -m interview.cmd_result \
  --talent-id t_xxxxx --round 2 --result pass
```

---

## 🤖 推荐运行方式

### 推荐：Hermes 自动化流程

对大多数团队，**推荐的生产运行方式**是：

1. **Hermes / agent gateway** 负责接收 Feishu 消息
2. `skills/recruit-ops/SKILL.md` 作为主 routing contract
3. `recruit-ops` CLI 负责执行真实状态变更
4. `cron / systemd` 负责自动扫描候选人邮件回复、改期请求与笔试结果

也就是说：

- HR / Boss 日常主要在 **Feishu** 里工作
- agent 负责路由到正确 CLI
- CLI 负责落状态、发邮件、触发日历
- 定时任务负责把“候选人回信”重新带回系统闭环

如果你是第一次落地这个项目，建议按下面顺序推进：

1. 先跑通 `skills/recruit-ops` CLI
2. 接上 PostgreSQL
3. 接上 Feishu 通知
4. 接上 IMAP 扫描
5. 最后接入 **Hermes 自动化流程**

这样你最终得到的是一个“**Feishu 驱动 + Hermes 编排 + CLI 落状态**”的完整自动化工作流，而不是一堆只能手工调用的脚本。

### 方式一：手工 CLI

适合开发、排错、手工运营。

```bash
cd skills/recruit-ops
PYTHONPATH=scripts uv run python3 -m common.cmd_status --all
```

### 方式二：Hermes / Agent gateway + Skill

适合正式运行，也是**推荐的产品化入口**：

- gateway 负责接 Feishu 消息
- `skills/recruit-ops/SKILL.md` 定义 agent 路由规则；sibling docs（`AGENT_RULES.md` / `CLI_REFERENCE.md` / `INCIDENT_RULES.md` / `OPERATIONS.md` / `INDEX.md`）按需 fetch
- 运行时 Hermes 用**目录级软链**直接挂载整个 `skills/recruit-ops/` 目录；详见 `skills/recruit-ops/docs/OPERATIONS.md §3`

### 方式三：cron / systemd 扫描器

适合自动跑：

```bash
cd skills/recruit-ops
PYTHONPATH=scripts ./.venv/bin/python -m cron.cron_runner
```

常见子任务：

- `inbox.cmd_scan`
- `inbox.cmd_analyze`
- `auto_reject.cmd_scan_exam_timeout --auto`
- `common.cmd_interview_reminder`

---

## 🧭 适合作为开源项目如何使用

如果你想把它作为一个内部产品 fork 下来跑，推荐顺序是：

1. 先只跑 `skills/recruit-ops` CLI
2. 接上 PostgreSQL
3. 再接 Feishu 通知
4. 再接 IMAP 扫描
5. 最后接入 Hermes / agent gateway 和 skill runtime

这样你可以逐层验证：

- 状态机对不对
- 数据库 schema 对不对
- 消息/日历配置对不对
- 自动扫描是否可靠

---

## 📚 文档入口

| 文档 | 受众 | 用途 |
|---|---|---|
| `skills/recruit-ops/SKILL.md` | agent | ⭐ 路由契约 + 决策主循环 + 安全协议（每次对话首读） |
| `skills/recruit-ops/docs/INDEX.md` | agent / 维护者 | 一页地图（"我想知道 X 去哪查"） |
| `skills/recruit-ops/docs/AGENT_RULES.md` | agent | 业务决策手册（stage × intent → chain） |
| `skills/recruit-ops/docs/CLI_REFERENCE.md` | agent / 开发者 | 全部 CLI 参数 / 输出 schema / exit codes |
| `skills/recruit-ops/docs/INCIDENT_RULES.md` | agent / 维护者 | 事故型规则录（带版本 / 日期） |
| `skills/recruit-ops/docs/OPERATIONS.md` | 运维 | 部署 / cron / symlink / 故障排查 |
| `skills/recruit-ops/docs/PROJECT_OVERVIEW.md` | 新人 onboarding | 设计动因 / 架构演进 |
| `skills/recruit-ops/README.md` | 开发者 | 产品级详细说明 |

---

## 🛠️ 开发 / 贡献

### 本地开发环境

```bash
cd skills/recruit-ops
uv sync --group dev       # 装运行时依赖 + pytest

# 跑全量测试（必须绿才能 commit）
PYTHONPATH=scripts \
  RECRUIT_DISABLE_DB=1 RECRUIT_DISABLE_SIDE_EFFECTS=1 \
  RECRUIT_DISABLE_DB_WRITES=1 RECRUIT_SUPPRESS_SELF_VERIFY_ALERT=1 \
  uv run python3 -m pytest scripts/tests/ -q
```

### pre-commit hook（强烈建议安装）

仓库根有 `.pre-commit-config.yaml`，会在 `git commit` 前自动跑 pytest、阻止真实 `config/*.json` 入库、检查大文件 / 私钥。

```bash
pip install pre-commit       # 或 brew install pre-commit
pre-commit install           # 在仓库根目录跑
pre-commit run --all-files   # 主动跑一遍（首次接入推荐）
```

### CI

`.github/workflows/test.yml` 在每个 PR + main push 上跑 Python 3.10 / 3.11 / 3.12 三套测试，并校验 `pyproject.toml` 的 `packages` 列表与真实目录一致（防止又出现 followup / round1 / round2 那种"列表项找不到目录"导致 fresh install 报错）。

---

## 📌 当前状态

这个仓库当前更接近：

- **可运行的内部产品**
- **可开源整理的工作区**

而不是一个已经抽象成通用 SaaS 的最终形态。

如果你要在自己的团队里落地，建议把它看成：

- 一套招聘运营运行时
- 一套 skill + CLI + 数据库的集成模板
- 一个可以按你自己的流程继续裁剪和二次开发的基础仓库
