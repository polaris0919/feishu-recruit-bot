<!--
audience: ops, maintainer
read_when: 部署 / 调试 / 重启 / 同步路径出问题；新机器装新 skill；cron 排障
do_not_put_here: 业务规则（→ AGENT_RULES.md）/ confirm 协议（→ SKILL.md §2）/ CLI 参数（→ CLI_REFERENCE.md）/ 历史事故故事（→ INCIDENT_RULES.md）
sibling_docs: SKILL.md, AGENT_RULES.md, CLI_REFERENCE.md, INCIDENT_RULES.md, PROJECT_OVERVIEW.md, INDEX.md
last_updated: 2026-05-09
-->

# Recruit-Ops 部署运维

> 这是给**运维 / 维护者**读的部署文档。agent 在线上对话中**不需要**读这份；只在用户明确问"怎么部署 / 重启 / cron 怎么调 / 软链断了怎么办"时引用。

---

## 1. 工作区路径（`<workspace_root>` 实际值）

> 开源分发时按部署重写本节；正文其它文件用 `<workspace_root>` 占位符。

| 名字 | 实际路径 |
|---|---|
| `<workspace_root>` | `/home/admin/recruit-workspace` |
| 招聘资料根目录 | `/home/admin/recruit-files` |
| 脚本目录 | `<workspace_root>/skills/recruit-ops/scripts/` |
| 运行时解释器 | `<workspace_root>/skills/recruit-ops/.venv/bin/python3`（与 `uv run python3` 等价） |
| Hermes Gateway 加载根 | `~/.hermes/skills/openclaw-imports/recruit-ops/` |
| 飞书附件落盘 | `<workspace_root>/data/media/inbound/`（消息里给的是已解出的绝对路径） |
| 候选人普通邮件附件落盘 | `/home/admin/recruit-files/candidates/<tid>/email/` |
| 候选人 CV 原件目录 | `/home/admin/recruit-files/candidate_cv/<候选人名>__<talent_id>/` |
| 候选人笔试答案目录 | `/home/admin/recruit-files/exam_submissions/<候选人名>__<talent_id>/` |
| 笔试题包目录 | `/home/admin/recruit-files/exam_package/` |
| 候选人删除归档目录 | `/home/admin/recruit-files/deleted_archive/<YYYY-MM>/` |

**重要**：Hermes Gateway 给消息里的文件路径**已经是绝对路径**，CLI `--file-path` 直接原样接受；agent 侧**不要**改写成 `<workspace_root>`-相对形式。

`talent.cmd_delete` 删除候选人前会把当前正式资料目录（CV、笔试提交、普通邮件附件）搬进删除归档目录；归档失败会中止 DB 删除，避免人才库已删但文件仍散落在正式资料区。

---

## 2. 硬性前置条件

| 条目 | 要求 | 缺失时表现 |
|---|---|---|
| **Python** | 3.10+ | `argparse` 报错或 import 失败 |
| **依赖** | `<workspace_root>/skills/recruit-ops/` 已跑过 `uv sync`，或 `.venv/` 在位 | `ModuleNotFoundError` |
| **数据库** | `talent-db` 可达；连接信息在 `<workspace_root>/config/talent-db-config.json` 或同名 env var | DB 连接错（按 SKILL.md §6 Infra 类故障上报） |
| **飞书身份** | env var `FEISHU_BOSS_OPEN_ID` / `FEISHU_POLARIS_OPEN_ID` / `FEISHU_HR_OPEN_ID`（或 `config/openclaw.json` 的 `bossOpenId` / `polarisOpenId` / `hrOpenId`；`ownerOpenId` 仅作为 Polaris 旧字段兼容） | 老板 / Polaris / HR 路由错对象 |
| **面试官身份** | `config/openclaw.json` 的 `interviewerMasterOpenId` / `interviewerBachelorOpenId` / 其它派单 ID | `intake.cmd_route_interviewer` 拿不到候选 |
| **PYTHONPATH** | cron / systemd 调用 import 时显式设 `PYTHONPATH=scripts` | `ModuleNotFoundError: No module named 'lib'` |

依赖详情见 `<workspace_root>/skills/recruit-ops/pyproject.toml`。

