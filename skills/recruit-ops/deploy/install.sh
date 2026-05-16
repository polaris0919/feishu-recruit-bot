#!/usr/bin/env bash
# deploy/install.sh —— recruit-ops 一键部署 (C1, v3.8.7)
#
# 做什么:
#   1) 校验 systemd --user 可用 + python venv 已建
#   2) 跑 schema.sql 把 DB 推到当前版本 (幂等, 重跑安全)
#   3) 把仓库内的 systemd unit 通过软链挂到 ~/.config/systemd/user/
#   4) reload + enable + start timer
#   5) 立刻跑一次 service 验证(不等 10 分钟)
#
# 不做:
#   - 不动 ~/.config/ 里的其它 unit
#   - 不调 systemctl 之外的进程
#   - 不 sudo
#
# 用法:
#   bash skills/recruit-ops/deploy/install.sh
#   bash skills/recruit-ops/deploy/install.sh --skip-db   # 仅刷 systemd 单元

set -euo pipefail

WORKSPACE_ROOT="${RECRUIT_WORKSPACE_ROOT:-/home/admin/recruit-workspace}"
RECRUIT_OPS="${WORKSPACE_ROOT}/skills/recruit-ops"
DEPLOY_DIR="${RECRUIT_OPS}/deploy"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
VENV_PYTHON="${RECRUIT_OPS}/.venv/bin/python"
CONFIG_DIR="${RECRUIT_CONFIG_DIR:-${WORKSPACE_ROOT}/config}"
DB_CONFIG="${CONFIG_DIR}/talent-db-config.json"
SCHEMA_SQL="${RECRUIT_OPS}/scripts/lib/migrations/schema.sql"

SKIP_DB=0
for arg in "$@"; do
    case "$arg" in
        --skip-db) SKIP_DB=1 ;;
        *) echo "未知参数: $arg" >&2; exit 2 ;;
    esac
done

log() { echo "[install] $*"; }
die() { echo "[install][ERR] $*" >&2; exit 1; }

# ─── 1. 前置校验 ─────────────────────────────────────────────────────────────

log "== 1. 前置校验 =="

[ -d "${RECRUIT_OPS}" ] || die "找不到 ${RECRUIT_OPS}, 检查 WORKSPACE_ROOT"
[ -x "${VENV_PYTHON}" ] || die "找不到 ${VENV_PYTHON}, 请先 cd ${RECRUIT_OPS} && uv sync --group dev"

if ! systemctl --user --version >/dev/null 2>&1; then
    die "systemd --user 不可用, 本脚本不支持非 systemd 系统"
fi

mkdir -p "${SYSTEMD_USER_DIR}"

# ─── 2. 跑 schema.sql ────────────────────────────────────────────────────────

if [ "${SKIP_DB}" -eq 0 ]; then
    log "== 2. 同步 DB schema (schema.sql 幂等) =="
    [ -f "${DB_CONFIG}" ] || die "找不到 ${DB_CONFIG}, 先填好 DB 凭据"
    PGPASSWORD=$(${VENV_PYTHON} -c "import json,os,sys; cfg=json.load(open('${DB_CONFIG}')); sys.stdout.write(os.environ.get('TALENT_DB_PASSWORD') or cfg.get('TALENT_DB_PASSWORD',''))")
    PGUSER=$(${VENV_PYTHON} -c "import json,os,sys; cfg=json.load(open('${DB_CONFIG}')); sys.stdout.write(os.environ.get('TALENT_DB_USER') or cfg.get('TALENT_DB_USER','recruit_app'))")
    PGHOST=$(${VENV_PYTHON} -c "import json,os,sys; cfg=json.load(open('${DB_CONFIG}')); sys.stdout.write(os.environ.get('TALENT_DB_HOST') or cfg.get('TALENT_DB_HOST','127.0.0.1'))")
    PGPORT=$(${VENV_PYTHON} -c "import json,os,sys; cfg=json.load(open('${DB_CONFIG}')); sys.stdout.write(str(os.environ.get('TALENT_DB_PORT') or cfg.get('TALENT_DB_PORT','5432')))")
    PGDB=$(${VENV_PYTHON} -c "import json,os,sys; cfg=json.load(open('${DB_CONFIG}')); sys.stdout.write(os.environ.get('TALENT_DB_NAME') or cfg.get('TALENT_DB_NAME','recruit'))")
    [ -n "${PGPASSWORD}" ] || die "talent-db-config.json 里 TALENT_DB_PASSWORD 是空, 拒绝跑 schema"
    PGPASSWORD="${PGPASSWORD}" psql \
        -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" -d "${PGDB}" \
        -v ON_ERROR_STOP=1 \
        -f "${SCHEMA_SQL}" >/dev/null
    log "  schema.sql 应用完成"
else
    log "== 2. 跳过 schema 同步 (--skip-db) =="
fi

# ─── 3. 软链 systemd units ───────────────────────────────────────────────────

log "== 3. 挂载 systemd user units =="

for unit in recruit-cron-runner.service recruit-cron-runner.timer; do
    src="${DEPLOY_DIR}/systemd/${unit}"
    dst="${SYSTEMD_USER_DIR}/${unit}"
    [ -f "${src}" ] || die "找不到 ${src}"

    if [ -L "${dst}" ]; then
        current=$(readlink "${dst}")
        if [ "${current}" = "${src}" ]; then
            log "  ${unit} 已链到 ${src}, 不动"
            continue
        else
            log "  ${unit} 已链到 ${current}, 替换 -> ${src}"
            rm "${dst}"
        fi
    elif [ -e "${dst}" ]; then
        backup="${dst}.bak.$(date +%s)"
        log "  ${unit} 是普通文件, 先备份到 ${backup}"
        mv "${dst}" "${backup}"
    fi

    ln -s "${src}" "${dst}"
    log "  symlinked ${dst} -> ${src}"
done

# ─── 4. reload + enable + start ──────────────────────────────────────────────

log "== 4. 重载 systemd + enable timer =="

systemctl --user daemon-reload
systemctl --user enable recruit-cron-runner.timer >/dev/null
systemctl --user start recruit-cron-runner.timer

# ─── 5. 立刻跑一次 service 验证 ──────────────────────────────────────────────

log "== 5. 触发一次 service 跑 (验证) =="

# --no-block 不让 install.sh 卡住等 cron 跑完(可能 25 分钟)
systemctl --user start --no-block recruit-cron-runner.service

log "完成。后续运维参考: docs/OPERATIONS.md §4"
log ""
log "立刻查状态:"
log "  systemctl --user status recruit-cron-runner.timer"
log "  systemctl --user status recruit-cron-runner.service"
log "  journalctl --user -u recruit-cron-runner.service -n 100 --no-pager"
