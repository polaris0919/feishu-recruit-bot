#!/usr/bin/env bash
# deploy/uninstall.sh —— 卸载 recruit-ops systemd units (C1, v3.8.7)
#
# 做什么:
#   1) stop + disable timer / service
#   2) 删 ~/.config/systemd/user/ 里的软链
#   3) daemon-reload
#
# 不做:
#   - 不动 DB schema / data/ 候选人目录 (那是 HR 数据, 卸载脚本不该碰)
#   - 不删 .venv / config/*.json
set -euo pipefail

SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

log() { echo "[uninstall] $*"; }

log "== 1. 停 timer / service =="
systemctl --user stop recruit-cron-runner.timer 2>/dev/null || true
systemctl --user stop recruit-cron-runner.service 2>/dev/null || true
systemctl --user disable recruit-cron-runner.timer 2>/dev/null || true

log "== 2. 删 unit 链 =="
for unit in recruit-cron-runner.service recruit-cron-runner.timer; do
    dst="${SYSTEMD_USER_DIR}/${unit}"
    if [ -L "${dst}" ]; then
        rm "${dst}"
        log "  removed ${dst}"
    elif [ -e "${dst}" ]; then
        log "  ${dst} 不是软链 (可能是手动 cp 的), 跳过, 请人工核对"
    else
        log "  ${dst} 不存在, 跳过"
    fi
done

log "== 3. daemon-reload =="
systemctl --user daemon-reload

log "完成。DB / data/ / config 未动, 如需彻底清理参见 docs/OPERATIONS.md §备份恢复。"
