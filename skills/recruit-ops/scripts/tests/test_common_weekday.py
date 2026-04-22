#!/usr/bin/env python3
"""common.cmd_weekday 单元测试（v3.5.14 / 2026-04-22）

钉死所有解析分支 + 已知日期 weekday，避免将来手贱误改。

事故源点：候选人A / t_demo01 邮件 msg_demo_* 的 body 写"5月6日（周二）"，
实际 2026-05-06 是**周三**。本套件钉死 2026-05-06=周三 这种已知日期作为
回归基准。
"""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta

_SCRIPTS_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)

# helpers 自带 RECRUIT_DISABLE_SIDE_EFFECTS=1 安全壳，本测试纯字符串无副作用
from tests import helpers  # noqa: F401, E402

from common import cmd_weekday  # noqa: E402
from common.cmd_weekday import (  # noqa: E402
    WeekdayInputError, describe, format_human, main, parse_date_token,
)


# ─── 已知日期 weekday 钉死（独立校验事实，与代码逻辑无关）────────────────

KNOWN = [
    # (year, month, day, expected_weekday_index, expected_weekday_cn)
    (2026, 5, 6, 2, "周三"),    # 冒烟枪：候选人A事故的"5月6日"
    (2026, 1, 1, 3, "周四"),    # 元旦
    (2025, 1, 1, 2, "周三"),
    (2024, 2, 29, 3, "周四"),   # 闰日
    (2026, 12, 31, 3, "周四"),  # 年末
]


class KnownDateWeekdayTests(unittest.TestCase):
    """直接校验 describe() 对已知日期的 weekday 计算。"""

    def test_known_dates(self):
        for y, m, d, idx, cn in KNOWN:
            with self.subTest(date="{}-{}-{}".format(y, m, d)):
                info = describe(date(y, m, d), today=date(2026, 4, 22))
                self.assertEqual(info["weekday_index"], idx,
                                 "{}-{}-{} 应为 weekday_index={}".format(y, m, d, idx))
                self.assertEqual(info["weekday_cn"], cn)


# ─── 输入解析 ───────────────────────────────────────────────────────────────

class ParseFullDateTests(unittest.TestCase):
    today = date(2026, 4, 22)  # 周三

    def test_iso_dash(self):
        self.assertEqual(parse_date_token("2026-05-06", today=self.today),
                         date(2026, 5, 6))

    def test_iso_slash(self):
        self.assertEqual(parse_date_token("2026/05/06", today=self.today),
                         date(2026, 5, 6))

    def test_iso_dot(self):
        self.assertEqual(parse_date_token("2026.5.6", today=self.today),
                         date(2026, 5, 6))

    def test_chinese_full(self):
        self.assertEqual(parse_date_token("2026年5月6日", today=self.today),
                         date(2026, 5, 6))
        # 末尾"日"可省
        self.assertEqual(parse_date_token("2026年5月6", today=self.today),
                         date(2026, 5, 6))

    def test_compact_eight_digit(self):
        self.assertEqual(parse_date_token("20260506", today=self.today),
                         date(2026, 5, 6))

    def test_unpadded_month_day(self):
        self.assertEqual(parse_date_token("2026-5-6", today=self.today),
                         date(2026, 5, 6))


class ParseShortDateYearStrategyTests(unittest.TestCase):
    """无年份输入：auto / this / next 的取年策略。"""

    def test_auto_future_in_this_year(self):
        # 今天 4-22；目标 5-6 还没到 → 取今年
        today = date(2026, 4, 22)
        self.assertEqual(parse_date_token("5-6", today=today), date(2026, 5, 6))

    def test_auto_past_in_this_year_jumps_next(self):
        # 今天 4-22；目标 1-1 早过了 → 自动跳明年（避免误回去年）
        today = date(2026, 4, 22)
        self.assertEqual(parse_date_token("1-1", today=today), date(2027, 1, 1))

    def test_auto_today_itself_uses_this_year(self):
        # 今天 4-22；目标 4-22 = 今天，不算"已过去"
        today = date(2026, 4, 22)
        self.assertEqual(parse_date_token("4-22", today=today), date(2026, 4, 22))

    def test_force_this(self):
        today = date(2026, 4, 22)
        # 1-1 即便已过去也用今年
        self.assertEqual(parse_date_token("1-1", year_strategy="this", today=today),
                         date(2026, 1, 1))

    def test_force_next(self):
        today = date(2026, 4, 22)
        self.assertEqual(parse_date_token("5-6", year_strategy="next", today=today),
                         date(2027, 5, 6))

    def test_chinese_short_with_strategy(self):
        today = date(2026, 4, 22)
        self.assertEqual(parse_date_token("5月6日", today=today), date(2026, 5, 6))
        self.assertEqual(parse_date_token("5月6", today=today), date(2026, 5, 6))


class ParseRelativeTests(unittest.TestCase):
    today = date(2026, 4, 22)

    def test_today(self):
        self.assertEqual(parse_date_token("today", today=self.today), self.today)
        self.assertEqual(parse_date_token("Today", today=self.today), self.today)
        self.assertEqual(parse_date_token("今天", today=self.today), self.today)

    def test_tomorrow_yesterday(self):
        self.assertEqual(parse_date_token("tomorrow", today=self.today),
                         self.today + timedelta(days=1))
        self.assertEqual(parse_date_token("yesterday", today=self.today),
                         self.today + timedelta(days=-1))
        self.assertEqual(parse_date_token("明天", today=self.today),
                         self.today + timedelta(days=1))

    def test_offsets(self):
        self.assertEqual(parse_date_token("+3", today=self.today),
                         self.today + timedelta(days=3))
        self.assertEqual(parse_date_token("-7", today=self.today),
                         self.today + timedelta(days=-7))
        self.assertEqual(parse_date_token("+0", today=self.today), self.today)


