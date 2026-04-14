Unsupported opcode: LOAD_FAST_AND_CLEAR (241)
Unsupported opcode: LOAD_FAST_CHECK (237)
# Source Generated with Decompyle++
# File: db_migrations.cpython-312.pyc (Python 3.12)

'''
正式的数据库 migration 系统。
按版本号顺序执行 SQL 文件，已执行的版本记录在 schema_migrations 表中。
'''
import os
import re
import sys
_MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'migrations')

def _ensure_migrations_table(conn):
    cur = conn.cursor()
    cur.execute('\n            CREATE TABLE IF NOT EXISTS schema_migrations (\n                version INTEGER PRIMARY KEY,\n                applied_at TIMESTAMPTZ DEFAULT NOW()\n            )\n        ')
    None(None, None)
    conn.commit()
    return None
    with None:
        if not None:
            pass
    continue


def _applied_versions(conn):
    cur = conn.cursor()
    cur.execute('SELECT version FROM schema_migrations ORDER BY version')
# WARNING: Decompyle incomplete


def _discover_migrations():
    '''Return sorted list of (version, filepath).'''
    results = []
    if not os.path.isdir(_MIGRATIONS_DIR):
        return results
    for fname in None(os.listdir(_MIGRATIONS_DIR)):
        m = re.match('^(\\d+)_.*\\.sql$', fname)
        if not m:
            continue
        results.append((int(m.group(1)), os.path.join(_MIGRATIONS_DIR, fname)))
    return sorted(results)


def run_migrations(conn):
    '''Execute all pending migrations. Returns number of migrations applied.'''
    _ensure_migrations_table(conn)
    applied = _applied_versions(conn)
    migrations = _discover_migrations()
    count = 0
# WARNING: Decompyle incomplete

