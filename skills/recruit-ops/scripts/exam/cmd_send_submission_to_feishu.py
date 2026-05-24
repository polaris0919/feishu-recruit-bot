#!/usr/bin/env python3
"""exam/cmd_send_submission_to_feishu.py —— 发送候选人最新笔试附件到飞书。"""
from __future__ import print_function

import argparse
import json
from pathlib import Path

from lib import talent_db
from lib.candidate_storage import data_root
from lib.cli_wrapper import UserInputError


def _latest_exam_email(talent_id):
    rows = talent_db._query_all(
        "SELECT email_id, subject, sent_at, ai_summary, attachments "
        "FROM talent_emails "
        "WHERE talent_id = %s AND direction = 'inbound' AND context = 'exam' "
        "ORDER BY sent_at DESC LIMIT 10",
        (talent_id,),
    )
    for row in rows:
        attachments = row.get("attachments") or []
        saved = [a for a in attachments if a and a.get("saved") and a.get("path")]
        if saved:
            row = dict(row)
            row["saved_attachments"] = saved
            return row
    return None


def _attachment_path(meta):
    p = Path(str(meta.get("path") or ""))
    if p.is_absolute():
        return p
    return data_root() / p


def _build_parser():
    p = argparse.ArgumentParser(description="把候选人最新笔试提交附件发到飞书")
    p.add_argument("--talent-id", required=True)
    p.add_argument("--to", default="boss", choices=[
        "boss", "hr", "polaris",
        "interviewer-master", "interviewer-bachelor", "interviewer-cpp",
    ])
    p.add_argument("--open-id", default="")
    p.add_argument("--confirm-open-id", default="",
                   help="使用 --open-id 时必须重复传入同一个值，防误发")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    snap = talent_db.get_full_talent_snapshot(args.talent_id)
    if not snap:
        raise UserInputError("候选人不存在: {}".format(args.talent_id))
    email_row = _latest_exam_email(args.talent_id)
    if not email_row:
        raise UserInputError("未找到已保存的笔试提交附件: {}".format(args.talent_id))

    candidate_name = snap.get("candidate_name") or args.talent_id
    title = (
        "候选人笔试提交附件\n"
        "候选人：{} ({})\n"
        "邮件时间：{}\n"
        "主题：{}\n"
        "AI 摘要：{}"
    ).format(
        candidate_name, args.talent_id,
        email_row.get("sent_at"),
        email_row.get("subject") or "(无主题)",
        email_row.get("ai_summary") or "(暂无)",
    )

    sent = []
    from feishu import cmd_send_file
    for meta in email_row["saved_attachments"]:
        path = _attachment_path(meta)
        rc = cmd_send_file.main([
            "--file", str(path),
            "--to", args.to,
            *(["--open-id", args.open_id,
               "--confirm-open-id", args.confirm_open_id] if args.open_id else []),
            "--title", title,
            *(["--dry-run"] if args.dry_run else []),
            "--json",
        ])
        sent.append({"path": str(path), "ok": rc == 0, "name": meta.get("name")})
        if rc != 0:
            break

    payload = {
        "ok": all(x["ok"] for x in sent),
        "talent_id": args.talent_id,
        "candidate_name": candidate_name,
        "email_id": str(email_row.get("email_id")),
        "attachments": sent,
        "dry_run": bool(args.dry_run),
    }
    print(json.dumps(payload, ensure_ascii=False) if args.json else payload)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UserInputError as e:
        print("[exam.cmd_send_submission_to_feishu] INPUT ERROR: {}".format(e))
        raise SystemExit(1)
