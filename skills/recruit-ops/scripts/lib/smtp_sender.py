#!/usr/bin/env python3
"""
SMTP 发送器（v3.3 把 followup/ 的实现提到 lib/，被 outbound/cmd_send 共用）。

为什么不用 email-send 技能：那个外部技能不暴露 In-Reply-To / References，
而我们必须正确串线，否则候选人邮件客户端会把回信视为新会话。

复用 config.email_smtp 配置（与发笔试邀请同一账号）：
  config/email-send-config.json  →  smtp.host / port / username / password / from_email

向后兼容：保留 send_followup_reply 的导出名（v3.5 followup/ 目录已下线，
但 lib/smtp_sender 仍可能被未迁移的脚本/测试通过该名 import）；
新代码直接用 send_email_with_threading（语义更准）。
"""
from __future__ import print_function

import mimetypes
import smtplib
import socket
import ssl
import sys
import time
from email.message import EmailMessage
from email.utils import make_msgid, formatdate, formataddr
from pathlib import Path
from typing import List, Optional, Tuple

from lib import config as _cfg
from lib.side_effect_guard import side_effects_disabled

_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20MB 单封硬上限（多数邮箱网关在 25MB 截断，留余量）


def _smtp_cfg():
    # type: () -> dict
    cfg = _cfg.get("email_smtp") or {}
    smtp = cfg.get("smtp") or cfg
    if not smtp.get("host") or not smtp.get("username"):
        raise RuntimeError("email_smtp 配置缺失 host/username（检查 email-send-config.json）")
    return smtp


def _normalize_subject_for_reply(subject):
    # type: (str) -> str
    s = (subject or "").strip()
    if not s:
        return "Re: (no subject)"
    low = s.lower()
    if low.startswith("re:") or low.startswith("回复：") or low.startswith("回复:"):
        return s
    return "Re: " + s


def _flatten_header(value):
    # type: (Optional[str]) -> str
    """把 header 值里的 \r / \n / 制表符 / 多余空白全部折叠成单个空格。

    IMAP 取下来的 References / In-Reply-To 经常带 RFC 2822 折叠续行（"\r\n "），
    `email.message.EmailMessage` 在 default policy 下会直接报：
      Header values may not contain linefeed or carriage return characters
    在写入 header 前统一展平。
    """
    if not value:
        return ""
    return " ".join(str(value).split()).strip()


def _attach_files(msg, attachments):
    # type: (EmailMessage, List[Path]) -> List[dict]
    """把 attachments 列表读入并 attach 到 msg；返回审计用的 metadata 列表。

    fail-fast：文件不存在 / 单文件超 20MB 直接抛 RuntimeError；
    msg.set_content() 必须在调用本函数之前已经写过正文。
    """
    meta = []
    for ap in attachments or []:
        path = Path(ap)
        if not path.is_file():
            raise RuntimeError("附件文件不存在: {}".format(path))
        data = path.read_bytes()
        if len(data) > _MAX_ATTACHMENT_BYTES:
            raise RuntimeError(
                "附件 {} 超过单文件上限 {}MB（实际 {:.1f}MB）".format(
                    path.name, _MAX_ATTACHMENT_BYTES // (1024 * 1024),
                    len(data) / (1024 * 1024)
                )
            )
        ctype, _ = mimetypes.guess_type(str(path))
        if ctype is None:
            maintype, subtype = "application", "octet-stream"
        else:
            maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)
        meta.append({"name": path.name, "size": len(data), "mime": "{}/{}".format(maintype, subtype)})
    return meta


def _build_message(to_email, subject, body, in_reply_to, references,
                   from_email, from_name, cc=None, attachments=None):
    # type: (str, str, str, Optional[str], Optional[str], str, Optional[str], Optional[str], Optional[List[Path]]) -> Tuple[EmailMessage, str, List[dict]]
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, from_email)) if from_name else from_email
    msg["To"] = _flatten_header(to_email)
    if cc:
        msg["Cc"] = _flatten_header(cc)
    msg["Subject"] = _flatten_header(subject) or "(no subject)"
    msg["Date"] = formatdate(localtime=True)
    domain = (from_email.split("@")[-1] or "localhost").strip() or "localhost"
    new_msg_id = make_msgid(domain=domain)
    msg["Message-ID"] = new_msg_id
    in_reply_to = _flatten_header(in_reply_to)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        joined_refs = _flatten_header(references)
        if in_reply_to not in joined_refs:
            joined_refs = (joined_refs + " " + in_reply_to).strip()
        msg["References"] = joined_refs
    msg.set_content(body or "", charset="utf-8")
    attach_meta = _attach_files(msg, attachments) if attachments else []
    return msg, new_msg_id, attach_meta


