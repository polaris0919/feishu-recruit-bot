#!/usr/bin/env python3
"""tests/test_metrics_dump.py —— ops.cmd_metrics_dump 单元测试 (C2, v3.8.7)。

不连 DB: 把 cur.execute / fetchall / fetchone mock 掉, 验证:
  1) _stage_counts 把 DB rows 投影到 STAGES 全集 + 缺失填 0
  2) _last_24h_email_counters 正确分桶 inbound/outbound + analyzed/pending
  3) _exam_timeout_pending 抽取 SQL 参数对得上 (threshold_days=3)
  4) _format_human 输出包含期望字段
"""
import io
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

import tests.helpers  # noqa: F401  side-effect: env / sys.modules


def _make_cur(canned):
    """构造一个最小化 cursor 假 mock, 按调用次数 yield 不同 fetchall 结果。

    canned = [(rows_for_first_query, ...), (rows_for_second_query, ...), ...]
    每次 execute 后, fetchall() / fetchone() 取 canned 的下一个; 实际 SQL 不验。
    """
    cur = mock.MagicMock()
    cur._index = [0]

    def _next():
        i = cur._index[0]
        rows = canned[i] if i < len(canned) else []
        cur._index[0] = i + 1
        return rows

    def _execute(sql, params=()):
        cur._next_rows = _next()

    cur.execute.side_effect = _execute
    cur.fetchall.side_effect = lambda: cur._next_rows
    cur.fetchone.side_effect = lambda: (cur._next_rows[0] if cur._next_rows else None)
    return cur


class TestStageCounts(unittest.TestCase):

    def test_fills_missing_stages_with_zero(self):
        from ops import cmd_metrics_dump as m
        from lib.core_state import STAGES

        cur = _make_cur([
            [("NEW", 3), ("ROUND1_SCHEDULED", 2), ("ONBOARDED", 5)],
        ])
        out = m._stage_counts(cur)

        self.assertEqual(set(out.keys()), set(STAGES),
                         "_stage_counts 应当输出 STAGES 全集而不只是 DB 有数据的桶")
        self.assertEqual(out["NEW"], 3)
        self.assertEqual(out["ROUND1_SCHEDULED"], 2)
        self.assertEqual(out["ONBOARDED"], 5)
        self.assertEqual(out["EXAM_REVIEWED"], 0)

    def test_unknown_stage_in_db_is_dropped(self):
        """DB 万一有 chk 约束允许之外的 stage(理论上 B2 守不到的情况),
        _stage_counts 不应往输出里塞它——避免下游 dashboard 把奇怪 stage 当真。
        """
        from ops import cmd_metrics_dump as m

        cur = _make_cur([[("BOGUS_STAGE", 99), ("NEW", 1)]])
        out = m._stage_counts(cur)
        self.assertNotIn("BOGUS_STAGE", out)
        self.assertEqual(out["NEW"], 1)


class TestLast24hEmailCounters(unittest.TestCase):

    def test_buckets_inbound_status_correctly(self):
        from ops import cmd_metrics_dump as m

        cur = _make_cur([[
            ("inbound", "analyzed", 5),
            ("inbound", "replied", 2),
            ("inbound", "received", 3),
            ("outbound", "sent", 4),
        ]])
        out = m._last_24h_email_counters(cur, datetime.now(timezone.utc))

        self.assertEqual(out["inbound"]["inserted"], 10)
        self.assertEqual(out["inbound"]["analyzed"], 7,
                         "analyzed + replied 都算 analyzed")
        self.assertEqual(out["inbound"]["pending"], 3,
                         "received 进 pending 桶")
        self.assertEqual(out["outbound"]["sent"], 4)

    def test_empty_result_zero_filled(self):
        from ops import cmd_metrics_dump as m
        cur = _make_cur([[]])
        out = m._last_24h_email_counters(cur, datetime.now(timezone.utc))
        self.assertEqual(out, {
            "inbound": {"inserted": 0, "analyzed": 0, "pending": 0},
            "outbound": {"sent": 0},
        })


class TestExamTimeoutPending(unittest.TestCase):

    def test_threshold_days_param_passed_to_sql(self):
        from ops import cmd_metrics_dump as m
        cur = _make_cur([[(0,)]])
        m._exam_timeout_pending(cur, threshold_days=5)
        sql, params = cur.execute.call_args.args[0], cur.execute.call_args.args[1]
        self.assertIn("EXAM_SENT", sql)
        self.assertIn("exam_sent_at", sql)
        threshold_dt = params[0]
        delta = datetime.now(timezone.utc) - threshold_dt
        self.assertGreater(delta.total_seconds() / 86400, 4.9, "5 天阈值 ≈ 5*24h")
        self.assertLess(delta.total_seconds() / 86400, 5.1)


class TestFormatHuman(unittest.TestCase):

    def test_contains_all_sections(self):
        from ops import cmd_metrics_dump as m
        snap = {
            "ts": "2026-05-16T00:00:00",
            "stage_count": {st: 0 for st in m.STAGES},
            "last_24h_emails": {
                "inbound": {"inserted": 7, "analyzed": 6, "pending": 1},
                "outbound": {"sent": 3},
            },
            "exam_timeout_pending": 2,
            "heartbeat_age_minutes": 4.5,
            "db_query_latency_ms": 11.0,
        }
        snap["stage_count"]["ONBOARDED"] = 4
        out = m._format_human(snap)
        self.assertIn("stage_count", out)
        self.assertIn("ONBOARDED", out)
        self.assertIn("inbound  inserted=7 analyzed=6 pending=1", out)
        self.assertIn("outbound sent=3", out)
        self.assertIn("exam_timeout_pending", out)
        self.assertIn("4.5 min", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