部署脚本 `deploy/install.sh` 默认读取 `<workspace_root>/config/talent-db-config.json` 来执行 `schema.sql`；可通过 `RECRUIT_WORKSPACE_ROOT` / `RECRUIT_CONFIG_DIR` 覆盖路径。字段与运行时代码一致：`TALENT_DB_HOST`、`TALENT_DB_PORT`、`TALENT_DB_NAME`、`TALENT_DB_USER`、`TALENT_DB_PASSWORD`。

---

## 3. Hermes 加载与 SKILL.md 同步（**目录级软链**）

当前部署把整个 `recruit-ops` skill 目录软链到 Hermes 的导入路径。**改完任何 doc 都不需要额外 `cp`**——Hermes 重启后直接读到新内容。

### 3.1 当前状态

```bash
ls -la ~/.hermes/skills/openclaw-imports/recruit-ops
# lrwxrwxrwx ... -> /home/admin/recruit-workspace/skills/recruit-ops
```

`SKILL.md` 在该 skill 目录的根（`<workspace_root>/skills/recruit-ops/SKILL.md`），sibling docs 在 `docs/`。Hermes 按约定读 `SKILL.md`；agent 在对话中按 SKILL.md 里的指针主动 fetch 任何 sibling doc。

### 3.2 重建（万一被运维误改成普通文件副本）

```bash
rm -rf ~/.hermes/skills/openclaw-imports/recruit-ops
ln -sfn /home/admin/recruit-workspace/skills/recruit-ops \
        ~/.hermes/skills/openclaw-imports/recruit-ops
```

`-n` 防止 `ln` 把目标当成已存在目录而创建嵌套链。重建完后**必须**重启 Hermes Gateway 才能让它重读。

### 3.3 改完一份 doc 后

- 日常只编辑 `<workspace_root>/skills/recruit-ops/{SKILL.md, docs/*.md}`；
- **不要**直接覆盖 Hermes 那端任一文件（会把软链变成普通文件副本，后续更新就分叉了）；
- 改完直接重启 Hermes 让它重读即可，无需 `cp`。

---

## 4. 定时任务（systemd user timer）

官方部署 = `systemd --user` 单元，每 10 分钟跑一轮 `cron/cron_runner.py`。

> **C1 (v3.8.7) 起，所有 systemd 单元 + 部署脚本入仓**：
> ```
> skills/recruit-ops/deploy/install.sh                          # 一键部署 / 升级
> skills/recruit-ops/deploy/uninstall.sh                        # 卸载
> skills/recruit-ops/deploy/systemd/recruit-cron-runner.service
> skills/recruit-ops/deploy/systemd/recruit-cron-runner.timer
> ```
> 部署后 `~/.config/systemd/user/recruit-cron-runner.{service,timer}` 是软链 → 仓库内文件，git pull 改了模板自动生效，**不需要**再 `cp` 一遍。

### 4.1 cron_runner 串了什么

1. `inbox.cmd_scan` —— 拉 IMAP 入站邮件
2. `inbox.cmd_analyze` —— LLM 分析意图，写 `talent_emails.ai_payload`；若 `EXAM_SENT + exam_submitted`，自动触发 `exam.cmd_exam_ai_review --feishu --save-event`
3. `common.cmd_interview_reminder` —— 面试结束后 15 分钟仍未出结果则催老板；之后每 30 分钟重复催
4. `auto_reject.cmd_scan_exam_timeout` —— 笔试超时自动拒删（见 SKILL.md §2.5）
5. `cron.cmd_review_reminder` —— EXAM_REVIEWED ≥3h 未拍板催老板（v3.8）
6. `ops.cmd_health_check` —— 每天 09:xx 飞书心跳（只这一拍跑）
7. `ops.cmd_metrics_dump` —— 每天 09:xx 业务计数器快照（**C2, v3.8.7 新增**: stage 分布 / 24h 邮件量 / 笔试超时 / cron heartbeat / DB 延迟，journal 留档不轰炸老板）

