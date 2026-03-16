#!/usr/bin/env python3
"""
邮件自动扫描：每 30 分钟被 cron 触发，检查是否有新的笔试附件回复。
新邮件发现时，生成预审报告并通过飞书推送给 Boss。

手动触发：python3 daily_exam_review.py [--auto]
  --auto: cron 模式（静默；无新邮件不输出）
"""
from __future__ import print_function

import argparse
import imaplib
import json
import os
import sys
import email as email_lib
from email.header import decode_header
from datetime import datetime
from typing import List, Optional, Dict, Any

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


_EMAIL_CONFIG_LOADED = False


def _load_email_config():
    """从配置文件加载 IMAP 环境变量（若环境变量尚未设置）。"""
    global _EMAIL_CONFIG_LOADED
    if _EMAIL_CONFIG_LOADED:
        return
    _EMAIL_CONFIG_LOADED = True
    if os.environ.get("RECRUIT_EXAM_IMAP_PASS", "").strip():
        return
    config_paths = [
        os.path.join(_HERE, "recruit-email-config.json"),
        os.path.expanduser("~/.openclaw/recruit-email-config.json"),
        os.path.expanduser("~/.openclaw/workspace/recruit-email-config.json"),
    ]
    for path in config_paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                if k.startswith("RECRUIT_EXAM_") and v:
                    os.environ.setdefault(k, str(v).strip())
            if os.environ.get("RECRUIT_EXAM_IMAP_PASS", "").strip():
                return
        except Exception:
            continue
    # 兜底：从 email-daily-summary-config.json 读取 IMAP 配置
    fallback_paths = [
        os.path.expanduser("~/.openclaw/email-daily-summary-config.json"),
    ]
    for path in fallback_paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            imap = data.get("imap", {})
            if imap.get("host"):
                os.environ.setdefault("RECRUIT_EXAM_IMAP_HOST", imap["host"])
            if imap.get("port"):
                os.environ.setdefault("RECRUIT_EXAM_IMAP_PORT", str(imap["port"]))
            if imap.get("username"):
                os.environ.setdefault("RECRUIT_EXAM_IMAP_USER", imap["username"])
            if imap.get("password"):
                os.environ.setdefault("RECRUIT_EXAM_IMAP_PASS", imap["password"])
            return
        except Exception:
            continue


def _get_env(key, default=""):
    _load_email_config()
    return (os.environ.get(key) or "").strip() or default


def connect_imap():
    """连接 IMAP 邮箱，含 30 秒超时。"""
    import socket
    _load_email_config()
    host = _get_env("RECRUIT_EXAM_IMAP_HOST")
    port = int(_get_env("RECRUIT_EXAM_IMAP_PORT", "993"))
    user = _get_env("RECRUIT_EXAM_IMAP_USER")
    pwd = _get_env("RECRUIT_EXAM_IMAP_PASS")

    if not host or not user or not pwd:
        raise ValueError("RECRUIT_EXAM_IMAP_HOST / IMAP_USER / IMAP_PASS 未配置")

    socket.setdefaulttimeout(30)
    try:
        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(user, pwd)
        return imap
    finally:
        socket.setdefaulttimeout(None)


def _decode_mime_header(value):
    parts = []
    for chunk, charset in decode_header(value):
        if isinstance(chunk, bytes):
            cs = charset or "utf-8"
            if cs.lower() in ("unknown-8bit", "unknown"):
                cs = "utf-8"
            try:
                parts.append(chunk.decode(cs, errors="replace"))
            except (LookupError, UnicodeDecodeError):
                parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


def _extract_body(msg):
    body_parts = []
    for part in msg.walk():
        ct = part.get_content_type()
        disposition = str(part.get("Content-Disposition") or "")
        if ct == "text/plain" and "attachment" not in disposition:
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                if charset.lower() in ("unknown-8bit", "unknown"):
                    charset = "utf-8"
                body_parts.append(payload.decode(charset, errors="replace"))
    return "\n".join(body_parts)


def _extract_attachment_info(msg):
    """
    提取所有附件信息，返回 (attachment_info_list, code_text)。
    attachment_info_list: [{"filename": str, "size": int, "is_text": bool}]
    code_text: 所有可读文本附件合并（用于代码分析，每个文件最多 4000 字符）
    """
    attachment_info_list = []
    code_parts = []
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
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        size = len(payload)
        is_text = False
        try:
            charset = part.get_content_charset() or "utf-8"
            if charset.lower() in ("unknown-8bit", "unknown"):
                charset = "utf-8"
            text = payload.decode(charset, errors="replace")
            is_text = True
            code_parts.append("# File: {}\n{}".format(fname, text[:4000]))
        except Exception:
            pass
        attachment_info_list.append({
            "filename": fname,
            "size": size,
            "is_text": is_text,
        })
    code_text = "\n\n".join(code_parts)
    return attachment_info_list, code_text


def _lookup_candidate_by_exam_id(exam_id, tdb):
    """根据 exam_id 从数据库查找候选人，返回候选人 dict 或 None。"""
    if not exam_id or not tdb:
        return None
    try:
        state = tdb.load_state_from_db()
        candidates = state.get("candidates") or {}
        for tid, cand in candidates.items():
            if cand.get("exam_id") == exam_id:
                return dict(cand, talent_id=tid)
    except Exception:
        pass
    return None


