#!/usr/bin/env python3
"""
把某个候选人的笔试回复邮件（含正文 + 附件 + 自动解压 zip/rar）从 IMAP 拉到本地目录。

注意：日常做 AI 评审无需手动调用本脚本——`cmd_exam_ai_review.py --talent-id t_xxx` 会自动调用
fetch_for() 拉取。本 CLI 仅在你想单独看候选人提交内容（不评审）时使用。

不修改候选人状态机字段，不修改 talent_events，纯只读拉取。

用法：
    python3 exam/fetch_exam_submission.py --talent-id t_xxx [--out /tmp/exam/<id>]
    python3 exam/fetch_exam_submission.py --email someone@example.com [--out ...]

默认输出目录（v3.5.8）：
  - 提供了 --talent-id：data/candidates/<talent_id>/exam_answer/legacy_fetch/
  - 仅有邮箱：/tmp/exam_submissions/<邮箱前缀>/（兜底）
"""
from __future__ import print_function

import argparse
import email as email_lib
import io
import os
import sys
import zipfile
from typing import List, Optional

from lib.core_state import get_tdb

from lib.exam_imap import (
    connect_imap,
    _decode_mime_header,
    _extract_body,
)


_CODE_EXTS = {".py", ".ipynb", ".cpp", ".cc", ".c", ".h", ".hpp",
              ".java", ".js", ".ts", ".r", ".sql", ".m", ".sh",
              ".md", ".txt", ".rst", ".csv", ".json", ".tsv"}
_ARCHIVE_EXTS = {".zip", ".rar"}


def _safe_name(name):
    base = os.path.basename(name).strip()
    if not base:
        return "unnamed.bin"
    base = base.replace("/", "_").replace("\\", "_")
    return base


def _extract_archive_to(payload, fname, out_dir):
    written = []
    ext = os.path.splitext(fname)[1].lower()
    try:
        if ext == ".zip":
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                for name in zf.namelist():
                    if name.endswith("/"):
                        continue
                    target = os.path.join(out_dir, name)
                    target_dir = os.path.dirname(target)
                    if target_dir and not os.path.isdir(target_dir):
                        os.makedirs(target_dir, exist_ok=True)
                    try:
                        with zf.open(name) as src, open(target, "wb") as dst:
                            dst.write(src.read())
                        written.append(target)
                    except Exception as e:
                        print("[warn] 解压失败 {}: {}".format(name, e), file=sys.stderr)
        elif ext == ".rar":
            try:
                import rarfile
            except ImportError:
                print("[warn] 未安装 rarfile，无法解压 {}".format(fname), file=sys.stderr)
                return written
            with rarfile.RarFile(io.BytesIO(payload)) as rf:
                for name in rf.namelist():
                    if name.endswith("/"):
                        continue
                    target = os.path.join(out_dir, name)
                    target_dir = os.path.dirname(target)
                    if target_dir and not os.path.isdir(target_dir):
                        os.makedirs(target_dir, exist_ok=True)
                    try:
                        with rf.open(name) as src, open(target, "wb") as dst:
                            dst.write(src.read())
                        written.append(target)
                    except Exception as e:
                        print("[warn] 解压失败 {}: {}".format(name, e), file=sys.stderr)
    except Exception as e:
        print("[warn] 压缩包打开失败 {}: {}".format(fname, e), file=sys.stderr)
    return written


def _download_message(msg, out_dir):
    """
    下载一封邮件的正文 + 附件到 out_dir。返回 dict：
      {"body_path": str, "files": [path, ...], "subject": str, "date": str, "from": str}
    """
    os.makedirs(out_dir, exist_ok=True)
    files = []

    body = _extract_body(msg)
    body_path = os.path.join(out_dir, "_email_body.txt")
    with open(body_path, "w", encoding="utf-8") as f:
        f.write(body or "")

    meta_path = os.path.join(out_dir, "_email_meta.txt")
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write("Subject: {}\n".format(_decode_mime_header(msg.get("Subject") or "")))
        f.write("From: {}\n".format(_decode_mime_header(msg.get("From") or "")))
        f.write("Date: {}\n".format(msg.get("Date") or ""))
        f.write("Message-ID: {}\n".format(msg.get("Message-ID") or ""))

    for part in msg.walk():
        disposition = str(part.get("Content-Disposition") or "")
        if "attachment" not in disposition:
            continue
        fname = part.get_filename() or ""
        if fname:
            try:
                fname = _decode_mime_header(fname)
            except Exception:
                pass
        fname = _safe_name(fname or "unnamed.bin")
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        ext = os.path.splitext(fname)[1].lower()

        if ext in _ARCHIVE_EXTS:
            archive_dir = os.path.join(out_dir, os.path.splitext(fname)[0])
            os.makedirs(archive_dir, exist_ok=True)
            archive_path = os.path.join(out_dir, fname)
            with open(archive_path, "wb") as f:
                f.write(payload)
            files.append(archive_path)
            inner = _extract_archive_to(payload, fname, archive_dir)
            files.extend(inner)
            continue

        target = os.path.join(out_dir, fname)
        with open(target, "wb") as f:
            f.write(payload)
        files.append(target)

    return {
        "body_path": body_path,
        "files": files,
        "subject": _decode_mime_header(msg.get("Subject") or ""),
        "date": msg.get("Date") or "",
        "from": _decode_mime_header(msg.get("From") or ""),
    }


