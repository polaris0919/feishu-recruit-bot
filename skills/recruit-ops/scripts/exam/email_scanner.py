#!/usr/bin/env python3
"""
邮件扫描模块：IMAP 连接、邮件解析、附件提取。
从 daily_exam_review.py 拆分出的独立模块。
"""
import imaplib
import os
import re
import socket
import sys
import email as email_lib
from email.header import decode_header
from typing import Dict, List, Optional, Tuple

_LIB = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import config as _cfg

_CODE_EXTS = {".py", ".ipynb", ".r", ".sql", ".java", ".cpp", ".go", ".js", ".ts",
              ".c", ".h", ".cs", ".rb", ".scala", ".kt", ".m", ".sh", ".txt", ".md"}


def connect_imap():
    """连接 IMAP 邮箱，含 30 秒超时。"""
    imap_cfg = _cfg.get("email_imap")
    host = imap_cfg.get("host", "")
    user = imap_cfg.get("user", "")
    pwd = imap_cfg.get("password", "")
    if not host or not user or not pwd:
        # fallback to env vars (backward compat)
        host = host or os.environ.get("RECRUIT_EXAM_IMAP_HOST", "")
        user = user or os.environ.get("RECRUIT_EXAM_IMAP_USER", "")
        pwd = pwd or os.environ.get("RECRUIT_EXAM_IMAP_PASS", "")
    if not host or not user or not pwd:
        raise ValueError("IMAP 配置缺失")
    port = int(os.environ.get("RECRUIT_EXAM_IMAP_PORT", "993"))
    socket.setdefaulttimeout(30)
    try:
        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(user, pwd)
        return imap
    finally:
        socket.setdefaulttimeout(None)


def decode_mime_header(value):
    # type: (str) -> str
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


def extract_body(msg):
    # type: (email_lib.message.Message) -> str
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


def extract_code_from_archive(payload, archive_name):
    # type: (bytes, str) -> Tuple[list, str]
    """解压压缩包，返回 (inner_file_list, code_text)。"""
    import io
    inner_files = []
    code_parts = []
    ext = os.path.splitext(archive_name)[1].lower()

    try:
        if ext == ".zip":
            import zipfile
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                for name in zf.namelist():
                    if name.endswith("/"):
                        continue
                    fext = os.path.splitext(name)[1].lower()
                    data = zf.read(name)
                    is_text = fext in _CODE_EXTS
                    inner_files.append({"filename": name, "size": len(data), "is_text": is_text})
                    if is_text:
                        try:
                            text = data.decode("utf-8", errors="replace")
                            code_parts.append("# File: {}\n{}".format(name, text[:4000]))
                        except Exception:
                            pass
    except Exception as e:
        print("[email_scanner] 解压 {} 失败: {}".format(archive_name, e), file=sys.stderr)

    return inner_files, "\n\n".join(code_parts)


def extract_attachment_info(msg):
    # type: (email_lib.message.Message) -> Tuple[list, str]
    """提取邮件附件信息和代码文本。返回 (attachment_info_list, code_text)。"""
    archive_exts = {".zip", ".rar", ".tar", ".gz", ".7z"}
    attachment_info = []
    code_parts = []

    for part in msg.walk():
        disposition = str(part.get("Content-Disposition") or "")
        if "attachment" not in disposition and part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        if not filename:
            continue
        filename = decode_mime_header(filename)
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        ext = os.path.splitext(filename)[1].lower()
        is_text = ext in _CODE_EXTS
        info = {"filename": filename, "size": len(payload), "is_text": is_text}

        if ext in archive_exts:
            inner_files, archive_code = extract_code_from_archive(payload, filename)
            info["archive_contents"] = [f["filename"] for f in inner_files]
            if archive_code:
                code_parts.append(archive_code)
        elif is_text:
            try:
                text = payload.decode("utf-8", errors="replace")
                code_parts.append("# File: {}\n{}".format(filename, text[:8000]))
            except Exception:
                pass
        attachment_info.append(info)

    return attachment_info, "\n\n".join(code_parts)
