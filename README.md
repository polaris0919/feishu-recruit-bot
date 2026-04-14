# feishu-recruit-bot

飞书招聘流程自动化：候选人状态、笔试扫描、面试确认与改期、简历解析、可选 PostgreSQL 人才库。

## 仓库结构

| 路径 | 说明 |
|------|------|
| [skills/recruit-ops/](skills/recruit-ops/) | 主代码与文档（`README.md`、`CLI_REFERENCE.md`、Python 脚本） |
| [docs/recruit-boss-SKILL.md](docs/recruit-boss-SKILL.md) | OpenClaw / Hermes 侧「老板自然语言」技能说明（路径占位为 `<workspace_root>`） |
| [config/*.example.json](config/) | 配置模板；复制为同名 `.json` 并填入真实值（**勿提交真实 config**） |

## 快速开始

1. 克隆本仓库到任意目录（记为 `<workspace_root>`）。
2. 设置环境变量 `RECRUIT_WORKSPACE_ROOT` 指向该目录（若脚本默认推断路径不对时）。
3. 将 `config/*.example.json` 复制为 `config/*.json` 并按 [skills/recruit-ops/README.md](skills/recruit-ops/README.md) 完成飞书、邮箱、数据库等配置。
4. `cd skills/recruit-ops/scripts && python3 test_all.py` 跑回归测试。

## 安全说明

本仓库 **不包含** 真实密钥、候选人 JSON 状态或简历附件；这些文件由本地 `.gitignore` 排除。
