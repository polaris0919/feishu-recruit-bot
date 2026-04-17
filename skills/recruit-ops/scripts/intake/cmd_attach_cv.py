#!/usr/bin/env python3
from __future__ import print_function

"""
将简历 PDF 路径及可选字段变更写入已有候选人记录（仅由 intake/cmd_ingest_cv.py 生成的确认命令调用）。
不触发任何邮件、飞书通知或日历事件。

用法（由 OC 在 HR 确认后执行，参数由 cmd_ingest_cv.py 自动生成）：
  python3 intake/cmd_attach_cv.py --talent-id t_xxx --cv-path <路径> --confirm \
      [--field education=博士 --field experience=摘要文本 ...]
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

_SAFE_FIELDS = {
    "candidate_name", "candidate_email", "phone", "wechat",
    "position", "education", "school", "work_years", "source", "experience",
}


def _apply_update(talent_id, cv_path, field_updates):
    # type: (str, str, dict) -> None
    """仅更新 cv_path 及白名单内的字段，其他流程字段保持不变。"""
    from core_state import load_candidate, save_candidate
    cand = load_candidate(talent_id)
    if not cand:
        raise RuntimeError("未找到候选人: {}".format(talent_id))
    if cv_path:
        cand["cv_path"] = cv_path
    for k, v in field_updates.items():
        if k in _SAFE_FIELDS:
            cand[k] = v
    save_candidate(talent_id, cand)


def main(argv=None):
    p = argparse.ArgumentParser(description="将简历 PDF 路径及字段变更写入已有候选人（确认步骤）")
    p.add_argument("--talent-id", required=True, help="候选人 talent_id")
    p.add_argument("--cv-path",   default="",    help="要写入的简历 PDF 本地路径")
    p.add_argument("--confirm",   action="store_true", help="必须传入，防止误执行")
    p.add_argument("--field",     action="append", default=[],
                   help="字段更新，格式：key=value，可多次使用")
    args = p.parse_args(argv or sys.argv[1:])

    if not args.confirm:
        print("ERROR: 必须加 --confirm 才能执行写入操作")
        return 1

    field_updates = {}
    for kv in args.field:
        if "=" in kv:
            k, v = kv.split("=", 1)
            k = k.strip()
            if k in _SAFE_FIELDS:
                field_updates[k] = v.strip()
                if k == "work_years":
                    try:
                        field_updates[k] = int(v.strip())
                    except ValueError:
                        field_updates.pop(k, None)

    try:
        _apply_update(args.talent_id.strip(), args.cv_path.strip(), field_updates)
    except Exception as e:
        print("ERROR: 写入失败: {}".format(e))
        return 1

    parts = ["已更新候选人 {} 的简历文件。".format(args.talent_id)]
    if args.cv_path:
        parts.append("cv_path: {}".format(args.cv_path))
    if field_updates:
        parts.append("同步更新字段: {}".format(
            "、".join("{}={}".format(k, v) for k, v in field_updates.items())))
    print("\n".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