def fetch_for(email_addr=None, talent_id=None, exam_id=None, out=None, max_msgs=5):
    if not email_addr and not talent_id and not exam_id:
        raise ValueError("必须提供 --talent-id 或 --email 或 --exam-id 至少一个")

    if talent_id and not email_addr:
        tdb = get_tdb()
        if tdb is None:
            raise RuntimeError("talent_db 未启用，无法通过 --talent-id 反查邮箱，请改用 --email")
        cand = tdb.get_one(talent_id)
        if not cand:
            raise RuntimeError("未找到 talent_id={}".format(talent_id))
        email_addr = cand.get("candidate_email") or ""
        exam_id = exam_id or cand.get("exam_id")
        if not email_addr:
            raise RuntimeError("候选人 {} 在 DB 中无邮箱字段".format(talent_id))
        print("[info] talent_id={} candidate_email={} exam_id={}".format(
            talent_id, email_addr, exam_id
        ), file=sys.stderr)

    if not out:
        # v3.5.8：默认落到候选人专属目录，不再丢 /tmp（自动清理风险 + 散落）
        # 仅当能拿到 talent_id 才走新路径；否则退回 /tmp（无主邮件没法归类）
        if talent_id:
            from lib.candidate_storage import exam_answer_dir, ensure_candidate_dirs
            ensure_candidate_dirs(talent_id)
            out = str(exam_answer_dir(talent_id) / "legacy_fetch")
        else:
            slug = email_addr.split("@")[0] if email_addr else "anon"
            out = os.path.join("/tmp", "exam_submissions", slug)
    os.makedirs(out, exist_ok=True)

    imap = connect_imap()
    try:
        imap.select("INBOX")
        # 严格 AND 搜索：FROM 是主锁，exam_id 仅作为可选加严条件。
        # 旧版本用三次独立 search + 并集（OR 语义），会因 IMAP BODY 搜索的子串/quote 穿透
        # 把别人邮件拉进来；改为单次 AND search + 二次校验。
        candidates = []
        seen_uids = set()

        if not email_addr:
            print("[error] 缺少候选人邮箱，拒绝执行宽搜索（避免拉到别人的邮件）", file=sys.stderr)
            return out, []

        criteria = ["FROM", '"{}"'.format(email_addr)]
        # exam_id 用作可选加严条件（AND，不是 OR）。仅当邮件主题/正文真包含才算匹配。
        # 多数情况下 FROM 已足够唯一，exam_id 仅在候选人多次发件时帮助挑出最匹配的那封。
        # 但因为 BODY 子串匹配在某些服务器上不可靠，这里只把 exam_id 用于后置 ranking，
        # 不放进 IMAP search criteria，避免回到旧 bug。

        status, data = imap.search(None, *criteria)
        if status != "OK" or not data or not data[0]:
            print("[warn] 未在 INBOX 中找到任何 FROM={} 的邮件".format(email_addr), file=sys.stderr)
            return out, []

        for mid in data[0].split():
            if mid in seen_uids:
                continue
            seen_uids.add(mid)
            status, raw = imap.fetch(mid, "(RFC822)")
            if status != "OK":
                continue
            raw_bytes = raw[0][1]
            msg = email_lib.message_from_bytes(raw_bytes)

            # 二次校验：From header 实际地址必须包含 target email（防 IMAP server 行为差异）
            from_raw = msg.get("From")
            from_hdr = str(from_raw or "").lower()
            if email_addr.lower() not in from_hdr:
                continue

            date_str = msg.get("Date") or ""
            candidates.append((mid, date_str, msg))

        if not candidates:
            print("[warn] 二次校验后未找到任何 FROM={} 的邮件".format(email_addr), file=sys.stderr)
            return out, []

        # 按邮件 Date 倒序，挑最新 N 封
        from email.utils import parsedate_to_datetime
        def _key(item):
            try:
                return parsedate_to_datetime(item[1])
            except Exception:
                return None
        sortable = [(k, c) for c in candidates if (k := _key(c))]
        if sortable:
            sortable.sort(key=lambda x: x[0], reverse=True)
            top = [c for _, c in sortable[:max_msgs]]
        else:
            top = candidates[-max_msgs:]

        all_files = []
        for i, (mid, date_str, msg) in enumerate(top):
            sub_out = out if len(top) == 1 else os.path.join(out, "msg_{}".format(i + 1))
            info = _download_message(msg, sub_out)
            print("[ok] 邮件 #{} {} | subject: {} | 附件: {}".format(
                i + 1, date_str, info["subject"], len(info["files"])
            ), file=sys.stderr)
            all_files.extend(info["files"])
        return out, all_files
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def main(argv=None):
    p = argparse.ArgumentParser(description="把候选人笔试回复从 IMAP 拉到本地（只读）")
    p.add_argument("--talent-id", help="候选人 talent_id（会从 DB 反查邮箱）")
    p.add_argument("--email", help="候选人邮箱（不传 --talent-id 时必填）")
    p.add_argument("--exam-id", help="exam_id 兜底搜索（如 exam-t_xxx-...）")
    p.add_argument("--out", help="输出目录（默认 /tmp/exam_submissions/<id 或 邮箱前缀>）")
    p.add_argument("--max", type=int, default=5, help="最多拉取最新 N 封匹配邮件（默认 5）")
    args = p.parse_args(argv)

    out, files = fetch_for(
        email_addr=args.email,
        talent_id=args.talent_id,
        exam_id=args.exam_id,
        out=args.out,
        max_msgs=args.max,
    )
    print("\n[done] 输出目录: {}".format(out))
    print("[done] 共下载 {} 个文件".format(len(files)))
    if args.talent_id:
        print("\n提示：跑 AI 评审无需手动 fetch，直接：")
        print("  PYTHONPATH=skills/recruit-ops/scripts python3 \\")
        print("    skills/recruit-ops/scripts/exam/cmd_exam_ai_review.py \\")
        print("    --talent-id {}".format(args.talent_id))
        print("（会自动复用刚才拉的缓存；加 --refetch 强制重拉，加 --feishu --save-event 推飞书+审计）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