外部调度**必须**显式 `PYTHONPATH=scripts`，否则 `import lib...` 会失败。完整命令清单见 [CLI_REFERENCE.md `cron_runner.py`](CLI_REFERENCE.md#cron_runnerpy)。

### 4.2 常用命令

```bash
systemctl --user status  recruit-cron-runner.timer
systemctl --user restart recruit-cron-runner.service
journalctl --user -u recruit-cron-runner.service -n 200 --no-pager
```

### 4.3 改调度间隔

编辑 `~/.config/systemd/user/recruit-cron-runner.timer`，改 `OnUnitActiveSec=`。改完 `systemctl --user daemon-reload && systemctl --user restart recruit-cron-runner.timer`。

---

## 5. 本地部署映射 vs 开源分发

为将来开源分发，**本 skill 正文保持可移植**：

- `SKILL.md` / `docs/*.md` 正文里**只**用 `<workspace_root>` 占位符，**不**出现宿主机绝对路径；
- 所有具体本地路径（`/home/admin/...`、`~/.hermes/...`）只能出现在**本文件**或 `config/openclaw.json` 这种部署文件里；
- Hermes Gateway 加载路径（`~/.hermes/skills/openclaw-imports/recruit-ops/`）也属部署细节，只在本文件出现；
- skill 的指针只指向**仓库内**的 CLI 命令；**绝不**指向运维者的 shell alias / 私人脚本；
- 部署特定的 override（自定义 cron 频率、特殊环境变量、私有 secrets）应放在**独立**的运维笔记里，不进 SKILL / AGENT_RULES。

如果开源时要把这份 skill 整体打包：

1. `<workspace_root>/skills/recruit-ops/` 是自包含目录，整个目录可以打 tarball；
2. **删掉**或 fork 掉本文件（`docs/OPERATIONS.md`），让接手方按自己环境填写；
3. `config/openclaw.json` 默认值 / 占位符化；
4. 通知接手方按本文件 §3.2 重建 Hermes symlink、§4.3 设置 cron。

---

## 6. 故障排查速查

| 现象 | 排查 |
|---|---|
| Hermes 重启后路由还是老规则 | 软链断了？`ls -la ~/.hermes/skills/openclaw-imports/recruit-ops`；按 §3.2 重建。 |
| Hermes 不再加载这个 skill | 软链 target 文件不存在；或 SKILL.md frontmatter 被破坏（YAML 解析失败）。检查 `head -80 SKILL.md`。 |
| `ModuleNotFoundError: No module named 'lib'` | cron 调用没设 `PYTHONPATH=scripts`。改 cron_runner 启动行。 |
| `intake.cmd_send_cv` 发到错误的对象 | 飞书 open_id 没配。查 env var 或 `config/openclaw.json`。 |
| `auto_reject.cmd_scan_exam_timeout` 失败 | SMTP / 邮件模板问题。看 cli_wrapper 推的飞书告警 + journalctl 日志。**不要手动跑 scanner 真跑**（会和 cron 撞车，候选人会被双发拒信 — v3.5 实测过）。 |
| `outbound.cmd_send` 成功但 `talent.cmd_update` 失败 | chain 中间一步失败；邮件已发出、DB 未推进。`feishu.cmd_notify --severity critical` 已自动告警；老板手动 `talent.cmd_update` 补救（详见 SKILL.md §6 / AGENT_RULES.md §1）。 |
| 候选人附件路径里残留 `doc_<hex>_` 前缀 | `lib.candidate_storage.import_cv` 已在新代码里自动剥；历史数据用 `talent.cmd_normalize_cv_filenames` 一次性补救（v3.5.10）。 |

---

## 7. 内部上线前预检

启用无人值守 cron 前，先按 [INTERNAL_RELEASE_CHECKLIST.md](INTERNAL_RELEASE_CHECKLIST.md) 执行。最小命令：

```bash
cd <workspace_root>/skills/recruit-ops
PYTHONPATH=scripts uv run python3 -m ops.cmd_preflight_release --json
```

---

## 8. 相关文件

- 本文件不写业务规则，只写部署细节。业务规则去 [AGENT_RULES.md](AGENT_RULES.md)。
- 不写 confirm / 安全协议，去 [SKILL.md §2](../SKILL.md)。
- 不写 CLI 参数详细语法，去 [CLI_REFERENCE.md](CLI_REFERENCE.md)。
- 不写历史事故复盘（"为什么有这条规则"），去 [INCIDENT_RULES.md](INCIDENT_RULES.md)。
- 不写架构设计动因，去 [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md)。
