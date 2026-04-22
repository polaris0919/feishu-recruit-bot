#!/usr/bin/env python3
"""通用 IMAP / 跟进相关单元测试。

【v3.4 Phase 3 重写】
原来这里有大量 followup_scanner / pending_store / followup_analyzer 的回归测试，
那些模块在 v3.4 Phase 3 一并下线（inbox.cmd_scan + inbox.cmd_analyze 接管）。
本文件只保留与具体模块解耦的、仍然有价值的通用单测：

  - TestStripQuotedReply：邮件引用块剥离规则（实现已迁到 inbox.cmd_scan）
  - TestFlattenHeader   ：smtp_sender._flatten_header 折叠 CRLF / 制表符
  - TestHttpRetry       ：lib.http_retry 通用重试器
  - TestSideEffectGuardDB：lib.talent_db._update 在 db_writes_disabled 下的拦截
"""
from __future__ import print_function

import os
import unittest

import tests.helpers  # noqa: F401  side-effect: 注入内存 talent_db


# ════════════════════════════════════════════════════════════════════════════
# 邮件引用块剥离（实现来自 inbox.cmd_scan）
# ════════════════════════════════════════════════════════════════════════════

class TestStripQuotedReply(unittest.TestCase):
    def setUp(self):
        from inbox.cmd_scan import _strip_quoted_reply
        self.fn = _strip_quoted_reply

    def test_qq_chinese_quote(self):
        body = (
            "您好，今天已经参加完面试，想确认下入职时间。\n\n"
            "------------------ 原始邮件 ------------------\n"
            "发件人: hr@x.com\n"
            "请尽快确认。\n"
        )
        out = self.fn(body)
        self.assertIn("入职时间", out)
        self.assertNotIn("hr@x.com", out)
        self.assertNotIn("原始邮件", out)

    def test_gmail_on_wrote(self):
        body = (
            "Thanks, I will be there.\n\n"
            "On Tue, Apr 14, 2026 at 5:18 PM HR <hr@example.com> wrote:\n"
            "> please confirm\n"
        )
        out = self.fn(body)
        self.assertIn("Thanks", out)
        self.assertNotIn("please confirm", out)
        self.assertNotIn("wrote:", out)

    def test_handles_none(self):
        self.assertEqual(self.fn(None), "")


# ════════════════════════════════════════════════════════════════════════════
# smtp_sender._flatten_header
# ════════════════════════════════════════════════════════════════════════════

class TestFlattenHeader(unittest.TestCase):
    def setUp(self):
        from lib.smtp_sender import _flatten_header
        self.fn = _flatten_header

    def test_crlf_collapsed(self):
        v = "Re: foo\r\n bar"
        self.assertEqual(self.fn(v), "Re: foo bar")

    def test_tabs_and_multispace(self):
        v = "a\t\tb\t  c"
        self.assertEqual(self.fn(v), "a b c")

    def test_empty(self):
        self.assertEqual(self.fn(""), "")
        self.assertEqual(self.fn(None), "")

    def test_long_references_chain(self):
        v = "<a@x>\r\n <b@x>\r\n <c@x>"
        out = self.fn(v)
        self.assertNotIn("\r", out)
        self.assertNotIn("\n", out)
        self.assertEqual(out.count("<"), 3)


# ════════════════════════════════════════════════════════════════════════════
# lib.http_retry
# ════════════════════════════════════════════════════════════════════════════

class TestHttpRetry(unittest.TestCase):
    def test_retries_on_retriable(self):
        from lib.http_retry import call_with_retry

        attempts = {"n": 0}

        def flaky():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise TimeoutError("transient")
            return "ok"

        # RECRUIT_DISABLE_SIDE_EFFECTS 已被 helpers 设上，sleep 会被压成 0
        result = call_with_retry(flaky, retries=3, retriable=(TimeoutError,))
        self.assertEqual(result, "ok")
        self.assertEqual(attempts["n"], 3)

    def test_non_retriable_raises_immediately(self):
        from lib.http_retry import call_with_retry

        attempts = {"n": 0}

        def boom():
            attempts["n"] += 1
            raise ValueError("not retriable")

        with self.assertRaises(ValueError):
            call_with_retry(boom, retries=3, retriable=(TimeoutError,))
        self.assertEqual(attempts["n"], 1)

    def test_exhaust_raises_last(self):
        from lib.http_retry import call_with_retry

        def always_fail():
            raise TimeoutError("nope")

        with self.assertRaises(TimeoutError):
            call_with_retry(always_fail, retries=2, retriable=(TimeoutError,))


# ════════════════════════════════════════════════════════════════════════════
# lib.talent_db side-effect guard
# ════════════════════════════════════════════════════════════════════════════

class TestSideEffectGuardDB(unittest.TestCase):
    """db_writes_disabled 拦截 _update / upsert_one。"""

    def test_db_writes_disabled_blocks_update(self):
        os.environ["RECRUIT_DISABLE_DB_WRITES"] = "1"
        # tests/helpers.py 把 sys.modules["lib.talent_db"] 替换成了内存 DB。
        # 这里要测真实模块的 guard 行为，先临时复原，结束再还原，避免影响其他测试。
        import importlib
        import sys as _sys
        import lib as _lib
        saved_sys = _sys.modules.pop("lib.talent_db", None)
        saved_attr = getattr(_lib, "talent_db", None)
        try:
            tdb = importlib.import_module("lib.talent_db")
            tdb._is_enabled = lambda: True  # type: ignore[attr-defined]
            ok = tdb._update("UPDATE talents SET x=1", ())
            self.assertTrue(ok, "guard 命中时应直接返回 True 不抛错")
        finally:
            os.environ.pop("RECRUIT_DISABLE_DB_WRITES", None)
            if saved_sys is not None:
                _sys.modules["lib.talent_db"] = saved_sys
            if saved_attr is not None:
                _lib.talent_db = saved_attr


if __name__ == "__main__":
    unittest.main(verbosity=2)
