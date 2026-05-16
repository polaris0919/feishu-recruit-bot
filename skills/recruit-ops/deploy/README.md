# Deploy

一键化部署脚本 + systemd unit 模板。**仅适用于当前生产宿主机** (`/home/admin/recruit-workspace`)。

## 文件

```
deploy/
├── install.sh                          一键: schema 同步 → systemd 装载 → timer 启动
├── uninstall.sh                        卸载: 停 timer + 删 unit 软链, 不动 DB/数据
├── systemd/
│   ├── recruit-cron-runner.service     cron_runner.py 调度入口
│   └── recruit-cron-runner.timer       每 10 分钟 (`*:0/10`)
└── README.md                           本文件
```

## 首次部署

```bash
cd /home/admin/recruit-workspace
cd skills/recruit-ops && uv sync --group dev && cd -          # 安装依赖
$EDITOR config/talent-db-config.json                          # 填 DB 凭据
bash skills/recruit-ops/deploy/install.sh                    # 一键装
```

完成后 `systemctl --user list-timers` 应看到 `recruit-cron-runner.timer enabled`，下一次触发在 10 分钟内。

## 更新部署 (修了 cron_runner / unit 模板后)

直接重跑 `install.sh`：

- service / timer 是软链，所以仓库里更新模板后**立即生效**，无需再跑 install。
- 真正需要重跑的场景：改了 `cron/cron_runner.py` 或 `lib/migrations/schema.sql`。

```bash
git pull
bash skills/recruit-ops/deploy/install.sh           # 默认会跑 schema.sql
# 或仅刷 systemd, 跳过 DB:
bash skills/recruit-ops/deploy/install.sh --skip-db
```

## 卸载

```bash
bash skills/recruit-ops/deploy/uninstall.sh
```

会停 timer / service + 删两个 unit 软链。**不动**: DB schema、`data/` 候选人目录、`config/`。

## 故障排查

请直接看 [docs/OPERATIONS.md §4](../docs/OPERATIONS.md)。
