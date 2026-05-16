<!--
audience: agent, maintainer
read_when: 不确定该读哪份 doc 时的兜底地图；新人 / 新 agent 第一次进入这个 skill 时
do_not_put_here: 任何业务规则；它只是路标
sibling_docs: SKILL.md, AGENT_RULES.md, CLI_REFERENCE.md, INCIDENT_RULES.md, OPERATIONS.md, PROJECT_OVERVIEW.md
last_updated: 2026-05-11
-->

# Recruit-Ops Docs Index

> 一页地图。**找不到答案先回这里**，然后按表格里的指针跳到对应 doc。

## 我想知道…

| 问题 | 去哪查 |
|---|---|
| **agent 入口契约** — 这条消息我该不该处理 / 是不是要 confirm / 怎么 propose / 怎么报失败 | [SKILL.md](../SKILL.md) |
| **决策主循环** — 5 步分诊（A. CV / B. 只读 / C. 写 / D. 破坏性 / E. 模糊） | [SKILL.md §0](../SKILL.md#0-agent-决策主循环) |
| **安全模型** — 只读 / 预览 / 写 / 破坏性 四档 | [SKILL.md §2](../SKILL.md#2-安全模型) |
| **confirm 三档** — Atomic / Declared chain / Ad-hoc 禁止 | [SKILL.md §2.3.1](../SKILL.md#231-执行前-confirm-协议强制) |
| **stop and ask** — 找不到 chain / 信息不全 / 命令失败 | [SKILL.md §10](../SKILL.md#10-升级--停止规则stop-and-ask) |
| **业务规则** — 给定 stage + intent → 走哪条 chain | [AGENT_RULES.md §4](AGENT_RULES.md#4-scenarios) + [§5 速查表](AGENT_RULES.md#5-表外的常见-intent) |
| **stage 状态机** — 13 个 stage 的语义、出入边界（v3.8.2 拆出 `OFFER_DECLINED_KEEP`） | [AGENT_RULES.md §2](AGENT_RULES.md#2-stages) + 代码权威源 `scripts/lib/core_state.py::STAGE_LABELS` |
| **atomic CLI 速查** — 哪些命令属于哪一类 | [AGENT_RULES.md §3](AGENT_RULES.md#3-commandsatomic-cli) |
| **CLI 参数语法** — `cmd_xxx.py` 的 args / flags / 输出 schema / exit codes | [CLI_REFERENCE.md](CLI_REFERENCE.md) |
| **改期 / 暂缓 / 笔试结果 / Offer / WAIT_RETURN / force-jump** 怎么走 | [AGENT_RULES.md §4.3 / §4.4 / §4.6 / §4.10 / §4.7 / §4.9](AGENT_RULES.md#4-scenarios) |
| **CV 入库流程** — HR 发简历 → 自动预览 → confirm 录入 | [SKILL.md §4](../SKILL.md#4-cv-入库路由skill-独占) + [§7.4 预览两种分支](../SKILL.md#74-cv-录入预览两种分支) |
| **反模式 / 不该做什么** | [SKILL.md §9](../SKILL.md#9-反模式通用-12-条)（高危 12 条）→ [INCIDENT_RULES.md](INCIDENT_RULES.md)（事故型） |
| **某条规则的来源** — 为什么有这条 / 是哪次事故触发的 | [INCIDENT_RULES.md](INCIDENT_RULES.md) |
| **部署 / cron / symlink / 环境变量** | [OPERATIONS.md](OPERATIONS.md) |
| **故障排查速查表** — Hermes 不重读 / DB 连不上 / 拒信发不出 等 | [OPERATIONS.md §6](OPERATIONS.md#6-故障排查速查) |
| **架构演进 / 设计动因** — 为什么是 atomic CLI、为什么 PostgreSQL 真源 | [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) |

## Sibling docs 一览

| 文件 | 主要受众 | 行数级别 |
|---|---|---|
| [`../SKILL.md`](../SKILL.md) | agent（每次对话首读；只放对话契约 / 安全模型 / stop-and-ask） | ~620 |
| [`AGENT_RULES.md`](AGENT_RULES.md) | agent（chain dispatch 时读；只放 stage × intent → chain） | ~420 |
| [`CLI_REFERENCE.md`](CLI_REFERENCE.md) | agent（写 propose 查参数时读；CLI 参数 / 输出 / 副作用唯一参考） | ~810 |
| [`INCIDENT_RULES.md`](INCIDENT_RULES.md) | agent + maintainer（命中事故标签 / 审规则来源） | ~180 |
| [`OPERATIONS.md`](OPERATIONS.md) | ops, maintainer（agent 不常读） | ~150 |
| [`PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md) | 新人 onboarding（架构动因和高层数据流，不作为 CLI 操作手册） | ~1100 |
| `archive/` | 仅历史参考；不读 | — |

## 维护

新增 doc 时**必须**回头更新本文件的两张表格 + 各 sibling doc 顶部 frontmatter 的 `sibling_docs` 字段。
