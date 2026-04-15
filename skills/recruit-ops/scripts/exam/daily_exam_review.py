#!/usr/bin/env python3
from __future__ import print_function

"""
邮件自动扫描：每8小时被 cron 触发，检查笔试回复 + 一面/二面时间确认邮件。
新邮件发现时，生成报告并通过飞书推送给 Boss。

手动触发：python3 exam/daily_exam_review.py（在 scripts 目录下）
  --auto: cron 模式（静默；无新邮件不输出）
"""
import os
import sys

_LIB = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import argparse
import imaplib
import json
import re
import subprocess
import time
import email as email_lib
from email.header import decode_header
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from core_state import get_tdb
from recruit_paths import config_candidates, scripts_dir

_SCRIPTS = scripts_dir()


def _rel_script_py(cmd_base):
    """返回仍以分类目录暴露的脚本相对路径。"""
    if cmd_base == "cmd_reschedule_request":
        return "common/{}.py".format(cmd_base)
    if cmd_base.startswith("cmd_round1"):
        return "round1/{}.py".format(cmd_base)
    if cmd_base.startswith("cmd_round2"):
        return "round2/{}.py".format(cmd_base)
    if cmd_base.startswith("cmd_exam"):
        return "exam/{}.py".format(cmd_base)
    return "{}.py".format(cmd_base)

# ─── LLM 配置（DashScope，直连，绕过 Gateway）──────────────────────────────────
_DASHSCOPE_URL = "https://coding.dashscope.aliyuncs.com/v1/chat/completions"
_DASHSCOPE_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
_LLM_MODEL = os.environ.get("DASHSCOPE_MODEL", "qwen3-max-2026-01-23")


def _load_dashscope_key():
    """若环境变量未设置，从 openclaw.json 或配置文件中读取 API Key。"""
    global _DASHSCOPE_KEY
    if _DASHSCOPE_KEY:
        return
    config_paths = [
        str(p) for p in config_candidates("dashscope-config.json")
    ] + [
        str(p) for p in config_candidates("openclaw.json")
    ]
    for path in config_paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            key = (
                data.get("DASHSCOPE_API_KEY")
                or data.get("dashscope_api_key")
                or (data.get("llm") or {}).get("api_key")
                or ""
            )
            if key:
                _DASHSCOPE_KEY = key.strip()
                return
        except Exception:
            continue


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
    # 兜底：从 email-daily-summary-config.json 读取 IMAP 配置
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
    _load_email_config()
    return (os.environ.get(key) or "").strip() or default


def _parse_local_datetime(value, default_dt=None):
    # type: (Optional[str], Optional[datetime]) -> Optional[datetime]
    text = (value or "").strip()
    if not text:
        return None

    try:
        from dateutil import parser as _dtparser
        kwargs = {}
        if default_dt is not None:
            kwargs["default"] = default_dt
        parsed = _dtparser.parse(text, **kwargs)
        if getattr(parsed, "tzinfo", None) is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except Exception:
        pass

    for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _has_explicit_year(text):
    # type: (str) -> bool
    return bool(re.search(r"\b20\d{2}\b|20\d{2}[年/-]", text or ""))


def _normalize_new_time(new_time, email_body, current_time):
    # type: (Optional[str], str, str) -> Optional[str]
    raw = (new_time or "").strip()
    if not raw:
        return None

    current_dt = _parse_local_datetime(current_time)
    fallback_dt = current_dt or datetime.now()
    proposed_dt = _parse_local_datetime(raw, default_dt=fallback_dt)
    if proposed_dt is None:
        return raw

    explicit_year = _has_explicit_year(email_body or "")
    if not explicit_year:
        anchor_year = fallback_dt.year
        try:
            proposed_dt = proposed_dt.replace(year=anchor_year)
        except ValueError:
            pass

        if current_dt is not None and proposed_dt < (current_dt - timedelta(days=30)):
            try:
                proposed_dt = proposed_dt.replace(year=proposed_dt.year + 1)
            except ValueError:
                pass

    return proposed_dt.strftime("%Y-%m-%d %H:%M")


def _message_cursor_key(msg, fallback_mid):
    # type: (Any, Any) -> str
    msg_id = (msg.get("Message-ID") or "").strip()
    if msg_id:
        return msg_id
    if isinstance(fallback_mid, bytes):
        fallback_mid = fallback_mid.decode("ascii", errors="ignore")
    return "imap-mid:{}".format((fallback_mid or "").strip())


