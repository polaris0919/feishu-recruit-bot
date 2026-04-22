#!/usr/bin/env python3
"""lib/exam_imap.py —— v3.5 招聘邮箱 IMAP 工具集（拆自 exam/daily_exam_review.py）。

【职责】
  - 加载 RECRUIT_EXAM_IMAP_* 环境（含 recruit-email-config.json / email-daily-summary-config.json
    两套配置文件兜底）
  - connect_imap()：登录并返回 imaplib.IMAP4_SSL（带 30 秒超时）
  - _decode_mime_header(value)：解码 MIME 头里的 quoted-printable / base64 / 中文
  - _extract_body(msg)：把 multipart 邮件里的 text/plain 部分拼成纯文本

【调用方（v3.5 现役）】
  - inbox/cmd_scan.py
  - exam/fetch_exam_submission.py
  - exam/cmd_exam_ai_review.py（间接通过 fetch_exam_submission）
  - ops/cmd_health_check.py

【与既有 lib/imap_* 的关系】
  招聘体系自始至终用的是「RECRUIT_EXAM_IMAP_*」一套独立的邮箱凭据
  （和 lib/email_watch、lib/smtp_sender 内部用的 SMTP/IMAP 配置不冲突）。
  这里保留独立 module 以避免触动其它通道。

【为什么用下划线开头的 _decode_mime_header / _extract_body】
  延续 daily_exam_review 时代的命名（外部代码已 import 这个名字），保持向后兼容。
  v3.5 不重命名，只搬家。
"""
from __future__ import print_function

import imaplib
import json
import os
from email.header import decode_header
from typing import Optional

from lib.recruit_paths import config_candidates


_EMAIL_CONFIG_LOADED = False


def _load_email_config():
    """从配置文件加载 IMAP 环境变量（若环境变量尚未设置）。"""
    global _EMAIL_CONFIG_LOADED
    if _EMAIL_CONFIG_LOADED:
        return
    _EMAIL_CONFIG_LOADED = True
    if os.environ.get("RECRUIT_EXAM_IMAP_PASS", "").strip():
        return

    config_paths = [str(p) for p in config_candidates("recruit-email-config.json")]
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

    fallback_paths = [
        str(p) for p in config_candidates("email-daily-summary-config.json")
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
    # type: (str, str) -> str
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
    # type: (Optional[str]) -> str
    parts = []
    for chunk, charset in decode_header(value or ""):
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
