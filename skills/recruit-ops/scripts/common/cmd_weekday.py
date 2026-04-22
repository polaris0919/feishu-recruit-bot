#!/usr/bin/env python3
"""common/cmd_weekday.py —— 日期 → 周几查证（v3.5.14 / 2026-04-22 新增）

【用途】
agent 在起草任何含"X月X日（周X）"、"下周X"、"周X X点"等表述的邮件 / 飞书草稿
**之前**必须先调一次本脚本，把返回的 weekday_cn 字段照抄进 body。LLM 心算
weekday 经常错（2026-04-22 候选人A邮件 msg_demo_* 就是事故源点：写了"5月6日
（周二）"，实际 5 月 6 日是周三）。

【时区】固定 Asia/Shanghai。所有 weekday / "今天" / 相对日期都按上海时间算，
和候选人邮件、HR 飞书、面试日历的时区一致。

【输入格式】（位置参数，可一次传多个）
  - 标准：2026-05-06 / 2026/05/06 / 2026.5.6 / 20260506
  - 中文：2026年5月6日 / 5月6日（无年时按 --year-strategy 决定）
  - 简短：5-6 / 5/6 / 5.6（无年时按 --year-strategy 决定）
  - 带时间：2026-05-06 09:00 / 2026-05-06T14:30:00（时间部分忽略，仅用日期）
  - 相对：today / tomorrow / yesterday / +N / -N（基准 = 上海当天）
  - 不传任何位置参数 = 今天

【--year-strategy】仅对"无年份"输入生效（默认 auto）：
  - auto：今年该日期还没过 → 用今年；已过 → 用明年（避免误回到去年）
  - this：强制今年
  - next：强制明年

【输出】
  默认（人话）：
    2026-05-06 (周三 / Wednesday) | 距今 +14 天
  --json：
    {"input": "5-6", "date": "2026-05-06", "weekday_index": 2,
     "weekday_cn": "周三", "weekday_en": "Wednesday",
     "is_today": false, "is_past": false, "days_from_today": 14,
     "iso_with_dow": "2026-05-06 周三"}
  传多个日期时：每行一条；--json 输出 list。

【调用示例】
  # 验证一个日期
  PYTHONPATH=scripts python3 -m common.cmd_weekday 2026-05-06
  # → 2026-05-06 (周三 / Wednesday) | 距今 +14 天

  # 一次验证多个候选时间段
  PYTHONPATH=scripts python3 -m common.cmd_weekday 5-6 5-13 +3 today
  # → 4 行输出

  # JSON 给 agent 程序消费
  PYTHONPATH=scripts python3 -m common.cmd_weekday 5月6日 --json

  # 今天
  PYTHONPATH=scripts python3 -m common.cmd_weekday
"""
from __future__ import print_function

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    _SHANGHAI = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover —— py<3.9 不该跑到这
    _SHANGHAI = None


# ─── 常量 ───────────────────────────────────────────────────────────────────

_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_WEEKDAY_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
               "Saturday", "Sunday"]

_RELATIVE_LITERALS = {"today": 0, "tomorrow": 1, "yesterday": -1,
                      "今天": 0, "明天": 1, "昨天": -1}

# +N / -N 相对偏移
_RE_REL_OFFSET = re.compile(r"^([+-])(\d+)$")

# 各种"完整日期"格式：年-月-日 三件套，分隔符可以是 - / . 年
_RE_FULL_DATE_VARIANTS = [
    re.compile(r"^(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})$"),
    re.compile(r"^(?P<y>\d{4})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日?$"),
    re.compile(r"^(?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})$"),  # 20260506 紧凑型
]

# 无年份的"短日期"：5-6 / 5/6 / 5.6 / 5月6日
_RE_SHORT_DATE_VARIANTS = [
    re.compile(r"^(?P<m>\d{1,2})[-/.](?P<d>\d{1,2})$"),
    re.compile(r"^(?P<m>\d{1,2})月(?P<d>\d{1,2})日?$"),
]


class WeekdayInputError(ValueError):
    """位置参数无法解析时抛出。main 转成 stderr + exit 2。"""


# ─── 时间锚 ─────────────────────────────────────────────────────────────────

def _today():
    # type: () -> date
    """上海当天（本脚本时区基准）。"""
    if _SHANGHAI is not None:
        return datetime.now(_SHANGHAI).date()
    return date.today()


# ─── 解析 ───────────────────────────────────────────────────────────────────

def _strip_time_suffix(token):
    # type: (str) -> str
    """`2026-05-06 09:00` / `2026-05-06T14:30:00` → `2026-05-06`。

    策略：从左往右找第一个 `T` 或第一个空格，截断。仅在该位置之后还有数字 +
    冒号时才认为是时间后缀；否则返回原 token（防止误伤"5月6日"中的中文）。
    """
    for sep in ("T", " "):
        if sep in token:
            head, tail = token.split(sep, 1)
            if re.match(r"^\d{1,2}:\d{2}", tail):
                return head
    return token


def _build_date_with_year_strategy(month, day, strategy, today):
    # type: (int, int, str, date) -> date
    """无年份输入根据 strategy 决定取哪一年。"""
    if strategy == "this":
        year = today.year
    elif strategy == "next":
        year = today.year + 1
    else:  # auto
        try:
            candidate_this = date(today.year, month, day)
        except ValueError as e:
            raise WeekdayInputError("非法月日 {}-{}：{}".format(month, day, e))
        year = today.year if candidate_this >= today else today.year + 1
    try:
        return date(year, month, day)
    except ValueError as e:
        raise WeekdayInputError("非法日期 {}-{}-{}：{}".format(year, month, day, e))