def send_email_with_threading(
    to_email,
    subject,
    body,
    in_reply_to=None,
    references=None,
    cc=None,
    from_name=None,
    normalize_subject=True,
    attachments=None,
):
    # type: (str, str, str, Optional[str], Optional[str], Optional[str], Optional[str], bool, Optional[List[Path]]) -> str
    """
    发送一封邮件（v3.3 主入口）。带线程头时保持串线，不带时作为新会话。

    Args:
        normalize_subject: 仅当 in_reply_to 存在时有效；True 会自动加 "Re: " 前缀。
                           对 v3.3 outbound/cmd_send 的模板模式（新主题）应传 False。
        attachments: 可选，附件文件路径列表（Path 或 str）。每个文件 ≤ 20MB；
                     不存在 / 超大直接抛 RuntimeError。dry-run 时也会读取并校验，
                     这样问题在 dry-run 阶段就能暴露。

    返回实际发出的 Message-ID。side_effects_disabled() 时只返回 dry-run id 不投递。
    """
    smtp = _smtp_cfg()
    from_email = (smtp.get("from_email") or smtp.get("username") or "").strip()
    if not from_email:
        raise RuntimeError("SMTP 配置缺失 from_email")

    if in_reply_to and normalize_subject:
        final_subject = _normalize_subject_for_reply(subject)
    else:
        final_subject = (subject or "").strip() or "(no subject)"

    msg, new_msg_id, attach_meta = _build_message(
        to_email=to_email,
        subject=final_subject,
        body=body,
        in_reply_to=in_reply_to,
        references=references,
        from_email=from_email,
        from_name=from_name,
        cc=cc,
        attachments=attachments,
    )

    if side_effects_disabled():
        attach_repr = "; attachments={}".format(
            [m["name"] for m in attach_meta]) if attach_meta else ""
        print("[smtp_sender] DRY-RUN 跳过实际投递: to={} subject={} new_msg_id={}{}".format(
            to_email, final_subject, new_msg_id, attach_repr), file=sys.stderr)
        return new_msg_id

    host = smtp["host"]
    port = int(smtp.get("port") or 465)
    user = smtp["username"]
    pwd = smtp.get("password") or ""
    use_tls = bool(smtp.get("use_tls"))

    socket.setdefaulttimeout(30)
    last_err = None
    for attempt in range(2):
        try:
            if port == 465 and not use_tls:
                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
                    s.login(user, pwd)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(host, port, timeout=30) as s:
                    s.ehlo()
                    if use_tls:
                        s.starttls(context=ssl.create_default_context())
                        s.ehlo()
                    s.login(user, pwd)
                    s.send_message(msg)
            return new_msg_id
        except Exception as e:
            last_err = e
            print("[smtp_sender] attempt {} 发信失败: {}".format(attempt + 1, e), file=sys.stderr)
            time.sleep(1.5)
        finally:
            socket.setdefaulttimeout(None)

    raise RuntimeError("SMTP 发信失败: {}".format(last_err))


def send_followup_reply(
    to_email, subject, body,
    in_reply_to=None, references=None, cc=None, from_name=None,
):
    """[向后兼容] 旧名字保留，转发到 send_email_with_threading。

    旧调用方（v3.4 之前的 followup/cmd_followup_reply.py，v3.5 已下线）
    的语义就是回信，所以默认仍走 normalize_subject=True 的路径，
    行为完全等价于 v3.2。
    """
    return send_email_with_threading(
        to_email=to_email, subject=subject, body=body,
        in_reply_to=in_reply_to, references=references, cc=cc,
        from_name=from_name, normalize_subject=True,
    )
