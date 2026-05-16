#!/usr/bin/env python3
"""lib/db/connection.py —— PostgreSQL 低层连接 + 通用读写 (B1, v3.8.7)。

═══════════════════════════════════════════════════════════════════════════════
本模块职责
═══════════════════════════════════════════════════════════════════════════════
- _is_enabled / _conn_params / _connect: 连接管理
- _update / _query_one / _query_all: 通用读写, 含 dry-run guard
- DBWriteError + _short_sql: 写错时的异常包装

═══════════════════════════════════════════════════════════════════════════════
为什么先拆这一层
═══════════════════════════════════════════════════════════════════════════════
- 没有业务语义, 纯 SQL 执行 + 异常包装
- 任何业务子模块 (talents / events / emails) 拆出后都会 import 它
- 测试基建 (`tests/helpers._InMemoryTdb`) 替换的是 `lib.talent_db`,
  本模块作为 talent_db 内部依赖被一并跳过, 不需新增 mock 面

═══════════════════════════════════════════════════════════════════════════════
向后兼容
═══════════════════════════════════════════════════════════════════════════════
- lib/talent_db.py 通过 `from lib.db.connection import *` 把全部符号
  重新挂到 talent_db namespace, 23 个 caller `_tdb._update(...)` 仍正常工作
- 新代码可以直接 `from lib.db.connection import _update`, 但不强求,
  也不批量改老代码; v4.0 再评估是否真把 talent_db 那层 shim 收掉
"""
from __future__ import print_function

import sys
from contextlib import contextmanager
from typing import List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from lib import config as _cfg


# ─── 异常 ─────────────────────────────────────────────────────────────────────

class DBWriteError(RuntimeError):
    """DB 写入失败 (INSERT/UPDATE/DELETE 抛 psycopg2 异常后包装上抛)。

    设计原则 (v3.8.5): 写入路径的失败**绝不静默吞掉**, 必须沿调用栈传到
    `cli_wrapper.run_with_self_verify`, 由其推飞书 critical 告警 + 非零退出。

    历史原因 (INCIDENT_RULES §15): v3.5.11 的事故链就是 `_update` 静默吞了
    "talent_emails 写不进去" 的 CHECK constraint 错误 → executor 把"写库失败"
    误判成"发邮件失败" → 重发了第二封拒信。这个异常的存在就是为了让那条
    链一开头就响。

    读路径 (get_one / _query_one) 仍保持返回 None / [] 的宽容语义, 因为
    "查不到" 和 "DB 抖动" 的差别在读侧多数 caller 处理一致 (None → 跳过) 。
    """

    def __init__(self, sql_preview, original):
        # type: (str, BaseException) -> None
        self.sql_preview = sql_preview
        self.original = original
        super(DBWriteError, self).__init__(
            "{}: {}".format(type(original).__name__, str(original)[:300])
        )


def _short_sql(sql, limit=200):
    # type: (str, int) -> str
    """规整 SQL 文本: 折叠多空白, 截 200 字符。仅给告警 / 日志可读用。"""
    s = " ".join(str(sql).split())
    return s if len(s) <= limit else s[:limit] + "...(+{} chars)".format(len(s) - limit)


# ─── 连接管理 ─────────────────────────────────────────────────────────────────

def _is_enabled():
    return _cfg.db_enabled()


def _conn_params():
    # type: () -> dict
    """Backward-compatible DB params helper for legacy callers."""
    return _cfg.db_conn_params()


@contextmanager
def _connect():
    """上下文管理器: 获取连接, 自动提交或回滚, 最后关闭。"""
    conn = psycopg2.connect(**_conn_params())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── 通用读写 ─────────────────────────────────────────────────────────────────

def _update(sql, params):
    # type: (str, tuple) -> bool
    """单条写入辅助。

    返回值:
      - True   实际写入成功, 或 db_writes_disabled() dry-run 路径
      - False  DB 未启用 (_is_enabled() == False, 常见于测试 / CI)
      - 抛 DBWriteError  真实写入失败 (psycopg2 异常)

    v3.8.5 改造: 之前 except Exception 后只 print stderr → return False,
    导致 caller (多为 update_calendar_event_id、mark_confirmed 等不检查
    返回值的纯过程函数) 拿到的是"看起来没事"的结果, 实际 DB 没改。
    """
    if not _is_enabled():
        return False
    try:
        from lib.side_effect_guard import db_writes_disabled
        if db_writes_disabled():
            print(
                "[talent_db][dry-run] 跳过 _update: {}".format(_short_sql(sql, 160)),
                file=sys.stderr,
            )
            return True
    except ImportError:
        pass
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
        return True
    except Exception as e:
        raise DBWriteError(_short_sql(sql), e)


def _query_one(sql, params=()):
    # type: (str, tuple) -> Optional[dict]
    if not _is_enabled():
        return None
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                return cur.fetchone()
    except Exception as e:
        print("[talent_db] QUERY 失败: {}".format(e), file=sys.stderr)
        return None


def _query_all(sql, params=()):
    # type: (str, tuple) -> List[dict]
    if not _is_enabled():
        return []
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                return cur.fetchall()
    except Exception as e:
        print("[talent_db] QUERY 失败: {}".format(e), file=sys.stderr)
        return []