def scan_new_replies(auto_mode=False):
    """
    扫描收件箱中未读的笔试回复邮件。
    返回已处理的邮件列表（每项包含预审结果 prereview_result）。
    """
    import re
    import exam_prereview as _prereview

    try:
        import talent_db as _tdb
        db_enabled = _tdb._is_enabled()
    except Exception:
        _tdb = None
        db_enabled = False

    processed_ids = set()
    if db_enabled:
        try:
            processed_ids = _tdb.get_processed_email_ids()
        except Exception:
            pass

    try:
        imap = connect_imap()
    except ValueError as e:
        if not auto_mode:
            print("IMAP 未配置，无法扫描邮件：{}".format(e))
        return []
    except Exception as e:
        if not auto_mode:
            print("IMAP 连接失败：{}".format(e))
        return []

    results = []
    try:
        imap.select("INBOX")
        status, data = imap.search(None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return results

        msg_ids = data[0].split()
        for mid in msg_ids[-20:]:  # 最多处理最新 20 封
            try:
                status, raw = imap.fetch(mid, "(RFC822)")
                if status != "OK":
                    continue
                raw_bytes = raw[0][1]
                msg = email_lib.message_from_bytes(raw_bytes)

                msg_id_header = (msg.get("Message-ID") or "").strip()
                if msg_id_header in processed_ids:
                    continue

                subject = _decode_mime_header(msg.get("Subject") or "")
                sender = _decode_mime_header(msg.get("From") or "")
                date_str = msg.get("Date") or ""
                body = _extract_body(msg)
                attachment_info_list, code_text = _extract_attachment_info(msg)

                # 过滤系统退信 / 非笔试邮件
                skip_keywords = [
                    "postmaster", "mailer-daemon", "退信", "undelivered",
                    "delivery failure", "mail delivery", "undeliverable",
                    "auto-reply", "out of office", "自动回复",
                ]
                subject_lower = subject.lower()
                sender_lower = sender.lower()
                if any(kw in subject_lower or kw in sender_lower for kw in skip_keywords):
                    continue

                exam_keywords = ["exam-", "笔试", "代码", "作业", "题目", "附件", "submission"]
                content_text = (subject + " " + body + " " + code_text).lower()
                is_exam_related = (
                    any(kw in content_text for kw in exam_keywords)
                    or bool(attachment_info_list)
                )
                if not is_exam_related:
                    continue

                # 提取 exam_id
                exam_match = re.search(r"exam-([a-zA-Z0-9_-]+)", subject + " " + body)
                exam_id = "exam-" + exam_match.group(1) if exam_match else None

                # 查找候选人信息（获取 exam_sent_at）
                cand_info = _lookup_candidate_by_exam_id(exam_id, _tdb if db_enabled else None)

                # 运行预审
                email_data = {
                    "sender": sender,
                    "subject": subject,
                    "date": date_str,
                    "body_text": body,
                    "code_text": code_text,
                    "attachment_info_list": attachment_info_list,
                }
                prereview_result = _prereview.run_prereview(email_data, cand_info or {})

                result = {
                    "message_id": msg_id_header,
                    "sender": sender,
                    "subject": subject,
                    "date": date_str,
                    "exam_id": exam_id,
                    "candidate_info": cand_info,
                    "prereview": prereview_result,
                }
                results.append(result)

                # 标记已处理 + 写预审结果到 DB
                if db_enabled and _tdb:
                    try:
                        if msg_id_header:
                            _tdb.mark_emails_processed([(msg_id_header, exam_id or "")])
                    except Exception:
                        pass
                    if cand_info and cand_info.get("talent_id"):
                        try:
                            _tdb.save_exam_prereview(
                                cand_info["talent_id"],
                                prereview_result["score"],
                                prereview_result["db_summary"],
                            )
                        except Exception:
                            pass

            except Exception as e:
                if not auto_mode:
                    print("处理邮件 {} 失败: {}".format(mid, e))
                continue
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    return results


def format_report(result):
    """格式化单封邮件的飞书推送报告，使用预审模块生成的完整报告。"""
    prereview = result.get("prereview")
    if prereview and prereview.get("report_text"):
        return prereview["report_text"]

    # 降级：没有预审结果时的简单格式
    lines = [
        "📧 新笔试回复",
        "- 发件人: {}".format(result.get("sender", "")),
        "- 主题: {}".format(result.get("subject", "")),
        "- 时间: {}".format(result.get("date", "")[:25] if result.get("date") else ""),
    ]
    if result.get("exam_id"):
        lines.append("- 笔试 ID: {}".format(result["exam_id"]))
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="邮件自动扫描，检查笔试回复")
    p.add_argument("--auto", action="store_true", help="cron 模式（无回复静默退出）")
    args = p.parse_args(argv or sys.argv[1:])

    results = scan_new_replies(auto_mode=args.auto)

    if not results:
        if not args.auto:
            print("[email_scan] 暂无新的笔试回复邮件。")
        return 0

    import feishu_notify as fn
    for r in results:
        report = format_report(r)
        ok = fn.send_text(report)
        if not args.auto:
            print("已推送到飞书: {}".format("✅" if ok else "❌"))
            print(report)

    print("[email_scan] 共发现 {} 封新笔试回复，已推送飞书。".format(len(results)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
