#!/usr/bin/env python3

"""查看指定日期的面试安排（一面/二面）。"""
import argparse
import datetime as dt
import json
import sys

from core_state import load_state


STATUS_LABELS = {
    "CONFIRMED": "已确认",
    "PENDING": "待确认",
    "UNSET": "未排期",
}


def _default_date():
    return dt.datetime.now().strftime("%Y-%m-%d")


def _parse_date(date_str):
    try:
        return dt.datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError("日期格式必须为 YYYY-MM-DD")


def _collect_items(target_date, confirmed_only=False):
    state = load_state()
    items = []
    for cand in (state.get("candidates") or {}).values():
        for round_num in (1, 2):
            time_key = "round{}_time".format(round_num)
            status_key = "round{}_confirm_status".format(round_num)
            interview_time = cand.get(time_key) or ""
            if not interview_time.startswith(target_date):
                continue
            status = cand.get(status_key) or "UNSET"
            if confirmed_only and status != "CONFIRMED":
                continue
            items.append({
                "time": interview_time,
                "round": round_num,
                "round_label": "一面" if round_num == 1 else "二面",
                "talent_id": cand.get("talent_id") or "",
                "candidate_name": cand.get("candidate_name") or "",
                "confirm_status": status,
                "confirm_status_label": STATUS_LABELS.get(status, status),
            })
    return sorted(items, key=lambda item: (item["time"], item["round"], item["talent_id"]))


def _format_text(title_label, target_date, items, confirmed_only=False):
    if confirmed_only:
        headline = "{}（{}）的已确认面试安排：".format(title_label, target_date)
        empty_line = "{}（{}）没有已确认的面试安排".format(title_label, target_date)
    else:
        headline = "{}（{}）的面试安排：".format(title_label, target_date)
        empty_line = "{}（{}）没有已录入的面试安排".format(title_label, target_date)

    if not items:
        return empty_line

    lines = [headline]
    for item in items:
        lines.append(
            "- {time} | {name} ({tid}) | {round_label} | {status}".format(
                time=item["time"],
                name=item["candidate_name"] or "未命名候选人",
                tid=item["talent_id"] or "未知 talent_id",
                round_label=item["round_label"],
                status=item["confirm_status_label"],
            )
        )
    return "\n".join(lines)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="查看指定日期的面试安排")
    p.add_argument("--date", type=_parse_date, default=_default_date(), help="目标日期，格式 YYYY-MM-DD（默认今天）")
    p.add_argument("--confirmed-only", action="store_true", help="只显示已确认的面试")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    return p.parse_args(sys.argv[1:] if argv is None else argv)


def main(argv=None):
    args = parse_args(argv)
    items = _collect_items(args.date, confirmed_only=args.confirmed_only)

    if args.json:
        print(json.dumps({
            "date": args.date,
            "count": len(items),
            "confirmed_only": bool(args.confirmed_only),
            "items": items,
        }, ensure_ascii=False, indent=2))
        return 0

    title_label = "今天" if args.date == _default_date() else "日期"
    print(_format_text(title_label, args.date, items, confirmed_only=args.confirmed_only))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