def parse_date_token(raw, year_strategy="auto", today=None):
    # type: (str, str, date | None) -> date
    """把单个用户传入的字符串解析成 date。失败抛 WeekdayInputError。"""
    if today is None:
        today = _today()
    if raw is None:
        raise WeekdayInputError("空输入")
    token = raw.strip()
    if not token:
        raise WeekdayInputError("空输入")

    lower = token.lower()
    if lower in _RELATIVE_LITERALS:
        return today + timedelta(days=_RELATIVE_LITERALS[lower])
    if token in _RELATIVE_LITERALS:
        return today + timedelta(days=_RELATIVE_LITERALS[token])

    m = _RE_REL_OFFSET.match(token)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        days = int(m.group(2))
        return today + timedelta(days=sign * days)

    # 剥时间后缀（仅当真的是 HH:MM 形式才剥；防止误伤中文格式）
    token_for_date = _strip_time_suffix(token)

    for pat in _RE_FULL_DATE_VARIANTS:
        m = pat.match(token_for_date)
        if m:
            try:
                return date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
            except ValueError as e:
                raise WeekdayInputError("非法日期 {}：{}".format(raw, e))

    for pat in _RE_SHORT_DATE_VARIANTS:
        m = pat.match(token_for_date)
        if m:
            return _build_date_with_year_strategy(
                int(m.group("m")), int(m.group("d")), year_strategy, today)

    raise WeekdayInputError(
        "无法解析日期 {!r}。支持: 2026-05-06 / 5-6 / 5月6日 / today / +3 等。"
        "完整列表见 `python3 -m common.cmd_weekday --help`。".format(raw))


# ─── 输出渲染 ──────────────────────────────────────────────────────────────

def describe(d, today=None, raw_input=None):
    # type: (date, date | None, str | None) -> dict
    """返回结构化描述（也是 --json 的 schema）。"""
    if today is None:
        today = _today()
    idx = d.weekday()
    delta_days = (d - today).days
    iso = d.strftime("%Y-%m-%d")
    return {
        "input": raw_input if raw_input is not None else iso,
        "date": iso,
        "weekday_index": idx,        # 0=周一, 6=周日（兼容 datetime.weekday）
        "weekday_cn": _WEEKDAY_CN[idx],
        "weekday_en": _WEEKDAY_EN[idx],
        "is_today": delta_days == 0,
        "is_past": delta_days < 0,
        "days_from_today": delta_days,
        "iso_with_dow": "{} {}".format(iso, _WEEKDAY_CN[idx]),
    }


def format_human(info):
    # type: (dict) -> str
    """默认人话格式：'2026-05-06 (周三 / Wednesday) | 距今 +14 天'。"""
    days = info["days_from_today"]
    if days == 0:
        delta_str = "就是今天"
    elif days > 0:
        delta_str = "距今 +{} 天".format(days)
    else:
        delta_str = "距今 {} 天（已过去）".format(days)
    line = "{date} ({cn} / {en}) | {delta}".format(
        date=info["date"], cn=info["weekday_cn"], en=info["weekday_en"],
        delta=delta_str)
    if info["input"] != info["date"]:
        line += "  [输入: {}]".format(info["input"])
    return line


# ─── 主流程 ─────────────────────────────────────────────────────────────────

def _build_parser():
    p = argparse.ArgumentParser(
        prog="common.cmd_weekday",
        description="日期 → 周几查证。agent 起草含 'X月X日（周X）' / '下周X' "
                    "等表述的邮件 / 飞书草稿前必须先调本脚本，避免 LLM 心算出错。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="详细输入格式见模块顶部 docstring；\n"
               "测试钉死见 scripts/tests/test_common_weekday.py。",
    )
    p.add_argument("dates", nargs="*",
                   help="一个或多个日期；不传 = 今天。"
                        "支持 2026-05-06 / 5-6 / 5月6日 / today / tomorrow / +3 / -7。")
    p.add_argument("--year-strategy", dest="year_strategy",
                   choices=("auto", "this", "next"), default="auto",
                   help="无年份输入时取哪一年（默认 auto：今年该日还没过取今年，"
                        "已过取明年；this 强制今年；next 强制明年）")
    p.add_argument("--json", action="store_true",
                   help="结构化 JSON 输出（多个日期时是 list）")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    today = _today()

    raw_dates = args.dates or [today.strftime("%Y-%m-%d")]
    results = []
    had_error = False
    for raw in raw_dates:
        try:
            d = parse_date_token(raw, year_strategy=args.year_strategy, today=today)
        except WeekdayInputError as e:
            had_error = True
            if args.json:
                results.append({"input": raw, "error": str(e)})
            else:
                print("[cmd_weekday] ERROR: {}".format(e), file=sys.stderr)
            continue
        results.append(describe(d, today=today, raw_input=raw))

    if args.json:
        payload = results if len(raw_dates) > 1 else (results[0] if results else {})
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for info in results:
            if "error" in info:
                continue
            print(format_human(info))

    return 0 if not had_error else 2


if __name__ == "__main__":
    sys.exit(main() or 0)