class ParseWithTimeSuffixTests(unittest.TestCase):
    today = date(2026, 4, 22)

    def test_iso_T_separator(self):
        self.assertEqual(parse_date_token("2026-05-06T14:30:00", today=self.today),
                         date(2026, 5, 6))

    def test_space_HHMM_separator(self):
        self.assertEqual(parse_date_token("2026-05-06 09:00", today=self.today),
                         date(2026, 5, 6))

    def test_chinese_no_time_not_affected(self):
        # "5月6日" 里的中文不能被误剥成时间
        self.assertEqual(parse_date_token("5月6日", today=self.today),
                         date(2026, 5, 6))


class ParseInvalidTests(unittest.TestCase):
    today = date(2026, 4, 22)

    def test_empty_raises(self):
        with self.assertRaises(WeekdayInputError):
            parse_date_token("", today=self.today)
        with self.assertRaises(WeekdayInputError):
            parse_date_token("   ", today=self.today)

    def test_garbage_raises(self):
        for bad in ["abc", "13-45", "2026-13-01", "5-32", "next-week"]:
            with self.subTest(bad=bad):
                with self.assertRaises(WeekdayInputError):
                    parse_date_token(bad, today=self.today)


# ─── describe() 字段完整性 ──────────────────────────────────────────────────

class DescribeShapeTests(unittest.TestCase):
    today = date(2026, 4, 22)

    def test_today_flag(self):
        info = describe(self.today, today=self.today)
        self.assertTrue(info["is_today"])
        self.assertFalse(info["is_past"])
        self.assertEqual(info["days_from_today"], 0)

    def test_future_flag(self):
        info = describe(date(2026, 5, 6), today=self.today)
        self.assertFalse(info["is_today"])
        self.assertFalse(info["is_past"])
        self.assertEqual(info["days_from_today"], 14)

    def test_past_flag(self):
        info = describe(date(2026, 4, 1), today=self.today)
        self.assertTrue(info["is_past"])
        self.assertEqual(info["days_from_today"], -21)

    def test_iso_with_dow(self):
        info = describe(date(2026, 5, 6), today=self.today)
        self.assertEqual(info["iso_with_dow"], "2026-05-06 周三")

    def test_input_field_defaults_to_iso(self):
        info = describe(date(2026, 5, 6), today=self.today)
        self.assertEqual(info["input"], "2026-05-06")
        info2 = describe(date(2026, 5, 6), today=self.today, raw_input="5-6")
        self.assertEqual(info2["input"], "5-6")


class FormatHumanTests(unittest.TestCase):
    today = date(2026, 4, 22)

    def test_future(self):
        info = describe(date(2026, 5, 6), today=self.today, raw_input="5-6")
        line = format_human(info)
        self.assertIn("2026-05-06", line)
        self.assertIn("周三", line)
        self.assertIn("Wednesday", line)
        self.assertIn("距今 +14 天", line)
        self.assertIn("[输入: 5-6]", line)

    def test_today_special_phrase(self):
        info = describe(self.today, today=self.today)
        self.assertIn("就是今天", format_human(info))

    def test_past_marker(self):
        info = describe(date(2026, 4, 1), today=self.today)
        self.assertIn("已过去", format_human(info))


# ─── main() 端到端：CLI 出参 ────────────────────────────────────────────────

class MainCliTests(unittest.TestCase):

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_default_no_arg_is_today(self):
        rc, stdout, _ = self._run([])
        self.assertEqual(rc, 0)
        # 默认输出今天对应的某个 weekday
        self.assertRegex(stdout, r"\d{4}-\d{2}-\d{2}.*周.*就是今天")

    def test_iso_human_output(self):
        rc, stdout, _ = self._run(["2026-05-06"])
        self.assertEqual(rc, 0)
        self.assertIn("2026-05-06", stdout)
        self.assertIn("周三", stdout)

    def test_json_single_returns_object(self):
        rc, stdout, _ = self._run(["2026-05-06", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(stdout)
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["date"], "2026-05-06")
        self.assertEqual(payload["weekday_cn"], "周三")

    def test_json_multi_returns_list(self):
        rc, stdout, _ = self._run(["2026-05-06", "2026-05-13", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(stdout)
        self.assertIsInstance(payload, list)
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["weekday_cn"], "周三")
        self.assertEqual(payload[1]["weekday_cn"], "周三")  # 一周后还是周三

    def test_invalid_input_exit_code_2(self):
        rc, _, stderr = self._run(["not-a-date"])
        self.assertEqual(rc, 2)
        self.assertIn("无法解析日期", stderr)

    def test_invalid_in_json_keeps_running(self):
        # 多日期场景下，单条失败不应炸；rc=2 表示有失败
        rc, stdout, _ = self._run(["2026-05-06", "garbage", "--json"])
        self.assertEqual(rc, 2)
        payload = json.loads(stdout)
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["weekday_cn"], "周三")
        self.assertIn("error", payload[1])


if __name__ == "__main__":
    unittest.main()
