## DDL 布局 (v3.8.7)

```
migrations/
├── schema.sql        ← 唯一事实源 (SSOT); 新装环境跑这一个文件即可
└── __init__.py
```

v3.8.7 (2026-05-16) 清盘后, 历史增量迁移文件已全部从仓库删除。`schema.sql` 完整内联了它们的最终态 (ADD COLUMN / DROP COLUMN / CHECK 重写 / 数据回填), 100% 幂等可重跑。

### 新装环境

```bash
psql "$DATABASE_URL" -f scripts/lib/migrations/schema.sql
```

### 已有环境升级 (代码 git pull 后)

```bash
psql "$DATABASE_URL" -f scripts/lib/migrations/schema.sql
```

schema.sql 全部 `IF NOT EXISTS` / `IF EXISTS` 兜底, 重跑只是 no-op, 不会动正常字段。跑完后:

```bash
.venv/bin/python -m pytest scripts/tests/test_architecture_contracts.py::test_python_stages_match_db_check_constraint -q
```

这条契约测试会比对 `schema.sql` 里 `chk_current_stage` 跟 Python `lib.core_state.STAGES` 是否同步 (B2, v3.8.7), 防止 schema 落后于代码。

### 加新 migration

```bash
# 1. 写新文件; 命名: YYYYMMDD_v<ver>_<short_desc>.sql
$EDITOR scripts/lib/migrations/<date>_v<ver>_<desc>.sql

# 2. 把变更也合并到 schema.sql 对应位置 (SSOT)
$EDITOR scripts/lib/migrations/schema.sql

# 3. 在生产 DB 上跑
psql "$DATABASE_URL" -f scripts/lib/migrations/<date>_v<ver>_<desc>.sql
# 或:
.venv/bin/python -m ops.cmd_db_migrate --apply

# 4. 确认生产已应用后, 该文件不立刻删, 攒一批等下次 schema cleanup 再统一删
#    (规则: 只要 schema.sql 已等价覆盖, 且 recruit_migrations 记账已写入, 就可以删)
```

> "**已 applied** ⇄ schema.sql 里有等价 DDL"——两者是 1:1 的, 不允许只改一边。

### 考古老 migration

v3.3 → v3.8.6 的增量迁移文件 (11 个) 在 v3.8.7 (commit `<TBD>`) 统一删除。要查具体 DDL/事故复盘:

```bash
git log --diff-filter=D --name-only -- scripts/lib/migrations/
git show <delete-commit>^:scripts/lib/migrations/_applied/<filename>
```
