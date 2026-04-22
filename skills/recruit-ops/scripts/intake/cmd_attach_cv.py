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
    # v3.5.7：CV 解析得到的「是否会 C++」（true/false 字面量，None 走清空路径）
    "has_cpp",
}


def _import_cv_to_candidate_dir(talent_id, src_cv_path):
    # type: (str, str) -> str
    """v3.5.8：把 OpenClaw 缓冲区 / 任意路径下的 CV 搬进 candidates/<tid>/cv/。

    返回新路径（已落到 candidate dir 下的绝对路径）。
    src 已经在 candidate dir 下时 no-op，原路返回。
    走 candidate_storage.import_cv（mode=move 默认；env RECRUIT_CV_IMPORT_MODE
    可改 'copy' 给极少数怕丢原件的场景留口子）。
    异常向上抛，由 caller 兜成 rc=1。
    """
    from lib import candidate_storage as _cs
    mode = (os.environ.get("RECRUIT_CV_IMPORT_MODE") or "move").strip().lower()
    if mode not in ("move", "copy"):
        mode = "move"
    new_path = _cs.import_cv(talent_id, src_cv_path, mode=mode)
    return str(new_path)


def _apply_update(talent_id, cv_path, field_updates):
    # type: (str, str, dict) -> str
    """仅更新 cv_path 及白名单内的字段，其他流程字段保持不变。

    v3.5.8：cv_path 不为空时，先把文件搬进 candidates/<tid>/cv/，再把
    入库的 cv_path 改为新路径。返回最终入库的 cv_path（caller 用它做 echo）。
    """
    from lib.core_state import load_candidate, save_candidate
    cand = load_candidate(talent_id)
    if not cand:
        raise RuntimeError("未找到候选人: {}".format(talent_id))
    final_cv_path = cv_path
    if cv_path:
        final_cv_path = _import_cv_to_candidate_dir(talent_id, cv_path)
        cand["cv_path"] = final_cv_path
    for k, v in field_updates.items():
        if k in _SAFE_FIELDS:
            cand[k] = v
    save_candidate(talent_id, cand)

    # v3.5.9：CV 落地后顺手刷一下 by_name 软链（姓名可能在 ingest 时更新过）
    # warn-continue：alias 失败不影响 cv_path 已写入 DB 的成功
    try:
        from lib import candidate_aliases as _ca
        _ca.rebuild_alias_for(talent_id, cand.get("name") or cand.get("candidate_name"))
    except Exception as e:
        print("[cmd_attach_cv] alias 重建异常: {}".format(e), file=sys.stderr)

    return final_cv_path


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
                elif k == "has_cpp":
                    # v3.5.7：true/false/null 三态。任何不可识别的输入丢弃。
                    raw = v.strip().lower()
                    if raw in ("true", "1", "yes", "y"):
                        field_updates[k] = True
                    elif raw in ("false", "0", "no", "n"):
                        field_updates[k] = False
                    elif raw in ("", "null", "none", "unknown"):
                        field_updates[k] = None
                    else:
                        field_updates.pop(k, None)

    try:
        final_cv_path = _apply_update(
            args.talent_id.strip(), args.cv_path.strip(), field_updates)
    except Exception as e:
        print("ERROR: 写入失败: {}".format(e))
        return 1

    parts = ["已更新候选人 {} 的简历文件。".format(args.talent_id)]
    if args.cv_path:
        # v3.5.8：echo 落定后的绝对路径（已搬进 candidates/<tid>/cv/），
        # 老板审计 / agent 后续引用都用这个新路径
        parts.append("cv_path: {}".format(final_cv_path))
        if final_cv_path != args.cv_path.strip():
            parts.append("（已自动搬至候选人资料目录）")
    if field_updates:
        parts.append("同步更新字段: {}".format(
            "、".join("{}={}".format(k, v) for k, v in field_updates.items())))
    print("\n".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