def _llm_analyze_reply(email_body, round_label="一面", current_time=""):
    # type: (str, str, str) -> Dict[str, Any]
    """
    调用 DashScope LLM 分析候选人邮件回复意图。
    返回 {"intent": "confirm|reschedule|request_online|defer_until_shanghai|unknown", "new_time": str|None, "summary": str}
    """
    text = (email_body or "").strip()
    if round_label == "二面":
        wants_online = any(x in text for x in ["线上", "视频面试", "腾讯会议", "会议链接", "video interview", "zoom"])
        defer_markers = [
            "不在上海", "暂时不在上海", "之后再约", "以后再约", "回上海再约",
            "等我回上海", "等之后有时间再约", "先不安排本次面试", "之后方便来上海",
            "在美国", "在国外", "在海外", "出国", "交换", "不在国内",
            "在日本", "在英国", "在欧洲", "在加拿大", "在澳洲",
        ]
        if any(x in text for x in defer_markers) and not wants_online:
            return {
                "intent": "defer_until_shanghai",
                "new_time": None,
                "summary": "候选人暂时不在国内/上海，之后再约",
            }

    _load_dashscope_key()
    if not _DASHSCOPE_KEY:
        return {"intent": "unknown", "new_time": None, "summary": "（LLM未配置，无法分析）"}

    prompt = (
        "你是一个招聘助手，请分析以下候选人邮件回复，判断候选人对{}邀请的意图。\n\n"
        "当前系统记录的{}时间：{}\n"
        "当前日期：{}\n\n"
        "邮件内容：\n{}\n\n"
        "请用JSON格式回复，包含以下字段：\n"
        "- intent: 只能是 confirm（确认参加）/ reschedule（要求改期）/ request_online（要求改为线上）/ defer_until_shanghai（暂时不在国内/上海，之后再约）/ unknown（意图不明）\n"
        "- new_time: 若候选人提出了新时间，填写时间字符串（如'2026-04-15 15:00'），否则填 null\n"
        "- summary: 一句话总结候选人意图（中文，20字以内）\n\n"
        "注意：\n"
        "- 候选人表示「可以」「没问题」「确认」「OK」「好的」等均视为 confirm\n"
        "- 候选人表示「不方便」「调整」「改一下」「换个时间」等均视为 reschedule\n"
        "- 候选人表示「不在国内」「人在外地」「希望线上」「视频面试」等均视为 request_online\n"
        "- 候选人表示「暂时不在国内/上海」「之后回国/回上海再约」「先不安排本次面试」等，且未要求线上，均视为 defer_until_shanghai\n"
        "- 候选人提出具体新时间，intent 必须是 reschedule\n"
        "- 若邮件中的时间未显式写年份，优先沿用当前系统记录时间的年份；不要臆造更早的历史年份\n"
        "只返回 JSON，不要其他内容。"
    ).format(
        round_label,
        round_label,
        current_time or "（未知）",
        datetime.now().strftime("%Y-%m-%d"),
        email_body[:1500],
    )

    try:
        import urllib.request as _req
        payload = json.dumps({
            "model": _LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        }).encode("utf-8")
        request = _req.Request(
            _DASHSCOPE_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer {}".format(_DASHSCOPE_KEY),
            },
        )
        with _req.urlopen(request, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"].strip()
        # 去除 Markdown 代码块包裹
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        parsed = json.loads(content)
        return {
            "intent": parsed.get("intent", "unknown"),
            "new_time": parsed.get("new_time"),
            "summary": parsed.get("summary", ""),
        }
    except Exception as e:
        return {"intent": "unknown", "new_time": None, "summary": "LLM分析失败: {}".format(str(e)[:50])}


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


_CODE_EXTS = {".py", ".ipynb", ".r", ".sql", ".java", ".cpp", ".go", ".js", ".ts",
              ".c", ".h", ".cs", ".rb", ".scala", ".kt", ".m", ".sh", ".txt", ".md"}


def _extract_code_from_archive(payload, archive_name):
    """
    解压 zip 或 rar 压缩包，返回 (inner_file_list, code_text)。
    inner_file_list: [{"filename": str, "size": int, "is_text": bool}]
    """
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

        elif ext == ".rar":
            try:
                import tempfile
                from unrar.cffi import rarfile as rar_mod
                with tempfile.NamedTemporaryFile(suffix=".rar", delete=False) as tmp:
                    tmp.write(payload)
                    tmp_path = tmp.name
                try:
                    rf = rar_mod.RarFile(tmp_path)
                    for info in rf.infolist():
                        name = info.filename
                        # 跳过目录项（尾斜杠 或 无扩展名的纯目录名）
                        if name.endswith("/") or name.endswith("\\"):
                            continue
                        fext = os.path.splitext(name)[1].lower()
                        if not fext:
                            continue
                        try:
                            data = rf.read(info)
                        except Exception:
                            continue
                        is_text = fext in _CODE_EXTS
                        inner_files.append({"filename": name, "size": len(data), "is_text": is_text})
                        if is_text:
                            try:
                                text = data.decode("utf-8", errors="replace")
                                code_parts.append("# File: {}\n{}".format(name, text[:4000]))
                            except Exception:
                                pass
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass
            except ImportError:
                pass
    except Exception:
        pass

    return inner_files, "\n\n".join(code_parts)


def _extract_attachment_info(msg):
    """
    提取所有附件信息，返回 (attachment_info_list, code_text)。
    attachment_info_list: [{"filename": str, "size": int, "is_text": bool}]
    code_text: 所有可读文本附件合并（用于代码分析，每个文件最多 4000 字符）
    自动解压 .zip / .rar，读取其中的代码文件。
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

        fext = os.path.splitext(fname)[1].lower()

        # 压缩包：自动解压并读取内部代码文件
        if fext in (".zip", ".rar"):
            inner_files, archive_code = _extract_code_from_archive(payload, fname)
            attachment_info_list.append({
                "filename": fname,
                "size": size,
                "is_text": False,
                "archive_contents": [f["filename"] for f in inner_files],
            })
            if archive_code:
                code_parts.append(
                    "# Archive: {}\n{}".format(fname, archive_code)
                )
            continue

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


def _lookup_exam_sent_at_from_sent(imap, candidate_email):
    """
    从已发送文件夹查找发给指定候选人的笔试邀请邮件，返回最早那封的实际发送时间字符串。
    找不到时返回 None。
    """
    if not imap or not candidate_email:
        return None
    import re as _re
    import email as _email_lib

    # 提取纯邮箱地址（小写）
    m = _re.search(r'<([^>]+)>', candidate_email)
    addr = (m.group(1).strip() if m else candidate_email.strip()).lower()

    exam_subj_kws = ["笔试邀请", "笔试通知", "exam"]
    found = []

    try:
        imap.select('"Sent Messages"')
        status, data = imap.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            return None

        msg_ids = data[0].split()
        # 只看最近 80 封，避免遍历过多
        for mid in msg_ids[-80:]:
            try:
                status, raw = imap.fetch(mid, "(RFC822.HEADER)")
                if status != "OK":
                    continue
                msg = _email_lib.message_from_bytes(raw[0][1])
                to_header = _decode_mime_header(msg.get("To") or "").lower()
                if addr not in to_header:
                    continue
                subject = _decode_mime_header(msg.get("Subject") or "")
                if not any(kw in subject or kw in subject.lower() for kw in exam_subj_kws):
                    continue
                date_str = msg.get("Date") or ""
                if date_str:
                    found.append(date_str)
            except Exception:
                continue
    except Exception:
        pass
    finally:
        try:
            imap.select("INBOX")
        except Exception:
            pass

    if not found:
        return None
    # 返回最早那封（解析后排序，取最小值）
    import email.utils as _eu
    def _parse_ts(s):
        try:
            return _eu.parsedate_to_datetime(s)
        except Exception:
            return None
    parsed = [(s, _parse_ts(s)) for s in found]
    parsed = [(s, dt) for s, dt in parsed if dt is not None]
    if not parsed:
        return found[0]
    parsed.sort(key=lambda x: x[1])
    return parsed[0][0]


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


def _lookup_candidate_by_email(sender_email, tdb):
    """根据发件人邮箱从数据库查找候选人（兜底匹配），返回候选人 dict 或 None。"""
    if not sender_email or not tdb:
        return None
    # 从 "Name <addr@domain>" 提取纯地址
    m = re.search(r'<([^>]+)>', sender_email)
    addr = m.group(1).strip().lower() if m else sender_email.strip().lower()
    try:
        state = tdb.load_state_from_db()
        candidates = state.get("candidates") or {}
        for tid, cand in candidates.items():
            db_email = (cand.get("candidate_email") or "").strip().lower()
            if db_email and db_email == addr:
                return dict(cand, talent_id=tid)
    except Exception:
        pass
    return None


def _email_is_before_reference(email_date_str, reference_date_str):
    # type: (str, str) -> bool
    """判断邮件时间是否早于参考时间。无法解析时返回 False。"""
    if not email_date_str or not reference_date_str:
        return False
    try:
        import datetime as _dt
        from dateutil import parser as _dtparser

        msg_dt = email_lib.utils.parsedate_to_datetime(email_date_str)
        ref_dt = _dtparser.parse(reference_date_str)
        if msg_dt is None or ref_dt is None:
            return False

        if msg_dt.tzinfo is None:
            msg_dt = msg_dt.replace(tzinfo=_dt.timezone.utc)
        else:
            msg_dt = msg_dt.astimezone(_dt.timezone.utc)

        if ref_dt.tzinfo is None:
            ref_dt = ref_dt.replace(tzinfo=_dt.timezone(_dt.timedelta(hours=8)))
        ref_dt = ref_dt.astimezone(_dt.timezone.utc)

        return msg_dt < ref_dt
    except Exception:
        return False


def scan_new_replies(auto_mode=False):
    """
    扫描收件箱中未读的笔试回复邮件。
    返回已处理的邮件列表（每项包含预审结果 prereview_result）。
    """
    import re
    import exam_prereview as _prereview

    _tdb = get_tdb()
    db_enabled = _tdb is not None

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

                # 过滤回复面试邀请的邮件（主题含"面试通知"/"一面"/"二面"且无附件）
                interview_reply_kws = ["面试通知", "一面邀请", "二面", "round1", "round2",
                                       "interview invitation"]
                if (any(kw in subject_lower for kw in interview_reply_kws)
                        and not attachment_info_list):
                    continue

                exam_keywords = ["exam-", "笔试邀请", "笔试通知", "笔试", "代码", "作业", "题目", "submission"]
                content_text = (subject + " " + body + " " + code_text).lower()
                # 附件须为代码/文档类型才算笔试相关（排除 inline 图片）
                code_ext = (".py", ".ipynb", ".zip", ".rar", ".pdf", ".docx", ".doc",
                            ".xlsx", ".xls", ".txt", ".java", ".cpp", ".c", ".js", ".ts",
                            ".r", ".m", ".sql", ".csv")
                has_code_attachment = any(
                    a["filename"].lower().endswith(code_ext)
                    for a in attachment_info_list
                )
                is_exam_related = (
                    any(kw in content_text for kw in exam_keywords)
                    or has_code_attachment
                )
                if not is_exam_related:
                    continue

                # 提取 exam_id
                exam_match = re.search(r"exam-([a-zA-Z0-9_-]+)", subject + " " + body)
                exam_id = "exam-" + exam_match.group(1) if exam_match else None

                # 查找候选人信息（获取 exam_sent_at）：先按 exam_id，再按发件人邮箱兜底
                cand_info = _lookup_candidate_by_exam_id(exam_id, _tdb if db_enabled else None)
                if not cand_info and db_enabled:
                    cand_info = _lookup_candidate_by_email(sender, _tdb)

                # 从已发送文件夹获取真实发送时间，覆盖 DB 中的 exam_sent_at
                # 用候选人邮箱（库中记录）或发件人邮箱（兜底）匹配已发送邮件
                _lookup_email = (
                    (cand_info.get("candidate_email") if cand_info else None)
                    or sender
                )
                actual_sent_at = _lookup_exam_sent_at_from_sent(imap, _lookup_email)
                if actual_sent_at:
                    if cand_info:
                        cand_info = dict(cand_info, exam_sent_at=actual_sent_at)
                    else:
                        # 未知候选人：只补充发件时间，不创建 cand_info（避免触发 stage 过滤）
                        pass

                # 如果找到候选人，但该候选人不在笔试阶段，说明这封邮件是面试回复邮件
                # 应由 scan_round1/2_confirmations 处理，笔试扫描直接跳过，避免混淆
                if cand_info and cand_info.get("talent_id"):
                    cand_stage = cand_info.get("stage", "")
                    exam_stages = {"EXAM_SENT", "EXAM_REVIEWED"}
                    if cand_stage not in exam_stages:
                        continue
                    if _email_is_before_reference(date_str, cand_info.get("exam_sent_at") or ""):
                        continue

                # 检查是否已处理过此邮件（游标去重）
                if cand_info and cand_info.get("talent_id") and msg_id_header:
                    if msg_id_header == cand_info.get("exam_last_email_id"):
                        continue

                # 运行预审
                email_data = {
                    "sender": sender,
                    "subject": subject,
                    "date": date_str,
                    "body_text": body,
                    "code_text": code_text,
                    "attachment_info_list": attachment_info_list,
                }
                # 未知候选人时，将从已发送查到的发件时间注入临时 cand_info
                prereview_cand = dict(cand_info) if cand_info else {}
                if actual_sent_at and not prereview_cand.get("exam_sent_at"):
                    prereview_cand["exam_sent_at"] = actual_sent_at
                prereview_result = _prereview.run_prereview(email_data, prereview_cand)

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

                # 更新游标 + 写预审结果到 DB
                if db_enabled and _tdb:
                    if cand_info and cand_info.get("talent_id"):
                        try:
                            _tdb.save_exam_prereview(
                                cand_info["talent_id"],
                                prereview_result["score"],
                                prereview_result["db_summary"],
                            )
                        except Exception:
                            pass
                        # 笔试回信处理完毕后，将 EXAM_SENT 推进到 EXAM_REVIEWED
                        try:
                            import core_state as _cs
                            _state = _cs.load_state()
                            _tid = cand_info["talent_id"]
                            _cand = _cs.get_candidate(_state, _tid)
                            if _cand.get("stage") == "EXAM_SENT":
                                _cand["stage"] = "EXAM_REVIEWED"
                                _cs.append_audit(_cand, actor="system", action="exam_reviewed_auto",
                                    payload={"trigger": "daily_exam_review", "score": prereview_result.get("score")})
                                _cs.save_state(_state)
                        except Exception:
                            pass
                        # 更新笔试邮件游标
                        if msg_id_header:
                            try:
                                _tdb.update_last_email_id(cand_info["talent_id"], "exam", msg_id_header)
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


def _scan_interview_confirmations(round_num, auto_mode=False):
    # type: (int, bool) -> List[Dict[str, Any]]
    """
    扫描一面（round_num=1）或二面（round_num=2）时间确认邮件。
    对每封回信用 LLM 分析意图，返回结果列表。
    """
    _tdb = get_tdb()
    db_enabled = _tdb is not None

    if not db_enabled:
        return []

    if round_num == 1:
        pending_list = _tdb.get_pending_confirmations(1)
        round_label = "一面"
    else:
        pending_list = _tdb.get_pending_confirmations(2)
        round_label = "二面"

    if not pending_list:
        return []

    last_eid_key = "round{}_last_email_id".format(round_num)

    try:
        imap = connect_imap()
    except Exception as e:
        if not auto_mode:
            print("IMAP 连接失败（{}确认扫描）：{}".format(round_label, e))
        return []

    results = []
    now_dt = datetime.now()

    try:
        imap.select("INBOX")
        for cand in pending_list:
            talent_id = cand["talent_id"]
            candidate_email = cand["candidate_email"]
            _time_key = "round{}_time".format(round_num)
            interview_time = cand.get(_time_key) or ""
            invite_sent_at_str = cand.get(
                "round1_invite_sent_at" if round_num == 1 else "round2_invite_sent_at"
            )

            # 超时判断：默认 48 小时（2880 分钟），可通过环境变量覆盖
            TIMEOUT_MINUTES = int(os.environ.get("INTERVIEW_CONFIRM_TIMEOUT_MINUTES", "2880"))
            timed_out = False
            if invite_sent_at_str:
                try:
                    # 兼容 Python 3.6（无 fromisoformat），用 dateutil 解析带时区字符串
                    from dateutil import parser as _dtparser
                    sent_dt = _dtparser.parse(invite_sent_at_str)
                    # 转为 naive datetime（去掉时区）进行比较
                    sent_dt_naive = sent_dt.replace(tzinfo=None)
                    minutes_elapsed = (now_dt - sent_dt_naive).total_seconds() / 60
                    if minutes_elapsed >= TIMEOUT_MINUTES:
                        timed_out = True
                except Exception as _te:
                    pass

            if timed_out:
                results.append({
                    "talent_id": talent_id,
                    "candidate_email": candidate_email,
                    "candidate_name": cand.get("candidate_name"),
                    "interview_time": interview_time,
                    "intent": "timeout",
                    "new_time": None,
                    "summary": "超时{}min未回复，自动默认确认".format(TIMEOUT_MINUTES),
                    "round": round_num,
                })
                continue

            # 搜索该候选人的邮件回复
            # IMAP FROM 搜索在很多邮件服务器上不可靠，改用时间窗口拉取 + 本地精确匹配
            # 以邀请发送时间为下限，避免把更早的笔试/一面邮件误判为面试确认
            try:
                if invite_sent_at_str:
                    try:
                        from dateutil import parser as _dtparser2
                        _sent_dt = _dtparser2.parse(invite_sent_at_str)
                        # 使用邀请发送日期作为 IMAP SINCE 下限（前一天，避免时区边界误差）
                        _since_dt = _sent_dt - __import__("datetime").timedelta(days=1)
                        _since_str = _since_dt.strftime("%d-%b-%Y").lstrip("0") or _since_dt.strftime("%d-%b-%Y")
                        search_criterion = '(SINCE "{}")'.format(_since_str)
                    except Exception:
                        search_criterion = '(SINCE "01-Jan-2026")'
                else:
                    search_criterion = '(SINCE "01-Jan-2026")'

                status, data = imap.search(None, search_criterion)
                if status != "OK" or not data or not data[0]:
                    continue

                msg_ids = data[0].split()
                fetch_limit = int(os.environ.get("INTERVIEW_CONFIRM_SCAN_LIMIT", "100"))
                for mid in reversed(msg_ids[-fetch_limit:]):  # 从最新往前扫
                    try:
                        status2, raw = imap.fetch(mid, "(RFC822)")
                        if status2 != "OK":
                            continue
                        raw_bytes = raw[0][1]
                        msg = email_lib.message_from_bytes(raw_bytes)

                        # 本地精确匹配 From 头，解决 IMAP FROM 搜索不可靠的问题
                        actual_from = email_lib.utils.parseaddr(msg.get("From", ""))[1].lower().strip()
                        if actual_from != candidate_email.lower().strip():
                            continue

                        # 跳过邀请发送时间之前的邮件（防止笔试回复等旧邮件混入）
                        # 注意：必须保留时区信息再比较，不能 replace(tzinfo=None)，否则 UTC 邮件会被误判
                        if invite_sent_at_str:
                            try:
                                from dateutil import parser as _dtparser3
                                import email.utils as _eu2
                                import datetime as _dt
                                _sent_dt2 = _dtparser3.parse(invite_sent_at_str)
                                # 统一转换为 UTC 进行比较
                                _sent_utc = _sent_dt2.astimezone(_dt.timezone.utc)
                                _date_str = msg.get("Date", "")
                                if _date_str:
                                    _msg_dt = _eu2.parsedate_to_datetime(_date_str)
                                    _msg_utc = _msg_dt.astimezone(_dt.timezone.utc)
                                    if _msg_utc < _sent_utc:
                                        continue  # 这封邮件早于邀请发送时间，跳过
                            except Exception:
                                pass

                        cursor_key = _message_cursor_key(msg, mid)
                        if cursor_key and cursor_key == cand.get(last_eid_key):
                            break

                        subject = _decode_mime_header(msg.get("Subject") or "")
                        body = _extract_body(msg)

                        # 跳过系统退信和自动回复
                        skip_kws = ["postmaster", "mailer-daemon", "退信", "auto-reply",
                                    "out of office", "自动回复", "undeliverable"]
                        if any(k in subject.lower() for k in skip_kws):
                            continue

                        # LLM 分析意图
                        analysis = _llm_analyze_reply(body or subject, round_label, interview_time)
                        normalized_new_time = _normalize_new_time(
                            analysis.get("new_time"), body or subject, interview_time
                        )

                        results.append({
                            "talent_id": talent_id,
                            "candidate_email": candidate_email,
                            "candidate_name": cand.get("candidate_name"),
                            "interview_time": interview_time,
                            "intent": analysis["intent"],
                            "new_time": normalized_new_time,
                            "summary": analysis["summary"],
                            "subject": subject,
                            "message_id": (msg.get("Message-ID") or "").strip() or cursor_key,
                            "round": round_num,
                        })

                        # 更新游标
                        if cursor_key:
                            try:
                                _tdb.update_last_email_id(talent_id, "round{}".format(round_num), cursor_key)
                            except Exception:
                                pass

                        break  # 每位候选人只处理最新一封
                    except Exception:
                        continue
            except Exception:
                continue
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    return results


def format_interview_confirmation_report(item):
    # type: (Dict[str, Any]) -> str
    """格式化一面/二面确认结果为飞书推送文本。"""
    round_num = item.get("round", 2)
    round_label = "一面" if round_num == 1 else "二面"
    talent_id = item["talent_id"]
    name = item.get("candidate_name") or talent_id
    interview_time = item.get("interview_time") or "（未知）"
    intent = item.get("intent", "unknown")
    summary = item.get("summary", "")

    if intent == "timeout":
        confirm_rel = "interview/cmd_confirm.py"
        return (
            "[{round_label}确认扫描]\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "候选人：{name}\n"
            "talent_id：{tid}（执行命令时必须用此ID）\n"
            "{round_label}时间：{t}\n"
            "{summary}，建议执行：\n"
            "  python3 {rel} --talent-id {tid} --round {round_num} --auto"
        ).format(round_label=round_label, name=name, tid=talent_id, t=interview_time,
                 summary=item.get("summary", "超时未回复"), rel=confirm_rel, round_num=round_num)

    elif intent == "confirm":
        confirm_rel = "interview/cmd_confirm.py"
        return (
            "[{round_label}确认扫描]\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "候选人：{name}\n"
            "talent_id：{tid}（执行命令时必须用此ID）\n"
            "{round_label}时间：{t}\n"
            "意图：{summary}\n"
            "建议执行确认：\n"
            "  python3 {rel} --talent-id {tid} --round {round_num}"
        ).format(round_label=round_label, name=name, tid=talent_id, t=interview_time,
                 summary=summary, rel=confirm_rel, round_num=round_num)

    elif intent == "reschedule":
        reschedule_rel = "interview/cmd_reschedule.py"
        new_time = item.get("new_time")
        time_hint = ""
        reschedule_hint = ""
        if new_time:
            time_hint = "\n候选人建议时间：{}".format(new_time)
            reschedule_hint = (
                "\n⚠️ 必须使用以下命令（talent_id={tid}，勿混淆其他候选人）：\n"
                "  python3 {rel} --talent-id {tid} --round {round_num} --time \"{t}\""
            ).format(rel=reschedule_rel, tid=talent_id, round_num=round_num, t=new_time)
        else:
            reschedule_hint = (
                "\n⚠️ 候选人未给出具体时间，请联系后执行（talent_id={tid}）：\n"
                "  python3 {rel} --talent-id {tid} --round {round_num} --time \"YYYY-MM-DD HH:MM\""
            ).format(rel=reschedule_rel, tid=talent_id, round_num=round_num)
        return (
            "[{round_label}确认扫描]\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "候选人：{name}\n"
            "talent_id：{tid}（执行命令时必须用此ID）\n"
            "原{round_label}时间：{t}\n"
            "意图：{summary}{time_hint}{reschedule_hint}"
        ).format(round_label=round_label, name=name, tid=talent_id, t=interview_time,
                 summary=summary, time_hint=time_hint, reschedule_hint=reschedule_hint)

    elif intent == "request_online":
        if round_num == 1:
            return (
                "[{round_label}确认扫描]\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "候选人：{name}\n"
                "talent_id：{tid}（执行命令时必须用此ID）\n"
                "原{round_label}时间：{t}\n"
                "意图：{summary}\n"
                "请人工确认是否改为线上，并重新安排。"
            ).format(round_label=round_label, name=name, tid=talent_id, t=interview_time, summary=summary)

        switch_hint = (
            "\n候选人希望线上面试：请直接邮件沟通会议方式；改时间可用：\n"
            "  python3 {rel} --talent-id {tid} --round 2 --time \"YYYY-MM-DD HH:MM\""
        ).format(tid=talent_id, rel="interview/cmd_reschedule.py")
        return (
            "[{round_label}确认扫描]\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "候选人：{name}\n"
            "talent_id：{tid}（执行命令时必须用此ID）\n"
            "原{round_label}时间：{t}\n"
            "意图：{summary}{switch_hint}"
        ).format(round_label=round_label, name=name, tid=talent_id, t=interview_time,
                 summary=summary, switch_hint=switch_hint)

    elif intent == "defer_until_shanghai":
        defer_cmd = "cmd_round1_defer" if round_num == 1 else "cmd_round2_defer"
        defer_hint = (
            "\n建议执行暂缓：\n"
            "  python3 {rel} --talent-id {tid} --reason \"{summary}\"\n"
            "处理后状态将进入 WAIT_RETURN，待候选人回国后再恢复安排。"
        ).format(
            tid=talent_id,
            summary=summary or "候选人暂时不在国内/上海，之后再约",
            rel=_rel_script_py(defer_cmd),
        )
        return (
            "[{round_label}确认扫描]\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "候选人：{name}\n"
            "talent_id：{tid}（执行命令时必须用此ID）\n"
            "原{round_label}时间：{t}\n"
            "意图：{summary}{defer_hint}"
        ).format(round_label=round_label, name=name, tid=talent_id, t=interview_time,
                 summary=summary, defer_hint=defer_hint)

    else:
        return (
            "[{round_label}确认扫描]\n"
            "[候选人回信]\n"
            "候选人：{name}（{tid}）\n"
            "{round_label}时间：{t}\n"
            "意图不明确，请人工查阅邮件。\n"
            "摘要：{summary}"
        ).format(round_label=round_label, name=name, tid=talent_id, t=interview_time, summary=summary)


def scan_round1_confirmations(auto_mode=False):
    # type: (bool) -> List[Dict[str, Any]]
    """扫描一面时间确认邮件（ROUND1_SCHEDULING 候选人）。"""
    return _scan_interview_confirmations(round_num=1, auto_mode=auto_mode)


def scan_round2_confirmations(auto_mode=False):
    # type: (bool) -> List[Dict[str, Any]]
    """扫描二面时间确认邮件（ROUND2_SCHEDULING 候选人）。"""
    return _scan_interview_confirmations(round_num=2, auto_mode=auto_mode)


# ─── 已确认候选人改期请求扫描 ─────────────────────────────────────────────────

def _scan_reschedule_requests(round_num, auto_mode=False):
    # type: (int, bool) -> List[Dict[str, Any]]
    """
    扫描已确认面试时间的候选人邮件，检测改期请求。
    与 _scan_interview_confirmations 结构一致，区别：
    - 目标是已确认候选人（confirmed=TRUE）
    - 不做超时判断
    - 只关注 reschedule 意图的结果
    """
    _tdb = get_tdb()
    db_enabled = _tdb is not None

    if not db_enabled:
        return []

    if round_num == 1:
        confirmed_list = _tdb.get_confirmed_candidates(1)
        round_label = "一面"
    else:
        confirmed_list = _tdb.get_confirmed_candidates(2)
        round_label = "二面"

    if not confirmed_list:
        return []

    last_eid_key = "round{}_last_email_id".format(round_num)

    try:
        imap = connect_imap()
    except Exception as e:
        if not auto_mode:
            print("IMAP 连接失败（{}改期扫描）：{}".format(round_label, e))
        return []

    results = []

    try:
        imap.select("INBOX")
        for cand in confirmed_list:
            talent_id = cand["talent_id"]
            candidate_email = cand["candidate_email"]
            _time_key = "round{}_time".format(round_num)
            interview_time = cand.get(_time_key) or ""
            invite_sent_at_str = cand.get(
                "round1_invite_sent_at" if round_num == 1 else "round2_invite_sent_at"
            )

            try:
                if invite_sent_at_str:
                    try:
                        from dateutil import parser as _dtparser2
                        _sent_dt = _dtparser2.parse(invite_sent_at_str)
                        _since_dt = _sent_dt - __import__("datetime").timedelta(days=1)
                        _since_str = _since_dt.strftime("%d-%b-%Y").lstrip("0") or _since_dt.strftime("%d-%b-%Y")
                        search_criterion = '(SINCE "{}")'.format(_since_str)
                    except Exception:
                        search_criterion = '(SINCE "01-Jan-2026")'
                else:
                    search_criterion = '(SINCE "01-Jan-2026")'

                status, data = imap.search(None, search_criterion)
                if status != "OK" or not data or not data[0]:
                    continue

                msg_ids = data[0].split()
                fetch_limit = int(os.environ.get("INTERVIEW_CONFIRM_SCAN_LIMIT", "100"))
                for mid in reversed(msg_ids[-fetch_limit:]):
                    try:
                        status2, raw = imap.fetch(mid, "(RFC822)")
                        if status2 != "OK":
                            continue
                        raw_bytes = raw[0][1]
                        msg = email_lib.message_from_bytes(raw_bytes)

                        actual_from = email_lib.utils.parseaddr(msg.get("From", ""))[1].lower().strip()
                        if actual_from != candidate_email.lower().strip():
                            continue

                        if invite_sent_at_str:
                            try:
                                from dateutil import parser as _dtparser3
                                import email.utils as _eu2
                                import datetime as _dt
                                _sent_dt2 = _dtparser3.parse(invite_sent_at_str)
                                _sent_utc = _sent_dt2.astimezone(_dt.timezone.utc)
                                _date_str = msg.get("Date", "")
                                if _date_str:
                                    _msg_dt = _eu2.parsedate_to_datetime(_date_str)
                                    _msg_utc = _msg_dt.astimezone(_dt.timezone.utc)
                                    if _msg_utc < _sent_utc:
                                        continue
                            except Exception:
                                pass

                        cursor_key = _message_cursor_key(msg, mid)
                        if cursor_key and cursor_key == cand.get(last_eid_key):
                            break

                        subject = _decode_mime_header(msg.get("Subject") or "")
                        body = _extract_body(msg)

                        skip_kws = ["postmaster", "mailer-daemon", "退信", "auto-reply",
                                    "out of office", "自动回复", "undeliverable"]
                        if any(k in subject.lower() for k in skip_kws):
                            continue

                        analysis = _llm_analyze_reply(body or subject, round_label, interview_time)
                        normalized_new_time = _normalize_new_time(
                            analysis.get("new_time"), body or subject, interview_time
                        )
                        detected_intent = analysis.get("intent", "unknown")

                        if detected_intent == "reschedule":
                            same_time = (
                                bool(normalized_new_time)
                                and bool(interview_time)
                                and normalized_new_time == interview_time
                            )
                            if not normalized_new_time or same_time:
                                detected_intent = "reschedule_noop"
                                if same_time:
                                    analysis["summary"] = (
                                        (analysis.get("summary") or "候选人邮件已记录")
                                        + "（未识别到与当前安排不同的新时间）"
                                    )
                                else:
                                    analysis["summary"] = (
                                        (analysis.get("summary") or "候选人邮件已记录")
                                        + "（未提取到明确的新时间）"
                                    )

                        actionable_intents = {
                            "reschedule",
                            "defer_until_shanghai",
                            "request_online",
                            "reschedule_noop",
                        }
                        if detected_intent not in actionable_intents:
                            # 非改期意图：更新游标避免下次重复扫描
                            if cursor_key:
                                try:
                                    _tdb.update_last_email_id(talent_id, "round{}".format(round_num), cursor_key)
                                except Exception:
                                    pass
                            continue

                        results.append({
                            "talent_id": talent_id,
                            "candidate_email": candidate_email,
                            "candidate_name": cand.get("candidate_name"),
                            "interview_time": interview_time,
                            "intent": detected_intent,
                            "new_time": normalized_new_time,
                            "summary": analysis["summary"],
                            "subject": subject,
                            "message_id": (msg.get("Message-ID") or "").strip() or cursor_key,
                            "round": round_num,
                        })

                        if cursor_key:
                            try:
                                _tdb.update_last_email_id(talent_id, "round{}".format(round_num), cursor_key)
                            except Exception:
                                pass

                        break
                    except Exception:
                        continue
            except Exception:
                continue
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    return results


def scan_round1_reschedule_requests(auto_mode=False):
    # type: (bool) -> List[Dict[str, Any]]
    """扫描一面已确认候选人的改期请求。"""
    return _scan_reschedule_requests(round_num=1, auto_mode=auto_mode)


def scan_round2_reschedule_requests(auto_mode=False):
    # type: (bool) -> List[Dict[str, Any]]
    """扫描二面已确认候选人的改期请求。"""
    return _scan_reschedule_requests(round_num=2, auto_mode=auto_mode)


def format_reschedule_request_report(item):
    # type: (Dict[str, Any]) -> str
    """格式化已确认候选人改期/暂缓/线上请求报告。"""
    round_num = item.get("round", 2)
    round_label = "一面" if round_num == 1 else "二面"
    talent_id = item["talent_id"]
    name = item.get("candidate_name") or talent_id
    interview_time = item.get("interview_time") or "(未知)"
    summary = item.get("summary", "")
    intent = item.get("intent", "reschedule")
    new_time = item.get("new_time")

    if intent == "defer_until_shanghai":
        defer_cmd = "cmd_round2_defer" if round_num == 2 else "cmd_round1_defer"
        cmd_hint = (
            "\n建议执行暂缓：\n"
            "  python3 {rel} --talent-id {tid} --reason \"{summary}\"\n"
            "处理后状态将进入 WAIT_RETURN，待候选人之后方便时再恢复安排。"
        ).format(
            rel=_rel_script_py(defer_cmd),
            tid=talent_id,
            summary=summary or "候选人暂不在国内",
        )
        return (
            "[{round_label}暂缓请求]\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "候选人：{name}\n"
            "talent_id：{tid}（执行命令时必须用此ID）\n"
            "原{round_label}时间：{t}\n"
            "原因：{summary}{cmd_hint}"
        ).format(round_label=round_label, name=name, tid=talent_id,
                 t=interview_time, summary=summary, cmd_hint=cmd_hint)

    if intent == "request_online":
        reschedule_rel = "interview/cmd_reschedule.py"
        cmd_hint = (
            "\n候选人希望改为线上面试：请邮件沟通会议细节；改时间可用：\n"
            "  python3 {rel} --talent-id {tid} --round {round_num} --time \"YYYY-MM-DD HH:MM\""
        ).format(rel=reschedule_rel, tid=talent_id, round_num=round_num)
        return (
            "[{round_label}线上面试请求]\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "候选人：{name}\n"
            "talent_id：{tid}（执行命令时必须用此ID）\n"
            "原{round_label}时间：{t}\n"
            "原因：{summary}{cmd_hint}"
        ).format(round_label=round_label, name=name, tid=talent_id,
                 t=interview_time, summary=summary, cmd_hint=cmd_hint)

    if intent == "reschedule_noop":
        return (
            "[{round_label}改期邮件已记录]\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "候选人：{name}\n"
            "talent_id：{tid}（执行命令时必须用此ID）\n"
            "原{round_label}时间：{t}\n"
            "说明：{summary}\n"
            "系统未自动撤销确认：未识别到与当前安排不同的新时间。"
        ).format(
            round_label=round_label,
            name=name,
            tid=talent_id,
            t=interview_time,
            summary=summary or "候选人邮件已记录",
        )

    reschedule_rel = "interview/cmd_reschedule.py"
    time_hint = ""
    cmd_hint = ""
    if new_time:
        time_hint = "\n候选人建议新时间：{}".format(new_time)
        cmd_hint = (
            "\n请确认后执行（talent_id={tid}，勿混淆其他候选人）：\n"
            "  python3 {rel} --talent-id {tid} --round {round_num} --time \"{t}\""
        ).format(rel=reschedule_rel, tid=talent_id, round_num=round_num, t=new_time)
    else:
        cmd_hint = (
            "\n候选人未给出具体新时间，请联系后执行（talent_id={tid}）：\n"
            "  python3 {rel} --talent-id {tid} --round {round_num} --time \"YYYY-MM-DD HH:MM\""
        ).format(rel=reschedule_rel, tid=talent_id, round_num=round_num)

    return (
        "[{round_label}改期请求]\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "候选人：{name}\n"
        "talent_id：{tid}（执行命令时必须用此ID）\n"
        "原{round_label}时间：{t}\n"
        "改期原因：{summary}{time_hint}{cmd_hint}"
    ).format(
        round_label=round_label, name=name, tid=talent_id,
        t=interview_time, summary=summary,
        time_hint=time_hint, cmd_hint=cmd_hint, round_num=round_num,
    )


def _run_reschedule_scan(args, fn):
    """运行已确认候选人改期/暂缓/线上请求扫描（一面 + 二面），处理结果并推送飞书。"""
    reschedule_script = os.path.join(_SCRIPTS, "common", "cmd_reschedule_request.py")
    total = 0

    for round_num in (1, 2):
        round_label = "一面" if round_num == 1 else "二面"
        if round_num == 1:
            rs_results = scan_round1_reschedule_requests(auto_mode=args.auto)
        else:
            rs_results = scan_round2_reschedule_requests(auto_mode=args.auto)

        for item in rs_results:
            talent_id = item["talent_id"]
            intent = item.get("intent", "reschedule")

            if intent == "defer_until_shanghai":
                defer_script = os.path.join(
                    _SCRIPTS,
                    "round1" if round_num == 1 else "round2",
                    "cmd_round{}_defer.py".format(round_num),
                )
                cmd = ["python3", defer_script, "--talent-id", talent_id]
                if item.get("summary"):
                    cmd += ["--reason", item["summary"]]
                try:
                    proc_result = subprocess.run(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                    )
                    out = proc_result.stdout.decode("utf-8", errors="replace").strip()
                    err = proc_result.stderr.decode("utf-8", errors="replace").strip()
                    report = format_reschedule_request_report(item)
                    report += "\n（已自动暂缓安排）\n{}".format(out)
                    if err:
                        report += "\n\u26a0 {}".format(err)
                except Exception as e:
                    report = format_reschedule_request_report(item)
                    report += "\n\u26a0 自动暂缓失败: {}".format(e)
            elif intent == "request_online":
                report = format_reschedule_request_report(item)
            elif intent == "reschedule_noop":
                report = format_reschedule_request_report(item)
            else:
                cmd = [
                    "python3", reschedule_script,
                    "--talent-id", talent_id,
                    "--round", str(round_num),
                ]
                if item.get("summary"):
                    cmd += ["--reason", item["summary"]]
                if item.get("new_time"):
                    cmd += ["--new-time", item["new_time"]]
                try:
                    proc_result = subprocess.run(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                    )
                    ack_out = proc_result.stdout.decode("utf-8", errors="replace").strip()
                    ack_err = proc_result.stderr.decode("utf-8", errors="replace").strip()
                    report = format_reschedule_request_report(item)
                    report += "\n（已自动撤销确认 + 回复候选人）\n{}".format(ack_out)
                    if ack_err:
                        report += "\n\u26a0 {}".format(ack_err)
                except Exception as e:
                    report = format_reschedule_request_report(item)
                    report += "\n\u26a0 自动处理失败: {}".format(e)

            if args.auto:
                fn.send_text(report)
            else:
                print(report)
                fn.send_text(report)
            total += 1

    if total and args.auto:
        print("[email_scan] 改期请求扫描：{} 条更新，已推送飞书。".format(total))
    elif not total and not args.auto:
        print("[email_scan] 暂无已确认候选人改期请求。")


def main(argv=None):
    p = argparse.ArgumentParser(description="邮件自动扫描，检查笔试回复和面试时间确认")
    p.add_argument("--auto", action="store_true", help="cron 模式（无回复静默退出）")
    p.add_argument("--exam-only", action="store_true", help="只扫笔试回复邮件（8h一次）")
    p.add_argument("--interview-confirm-only", action="store_true",
                   help="只扫面试时间确认邮件（6h一次）")
    p.add_argument("--reschedule-scan-only", action="store_true",
                   help="只扫已确认候选人的改期请求邮件（2h一次）")
    args = p.parse_args(argv or sys.argv[1:])

    import feishu as fn

    # --reschedule-scan-only: 单独的改期扫描 cron
    if args.reschedule_scan_only:
        _run_reschedule_scan(args, fn)
        return 0

    # 决定运行哪些模块
    run_exam = not args.interview_confirm_only   # 默认跑，--interview-confirm-only 时跳过
    run_interview = not args.exam_only           # 默认跑，--exam-only 时跳过

    # 1. 扫描笔试回复（--exam-only 或无特定参数时运行）
    if run_exam:
        exam_results = scan_new_replies(auto_mode=args.auto)
        if exam_results:
            for r in exam_results:
                report = format_report(r)
                if args.auto:
                    fn.send_text(report)
                else:
                    print(report)
                    fn.send_text(report)
            if args.auto:
                print("[email_scan] 共发现 {} 封新笔试回复，已推送飞书。".format(len(exam_results)))
        elif not args.auto:
            print("[email_scan] 暂无新的笔试回复邮件。")

    # 2. 扫描一面确认（--interview-confirm-only 或无特定参数时运行）
    if not run_interview:
        return 0

    # 通用：处理一面/二面确认结果（不再自动确认，全部记录 pending + 推送老板）
    _tdb = get_tdb()
    for round_num in (1, 2):
        round_label = "一面" if round_num == 1 else "二面"
        if round_num == 1:
            rx_results = scan_round1_confirmations(auto_mode=args.auto)
        else:
            rx_results = scan_round2_confirmations(auto_mode=args.auto)

        if rx_results:
            for item in rx_results:
                intent = item.get("intent", "unknown")
                talent_id = item["talent_id"]
                name = item.get("candidate_name") or talent_id
                interview_time = item.get("interview_time") or "（未知）"
                new_time = item.get("new_time")
                summary = item.get("summary", "")

                if intent == "confirm":
                    # 候选人同意时间；若 LLM 同时给出新时间，则以新时间为准，避免确认文案与 DB 不一致。
                    proposed_time = new_time or interview_time
                    if _tdb:
                        _tdb.set_boss_confirm_pending(
                            talent_id, round_num,
                            proposed_time=proposed_time,
                        )
                    if new_time and new_time != interview_time:
                        report = (
                            "[{rl}候选人回信]\n"
                            "━━━━━━━━━━━━━━━━━━━━\n"
                            "候选人：{name}\n"
                            "talent_id：{tid}\n"
                            "原{rl}时间：{t}\n"
                            "候选人确认时间：{nt}\n"
                            "候选人意图：{summary}\n"
                            "━━━━━━━━━━━━━━━━━━━━\n"
                            "⏳ 候选人已确认新时间，请确认是否最终敲定：\n"
                            "  回复「确认 {tid} {rl}」→ 按新时间 {nt} 最终确认\n"
                            "  回复「改期 {tid} YYYY-MM-DD HH:MM」→ 改为其他时间"
                        ).format(
                            rl=round_label, name=name, tid=talent_id,
                            t=interview_time, nt=new_time, summary=summary,
                        )
                    else:
                        report = (
                            "[{rl}候选人回信]\n"
                            "━━━━━━━━━━━━━━━━━━━━\n"
                            "候选人：{name}\n"
                            "talent_id：{tid}\n"
                            "{rl}时间：{t}\n"
                            "候选人意图：{summary}\n"
                            "━━━━━━━━━━━━━━━━━━━━\n"
                            "⏳ 候选人已同意该时间，请确认是否最终敲定：\n"
                            "  回复「确认 {tid} {rl}」即可最终确认并创建日历"
                        ).format(rl=round_label, name=name, tid=talent_id,
                                 t=interview_time, summary=summary)

                elif intent == "reschedule" and new_time:
                    # 候选人提出新时间 -> 记录 pending，推送老板确认
                    if _tdb:
                        _tdb.set_boss_confirm_pending(
                            talent_id, round_num,
                            proposed_time=new_time,
                        )
                    report = (
                        "[{rl}候选人回信]\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "候选人：{name}\n"
                        "talent_id：{tid}\n"
                        "原{rl}时间：{t}\n"
                        "候选人建议新时间：{nt}\n"
                        "意图：{summary}\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "⏳ 候选人提议了新时间，请确认是否按此时间最终敲定：\n"
                        "  回复「确认 {tid} {rl}」→ 按新时间 {nt} 最终确认\n"
                        "  回复「改期 {tid} YYYY-MM-DD HH:MM」→ 改为其他时间"
                    ).format(rl=round_label, name=name, tid=talent_id,
                             t=interview_time, nt=new_time, summary=summary)

                elif intent == "timeout":
                    # 超时不再自动确认，只催老板
                    if _tdb:
                        _tdb.set_boss_confirm_pending(
                            talent_id, round_num,
                            proposed_time=interview_time,
                        )
                    report = (
                        "[{rl}确认超时提醒]\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "候选人：{name}\n"
                        "talent_id：{tid}\n"
                        "{rl}时间：{t}\n"
                        "候选人已超过48小时未回复。\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "⏳ 是否按当前时间最终确认？\n"
                        "  回复「确认 {tid} {rl}」即可最终确认并创建日历"
                    ).format(rl=round_label, name=name, tid=talent_id, t=interview_time)

                elif intent == "defer_until_shanghai":
                    defer_script = os.path.join(
                        _SCRIPTS,
                        "round1" if round_num == 1 else "round2",
                        "cmd_round{}_defer.py".format(round_num),
                    )
                    cmd = ["python3", defer_script, "--talent-id", talent_id]
                    if summary:
                        cmd += ["--reason", summary]
                    try:
                        proc_result = subprocess.run(
                            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
                        defer_out = proc_result.stdout.decode("utf-8", errors="replace").strip()
                        report = format_interview_confirmation_report(item)
                        report += "\n（已自动暂缓安排）\n{}".format(defer_out)
                    except Exception as e:
                        report = format_interview_confirmation_report(item)
                        report += "\n⚠ 自动暂缓失败: {}".format(e)

                else:
                    report = format_interview_confirmation_report(item)

                if args.auto:
                    fn.send_text(report)
                else:
                    print(report)
                    fn.send_text(report)
            if args.auto:
                print("[email_scan] {}确认扫描：{} 条更新，已推送飞书。".format(
                    round_label, len(rx_results)))
        elif not args.auto:
            print("[email_scan] 暂无{}确认待处理。".format(round_label))

    # 4. 改期请求扫描（无特定参数时也跑一次）
    _run_reschedule_scan(args, fn)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
