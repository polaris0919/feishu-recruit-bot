# recruit-workspace

这个仓库当前以 `skills/recruit-ops` 为主，根目录 README 只保留导航与仓库卫生说明，避免旧文档继续传播过时信息。

## 入口

- 主项目文档：`skills/recruit-ops/README.md`
- CLI 总参考：`skills/recruit-ops/docs/CLI_REFERENCE.md`
- 复杂协商回归清单：`skills/recruit-ops/docs/COMPLEX_NEGOTIATION_REGRESSION.md`

## 目录约定

- `skills/recruit-ops/`：招聘流程主项目
- `config/*.example.json`：可提交的配置模板
- `config/*.json`：本地真实配置，不应提交
- `state/*.json`：本地运行状态，不应提交

## 运行前提

- Python 3.10+
- PostgreSQL
- 飞书应用配置
- IMAP / SMTP 配置

具体安装、初始化和命令用法，统一以 `skills/recruit-ops/README.md` 与 `skills/recruit-ops/docs/CLI_REFERENCE.md` 为准。
